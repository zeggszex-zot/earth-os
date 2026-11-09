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

from typing import Optional, Tuple

from .. import brand
from ..chrome import Frame, clock_label
from ..chrome import pieces as chrome_pieces
from ..widgets import (
    Element, FlatButton, Group, Label, RenderContext, StandardButton,
    draw_text,
)

__all__ = ["Frame", "Screen"]


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

    # -- lifecycle ---------------------------------------------------------
    def ensure_built(self, ctx: RenderContext) -> None:
        key = (round(ctx.width), round(ctx.height))
        # The programs strip is state, not geometry, and it is on every
        # screen, so every screen rebuilds when the window list changes.
        signature = self.strip_signature()
        if key != self._built_for or signature != self._strip_built:
            self.g.clear()
            self.frame = Frame(ctx)
            self._strip_built = signature
            self.build(ctx, self.frame)
            self._built_for = key

    def build(self, ctx: RenderContext, f: Frame) -> None:  # pragma: no cover
        """Populate ``self.g``.  Called on first paint and on every resize."""

    def invalidate(self) -> None:
        self._built_for = (0.0, 0.0)

    def on_enter(self) -> None:
        pass

    def on_leave(self) -> None:
        pass

    def tick(self) -> None:
        pass

    # -- painting ----------------------------------------------------------
    def draw(self, cr, ctx: RenderContext) -> None:
        self.ensure_built(ctx)
        for element in self.g:
            if element.visible:
                element.draw(cr, ctx)
        self.draw_overlay(cr, ctx)
        self.draw_toast(cr, ctx)

    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        pass

    def draw_toast(self, cr, ctx: RenderContext) -> None:
        """The shell's short message, over whichever screen is up.

        Drawn last and above everything, along the bottom bar, because it is
        the answer to a press and has to be where the person is looking — not
        in the main screen's transcript, which may be two screens away.
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
        y = ctx.height - u(0.7) - u(3.0) - h - u(0.6)
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
        if element:
            element.pressed = True
        return element

    def on_release(self, x: float, y: float, pressed: Optional[Element]) -> None:
        if pressed:
            pressed.pressed = False
            if pressed.contains(x, y):
                self.app.play("beep")
                pressed.click()

    def on_motion(self, x: float, y: float) -> bool:
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
        self.add_program_strip(ctx, f)

        bl_y = f.H - f.margin - f.bottom_h
        if back and self.name != "main":
            # Sits clear above the bottom elbow's vertical arm.
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
