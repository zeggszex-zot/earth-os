"""The LCARS control set.

One class per control in the original ``LCARS.dll``, plus a handful this port
adds for things a 2010 Windows shell had no reason to draw (a conversation
transcript, a live system meter, a status strip).

Original control            -> class here
-------------------------------------------------------------------
StandardButton              -> :class:`StandardButton`   (pill, both ends)
HalfPillButton              -> :class:`HalfPillButton`   (pill, one end)
FlatButton                  -> :class:`FlatButton`       (rectangle)
ComplexButton               -> :class:`ComplexButton`    (block + cap)
TextButton                  -> :class:`TextButton`       (cap + label + rule)
Elbow                       -> :class:`Elbow`
ArrowButton                 -> :class:`ArrowButton`
LevelIndicator              -> :class:`LevelIndicator`
PieButton                   -> :class:`PieButton`
ScrollPanel / ScrollBar     -> :class:`ScrollPanel`
x32TabControl               -> :class:`TabStrip`
Listbox                     -> :class:`ListPanel`
"""

from __future__ import annotations

import math
import time
from typing import Callable, List, Optional, Sequence, Tuple

from . import shapes
from .base import Element, RenderContext, RGB, draw_text, text_size

LABEL_PAD = 0.55      # label inset as a fraction of button height
LABEL_SIZE = 0.52     # label size as a fraction of button height


# --------------------------------------------------------------------------
# Buttons
# --------------------------------------------------------------------------

class _LabelledButton(Element):
    """Shared behaviour: a filled shape with an inset uppercase label."""

    interactive = True

    def __init__(self, name: str = "", rect=(0, 0, 0, 0), *,
                 label: str = "", label_align: str = "right",
                 label_valign: str = "bottom",
                 label_color: Optional[str | RGB] = None,
                 label_size: float = LABEL_SIZE,
                 uppercase: bool = True, **kw):
        super().__init__(name, rect, **kw)
        self.label = label
        self.label_align = label_align
        self.label_valign = label_valign
        self.label_color = label_color
        self.label_size = label_size
        self.uppercase = uppercase

    def display_label(self) -> str:
        return self.label.upper() if self.uppercase else self.label

    def _label_color(self, ctx: RenderContext) -> RGB:
        if self.label_color is not None:
            return ctx.color(self.label_color)
        # Classic LCARS knocks the label out of the fill; a fill too dark for
        # that gets a lit label instead (see Palette.ink_for).
        return ctx.palette.ink_for(self.paint_color(ctx))

    def _draw_label(self, cr, ctx: RenderContext, inset: float = 0.0) -> None:
        text = self.display_label()
        if not text:
            return
        x, y, w, h = self.rect
        pad = h * LABEL_PAD * 0.5 + inset
        size = h * self.label_size
        if self.label_align == "right":
            tx = x + w - pad
        elif self.label_align == "center":
            tx = x + w / 2
        else:
            tx = x + pad
        if self.label_valign == "bottom":
            ty = y + h - h * 0.16
            valign = "bottom"
        elif self.label_valign == "middle":
            ty = y + h / 2
            valign = "middle"
        else:
            ty = y + h * 0.16
            valign = "top"
        draw_text(cr, ctx, text, tx, ty, size, self._label_color(ctx),
                  align=self.label_align, valign=valign,
                  max_width=max(w - pad * 2, 8))


class StandardButton(_LabelledButton):
    """A pill with both ends rounded."""

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        shapes.pill(cr, x, y, w, h, ends="both")
        shapes.fill(cr, self.paint_color(ctx))
        self._draw_label(cr, ctx, inset=h * 0.18)

    def contains(self, px: float, py: float) -> bool:
        x, y, w, h = self.rect
        if not (x <= px <= x + w and y <= py <= y + h):
            return False
        r = h / 2
        if x + r <= px <= x + w - r:
            return True
        cx = x + r if px < x + r else x + w - r
        return (px - cx) ** 2 + (py - y - r) ** 2 <= r * r


