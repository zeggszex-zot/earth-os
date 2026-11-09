"""The capability boundary, enforced.

``docs/CDOS_DISTRIBUTION.md`` states a rule:

    Below the boundary, name any mechanism you like.
    Above it, name none.

A rule that only exists in a document drifts away from the code, quietly, and
nobody finds out until someone tries the port it was written to enable.  The
delivery note in this very repository claimed "36/36 tests passing" for a
suite that had not been running at all.  So the rule is asserted here instead,
where it fails a build rather than ageing in a paragraph.

This is a **ratchet**, not a clean-room check.  The codebase predates the
boundary and there is real debt above it — 96 mechanism names across eleven
modules at the time of writing.  Deleting the debt in one pass would be a
rewrite of the voice stack, the window manager and the telemetry reader at
once, which is how working systems get broken.  So each module carries a
recorded allowance and the test permits only what is already there:

* a module with no allowance may name **nothing**;
* a module with an allowance may not exceed it;
* an allowance that is beaten is reported, so it can be lowered.

New violations therefore fail immediately, while the existing ones come down
at whatever pace the work allows.  The numbers below are the Phase 3 worklist
expressed as something a machine checks.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PACKAGE = ROOT / "earth_os"

#: Everything under here is an adapter, and adapters exist to know mechanisms.
BELOW = PACKAGE / "platform"

#: The names of Linux mechanisms.  Not exhaustive, and does not need to be:
#: it is a tripwire for the habit of reaching past the boundary, not a
#: catalogue of Linux.
MECHANISMS: Tuple[str, ...] = (
    "systemctl", "loginctl", "shutdown -r", "shutdown -h",
    "xrandr", "brightnessctl", "xbacklight",
    "pactl", "amixer", "aplay", "paplay", "arecord",
    "espeak", "festival", "piper",
    "nmcli", "iwconfig", "bluetoothctl", "rfkill",
    "grim", "scrot", "maim", "gnome-screenshot", "spectacle",
    "swaymsg", "i3-msg", "wmctrl", "xdotool", "hyprctl", "wlrctl",
    "xdg-open", "gio open", "gnome-open",
    "slock", "xlock", "gnome-screensaver-command",
    "/proc/", "/sys/class",
)

#: What each module above the boundary is currently allowed to name, and why
#: it has not been moved yet.  Lower these; do not raise them.
ALLOWANCE: Dict[str, Tuple[int, str]] = {
    # windows.py is gone from this table, and that is the point of the table.
    # It carried 21 names — the largest single entry — as a WindowBackend ABC
    # with four implementations (sway, wlrctl, wmctrl, null): already the
    # right shape, simply on the wrong side of the line.  The backends moved
    # to platform/linux_windows.py unchanged and the policy stayed above, so
    # the migration was a mount rather than a rewrite.  96 -> 75.

    # diagnostics.py's job is to report which mechanisms this machine actually has, so it
    # is the one module above the boundary that legitimately names them.
    # Kept under an allowance rather than exempted outright, so it cannot
    # quietly grow into a second adapter.
    "diagnostics.py": (14, "declared: probes for mechanisms by design"),
    # Mechanism names shipped as configuration defaults.  A default is the
    # adapter's opinion about this machine, and belongs with the adapter that
    # can actually check.
    "config.py": (13, "defaults belong to the adapter"),
    # The voice stack predates the audio domain entirely.
    "voice/backends.py": (12, "move to audio.speak"),
    "voice/capture.py": (10, "move to audio.listen"),
    "voice/engine.py": (7, "move to audio.speak"),
    "audio.py": (5, "move to audio.play"),
    # sysinfo.py is gone from this table too. It read /proc and /sys for the
    # gauges while LinuxHardware read the same files for the catalogue — two
    # implementations of one number, which is how a machine comes to report
    # 52 degrees and 57 degrees at the same time. It is now a rate-limited
    # view over the hardware capability and reads nothing itself. 75 -> 66.

    # The opener list, and the `notepad` vocabulary default that uses it.
    "openers.py": (2, "move to filesystem.open"),
    "screens/settings.py": (2, "mechanism named in a settings label"),
    "core.py": (1, "the xdg-open notepad default"),
}


def mechanism_names(path: Path) -> List[str]:
    """Mechanism names appearing in a module's *code*.

    Parsed rather than grepped.  Comments never reach the syntax tree, and
    docstrings are skipped explicitly, so prose about ``systemctl`` — of which
    this codebase has a lot, deliberately — is not mistaken for a call to it.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))

    found: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in docstrings:
            continue
        if not isinstance(node.value, str):
            continue
        for mechanism in MECHANISMS:
            found.extend([mechanism] * node.value.count(mechanism))
    return found


