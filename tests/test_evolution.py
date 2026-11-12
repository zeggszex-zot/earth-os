"""The System Evolution Center, held to what it promises.

``docs/SEC_ARCHITECTURE.md`` makes a short list of claims, and a claim that
only exists in a document drifts. Each class below is one of them:

* **It proposes; it does not act.** Read from the source, the way
  ``tests/test_mind.py`` reads Earth Mind's imports: nothing under
  ``earth_os/evolution/`` imports what can change the machine, only two
  modules write a file, and no engine module calls a decision.
* **Nothing changes without approval** — checked by hashing the
  configuration file across everything the engine can do unprompted.
* **A reading that cannot be taken is unavailable, never zero.**
* **Its verdicts agree with the machine's own.** The same readings are given
  to ``performance_report`` and to the Health Analyzer.
* **An expectation is never reported as a measurement.**
* **It is cheap**, and silent when nobody is looking.
* It works with a model, without one, and with half its readings missing.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os.capability_corpus import CapabilityCorpus          # noqa: E402
from earth_os.config import Config                               # noqa: E402
from earth_os.core import Shell                                  # noqa: E402
from earth_os.evolution import build_center                      # noqa: E402
from earth_os.evolution import changes, health, planner          # noqa: E402
from earth_os.evolution.center import EvolutionCenter            # noqa: E402
from earth_os.evolution.changes import (                         # noqa: E402
    TUNABLES, ChangeApplier, ChangeRefused, remove_value, set_value, validate,
)
from earth_os.evolution.health import HealthAnalyzer             # noqa: E402
from earth_os.evolution.inspector import Inspector               # noqa: E402
from earth_os.evolution.model import (                           # noqa: E402
    APPROVED, CATEGORIES, COMPLETED, CRITICAL, DISCOVERED, HIGH, IN_DEVELOPMENT,
    INFORMATIONAL, LOW, MODERATE, PROPOSED, REJECTED, ROLLED_BACK, SEVERITIES,
    STATUSES, TESTING, Reading, Snapshot, severity_rank,
)
from earth_os.evolution.optimizer import OptimizationAdvisor     # noqa: E402
from earth_os.evolution.planner import DECISIONS, Planner, Refused   # noqa: E402
from earth_os.evolution.registry import CapabilityRegistry, Facts    # noqa: E402
from earth_os.evolution.reporter import KINDS, Reporter          # noqa: E402
from earth_os.evolution.store import Store                       # noqa: E402
from earth_os.evolution.trends import Sample, Trends             # noqa: E402
from earth_os.platform import PlatformError                      # noqa: E402
from earth_os.screens import ALL_SCREENS                         # noqa: E402
from earth_os.screens.evolution import TABS, EvolutionScreen     # noqa: E402
from earth_os.screens.main import COLUMN                         # noqa: E402
from earth_os.sysinfo import SystemMonitor                       # noqa: E402

PACKAGE = ROOT / "earth_os" / "evolution"
SIZES = ((1920, 1080), (1600, 1000), (1280, 720), (1024, 600))
GB = 1024 ** 3


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def make_shell(settings=None):
    """A real shell on this machine, with the center's records in a temp dir.

    *settings* is ``{(section, key): value}`` laid over the defaults.
    """
    home = Path(tempfile.mkdtemp(prefix="earth-os-sec-"))
    config = Config.load(home / "config.toml")
    config.values["voice"]["enabled"] = False
    for (section, key), value in (settings or {}).items():
        config.values.setdefault(section, {})[key] = value
    shell = Shell(config)
    shell.sound.enabled = False
    for screen_class in ALL_SCREENS:
        shell.register_screen(screen_class(shell))
    center = build_center(shell, directory=home / "evolution")
    shell._evolution, shell._evolution_built = center, True
    return shell, center


def process(pid, name, memory, state="S", cpu=0.0, command=""):
    return SimpleNamespace(pid=pid, name=name, command=command or name, state=state,
                           cpu_percent=cpu, memory_bytes=memory)


def snapshot(cpu=0.10, memory=0.30, disk=0.50, temperature=None, swap=(0, 0),
             load=(0.2, 0.2, 0.2), cores=4, processes=None, render=None, total=8 * GB):
    """A snapshot with chosen figures. None for a figure leaves it unavailable."""
    snap = Snapshot()
    put = snap.put
    put(Reading("cpu", cpu) if cpu is not None else Reading.missing("cpu", "no counters"))
    if memory is not None:
        used = int(total * memory)
        put(Reading("memory", {"fraction": memory, "used": used, "total": total,
                               "available": total - used}))
    else:
        put(Reading.missing("memory", "no memory reading"))
    if disk is not None:
        size = 100 * GB
        put(Reading("disk", {"fraction": disk, "used": int(size * disk), "total": size,
                             "free": size - int(size * disk)}))
    else:
        put(Reading.missing("disk", "no storage reading"))
    put(Reading("temperature", temperature) if temperature is not None
        else Reading.missing("temperature", "no thermal sensor reported"))
    put(Reading("swap", {"fraction": (swap[1] / swap[0]) if swap[0] else 0.0,
                         "used": swap[1], "total": swap[0]}))
    put(Reading("load", tuple(load)))
    put(Reading("cpu_count", cores))
    if processes is not None:
        put(Reading("processes", list(processes)))
    put(Reading("render", render) if render is not None
        else Reading.missing("render", "no window is attached (headless)"))
    return snap


def by_id(findings):
    return {f.id: f for f in findings}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class BrokenHardware:
    """A platform whose every reading fails, the way a real one can."""

    def __getattr__(self, name):
        def fail(*_a, **_k):
            raise PlatformError(f"{name}: no such sensor on this body")
        return fail


class BrokenPort:
    """A shell with no working machine under it: a robot, a bad day."""

    def __init__(self, home: Path):
        broken = SimpleNamespace(hardware=BrokenHardware(), system=BrokenHardware(),
                                 name="Broken")
        self.monitor = SystemMonitor(platform=broken)
        self.config = Config.load(home / "config.toml")
        self.platform = None
        self.platform_error = "no platform adapter for this host"
        self.capabilities = None
        self.render_stats = None

    def take_reading(self, capability):
        return SimpleNamespace(ok=False, value=None, error=f"{capability}: unavailable")


def imports_of(path: Path):
    """``(level, module)`` for every import in *path*, wherever it sits."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((0, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append((node.level, node.module or ""))
            if not node.module:
                found.extend((node.level, alias.name) for alias in node.names)
    return found


def called_attributes(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]


MODULES = sorted(PACKAGE.glob("*.py"))


