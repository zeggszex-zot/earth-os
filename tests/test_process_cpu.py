"""Routing P4: processor time per process, measured.

Until this change the Linux adapter wrote ``cpu_percent = 0.0`` for every
process, under a comment saying the real implementation needed history. So no
answer about which program is using the processor was possible, and the
performance report's "busiest program" line had never once appeared on Linux
— silently, because a zero looked like an idle machine.

What this holds the adapter to — the review's seven safeguards, and the
Evolution Center's own requirements for this gap (``per-process-cpu``):

1. real measurements from /proc: two samples of each process's clock ticks;
2. a figure that was not measured is None, never zero;
3. a process that exits, starts, or reuses a pid between samples is handled;
4. nothing blocks: the adapter never waits for a second sample;
5. the overhead is measured (docs/EARTH_MIND_ROUTING_AUDIT.md §11);
6. a known CPU-heavy process reads busy, and an idle one reads idle;
7. the process interface is the same, plus the window a figure covers.

Linux only: the tests run real processes against the real /proc.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os.platform import linux_adapter                      # noqa: E402
from earth_os.platform.api import ProcessInfo                    # noqa: E402
from earth_os.platform.linux_adapter import (                    # noqa: E402
    CPU_MIN_WINDOW, LinuxProcess, _parse_stat,
)

LINUX = Path("/proc/self/stat").exists()


def _spawn(code: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", code])


class TestReadingTheStatLine(unittest.TestCase):

    def test_a_name_with_spaces_and_parentheses(self):
        """Splitting on spaces read "(Web Content)"'s state as "Content)"."""
        line = "4242 (Web Content) S 1 2 3 4 5 6 7 8 9 10 150 50 0 0 20 0 1 0 98765 0 0"
        self.assertEqual(_parse_stat(line), ("Web Content", "S", 200, 98765))
        odd = "7 (a) (b)) R 1 2 3 4 5 6 7 8 9 10 3 4 0 0 20 0 1 0 11 0 0"
        self.assertEqual(_parse_stat(odd), ("a) (b)", "R", 7, 11))

    def test_a_short_or_broken_line_is_no_reading(self):
        self.assertIsNone(_parse_stat("12 (x) S 1 2"))
        self.assertIsNone(_parse_stat("not a stat line"))


class TestTheArithmetic(unittest.TestCase):
    """The difference between two samples, with no real processes involved."""

    def setUp(self):
        with unittest.mock.patch.object(LinuxProcess, "_take_baseline", lambda self: None):
            self.process = LinuxProcess()
        self.process._hz = 100.0
        self.process._baseline = {10: (500, 1000), 11: (600, 50), 12: (700, 0)}
        self.process._baseline_at = 100.0

    def test_percent_of_one_core_over_the_window(self):
        figures, window = self.process._cpu_figures(
            {10: (500, 1100), 11: (600, 50)}, now=102.0, advance=True)
        self.assertEqual(window, 2.0)
        self.assertEqual(figures, {10: 50.0, 11: 0.0})     # 100 ticks over 2 s; and idle

    def test_a_reused_pid_and_a_new_process_have_no_figure(self):
        figures, _ = self.process._cpu_figures(
            {12: (999, 40), 13: (800, 10)}, now=102.0, advance=True)
        self.assertEqual(figures, {})

    def test_too_soon_repeats_the_last_figures_and_keeps_the_baseline(self):
        self.process._cpu_figures({10: (500, 1100)}, now=102.0, advance=True)
        figures, window = self.process._cpu_figures(
            {10: (500, 1101)}, now=102.0 + CPU_MIN_WINDOW / 2, advance=True)
        self.assertEqual((figures, window), ({10: 50.0}, 2.0))
        self.assertEqual(self.process._baseline_at, 102.0)

    def test_too_soon_with_nothing_before_is_no_figure(self):
        figures, window = self.process._cpu_figures({10: (500, 1001)}, now=100.1, advance=True)
        self.assertEqual((figures, window), ({}, None))

    def test_no_clock_rate_is_no_figure_never_a_guess(self):
        self.process._hz = None
        figures, window = self.process._cpu_figures({10: (500, 1100)}, now=102.0, advance=True)
        self.assertEqual((figures, window), ({}, None))

    def test_inspecting_one_process_does_not_move_the_baseline(self):
        self.process._cpu_figures({10: (500, 1100)}, now=102.0, advance=False)
        self.assertEqual(self.process._baseline_at, 100.0)

    def test_a_clock_that_went_backwards_is_not_negative(self):
        figures, _ = self.process._cpu_figures({10: (500, 900)}, now=102.0, advance=True)
        self.assertEqual(figures, {10: 0.0})