class HalfPillButton(_LabelledButton):
    """A pill rounded on one end only; the workhorse of LCARS side columns."""

    def __init__(self, name="", rect=(0, 0, 0, 0), *, side: str = "left", **kw):
        super().__init__(name, rect, **kw)
        self.side = side
        kw.setdefault("label_align", "right" if side == "left" else "left")

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        shapes.pill(cr, x, y, w, h, ends=self.side)
        shapes.fill(cr, self.paint_color(ctx))
        self._draw_label(cr, ctx, inset=h * 0.10 if self.label_align == self.side else 0.0)


class FlatButton(_LabelledButton):
    """A plain rectangle; the original's most common filler control."""

    def __init__(self, name="", rect=(0, 0, 0, 0), *, radius: float = 0.0, **kw):
        super().__init__(name, rect, **kw)
        self.radius = radius

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        shapes.block(cr, x, y, w, h, self.radius)
        shapes.fill(cr, self.paint_color(ctx))
        self._draw_label(cr, ctx)


class ComplexButton(_LabelledButton):
    """A block paired with a detached cap, joined by a gutter."""

    def __init__(self, name="", rect=(0, 0, 0, 0), *, cap_ratio: float = 0.18,
                 gap: float = 0.02, cap_color: Optional[str | RGB] = None, **kw):
        super().__init__(name, rect, **kw)
        self.cap_ratio = cap_ratio
        self.gap = gap
        self.cap_color = cap_color

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        gap = w * self.gap
        cap_w = max(w * self.cap_ratio - gap, 2.0)
        body_x = x + cap_w + gap
        body_w = w - cap_w - gap
        col = self.paint_color(ctx)
        shapes.pill(cr, x, y, cap_w, h, ends="left")
        shapes.fill(cr, ctx.color(self.cap_color) if self.cap_color else col)
        shapes.pill(cr, body_x, y, body_w, h, ends="right")
        shapes.fill(cr, col)
        saved = self.rect
        self.rect = (body_x, y, body_w, h)
        self._draw_label(cr, ctx, inset=h * 0.18)
        self.rect = saved


class TextButton(_LabelledButton):
    """A cap, a label in palette colour, then a rule running to the edge.

    This is the row style used by the original's ENGINEERING / MY COMPUTER /
    INITIATE AUTO DESTRUCT SEQUENCE headers.
    """

    def __init__(self, name="", rect=(0, 0, 0, 0), *,
                 rule: bool = True, cap: bool = True,
                 tail_cap: bool = True, bar_ratio: float = 0.42, **kw):
        kw.setdefault("label_color", "accent4")
        super().__init__(name, rect, **kw)
        self.rule = rule
        self.cap = cap
        self.tail_cap = tail_cap
        self.bar_ratio = bar_ratio

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        col = self.paint_color(ctx)
        cx = x
        cap_w = h * 0.5
        if self.cap:
            shapes.pill(cr, x, y, cap_w, h, ends="left")
            shapes.fill(cr, col)
            cx = x + cap_w + h * 0.22

        size = h * 0.80
        label = self.display_label()
        tw, _ = text_size(cr, ctx, label, size)
        draw_text(cr, ctx, label, cx, y + h / 2, size,
                  ctx.palette.on_background(
                      ctx.color(self.label_color or "accent4")),
                  align="left", valign="middle")
        if self.rule:
            bar_y = y + h * (1 - self.bar_ratio) / 2
            bar_x = cx + tw + h * 0.35
            bar_w = max(x + w - bar_x - (cap_w if self.tail_cap else 0), 0)
            if bar_w > 2:
                shapes.pill(cr, bar_x, bar_y, bar_w + (cap_w if self.tail_cap else 0),
                            h * self.bar_ratio,
                            ends="right" if self.tail_cap else "none")
                shapes.fill(cr, col)


