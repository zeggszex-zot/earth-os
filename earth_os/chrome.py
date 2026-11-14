"""The LCARS frame: its proportions, and the pieces every screen shares.

This used to live in :mod:`earth_os.screens.base`, which was the natural
home for it right up until something other than a screen needed it. The boot
splash draws the frame before any screen exists, and reaching into
``screens.base`` for it meant importing the package ``__init__`` — and with it
every screen, the window manager and the platform adapters: 72 ms of imports
to draw an empty rectangle, in the one place where imports are the thing being
covered up.

So the frame moved down to where both callers can reach it. Geometry over a
render context is not a screen; it only lived beside them because screens were
the only thing that had ever asked.

The second reason matters more than the milliseconds. :func:`pieces` is the
*only* description of the shared chrome. The splash and
:meth:`~earth_os.screens.base.Screen.add_chrome` both call it, so the frame
cannot drift between boot and shell — when they were two copies it already
had, in two places: the bottom bar's right cap and the clock pill beside it.
Handover looked like the frame twitching, which is precisely the seam the
splash is supposed to remove.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from . import brand
from .widgets import (
    Elbow, Element, FlatButton, HalfPillButton, Label, RenderContext, Tile,
    draw_text,
)


class Frame:
    """Standard proportions of the LCARS chrome for a given canvas size."""

    def __init__(self, ctx: RenderContext):
        self.ctx = ctx
        u = ctx.u
        self.W = ctx.width
        self.H = ctx.height
        self.margin = u(0.7)
        self.col_w = max(u(6.4), min(self.W * 0.19, u(9.0)))
        self.bar_h = u(1.5)
        self.gap = u(0.32)
        #: Band under the title bar holding the running-programs strip. It is
        #: part of the top chrome on every screen, so every screen's content
        #: starts below it and nothing has to know the strip is there.
        self.strip_h = u(2.7)
        self.top_h = u(3.4) + self.strip_h
        #: There is no bottom chrome any more: the side column and the
        #: content run to the foot of the screen. Kept as an attribute, at
        #: zero, so geometry written against it stays correct.
        self.bottom_h = 0.0
        self.elbow_w = self.col_w + u(3.2)
        #: The main screen's right-hand status rail, whose foot is the clock.
        self.rail_w = u(9.0)

    @property
    def box(self) -> Tuple[float, float, float, float]:
        """The square at the head of the side column: four spots for icons.

        As wide as the column and exactly as tall, whatever that does to the
        column below. It used to be whatever height the title band left it,
        which made it a slab rather than a place to put things.
        """
        return (self.margin, self.margin, self.col_w, self.col_w)

    @property
    def spots(self) -> List[Tuple[float, float, float, float]]:
        """The box's four spots, in reading order.

        Spot 0 carries the frame's swept corner; spot 1 is the foot of the
        elbow arm that runs on into the title bar.
        """
        x, y, side, _ = self.box
        t = (side - self.gap) / 2
        far = t + self.gap
        return [(x, y, t, t), (x + far, y, t, t),
                (x, y + far, t, t), (x + far, y + far, t, t)]

    @property
    def column_top(self) -> float:
        """Where the side column's own controls may start, below the box."""
        return self.margin + self.col_w + self.gap * 2

    @property
    def strip(self) -> Tuple[float, float, float, float]:
        """Where the running-programs strip lives, in canvas coordinates."""
        x = self.margin + self.elbow_w + self.gap
        y = self.margin + self.bar_h + self.gap
        return (x, y, max(self.W - x - self.margin, self.ctx.u(2)), self.strip_h)

    @property
    def content(self) -> Tuple[float, float, float, float]:
        """The free rectangle inside the chrome."""
        x = self.margin + self.col_w + self.gap * 2
        y = self.margin + self.top_h + self.gap
        w = self.W - x - self.margin
        h = self.H - y - self.bottom_h - self.margin
        return (x, y, w, h)

    @property
    def bottom(self) -> float:
        """The lowest y anything inside the frame may reach."""
        return self.H - self.margin - self.bottom_h

    @property
    def clock(self) -> Tuple[float, float, float, float]:
        """The clock block: the foot of the main screen's rail.

        A rectangle the width of the rail, running down to the bottom of the
        frame. It replaced a small pill at the end of the bottom bar, which
        went when the bar did.
        """
        u = self.ctx.u
        h = u(4.2)
        x = self.W - self.margin - self.rail_w
        return (x, self.bottom - h, self.rail_w, h)