@unittest.skipUnless(LINUX, "needs /proc")
class TestOnRealProcesses(unittest.TestCase):

    def setUp(self):
        self.children = []

    def tearDown(self):
        for child in self.children:
            child.kill()
            child.wait()

    def _child(self, code):
        child = _spawn(code)
        self.children.append(child)
        return child

    def _listing(self, process):
        return {p.pid: p for p in process.list_processes()}

    def test_a_busy_process_reads_busy_and_an_idle_one_reads_idle(self):
        busy = self._child("while True: pass")
        idle = self._child("import time; time.sleep(60)")
        time.sleep(0.2)                                   # both exist at the baseline
        process = LinuxProcess()
        time.sleep(0.6)
        listed = self._listing(process)
        self.assertGreater(listed[busy.pid].cpu_percent, 60.0)
        self.assertLess(listed[idle.pid].cpu_percent, 5.0)
        self.assertAlmostEqual(listed[busy.pid].cpu_window, 0.6, delta=0.3)

    def test_the_first_question_after_start_already_has_a_baseline(self):
        idle = self._child("import time; time.sleep(60)")
        time.sleep(0.1)
        process = LinuxProcess()
        time.sleep(CPU_MIN_WINDOW + 0.05)
        self.assertIsNotNone(self._listing(process)[idle.pid].cpu_percent)

    def test_a_process_newer_than_the_baseline_has_no_figure(self):
        process = LinuxProcess()
        time.sleep(CPU_MIN_WINDOW + 0.05)
        late = self._child("import time; time.sleep(60)")
        time.sleep(0.1)
        info = self._listing(process)[late.pid]
        self.assertIsNone(info.cpu_percent)
        self.assertIsNone(info.cpu_window)

    def test_a_process_that_exits_between_samples_is_simply_not_listed(self):
        short = self._child("import time; time.sleep(60)")
        time.sleep(0.1)
        process = LinuxProcess()
        short.kill()
        short.wait()
        time.sleep(CPU_MIN_WINDOW + 0.05)
        self.assertNotIn(short.pid, self._listing(process))

    def test_a_process_that_vanishes_mid_read_is_not_listed(self):
        process = LinuxProcess()
        real = linux_adapter._read_stat
        with unittest.mock.patch.object(linux_adapter, "_read_stat",
                                        lambda pid: None if pid == 1 else real(pid)):
            listed = self._listing(process)
        self.assertNotIn(1, listed)
        self.assertTrue(listed)

    def test_no_figure_is_ever_the_old_placeholder(self):
        """Every figure is None or a measurement over a real window."""
        process = LinuxProcess()
        time.sleep(CPU_MIN_WINDOW + 0.05)
        for info in process.list_processes():
            if info.cpu_percent is None:
                self.assertIsNone(info.cpu_window)
            else:
                self.assertGreaterEqual(info.cpu_window, CPU_MIN_WINDOW)

    def test_listing_does_not_wait(self):
        process = LinuxProcess()
        started = time.perf_counter()
        process.list_processes()
        process.list_processes()
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_two_threads_listing_at_once(self):
        process = LinuxProcess()
        errors = []

        def run():
            try:
                for _ in range(20):
                    process.list_processes()
            except Exception as exc:              # pragma: no cover - the failure
                errors.append(exc)
        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class TestNothingSleeps(unittest.TestCase):
    """Safeguard 4, structurally: the process code has no sleep in it."""

    def test_no_sleep_in_the_process_adapter(self):
        tree = ast.parse(Path(linux_adapter.__file__).read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LinuxProcess")
        sleeps = [n for n in ast.walk(cls) if isinstance(n, ast.Attribute) and n.attr == "sleep"]
        self.assertEqual(sleeps, [])


class TestTheInterface(unittest.TestCase):
    """Safeguard 7: the same record, with the window added at the end."""

    def test_old_constructions_still_work(self):
        info = ProcessInfo(pid=1, name="a", command="a", state="S", cpu_percent=None,
                           memory_bytes=1)
        self.assertIsNone(info.cpu_window)
        positional = ProcessInfo(1, "a", "a", "S", 12.5, 1)
        self.assertEqual(positional.cpu_percent, 12.5)


@unittest.skipUnless(LINUX, "needs /proc")
class TestTheGapItClosed(unittest.TestCase):
    """The performance report's busiest-program line, through the real shell."""

    def test_the_busiest_program_is_named(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from mind_helpers import dead_host, temp_config
        from earth_os.core import Shell
        busy = _spawn("import time\nwhile True: pass")
        try:
            time.sleep(0.3)
            config = temp_config()
            config.values["companion"]["host"] = dead_host()
            shell = Shell(config)
            shell.sound.enabled = False
            shell.capabilities.invoke("process.list")        # a baseline that has the child
            time.sleep(0.6)
            ok, text = shell.answer_analysis("performance_report")
        finally:
            busy.kill()
            busy.wait()
        self.assertTrue(ok, text)
        self.assertIn("is using the most processor time", text)


if __name__ == "__main__":
    unittest.main()