class ArrowButton(Element):
    """A directional control; ``style`` picks solid triangle or chevron."""

    interactive = True

    def __init__(self, name="", rect=(0, 0, 0, 0), *, direction: str = "up",
                 style: str = "chevron", **kw):
        super().__init__(name, rect, **kw)
        self.direction = direction
        self.style = style

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        inset = min(w, h) * 0.18
        if self.style == "solid":
            shapes.arrow(cr, x + inset, y + inset, w - inset * 2, h - inset * 2,
                         self.direction)
        else:
            shapes.chevron(cr, x + inset, y + inset, w - inset * 2, h - inset * 2,
                           self.direction)
        shapes.fill(cr, self.paint_color(ctx))


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------

class Elbow(Element):
    """The L-bracket that frames every LCARS panel."""

    def __init__(self, name="", rect=(0, 0, 0, 0), *, corner: str = "tl",
                 bar_w: float = 0.0, bar_h: float = 0.0,
                 label: str = "", label_color: Optional[str | RGB] = None,
                 interactive: bool = False,
                 outer_radius: Optional[float] = None, **kw):
        super().__init__(name, rect, **kw)
        self.corner = corner
        self.bar_w = bar_w
        self.bar_h = bar_h
        self.label = label
        self.label_color = label_color
        self.interactive = interactive
        #: None for the classic sweep; 0 for a square outer corner, as when
        #: the elbow is one spot of a grid and its neighbour has the sweep.
        self.outer_radius = outer_radius

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        bw = self.bar_w or w * 0.34
        bh = self.bar_h or h * 0.30
        shapes.elbow(cr, x, y, w, h, bw, bh, self.corner,
                     outer_radius=self.outer_radius)
        shapes.fill(cr, self.paint_color(ctx))
        if self.label:
            size = bh * 0.55
            pad = bh * 0.35
            top = self.corner in ("tl", "tr")
            right = self.corner in ("tr", "br")
            tx = (x + w - pad) if not right else (x + pad)
            ty = (y + bh - bh * 0.22) if top else (y + h - bh + bh * 0.22)
            draw_text(cr, ctx, self.label.upper(), tx, ty, size,
                      ctx.color(self.label_color) if self.label_color
                      else ctx.palette.ink_for(self.paint_color(ctx)),
                      align="right" if not right else "left",
                      valign="bottom" if top else "top")

    def contains(self, px: float, py: float) -> bool:
        x, y, w, h = self.rect
        bw = self.bar_w or w * 0.34
        bh = self.bar_h or h * 0.30
        return shapes.elbow_hit(px, py, x, y, w, h, bw, bh, self.corner)


class Tile(_LabelledButton):
    """A spot in the upper-left box: a block that can carry an icon.

    ``radii`` rounds each corner independently (top-left, top-right,
    bottom-right, bottom-left), so the outermost spot can carry the frame's
    sweep. Without ``on_click`` it is chrome — drawn, never hit.

    ``icon`` is called as ``icon(cr, ctx, cx, cy, size, ink)`` to draw the
    glyph; ``caption`` is a callable for a small live reading under it.
    """

    def __init__(self, name="", rect=(0, 0, 0, 0), *,
                 radii: Tuple[float, float, float, float] = (0, 0, 0, 0),
                 icon: Optional[Callable] = None,
                 caption: Optional[Callable[[], str]] = None, **kw):
        kw.setdefault("label_align", "center")
        super().__init__(name, rect, **kw)
        self.radii = radii
        self.icon = icon
        self.caption = caption
        self.interactive = self.on_click is not None

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        tl, tr, br, bl = self.radii
        shapes.rounded(cr, x, y, w, h, tl=tl, tr=tr, br=br, bl=bl)
        fill = self.paint_color(ctx)
        shapes.fill(cr, fill)
        ink = self._label_color(ctx)
        text = self.caption() if self.caption else ""
        text_h = h * 0.17 if text else 0.0
        if self.icon is not None:
            size = min(w, h - text_h * 1.4) * 0.80
            # Nudged toward the open corner: a swept corner takes room from
            # the top left, and a glyph centred on the box looks pushed into
            # the curve.
            nudge = min(max(self.radii[0] / max(w, 1.0), 0.0), 1.0) * w * 0.08
            self.icon(cr, ctx, x + w / 2 + nudge,
                      y + (h - text_h * 1.2) / 2 + nudge, size, ink)
        if text:
            draw_text(cr, ctx, text, x + w / 2, y + h - h * 0.08, text_h,
                      ink, align="center", valign="bottom",
                      max_width=w * 0.9)