def pieces(ctx: RenderContext, f: Frame, *, title: str = "",
           subtitle: str = "") -> List[Element]:
    """The chrome every screen has, and the splash draws while waiting.

    Returned rather than added to anything, because the two callers want
    different things done with it: a screen puts these in its element group so
    they can be hit-tested, and the splash simply paints them. What must not
    differ is the geometry, so only this function knows it.

    Deliberately excluded: the running-programs strip, the back and home
    buttons, and anything else that needs the application. Those are a
    screen's business. This is the part that is true before there is a
    program, a history or a machine to ask.
    """
    u = ctx.u
    out: List[Element] = []

    # The upper-left box: a square of four spots. Spot 0 takes the frame's
    # swept corner; spot 1 is the foot of the elbow whose arm becomes the
    # title bar. Empty spots are plain frame; a screen puts controls on them
    # (Screen.add_box_controls) because a control needs the application and
    # the splash is drawn before there is one.
    spots = f.spots
    t = spots[0][2]
    out.append(Tile(
        "chrome_spot_0", spots[0], radii=(t * 0.72, 0, 0, 0), color="frame",
    ))
    sx, sy, _, _ = spots[1]
    out.append(Elbow(
        "chrome_tl",
        (sx, sy, f.margin + f.elbow_w - sx, t),
        corner="tl", bar_w=t, bar_h=f.bar_h, color="frame", outer_radius=0,
    ))
    for i in (2, 3):
        out.append(Tile(f"chrome_spot_{i}", spots[i], color="frame"))
    # Title bar continuing to the right edge.
    title_x = f.margin + f.elbow_w + f.gap
    out.append(FlatButton(
        "chrome_titlebar",
        (title_x, f.margin, max(f.W - title_x - f.margin - u(4.2), u(2)),
         f.bar_h),
        color="accent3", label="", enabled=False,
    ))
    out.append(HalfPillButton(
        "chrome_titlecap",
        (f.W - f.margin - u(4.0), f.margin, u(4.0), f.bar_h),
        side="right", color="accent4",
        label=subtitle or brand.cap(),
        label_align="center", enabled=False,
    ))
    if title:
        out.append(Label(
            "chrome_title",
            (title_x + u(0.6), f.margin + f.bar_h * 0.1, f.W * 0.6,
             f.bar_h * 0.8),
            text=title, size=f.bar_h * 0.72, color="accent5",
            align="left", valign="top", z=2,
        ))

    # Nothing along the bottom. The elbow, the run of unlabelled bars and
    # the clock pill that used to close the frame there did nothing, and
    # took a band of every screen to do it; the side column and the content
    # now run to the foot of the screen. The clock lives at the foot of the
    # main screen's rail (see ClockBlock).
    return out


class ClockBlock(FlatButton):
    """The clock: a rectangle with the time large and the date above it.

    ``label`` is :func:`clock_label`'s two lines, date then time, so anything
    that sets the label keeps working; this only decides how they are drawn.
    """

    def __init__(self, name: str = "chrome_clock", rect=(0, 0, 0, 0), **kw):
        kw.setdefault("color", "accent4")
        kw.setdefault("enabled", False)
        super().__init__(name, rect, **kw)

    def _draw_label(self, cr, ctx: RenderContext, inset: float = 0.0) -> None:
        text = self.display_label()
        if not text:
            return
        date, _, clock = text.partition("\n")
        x, y, w, h = self.rect
        ink = self._label_color(ctx)
        pad = min(w, h) * 0.12
        # Text height is font size, not width, so a narrow block caps it too.
        time_size = min(h * 0.40, w * 0.17)
        date_size = time_size * 0.48
        cx = x + w / 2
        draw_text(cr, ctx, date, cx, y + h * 0.30, date_size, ink,
                  align="center", valign="middle", max_width=w - pad * 2)
        draw_text(cr, ctx, clock, cx, y + h * 0.66, time_size, ink,
                  align="center", valign="middle", max_width=w - pad * 2)


def clock_label(when: Optional[float] = None) -> str:
    """What goes in the ``chrome_clock`` block: date, newline, time.

    Here rather than in the screen so the splash reads the same clock. A boot
    screen showing a different time format from the shell it becomes is a
    small thing that makes the handover look like two programs.
    """
    return time.strftime("%a %d %b\n%H:%M:%S",
                         time.localtime(when) if when is not None
                         else time.localtime())
