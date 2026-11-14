"""GTK4 front end: window, frame clock and input plumbing.

The whole UI is one ``Gtk.DrawingArea``.  Everything above this file is
toolkit-agnostic, which is what lets ``tools/screenshot.py`` render the same
screens straight to a PNG without a display server.

## One window, from the first frame to the last

There used to be two: a ``SplashWindow`` while the shell was built, and an
``EarthWindow`` once it existed, with a handover between them — present the
second, destroy the first, and hope the compositor never showed a frame in
between.  It usually did not.  "Usually" is the wrong guarantee for the one
moment the machine is being judged on.

So there is one window now.  It comes up drawing :mod:`earth_os.splash` —
the LCARS frame with nothing in it — and starts drawing screens in the same
surface once there are screens to draw.  The frame does not move because it is
not redrawn by something else; it is the same pixels throughout, and what
changes is only what is inside them.  There is no handover left to get wrong.

``self.shell`` is therefore ``Optional``: ``None`` means the machine is still
coming up, and every handler below treats that as "not yet" rather than
assuming a shell that may not exist.

## The heavy imports are deferred on purpose

:class:`~earth_os.core.Shell`, the screens and the voice engine are imported
inside the boot steps, not at the top of this file.  Imported here they would
be paid before :meth:`EarthApplication.do_activate` could run, which is before
any window can exist — 58 ms during which the machine shows a console or a
grey root window, and no splash can cover it, because the splash cannot be on
screen until this module has finished loading.

That is the whole point of the screen, so the imports moved.  Keep them there.
"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .config import Config
from . import brand, phosphor, splash
from .scheduler import Background, FrameScheduler

if TYPE_CHECKING:                       # names for annotations only: importing
    from .core import Shell             # these for real would cost the frame
    from .widgets.base import Element   # that this file exists to put up first

APP_ID = "org.earthos.shell"

#: Frames per second while the splash is up. The screen animates one sweeping
#: element and nothing else, and a boot screen competing for CPU with the boot
#: is self-defeating. The shell's own rate takes over at :meth:`attach`.
BOOT_FPS = 15


class EarthWindow(Gtk.ApplicationWindow):
    """The window. There is only ever one, and it opens before the shell.

    Until :meth:`attach` is called it draws the boot frame; afterwards it
    draws screens into the same surface.
    """

    def __init__(self, application: "EarthApplication", config: Config):
        super().__init__(application=application, title=brand.NAME)
        #: ``None`` until the shell has been built. Everything below reads
        #: this as "the machine is still coming up".
        self.shell: Optional["Shell"] = None
        self.config = config
        self.message = "starting"
        self._opened = time.monotonic()
        self._pressed: Optional["Element"] = None
        self._pointer: tuple = (0.0, 0.0)
        self._last_tick = 0.0

        #: Decides when frames happen; see :mod:`earth_os.scheduler`. Starts
        #: on the boot rate and is reconfigured from the shell's own settings
        #: at :meth:`attach`.
        self.frames = FrameScheduler(
            self._invalidate, fps=BOOT_FPS, grace=0.45,
            animation_delay=self._animation_delay,
            # The boot screen genuinely animates every frame, so adaptive
            # rendering has nothing to save until there is a shell.
            enabled=False)
        #: Window polling, GODCORE, screenshot notices and telemetry, each on
        #: its own interval rather than on the frame clock.
        self.jobs = Background(self.frames)
        #: The last composited frame, for repaints GTK asks for itself.
        self._cache = phosphor.FrameCache()
        self._cache_enabled = True

        fullscreen = bool(config.get("shell", "fullscreen", True))
        self.set_default_size(1280, 800)
        self.set_decorated(not fullscreen)

        self.area = Gtk.DrawingArea()
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        self.area.set_draw_func(self._on_draw)
        self.set_child(self.area)

        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect("pressed", self._on_pressed)
        click.connect("released", self._on_released)
        self.area.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.area.add_controller(motion)

        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.area.add_controller(scroll)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        # Looking away from the shell closes the exit menu: Alt+Tab to a
        # program, or a press that lands on one, takes focus from this window.
        self.connect("notify::is-active", self._on_active_changed)

        if fullscreen:
            self.fullscreen()

        # No pointer until the shell can take input. The contract is that a
        # visible cursor means ready, so it must not appear early — and
        # because this is the same window throughout, "revealed" is now a
        # property change rather than a second window arriving.
        self.set_cursor(Gdk.Cursor.new_from_name("none", None))

        css = Gtk.CssProvider()
        css.load_from_data(b"window, drawingarea { background-color: #000000; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.frames.start()

    # ------------------------------------------------------------------
    def _invalidate(self) -> None:
        """Ask GTK for a frame. The scheduler's only way out to the toolkit."""
        self.area.queue_draw()

    def _animation_delay(self) -> Optional[float]:
        """How long until the picture changes by itself, for the scheduler.

        Before there is a shell this is the boot sweep, which moves every
        frame. Afterwards it is whatever the screen on display says.
        """
        shell = self.shell
        if shell is None:
            return 0.0
        screen = shell.screen()
        if screen is None:
            return 0.0
        try:
            return screen.next_frame_in()
        except Exception:
            # A screen that cannot answer gets frames rather than a freeze.
            return 0.0

    def say(self, message: str) -> None:
        """Set the boot diagnostic line. Ignored once the shell is up."""
        self.message = message
        self.frames.request_frame()

    def attach(self, shell: "Shell") -> None:
        """Start drawing the shell into the window that is already open.

        This is what used to be a window handover, and it is now an attribute
        assignment. Nothing is presented, nothing is destroyed, and there is
        no frame in which the compositor can show what is underneath.
        """
        self.shell = shell

        # The cursor is the readiness signal: hidden since the window opened,
        # revealed here, at the first moment the shell can take input. A
        # person across the room can tell the machine is up without reading
        # anything. Honoured unless the configuration wants no pointer at
        # all, which is the kiosk case.
        if bool(shell.config.get("shell", "cursor", True)):
            self.set_cursor(None)

        config = shell.config
        self._cache_enabled = bool(config.get("shell", "frame_cache", True))
        self.frames.reconfigure(
            fps=max(int(config.get("shell", "fps", 30)), 5),
            idle_fps=float(config.get("shell", "idle_fps", 0) or 0),
            grace=float(config.get("shell", "interaction_grace", 0.45)),
            enabled=bool(config.get("shell", "adaptive", True)))
        # What the window knows and the shell cannot otherwise see: how
        # often frames happen, what one costs, and what the periodic jobs
        # have been doing. Published as a callable so reading it is the
        # reader's cost, paid only when something asks.
        shell.render_stats = self.render_stats
        # A setting the window owns, changed while running, is applied here
        # rather than waiting for a restart: see :meth:`apply_setting`.
        shell.settings_hook = self.apply_setting
        self._start_background(shell)
        self.frames.request_frame()

    #: Settings this window reads, and so can apply while running.
    FRAME_SETTINGS = ("fps", "idle_fps", "adaptive", "interaction_grace")
    JOB_SETTINGS = (("shell", "telemetry_interval"), ("shell", "tick_interval"),
                    ("windows", "poll_interval"), ("evolution", "background_interval"),
                    ("evolution", "enabled"))

    def apply_setting(self, section: str, key: str) -> bool:
        """Put a changed setting into effect now. True if this window did.

        Until this existed every one of these was read once, at
        :meth:`attach`, so changing one meant restarting Earth OS — and the
        System Evolution Center's SETTINGS said a change applied "now" while
        the timer it governed went on at its old rate. Each branch uses a
        path that already exists for exactly this: the scheduler's own
        ``reconfigure``, which cancels the old timer before arming the new,
        and ``Background.add``, which replaces a job rather than adding a
        second one. Only the interface thread touches timers.
        """
        shell = self.shell
        if shell is None:
            return False
        config = shell.config
        if section == "shell" and key in self.FRAME_SETTINGS:
            self.frames.reconfigure(
                fps=max(int(config.get("shell", "fps", 30)), 5),
                idle_fps=float(config.get("shell", "idle_fps", 0) or 0),
                grace=float(config.get("shell", "interaction_grace", 0.45)),
                enabled=bool(config.get("shell", "adaptive", True)))
        elif (section, key) == ("shell", "frame_cache"):
            self._cache_enabled = bool(config.get("shell", "frame_cache", True))
        elif (section, key) == ("shell", "phosphor"):
            pass                        # read on every draw; a frame shows it
        elif (section, key) in self.JOB_SETTINGS:
            self._start_background(shell)
        else:
            return False
        self.frames.request_frame()
        return True

    def render_stats(self) -> dict:
        """Frame and background-job statistics, measured, for the shell.

        The scheduler's own counters, with each job's tally beside them —
        ``Background.ran`` and ``changed`` were kept "for the diagnostics
        screen" and this is the first thing to read them.
        """
        stats = dict(self.frames.stats())
        stats["frame_cache"] = bool(self._cache_enabled)
        stats["jobs"] = {name: {"ran": self.jobs.ran.get(name, 0),
                                "changed": self.jobs.changed.get(name, 0),
                                "every": self.jobs.intervals.get(name, 0.0)}
                         for name in self.jobs.names}
        return stats

    # ------------------------------------------------------------------
    def _start_background(self, shell: "Shell") -> None:
        """Put the periodic work on its own intervals, off the frame clock.

        All four of these used to run inside ``_on_frame``, which tied how
        often the machine looked for a new window to how smoothly it drew —
        two unrelated things. Each job returns True when it changed
        something a person would see, and only then is a frame asked for.

        Safe to call again while running: a job whose interval is unchanged
        is left alone, one whose interval changed is replaced, and the
        System Evolution Center's job is removed when its interval is 0.
        """
        config = shell.config
        poll = max(float(config.get("windows", "poll_interval", 1.2)), 0.2)
        telemetry = max(float(config.get("shell", "telemetry_interval", 1.0)),
                        0.1)
        tick = max(float(config.get("shell", "tick_interval", 0.1)), 0.02)

        def windows() -> bool:
            # poll_due still arms its own interval, so a configuration that
            # asks for a slower poll than this timer still gets one.
            if not shell.poll_due:
                return False
            before = shell.window_signature()
            shell.poll_windows()
            return shell.window_signature() != before

        def godcore() -> bool:
            # Throttled to six seconds inside, and the read itself runs on a
            # worker thread, which asks for its own frame when it lands.
            shell.refresh_cdos()
            return False

        def screenshots() -> bool:
            before = shell.last_screenshot
            shell.notice_screenshots()
            return shell.last_screenshot != before

        def readings() -> bool:
            """Sample the hardware, and draw only if a reading moved.

            This is the line between telemetry and rendering. The gauges are
            live; they are not a reason to redraw the whole screen thirty
            times a second, and at one sample a second a frame follows only
            when the picture would differ.
            """
            before = shell.telemetry_signature()
            shell.monitor.poll()
            if shell.telemetry_signature() == before:
                return False
            screen = shell.screen()
            # A reading changing is only news on a screen that shows one.
            return bool(screen is not None and getattr(screen, "live", False))

        def ticks() -> bool:
            # Screens notice their own news here — command output, a
            # refreshed mailbox — and ask for a frame themselves when they
            # find any, because only they know whether it is visible.
            screen = shell.screen()
            if screen is not None:
                screen.tick()
            self._publish_frame_rect()
            return False

        wanted = {"windows": (poll, windows), "godcore": (max(poll, 1.0), godcore),
                  "screenshots": (1.0, screenshots), "telemetry": (telemetry, readings),
                  "ticks": (tick, ticks)}

        # The System Evolution Center keeps a slow trend while it is not on
        # screen, so "has memory been growing" has more to go on than the
        # minutes it was open. One sample a minute from readings the shell
        # already holds; nothing at all until the center has been opened
        # once, and 0 switches it off.
        slow = float(config.get("evolution", "background_interval", 60.0) or 0.0)
        if slow > 0 and bool(config.get("evolution", "enabled", True)):
            wanted["evolution"] = (max(slow, 5.0), shell.evolution_background)
        else:
            self.jobs.remove("evolution")

        for name, (seconds, job) in wanted.items():
            if self.jobs.intervals.get(name) != max(float(seconds), 0.01):
                self.jobs.add(name, seconds, job)

    # ------------------------------------------------------------------
    def _on_draw(self, area, cr, width, height) -> None:
        shell = self.shell
        # The splash and the shell go through the same tube, so the glow and
        # scanlines do not switch on at handover.
        tube = phosphor.enabled(shell.config if shell is not None
                                else getattr(self, "config", None))
        if shell is None:
            elapsed = time.monotonic() - self._opened
            phosphor.composite(
                cr, width, height,
                lambda c: splash.draw_frame(c, width, height,
                                            message=self.message,
                                            elapsed=elapsed),
                background=splash.context(width, height).palette.background,
                enabled=tube)
            return
        shell.surface_size = (int(width), int(height))
        ctx = shell.context(width, height)
        screen = shell.screen()

        def paint(c) -> None:
            if screen is not None:
                screen.draw(c, ctx)

        # The cache is offered a token only when the picture is not moving.
        # While anything animates — a caret, a flash, a clock mid-second —
        # the drawing depends on ``ctx.now`` as well as on the model, and a
        # token that stood only for the model would hand back a stale frame.
        # None means "composite this properly and keep nothing".
        token = None
        if self._cache_enabled and self._animation_delay() is None:
            token = (self.frames.generation, id(screen), tube)
        started = time.perf_counter()
        self._cache.draw(cr, width, height, paint,
                         background=shell.palette.background,
                         enabled=tube, token=token)
        # What this frame cost, cached or not: a measurement for anything
        # that wants to say whether drawing is expensive on this machine.
        self.frames.note_draw(time.perf_counter() - started)

    def _publish_frame_rect(self) -> None:
        """Tell the shell where, in screen pixels, a program should be put.

        The shell runs fullscreen at the origin, so its own coordinates are
        screen coordinates. Windowed, the offset is unknown on Wayland, and
        frame mode is not useful there anyway.
        """
        if self.shell is None:
            return
        main = self.shell.screens.get("main")
        if main is None:
            return
        # The rectangle belongs to the main screen's layout, but a program can
        # be launched from any screen, so build main's geometry even when it
        # is not the one on display. ensure_built is a no-op unless the size
        # changed, so this costs nothing per frame.
        width = self.area.get_width() or self.get_width()
        height = self.area.get_height() or self.get_height()
        if width > 0 and height > 0:
            try:
                main.ensure_built(self.shell.context(width, height))
            except Exception:
                pass
        rect = getattr(main, "viewport_candidate", None)
        if rect is None:
            return
        x, y, w, h = rect
        # Under a tiling compositor the shell may not be "fullscreen" in GTK's
        # sense even while it covers the output, so trust the compositor's
        # output rectangle when we have one.
        origin = self.shell.output_rect
        if origin is not None:
            ox, oy = origin[0], origin[1]
            self.shell.frame_rect = (int(ox + x), int(oy + y), int(w), int(h))
            return
        if not self.is_fullscreen():
            # Not fullscreen and no compositor to ask: the offset would be
            # guesswork, so do not place anything.
            self.shell.frame_rect = None
            return
        self.shell.frame_rect = (int(x), int(y), int(w), int(h))

    # ------------------------------------------------------------------
    def _screen(self):
        """The screen taking input, or ``None`` if there is not one yet.

        Input arriving before the shell exists is dropped rather than queued.
        A press aimed at a frame with nothing in it was not aimed at anything,
        and replaying it once the buttons appear would act on a control the
        person never saw.
        """
        shell = self.shell
        return None if shell is None else shell.screen()

    def _on_pressed(self, gesture, n_press, x, y) -> None:
        screen = self._screen()
        if screen is not None:
            self._pressed = screen.on_press(x, y)
            # A press is the start of a drag as often as not, so the full
            # rate is held from here rather than discovered a frame late.
            self.frames.note_interaction()

    def _on_released(self, gesture, n_press, x, y) -> None:
        screen = self._screen()
        if screen is not None:
            screen.on_release(x, y, self._pressed)
        self._pressed = None
        self.frames.note_interaction()

    def _on_motion(self, controller, x, y) -> None:
        self._pointer = (x, y)
        screen = self._screen()
        if screen is None:
            return
        changed = screen.on_motion(x, y)
        # Either way the pointer is moving, so stay at the full rate: the
        # next move may cross into a button, and that must light up at once.
        # Only a move that actually changed something asks for a frame.
        self.frames.note_interaction(redraw=bool(changed))

    def _on_scroll(self, controller, dx, dy) -> bool:
        # GTK4 scroll events carry no coordinates, so use the last position
        # the motion controller saw.
        screen = self._screen()
        if screen is None:
            return False
        x, y = self._pointer
        if screen.on_scroll(x, y, dy):
            self.frames.note_interaction()
            return True
        self.frames.note_interaction(redraw=False)
        return False

    # ------------------------------------------------------------------
    def _on_active_changed(self, window, _param) -> None:
        shell = self.shell
        if shell is not None and not self.is_active():
            shell.shell_lost_focus()

    def _on_key(self, controller, keyval, keycode, state) -> bool:
        name = Gdk.keyval_name(keyval) or ""
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        screen = self._screen()
        if screen is not None and screen.on_key(keyval, name, ctrl):
            # Typing is interaction: hold the rate so a held key repeats
            # smoothly rather than one frame at a time.
            self.frames.note_interaction()
            return True
        shell = self.shell
        if shell is None:
            # Every binding below asks the shell to do something, and there is
            # no shell yet. Nothing here is worth deferring: a person pressing
            # F5 during boot wants the settings screen when it exists, not a
            # jump to it the moment it appears.
            return False
        if name == "Escape":
            if shell.dismiss_exit_menu():
                self.frames.request_frame()
                return True
            shell.go_back()
            return True
        if name in ("F1", "Home"):
            # Also the way back when a program has taken the screen.
            if shell.handed_over_to:
                shell.reclaim()
            shell.go("main")
            return True
        if name == "F2":
            shell.go("programs")
            return True
        if name == "F3":
            shell.go("browser")
            return True
        if name == "F4":
            shell.go("engineering")
            return True
        if name == "F5":
            shell.go("settings")
            return True
        if name == "F6":
            shell.go("tasks")
            return True
        if name == "F7":
            shell.go("terminal")
            return True
        if name == "F8":
            shell.go("claude")
            return True
        if name == "F9":
            shell.go("comms")
            return True
        if name in ("Print", "Sys_Req") or (
                ctrl and name in ("s", "S")
                and state & Gdk.ModifierType.SHIFT_MASK):
            # Ctrl+Shift+S as well, for keyboards — most laptops — whose
            # Print key is a function-layer chord or missing entirely.
            shell.take_screenshot()
            return True
        if name == "F11":
            # With a program in the frame, F11 is about that program, not us.
            if shell.framed_window is not None:
                shell.break_out()
                return True
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.fullscreen()
            return True
        if name == "F12":
            shell.toggle_listening()
            return True
        if name in ("q", "Q") and state & Gdk.ModifierType.CONTROL_MASK:
            shell.quit()
            return True
        return False


