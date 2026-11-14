"""Tests for the adaptive frame scheduler.

The shell used to redraw thirty times a second whether or not the picture had
changed.  Measured frame against frame, the PROGRAM INDEX produced 89
identical pictures out of 89 while costing 34.6% of a core, and the home
screen changed 7 frames in 90.  :mod:`earth_os.scheduler` replaced the clock
with a request: a frame happens because something asked for one.

That makes the interesting failures *silence* rather than slowness — a screen
that stops updating is a worse bug than one that updates too often, and it is
much harder to notice.  So these tests are mostly about the three things that
must still get frames:

* a change anyone asked for, from any thread;
* input, for as long as the person keeps touching the machine;
* animation, for exactly as long as something is moving.

and the two things that must not happen:

* a timer left running after it is no longer needed;
* two timers where one was wanted.

:class:`FrameScheduler` takes its clock and its timer factory as arguments
precisely so this file needs no display, no main loop and no sleeping.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os.scheduler import Background, FrameScheduler, Timers  # noqa: E402


class FakeTimers(Timers):
    """A main loop driven by hand.

    Time only moves when a test moves it, and every timer that exists is
    visible in :attr:`repeating` and :attr:`one_shots`, so "is there a timer
    running" is a question with an answer rather than a guess.
    """

    def __init__(self) -> None:
        self.clock = 1000.0
        self._next = 1
        self.repeating: dict = {}
        self.one_shots: dict = {}
        self.cancelled: list = []
        self.idle: list = []

    # -- the Timers interface ---------------------------------------------
    def every(self, seconds, fn):
        handle = self._next
        self._next += 1
        self.repeating[handle] = (max(seconds, 0.0), fn, self.clock)
        return handle

    def once(self, seconds, fn):
        handle = self._next
        self._next += 1
        self.one_shots[handle] = (self.clock + max(seconds, 0.0), fn)
        return handle

    def cancel(self, handle):
        self.cancelled.append(handle)
        self.repeating.pop(handle, None)
        self.one_shots.pop(handle, None)

    def on_main(self, fn):
        self.idle.append(fn)

    def now(self):
        return self.clock

    # -- driving it -------------------------------------------------------
    def run_idle(self) -> int:
        """Run whatever a worker thread posted to the main loop."""
        posted, self.idle = self.idle, []
        for fn in posted:
            fn()
        return len(posted)

    def advance(self, seconds: float, step: float = 0.01) -> None:
        """Move time forward, firing timers as they come due."""
        target = self.clock + seconds
        while self.clock < target - 1e-9:
            self.clock = min(self.clock + step, target)
            for handle, (due, fn) in list(self.one_shots.items()):
                if self.clock >= due - 1e-9:
                    del self.one_shots[handle]
                    fn()
            for handle, (period, fn, last) in list(self.repeating.items()):
                if handle not in self.repeating:
                    continue
                if self.clock >= last + period - 1e-9:
                    self.repeating[handle] = (period, fn, self.clock)
                    if fn() is False:
                        self.repeating.pop(handle, None)

    @property
    def timer_count(self) -> int:
        return len(self.repeating) + len(self.one_shots)


def build(animation=None, **kw):
    """A scheduler wired to a counter, on hand-driven time."""
    timers = FakeTimers()
    painted = []
    scheduler = FrameScheduler(
        lambda: painted.append(timers.clock),
        timers=timers, animation_delay=animation, **kw)
    return scheduler, timers, painted


class TestAStaticScreenCostsNothing(unittest.TestCase):
    """The measurement this whole change exists for."""

    def test_nothing_is_drawn_and_no_timer_is_left_running(self):
        scheduler, timers, painted = build(animation=lambda: None, fps=30)
        scheduler.start()
        self.assertEqual(len(painted), 1, "the first frame must be drawn")

        timers.advance(10.0)
        self.assertEqual(len(painted), 1,
                         "a static, untouched screen was redrawn")
        self.assertEqual(timers.timer_count, 0,
                         "a static screen left a timer running")
        self.assertEqual(scheduler.state, "asleep")

    def test_the_old_engine_is_still_available(self):
        """``[shell] adaptive = false`` must restore the fixed clock exactly."""
        scheduler, timers, painted = build(
            animation=lambda: None, fps=30, enabled=False)
        scheduler.start()
        # Stepped finely: at the default 10 ms a 33 ms period resolves to 25
        # firings a second, which measures the fake loop and not the shell.
        timers.advance(1.0, step=0.001)
        self.assertGreaterEqual(len(painted), 29)
        self.assertLessEqual(len(painted), 31)


class TestAChangeIsAlwaysDrawn(unittest.TestCase):

    def test_a_request_draws_one_frame_and_then_settles(self):
        scheduler, timers, painted = build(animation=lambda: None, fps=30)
        scheduler.start()
        timers.advance(0.2)
        painted.clear()

        scheduler.request_frame()
        self.assertEqual(len(painted), 1, "a change was not drawn at once")
        timers.advance(2.0)
        self.assertEqual(len(painted), 1, "one change became many frames")

    def test_a_request_is_drawn_immediately_not_a_period_later(self):
        """A press must not wait 33 ms to be seen."""
        scheduler, timers, painted = build(animation=lambda: None, fps=30)
        scheduler.start()
        timers.advance(0.2)
        painted.clear()
        at = timers.clock
        scheduler.request_frame()
        self.assertEqual(painted, [at],
                         "the change waited for the next tick")

    def test_a_request_from_a_worker_thread_is_not_lost(self):
        """``Shell.redraw()`` is called from workers; nothing may be dropped."""
        scheduler, timers, painted = build(animation=lambda: None, fps=30)
        scheduler.start()
        timers.advance(0.2)
        painted.clear()

        def worker() -> None:
            scheduler.request_frame()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        # The flag is set from the thread; the timers are only touched on the
        # main loop, which is what the posted callable is for.
        self.assertTrue(timers.idle, "the worker did not post to the main loop")
        timers.run_idle()
        self.assertEqual(len(painted), 1,
                         "a change from a worker thread was never drawn")

    def test_many_requests_between_frames_do_not_queue_up_frames(self):
        scheduler, timers, painted = build(animation=lambda: None, fps=30)
        scheduler.start()
        painted.clear()
        for _ in range(50):
            scheduler.request_frame()
        timers.advance(0.5)
        self.assertLess(len(painted), 20,
                        "fifty changes in one period became fifty frames")


class TestInteractionHoldsTheFullRate(unittest.TestCase):

    def test_the_rate_is_held_after_input_and_then_released(self):
        scheduler, timers, painted = build(
            animation=lambda: None, fps=30, grace=0.5)
        scheduler.start()
        scheduler.note_interaction()
        self.assertEqual(scheduler.state, "active")

        timers.advance(0.2)
        self.assertEqual(scheduler.state, "active",
                         "the rate was dropped while the person was still "
                         "interacting")
        timers.advance(1.0)
        self.assertEqual(scheduler.state, "asleep",
                         "the full rate outlived the interaction")
        self.assertEqual(timers.timer_count, 0)

    def test_holding_the_rate_is_not_holding_the_work(self):
        """In the grace window with nothing changing, nothing is drawn.

        The timer stays alive so the next press is served within a period,
        but a pointer sitting still over dead space must not cost frames.
        """
        scheduler, timers, painted = build(
            animation=lambda: None, fps=30, grace=0.5)
        scheduler.start()
        scheduler.note_interaction(redraw=False)
        painted.clear()
        timers.advance(0.45)
        self.assertEqual(painted, [],
                         "the grace window drew frames with nothing to draw")

    def test_a_move_that_changes_something_is_drawn(self):
        scheduler, timers, painted = build(
            animation=lambda: None, fps=30, grace=0.5)
        scheduler.start()
        timers.advance(0.2)
        painted.clear()
        scheduler.note_interaction(redraw=True)
        self.assertEqual(len(painted), 1)


class TestAnimationGetsExactlyWhatItAsksFor(unittest.TestCase):

    def test_a_caret_at_two_hertz_costs_two_frames_a_second(self):
        scheduler, timers, painted = build(animation=lambda: 0.5, fps=30)
        scheduler.start()
        painted.clear()
        timers.advance(4.0)
        # Four seconds at one frame every half second.
        self.assertGreaterEqual(len(painted), 6)
        self.assertLessEqual(len(painted), 10,
                             "a 2 Hz caret was drawn at the full frame rate")

    def test_a_clock_at_one_hertz_costs_one_frame_a_second(self):
        scheduler, timers, painted = build(animation=lambda: 1.0, fps=30)
        scheduler.start()
        painted.clear()
        timers.advance(5.0)
        self.assertGreaterEqual(len(painted), 4)
        self.assertLessEqual(len(painted), 7)

    def test_an_animation_that_wants_every_frame_gets_every_frame(self):
        """Smooth is still smooth: zero means "as soon as you can"."""
        scheduler, timers, painted = build(animation=lambda: 0.0, fps=30)
        scheduler.start()
        painted.clear()
        timers.advance(1.0)
        self.assertGreaterEqual(len(painted), 25,
                                "a real animation was throttled")

    def test_an_animation_that_finishes_stops_costing_frames(self):
        state = {"moving": True}
        scheduler, timers, painted = build(
            animation=lambda: 0.0 if state["moving"] else None, fps=30)
        scheduler.start()
        timers.advance(0.3)
        state["moving"] = False
        timers.advance(0.3)
        painted.clear()
        timers.advance(3.0)
        self.assertEqual(painted, [],
                         "the timer kept running after the animation ended")
        self.assertEqual(timers.timer_count, 0,
                         "a finished animation left its timer behind")

    def test_a_screen_that_raises_is_given_frames_rather_than_a_freeze(self):
        def broken():
            raise RuntimeError("no idea")

        scheduler, timers, painted = build(animation=broken, fps=30)
        scheduler.start()
        painted.clear()
        timers.advance(0.5)
        self.assertGreater(len(painted), 5,
                           "a screen that cannot answer froze the shell")


class TestItDoesNotLeakTimers(unittest.TestCase):

    def test_arming_repeatedly_does_not_make_a_second_timer(self):
        scheduler, timers, painted = build(animation=lambda: 0.0, fps=30)
        scheduler.start()
        for _ in range(20):
            scheduler.request_frame()
        self.assertLessEqual(len(timers.repeating), 1,
                             "twenty requests made more than one timer")

    def test_stop_cancels_everything(self):
        scheduler, timers, painted = build(animation=lambda: 0.0, fps=30)
        scheduler.start()
        scheduler.note_interaction()
        self.assertGreater(timers.timer_count, 0)
        scheduler.stop()
        self.assertEqual(timers.timer_count, 0)
        painted.clear()
        timers.advance(2.0)
        self.assertEqual(painted, [], "a frame was drawn after stop()")

    def test_stop_is_idempotent(self):
        scheduler, timers, _ = build(animation=lambda: 0.0)
        scheduler.start()
        scheduler.stop()
        scheduler.stop()
        self.assertEqual(timers.timer_count, 0)

    def test_reconfiguring_does_not_leave_the_old_timer_behind(self):
        scheduler, timers, painted = build(animation=lambda: 0.0, fps=30)
        scheduler.start()
        scheduler.reconfigure(fps=10)
        self.assertLessEqual(len(timers.repeating), 1,
                             "the 30 fps timer survived the change to 10")
        painted.clear()
        timers.advance(1.0)
        self.assertLessEqual(len(painted), 12,
                             "frames kept arriving at the old rate")


class TestTheIdleFloor(unittest.TestCase):
    """``[shell] idle_fps`` is the way back if something draws silently."""

    def test_a_floor_keeps_a_static_screen_ticking_over(self):
        scheduler, timers, painted = build(
            animation=lambda: None, fps=30, idle_fps=2.0)
        scheduler.start()
        painted.clear()
        timers.advance(3.0)
        self.assertGreaterEqual(len(painted), 4)
        self.assertLessEqual(len(painted), 9,
                             "the floor behaved like the full frame rate")

    def test_zero_means_no_floor(self):
        scheduler, timers, painted = build(
            animation=lambda: None, fps=30, idle_fps=0.0)
        scheduler.start()
        painted.clear()
        timers.advance(3.0)
        self.assertEqual(painted, [])


class TestTheGenerationCounter(unittest.TestCase):
    """What the frame cache keys on; it must move whenever content might."""

    def test_it_moves_on_every_request(self):
        scheduler, timers, _ = build(animation=lambda: None)
        scheduler.start()
        before = scheduler.generation
        scheduler.request_frame()
        self.assertGreater(scheduler.generation, before)

    def test_it_does_not_move_when_nothing_was_asked_for(self):
        scheduler, timers, _ = build(animation=lambda: None)
        scheduler.start()
        timers.advance(2.0)
        before = scheduler.generation
        timers.advance(2.0)
        self.assertEqual(scheduler.generation, before,
                         "the cache would miss on an unchanged picture")


class TestBackgroundWorkIsIndependentOfTheFrameRate(unittest.TestCase):

    def test_a_job_runs_on_its_own_interval(self):
        scheduler, timers, _ = build(animation=lambda: None, fps=30)
        scheduler.start()
        jobs = Background(scheduler, timers)
        runs = []
        jobs.add("poll", 1.0, lambda: runs.append(timers.clock) or False)
        timers.advance(5.0)
        self.assertGreaterEqual(len(runs), 4)
        self.assertLessEqual(len(runs), 6,
                             "a one-second job ran at the frame rate")

    def test_a_job_that_changes_nothing_costs_no_frame(self):
        scheduler, timers, painted = build(animation=lambda: None, fps=30)
        scheduler.start()
        jobs = Background(scheduler, timers)
        jobs.add("telemetry", 1.0, lambda: False)
        painted.clear()
        timers.advance(6.0)
        self.assertEqual(painted, [],
                         "sampling the hardware redrew the screen")

    def test_a_job_that_changes_something_asks_for_a_frame(self):
        scheduler, timers, painted = build(animation=lambda: None, fps=30)
        scheduler.start()
        jobs = Background(scheduler, timers)
        jobs.add("telemetry", 1.0, lambda: True)
        painted.clear()
        timers.advance(3.5)
        self.assertGreaterEqual(len(painted), 3,
                                "a reading moved and nothing was redrawn")

    def test_a_job_that_throws_keeps_its_timer(self):
        """Otherwise the shell quietly stops polling for windows."""
        scheduler, timers, _ = build(animation=lambda: None)
        scheduler.start()
        jobs = Background(scheduler, timers)
        runs = []

        def broken():
            runs.append(1)
            raise OSError("the compositor said no")

        jobs.add("windows", 1.0, broken)
        timers.advance(4.0)
        self.assertGreaterEqual(len(runs), 3,
                               "one failure killed the background job")

    def test_jobs_stop_cleanly(self):
        scheduler, timers, _ = build(animation=lambda: None)
        scheduler.start()
        jobs = Background(scheduler, timers)
        jobs.add("a", 1.0, lambda: False)
        jobs.add("b", 2.0, lambda: False)
        self.assertEqual(jobs.names, ["a", "b"])
        jobs.stop()
        self.assertEqual(jobs.names, [])
        scheduler.stop()
        self.assertEqual(timers.timer_count, 0)

    def test_adding_the_same_job_twice_replaces_it(self):
        scheduler, timers, _ = build(animation=lambda: None)
        scheduler.start()
        jobs = Background(scheduler, timers)
        runs = []
        jobs.add("poll", 1.0, lambda: runs.append("first") or False)
        jobs.add("poll", 1.0, lambda: runs.append("second") or False)
        timers.advance(2.5)
        self.assertNotIn("first", runs,
                         "the replaced job is still running: a duplicate "
                         "callback")


if __name__ == "__main__":
    unittest.main()