# --------------------------------------------------------------------------
# 1. It proposes; it does not act — read from the source
# --------------------------------------------------------------------------
class TestTheCenterCannotActOnTheMachine(unittest.TestCase):

    #: Standard-library modules that start programs, open sockets or load code.
    FORBIDDEN_STDLIB = {"subprocess", "socket", "ctypes", "multiprocessing", "pty",
                        "urllib", "http", "ftplib", "smtplib", "importlib", "runpy",
                        "signal"}
    #: Parts of Earth OS that can change the machine, or that hold a registry.
    FORBIDDEN_LOCAL = {"platform", "agent", "compositions", "core", "voice", "windows",
                       "app", "screens", "openers", "audio", "comms", "volume", "screengrab",
                       "diagnostics"}

    def test_nothing_imports_what_can_change_the_machine(self):
        """The rule ``earth_os/mind/`` lives under, applied here.

        A reading reaches the engine through ``Shell.take_reading``, which
        refuses anything effectful. If a module here could import an adapter
        or a registry, that gate would be one import away from irrelevant.
        """
        offenders = []
        for path in MODULES:
            for level, module in imports_of(path):
                head = module.split(".")[0]
                if level == 0 and head in self.FORBIDDEN_STDLIB:
                    offenders.append(f"{path.name}: import {module}")
                if level == 0 and module.startswith("earth_os") and \
                        any(part in self.FORBIDDEN_LOCAL for part in module.split(".")[1:2]):
                    offenders.append(f"{path.name}: import {module}")
                if level == 2 and head in self.FORBIDDEN_LOCAL:
                    offenders.append(f"{path.name}: from ..{module}")
        self.assertEqual(offenders, [])

    def test_only_two_modules_write_a_file(self):
        """``store.py`` keeps the center's own records; ``changes.py`` is the
        one actuator. A write anywhere else is a second actuator."""
        writers = {"write_text", "write_bytes", "unlink", "rmtree", "rmdir", "remove",
                   "rename", "replace", "copy", "copy2", "copyfile", "move", "mkdir",
                   "makedirs", "chmod", "truncate", "touch", "symlink_to"}
        offenders = []
        for path in MODULES:
            if path.name in ("store.py", "changes.py"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (node.func.attr if isinstance(node.func, ast.Attribute)
                        else getattr(node.func, "id", ""))
                if name in writers and name != "replace":
                    offenders.append(f"{path.name}:{node.lineno} {name}()")
                if name == "open" and len(node.args) > 1:
                    offenders.append(f"{path.name}:{node.lineno} open() with a mode")
        self.assertEqual(offenders, [])

    def test_changes_writes_only_the_configuration_file(self):
        """Every write in the actuator is to ``path`` — the config file."""
        tree = ast.parse((PACKAGE / "changes.py").read_text(encoding="utf-8"))
        targets = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("write_text", "write_bytes"):
                targets.add(ast.unparse(node.func.value))
        self.assertEqual(targets, {"path"})

    def test_no_engine_module_makes_a_decision(self):
        """The engine may ``discover`` and ``propose``. Everything in
        ``DECISIONS`` needs a person's name, and nothing here supplies one."""
        offenders = []
        for path in MODULES:
            if path.name == "planner.py":
                continue
            for attr in called_attributes(path):
                if attr in DECISIONS:
                    offenders.append(f"{path.name}: .{attr}()")
        self.assertEqual(offenders, [])

    def test_a_decision_without_a_person_is_refused(self):
        _shell, center = make_shell()
        proposal, _ = center.planner.discover(title="t", kind="capability", source="x:1")
        for name in DECISIONS:
            for nobody in ("", "   ", "engine"):
                with self.assertRaises(Refused, msg=f"{name} by {nobody!r}"):
                    args = {"note": "n"} if name == "annotate" else {}
                    getattr(center.planner, name)(proposal.id, by=nobody, **args)
        self.assertEqual(center.planner.get(proposal.id).status, DISCOVERED)

    def test_the_list_of_settings_it_may_change_is_closed(self):
        """Written out, like the catalogue. Widening it is a deliberate act
        that has to be made here too."""
        self.assertEqual(sorted(TUNABLES), sorted([
            "shell.adaptive", "shell.frame_cache", "shell.phosphor", "shell.fps",
            "shell.idle_fps", "shell.telemetry_interval", "shell.tick_interval",
            "windows.poll_interval", "voice.continuous", "companion.keep_alive",
            "evolution.poll_interval", "evolution.process_interval",
            "evolution.background_interval"]))
        for tunable in TUNABLES.values():
            # Nothing free-form: no setting here can name a program or a path.
            self.assertIn(tunable.kind, ("bool", "int", "float", "choice"))

    def test_take_reading_refuses_everything_that_is_not_a_reading(self):
        shell, _ = make_shell()
        if shell.capabilities is None:
            self.skipTest("no platform adapter")
        for capability in ("system.shutdown", "system.reboot", "process.terminate",
                           "filesystem.delete", "filesystem.write", "filesystem.read",
                           "network.wifi_off", "system.format_disk", ""):
            result = shell.take_reading(capability)
            self.assertFalse(result.ok, capability)
            self.assertIn("not a reading", result.error)

    def test_take_reading_and_answer_reading_are_one_rule(self):
        shell, _ = make_shell()
        if shell.capabilities is None:
            self.skipTest("no platform adapter")
        result = shell.take_reading("hardware.cpu_count")
        ok, said = shell.answer_reading("hardware.cpu_count")
        self.assertTrue(result.ok and ok)
        self.assertIn(str(result.value), said)
        self.assertEqual(shell.answer_reading("system.shutdown"),
                         (False, "system.shutdown is not a reading"))


# --------------------------------------------------------------------------
# 2. It launches, and is reachable the way every other screen is
# --------------------------------------------------------------------------
class TestItLaunches(unittest.TestCase):

    def setUp(self):
        self.shell, self.center = make_shell()

    def test_it_is_a_registered_screen(self):
        self.assertIn(EvolutionScreen, ALL_SCREENS)
        self.assertIsInstance(self.shell.screens["evolution"], EvolutionScreen)

    def test_the_home_column_opens_it(self):
        self.assertIn("evolution", [action for _key, action, _colour in COLUMN])
        self.assertTrue(self.shell.run_action("evolution"))
        self.assertEqual(self.shell.current, "evolution")

    def test_it_can_be_asked_for_by_name(self):
        for phrase in ("system evolution center", "evolution center", "system evolution"):
            self.shell.go("main")
            self.shell.handle_utterance(phrase)
            self.assertEqual(self.shell.current, "evolution", phrase)

    def test_a_sentence_about_evolution_does_not_open_it(self):
        """Only the full phrases are in the vocabulary; a bare word would
        fire inside ordinary conversation, as the bare ``home`` does."""
        self.assertIsNone(self.shell.dispatcher.vocabulary.phrases.get("evolution"))

    def test_every_section_builds_at_every_size(self):
        screen = self.shell.screens["evolution"]
        self.shell.go("evolution")
        self.center.discover()
        for width, height in SIZES:
            ctx = self.shell.context(width, height)
            for index, (key, _label, _full) in enumerate(TABS):
                screen._switch(index, key)
                screen.ensure_built(ctx)
                self.assertIsNotNone(screen.g.get("tabs"), f"{key} at {width}x{height}")
                for element in screen.g:
                    x, y, w, h = element.rect
                    self.assertLessEqual(x + w, width + 1, f"{element.name} in {key}")
                    self.assertLessEqual(y + h, height + 1, f"{element.name} in {key}")

    def test_every_section_draws(self):
        try:
            import cairo
        except ImportError:
            self.skipTest("pycairo not installed")
        screen = self.shell.screens["evolution"]
        self.shell.go("evolution")
        self.center.discover()
        self.center.run_checks(wait=True)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1280, 720)
        ctx = self.shell.context(1280, 720)
        for index, (key, _label, _full) in enumerate(TABS):
            screen._switch(index, key)
            screen.draw(cairo.Context(surface), ctx)
            # Every selectable row has something to say about itself.
            for row in screen._rows():
                screen.picked[key] = row[0]
                self.assertTrue(screen._blocks(), f"{key}: nothing shown for {row[0]}")

    def test_switched_off_it_says_so_and_builds_nothing(self):
        shell, _ = make_shell()
        shell.config.values["evolution"]["enabled"] = False
        shell._evolution, shell._evolution_built = None, False
        self.assertIsNone(shell.evolution())
        screen = shell.screens["evolution"]
        shell.go("evolution")
        screen.ensure_built(shell.context(1280, 720))
        self.assertIsNotNone(screen.notice_rect)
        screen.tick()                      # and does not raise

    def test_the_engine_is_not_imported_until_it_is_asked_for(self):
        """Boot pays nothing: registering the screen must not load the engine."""
        code = ("import sys; sys.path.insert(0, %r)\n"
                "from earth_os.screens import ALL_SCREENS\n"
                "from earth_os import core\n"
                "print(sorted(m for m in sys.modules if m.startswith('earth_os.evolution')))"
                % str(ROOT))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(out.stdout.strip(), "[]", out.stderr[-400:])

    def test_the_window_module_still_imports_nothing_heavy_at_the_top(self):
        """AGENTS.md §5: ``app.py`` must not import ``core`` or ``screens`` at
        module level. The center's wiring is inside functions."""
        tree = ast.parse((ROOT / "earth_os" / "app.py").read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {(n.module or "") for n in top if isinstance(n, ast.ImportFrom)}
        self.assertFalse({"evolution", "core", "screens"} & names)


# --------------------------------------------------------------------------
# 3. The metrics are the machine's
# --------------------------------------------------------------------------
@unittest.skipUnless(sys.platform.startswith("linux"), "compares against Linux directly")
class TestTheMetricsAreAccurate(unittest.TestCase):
    """Each figure is checked against a second, independent look at the
    machine. A test may read the kernel directly; the package may not."""

    @classmethod
    def setUpClass(cls):
        cls.shell, cls.center = make_shell()
        if cls.shell.capabilities is None:
            raise unittest.SkipTest("no platform adapter")
        cls.shell.monitor.poll(force=True)
        time.sleep(1.1)                     # processor load is a difference
        cls.center.sample(force=True)
        cls.snap = cls.center.snapshot

    @staticmethod
    def meminfo():
        out = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            out[key] = int(rest.split()[0]) * 1024
        return out

    def test_memory(self):
        memory = self.snap.get("memory")
        self.assertTrue(memory.ok, memory.why)
        info = self.meminfo()
        self.assertEqual(memory.value["total"], info["MemTotal"])
        used = info["MemTotal"] - info["MemAvailable"]
        self.assertAlmostEqual(memory.value["used"], used, delta=info["MemTotal"] * 0.05)
        self.assertAlmostEqual(memory.value["fraction"],
                               memory.value["used"] / memory.value["total"], places=6)
        self.assertEqual(memory.value["available"],
                         memory.value["total"] - memory.value["used"])

    def test_storage(self):
        disk = self.snap.get("disk")
        self.assertTrue(disk.ok, disk.why)
        usage = shutil.disk_usage("/")
        self.assertEqual(disk.value["total"], usage.total)
        self.assertAlmostEqual(disk.value["free"], usage.free, delta=usage.total * 0.01)

    def test_swap(self):
        swap = self.snap.get("swap")
        self.assertTrue(swap.ok, swap.why)
        self.assertEqual(swap.value["total"], self.meminfo().get("SwapTotal", 0))

    def test_processor(self):
        cpu = self.snap.get("cpu")
        self.assertTrue(cpu.ok, cpu.why)
        self.assertGreaterEqual(cpu.value, 0.0)
        self.assertLessEqual(cpu.value, 1.0)
        # And it is the monitor's figure, not a second sampler's.
        self.assertEqual(cpu.value, self.shell.monitor.cpu())
        self.assertEqual(self.snap.value("cpu_count"), len(os.sched_getaffinity(0)))
        self.assertEqual(len(self.snap.value("cores")), os.cpu_count())

    def test_uptime_and_processes(self):
        uptime = self.snap.get("uptime")
        self.assertTrue(uptime.ok, uptime.why)
        real = float(Path("/proc/uptime").read_text().split()[0])
        self.assertAlmostEqual(uptime.value, real, delta=10.0)
        running = sum(1 for entry in os.listdir("/proc") if entry.isdigit())
        self.assertAlmostEqual(self.snap.value("process_count"), running, delta=15)
        listed = self.snap.value("processes")
        self.assertAlmostEqual(len(listed), running, delta=15)

    def test_its_own_memory_is_this_process(self):
        own = self.snap.get("own_memory")
        self.assertTrue(own.ok, own.why)
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                real = int(line.split()[1]) * 1024
        self.assertAlmostEqual(own.value, real, delta=real * 0.10)

    def test_identity(self):
        from earth_os import __version__
        self.assertEqual(self.snap.value("version"), __version__)
        self.assertEqual(self.snap.value("kernel"), os.uname().release)

    def test_the_overview_shows_these_same_figures(self):
        screen = self.shell.screens["evolution"]
        fraction, text = screen._m_memory()
        self.assertAlmostEqual(fraction, self.snap.value("memory")["fraction"])
        self.assertIn("/", text)
        rows = dict(screen._overview_rows())
        self.assertEqual(rows["KERNEL"], os.uname().release)
        self.assertEqual(rows["RUNNING PROCESSES"], str(self.snap.value("process_count")))


# --------------------------------------------------------------------------
# 4. A reading that cannot be taken is unavailable — never zero
# --------------------------------------------------------------------------
class TestMissingReadings(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="earth-os-sec-broken-"))
        self.port = BrokenPort(self.home)
        self.center = EvolutionCenter(self.port, directory=self.home / "evolution")

    def test_the_monitor_says_why_a_reading_is_a_default(self):
        monitor = self.port.monitor
        self.assertEqual(monitor.cpu(), 0.0)            # what a gauge draws
        self.assertIn("no such sensor", monitor.problem("cpu"))
        self.assertEqual(monitor.memory(), (0.0, 0, 0))
        self.assertTrue(monitor.problem("memory"))

    def test_a_reading_that_recovers_stops_being_a_problem(self):
        class Flaky:
            fail = True

            def cpu_load(self):
                if self.fail:
                    raise PlatformError("counters unreadable")
                return 0.25
        flaky = Flaky()
        monitor = SystemMonitor(interval=0.0, platform=SimpleNamespace(hardware=flaky))
        monitor.cpu()
        self.assertTrue(monitor.problem("cpu"))
        flaky.fail = False
        self.assertEqual(monitor.cpu(), 0.25)
        self.assertEqual(monitor.problem("cpu"), "")

    def test_every_failed_reading_is_unavailable_with_a_reason(self):
        snap = Inspector(self.port).snapshot(processes=True)
        for name in ("cpu", "memory", "disk", "temperature", "uptime", "process_count",
                     "cpu_count", "processes", "render", "kernel", "platform"):
            reading = snap.get(name)
            self.assertFalse(reading.ok, f"{name} was reported as {reading.value!r}")
            self.assertTrue(reading.why, name)
            self.assertIsNone(snap.value(name))

    def test_nothing_is_concluded_from_a_reading_it_does_not_have(self):
        self.center.sample(force=True)
        findings = self.center.findings()
        found = by_id(findings)
        for name in ("processor.unavailable", "memory.unavailable", "storage.unavailable",
                     "thermal.unavailable", "rendering.unavailable"):
            self.assertIn(name, found)
        # Not one finding claims the machine is fine, or in trouble.
        for finding in findings:
            self.assertFalse(finding.id.endswith(".normal") and finding.area in
                             ("processor", "memory", "storage", "thermal"), finding.id)
            if finding.area == "applications":
                continue        # the missing adapter itself, which *was* recorded
            self.assertLess(severity_rank(finding.severity), severity_rank(MODERATE),
                            f"{finding.id} is {finding.severity} with no readings")
        self.assertEqual(found["applications.platform"].severity, HIGH)

    def test_the_rest_of_the_center_still_works(self):
        self.center.sample(force=True)
        self.assertTrue(self.center.optimizations())
        self.assertTrue(self.center.capabilities())
        self.center.discover()
        for kind in KINDS:
            report = self.center.report(kind)
            text = Reporter.as_text(report)
            json.loads(Reporter.as_json(report))
            self.assertIn("Readings that were unavailable", text, kind)
        self.assertEqual(self.center.explain(wait=True), True)

    def test_a_missing_thermal_sensor_is_said_plainly(self):
        found = by_id(HealthAnalyzer().analyze(snapshot(temperature=None)))
        self.assertEqual(found["thermal.unavailable"].severity, INFORMATIONAL)
        self.assertIn("no thermal sensor", found["thermal.unavailable"].evidence[0])

    def test_an_uptime_of_zero_is_not_believed(self):
        """The Linux adapter answers a failed uptime read with 0.0. The
        center does not pass that on as "up for no time at all"."""
        monitor = SimpleNamespace(uptime=lambda: 0.0, problem=lambda key: "")
        snap = Snapshot()
        reading = Inspector(SimpleNamespace(monitor=monitor))._from_monitor(
            snap, "uptime", "uptime", lambda m: float(m.uptime()))
        self.assertTrue(reading.ok)             # the monitor did not complain…
        shell, center = make_shell()
        shell.monitor.uptime = lambda: 0.0      # …so the inspector has to
        center.sample(force=True)
        self.assertFalse(center.snapshot.get("uptime").ok)