def speaker_icon(level: Callable[[], Optional[float]]) -> Callable:
    """A :class:`Tile` icon: a loudspeaker with one to three waves.

    No waves and a cross means silent; a speaker with no waves at all means
    the level is not known.
    """
    def draw(cr, ctx: RenderContext, cx: float, cy: float, size: float,
             ink: RGB) -> None:
        value = level()
        shapes.speaker(cr, cx, cy, size)
        shapes.fill(cr, ink)
        cr.save()
        cr.set_line_width(max(size * 0.075, 1.5))
        cr.set_line_cap(1)                    # round
        shapes.set_source(cr, ink)
        if value is not None and value <= 0.0:
            ox, d = cx + size * 0.22, size * 0.13
            cr.move_to(ox - d, cy - d)
            cr.line_to(ox + d, cy + d)
            cr.move_to(ox - d, cy + d)
            cr.line_to(ox + d, cy - d)
            cr.stroke()
        elif value is not None:
            waves = 1 if value < 0.34 else 2 if value < 0.67 else 3
            for i in range(waves):
                shapes.sound_wave(cr, cx, cy, size, i)
                cr.stroke()
        cr.restore()
    return draw


class Slider(Element):
    """A horizontal level control, drawn as a run of segments.

    Pressing anywhere on it sets the level there, and dragging follows the
    pointer. ``on_change(level, final)`` is called with ``final`` False while
    dragging and True on release, so the owner can apply cheaply on the way
    and decisively at the end. ``provider`` gives the level to draw (None
    when unknown).
    """

    interactive = True

    def __init__(self, name="", rect=(0, 0, 0, 0), *,
                 provider: Optional[Callable[[], Optional[float]]] = None,
                 on_change: Optional[Callable[[float, bool], None]] = None,
                 note: Optional[Callable[[], str]] = None,
                 segments: int = 32, **kw):
        kw.setdefault("color", "accent4")
        super().__init__(name, rect, **kw)
        self.provider = provider
        self.on_change = on_change
        #: Shown across the track while there is no level to draw — why not.
        self.note = note
        self.segments = max(int(segments), 2)

    def _track(self) -> Tuple[float, float]:
        x, _y, w, h = self.rect
        pad = h * 0.25
        return (x + pad, max(w - pad * 2, 1.0))

    def level_at(self, px: float) -> float:
        start, width = self._track()
        return max(0.0, min(1.0, (px - start) / width))

    def drag(self, px: float, final: bool = False) -> None:
        if self.on_change is not None and self.enabled:
            self.on_change(self.level_at(px), final)

    def click(self) -> None:
        """A press has already set the level; a release only finishes it."""
        self.flash_until = time.monotonic() + 0.18

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        value = self.provider() if self.provider else None
        start, width = self._track()
        n = self.segments
        gap = max(width * 0.006, 1.5)
        seg_w = (width - gap * (n - 1)) / n
        seg_h = h * 0.56
        seg_y = y + (h - seg_h) / 2
        lit = -1 if value is None else round(value * n)
        base = ctx.color(self.color)
        for i in range(n):
            col = base if i < lit else tuple(c * 0.26 for c in base)
            shapes.block(cr, start + i * (seg_w + gap), seg_y, seg_w, seg_h,
                         seg_h * 0.18)
            shapes.fill(cr, col)  # type: ignore[arg-type]
        if value is not None:
            # The thumb: a tall bar at the level, so where it is can be read
            # without counting segments.
            tx = start + value * width
            tw = max(h * 0.26, 5.0)
            halo = max(gap * 1.5, 3.0)
            # Cut out of the segments rather than laid over them, so the
            # thumb reads against lit and unlit track alike.
            # block, not pill: pill rounds the short *vertical* ends of a
            # horizontal bar, and this bar stands upright.
            shapes.block(cr, tx - tw / 2 - halo, y - halo * 0.5,
                         tw + halo * 2, h + halo, (tw + halo * 2) / 2)
            shapes.fill(cr, ctx.palette.background)
            shapes.block(cr, tx - tw / 2, y, tw, h, tw / 2)
            shapes.fill(cr, ctx.color("highlight" if self.pressed else "text"))
        elif self.note is not None and self.note():
            draw_text(cr, ctx, self.note().upper(), x + w / 2, y + h / 2,
                      h * 0.34, ctx.color("alert"), align="center",
                      valign="middle", max_width=w * 0.94)


