"""When the next frame happens, and why.

The shell used to redraw thirty times a second whether or not anything had
changed.  Measured on the real process at 1920x1080, that cost 47.6% of a
core on the home screen and 34.6% on the PROGRAM INDEX — a screen on which,
measured frame against frame, **100% of the pictures were identical to the
one before**.  The home screen changed 7 frames in 90; ENGINEERING changed 3.
The machine was spending a third of a core drawing the same picture again.

So frames are no longer produced on a clock.  They are produced because
something asked for one, and the three things that ask are:

* **a change** — :meth:`FrameScheduler.request_frame`.  Navigation, a press,
  a reading that moved, output from a command.  This is what
  ``Shell.redraw()`` has always meant; it now reaches a scheduler instead of
  an unconditional ``queue_draw``.
* **interaction** — :meth:`FrameScheduler.note_interaction`.  While a finger
  is down or a pointer is moving, the full configured rate runs, because a
  drag that updates when it feels like it is worse than one that costs CPU.
  It keeps running for :attr:`grace` seconds after the last event so the
  release and its flash are smooth.
* **animation** — the screen on display is asked, every frame,
  :meth:`~earth_os.screens.base.Screen.next_frame_in`: how long until your
  picture changes by itself?  A terminal caret says half a second, a screen
  with a running clock says however long is left of this second, and a static
  screen says never.  Never means the timer is removed and the process sleeps
  in ``poll`` until something wakes it.

Three states fall out of that rather than being coded as a state machine:

======  ================================================================
idle    no render timer at all; at most one armed wake-up
active  the render timer at ``[shell] fps``, because input or animation
        needs it
asleep  idle with nothing even armed: the screen is static and untouched
======  ================================================================

Background work is no longer carried on the frame clock either.  Window
polling, GODCORE refreshes, screenshot notices and telemetry sampling each
run on their own interval, so lowering the frame rate cannot slow them down
and raising it cannot make them hammer the machine.  That separation is the
point: telemetry is sampled once a second and asks for a frame *only when a
reading it would draw has actually changed*.

Nothing here touches Cairo or GTK drawing.  It owns timers and one callback
that invalidates the drawing area, which is what makes it testable without a
display: :class:`FrameScheduler` takes its clock and its timer factory as
arguments, and ``tests/test_scheduler.py`` drives it with a fake pair.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

#: A timer handle is whatever the loop gave us back.
Handle = object


class Timers:
    """The GLib main loop, behind an interface a test can replace.

    :func:`GLib.timeout_add` is the whole dependency on the toolkit, and it
    is three methods wide.  Keeping it behind this makes the scheduler
    ordinary Python: no display, no main loop, no sleeping in tests.
    """

    def every(self, seconds: float, fn: Callable[[], bool]) -> Handle:
        """Call *fn* every *seconds* until it returns False."""
        from gi.repository import GLib
        return GLib.timeout_add(max(int(seconds * 1000), 1), fn)

    def once(self, seconds: float, fn: Callable[[], None]) -> Handle:
        """Call *fn* once, after *seconds*."""
        from gi.repository import GLib

        def run() -> bool:
            fn()
            return False

        return GLib.timeout_add(max(int(seconds * 1000), 1), run)

    def cancel(self, handle: Handle) -> None:
        from gi.repository import GLib
        GLib.source_remove(handle)

    def on_main(self, fn: Callable[[], None]) -> None:
        """Run *fn* on the main loop, from whatever thread we are on."""
        from gi.repository import GLib

        def run() -> bool:
            fn()
            return False

        GLib.idle_add(run)

    def now(self) -> float:
        return time.monotonic()


class FrameScheduler:
    """Owns the decision of when to paint, and the timers that do it.

    *invalidate* is called when a frame should be drawn — in the shell that
    is ``drawing_area.queue_draw``.  *animation_delay* is asked how long
    until the thing on screen changes by itself, in seconds, or ``None`` for
    "nothing is moving"; in the shell that is the current screen's
    :meth:`~earth_os.screens.base.Screen.next_frame_in`.
    """

    def __init__(self, invalidate: Callable[[], None], *,
                 fps: int = 30,
                 idle_fps: float = 0.0,
                 grace: float = 0.45,
                 animation_delay: Optional[Callable[[], Optional[float]]] = None,
                 timers: Optional[Timers] = None,
                 enabled: bool = True) -> None:
        self._invalidate = invalidate
        self._animation_delay = animation_delay
        self.timers = timers or Timers()
        #: Full rate, used while interacting or animating.
        self.fps = max(int(fps), 1)
        #: A floor, for anyone who wants the old behaviour back without
        #: turning adaptive rendering off entirely. 0 means no floor.
        self.idle_fps = max(float(idle_fps), 0.0)
        #: Seconds of full-rate rendering after the last input event.
        self.grace = max(float(grace), 0.0)
        #: False restores the original fixed-rate clock exactly.
        self.enabled = bool(enabled)

        self._main_thread = threading.current_thread()
        self._render: Optional[Handle] = None
        self._wake: Optional[Handle] = None
        self._wake_at: Optional[float] = None
        self._dirty = False
        self._active_until = 0.0
        self._stopped = False
        #: When the last frame was drawn, so a change can be drawn the
        #: instant it arrives without a flood of changes outrunning the
        #: configured rate. Negative so the first frame is never held back.
        self._last_paint = float("-inf")

        #: Bumped whenever a frame is asked for, so a cache can tell whether
        #: the picture it holds is still the current one.  See
        #: :class:`earth_os.phosphor.FrameCache`.
        self.generation = 1

        # Counters, for the diagnostics screen and for tests that want to
        # assert the machine is not working when it should be resting.
        self.frames_drawn = 0
        self.frames_requested = 0
        self.wakeups = 0
        self.idle_periods = 0

    # ------------------------------------------------------------------
    @property
    def period(self) -> float:
        return 1.0 / float(self.fps)

    @property
    def running(self) -> bool:
        """True while the full-rate render timer exists."""
        return self._render is not None

    @property
    def armed(self) -> bool:
        """True while a one-shot wake-up is pending."""
        return self._wake is not None

    @property
    def state(self) -> str:
        if self._render is not None:
            return "active"
        if self._wake is not None:
            return "idle"
        return "asleep"

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Draw the first frame and settle into whatever state follows."""
        self._stopped = False
        if not self.enabled:
            # The original engine: one repeating timer, draw every tick.
            self._render = self.timers.every(self.period, self._fixed_tick)
            return
        self.request_frame()

    def stop(self) -> None:
        """Cancel every timer.  Called on shutdown and on reconfiguration."""
        self._stopped = True
        self._cancel_render()
        self._cancel_wake()

    def reconfigure(self, *, fps: Optional[int] = None,
                    idle_fps: Optional[float] = None,
                    grace: Optional[float] = None,
                    enabled: Optional[bool] = None) -> None:
        """Change the rate while running, without leaking the old timer."""
        if fps is not None:
            self.fps = max(int(fps), 1)
        if idle_fps is not None:
            self.idle_fps = max(float(idle_fps), 0.0)
        if grace is not None:
            self.grace = max(float(grace), 0.0)
        if enabled is not None:
            self.enabled = bool(enabled)
        self.stop()
        self.start()

    # ------------------------------------------------------------------
    def request_frame(self) -> None:
        """Something changed: draw a frame as soon as the loop will let us.

        Safe from any thread.  The flag is set here so a worker's change is
        never lost, and the timers are only touched on the main loop.
        """
        self._dirty = True
        self.frames_requested += 1
        self.generation += 1
        self._from_any_thread(self._arm)

    def note_interaction(self, *, redraw: bool = True) -> None:
        """Input happened: hold the full rate for :attr:`grace` seconds."""
        self._active_until = self.timers.now() + self.grace
        if redraw:
            self.request_frame()
        else:
            self._from_any_thread(self._arm)

    def wake_at(self, when: float) -> None:
        """Draw no later than the monotonic time *when*."""
        if self._wake_at is None or when < self._wake_at:
            self._wake_at = when
            self._from_any_thread(self._arm)

    def wake_in(self, seconds: float) -> None:
        self.wake_at(self.timers.now() + max(seconds, 0.0))

    # ------------------------------------------------------------------
    def _from_any_thread(self, fn: Callable[[], None]) -> None:
        if threading.current_thread() is self._main_thread:
            fn()
        else:
            self.timers.on_main(fn)

    def _cancel_render(self) -> None:
        if self._render is not None:
            try:
                self.timers.cancel(self._render)
            except Exception:
                pass
            self._render = None

    def _cancel_wake(self) -> None:
        if self._wake is not None:
            try:
                self.timers.cancel(self._wake)
            except Exception:
                pass
            self._wake = None

    # ------------------------------------------------------------------
    def _fixed_tick(self) -> bool:
        """The original engine, kept for ``[shell] adaptive = false``."""
        if self._stopped:
            return False
        self._paint()
        return True

    def _arm(self) -> None:
        """Make the timers match what is currently needed.

        The only place timers are created.  Called after every change of
        state, and idempotent: arming twice does not make two timers.
        """
        if self._stopped or not self.enabled:
            return
        now = self.timers.now()

        # Draw a change the moment it arrives rather than up to a period
        # later: a press must not wait 33 ms to be seen. The guard is the
        # frame rate itself — a burst of changes inside one period is drawn
        # once, by the timer below, instead of once per change.
        if self._dirty and now - self._last_paint >= self.period - 1e-9:
            self._paint()
            now = self.timers.now()

        wants_full_rate = self._dirty or now < self._active_until

        if wants_full_rate:
            self._cancel_wake()
            if self._render is None:
                self._render = self.timers.every(self.period, self._render_tick)
            return

        # Nothing needs the full rate.  Stop the timer and arm at most one
        # wake-up, for the earliest deadline anything has asked for.
        self._cancel_render()
        deadline = self._next_deadline(now)
        if deadline is None:
            self._cancel_wake()
            self._wake_at = None
            return
        if self._wake is not None and self._wake_at is not None \
                and self._wake_at <= deadline:
            return
        self._cancel_wake()
        self._wake_at = deadline
        self._wake = self.timers.once(max(deadline - now, 0.0), self._on_wake)

    def _next_deadline(self, now: float) -> Optional[float]:
        """The soonest monotonic time anything wants a frame, or None."""
        candidates: List[float] = []
        if self._wake_at is not None and self._wake_at > now:
            candidates.append(self._wake_at)
        delay = self._ask_animation()
        if delay is not None:
            candidates.append(now + max(delay, 0.0))
        if self.idle_fps > 0.0:
            candidates.append(now + 1.0 / self.idle_fps)
        return min(candidates) if candidates else None

    def _ask_animation(self) -> Optional[float]:
        if self._animation_delay is None:
            return None
        try:
            delay = self._animation_delay()
        except Exception:
            # A screen that cannot say what it needs must not stop the shell
            # from drawing; assume it wants the next frame.
            return 0.0
        if delay is None:
            return None
        return max(float(delay), 0.0)

    def _on_wake(self) -> None:
        self._wake = None
        self._wake_at = None
        self.wakeups += 1
        if self._stopped:
            return
        self._paint()
        self._arm()

    def _render_tick(self) -> bool:
        if self._stopped:
            self._render = None
            return False
        now = self.timers.now()
        animation = self._ask_animation()
        due = (self._dirty
               or (animation is not None and animation <= self.period))
        if due:
            self._paint()
            return True
        if now < self._active_until:
            # Still in the grace window after an input event.  The timer is
            # kept alive so the next press is served within one period, but
            # there is nothing to draw, so nothing is drawn: holding the
            # rate is not the same as holding the work.
            return True
        # Nothing wants this tick.  Drop out of the full rate and let _arm
        # decide whether to sleep or to arm a wake-up.
        self._render = None
        self.idle_periods += 1
        self._arm()
        return False

    def _paint(self) -> None:
        self._dirty = False
        self._last_paint = self.timers.now()
        self.frames_drawn += 1
        self._invalidate()

    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, object]:
        """What the scheduler has been doing, for the diagnostics screen."""
        return {
            "state": self.state,
            "fps": self.fps,
            "adaptive": self.enabled,
            "frames_drawn": self.frames_drawn,
            "frames_requested": self.frames_requested,
            "wakeups": self.wakeups,
            "idle_periods": self.idle_periods,
        }