# --------------------------------------------------------------------------
# 5. The Health Analyzer
# --------------------------------------------------------------------------
class TestHealthRules(unittest.TestCase):

    def setUp(self):
        self.analyzer = HealthAnalyzer(Trends())

    def severity(self, finding_id, **figures):
        found = by_id(self.analyzer.analyze(snapshot(**figures)))
        return found[finding_id].severity if finding_id in found else None

    def test_the_five_severities_are_the_directives(self):
        self.assertEqual(SEVERITIES, ("INFORMATIONAL", "LOW", "MODERATE", "HIGH", "CRITICAL"))

    def test_processor(self):
        self.assertEqual(self.severity("processor.normal", cpu=0.30), INFORMATIONAL)
        self.assertEqual(self.severity("processor.busy", cpu=0.60), MODERATE)
        self.assertEqual(self.severity("processor.saturated", cpu=0.85), HIGH)
        self.assertEqual(self.severity("processor.busy", cpu=0.10, load=(4.2, 1, 1), cores=4),
                         MODERATE)
        self.assertEqual(self.severity("processor.saturated", cpu=0.10, load=(6.5, 1, 1),
                                       cores=4), HIGH)

    def test_a_processor_saturated_for_a_minute_is_critical(self):
        trends = Trends()
        for i in range(8):
            trends.add(Sample(at=i * 10.0, wall=0, values={"cpu": 0.97}))
        found = by_id(HealthAnalyzer(trends).analyze(snapshot(cpu=0.97)))
        self.assertEqual(found["processor.saturated"].severity, CRITICAL)
        # One dip in that minute and it is HIGH again: not sustained.
        trends.add(Sample(at=75.0, wall=0, values={"cpu": 0.40}))
        found = by_id(HealthAnalyzer(trends).analyze(snapshot(cpu=0.97)))
        self.assertEqual(found["processor.saturated"].severity, HIGH)

    def test_memory(self):
        self.assertEqual(self.severity("memory.normal", memory=0.50), INFORMATIONAL)
        self.assertEqual(self.severity("memory.pressure", memory=0.80), MODERATE)
        self.assertEqual(self.severity("memory.pressure", memory=0.90), HIGH)
        self.assertEqual(self.severity("memory.pressure", memory=0.96), CRITICAL)

    def test_swapping(self):
        self.assertEqual(self.severity("memory.swapping", swap=(4 * GB, 2 * GB)), MODERATE)
        self.assertIsNone(self.severity("memory.swapping", swap=(4 * GB, 100 * 1024 ** 2)))
        self.assertIsNone(self.severity("memory.swapping", swap=(0, 0)))

    def test_storage(self):
        self.assertEqual(self.severity("storage.normal", disk=0.50), INFORMATIONAL)
        self.assertEqual(self.severity("storage.pressure", disk=0.82), LOW)
        self.assertEqual(self.severity("storage.pressure", disk=0.91), MODERATE)
        self.assertEqual(self.severity("storage.pressure", disk=0.96), HIGH)
        self.assertEqual(self.severity("storage.pressure", disk=0.99), CRITICAL)

    def test_temperature(self):
        self.assertEqual(self.severity("thermal.normal", temperature=48.0), INFORMATIONAL)
        self.assertEqual(self.severity("thermal.hot", temperature=78.0), MODERATE)
        self.assertEqual(self.severity("thermal.hot", temperature=88.0), HIGH)
        self.assertEqual(self.severity("thermal.hot", temperature=97.0), CRITICAL)

    def test_processes(self):
        listed = [process(1, "init", 10 * 1024 ** 2),
                  process(2, "browser", 3 * GB, command="/usr/bin/browser --tab"),
                  process(3, "browser", 1 * GB, command="/usr/bin/browser --tab"),
                  process(4, "gone", 0, state="Z")]
        found = by_id(self.analyzer.analyze(snapshot(processes=listed)))
        self.assertEqual(found["processes.defunct"].severity, LOW)
        self.assertIn("1 of 4", found["processes.defunct"].evidence[0])
        # 4 GB of 8 GB in one program: worth a line, and it names the program.
        self.assertEqual(found["processes.memory"].severity, LOW)
        self.assertIn("browser", found["processes.memory"].title)
        # No process carries a processor figure, and that is what is said.
        self.assertIn("processes.cpu-unreported", found)

    def test_it_never_names_a_busiest_program_it_was_not_told_about(self):
        listed = [process(1, "a", 1024 ** 2), process(2, "b", 1024 ** 2)]
        for finding in self.analyzer.analyze(snapshot(processes=listed)):
            self.assertNotIn("most processor", " ".join(finding.evidence))

    def test_rendering(self):
        base = {"state": "IDLE", "fps": 30, "adaptive": True, "frames_drawn": 400,
                "draws_timed": 400, "draw_ms_mean": 6.0, "draw_ms_peak": 12.0, "jobs": {}}
        self.assertEqual(self.severity("rendering.normal", render=base), INFORMATIONAL)
        self.assertEqual(self.severity("rendering.heavy",
                                       render={**base, "draw_ms_mean": 20.0}), LOW)
        self.assertEqual(self.severity("rendering.over-budget",
                                       render={**base, "draw_ms_mean": 41.0}), HIGH)
        self.assertEqual(self.severity("rendering.fixed-clock",
                                       render={**base, "adaptive": False}), MODERATE)
        # Too few draws to call it: nothing is concluded from five frames.
        self.assertIsNone(self.severity("rendering.over-budget",
                                        render={**base, "draws_timed": 5,
                                                "draw_ms_mean": 90.0}))

    def test_services_are_said_to_be_unmeasurable_not_healthy(self):
        found = by_id(self.analyzer.analyze(snapshot()))
        finding = found["services.unmeasurable"]
        self.assertEqual(finding.severity, INFORMATIONAL)
        self.assertIn("not reporting that all is well", finding.explanation)
        self.assertEqual(finding.remedy, "service-state")

    def test_start_up_errors_and_the_failed_start_log(self):
        found = by_id(self.analyzer.analyze(
            snapshot(), errors={"platform": "no adapter", "earth mind": "", "world model": "boom"},
            error_log=("/tmp/last-error.log", 120.0)))
        self.assertEqual(found["applications.platform"].severity, HIGH)
        self.assertEqual(found["applications.world model"].severity, MODERATE)
        self.assertNotIn("applications.earth mind", found)
        self.assertEqual(found["applications.last-error"].severity, MODERATE)
        old = by_id(self.analyzer.analyze(snapshot(), error_log=("/x", 9 * 86400.0)))
        self.assertEqual(old["applications.last-error"].severity, LOW)

    def test_the_doctors_rows_become_findings(self):
        rows = [("ok", "python", "3.12"), ("warn", "bluetooth", "no bluetooth control\nmore"),
                ("fail", "display", "no display")]
        found = by_id(self.analyzer.analyze(snapshot(), doctor=rows))
        self.assertEqual(found["configuration.check.display"].severity, HIGH)
        self.assertEqual(found["configuration.check.bluetooth"].severity, LOW)
        self.assertEqual(found["configuration.check.bluetooth"].evidence,
                         ["no bluetooth control"])
        self.assertIn("1 of 3", found["configuration.checks"].title)
        unchecked = by_id(self.analyzer.analyze(snapshot(), doctor=None))
        self.assertIn("configuration.unchecked", unchecked)

    def test_an_aggressive_configuration_is_noticed(self):
        config = Config.load(Path(tempfile.mkdtemp()) / "c.toml")
        config.values["shell"]["fps"] = 120
        config.values["shell"]["telemetry_interval"] = 0.1
        found = by_id(self.analyzer.analyze(snapshot(), config=config))
        self.assertEqual(found["configuration.shell.fps"].severity, LOW)
        self.assertEqual(found["configuration.shell.telemetry_interval"].severity, LOW)

    def test_every_finding_above_informational_has_evidence_and_advice(self):
        listed = [process(1, "hog", 7 * GB), process(2, "z", 0, state="Z")]
        render = {"state": "RENDER", "fps": 30, "adaptive": False, "frames_drawn": 9000,
                  "draws_timed": 9000, "draw_ms_mean": 50.0, "draw_ms_peak": 80.0, "jobs": {}}
        findings = self.analyzer.analyze(
            snapshot(cpu=0.99, memory=0.97, disk=0.99, temperature=99.0,
                     swap=(4 * GB, 3 * GB), processes=listed, render=render),
            errors={"platform": "gone"}, error_log=("/x", 5.0),
            doctor=[("fail", "gtk4", "missing")])
        raised = [f for f in findings if f.severity != INFORMATIONAL]
        self.assertGreaterEqual(len(raised), 10)
        for finding in raised:
            self.assertTrue(finding.evidence, f"{finding.id} has no evidence")
            self.assertTrue(finding.explanation, f"{finding.id} has no explanation")
        # Most severe first.
        ranks = [severity_rank(f.severity) for f in findings]
        self.assertEqual(ranks, sorted(ranks, reverse=True))