class BarRun(Element):
    """A run of flat segments — the LCARS "data strip".

    ``weights`` gives the relative width of each segment; ``colors`` cycles
    through palette slot names.  Purely decorative unless ``on_click`` is set.
    """

    def __init__(self, name="", rect=(0, 0, 0, 0), *,
                 weights: Sequence[float] = (2, 1, 3, 1.5, 1, 2.5),
                 colors: Sequence[str] = ("accent2", "accent3", "accent4",
                                          "accent5", "accent2", "accent6"),
                 gap: float = 0.006, cap_left: bool = False,
                 cap_right: bool = False, animate: bool = False, **kw):
        super().__init__(name, rect, **kw)
        self.weights = list(weights)
        self.colors = list(colors)
        self.gap = gap
        self.cap_left = cap_left
        self.cap_right = cap_right
        self.animate = animate

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        n = len(self.weights)
        gap = max(w * self.gap, 1.5)
        total = sum(self.weights)
        avail = w - gap * (n - 1)
        cx = x
        for i, weight in enumerate(self.weights):
            seg_w = avail * weight / total
            slot = self.colors[i % len(self.colors)]
            col = ctx.color(slot)
            if self.animate:
                phase = math.sin(ctx.now * 1.7 + i * 0.9) * 0.5 + 0.5
                col = tuple(c * (0.55 + 0.45 * phase) for c in col)  # type: ignore[assignment]
            ends = "none"
            if i == 0 and self.cap_left:
                ends = "left"
            elif i == n - 1 and self.cap_right:
                ends = "right"
            shapes.pill(cr, cx, y, seg_w, h, ends=ends)
            shapes.fill(cr, col)
            cx += seg_w + gap


class Label(Element):
    """Static or bound text."""

    def __init__(self, name="", rect=(0, 0, 0, 0), *, text: str = "",
                 size: float = 0.0, align: str = "left", valign: str = "top",
                 uppercase: bool = True, wrap: bool = False,
                 provider: Optional[Callable[[], str]] = None, **kw):
        kw.setdefault("color", "accent5")
        super().__init__(name, rect, **kw)
        self.text = text
        self.size = size
        self.align = align
        self.valign = valign
        self.uppercase = uppercase
        self.wrap = wrap
        self.provider = provider

    def current(self) -> str:
        value = self.provider() if self.provider else self.text
        return value.upper() if self.uppercase else value

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        size = self.size or h * 0.72
        tx = x if self.align == "left" else (x + w / 2 if self.align == "center" else x + w)
        ty = y if self.valign == "top" else (y + h / 2 if self.valign == "middle" else y + h)
        # Labels sit on the background; a dim slot is lifted until legible.
        draw_text(cr, ctx, self.current(), tx, ty, size,
                  ctx.palette.on_background(ctx.color(self.color)),
                  align=self.align, valign=self.valign,
                  max_width=w if w > 0 else None, wrap=self.wrap)


