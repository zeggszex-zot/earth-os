"""The tube: glow, scanlines and a darkened edge over whatever is drawn.

EARTH OS takes its look from the green CRT touchscreens of late-80s
dashboards, and what makes those read as a *tube* rather than as green
rectangles is three things the phosphor does on its own:

* **glow** — lit phosphor bleeds light into its surroundings, so bright
  strokes carry a soft halo;
* **scanlines** — the beam paints in rows, and the gaps between them show;
* **falloff** — the glass is curved and the corners are dimmer.

This module adds all three on top of a frame that has already been drawn, and
it is the only place that does. The window, the boot splash and the shell's
own screenshots all paint through :func:`composite`, so a screenshot looks
like the screen and the splash looks like the shell it becomes.

It needs nothing but Cairo: no palette, no shell, no GTK. ``app.py`` imports
it at the top, where every millisecond is paid before the first frame, so it
must stay that way.

Cost is measured, not hoped for: ``tests/test_phosphor.py`` times a 1080p
composite. Turn it off with ``[shell] phosphor = false`` on a machine that
cannot spare it.
"""

from __future__ import annotations

from typing import Callable, Dict, Tuple

import cairo

#: How lit areas bleed light: ``(halvings, strength)`` per pass. A pass
#: blurs by averaging the frame down ``2**halvings`` times and stretching it
#: back. Two passes at different radii give a tight halo and a wide faint
#: one, which is how phosphor actually falls off; one pass looks like a blur
#: filter.
GLOW_PASSES: Tuple[Tuple[int, float], ...] = ((2, 0.85), (4, 0.95))

#: Darkness of the gap between scanlines, 0..1.
SCANLINE_DEPTH = 0.20

#: How much the corners darken, 0..1.
FALLOFF = 0.36

RGB = Tuple[float, float, float]

#: The scanlines and the corner falloff do not change from frame to frame,
#: so they are drawn once per screen size into one overlay and laid on with
#: a single paint. Drawing the radial gradient afresh cost 30 ms a frame.
_OVERLAY: Dict[Tuple[int, int], cairo.ImageSurface] = {}


def composite(cr, width: float, height: float,
              paint: Callable[[object], None], *,
              background: RGB = (0.0, 0.0, 0.0),
              enabled: bool = True) -> None:
    """Fill with *background*, call ``paint(cr)``, and lay the tube over it.

    *paint* must **not** fill the background itself: the glow is drawn
    between the background and the picture, so it has to be able to see
    where the picture is not. With *enabled* false this is the plain
    background-then-paint, so callers never need two drawing paths.
    """
    cr.save()
    cr.set_source_rgb(*background)
    cr.rectangle(0, 0, width, height)
    cr.fill()
    cr.restore()

    if not enabled or width < 8 or height < 8:
        paint(cr)
        return

    cr.push_group_with_content(cairo.CONTENT_COLOR_ALPHA)
    paint(cr)
    frame = cr.pop_group()

    cr.save()
    _glow(cr, frame, width, height)
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.set_source(frame)
    cr.paint()
    cr.set_source_surface(overlay(width, height), 0, 0)
    cr.paint()
    cr.restore()


def _half(source, width: int, height: int) -> Tuple[cairo.ImageSurface, int, int]:
    """*source* averaged down to half size.

    Bilinear sampling at exactly one half lands every sample between four
    pixels and averages them — a true 2x2 box filter at the price of the
    cheapest resampler. Repeated, it is a blur pyramid. Cairo's "good"
    filter does the same in one step and cost 67 ms a frame at 1080p.
    """
    w, h = max(width // 2, 1), max(height // 2, 1)
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    c = cairo.Context(out)
    c.scale(w / width, h / height)
    c.set_source(source)
    c.get_source().set_filter(cairo.FILTER_BILINEAR)
    c.paint()
    out.flush()
    return out, w, h


def _glow(cr, frame, width: float, height: float) -> None:
    """Paint a blurred copy of the frame *underneath* it.

    Underneath is the whole trick. Drawn on top, a glow lifts the black
    lettering knocked out of every button and pushes pale fills past white.
    Drawn first, the crisp frame covers it everywhere the frame is opaque —
    fills keep their exact palette colour, lettering stays black — and it
    shows only in the empty space around lit things: a halo, which is all
    glow is.

    Both passes are mixed at the size of the finer one and stretched to the
    screen once. Stretching is the expensive step — 15 ms a pass at 1080p
    when blended additively — so it happens a single time, as a plain
    ``OVER`` onto the background, which on an opaque background gives the
    same light as adding it.
    """
    source, w, h = frame, int(width), int(height)
    levels = {}
    for step in range(1, max(n for n, _ in GLOW_PASSES) + 1):
        surface, w, h = _half(source, w, h)
        source = cairo.SurfacePattern(surface)
        levels[step] = (surface, w, h)

    finest = min(n for n, _ in GLOW_PASSES)
    _, gw, gh = levels[finest]
    glow = cairo.ImageSurface(cairo.FORMAT_ARGB32, gw, gh)
    gc = cairo.Context(glow)
    gc.set_operator(cairo.OPERATOR_ADD)
    for halvings, strength in GLOW_PASSES:
        surface, w, h = levels[halvings]
        gc.save()
        gc.scale(gw / w, gh / h)
        gc.set_source_surface(surface, 0, 0)
        pattern = gc.get_source()
        pattern.set_filter(cairo.FILTER_BILINEAR)
        pattern.set_extend(cairo.EXTEND_PAD)
        gc.paint_with_alpha(strength)
        gc.restore()
    glow.flush()

    cr.save()
    cr.scale(width / gw, height / gh)
    cr.set_source_surface(glow, 0, 0)
    pattern = cr.get_source()
    pattern.set_filter(cairo.FILTER_BILINEAR)
    pattern.set_extend(cairo.EXTEND_PAD)
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.paint()
    cr.restore()


def scanline_pitch(height: float) -> int:
    """Rows per scanline: about 360 lines on screen whatever its size."""
    return max(2, round(height / 360.0))


def overlay(width: float, height: float) -> cairo.ImageSurface:
    """Scanlines and corner falloff for a screen this size, drawn once."""
    key = (int(width), int(height))
    cached = _OVERLAY.get(key)
    if cached is not None:
        return cached
    w, h = key
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(w, 1), max(h, 1))
    c = cairo.Context(surface)

    pitch = scanline_pitch(h)
    c.set_source_rgba(0, 0, 0, SCANLINE_DEPTH)
    for row in range(pitch - 1, h, pitch):
        c.rectangle(0, row, w, 1)
    c.fill()

    cx, cy = w / 2, h / 2
    reach = (cx * cx + cy * cy) ** 0.5
    gradient = cairo.RadialGradient(cx, cy, reach * 0.55, cx, cy, reach)
    gradient.add_color_stop_rgba(0.0, 0, 0, 0, 0.0)
    gradient.add_color_stop_rgba(1.0, 0, 0, 0, FALLOFF)
    c.set_source(gradient)
    c.paint()
    surface.flush()

    _OVERLAY.clear()                # one screen size at a time
    _OVERLAY[key] = surface
    return surface


def enabled(config) -> bool:
    """Whether *config* wants the tube. A missing config means yes."""
    if config is None:
        return True
    try:
        return bool(config.get("shell", "phosphor", True))
    except Exception:
        return True