class TestTheVerdictAgreesWithTheMachinesOwn(unittest.TestCase):
    """``performance_report`` answers "why is my computer slow". Give it and
    the Health Analyzer the same readings and they must not disagree."""

    WORDS = {"the processor is saturated": ("processor.saturated", HIGH),
             "the processor is busy": ("processor.busy", MODERATE),
             "memory is nearly full": ("memory.pressure", HIGH),
             "memory is under pressure": ("memory.pressure", MODERATE),
             "the machine is swapping": ("memory.swapping", MODERATE),
             "storage is nearly full": ("storage.pressure", HIGH)}

    def test_across_a_grid_of_readings(self):
        corpus = CapabilityCorpus(None)
        total = 8 * GB
        for cpu in (0.05, 0.59, 0.60, 0.84, 0.85, 0.99):
            for memory in (0.20, 0.79, 0.80, 0.89, 0.90, 0.93):
                for disk in (0.40, 0.94, 0.95):
                    for swap in ((0, 0), (4 * GB, 2 * GB)):
                        used = round(total * memory)
                        ok, said = corpus.explain("performance_report", {
                            "hardware.cpu_load": cpu, "hardware.load_average": (0.1, 0.1, 0.1),
                            "hardware.cpu_count": 4,
                            "hardware.memory": (total, total - used),
                            "hardware.swap": (swap[0], swap[0] - swap[1]),
                            "hardware.disk": (100 * GB, 100 * GB - round(100 * GB * disk)),
                            "process.list": []})
                        self.assertTrue(ok)
                        found = by_id(HealthAnalyzer().analyze(
                            snapshot(cpu=cpu, memory=memory, disk=disk, swap=swap)))
                        where = f"cpu={cpu} memory={memory} disk={disk} swap={swap}"
                        for words, (finding, least) in self.WORDS.items():
                            spoken = words in said
                            got = found.get(finding)
                            rank = severity_rank(got.severity) if got is not None else -1
                            if spoken:
                                self.assertGreaterEqual(
                                    rank, severity_rank(least),
                                    f"{where}: the machine says '{words}', the center "
                                    f"says {got.severity if got else 'nothing'}")
                        if "Nothing I can measure explains a slowdown" in said:
                            for finding in found.values():
                                if finding.area in ("processor", "memory"):
                                    self.assertLess(severity_rank(finding.severity),
                                                    severity_rank(MODERATE), where)

    def test_the_cut_points_are_the_same_numbers(self):
        self.assertEqual((health.CPU_BUSY, health.CPU_SATURATED), (0.60, 0.85))
        self.assertEqual((health.LOAD_BUSY, health.LOAD_SATURATED), (1.0, 1.5))
        self.assertEqual((health.MEMORY_PRESSURE, health.MEMORY_NEARLY_FULL), (0.80, 0.90))
        self.assertEqual(health.STORAGE_NEARLY_FULL, 0.95)
        self.assertEqual((health.SWAP_BYTES, health.SWAP_FRACTION), (256 * 1024 ** 2, 0.25))


class TestBehaviourOverTime(unittest.TestCase):

    def trends(self, values, step=60.0, name="own_memory"):
        trends = Trends()
        for i, value in enumerate(values):
            trends.add(Sample(at=i * step, wall=0, values={name: value}))
        return trends

    def test_the_history_is_bounded(self):
        trends = Trends(capacity=20)
        for i in range(500):
            trends.add(Sample(at=float(i), wall=0, values={"cpu": 0.5}))
        self.assertEqual(len(trends), 20)

    def test_figures_say_what_they_rest_on(self):
        stat = self.trends([0.2, 0.4, None, 0.6], name="cpu").stat("cpu")
        self.assertEqual((stat.count, stat.low, stat.high), (3, 0.2, 0.6))
        self.assertAlmostEqual(stat.mean, 0.4)
        self.assertEqual(stat.span, 180.0)
        self.assertIsNone(Trends().stat("cpu"))

    def test_steady_growth_is_a_possible_leak(self):
        mb = 1024 ** 2
        trends = self.trends([100 * mb + i * 8 * mb for i in range(15)])
        found = by_id(HealthAnalyzer(trends).analyze(snapshot()))
        self.assertEqual(found["leaks.own-memory"].severity, MODERATE)
        self.assertIn("15 samples", found["leaks.own-memory"].evidence[0])

    def test_too_little_history_says_so_instead_of_guessing(self):
        mb = 1024 ** 2
        for values, step in (([100 * mb, 400 * mb, 900 * mb], 60.0),          # three samples
                             ([100 * mb + i * 50 * mb for i in range(15)], 5.0)):   # 70 seconds
            found = by_id(HealthAnalyzer(self.trends(values, step)).analyze(snapshot()))
            self.assertIn("leaks.insufficient", found)
            self.assertNotIn("leaks.own-memory", found)

    def test_memory_that_rose_and_fell_back_is_not_a_leak(self):
        mb = 1024 ** 2
        values = [100 * mb] * 5 + [400 * mb] * 5 + [110 * mb] * 5
        found = by_id(HealthAnalyzer(self.trends(values)).analyze(snapshot()))
        self.assertIn("leaks.none", found)

    def test_a_counter_that_was_reset_is_not_a_negative_rate(self):
        trends = self.trends([100, 200, 5], step=1.0, name="frames_drawn")
        self.assertIsNone(trends.rate("frames_drawn"))
        self.assertEqual(self.trends([0, 30, 60], 1.0, "frames_drawn").rate("frames_drawn"), 30.0)


# --------------------------------------------------------------------------
# 6. Optimizations: six answers each, and an estimate is called an estimate
# --------------------------------------------------------------------------
class TestOptimizations(unittest.TestCase):

    def advise(self, snap=None, **settings):
        config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
        for name, value in settings.items():
            section, key = name.split("__")
            config.values.setdefault(section, {})[key] = value
        snap = snap or snapshot()
        findings = HealthAnalyzer().analyze(snap, config=config)
        return {o.id: o for o in OptimizationAdvisor().advise(config, snap, findings)}

    def test_every_one_answers_all_six_questions(self):
        for found in (self.advise(), self.advise(shell__adaptive=False, shell__fps=60,
                                                 shell__idle_fps=2, voice__continuous=True,
                                                 voice__enabled=True,
                                                 shell__telemetry_interval=0.2)):
            self.assertGreaterEqual(len(found), 8)
            for item in found.values():
                for answer in ("changes", "why", "benefits", "drawbacks", "reverse"):
                    self.assertTrue(getattr(item, answer).strip(), f"{item.id}.{answer}")
                self.assertIsInstance(item.restart, bool)
                self.assertTrue(item.reason.strip(), item.id)

    def test_an_expected_benefit_is_never_presented_as_measured(self):
        for item in self.advise(shell__adaptive=False, shell__idle_fps=1).values():
            self.assertTrue(item.benefits.startswith("ESTIMATE:"), item.id)
            self.assertNotIn("measured", item.benefits.lower(), item.id)
            self.assertTrue(item.basis.strip(), f"{item.id} does not say where it comes from")

    def test_figures_from_another_machine_say_so(self):
        for item in self.advise().values():
            if "RENDERING.md" in item.basis:
                self.assertIn("Not measured on this machine", item.basis, item.id)

    def test_what_is_already_so_is_not_recommended(self):
        found = self.advise()
        for name in ("adaptive-rendering", "frame-cache", "idle-floor"):
            self.assertEqual(found[name].state, "IN EFFECT")
            self.assertIsNone(found[name].change)

    def test_what_the_configuration_gets_wrong_is_recommended(self):
        found = self.advise(shell__adaptive=False, shell__frame_cache=False,
                            shell__idle_fps=2, shell__fps=60, shell__telemetry_interval=0.2,
                            evolution__poll_interval=0.5)
        for name, change in (("adaptive-rendering", ("shell", "adaptive", True)),
                             ("frame-cache", ("shell", "frame_cache", True)),
                             ("idle-floor", ("shell", "idle_fps", 0)),
                             ("frame-rate", ("shell", "fps", 30)),
                             ("telemetry-interval", ("shell", "telemetry_interval", 1.0)),
                             ("center-poll", ("evolution", "poll_interval", 2.0))):
            self.assertEqual(found[name].state, "RECOMMENDED", name)
            got = found[name].change
            self.assertEqual((got["section"], got["key"], got["value"]), change)

    def test_the_phosphor_effect_is_only_recommended_off_on_a_measurement(self):
        self.assertEqual(self.advise()["phosphor-off"].state, "AVAILABLE")
        slow = {"state": "RENDER", "fps": 30, "adaptive": True, "frames_drawn": 900,
                "draws_timed": 900, "draw_ms_mean": 44.0, "draw_ms_peak": 60.0, "jobs": {}}
        found = self.advise(snapshot(render=slow))
        self.assertEqual(found["phosphor-off"].state, "RECOMMENDED")
        self.assertIn("44.0 ms", found["phosphor-off"].reason)

    def test_memory_pressure_recommends_releasing_the_model(self):
        self.assertEqual(self.advise()["companion-keep-alive"].state, "AVAILABLE")
        self.assertEqual(self.advise(snapshot(memory=0.92))["companion-keep-alive"].state,
                         "RECOMMENDED")

    def test_every_change_it_offers_is_one_it_is_allowed_to_make(self):
        for item in self.advise(shell__adaptive=False, shell__frame_cache=False,
                                shell__idle_fps=2, voice__continuous=True,
                                evolution__poll_interval=0.5).values():
            if item.change is not None:
                validate(item.change)           # raises if it is not


