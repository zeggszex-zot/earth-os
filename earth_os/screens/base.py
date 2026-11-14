"""Screen base class.

Every screen is rebuilt from scratch whenever the window size changes, so all
geometry is expressed as a function of the current width, height and base unit.
That is what makes the port resolution-independent where the original was
pinned to a fixed form size.

``Frame`` and the shared chrome pieces used to be defined here and now live in
:mod:`earth_os.chrome`, because the boot splash draws the same frame before
any screen exists. ``Frame`` is re-exported so ``from .base import Frame,
Screen`` keeps working; it is one frame either way.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

from .. import brand
from ..chrome import Frame, clock_label
from ..chrome import pieces as chrome_pieces
from ..widgets import (
    Element, FlatButton, Group, HalfPillButton, Label, RenderContext, Slider,
    StandardButton, Tile, draw_text, speaker_icon,
)

__all__ = ["Frame", "Screen"]

#: Element names that belong to the volume control, which a press may land on
#: without closing the slider.
VOLUME_PARTS = ("volume_",)

#: The elements that make up a confirmation strip: prompt and buttons. A press
#: on any of these is the menu being used; a press anywhere else is the
#: person leaving it.
MENU_PARTS = ("choice_", "claude_choice_", "confirm_")


class Screen:
    """A named page of the shell."""

    name = "screen"
    title = brand.NAME

    def __init__(self, app):
        self.app = app
        self.g = Group()
        self._built_for: Tuple[float, float] = (0.0, 0.0)
        self._strip_built: Optional[Tuple] = None
        self.frame: Optional[Frame] = None
        #: The slider a press started on, followed until release.
        self._dragging: Optional[Slider] = None

    # -- lifecycle ---------------------------------------------------------
    def ensure_built(self, ctx: RenderContext) -> None:
        key = (round(ctx.width), round(ctx.height))
        # The programs strip is state, not geometry, and it is on every
        # screen, so every screen rebuilds when the window list changes.
        signature = self.chrome_signature()
        if key != self._built_for or signature != self._strip_built:
            self.g.clear()
            self.frame = Frame(ctx)
            self._strip_built = signature
            self.build(ctx, self.frame)
            self._built_for = key

    def build(self, ctx: RenderContext, f: Frame) -> None:  # pragma: no cover
        """Populate ``self.g``.  Called on first paint and on every resize."""

    def invalidate(self) -> None:
        """Throw away the built geometry; it is rebuilt on the next paint.

        And ask for that paint. Invalidating *means* the picture on screen is
        out of date, so the two always belonged together — it only worked
        without the request because a clock was redrawing regardless.

        It matters most for the call sites inside ``draw_overlay``, which
        notice a changed option list part-way through a frame whose geometry
        was already built. That frame shows the old layout; the rebuilt one
        appears on the next, so without a request here a confirmation strip
        would be a frame behind and, with nothing else asking, would stay
        there. The conditions guarding those call sites go false once
        ``build`` records what it drew, so this settles after one extra frame
        rather than looping.
        """
        self._built_for = (0.0, 0.0)
        app = getattr(self, "app", None)
        redraw = getattr(app, "redraw", None)
        if callable(redraw):
            redraw()

    def on_enter(self) -> None:
        pass

    def on_leave(self) -> None:
        pass

    def tick(self) -> None:
        pass

    #: Seconds between frames for a screen whose picture changes once a
    #: second on its own — a running clock, a hardware gauge. Screens set
    #: :attr:`live` rather than overriding :meth:`next_frame_in`, so the
    #: common case stays one word.
    SECOND = 1.0

    #: True on screens that show a clock ticking seconds or a live reading,
    #: and so need a frame a second even when nobody is touching them.
    live = False

    def next_frame_in(self) -> Optional[float]:
        """How long until this screen's picture changes by itself.

        Seconds, or ``None`` for "nothing here is moving". This is the whole
        of what the frame scheduler knows about animation, and the reason a
        static screen costs nothing: :class:`earth_os.scheduler.FrameScheduler`
        asks every screen this, and a ``None`` from the screen on display is
        what lets it take its timer down and leave the process asleep until
        something wakes it.

        The base class answers for the two things every screen can have
        moving: the shell's toast, which expires, and an element mid-flash
        after a press. Subclasses add what is theirs — a caret, a transcript
        being typed out — and should take the minimum of their own answer
        and ``super().next_frame_in()`` rather than replacing it, or a flash
        on their screen will stick.
        """
        now = time.monotonic()
        deadlines = []

        toast_until = getattr(self.app, "toast_until", 0.0)
        if toast_until > now:
            # One frame after it expires, to draw the screen without it.
            deadlines.append(toast_until - now)

        for element in self.g.items:
            flash = getattr(element, "flash_until", 0.0)
            if flash > now:
                deadlines.append(flash - now)

        if self.live:
            # Align to the next whole second rather than one second from
            # now, so a clock showing seconds changes when the second does
            # instead of drifting a fraction later each time.
            deadlines.append(max(self.SECOND - (time.time() % self.SECOND),
                                 0.0))

        return min(deadlines) if deadlines else None

    # -- painting ----------------------------------------------------------
    def draw(self, cr, ctx: RenderContext) -> None:
        self.ensure_built(ctx)
        reading = self.g.get("volume_read")
        if reading is not None:
            reading.label = self.app.volume.label()  # type: ignore[attr-defined]
        for element in self.g:
            if element.visible:
                element.draw(cr, ctx)
        self.draw_overlay(cr, ctx)
        self.draw_toast(cr, ctx)

    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        pass

    def draw_toast(self, cr, ctx: RenderContext) -> None:
        """The shell's short message, over whichever screen is up.

        Drawn last and above everything, near the foot of the screen, because
        it is the answer to a press and has to be where the person is looking
        — not in the main screen's transcript, which may be two screens away.
        """
        app = self.app
        text = getattr(app, "toast_text", "")
        if not text or ctx.now >= getattr(app, "toast_until", 0.0):
            return
        from ..widgets import shapes
        u = ctx.u
        h = u(2.2)
        size = h * 0.42
        from ..widgets import text_size
        tw, _ = text_size(cr, ctx, text, size)
        w = min(tw + h * 1.6, ctx.width * 0.8)
        x = (ctx.width - w) / 2
        y = ctx.height - u(0.7) - h - u(0.6)
        shapes.pill(cr, x, y, w, h, ends="both")
        shapes.fill(cr, ctx.color("accent4"))
        draw_text(cr, ctx, text, x + w / 2, y + h / 2, size,
                  ctx.palette.background, align="center", valign="middle",
                  max_width=w - h * 0.8)

    # -- input -------------------------------------------------------------
    def hit(self, x: float, y: float) -> Optional[Element]:
        return self.g.hit(x, y)

    def on_press(self, x: float, y: float) -> Optional[Element]:
        element = self.hit(x, y)
        # The volume slider is a popover: a press anywhere else closes it,
        # and only closes it, so reaching for it and missing cannot also
        # press whatever was underneath.
        volume = self._volume()
        if volume is not None and volume.panel_open and not (
                element is not None and element.name.startswith(VOLUME_PARTS)):
            volume.close_panel()
            return None
        # The exit menu is the same: a press that is not on it closes it, and
        # only closes it, so the machine is never shut down by a stray press.
        if getattr(self.app, "exit_menu_open", False) and not (
                element is not None and element.name.startswith(MENU_PARTS)):
            self.app.dismiss_exit_menu()
            return None
        if isinstance(element, Slider):
            self._dragging = element
            element.drag(x, final=False)
        if element:
            element.pressed = True
        return element

    def on_release(self, x: float, y: float, pressed: Optional[Element]) -> None:
        if isinstance(pressed, Slider):
            pressed.pressed = False
            pressed.drag(x, final=True)
            self._dragging = None
            return
        if pressed:
            pressed.pressed = False
            if pressed.contains(x, y):
                self.app.play("beep")
                pressed.click()

    def on_motion(self, x: float, y: float) -> bool:
        if self._dragging is not None:
            self._dragging.drag(x, final=False)
            return True
        changed = False
        for element in self.g.items:
            hovered = element.interactive and element.enabled and element.contains(x, y)
            if hovered != element.hovered:
                element.hovered = hovered
                changed = True
        return changed

    def on_scroll(self, x: float, y: float, dy: float) -> bool:
        return False

    def on_key(self, keyval: int, name: str, ctrl: bool = False) -> bool:
        """A key press while this screen is up.

        ``ctrl`` is whether Control was held; the terminal needs it to tell
        ``Ctrl+C`` from a typed ``c``. Return True to consume the key, False
        to let the application's own shortcuts see it.
        """
        return False

    # -- helpers used by subclasses ---------------------------------------
    def add_chrome(self, ctx: RenderContext, f: Frame, *,
                   title: str = "", subtitle: str = "",
                   back: bool = True) -> None:
        """Add the elbow frame, title bar and status strip common to screens.

        The shared pieces come from :func:`earth_os.chrome.pieces`, which the
        boot splash also draws. Built here instead, they would be a second copy
        of the frame, and the two would drift — they already had, which is how
        boot came to look like the frame twitching at handover.
        """
        u = ctx.u

        for piece in chrome_pieces(ctx, f, title=title, subtitle=subtitle):
            self.g.add(piece)

        # The running-programs strip, directly under the title bar on every
        # screen. It is chrome, not a panel: it is there whether or not
        # anything is running, so the way back to a program is always in the
        # same place instead of appearing and disappearing.
        #
        # Not in the shared pieces: it needs the application, and the splash
        # is on screen precisely because there is not one yet.
        #
        # While the volume slider is open it takes the strip's band, which is
        # the one place on every screen that is free of content and wide
        # enough to give a finger a long track.
        volume = self._volume()
        if volume is not None and volume.panel_open:
            self.add_volume_panel(ctx, f)
        else:
            self.add_program_strip(ctx, f)
        self.add_box_controls(ctx, f)

        bl_y = f.bottom + f.gap
        if back and self.name != "main":
            # At the foot of the side column, which is the foot of the screen.
            self.g.add(StandardButton(
                "chrome_back",
                (f.margin + u(0.3), bl_y - f.gap - u(2.0),
                 f.col_w - u(0.6), u(2.0)),
                label=ctx.strings.get("myBack", "BACK"), color="accent4",
                on_click=self.app.go_back,
            ))
            self.g.add(StandardButton(
                "chrome_home",
                (f.margin + u(0.3), bl_y - f.gap * 2 - u(4.0),
                 f.col_w - u(0.6), u(2.0)),
                label=ctx.strings.get("myHome", "HOME"), color="accent2",
                on_click=lambda: self.app.go("main"),
            ))
            # On every screen, so whatever is up can be shown to someone
            # without first navigating away from it.
            self.g.add(StandardButton(
                "chrome_screenshot",
                (f.margin + u(0.3), bl_y - f.gap * 3 - u(6.0),
                 f.col_w - u(0.6), u(2.0)),
                label=ctx.strings.get("myScreenshot", "SCREEN SHOT"),
                color="accent5", tooltip="save a picture of the screen (Print)",
                on_click=lambda: self.app.take_screenshot(),
            ))

    # -- the upper-left box -------------------------------------------------
    def _volume(self):
        return getattr(self.app, "volume", None)

    def add_box_controls(self, ctx: RenderContext, f: Frame) -> None:
        """Put the box's controls on its spots.

        One so far: system volume, on spot 0. The other three stay frame
        until something earns a place there. Each control is drawn over the
        chrome spot it replaces, in the same shape, so the box keeps its
        outline whether a spot is filled or not.
        """
        volume = self._volume()
        if volume is None:
            return
        volume.ensure_read()
        spot = f.spots[0]
        t = spot[2]
        self.g.add(Tile(
            "volume_spot", spot, radii=(t * 0.72, 0, 0, 0),
            color="accent4" if volume.panel_open else "accent2",
            icon=speaker_icon(lambda: volume.level),
            caption=volume.label,
            tooltip="system volume",
            on_click=volume.toggle_panel, z=3,
        ))

    def add_volume_panel(self, ctx: RenderContext, f: Frame) -> None:
        """The volume slider, across the band under the title bar.

        A cap naming it, a step down, the track, a step up and the reading.
        Steps are there because a track this long is precise but not
        fine-grained under a finger; five percent at a time is.
        """
        volume = self._volume()
        u = ctx.u
        x, y, w, h = f.strip
        row_h = min(h * 0.82, u(2.2))
        row_y = y + (h - row_h) / 2
        gap = u(0.3)
        cap_w = u(6.6)
        step_w = u(3.4)
        read_w = u(5.2)
        z = 6

        self.g.add(HalfPillButton(
            "volume_cap", (x, row_y, cap_w, row_h), side="left",
            label="VOLUME", label_align="right", color="accent4",
            enabled=False, z=z,
        ))
        cursor = x + cap_w + gap
        self.g.add(StandardButton(
            "volume_down", (cursor, row_y, step_w, row_h), label="-",
            label_align="center", color="accent2", z=z,
            tooltip="quieter", on_click=lambda: volume.nudge(-1),
        ))
        cursor += step_w + gap
        track_w = w - (cursor - x) - gap * 2 - step_w - read_w
        self.g.add(Slider(
            "volume_slider", (cursor, row_y, track_w, row_h),
            provider=lambda: volume.level, color="accent2", z=z,
            on_change=lambda level, final: volume.set(level),
            # Why it cannot be read, on the track itself, rather than a
            # silent track that does nothing.
            note=lambda: volume.error,
        ))
        cursor += track_w + gap
        self.g.add(StandardButton(
            "volume_up", (cursor, row_y, step_w, row_h), label="+",
            label_align="center", color="accent2", z=z,
            tooltip="louder", on_click=lambda: volume.nudge(1),
        ))
        cursor += step_w + gap
        self.g.add(HalfPillButton(
            "volume_read", (cursor, row_y, x + w - cursor, row_h),
            side="right", label=volume.label(), label_align="center",
            color="accent4", enabled=False, z=z,
        ))

    def chrome_signature(self) -> Tuple:
        """Everything the chrome is built from that is state, not geometry."""
        volume = self._volume()
        return (self.strip_signature(),
                bool(volume is not None and volume.panel_open))

    # -- the running-programs strip ---------------------------------------
    #: accent4 is deliberately absent: it is what marks the framed program,
    #: and a highlight that also turns up in the rotation is not a highlight.
    STRIP_COLORS = ("accent2", "accent3", "accent5", "accent6")

    def add_program_strip(self, ctx: RenderContext, f: Frame) -> None:
        """One button per running program, along the top of every screen.

        Always present, even with nothing running — a control that moves or
        vanishes is one you have to look for. Pressing a program puts it in
        the frame and goes to the main screen, which is where the frame is.
        """
        u = ctx.u
        app = self.app
        x, y, w, h = f.strip
        gap = u(0.3)
        manager = getattr(app, "windows", None)
        windows = list(getattr(manager, "windows", []) or [])
        available = bool(getattr(manager, "available", False))

        head_h = u(0.78)
        row_y = y + head_h + u(0.12)
        row_h = max(h - head_h - u(0.12), u(1.0))

        # Left of the header line: what the strip is. Right of it: the keys
        # that work from inside a program, where a click cannot reach.
        self.g.add(Label(
            "strip_label", (x, y, w * 0.5, head_h),
            text=f"{ctx.strings.get('myTasks', 'RUNNING PROGRAMS')}"
                 f"   ·   {len(windows)}",
            size=u(0.62), color="accent4", align="left", valign="middle",
        ))
        keys = f"SUPER+F1 {ctx.strings['myReclaim']}"
        if app.frame_mode:
            keys = "F11 FULL SCREEN   ·   " + keys
        self.g.add(Label(
            "strip_keys", (x + w * 0.5, y, w * 0.5, head_h),
            text=keys, size=u(0.58), color="accent3",
            align="right", valign="middle",
        ))

        # Room for the break-out control, but only when there is something to
        # break out of.
        control_w = u(7.6) if app.framed_window is not None else 0.0
        avail = w - (control_w + gap if control_w else 0.0)

        if not windows:
            note = ("NO PROGRAMS RUNNING" if available
                    else "WINDOW CONTROL UNAVAILABLE")
            self.g.add(FlatButton(
                "strip_empty", (x, row_y, avail, row_h),
                color="accent3" if available else "accent6",
                label=note, label_align="center", enabled=False,
            ))
        else:
            limit = max(int(app.config.get("windows", "strip_limit", 8)), 1)
            shown = windows[:limit]
            bw = min((avail - gap * (len(shown) - 1)) / len(shown), u(14))
            for i, window in enumerate(shown):
                framed = (app.framed_window is not None
                          and app.framed_window.handle == window.handle)
                colour = ("accent4" if framed
                          else self.STRIP_COLORS[i % len(self.STRIP_COLORS)])
                self.g.add(StandardButton(
                    f"strip_{i}", (x + i * (bw + gap), row_y, bw, row_h),
                    label=window.app or window.title, label_align="center",
                    color=colour, tooltip=window.title,
                    on_click=(lambda win=window: self.app.focus_program(win)),
                ))

        if control_w:
            self.g.add(StandardButton(
                "strip_breakout", (x + w - control_w, row_y, control_w, row_h),
                label="FULL SCREEN", label_align="center", color="accent5",
                on_click=self.app.break_out,
            ))

    def strip_signature(self) -> Tuple:
        """What the strip is drawn from, so a change can force a rebuild."""
        app = self.app
        manager = getattr(app, "windows", None)
        windows = list(getattr(manager, "windows", []) or [])
        framed = (app.framed_window.handle
                  if getattr(app, "framed_window", None) is not None else None)
        return (tuple((w.handle, w.app or w.title) for w in windows), framed,
                bool(getattr(manager, "available", False)))

    def draw_clock(self, cr, ctx: RenderContext) -> None:
        clock = self.g.get("chrome_clock")
        if clock is not None:
            clock.label = clock_label()  # type: ignore[attr-defined]