# --------------------------------------------------------------------------
# Indicators
# --------------------------------------------------------------------------

class LevelIndicator(Element):
    """A ticked bar showing a 0..1 level, as used by the engineering screen."""

    def __init__(self, name="", rect=(0, 0, 0, 0), *, value: float = 0.5,
                 vertical: bool = True, ticks: int = 20,
                 fill_color: str = "accent3", empty_color: str = "accent1",
                 tick_color: str = "alert", label: str = "",
                 provider: Optional[Callable[[], float]] = None, **kw):
        super().__init__(name, rect, **kw)
        self.value = value
        self.vertical = vertical
        self.ticks = ticks
        self.fill_color = fill_color
        self.empty_color = empty_color
        self.tick_color = tick_color
        self.label = label
        self.provider = provider

    def current(self) -> float:
        v = self.provider() if self.provider else self.value
        return max(0.0, min(1.0, v))

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        v = self.current()
        segs = max(self.ticks, 4)
        gap = (h if self.vertical else w) * 0.012
        if self.vertical:
            seg_h = (h - gap * (segs - 1)) / segs
            lit = round(v * segs)
            for i in range(segs):
                cy = y + h - (i + 1) * seg_h - i * gap
                on = i < lit
                col = ctx.color(self.fill_color if on else self.empty_color)
                if not on:
                    col = tuple(c * 0.35 for c in col)  # type: ignore[assignment]
                shapes.block(cr, x, cy, w, seg_h, seg_h * 0.18)
                shapes.fill(cr, col)
        else:
            seg_w = (w - gap * (segs - 1)) / segs
            lit = round(v * segs)
            for i in range(segs):
                cx = x + i * (seg_w + gap)
                on = i < lit
                col = ctx.color(self.fill_color if on else self.empty_color)
                if not on:
                    col = tuple(c * 0.35 for c in col)  # type: ignore[assignment]
                shapes.block(cr, cx, y, seg_w, h, h * 0.18)
                shapes.fill(cr, col)


class PieButton(Element):
    """A wedge or annulus segment, optionally showing a proportion."""

    interactive = True

    def __init__(self, name="", rect=(0, 0, 0, 0), *, start: float = -90.0,
                 sweep: float = 90.0, inner_ratio: float = 0.45,
                 value: Optional[float] = None, label: str = "", **kw):
        super().__init__(name, rect, **kw)
        self.start = start
        self.sweep = sweep
        self.inner_ratio = inner_ratio
        self.value = value
        self.label = label

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        r = min(w, h) / 2
        cx, cy = x + w / 2, y + h / 2
        sweep = self.sweep if self.value is None else self.sweep * self.value
        if self.value is not None:
            shapes.pie(cr, cx, cy, r, self.start, self.sweep, r * self.inner_ratio)
            col = ctx.color(self.color)
            shapes.fill(cr, tuple(c * 0.3 for c in col))  # type: ignore[arg-type]
        shapes.pie(cr, cx, cy, r, self.start, sweep, r * self.inner_ratio)
        shapes.fill(cr, self.paint_color(ctx))
        if self.label:
            draw_text(cr, ctx, self.label.upper(), cx, cy, r * 0.32,
                      ctx.color("text"), align="center", valign="middle")