# --------------------------------------------------------------------------
# 7. The Capability Registry
# --------------------------------------------------------------------------
class TestCapabilityRegistry(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.shell, cls.center = make_shell()
        cls.center.sample(force=True)
        cls.entries = cls.center.capabilities()
        cls.found = {e.id: e for e in cls.entries}

    def test_the_five_groups_are_the_directives(self):
        self.assertEqual(CATEGORIES, ("MISSING", "INCOMPLETE", "NEEDS IMPROVEMENT",
                                      "EXPERIMENTAL", "FUTURE"))
        groups = CapabilityRegistry.grouped(self.entries)
        self.assertEqual(list(groups), list(CATEGORIES))
        self.assertEqual(sum(len(v) for v in groups.values()), len(self.entries))

    def test_every_entry_says_what_was_found_and_what_it_would_take(self):
        self.assertGreaterEqual(len(self.entries), 12)
        for entry in self.entries:
            self.assertIn(entry.category, CATEGORIES, entry.id)
            self.assertTrue(entry.evidence, f"{entry.id}: no evidence")
            self.assertTrue(entry.description, f"{entry.id}: no development description")
            self.assertTrue(entry.requirements, f"{entry.id}: no implementation requirements")
            self.assertIn(entry.basis, ("detected live", "source review", "policy"))
            self.assertIn(entry.rung, (0, 1, 2, 3))

    def test_what_the_catalogue_cannot_read_is_missing(self):
        if self.shell.capabilities is None:
            self.skipTest("no platform adapter")
        for name in ("service-state", "system-logs", "application-management"):
            self.assertEqual(self.found[name].category, "MISSING")
            self.assertEqual(self.found[name].rung, 3, "new power is a person's to grant")
            self.assertEqual(self.found[name].basis, "detected live")

    def test_detection_follows_the_machine_not_a_list(self):
        """Give the registry a catalogue that *has* a service reading and the
        gap is no longer reported."""
        class Registry:
            def names(self):
                return ["service.failed", "hardware.memory"]

            def supported(self, _name):
                return True
        entries = CapabilityRegistry().survey(Facts(SimpleNamespace(capabilities=Registry())))
        self.assertNotIn("service-state", {e.id for e in entries})

    def test_processor_time_per_program_is_detected_from_the_reading(self):
        with_figures = Facts(SimpleNamespace(), snapshot(processes=[process(1, "a", 1, cpu=12.0)]))
        without = Facts(SimpleNamespace(), snapshot(processes=[process(1, "a", 1)]))
        ids = lambda facts: {e.id for e in CapabilityRegistry().survey(facts)}   # noqa: E731
        self.assertNotIn("per-process-cpu", ids(with_figures))
        self.assertIn("per-process-cpu", ids(without))

    def test_a_machine_with_no_mechanism_for_an_area_says_which(self):
        platform = SimpleNamespace(name="Test", mechanisms=lambda: {
            "volume": "none", "bluetooth": "bluez", "thermal": "none"})
        entries = {e.id: e for e in CapabilityRegistry().survey(
            Facts(SimpleNamespace(platform=platform)))}
        self.assertIn("platform-volume", entries)
        self.assertIn("platform-thermal", entries)
        self.assertNotIn("platform-bluetooth", entries)
        self.assertEqual(entries["platform-volume"].category, "INCOMPLETE")

    def test_the_doctors_findings_reach_the_registry(self):
        rows = [("ok", "reasoning", "ollama up"), ("ok", "companion", "model pulled"),
                ("ok", "computer", "service"), ("ok", "earth mind", "ready")]
        entries = {e.id for e in CapabilityRegistry().survey(
            Facts(self.shell, self.center.snapshot, rows))}
        for name in ("local-llm", "cdos-integration", "terminal-intelligence"):
            self.assertNotIn(name, entries, f"{name} reported although its check passed")
        unchecked = {e.id: e for e in CapabilityRegistry().survey(
            Facts(self.shell, self.center.snapshot, None))}
        self.assertIn("not checked this session", " ".join(unchecked["local-llm"].evidence))

    def test_it_adds_nothing_to_the_capability_catalogue(self):
        from earth_os.platform.capabilities import CATALOGUE
        self.assertEqual(len(CATALOGUE), 68)


# --------------------------------------------------------------------------
# 8. The roadmap: proposals can be created and managed
# --------------------------------------------------------------------------
class TestTheRoadmap(unittest.TestCase):

    def setUp(self):
        self.shell, self.center = make_shell()
        self.planner = self.center.planner
        self.center.sample(force=True)

    def new(self, source="capability:test", **kw):
        fields = dict(title="A thing", kind="capability", source=source,
                      testing=["the suite passes"], reverse="git revert")
        fields.update(kw)
        return self.planner.discover(**fields)[0]

    def test_the_eight_statuses_are_the_directives(self):
        self.assertEqual(STATUSES, ("DISCOVERED", "PROPOSED", "APPROVED", "IN DEVELOPMENT",
                                    "TESTING", "COMPLETED", "REJECTED", "ROLLED BACK"))

    def test_discovery_creates_records_and_only_records(self):
        created = self.center.discover()
        self.assertGreaterEqual(len(created), 5)
        for proposal in created:
            self.assertEqual(proposal.status, DISCOVERED)
            self.assertTrue(proposal.testing, f"{proposal.id}: no testing requirements")
            self.assertTrue(proposal.reverse, f"{proposal.id}: no way back stated")
            self.assertEqual(proposal.events[0]["by"], "engine")
        # Noticing the same things again is not a second set of proposals.
        self.assertEqual(self.center.discover(), [])

    def test_what_a_person_rejected_is_not_rediscovered(self):
        first = self.center.discover()[0]
        self.planner.reject(first.id, by="operator")
        self.assertEqual(self.center.discover(), [])
        self.assertEqual(self.planner.get(first.id).status, REJECTED)

    def test_proposing_from_a_panel_writes_it_up_and_approves_nothing(self):
        entry = self.center.capabilities()[0]
        proposal = self.center.propose_from("capability", entry.id, by="operator")
        self.assertEqual(proposal.status, PROPOSED)
        self.assertEqual(proposal.source, f"capability:{entry.id}")
        again = self.center.propose_from("capability", entry.id, by="operator")
        self.assertEqual(again.id, proposal.id)
        self.assertIsNone(self.center.propose_from("capability", "no-such-thing"))

    def test_the_whole_line_for_work_a_person_carries_out(self):
        p = self.new()
        self.planner.propose(p.id)
        self.planner.approve(p.id, by="operator")
        self.planner.implement(p.id, by="operator")
        self.planner.begin_testing(p.id, by="operator")
        self.planner.complete(p.id, by="operator", note="suite green")
        self.assertEqual(p.status, COMPLETED)
        self.assertEqual([e["status"] for e in p.events if e["event"] == "status"],
                         [DISCOVERED, PROPOSED, APPROVED, IN_DEVELOPMENT, TESTING, COMPLETED])
        self.assertEqual({e["by"] for e in p.events[2:]}, {"operator"})
        self.planner.roll_back(p.id, by="operator")
        self.assertEqual(p.status, ROLLED_BACK)
        self.planner.reconsider(p.id, by="operator")
        self.assertEqual(p.status, PROPOSED)

    def test_steps_cannot_be_skipped(self):
        p = self.new()
        for method in ("implement", "begin_testing", "complete", "roll_back"):
            with self.assertRaises(Refused, msg=method):
                getattr(self.planner, method)(p.id, by="operator")
        self.planner.approve(p.id, by="operator")
        for method in ("begin_testing", "complete", "roll_back"):
            with self.assertRaises(Refused, msg=method):
                getattr(self.planner, method)(p.id, by="operator")
        self.planner.implement(p.id, by="operator")
        with self.assertRaises(Refused):
            self.planner.complete(p.id, by="operator")        # not tested yet
        with self.assertRaises(Refused):
            self.planner.reject(p.id, by="operator")          # under way: roll it back
        self.assertEqual(p.status, IN_DEVELOPMENT)

    def test_approving_a_discovery_records_both_decisions(self):
        p = self.new()
        self.planner.approve(p.id, by="operator")
        self.assertEqual([e["status"] for e in p.events], [DISCOVERED, PROPOSED, APPROVED])

    def test_postpone_resume_and_priority(self):
        low, high = self.new("x:low"), self.new("x:high")
        self.planner.prioritize(high.id, by="operator", step=1)
        self.assertEqual(high.priority, "HIGH")
        self.assertEqual(self.planner.ordered()[0].id, high.id)
        self.planner.postpone(high.id, by="operator")
        self.assertTrue(high.postponed)
        self.assertEqual(high.status, DISCOVERED, "postponed is a mark, not a status")
        self.assertEqual(self.planner.ordered()[0].id, low.id)
        self.planner.resume(high.id, by="operator")
        self.assertFalse(high.postponed)
        for _ in range(6):
            self.planner.prioritize(low.id, by="operator", step=-1)
        self.assertEqual(low.priority, "LOW")

    def test_work_waits_for_what_it_depends_on(self):
        first = self.new("capability:first")
        second = self.new("capability:second", dependencies=["capability:first"])
        self.planner.approve(second.id, by="operator")
        with self.assertRaises(Refused) as caught:
            self.planner.implement(second.id, by="operator")
        self.assertIn(first.id, str(caught.exception))
        for step in ("approve", "implement", "begin_testing", "complete"):
            getattr(self.planner, step)(first.id, by="operator")
        self.planner.implement(second.id, by="operator")
        self.assertEqual(second.status, IN_DEVELOPMENT)

    def test_the_roadmap_survives_a_restart(self):
        p = self.new()
        self.planner.approve(p.id, by="operator", note="yes")
        again = Planner(Store(self.center.store.root))
        self.assertEqual(again.get(p.id).status, APPROVED)
        self.assertEqual(again.get(p.id).events[-1]["note"], "yes")
        self.assertNotEqual(again.discover(title="n", kind="health", source="x:new")[0].id, p.id)

    def test_every_decision_is_in_the_history(self):
        p = self.new()
        self.planner.approve(p.id, by="ada")
        self.planner.postpone(p.id, by="ada")
        history = self.center.store.read_history()
        self.assertEqual([(e["event"], e["status"], e["by"]) for e in history],
                         [("status", DISCOVERED, "engine"), ("status", PROPOSED, "ada"),
                          ("status", APPROVED, "ada"), ("postponed", APPROVED, "ada")])
        raw = self.center.store.history_path.read_text().splitlines()
        self.assertEqual(len(raw), 4)

    def test_the_history_is_only_ever_added_to(self):
        tree = ast.parse((PACKAGE / "store.py").read_text(encoding="utf-8"))
        modes = [node.args[0].value for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "open" and node.args
                 and isinstance(node.args[0], ast.Constant)]
        self.assertEqual(modes, ["a"])

    def test_the_screens_buttons_follow_the_status(self):
        screen = self.shell.screens["evolution"]
        self.shell.go("evolution")
        screen._go_section("roadmap")
        labels = lambda: [label for label, _c, _f in screen._actions()]      # noqa: E731
        self.assertEqual(labels(), ["DISCOVER"])
        manual = self.new("capability:manual", rung=3)
        screen.picked["roadmap"] = manual.id
        self.assertEqual(labels()[:3], ["PROPOSE", "APPROVE", "REJECT"])
        screen._decide("approve", manual.id)
        self.assertEqual(manual.events[-1]["by"], screen.operator())
        self.assertEqual(labels()[0], "START WORK")
        # New machine power is never offered to an agent.
        self.shell.agent = SimpleNamespace(ask=lambda _t: True, problem="")
        self.assertNotIn("HAND TO CLAUDE", labels())
        verifiable = self.new("capability:mechanism", rung=2)
        self.planner.approve(verifiable.id, by="operator")
        screen.picked["roadmap"] = verifiable.id
        self.assertIn("HAND TO CLAUDE", labels())

    def test_handing_work_to_the_agent_is_a_press_and_goes_through_its_gate(self):
        asked = []
        self.shell.agent = SimpleNamespace(ask=lambda text: asked.append(text) or True,
                                           problem="")
        screen = self.shell.screens["evolution"]
        p = self.new("capability:mechanism", rung=2, title="Per-process time")
        self.planner.approve(p.id, by="operator")
        self.assertEqual(asked, [], "nothing reaches the agent before the press")
        screen._hand_over(p.id)
        self.assertEqual(len(asked), 1)
        self.assertIn("Per-process time", asked[0])
        self.assertIn("AGENTS.md", asked[0])
        self.assertEqual(p.status, IN_DEVELOPMENT)
        self.assertEqual(self.shell.current, "claude")

    def test_an_agent_that_declines_leaves_the_proposal_where_it_was(self):
        self.shell.agent = SimpleNamespace(ask=lambda _t: False, problem="not installed")
        p = self.new("capability:mechanism", rung=2)
        self.planner.approve(p.id, by="operator")
        self.shell.screens["evolution"]._hand_over(p.id)
        self.assertEqual(p.status, APPROVED)


# --------------------------------------------------------------------------
# 9. Nothing changes without approval
# --------------------------------------------------------------------------
def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


def tree(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


class TestNothingChangesWithoutApproval(unittest.TestCase):

    def setUp(self):
        self.shell, self.center = make_shell()
        self.config_path = Path(self.shell.config.path)
        self.home = self.config_path.parent
        self.planner = self.center.planner

    def optimization(self, status=PROPOSED):
        """A real, applicable proposal: stop drawing the phosphor effect."""
        proposal = self.center.propose_from("optimization", "phosphor-off", by="operator")
        self.assertEqual(proposal.change, {"section": "shell", "key": "phosphor", "value": False})
        if status == APPROVED:
            self.planner.approve(proposal.id, by="operator")
        return proposal

    def test_everything_the_engine_does_unprompted_leaves_the_machine_alone(self):
        """Sample, analyze, advise, survey, discover, report, check, explain —
        twenty times over. The configuration file is the same bytes, the live
        configuration is the same values, and the only files that exist are
        the center's own records."""
        before = digest(self.config_path)
        values = json.dumps(self.shell.config.values, sort_keys=True, default=str)
        for _ in range(20):
            self.center.sample(visible=True, force=True)
            self.center.sample(visible=False)
            self.center.findings(), self.center.optimizations(), self.center.capabilities()
            self.center.discover()
            for kind in KINDS:
                self.center.report(kind)
        self.center.run_checks(wait=True)
        self.center.explain(wait=True)
        self.shell.evolution_background()
        self.assertEqual(digest(self.config_path), before)
        self.assertEqual(json.dumps(self.shell.config.values, sort_keys=True, default=str), values)
        self.assertEqual(tree(self.home), ["config.toml", "evolution/history.jsonl",
                                           "evolution/roadmap.json"])
        for proposal in self.planner.proposals.values():
            self.assertEqual(proposal.status, DISCOVERED,
                             f"{proposal.id} moved to {proposal.status} with nobody pressing")
            self.assertIsNone(proposal.applied)

    def test_a_proposal_that_is_not_approved_cannot_be_applied(self):
        before = digest(self.config_path)
        proposal = self.optimization()
        for status in (DISCOVERED, PROPOSED, REJECTED, IN_DEVELOPMENT, TESTING, COMPLETED,
                       ROLLED_BACK):
            proposal.status = status
            with self.assertRaises(ChangeRefused, msg=status):
                self.center.applier.apply(proposal)
        proposal.status = PROPOSED
        with self.assertRaises(Refused) as caught:
            self.planner.implement(proposal.id, by="operator")
        self.assertIn("without approval", str(caught.exception))
        self.assertEqual(digest(self.config_path), before)
        self.assertTrue(self.shell.config.get("shell", "phosphor"))
        self.assertFalse((self.home / "evolution" / "backups").exists())

    def test_approval_alone_changes_nothing(self):
        before = digest(self.config_path)
        self.optimization(APPROVED)
        self.assertEqual(digest(self.config_path), before)

    def test_an_approved_change_is_backed_up_made_and_recorded(self):
        original = self.config_path.read_text()
        proposal = self.optimization(APPROVED)
        self.planner.implement(proposal.id, by="operator", before=self.center.vitals())

        self.assertEqual(proposal.status, IN_DEVELOPMENT)
        self.assertFalse(self.shell.config.get("shell", "phosphor"))
        # The file differs in exactly one line.
        changed = [(a, b) for a, b in zip(original.splitlines(),
                                          self.config_path.read_text().splitlines()) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertRegex(changed[0][0], r"^phosphor\s*=\s*true")
        self.assertRegex(changed[0][1], r"^phosphor = false")
        # The person's note on that line is still there.
        self.assertEqual(changed[0][0].partition("#")[2], changed[0][1].partition("#")[2])
        self.assertEqual(len(original.splitlines()),
                         len(self.config_path.read_text().splitlines()))
        # The backup is the file as it was.
        backup = Path(proposal.applied["backup"])
        self.assertEqual(backup.read_text(), original)
        self.assertEqual(backup.parent, self.home / "evolution" / "backups")
        # The history says what, from what, where the copy is and how to undo it.
        record = [e for e in self.center.store.read_history()
                  if e["event"] == "change-applied"][0]
        self.assertEqual((record["setting"], record["previous"], record["value"]),
                         ("shell.phosphor", True, False))
        self.assertEqual(record["backup"], str(backup))
        self.assertIn("ROLL BACK", record["rollback"])
        self.assertIn("before", proposal.measured)

    def test_rolling_back_restores_the_file_exactly(self):
        original = self.config_path.read_bytes()
        proposal = self.optimization(APPROVED)
        self.planner.implement(proposal.id, by="operator")
        self.assertNotEqual(self.config_path.read_bytes(), original)
        self.planner.roll_back(proposal.id, by="operator")
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertTrue(self.shell.config.get("shell", "phosphor"))
        self.assertEqual(proposal.status, ROLLED_BACK)
        events = [e["event"] for e in self.center.store.read_history()]
        self.assertIn("change-rolled-back", events)

    def test_rolling_back_does_not_undo_what_the_person_changed_since(self):
        proposal = self.optimization(APPROVED)
        self.planner.implement(proposal.id, by="operator")
        edited = set_value(self.config_path.read_text(), "shell", "computer_name", "MINE")
        self.config_path.write_text(edited)
        self.planner.roll_back(proposal.id, by="operator")
        text = self.config_path.read_text()
        self.assertIn('computer_name = "MINE"', text)
        self.assertRegex(text, r"(?m)^phosphor\s*=\s*true")

    def test_a_change_goes_through_verification_with_readings_either_side(self):
        proposal = self.optimization(APPROVED)
        self.planner.implement(proposal.id, by="operator", before=self.center.vitals())
        self.planner.begin_testing(proposal.id, by="operator")
        self.planner.complete(proposal.id, by="operator", after=self.center.vitals())
        self.assertEqual(proposal.status, COMPLETED)
        self.assertEqual(set(proposal.measured), {"before", "after"})
        text = Reporter.as_text(self.center.report("progress"))
        self.assertIn("Measured before", text)
        self.assertIn("Measured after", text)

    def test_work_for_a_person_changes_no_file_at_all(self):
        entry = next(e for e in self.center.capabilities() if e.id == "service-state")
        proposal = self.center.propose_from("capability", entry.id, by="operator")
        before, files = digest(self.config_path), None
        self.planner.approve(proposal.id, by="operator")
        self.planner.implement(proposal.id, by="operator")
        self.assertEqual(digest(self.config_path), before)
        self.assertIsNone(proposal.applied)
        self.assertFalse((self.home / "evolution" / "backups").exists())

    def test_only_the_listed_settings_within_their_bounds(self):
        refused = [
            {"section": "shell", "key": "exit_command", "value": "rm -rf ~"},
            {"section": "claude", "key": "command", "value": "/tmp/evil"},
            {"section": "cdos", "key": "root", "value": "/"},
            {"section": "shell", "key": "fps", "value": 1000},
            {"section": "shell", "key": "fps", "value": "30"},
            {"section": "shell", "key": "fps", "value": True},
            {"section": "shell", "key": "phosphor", "value": "false"},
            {"section": "companion", "key": "keep_alive", "value": "forever"},
            {"section": "evolution", "key": "poll_interval", "value": 0.01},
            {"key": "phosphor", "value": False}, None, "shell.phosphor=false",
        ]
        before = digest(self.config_path)
        for change in refused:
            with self.assertRaises(ChangeRefused, msg=repr(change)):
                validate(change)
            proposal = self.planner.discover(title="x", kind="optimization",
                                             source=f"x:{refused.index(change)}")[0]
            proposal.change = change if isinstance(change, dict) else {"bad": change}
            self.planner.approve(proposal.id, by="operator")
            with self.assertRaises(Refused):
                self.planner.implement(proposal.id, by="operator")
            self.assertEqual(proposal.status, APPROVED)
        self.assertEqual(digest(self.config_path), before)

    def test_a_file_that_cannot_be_backed_up_is_left_alone(self):
        proposal = self.optimization(APPROVED)
        before = digest(self.config_path)

        def fail(*_a, **_k):
            raise OSError("disk full")
        self.center.store.back_up = fail
        with self.assertRaises(Refused) as caught:
            self.planner.implement(proposal.id, by="operator")
        self.assertIn("left alone", str(caught.exception))
        self.assertEqual(digest(self.config_path), before)
        self.assertEqual(proposal.status, APPROVED)

    def test_the_store_cannot_write_outside_its_own_directory(self):
        store = self.center.store
        with self.assertRaises(ValueError):
            store._inside(self.config_path)
        with self.assertRaises(ValueError):
            store._inside(store.root / ".." / "config.toml")
        path = store.write_report("../../escape", "x", "txt")
        self.assertEqual(path.parent, store.reports)

    def test_cdos_and_the_lcars_frame_are_out_of_reach(self):
        """No setting in the list belongs to CDOS, the computer link or the
        chrome, and the package names none of their files."""
        for name in TUNABLES:
            self.assertNotIn(name.split(".")[0], ("cdos", "computer", "claude", "mind"))
        for path in MODULES:
            source = path.read_text(encoding="utf-8")
            for forbidden in ("chrome.py", "cdos-computer/computer", "capabilities.py\""):
                if path.name == "registry.py":
                    continue            # it *describes* them, as evidence
                self.assertNotIn(forbidden, source, path.name)


class TestEditingOneKey(unittest.TestCase):

    TEXT = ('# top\n[shell]\npalette = "Earth OS"\nfps = 30   # frames a second\n'
            'adaptive = true\n\n[voice]\nenabled = true\n\n[[programs]]\nlabel = "T"\n')

    def test_one_line_changes_and_its_comment_is_kept(self):
        out = set_value(self.TEXT, "shell", "fps", 20)
        self.assertIn("fps = 20   # frames a second", out)
        self.assertEqual([a for a, b in zip(self.TEXT.splitlines(), out.splitlines()) if a != b],
                         ["fps = 30   # frames a second"])

    def test_a_key_in_another_section_is_not_mistaken_for_it(self):
        out = set_value(self.TEXT, "voice", "fps", 20)
        self.assertIn("fps = 30   # frames a second", out)
        self.assertRegex(out, r"\[voice\]\nenabled = true\nfps = 20\n")

    def test_a_missing_key_is_added_to_its_section_and_a_missing_section_made(self):
        out = set_value(self.TEXT, "shell", "idle_fps", 0)
        self.assertRegex(out, r"adaptive = true\nidle_fps = 0\n\n\[voice\]")
        out = set_value(self.TEXT, "evolution", "poll_interval", 2.0)
        self.assertTrue(out.endswith("[evolution]\npoll_interval = 2.0\n"))

    def test_values_are_written_as_toml(self):
        self.assertIn("adaptive = false", set_value(self.TEXT, "shell", "adaptive", False))
        self.assertIn('keep = "2m"', set_value(self.TEXT, "shell", "keep", "2m"))
        import tomllib
        out = set_value(set_value(self.TEXT, "shell", "fps", 20), "evolution", "x", 1.5)
        parsed = tomllib.loads(out)
        self.assertEqual((parsed["shell"]["fps"], parsed["evolution"]["x"]), (20, 1.5))
        self.assertEqual(parsed["programs"], [{"label": "T"}])

    def test_removing_a_key_removes_only_that_line(self):
        out = remove_value(self.TEXT, "shell", "adaptive")
        self.assertNotIn("adaptive", out)
        self.assertEqual(len(out.splitlines()), len(self.TEXT.splitlines()) - 1)


# --------------------------------------------------------------------------
# 10. Reports
# --------------------------------------------------------------------------
class TestReports(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.shell, cls.center = make_shell()
        cls.center.sample(force=True)
        cls.center.discover()

    def test_the_seven_reports_are_the_directives(self):
        self.assertEqual(list(KINDS), ["health", "cpu", "ram", "applications", "hardware",
                                       "optimization", "progress"])

    def test_each_builds_as_text_and_as_json(self):
        for kind, title in KINDS.items():
            report = self.center.report(kind)
            text = Reporter.as_text(report)
            self.assertTrue(text.startswith(title.upper()), kind)
            self.assertIn("Generated", text)
            parsed = json.loads(Reporter.as_json(report))
            self.assertEqual(parsed["report"], kind)
            self.assertTrue(parsed["sections"], kind)
            for section in parsed["sections"]:
                self.assertEqual(set(section), {"heading", "rows", "lines"})

    def test_a_report_holds_the_figures_the_panels_show(self):
        text = Reporter.as_text(self.center.report("health"))
        for finding in self.center.findings():
            self.assertIn(f"[{finding.severity}] {finding.title}", text)
        ram = Reporter.as_text(self.center.report("ram"))
        self.assertIn("Largest consumers, by program", ram)
        optimization = Reporter.as_text(self.center.report("optimization"))
        for item in self.center.optimizations():
            self.assertIn(item.title, optimization)
        self.assertIn("6. How to reverse", optimization)
        progress = Reporter.as_text(self.center.report("progress"))
        for proposal in self.center.planner.proposals.values():
            self.assertIn(proposal.id, progress)

    def test_an_unknown_report_is_refused(self):
        with self.assertRaises(ValueError):
            self.center.report("everything")

    def test_export_writes_into_the_centers_own_reports_folder(self):
        text = self.center.export("health", "text")
        data = self.center.export("health", "json")
        self.assertEqual((text.suffix, data.suffix), (".txt", ".json"))
        self.assertEqual(text.parent, self.center.store.reports)
        self.assertIn("SYSTEM HEALTH REPORT", text.read_text())
        self.assertEqual(json.loads(data.read_text())["report"], "health")

    def test_the_screen_exports_what_it_is_showing(self):
        screen = self.shell.screens["evolution"]
        self.shell.go("evolution")
        screen._go_section("diagnostics")
        screen._select("cpu")
        self.assertTrue(screen._report_text.startswith("CPU PERFORMANCE REPORT"))
        screen._export("json")
        self.assertTrue(Path(screen._exported).name.startswith("cpu-"))

    def test_reports_without_a_window(self):
        """``earth-os --evolution-report``: the same engine, no screen."""
        from earth_os import cli
        home = Path(tempfile.mkdtemp(prefix="earth-os-sec-cli-"))
        config = home / "config.toml"
        config.write_text("[voice]\nenabled = false\n")
        before = digest(config)
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.evolution_report("health", str(config), as_json=True)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["report"], "health")
        self.assertEqual(digest(config), before)
        self.assertEqual(cli.evolution_report("nonsense", str(config)), 2)
        parser = cli.build_parser()
        args = parser.parse_args(["--evolution-report", "ram", "--json", "--checks"])
        self.assertEqual((args.evolution_report, args.json, args.checks), ("ram", True, True))


# --------------------------------------------------------------------------
# 11. With a model, and without one
# --------------------------------------------------------------------------
class FakeMind:
    """Earth Mind with a companion that answers as told."""

    def __init__(self, reply):
        self.reply = reply
        self.asked = []

    def persona(self, _interface):
        return SimpleNamespace(id="technician")

    def companion(self, _pid):
        return SimpleNamespace(explain=self._explain)

    def _explain(self, question, findings, origin=""):
        self.asked.append((question, list(findings), origin))
        return self.reply


class TestWithAndWithoutAModel(unittest.TestCase):

    def setUp(self):
        self.shell, self.center = make_shell()
        self.center.sample(force=True)

    def mind(self, source, text="It is fine."):
        fake = FakeMind(SimpleNamespace(source=source, text=text, checked=source == "model"))
        self.shell._mind, self.shell._mind_built = fake, True
        return fake

    def test_without_a_model_everything_still_works(self):
        self.shell.config.values["mind"]["enabled"] = False
        self.shell.config.values["companion"]["enabled"] = False
        self.assertTrue(self.center.findings())
        self.assertTrue(self.center.optimizations())
        self.assertTrue(self.center.capabilities())
        self.assertTrue(self.center.explain(wait=True))
        text, source = self.center.explanation
        self.assertTrue(source.startswith("findings"), source)
        for sentence in self.center.explainable():
            self.assertIn(sentence, text)

    def test_with_no_model_running_the_findings_are_said_as_they_stand(self):
        # A real Earth Mind, and whatever model this machine does or does not have.
        text, source = self.shell.explain_findings("How is it?", ["Memory is 6% full."])
        if source != "model":
            self.assertEqual(text, "Memory is 6% full.")
            self.assertTrue(source.startswith("findings"))

    def test_a_models_words_are_used_when_they_check(self):
        fake = self.mind("model", "Everything reads normal; storage is the one to watch.")
        self.assertTrue(self.center.explain(wait=True))
        self.assertEqual(self.center.explanation,
                         ("Everything reads normal; storage is the one to watch.", "model"))
        question, findings, origin = fake.asked[0]
        self.assertEqual(findings, self.center.explainable())
        self.assertIn("Evolution Center", origin)

    def test_a_model_whose_figures_did_not_check_is_not_quoted(self):
        """The Companion discards an answer with a figure the findings lack
        and returns them itself, marked as not from the model."""
        self.mind("computer", "The processor is at 97% and about to fail.")
        self.center.explain(wait=True)
        text, source = self.center.explanation
        self.assertNotIn("97%", text)
        self.assertTrue(source.startswith("findings"))

    def test_a_model_that_raises_costs_nothing_but_the_explanation(self):
        fake = self.mind("model")
        fake._explain = lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError("model timed out"))
        self.center.explain(wait=True)
        text, source = self.center.explanation
        self.assertIn("TimeoutError", source)
        self.assertTrue(text)

    def test_a_model_is_never_the_source_of_a_finding(self):
        before = [f.to_dict() for f in self.center.findings()]
        advice = [o.to_dict() for o in self.center.optimizations()]
        self.mind("model", "CRITICAL: the disk has failed. Storage is 100% full.")
        self.center.explain(wait=True)
        self.assertEqual([f.to_dict() for f in self.center.findings()], before)
        self.assertEqual([o.to_dict() for o in self.center.optimizations()], advice)
        report = Reporter.as_text(self.center.report("health"))
        self.assertNotIn("the disk has failed", report)

    def test_the_engine_never_imports_a_model(self):
        for path in MODULES:
            for _level, module in imports_of(path):
                for part in ("mind", "companion", "voice", "ollama"):
                    self.assertNotIn(part, module.split("."), f"{path.name}: {module}")

    def test_explanation_can_be_switched_off(self):
        fake = self.mind("model")
        self.shell.config.values["evolution"]["explain"] = False
        self.center.explain(wait=True)
        self.assertEqual(fake.asked, [])
        self.assertIn("off", self.center.explanation[1])


# --------------------------------------------------------------------------
# 12. It is cheap, and silent when nobody is looking
# --------------------------------------------------------------------------
class TestItIsCheap(unittest.TestCase):

    def setUp(self):
        self.shell, _ = make_shell()
        self.clock = FakeClock()
        self.center = EvolutionCenter(self.shell, clock=self.clock,
                                      directory=Path(tempfile.mkdtemp()) / "evolution")
        self.shell._evolution = self.center
        self.snapshots = []
        real = self.center.inspector.snapshot

        def counting(processes=False):
            self.snapshots.append(processes)
            return real(processes=processes)
        self.center.inspector.snapshot = counting

    def test_asking_often_does_not_sample_often(self):
        for _ in range(500):
            self.center.sample(visible=True, panel="overview")
        self.assertEqual(len(self.snapshots), 1)
        self.clock.advance(1.9)
        self.center.sample(visible=True, panel="overview")
        self.assertEqual(len(self.snapshots), 1)
        self.clock.advance(0.2)
        self.assertTrue(self.center.sample(visible=True, panel="overview"))
        self.assertEqual(len(self.snapshots), 2)

    def test_the_polling_interval_is_configurable_and_takes_effect_at_once(self):
        self.center.sample(visible=True)
        self.shell.config.values["evolution"]["poll_interval"] = 10.0
        self.clock.advance(9.0)
        self.assertFalse(self.center.sample(visible=True))
        self.clock.advance(1.5)
        self.assertTrue(self.center.sample(visible=True))

    def test_the_process_list_is_only_read_for_panels_that_show_it(self):
        for _ in range(10):
            self.clock.advance(2.1)
            self.center.sample(visible=True, panel="overview")
        self.assertEqual(self.snapshots, [False] * 10)

    def test_and_then_no_more_often_than_its_own_interval(self):
        for _ in range(10):                         # 21 seconds on HEALTH
            self.clock.advance(2.1)
            self.center.sample(visible=True, panel="health")
        # At 2.1 s, then at the first sample five seconds after each: 8.4,
        # 14.7 and 21.0. Four readings in ten samples.
        self.assertEqual(self.snapshots.count(True), 4)
        # In between, the last list is carried forward rather than read again.
        self.assertTrue(self.center.snapshot.get("processes").ok)

    def test_findings_are_computed_once_per_sample_not_once_per_frame(self):
        self.center.sample(visible=True, panel="health")
        calls = []
        real = self.center.health.analyze
        self.center.health.analyze = lambda *a, **k: calls.append(1) or real(*a, **k)
        for _ in range(60):
            self.center.findings(), self.center.optimizations(), self.center.health_summary()
        self.assertEqual(len(calls), 1)

    def test_a_panel_nobody_opened_was_never_computed(self):
        self.center.sample(visible=True, panel="overview")
        self.center.findings()
        self.assertIsNone(self.center._survey)
        self.assertIsNone(self.center._advice)
        self.assertIsNone(self.center.doctor, "the doctor's checks ran without a press")

    def test_hidden_it_does_nothing_until_it_has_been_opened_once(self):
        shell, _ = make_shell()
        shell._evolution, shell._evolution_built = None, False
        shell.evolution_background()
        self.assertIsNone(shell._evolution)
        self.assertFalse(shell._evolution_built)

    def test_hidden_it_keeps_a_slow_trend_and_stops_while_on_screen(self):
        self.shell.current = "main"
        self.shell.evolution_background()
        self.assertEqual(len(self.snapshots), 1)
        self.assertFalse(self.center.trends.latest().visible)
        self.shell.current = "evolution"            # the screen's own tick does it now
        self.shell.evolution_background()
        self.assertEqual(len(self.snapshots), 1)

    def test_the_screen_only_samples_while_it_is_the_screen_on_show(self):
        screen = self.shell.screens["evolution"]
        self.shell.go("evolution")
        taken = len(self.snapshots)
        self.shell.handed_over_to = "FIREFOX"       # another program has the display
        self.clock.advance(60.0)
        screen.tick()
        self.assertEqual(len(self.snapshots), taken)
        self.shell.handed_over_to = ""
        screen.tick()
        self.assertEqual(len(self.snapshots), taken + 1)

    def test_it_is_not_a_live_screen_and_asks_for_no_frames_at_rest(self):
        screen = self.shell.screens["evolution"]
        self.assertFalse(screen.live)
        self.shell.go("evolution")
        screen.ensure_built(self.shell.context(1280, 720))
        self.shell.toast_until = 0.0
        self.assertIsNone(screen.next_frame_in())

    def test_a_tick_that_finds_nothing_new_asks_for_no_frame(self):
        screen = self.shell.screens["evolution"]
        self.shell.go("evolution")
        screen._go_section("optimization")
        frames = []
        self.shell.redraw_hook = lambda: frames.append(1)
        for _ in range(50):                         # five seconds of ticks, no time passing
            screen.tick()
        self.assertEqual(frames, [])
        self.clock.advance(2.5)
        screen.tick()                               # a sample, but the list is unchanged
        self.assertEqual(frames, [])

    def test_no_second_sampler_is_started(self):
        """One monitor. The center holds the shell's, and builds none."""
        self.assertIs(self.center.inspector._monitor(), self.shell.monitor)
        for path in MODULES:
            self.assertNotIn("SystemMonitor(", path.read_text(encoding="utf-8"), path.name)
            self.assertNotIn("Thread(", path.read_text(encoding="utf-8").replace(
                'threading.Thread(target=work, name="evolution-', ""), path.name)

    def test_a_sample_is_fast_and_the_center_reports_its_own_cost(self):
        shell, center = make_shell()
        for _ in range(20):
            center.sample(force=True)
        cost = center.overhead()
        self.assertEqual(cost["samples"], 20)
        # Generous: this is a regression tripwire, not the measurement. The
        # measured figures are in docs/EVOLUTION_CENTER.md.
        self.assertLess(cost["mean_ms"], 150.0)

    def test_the_background_job_is_optional(self):
        source = (ROOT / "earth_os" / "app.py").read_text(encoding="utf-8")
        self.assertIn('config.get("evolution", "background_interval", 60.0)', source)
        self.assertIn("slow > 0", source)


class TestRenderingStatistics(unittest.TestCase):

    def test_the_scheduler_reports_what_drawing_cost(self):
        from earth_os.scheduler import FrameScheduler
        scheduler = FrameScheduler(lambda: None)
        self.assertEqual(scheduler.stats()["draws_timed"], 0)
        for seconds in (0.004, 0.010, 0.007):
            scheduler.note_draw(seconds)
        scheduler.note_draw(-1.0)                   # a clock that went backwards
        stats = scheduler.stats()
        self.assertEqual(stats["draws_timed"], 3)
        self.assertAlmostEqual(stats["draw_ms_mean"], 7.0)
        self.assertAlmostEqual(stats["draw_ms_peak"], 10.0)
        self.assertAlmostEqual(stats["draw_ms_last"], 7.0)

    def test_the_center_reads_them_when_a_window_publishes_them(self):
        shell, center = make_shell()
        shell.render_stats = lambda: {"state": "IDLE", "fps": 30, "adaptive": True,
                                      "frames_drawn": 120, "draws_timed": 120,
                                      "draw_ms_mean": 5.5, "draw_ms_peak": 9.0,
                                      "jobs": {"telemetry": {"ran": 40, "changed": 3}}}
        center.sample(force=True)
        self.assertTrue(center.snapshot.get("render").ok)
        found = by_id(center.findings())
        self.assertIn("rendering.normal", found)
        self.assertIn("telemetry: ran 40 times", found["services.jobs"].evidence[0])
        rows = dict(shell.screens["evolution"]._overview_rows())
        self.assertIn("1 Earth OS jobs: telemetry", rows["BACKGROUND SERVICES"])
        self.assertIn("5.5 ms", rows["DESKTOP RENDERING"])


# --------------------------------------------------------------------------
# 13. In a real window, on a real main loop
# --------------------------------------------------------------------------
HAVE_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    HAVE_GTK = True
except (ImportError, ValueError):                    # pragma: no cover
    HAVE_GTK = False


@unittest.skipUnless(HAVE_GTK and HAVE_DISPLAY, "needs GTK4 and a display (try xvfb-run)")
class TestInARealWindow(unittest.TestCase):
    """The wiring: the real ``EarthWindow``, the real scheduler, real frames."""

    def setUp(self):
        from earth_os.app import EarthWindow
        self.shell, self.center = make_shell({("shell", "fullscreen"): False,
                                              ("shell", "adaptive"): True,
                                              ("shell", "idle_fps"): 0})
        self.window = EarthWindow.__new__(EarthWindow)
        Gtk.ApplicationWindow.__init__(self.window, title="evolution")
        EarthWindow.__init__(self.window, None, self.shell.config)
        self.shell.redraw_hook = self.window.frames.request_frame
        self.draws = 0
        real = self.window._on_draw

        def counting(area, cr, width, height):
            self.draws += 1
            return real(area, cr, width, height)
        self.window.area.set_draw_func(counting)
        self.window.set_default_size(800, 500)
        self.window.present()
        self.pump(0.4)
        self.window.attach(self.shell)
        self.pump(0.6)

    def tearDown(self):
        try:
            self.window.jobs.stop()
            self.window.frames.stop()
            self.window.destroy()
        except Exception:
            pass
        self.pump(0.1)

    def pump(self, seconds):
        context = GLib.main_context_default()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            while context.pending():
                context.iteration(False)
            time.sleep(0.005)

    def test_the_window_publishes_rendering_and_job_statistics(self):
        self.assertTrue(callable(self.shell.render_stats))
        stats = self.shell.render_stats()
        self.assertGreater(stats["draws_timed"], 0)
        self.assertGreater(stats["draw_ms_mean"], 0.0)
        self.assertIn("evolution", stats["jobs"])
        self.assertIn("telemetry", stats["jobs"])

    def test_opening_it_draws_it_with_live_rendering_figures(self):
        self.shell.go("evolution")
        self.pump(0.5)
        self.assertGreaterEqual(self.draws, 1)
        render = self.center.snapshot.get("render")
        self.assertTrue(render.ok, render.why)
        self.assertNotIn("rendering.unavailable", by_id(self.center.findings()))

    def test_at_rest_it_draws_no_more_than_once_a_sample(self):
        self.shell.go("evolution")
        self.pump(1.0)
        self.draws = 0
        self.pump(6.0)                               # three poll intervals
        self.assertLessEqual(self.draws, 5, "the overview is drawing more than it samples")
        self.assertGreaterEqual(self.draws, 2, "the overview has stopped updating")

    def test_a_section_with_nothing_moving_draws_nothing(self):
        self.shell.go("evolution")
        self.shell.screens["evolution"]._go_section("settings")
        self.pump(1.0)
        self.draws = 0
        self.pump(5.0)
        self.assertLessEqual(self.draws, 1)

    def test_leaving_it_stops_its_sampling(self):
        self.shell.go("evolution")
        self.pump(0.5)
        self.shell.go("programs")
        self.pump(0.5)
        taken = self.center.samples_taken
        self.pump(5.0)
        self.assertEqual(self.center.samples_taken, taken)


if __name__ == "__main__":
    unittest.main()
