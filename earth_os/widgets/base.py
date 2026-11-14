"""Scene-graph base classes for the LCARS canvas.

The original was a WinForms form with a few hundred child controls.  A
Linux shell wants something cheaper and resolution-independent, so this port
keeps a retained list of lightweight :class:`Element` objects that are laid out
proportionally, painted onto one Cairo surface, and hit-tested by hand.

That means the whole UI is one ``GtkDrawingArea``: no widget hierarchy to fight
with, trivial full-screen scaling, and the panels animate as a unit.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

from ..theme import Palette, Strings, font_family, font_scale  # noqa: E402

RGB = Tuple[float, float, float]
Rect = Tuple[float, float, float, float]


@dataclass
class RenderContext:
    """Everything an element needs in order to paint itself."""

    palette: Palette
    strings: Strings
    width: float
    height: float
    unit: float              # base layout unit, derived from height
    scale: float = 1.0
    now: float = field(default_factory=time.monotonic)
    font: str = field(default_factory=font_family)
    data: Dict[str, object] = field(default_factory=dict)

    def u(self, n: float) -> float:
        """Convert layout units to pixels."""
        return n * self.unit

    def color(self, slot_or_rgb) -> RGB:
        if isinstance(slot_or_rgb, str):
            return self.palette[slot_or_rgb]
        return slot_or_rgb

    def ink(self, slot_or_rgb) -> RGB:
        """A colour for text written straight on the background.

        The same as :meth:`color` unless the slot is too dark to read on
        black, in which case it is lifted (``Palette.on_background``). Use
        it for captions and hints drawn outside any fill.
        """
        return self.palette.on_background(self.color(slot_or_rgb))


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

_LAYOUT_CACHE: Dict[Tuple, Tuple[float, float]] = {}

#: The smallest a fitted label may be drawn, as a fraction of its asked size.
#: Below this it is cut with an ellipsis instead: shrinking further makes a
#: row of buttons look like it has text in four different sizes.
FIT_MIN = 0.72


def draw_text(cr, ctx: RenderContext, text: str, x: float, y: float,
              size: float, color: RGB,
              align: str = "left", valign: str = "top",
              weight: str = "normal", letter_spacing: float = 0.06,
              max_width: Optional[float] = None,
              wrap: bool = False, line_height: float = 1.15,
              family: Optional[str] = None) -> Tuple[float, float]:
    """Draw a run of text and return its rendered ``(width, height)``.

    ``align`` is one of ``left``/``center``/``right`` and positions the text
    relative to *x*; ``valign`` does the same for *y* with
    ``top``/``middle``/``bottom``/``baseline``.

    ``family`` overrides the LCARS face for this run. The terminal uses it to
    get a monospace font, because command output is laid out in columns that
    a proportional face destroys.
    """
    if not text:
        return (0.0, 0.0)

    family = family or ctx.font
    size = size * font_scale(family)

    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription()
    desc.set_family(family)
    if weight == "bold":
        desc.set_weight(Pango.Weight.BOLD)

    def _set_size(px: float) -> None:
        desc.set_absolute_size(max(px, 1.0) * Pango.SCALE)
        layout.set_font_description(desc)
        if letter_spacing:
            attrs = Pango.AttrList()
            spacing = int(letter_spacing * px * Pango.SCALE)
            attrs.insert(Pango.attr_letter_spacing_new(spacing))
            layout.set_attributes(attrs)

    _set_size(size)
    layout.set_text(text, -1)

    if wrap and max_width:
        layout.set_width(int(max_width * Pango.SCALE))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_spacing(int((line_height - 1.0) * size * Pango.SCALE))
    elif max_width:
        # Shrink a little before cutting anything off. A label that is a few
        # percent too long for its button reads better slightly smaller than
        # with its last word replaced by an ellipsis — and the wider faces
        # make "a few percent too long" the common case, not the rare one.
        natural, _ = layout.get_pixel_size()
        if natural > max_width > 0:
            _set_size(size * max(max_width / natural, FIT_MIN))
        layout.set_width(int(max_width * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)

    tw, th = layout.get_pixel_size()

    tx = x
    if align == "center":
        tx = x - tw / 2.0
    elif align == "right":
        tx = x - tw

    ty = y
    if valign == "middle":
        ty = y - th / 2.0
    elif valign == "bottom":
        ty = y - th
    elif valign == "baseline":
        ty = y - layout.get_baseline() / Pango.SCALE

    cr.save()
    cr.set_source_rgba(color[0], color[1], color[2], 1.0)
    cr.move_to(tx, ty)
    PangoCairo.show_layout(cr, layout)
    cr.restore()
    return (tw, th)


def text_size(cr, ctx: RenderContext, text: str, size: float,
              letter_spacing: float = 0.06) -> Tuple[float, float]:
    size = size * font_scale(ctx.font)
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription()
    desc.set_family(ctx.font)
    desc.set_absolute_size(max(size, 1.0) * Pango.SCALE)
    layout.set_font_description(desc)
    if letter_spacing:
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_letter_spacing_new(int(letter_spacing * size * Pango.SCALE)))
        layout.set_attributes(attrs)
    layout.set_text(text, -1)
    return layout.get_pixel_size()


# --------------------------------------------------------------------------
# Elements
# --------------------------------------------------------------------------

class Element:
    """A single drawable, optionally interactive, item on a screen."""

    #: Set by subclasses that respond to clicks.
    interactive = False

    def __init__(self, name: str = "", rect: Rect = (0, 0, 0, 0), *,
                 color: str | RGB = "accent4",
                 on_click: Optional[Callable[[], None]] = None,
                 enabled: bool = True,
                 visible: bool = True,
                 tooltip: str = "",
                 z: int = 0):
        self.name = name
        self.rect: Rect = rect
        self.color = color
        self.on_click = on_click
        self.enabled = enabled
        self.visible = visible
        self.tooltip = tooltip
        self.z = z
        self.hovered = False
        self.pressed = False
        self.flash_until = 0.0

    # -- geometry ----------------------------------------------------------
    @property
    def x(self) -> float: return self.rect[0]

    @property
    def y(self) -> float: return self.rect[1]

    @property
    def w(self) -> float: return self.rect[2]

    @property
    def h(self) -> float: return self.rect[3]

    def set_rect(self, x: float, y: float, w: float, h: float) -> "Element":
        self.rect = (x, y, w, h)
        return self

    def contains(self, px: float, py: float) -> bool:
        x, y, w, h = self.rect
        return x <= px <= x + w and y <= py <= y + h

    # -- painting ----------------------------------------------------------
    def paint_color(self, ctx: RenderContext) -> RGB:
        base = ctx.color(self.color)
        if not self.enabled:
            return ctx.palette.disabled(self.color) if isinstance(self.color, str) else base
        if self.pressed:
            return _shade(base, -0.30)
        if ctx.now < self.flash_until:
            return _shade(base, 0.55)
        if self.hovered and self.interactive:
            return _shade(base, 0.30)
        return base

    def draw(self, cr, ctx: RenderContext) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- interaction -------------------------------------------------------
    def click(self) -> None:
        self.flash_until = time.monotonic() + 0.18
        if self.on_click and self.enabled:
            self.on_click()

    def activate(self) -> None:
        """Programmatic activation, e.g. from a voice command."""
        self.click()


def _shade(rgb: RGB, amount: float) -> RGB:
    if amount >= 0:
        return tuple(c + (1.0 - c) * amount for c in rgb)  # type: ignore[return-value]
    return tuple(c * (1.0 + amount) for c in rgb)          # type: ignore[return-value]


class Group:
    """An ordered collection of elements with a name-indexed lookup."""

    def __init__(self) -> None:
        self.items: List[Element] = []
        self._by_name: Dict[str, Element] = {}

    def add(self, element: Element) -> Element:
        self.items.append(element)
        if element.name:
            self._by_name[element.name] = element
        return element

    def extend(self, elements) -> None:
        for e in elements:
            self.add(e)

    def clear(self) -> None:
        self.items.clear()
        self._by_name.clear()

    def get(self, name: str) -> Optional[Element]:
        return self._by_name.get(name)

    def __getitem__(self, name: str) -> Element:
        return self._by_name[name]

    def __iter__(self):
        return iter(sorted(self.items, key=lambda e: e.z))

    def __len__(self) -> int:
        return len(self.items)

    def hit(self, px: float, py: float) -> Optional[Element]:
        for e in sorted(self.items, key=lambda e: -e.z):
            if e.visible and e.enabled and e.interactive and e.contains(px, py):
                return e
        return None