class Meter(Element):
    """Label, live value and a horizontal level in one row."""

    def __init__(self, name="", rect=(0, 0, 0, 0), *, label: str = "",
                 provider: Optional[Callable[[], Tuple[float, str]]] = None,
                 bar_color: str = "accent2", **kw):
        kw.setdefault("color", "accent5")
        super().__init__(name, rect, **kw)
        self.label = label
        self.provider = provider
        self.bar_color = bar_color

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        value, text = (self.provider() if self.provider else (0.0, ""))
        value = max(0.0, min(1.0, value))
        size = h * 0.55
        label_w = w * 0.30
        draw_text(cr, ctx, self.label.upper(), x, y + h / 2, size,
                  ctx.color("accent5"), align="left", valign="middle",
                  max_width=label_w)
        # The reading takes the room it needs, from 16% of the row up to
        # 30%, and the bar has what is left. The bar is an approximation of
        # the number; when one has to give way it is the bar. A fixed 16%
        # let a wide face run the reading back over the bar.
        gutter = h * 0.2
        needed, _ = text_size(cr, ctx, text, size) if text else (0.0, 0.0)
        # A little slack: Pango ellipsizes a run that exactly fills its width
        # once letter spacing is on.
        value_w = min(max(needed + size * 0.3, w * 0.16), w * 0.30)
        draw_text(cr, ctx, text, x + w, y + h / 2, size,
                  ctx.color("text"), align="right", valign="middle",
                  max_width=value_w)
        bar_x = x + label_w + gutter
        bar_w = w - label_w - gutter * 2 - value_w
        bar_h = h * 0.46
        bar_y = y + (h - bar_h) / 2
        if bar_w > 4:
            segs = 24
            gap = bar_w * 0.008
            seg_w = (bar_w - gap * (segs - 1)) / segs
            lit = round(value * segs)
            hot = value > 0.85
            for i in range(segs):
                col = ctx.color("alert" if hot and i >= segs * 0.85 else self.bar_color)
                if i >= lit:
                    col = tuple(c * 0.28 for c in col)  # type: ignore[assignment]
                shapes.block(cr, bar_x + i * (seg_w + gap), bar_y, seg_w, bar_h,
                             bar_h * 0.2)
                shapes.fill(cr, col)


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------

class ScrollPanel(Element):
    """A clipped, scrollable region that delegates its contents to a callback.

    ``draw_content(cr, ctx, x, y, w, offset)`` should paint at the supplied
    origin and return the total content height, which the panel uses to clamp
    scrolling.
    """

    interactive = True

    def __init__(self, name="", rect=(0, 0, 0, 0), *,
                 draw_content: Optional[Callable] = None,
                 bar_color: str = "accent3", **kw):
        super().__init__(name, rect, **kw)
        self.draw_content = draw_content
        self.offset = 0.0
        self.content_height = 0.0
        self.bar_color = bar_color
        self.follow_tail = True

    def scroll_by(self, dy: float) -> None:
        self.offset = max(0.0, min(self.offset + dy,
                                   max(0.0, self.content_height - self.h)))
        self.follow_tail = self.offset >= max(0.0, self.content_height - self.h) - 1

    def scroll_to_end(self) -> None:
        self.offset = max(0.0, self.content_height - self.h)
        self.follow_tail = True

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        bar_w = max(ctx.u(0.28), 6.0)
        view_w = w - bar_w * 1.6
        cr.save()
        cr.rectangle(x, y, view_w, h)
        cr.clip()
        if self.draw_content:
            self.content_height = self.draw_content(cr, ctx, x, y - self.offset,
                                                    view_w, self.offset) or 0.0
        cr.restore()
        if self.follow_tail:
            self.offset = max(0.0, self.content_height - h)
        if self.content_height > h + 1:
            track_x = x + w - bar_w
            shapes.block(cr, track_x, y, bar_w, h, bar_w / 2)
            col = ctx.color(self.bar_color)
            shapes.fill(cr, tuple(c * 0.25 for c in col))  # type: ignore[arg-type]
            frac = h / self.content_height
            thumb_h = max(h * frac, ctx.u(1.2))
            max_off = max(self.content_height - h, 1.0)
            thumb_y = y + (h - thumb_h) * min(self.offset / max_off, 1.0)
            shapes.block(cr, track_x, thumb_y, bar_w, thumb_h, bar_w / 2)
            shapes.fill(cr, col)


