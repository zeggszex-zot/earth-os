"""The frame engine end to end, in a real window on a real main loop.

``tests/test_scheduler.py`` drives the scheduler with a fake clock, which
proves the logic and nothing about the wiring.  This boots the real
:class:`~earth_os.app.EarthWindow` against a real ``GLib`` main loop and
counts the times GTK actually calls the draw function, which is the only
number that settles whether the shell draws when it should.

The two directions are not equally dangerous.  Drawing too often costs CPU
and shows up in a benchmark; drawing too rarely means a screen that is
silently out of date, and nothing but a person noticing will catch it.  So
the assertions lean that way: every "this must still update" case is checked
first, and only then the "this must cost nothing" ones.

Skipped where there is no display.  Under CI that is most places, so these
run on a workstation or behind ``xvfb-run``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAVE_DISPLAY = bool(os.environ.get("DISPLAY")
                    or os.environ.get("WAYLAND_DISPLAY"))

try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    HAVE_GTK = True
except (ImportError, ValueError):  # pragma: no cover - depends on the box
    HAVE_GTK = False


@unittest.skipUnless(HAVE_GTK and HAVE_DISPLAY,
                     "needs GTK4 and a display (try xvfb-run)")
class FrameEngineCase(unittest.TestCase):
    """A window, a shell, and a count of how often GTK asked for pixels."""

    SIZE = (640, 400)

    def setUp(self) -> None:
        from earth_os.app import EarthWindow
        from earth_os.config import Config
        from earth_os.core import Shell
        from earth_os.screens import ALL_SCREENS

        home = Path(tempfile.mkdtemp(prefix="earth-os-frames-"))
        config = Config.load(home / "config.toml")
        config.values["voice"]["enabled"] = False
        config.values["shell"]["fullscreen"] = False
        config.values["shell"]["fps"] = 30
        config.values["shell"]["adaptive"] = True
        config.values["shell"]["idle_fps"] = 0

        self.app = Gtk.Application(application_id="org.earthos.test.frames")
        self.window = EarthWindow.__new__(EarthWindow)
        # Built without an application: presenting a window is not what is
        # under test, and a GtkApplication that owns one wants its own loop.
        Gtk.ApplicationWindow.__init__(self.window, title="frames")
        EarthWindow.__init__(self.window, None, config)

        self.shell = Shell(config)
        for cls in ALL_SCREENS:
            self.shell.register_screen(cls(self.shell))
        self.shell.current = "main"
        self.shell.redraw_hook = self.window.frames.request_frame

        self.draws = 0
        real_draw = self.window._on_draw

        def counting(area, cr, width, height):
            self.draws += 1
            return real_draw(area, cr, width, height)

        self.window.area.set_draw_func(counting)
        self.window.set_default_size(*self.SIZE)
        self.window.present()
        self.pump(0.4)
        self.window.attach(self.shell)
        self.pump(0.6)

    def tearDown(self) -> None:
        try:
            self.window.jobs.stop()
            self.window.frames.stop()
            self.window.destroy()
        except Exception:
            pass
        self.pump(0.1)

    # ------------------------------------------------------------------
    def pump(self, seconds: float) -> None:
        """Run the main loop for *seconds*, as the shell really would."""
        context = GLib.main_context_default()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            while context.pending():
                context.iteration(False)
            time.sleep(0.005)
        while context.pending():
            context.iteration(False)

    def counted(self, seconds: float) -> int:
        """Frames GTK asked for over *seconds*."""
        self.draws = 0
        self.pump(seconds)
        return self.draws

    def go(self, name: str) -> None:
        self.shell.go(name)
        self.pump(0.4)


class TestTheThingsThatMustStillUpdate(FrameEngineCase):
    """Drawing too rarely is the dangerous direction; check it first."""

    def test_a_redraw_request_produces_a_frame(self):
        self.pump(0.3)
        self.draws = 0
        self.shell.redraw()
        self.pump(0.2)
        self.assertGreaterEqual(self.draws, 1,
                                "Shell.redraw() did not reach the screen")

    def test_navigation_produces_a_frame(self):
        self.go("main")
        self.draws = 0
        self.shell.go("programs")
        self.pump(0.3)
        self.assertGreaterEqual(self.draws, 1, "a screen change was not drawn")

    def test_a_status_message_produces_a_frame(self):
        self.go("programs")
        self.pump(0.3)
        self.draws = 0
        self.shell.status_message = "something went wrong"
        self.pump(0.25)
        self.assertGreaterEqual(
            self.draws, 1,
            "an error message was set and never appeared on screen")

    def test_a_toast_produces_frames_and_then_one_more_to_clear_it(self):
        self.go("programs")
        self.pump(0.3)
        self.draws = 0
        self.shell.toast("SAVED", seconds=0.4)
        self.pump(0.3)
        self.assertGreaterEqual(self.draws, 1, "the toast never appeared")
        self.draws = 0
        self.pump(0.5)
        self.assertGreaterEqual(self.draws, 1,
                                "the toast would stay on screen for ever")

    def test_a_screen_with_live_readings_keeps_drawing(self):
        self.go("main")
        drawn = self.counted(3.0)
        self.assertGreaterEqual(
            drawn, 2, "the clock and the gauges stopped updating")
        self.assertLessEqual(
            drawn, 18, "a live screen is drawing far more than it needs")

    def test_the_engineering_screen_keeps_its_gauges_moving(self):
        self.go("engineering")
        drawn = self.counted(3.0)
        self.assertGreaterEqual(drawn, 2, "the gauges froze")
        self.assertLessEqual(drawn, 18)

    def test_the_terminal_keeps_its_caret_blinking(self):
        self.go("terminal")
        drawn = self.counted(2.0)
        # About two flips a second.
        self.assertGreaterEqual(drawn, 2, "the caret stopped blinking")
        self.assertLessEqual(drawn, 14,
                             "the caret is being drawn at the frame rate")

    def test_terminal_output_appears_without_anyone_pressing_a_key(self):
        """A worker thread's output has to reach the screen on its own."""
        self.go("terminal")
        terminal = self.shell.screens["terminal"]
        self.pump(0.3)
        self.draws = 0
        terminal._queue.put(("out", "hello from a worker"))
        self.pump(0.6)
        self.assertGreaterEqual(
            self.draws, 1,
            "output arrived from a thread and was never drawn")
        self.assertTrue(any("hello from a worker" in str(line)
                            for line in terminal.lines),
                        "the queue was never drained")

    def test_invalidating_a_screen_produces_a_frame(self):
        self.go("programs")
        self.pump(0.3)
        self.draws = 0
        self.shell.screens["programs"].invalidate()
        self.pump(0.25)
        self.assertGreaterEqual(self.draws, 1,
                                "a rebuilt screen was never repainted")

    def test_input_is_drawn_at_once_rather_than_on_the_next_tick(self):
        self.go("main")
        self.pump(0.3)
        self.draws = 0
        self.window.frames.note_interaction()
        self.pump(0.05)
        self.assertGreaterEqual(self.draws, 1, "input waited for a tick")