def modules_above_the_boundary() -> List[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py")
                  if BELOW not in p.parents and p != BELOW)


class TestNothingAboveTheBoundaryNamesAMechanism(unittest.TestCase):

    def test_no_module_exceeds_its_allowance(self):
        """The ratchet. A module may not name more mechanisms than recorded."""
        exceeded = []
        for path in modules_above_the_boundary():
            key = path.relative_to(PACKAGE).as_posix()
            count = len(mechanism_names(path))
            allowed, _reason = ALLOWANCE.get(key, (0, ""))
            if count > allowed:
                exceeded.append(
                    f"{key}: names {count} mechanism(s), allowance {allowed} "
                    f"-> {sorted(set(mechanism_names(path)))}")
        self.assertEqual(exceeded, [], "\n\nAbove the capability boundary:\n"
                         + "\n".join(exceeded)
                         + "\n\nEither route this through a capability, or — if it "
                           "genuinely belongs above — add it to ALLOWANCE with a "
                           "reason.\nSee docs/CDOS_DISTRIBUTION.md.\n")

    def test_allowances_are_not_stale(self):
        """An allowance that has been beaten should be lowered.

        Reported rather than failed: a module legitimately shrinks between the
        work and the bookkeeping, and failing that would punish the fix.
        """
        slack = []
        for key, (allowed, reason) in sorted(ALLOWANCE.items()):
            path = PACKAGE / key
            if not path.exists():
                slack.append(f"  {key}: gone entirely — drop the allowance")
                continue
            count = len(mechanism_names(path))
            if count < allowed:
                slack.append(f"  {key}: {count} now, allowance {allowed} "
                             f"— lower it ({reason})")
        if slack:
            print("\nAllowances that can be tightened:\n" + "\n".join(slack))

    def test_the_debt_is_a_known_quantity(self):
        """Phase 3 has a number, and it is this one."""
        total = sum(len(mechanism_names(p)) for p in modules_above_the_boundary())
        self.assertLessEqual(
            total, sum(a for a, _ in ALLOWANCE.values()),
            "total mechanism names above the boundary has grown")

    def test_the_adapter_is_where_the_mechanisms_live(self):
        """The other half of the rule: below the boundary they are expected.

        If this ever fails, the adapter has stopped doing its job and the
        knowledge has gone somewhere it should not be.
        """
        adapter = BELOW / "linux_adapter.py"
        self.assertGreater(
            len(mechanism_names(adapter)), 20,
            "the Linux adapter should be where Linux mechanisms are named")


