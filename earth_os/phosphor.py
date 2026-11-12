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

from typing import Callable, Dict, Optional, Tuple

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

#: The blur pyramid, kept between frames. Four surfaces were allocated and
#: thrown away every frame — 0.065 ms at 1080p, which is not the glow's
#: problem, but it is 120 allocations a second of up to 2 MB each, and the
#: memory target asks for fewer of those. Reused surfaces are overwritten
#: with ``OPERATOR_SOURCE`` rather than drawn onto, so what lands in them is
#: bit-for-bit what a fresh surface would have held. Keyed by size, so a
#: resize drops the lot rather than keeping both.
_PYRAMID: Dict[Tuple[int, int], cairo.ImageSurface] = {}
_PYRAMID_FOR: Tuple[int, int] = (0, 0)


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


def _scratch(w: int, h: int, screen: Tuple[int, int],
             slot: str = "level") -> cairo.ImageSurface:
    """A surface of this size, reused between frames where possible.

    *slot* separates roles that happen to want the same dimensions — the
    finest pyramid level and the surface the passes are mixed into are both
    one quarter of the screen, and they must not be the same surface. The
    pool is dropped whole when the screen size changes, so a resize does not
    leave the old resolution's pyramid resident beside the new one.
    """
    global _PYRAMID_FOR
    if screen != _PYRAMID_FOR:
        _PYRAMID.clear()
        _PYRAMID_FOR = screen
    key = (slot, w, h)
    surface = _PYRAMID.get(key)
    if surface is None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        _PYRAMID[key] = surface
    return surface


def _half(source, width: int, height: int,
          screen: Optional[Tuple[int, int]] = None
          ) -> Tuple[cairo.ImageSurface, int, int]:
    """*source* averaged down to half size.

    Bilinear sampling at exactly one half lands every sample between four
    pixels and averages them — a true 2x2 box filter at the price of the
    cheapest resampler. Repeated, it is a blur pyramid. Cairo's "good"
    filter does the same in one step and cost 67 ms a frame at 1080p.

    With *screen* given, the destination comes from the pool above instead of
    being allocated. ``OPERATOR_SOURCE`` makes the write unconditional, so
    whatever the surface held last frame is replaced rather than blended
    with: the result is identical to painting onto a fresh transparent
    surface, which is what the tests assert.
    """
    w, h = max(width // 2, 1), max(height // 2, 1)
    if screen is None:
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    else:
        out = _scratch(w, h, screen)
    c = cairo.Context(out)
    c.set_operator(cairo.OPERATOR_SOURCE)
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
    screen = (int(width), int(height))
    source, w, h = frame, int(width), int(height)
    levels = {}
    for step in range(1, max(n for n, _ in GLOW_PASSES) + 1):
        surface, w, h = _half(source, w, h, screen)
        source = cairo.SurfacePattern(surface)
        levels[step] = (surface, w, h)

    finest = min(n for n, _ in GLOW_PASSES)
    _, gw, gh = levels[finest]
    # The mix surface is pooled too, in its own slot so it is never the
    # pyramid level of the same size, and cleared before use because
    # OPERATOR_ADD would otherwise accumulate last frame's light.
    glow = _scratch(gw, gh, screen, slot="mix")
    gc = cairo.Context(glow)
    gc.set_operator(cairo.OPERATOR_CLEAR)
    gc.paint()
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


class FrameCache:
    """The last composited frame, kept so an unchanged one is not redrawn.

    The expensive part of the tube is not the scanlines — those are one
    cached paint — it is the glow, and the glow is 7.3 ms of the 14.3 ms a
    1080p frame costs. Measured, the stretch back from the quarter-size blur
    to the screen is 3.9 ms of that on its own. None of it is worth paying
    twice for the same picture, and blitting a kept copy of the finished
    frame costs 0.6 ms: twenty-two times cheaper.

    The hard part is knowing the picture is the same, and the answer is that
    this class does not try to work it out. The caller passes a *token* that
    stands for "the content that would be drawn", and passes ``None`` when
    it cannot promise that — while anything is animating, or while an
    element is mid-flash. A ``None`` token composites and caches nothing, so
    the cache can go stale only if a caller lies about its token.

    In the shell the token is :attr:`earth_os.scheduler.FrameScheduler.generation`,
    which is bumped by every request for a frame. So a repaint GTK asks for
    on its own — the window being shown, uncovered, or moved between
    monitors — is served from here, and a repaint the shell asked for
    because something changed is drawn properly.
    """

    def __init__(self) -> None:
        self.surface: Optional[cairo.ImageSurface] = None
        self.token: object = None
        self.key: Tuple[int, int, float, float] = (0, 0, 0.0, 0.0)
        self.hits = 0
        self.misses = 0
        self.bypassed = 0

    # ------------------------------------------------------------------
    def _scale(self, cr) -> Tuple[float, float]:
        """The device pixels per user unit of *cr*'s current transform.

        A HiDPI window hands the draw function logical sizes and a scaled
        context. Caching at logical resolution would quietly halve the
        sharpness of every cached frame, so the kept surface is allocated at
        device resolution and told its own scale.
        """
        try:
            dx, dy = cr.user_to_device_distance(1.0, 1.0)
        except Exception:
            return 1.0, 1.0
        sx, sy = abs(dx) or 1.0, abs(dy) or 1.0
        return sx, sy

    def invalidate(self) -> None:
        self.surface = None
        self.token = None

    def draw(self, cr, width: float, height: float,
             paint: Callable[[object], None], *,
             background: RGB = (0.0, 0.0, 0.0),
             enabled: bool = True,
             token: object = None) -> bool:
        """Composite onto *cr*, reusing the kept frame when *token* matches.

        Returns True when the kept frame was reused. With *token* ``None``
        this is exactly :func:`composite` and nothing is kept.
        """
        if token is None:
            self.bypassed += 1
            composite(cr, width, height, paint,
                      background=background, enabled=enabled)
            return False

        sx, sy = self._scale(cr)
        key = (int(width), int(height), round(sx, 4), round(sy, 4))
        if (self.surface is not None and self.token == token
                and self.key == key):
            self.hits += 1
            self._blit(cr)
            return True

        self.misses += 1
        if self.surface is None or self.key != key:
            dev_w = max(int(round(width * sx)), 1)
            dev_h = max(int(round(height * sy)), 1)
            self.surface = cairo.ImageSurface(
                cairo.FORMAT_ARGB32, dev_w, dev_h)
            try:
                self.surface.set_device_scale(sx, sy)
            except (AttributeError, cairo.Error):
                # Older pycairo: fall back to caching at logical size, which
                # is correct wherever the scale is 1 and that is every
                # ordinary display.
                self.surface = cairo.ImageSurface(
                    cairo.FORMAT_ARGB32, max(int(width), 1),
                    max(int(height), 1))
            self.key = key

        own = cairo.Context(self.surface)
        own.set_operator(cairo.OPERATOR_SOURCE)
        own.set_source_rgba(0, 0, 0, 0)
        own.paint()
        own.set_operator(cairo.OPERATOR_OVER)
        composite(own, width, height, paint,
                  background=background, enabled=enabled)
        self.surface.flush()
        self.token = token
        self._blit(cr)
        return False

    def _blit(self, cr) -> None:
        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_surface(self.surface, 0, 0)
        cr.paint()
        cr.restore()

    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, int]:
        return {"hits": self.hits, "misses": self.misses,
                "bypassed": self.bypassed}


def enabled(config) -> bool:
    """Whether *config* wants the tube. A missing config means yes."""
    if config is None:
        return True
    try:
        return bool(config.get("shell", "phosphor", True))
    except Exception:
        return True