class Background:
    """The periodic work that is not drawing, on its own intervals.

    Window polling shells out to the compositor, GODCORE reads a reasoning
    engine, the screenshot notice stats a folder and telemetry reads
    ``/proc``.  None of that is rendering, and on the old engine all of it
    was carried by the frame clock — so a machine set to 10 fps polled for
    windows a third as eagerly as one set to 30, which is not a frame rate
    decision.  Each job gets an interval here and keeps it whatever the
    frame rate is.

    A job returns True when it changed something a person would see, and the
    scheduler is asked for a frame only then.  That is what keeps live
    telemetry from costing thirty frames a second: the readings are sampled
    once a second, and a frame is drawn when a reading moves.
    """

    def __init__(self, scheduler: FrameScheduler,
                 timers: Optional[Timers] = None) -> None:
        self.scheduler = scheduler
        self.timers = timers or scheduler.timers
        self._jobs: Dict[str, Handle] = {}
        self.ran: Dict[str, int] = {}
        self.changed: Dict[str, int] = {}

    def add(self, name: str, seconds: float,
            job: Callable[[], Optional[bool]]) -> None:
        """Run *job* every *seconds*; request a frame when it returns True."""
        self.remove(name)
        self.ran.setdefault(name, 0)
        self.changed.setdefault(name, 0)

        def run() -> bool:
            self.ran[name] = self.ran.get(name, 0) + 1
            try:
                changed = job()
            except Exception:
                # A background job that throws must not take the timer with
                # it, or the shell quietly stops polling for windows.
                return True
            if changed:
                self.changed[name] = self.changed.get(name, 0) + 1
                self.scheduler.request_frame()
            return True

        self._jobs[name] = self.timers.every(max(seconds, 0.01), run)

    def remove(self, name: str) -> None:
        handle = self._jobs.pop(name, None)
        if handle is not None:
            try:
                self.timers.cancel(handle)
            except Exception:
                pass

    def stop(self) -> None:
        for name in list(self._jobs):
            self.remove(name)

    @property
    def names(self) -> List[str]:
        return sorted(self._jobs)