class TestTheBoundaryItselfIsIntact(unittest.TestCase):
    """The gate only means anything while the catalogue is closed and the
    shell has one way through it."""

    def test_the_shell_core_spawns_nothing_directly(self):
        """core.py is the part Phase 2 actually cleaned. Keep it clean."""
        source = (PACKAGE / "core.py").read_text(encoding="utf-8")
        for spawn in ("subprocess.Popen", "subprocess.run",
                      "subprocess.check_output", "subprocess.call",
                      "os.system", "os.exec", "os.spawn"):
            self.assertNotIn(
                spawn, source,
                f"core.py calls {spawn} — it must go through a capability")

    def test_nothing_above_the_boundary_asks_a_shell_to_parse(self):
        """``shell=True`` hands a string to /bin/sh to split, which is both a
        Linux assumption and an injection surface.

        The terminal screen is the single declared exception: it is CDOS's
        escape hatch, where a person types a command line and means it.  An
        escape hatch that is declared is a feature; an undeclared one is a
        leak, so it is named here rather than left to be discovered.
        """
        ESCAPE_HATCH = "screens/terminal.py"
        offenders = []
        for path in modules_above_the_boundary():
            key = path.relative_to(PACKAGE).as_posix()
            if key == ESCAPE_HATCH:
                continue
            if "shell=True" in path.read_text(encoding="utf-8"):
                offenders.append(key)
        self.assertEqual(offenders, [], f"shell=True outside {ESCAPE_HATCH}")

    def test_the_escape_hatch_is_still_where_it_was_declared(self):
        """If the terminal stops using it, the exception should be removed."""
        hatch = PACKAGE / "screens" / "terminal.py"
        self.assertIn("shell=True", hatch.read_text(encoding="utf-8"),
                      "the declared escape hatch is gone — drop the exception")

    def test_no_adapter_knows_any_vocabulary(self):
        """The other direction of the rule, and the one that is easy to lose.

        Above the boundary, no mechanisms.  Below it, no *words*: an adapter
        that learned "restart" also means reboot would be a second, hidden
        vocabulary — one the corpus cannot see, the semantic layer cannot
        weigh, and nobody would think to look in when a phrase resolved
        strangely.  Meaning belongs above; mechanism belongs below.

        Asserted as a dependency direction because that is the crisp version:
        an adapter that cannot import the vocabulary cannot consult one.
        """
        forbidden = ("commands", "semantics", "translator", "theme",
                     "widgets", "screens", "core")
        offenders = []
        for path in sorted(BELOW.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                for name in names:
                    leaf = name.rsplit(".", 1)[-1]
                    if leaf in forbidden:
                        offenders.append(
                            f"{path.relative_to(PACKAGE)} imports {name}")
        self.assertEqual(offenders, [], "\n\nBelow the boundary, an adapter "
                         "reached up into the shell:\n" + "\n".join(offenders))

    def test_the_catalogue_is_a_closed_set(self):
        from earth_os.platform import CapabilityRegistry, get_platform
        from earth_os.platform.capabilities import CATALOGUE
        registry = CapabilityRegistry(get_platform())
        for invented in ("system.format_disk", "process.kill_all",
                         "filesystem.wipe", "robot.arm.move"):
            self.assertNotIn(invented, CATALOGUE)
            self.assertFalse(registry.invoke(invented).ok)


if __name__ == "__main__":
    unittest.main()


class TestTheAdapterContainsItsOwnNoise(unittest.TestCase):
    """A mechanism's error output is mechanism too, and must not escape.

    ``loginctl`` on a machine with no bus prints "Failed to connect to bus"
    to its own stderr.  When the adapter does not capture that, it lands on
    whatever terminal the shell is attached to — which, when CDOS *is* the
    login session, is the console the person is looking at, and in a
    conformance report makes every honest absence look like a crash.

    The boundary is supposed to contain mechanism. That includes its noise.
    """

    def test_no_adapter_subprocess_leaves_stderr_uncaptured(self):
        offenders = []
        for path in sorted(BELOW.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                target = getattr(node.func, "attr", "")
                if target not in ("run", "check_output", "check_call", "Popen"):
                    continue
                root = getattr(node.func, "value", None)
                if getattr(root, "id", "") != "subprocess":
                    continue
                keywords = {kw.arg for kw in node.keywords}
                if not ({"stderr", "capture_output"} & keywords):
                    offenders.append(
                        f"{path.relative_to(PACKAGE)}:{node.lineno}")
        self.assertEqual(
            offenders, [],
            "\n\nThese let a mechanism's stderr reach the terminal:\n  "
            + "\n  ".join(offenders)
            + "\n\nAdd stderr=subprocess.DEVNULL, or capture and report it.\n")