class TestTheThingsThatMustCostNothing(FrameEngineCase):

    def test_a_static_screen_is_not_redrawn(self):
        """The 34.6% of a core this whole change was about."""
        self.go("programs")
        self.pump(0.5)
        drawn = self.counted(3.0)
        self.assertLessEqual(
            drawn, 2,
            f"the PROGRAM INDEX was redrawn {drawn} times in three seconds "
            "with nothing happening")

    def test_a_static_screen_leaves_no_render_timer_running(self):
        self.go("tasks")
        self.pump(1.0)
        self.assertFalse(self.window.frames.running,
                         "the render timer is still running on a static screen")
        self.assertIn(self.window.frames.state, ("idle", "asleep"))

    def test_sampling_the_hardware_does_not_redraw_a_static_screen(self):
        """Telemetry is sampled on every screen; it is only news on some."""
        self.go("programs")
        self.pump(0.5)
        before = self.window.jobs.ran.get("telemetry", 0)
        drawn = self.counted(2.5)
        after = self.window.jobs.ran.get("telemetry", 0)
        self.assertGreater(after, before,
                           "the telemetry job is not running at all")
        self.assertLessEqual(
            drawn, 2,
            "sampling the gauges redrew a screen that does not show them")

    def test_the_background_jobs_all_run(self):
        self.go("main")
        self.pump(2.5)
        for name in ("windows", "telemetry", "ticks", "screenshots"):
            with self.subTest(job=name):
                self.assertGreater(
                    self.window.jobs.ran.get(name, 0), 0,
                    f"the {name} job never ran, so it is not being done")

    def test_the_interaction_grace_window_ends(self):
        self.go("programs")
        self.window.frames.note_interaction()
        self.pump(0.1)
        self.assertTrue(self.window.frames.running)
        self.pump(1.2)
        self.assertFalse(self.window.frames.running,
                         "the full rate outlived the interaction")

    def test_the_frame_cache_serves_an_unchanged_repaint(self):
        self.go("programs")
        self.pump(0.5)
        before = self.window._cache.hits
        # A repaint nobody asked for, which is what GTK does on its own when
        # a window is uncovered or moved.
        self.window.area.queue_draw()
        self.pump(0.3)
        self.assertGreater(
            self.window._cache.hits, before,
            "an unchanged repaint went through the glow again")

    def test_stopping_everything_stops_the_frames(self):
        self.go("main")
        self.window.jobs.stop()
        self.window.frames.stop()
        drawn = self.counted(1.5)
        self.assertEqual(drawn, 0, "a frame was drawn after shutdown")


class TestTheOldEngineStillWorks(FrameEngineCase):
    """``adaptive = false`` has to be a real way back, not a flag that lies."""

    def test_turning_adaptive_off_restores_the_fixed_rate(self):
        self.go("programs")
        self.window.frames.reconfigure(enabled=False, fps=20)
        self.pump(0.3)
        drawn = self.counted(1.0)
        self.assertGreaterEqual(
            drawn, 10,
            "adaptive = false did not go back to drawing on a clock")

    def test_an_idle_floor_keeps_a_static_screen_ticking(self):
        self.go("programs")
        self.window.frames.reconfigure(idle_fps=4.0)
        self.pump(0.3)
        drawn = self.counted(2.0)
        self.assertGreaterEqual(drawn, 3, "idle_fps had no effect")
        self.assertLessEqual(drawn, 14, "idle_fps behaved like the full rate")


if __name__ == "__main__":
    unittest.main()
