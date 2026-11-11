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
        self._frame_source: Optional[int] = None

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

        self._start_frame_clock(BOOT_FPS)

    # ------------------------------------------------------------------
    def say(self, message: str) -> None:
        """Set the boot diagnostic line. Ignored once the shell is up."""
        self.message = message
        self.area.queue_draw()

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

        self._start_frame_clock(
            max(int(shell.config.get("shell", "fps", 30)), 5))
        self.area.queue_draw()

    def _start_frame_clock(self, fps: int) -> None:
        """Run the redraw timer at *fps*, replacing any previous one."""
        if self._frame_source is not None:
            GLib.source_remove(self._frame_source)
        self._frame_source = GLib.timeout_add(
            int(1000 / max(fps, 1)), self._on_frame)

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

        phosphor.composite(cr, width, height, paint,
                           background=shell.palette.background, enabled=tube)

    def _on_frame(self) -> bool:
        now = time.monotonic()
        if self.shell is None:
            # Still coming up: repaint so the sweep moves, and nothing else.
            # A boot screen that polls the machine is competing with the boot.
            self.area.queue_draw()
            return GLib.SOURCE_CONTINUE
        screen = self.shell.screen()
        if screen is not None:
            screen.tick()
        self._publish_frame_rect()
        # Watch for new windows here rather than in a screen's tick: a program
        # opened while the PROGRAM INDEX is up has to be framed just the same.
        if self.shell.poll_due:
            self.shell.poll_windows()
            # GODCORE is for the AI, not for a screen, so nothing on screen
            # is there to keep its reading warm. Throttled to six seconds
            # inside, and the read itself runs on a worker thread.
            self.shell.refresh_cdos()
        self.shell.notice_screenshots()
        # Animation and live readings both want a steady redraw.
        self.area.queue_draw()
        self._last_tick = now
        return GLib.SOURCE_CONTINUE

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
            self.area.queue_draw()

    def _on_released(self, gesture, n_press, x, y) -> None:
        screen = self._screen()
        if screen is not None:
            screen.on_release(x, y, self._pressed)
        self._pressed = None
        self.area.queue_draw()

    def _on_motion(self, controller, x, y) -> None:
        self._pointer = (x, y)
        screen = self._screen()
        if screen is not None and screen.on_motion(x, y):
            self.area.queue_draw()

    def _on_scroll(self, controller, dx, dy) -> bool:
        # GTK4 scroll events carry no coordinates, so use the last position
        # the motion controller saw.
        screen = self._screen()
        if screen is None:
            return False
        x, y = self._pointer
        if screen.on_scroll(x, y, dy):
            self.area.queue_draw()
            return True
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
            self.area.queue_draw()
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
                self.area.queue_draw()
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
        if self.window is not None:
            GLib.idle_add(self.window.area.queue_draw)

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