class ListPanel(Element):
    """A selectable list of rows, matching the original ``Listbox``."""

    interactive = True

    def __init__(self, name="", rect=(0, 0, 0, 0), *, items: Optional[List[str]] = None,
                 on_select: Optional[Callable[[int, str], None]] = None,
                 row_units: float = 1.5, **kw):
        kw.setdefault("color", "accent2")
        super().__init__(name, rect, **kw)
        self.items: List[str] = items or []
        self.on_select = on_select
        self.selected = -1
        self.offset = 0
        self.row_units = row_units
        self._row_h = 0.0

    def visible_rows(self, ctx: RenderContext) -> int:
        rh = ctx.u(self.row_units)
        return max(int(self.h // rh), 1)

    def page(self, delta: int, ctx: RenderContext) -> None:
        n = self.visible_rows(ctx)
        self.offset = max(0, min(self.offset + delta * n, max(0, len(self.items) - n)))

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        rh = ctx.u(self.row_units)
        self._row_h = rh
        gap = rh * 0.14
        rows = int(h // rh)
        for i in range(rows):
            idx = self.offset + i
            if idx >= len(self.items):
                break
            ry = y + i * rh
            selected = idx == self.selected
            col = ctx.color("select" if selected else self.color)
            shapes.pill(cr, x, ry, w, rh - gap, ends="left")
            shapes.fill(cr, col if selected else tuple(c * 0.30 for c in col))  # type: ignore[arg-type]
            draw_text(cr, ctx, self.items[idx], x + rh * 0.7, ry + (rh - gap) / 2,
                      rh * 0.46,
                      ctx.palette.background if selected else ctx.color("text"),
                      align="left", valign="middle", max_width=w - rh)

    def row_at(self, px: float, py: float, ctx: RenderContext) -> int:
        rh = ctx.u(self.row_units)
        if rh <= 0:
            return -1
        i = int((py - self.y) // rh)
        idx = self.offset + i
        return idx if 0 <= idx < len(self.items) else -1


class TabStrip(Element):
    """Horizontal tabs, matching ``x32TabControl``'s selector row."""

    interactive = True

    def __init__(self, name="", rect=(0, 0, 0, 0), *, tabs: Optional[List[str]] = None,
                 on_change: Optional[Callable[[int, str], None]] = None, **kw):
        kw.setdefault("color", "accent3")
        super().__init__(name, rect, **kw)
        self.tabs = tabs or []
        self.index = 0
        self.on_change = on_change

    def tab_rects(self) -> List[Tuple[float, float, float, float]]:
        x, y, w, h = self.rect
        n = max(len(self.tabs), 1)
        gap = w * 0.012
        tw = (w - gap * (n - 1)) / n
        return [(x + i * (tw + gap), y, tw, h) for i in range(n)]

    def draw(self, cr, ctx: RenderContext) -> None:
        for i, (tx, ty, tw, th) in enumerate(self.tab_rects()):
            active = i == self.index
            col = ctx.color("accent4" if active else self.color)
            if not active:
                col = tuple(c * 0.55 for c in col)  # type: ignore[assignment]
            ends = "left" if i == 0 else ("right" if i == len(self.tabs) - 1 else "none")
            shapes.pill(cr, tx, ty, tw, th, ends=ends)
            shapes.fill(cr, col)
            draw_text(cr, ctx, self.tabs[i].upper(), tx + tw / 2, ty + th / 2,
                      th * 0.44, ctx.palette.ink_for(col),
                      align="center", valign="middle", max_width=tw * 0.88)

    def click_at(self, px: float, py: float) -> None:
        for i, (tx, ty, tw, th) in enumerate(self.tab_rects()):
            if tx <= px <= tx + tw and ty <= py <= ty + th:
                if i != self.index:
                    self.index = i
                    if self.on_change:
                        self.on_change(i, self.tabs[i])
                return