class EarthApplication(Gtk.Application):
    def __init__(self, config: Optional[Config] = None):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        # The shell is *not* built here. Constructing it took every
        # millisecond before a window could exist, which meant the machine
        # showed whatever was underneath — a console, a grey root window — for
        # the whole of CDOS's startup. It is built in stages below, inside a
        # window that is already on screen showing the frame.
        self._config = config
        self.shell: Optional["Shell"] = None
        self.window: Optional[EarthWindow] = None
        self._boot_steps: list = []

    # ------------------------------------------------------------------
    def do_activate(self) -> None:
        # Configuration first, and synchronously: it is one small file and
        # under a millisecond, and it decides the window's shape. Opening
        # fullscreen and then discovering the machine wanted a window would
        # resize the frame in front of the person, which is a worse cut than
        # the one this whole screen exists to remove.
        self._config = self._config or Config.load()

        self.window = EarthWindow(self, self._config)
        self.window.present()

        # Each step runs on its own idle turn so the frame repaints between
        # them. Doing the work in one callback would freeze the animation for
        # its duration, which looks exactly like the hang it is meant to rule
        # out.
        self._boot_steps = [
            ("selecting the platform adapter", self._step_shell),
            ("building the interface", self._step_screens),
            ("starting voice", self._step_voice),
            ("ready", self._step_attach),
        ]
        GLib.idle_add(self._boot)

    def _boot(self) -> bool:
        """Run one boot step per turn, then start drawing the shell."""
        if not self._boot_steps:
            return False
        message, step = self._boot_steps.pop(0)
        if self.window is not None:
            self.window.say(message)
        try:
            step()
        except Exception as exc:
            # A boot that fails should say so on the screen the person is
            # already looking at, not only on a console nobody can see. The
            # window stays up showing the frame and the reason, which is a
            # better failure than a machine that shows nothing.
            if self.window is not None:
                self.window.say(f"failed: {type(exc).__name__}: {exc}"[:90])
            raise
        GLib.idle_add(self._boot)
        return False

    def _step_shell(self) -> None:
        from .core import Shell         # deferred: see the module docstring
        self.shell = Shell(self._config)

    def _step_screens(self) -> None:
        from .screens import ALL_SCREENS
        shell = self.shell
        for screen_class in ALL_SCREENS:
            shell.register_screen(screen_class(shell))
        shell.current = str(shell.config.get("shell", "start_screen", "main"))
        if shell.current not in shell.screens:
            shell.current = "main"

    def _step_voice(self) -> None:
        shell = self.shell
        if shell.config.get("voice", "enabled", True):
            from .voice import VoiceEngine
            shell.voice = VoiceEngine(shell)

    def _step_attach(self) -> None:
        self._attach_shell(self.shell)

    def _attach_shell(self, shell: "Shell") -> None:
        """Start drawing the shell in the window that is already open."""
        shell.redraw_hook = self._redraw
        shell.quit_hook = self._quit
        # The built-in agent's bridge answers on a socket thread; screens and
        # pending choices belong to this one, and this is how work crosses.
        shell.call_on_main = lambda fn: GLib.idle_add(lambda: (fn(), False)[1])
        shell.restart_hook = self._restart
        shell.unfullscreen_hook = self._step_aside
        shell.fullscreen_hook = self._reclaim

        # No second window, no present, no destroy: the frame on screen is
        # already this window's, and from here it has something to put in it.
        self.window.attach(shell)

        # Claim the output before anything else opens beside us, then again
        # shortly after, because our own window may not exist in the
        # compositor's tree on the very first call.
        GLib.timeout_add(300, lambda: (shell.claim_screen(), False)[1])
        GLib.timeout_add(1500, lambda: (shell.claim_screen(), False)[1])

        greeting = str(shell.config.get("persona", "greeting", ""))
        if greeting:
            GLib.timeout_add(600, lambda: (shell.say(greeting), False)[1])

    def _redraw(self) -> None:
        """What ``Shell.redraw()`` reaches: a request, not a repaint.

        Thread-safe, because the scheduler is: a worker calling
        ``shell.redraw()`` sets the flag and the timers are touched on the
        main loop.
        """
        if self.window is not None:
            self.window.frames.request_frame()

    def _step_aside(self) -> None:
        """Drop out of fullscreen so another program can be seen."""
        window = self.window
        if window is None:
            return

        def apply() -> None:
            if window.is_fullscreen():
                window.unfullscreen()
            window.minimize()
            return False

        GLib.idle_add(apply)

    def _reclaim(self) -> None:
        """Come back to the front, fullscreen if that is how we were told to run."""
        window = self.window
        if window is None:
            return

        def apply() -> None:
            window.present()
            if self.shell.config.get("shell", "fullscreen", True):
                window.fullscreen()
            return False

        GLib.idle_add(apply)

    def _release(self) -> None:
        # Timers first: a restart replaces this process image, and a frame
        # timer that outlives the shell it was drawing is how a "runaway
        # timer" happens. Both of these are idempotent.
        window = self.window
        if window is not None:
            window.jobs.stop()
            window.frames.stop()
        if self.shell.voice is not None:
            self.shell.voice.shutdown()
        agent = getattr(self.shell, "agent", None)
        if agent is not None:
            agent.shutdown()
        self.shell.sound.stop()

    def _quit(self) -> None:
        self._release()
        self.quit()

    def _restart(self) -> None:
        """Replace this process with a fresh one running the code on disk.

        How an accepted change from the built-in agent takes effect. Exactly
        the command line this process was started with, so the installed
        launcher, ``python3 -m earth_os --windowed`` and the session script
        all come back as themselves. Not a second window and not a destroy:
        the process image is replaced and the new one boots its own frame.
        """
        import os
        self._release()
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, list(sys.orig_argv))


def main(argv: Optional[list] = None) -> int:
    """Kept so older launcher scripts importing ``app.main`` still work.

    The real entry point is :mod:`earth_os.cli`, which can parse arguments
    and run ``--doctor`` without importing GTK.
    """
    from .cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
