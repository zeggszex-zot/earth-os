"""Alt+Tab: one ring through the home screen and every program.

The compositor owns the key, so the logic runs as ``earth-os --switch`` and
asks a window backend to move focus. These tests use a fake backend, which is
the honest way to test it: what is being checked is the ring — its order, its
wrap, what it does at the edges — not any compositor.

Imports only ``earth_os.windows``, so it runs on a machine with no GTK.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os import cli                                   # noqa: E402
from earth_os.windows import (                             # noqa: E402
    NullWindows, Window, WindowBackend, cycle, switch_ring,
)


def win(handle, app, title="", focused=False, fullscreen=False):
    return Window(handle=str(handle), title=title or app, app=app,
                  focused=focused, fullscreen=fullscreen)


class FakeBackend(WindowBackend):
    name = "fake"

    def __init__(self, windows, focus_ok=True):
        self._windows = windows
        self.focus_ok = focus_ok
        self.focused_handles = []
        self.fullscreen_calls = []

    @property
    def available(self):
        return True

    def list_windows(self):
        return list(self._windows)

    def focus(self, window):
        self.focused_handles.append(window.handle)
        return self.focus_ok

    def fullscreen(self, window, on=True):
        self.fullscreen_calls.append((window.handle, on))
        return True


def shell(focused=False):
    return win(7, "org.earthos.shell", "EARTH OS", focused=focused)


class TestTheRing(unittest.TestCase):
    def test_home_leads_and_programs_follow_in_opening_order(self):
        found = [win(30, "gedit"), win(12, "firefox"), shell(), win(9, "foot")]
        ring = switch_ring(found)
        self.assertEqual([w.app for w in ring],
                         ["org.earthos.shell", "foot", "firefox", "gedit"])

    def test_numeric_ids_sort_as_numbers_not_text(self):
        """"9" sorts after "10" as text, which would shuffle the ring the
        moment ids gained a digit."""
        ring = switch_ring([shell(), win(100, "b"), win(9, "a")])
        self.assertEqual([w.app for w in ring][1:], ["a", "b"])

    def test_the_order_does_not_depend_on_what_has_focus(self):
        a = switch_ring([shell(), win(8, "a", focused=True), win(9, "b")])
        b = switch_ring([shell(), win(8, "a"), win(9, "b", focused=True)])
        self.assertEqual([w.handle for w in a], [w.handle for w in b])

    def test_without_the_shell_in_the_list_it_is_programs_only(self):
        ring = switch_ring([win(8, "a"), win(9, "b")])
        self.assertEqual([w.app for w in ring], ["a", "b"])

    def test_non_numeric_handles_still_give_a_stable_ring(self):
        one = switch_ring([shell(), win("b:two", "b"), win("a:one", "a")])
        two = switch_ring([win("a:one", "a"), shell(), win("b:two", "b")])
        self.assertEqual([w.handle for w in one], [w.handle for w in two])


class TestCycling(unittest.TestCase):
    def test_forward_from_home_goes_to_the_first_program(self):
        backend = FakeBackend([shell(focused=True), win(8, "foot"), win(9, "gedit")])
        ok, message = cycle(backend, 1)
        self.assertTrue(ok)
        self.assertEqual(backend.focused_handles, ["8"])
        self.assertEqual(message, "foot")

    def test_forward_from_the_last_program_wraps_to_home(self):
        backend = FakeBackend([shell(), win(8, "foot"), win(9, "gedit", focused=True)])
        ok, message = cycle(backend, 1)
        self.assertTrue(ok)
        self.assertEqual(backend.focused_handles, ["7"])
        self.assertEqual(message, "home")

    def test_back_from_home_wraps_to_the_last_program(self):
        backend = FakeBackend([shell(focused=True), win(8, "foot"), win(9, "gedit")])
        cycle(backend, -1)
        self.assertEqual(backend.focused_handles, ["9"])

    def test_back_from_a_program_goes_to_the_one_before(self):
        backend = FakeBackend([shell(), win(8, "foot"), win(9, "gedit", focused=True)])
        cycle(backend, -1)
        self.assertEqual(backend.focused_handles, ["8"])

    def test_repeated_presses_visit_everything_and_come_back(self):
        windows = [shell(focused=True), win(8, "a"), win(9, "b"), win(11, "c")]
        seen = []
        for _ in range(4):
            backend = FakeBackend(windows)
            cycle(backend, 1)
            seen.append(backend.focused_handles[0])
            for w in windows:
                w.focused = (w.handle == backend.focused_handles[0])
        self.assertEqual(seen, ["8", "9", "11", "7"])

    def test_focus_on_nothing_known_goes_home_forward_and_last_back(self):
        windows = [shell(), win(8, "a"), win(9, "b")]
        forward, back = FakeBackend(windows), FakeBackend(windows)
        cycle(forward, 1)
        cycle(back, -1)
        self.assertEqual(forward.focused_handles, ["7"])
        self.assertEqual(back.focused_handles, ["9"])

    def test_only_the_home_screen_open_is_reported_not_silent(self):
        backend = FakeBackend([shell(focused=True)])
        ok, message = cycle(backend, 1)
        self.assertFalse(ok)
        self.assertIn("nothing else", message)
        self.assertEqual(backend.focused_handles, [])

    def test_a_fullscreen_program_is_released_on_the_way_out(self):
        """Otherwise it stays on top of whatever is focused next and the
        press looks as if nothing happened."""
        backend = FakeBackend(
            [shell(), win(8, "firefox", focused=True, fullscreen=True)])
        cycle(backend, 1)
        self.assertEqual(backend.fullscreen_calls, [("8", False)])
        self.assertEqual(backend.focused_handles, ["7"])

    def test_a_windowed_program_is_left_alone(self):
        backend = FakeBackend([shell(), win(8, "foot", focused=True)])
        cycle(backend, 1)
        self.assertEqual(backend.fullscreen_calls, [])

    def test_a_refused_focus_is_a_failure_not_a_success(self):
        backend = FakeBackend([shell(focused=True), win(8, "foot")],
                              focus_ok=False)
        ok, message = cycle(backend, 1)
        self.assertFalse(ok)
        self.assertIn("could not focus", message)

    def test_no_window_control_says_why(self):
        ok, message = cycle(NullWindows("no compositor"), 1)
        self.assertFalse(ok)
        self.assertEqual(message, "no compositor")


class TestCommandLine(unittest.TestCase):
    def run_main(self, argv, backend):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("earth_os.windows.detect", return_value=backend), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_switch_moves_forward_and_exits_zero(self):
        backend = FakeBackend([shell(focused=True), win(8, "foot")])
        code, out, _ = self.run_main(["--switch"], backend)
        self.assertEqual(code, 0)
        self.assertEqual(backend.focused_handles, ["8"])
        self.assertIn("foot", out)

    def test_switch_back_moves_backward(self):
        backend = FakeBackend([shell(focused=True), win(8, "a"), win(9, "b")])
        self.run_main(["--switch-back"], backend)
        self.assertEqual(backend.focused_handles, ["9"])

    def test_nothing_to_switch_to_exits_nonzero_and_says_so(self):
        backend = FakeBackend([shell(focused=True)])
        code, _, err = self.run_main(["--switch"], backend)
        self.assertEqual(code, 1)
        self.assertIn("nothing else", err)

    def test_the_switch_path_does_not_load_the_toolkit(self):
        """It is bound to a key. A GTK import on every press is the delay
        that makes people stop using the key."""
        sys.modules.pop("gi", None)
        backend = FakeBackend([shell(focused=True), win(8, "foot")])
        self.run_main(["--switch"], backend)
        self.assertNotIn("gi", sys.modules)


if __name__ == "__main__":
    unittest.main()
