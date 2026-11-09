"""The frame, drawn before there is anything to put in it.

The first version of this file was a centred wordmark on black, drawn from
constants so it could load nothing at all.  It worked, and it was wrong: when
the shell arrived it replaced a wordmark with a completely different layout,
and boot looked like two unrelated programs rather than one machine waking
up.

So this draws **the real chrome** — the same elbows, the same column, the same
bottom bar, at the same proportions — with nothing in it yet.  When the shell
takes over, the frame does not move.  The panels simply fill.

## Why that is affordable

The wordmark version avoided every file on the principle that a splash which
waits for the disk is a black screen.  That principle is sound and the
application of it was not: the palette is **88 bytes** and the language table
**640**, and reading both costs a quarter of a millisecond — against the 128
milliseconds of imports that must already have completed for this module to
be running at all.  By the time anything here executes, the disk has
demonstrably worked. Purity that buys 0.23 ms and costs a jarring cut is a
bad trade.

What is still avoided is :class:`~earth_os.core.Shell`.  Building the shell
is the thing this screen exists to cover, so it cannot wait for one — and the
chrome does not need it.  :mod:`earth_os.chrome` needs only a
:class:`~earth_os.widgets.base.RenderContext`, and the elbow and bar are
ordinary widgets that draw themselves.

The chrome is not merely *aligned with* the shell's, it is **the same
objects**: :func:`earth_os.chrome.pieces` is called here and by
:meth:`~earth_os.screens.base.Screen.add_chrome`, and nothing else describes
that geometry.  The first attempt copied the construction instead, and the
copy had already drifted before it was a day old — a missing right cap on the
bottom bar and a missing clock pill, which at handover read as the frame
twitching.  That is the seam this screen exists to remove, so it cannot be
allowed to reappear inside it.  ``chrome`` lives beside this module rather
than under ``screens/`` for the same reason: importing ``screens`` runs its
package ``__init__`` and drags in every screen, the window manager and the
platform adapters — 72 ms of the very startup this is covering.

## The cursor is the contract

    No pointer on screen  ->  still coming up.
    Pointer appears       ->  the machine is ready.

One signal, no progress bar to lie about how far along anything is. The
pointer is hidden while this is on screen and revealed at the moment the
shell can take input, which makes "is it ready" answerable from across the
room. The status line exists only so a machine which *stops* can be
diagnosed; nobody should need to read it.
"""

from __future__ import annotations

import time

from . import brand
from .chrome import Frame, clock_label
from .chrome import pieces as chrome_pieces
from .theme import Palette, Strings, ensure_font, font_family
from .widgets.base import RenderContext, draw_text

#: What the machine calls itself before it can read a configuration file.
NAME = brand.NAME

_CONTEXT_CACHE: dict = {}


def context(width: float, height: float,
            palette_name: str = "Earth OS") -> RenderContext:
    """A render context with no shell behind it.

    Cached on size because the splash repaints on a timer and re-reading two
    small files thirty times a second would be silly, not because reading
    them once is expensive.
    """
    key = (int(width), int(height), palette_name)
    cached = _CONTEXT_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        palette = Palette.builtin(palette_name)
        strings = Strings.builtin("Standard")
    except Exception:
        # A missing or unreadable asset must not stop the machine booting.
        # The frame is worth drawing even in the wrong colours.
        palette = Palette.builtin("Standard Colors")
        strings = Strings.builtin("Standard")

    # The bundled faces, registered here as well as by the shell. Until
    # they are, Pango draws the name in whatever system face it finds, and
    # the lettering changed typeface at handover — the seam this screen
    # exists to remove. Registering is a few fontconfig calls, once.
    ensure_font()

    ctx = RenderContext(
        palette=palette, strings=strings,
        width=width, height=height,
        unit=max(height / 40.0, 8.0),
        now=time.monotonic(), font=font_family(),
    )
    _CONTEXT_CACHE.clear()          # only ever one size on screen
    _CONTEXT_CACHE[key] = ctx
    return ctx


def draw(cr, width: float, height: float, message: str = "",
         elapsed: float = 0.0) -> None:
    """Paint the empty chrome on its background.

    *message* is the diagnostic line. *elapsed* drives the one moving
    element, so a machine that has stopped looks different from one that is
    merely slow — a still image is indistinguishable from a hang.
    """
    if width < 2 or height < 2:
        return
    ctx = context(width, height)
    cr.set_source_rgb(*ctx.palette.background)
    cr.rectangle(0, 0, width, height)
    cr.fill()
    draw_frame(cr, width, height, message=message, elapsed=elapsed)


def draw_frame(cr, width: float, height: float, message: str = "",
               elapsed: float = 0.0) -> None:
    """:func:`draw` without the background fill.

    For :func:`earth_os.phosphor.composite`, which fills the background
    itself and draws the glow between that fill and the frame. Told exactly
    what :func:`draw` is told and nothing more — in particular, nothing
    about progress.
    """
    if width < 2 or height < 2:
        return

    ctx = context(width, height)
    f = Frame(ctx)
    u = ctx.u

    # -- the chrome: the shell's own pieces, not a copy of them -----------
    #
    # The name goes in as the title, which puts it exactly where the shell
    # will put a title, drawn by the same Label. Nothing here knows where
    # that is.
    for piece in chrome_pieces(ctx, f, title=NAME):
        if piece.name == "chrome_clock":
            piece.label = clock_label()     # type: ignore[attr-defined]
        piece.draw(cr, ctx)

    # -- the empty middle ------------------------------------------------
    #
    # Exactly ``f.content``: the rectangle the shell is about to fill, asked
    # for rather than measured out again here.
    #
    # Outlined, not filled. An outline reads as a space waiting to be
    # occupied; a filled block reads as a component that will be replaced,
    # and being replaced is the look this screen is trying not to have.
    panel_x, panel_y, panel_w, panel_h = f.content
    if panel_w > u(4) and panel_h > u(4):
        outline = ctx.color("accent3")
        cr.save()
        cr.set_source_rgba(outline[0], outline[1], outline[2], 0.30)
        cr.set_line_width(max(u(0.09), 1.0))
        cr.rectangle(panel_x, panel_y, panel_w, panel_h)
        cr.stroke()
        cr.restore()

        _sweep(cr, ctx, panel_x, panel_y + panel_h / 2, panel_w, elapsed)

        if message:
            draw_text(cr, ctx, message.upper(),
                      panel_x + panel_w / 2, panel_y + panel_h / 2 + u(2.2),
                      u(0.66), ctx.color("accent5"),
                      align="center", valign="middle")


def _sweep(cr, ctx: RenderContext, x: float, y: float, width: float,
           elapsed: float) -> None:
    """The one moving element.

    A sweep rather than a progress bar. Progress cannot be known here — this
    module deliberately does not import the thing it is waiting for — and a
    bar that invents a percentage is the same fabricated answer the platform
    layer spent an audit removing.

    Ping-pong rather than wrapping: a wrapping phase lights both ends at once
    as it crosses zero, which reads as a rendering fault, and on a boot
    screen anything that looks like a fault is one.
    """
    u = ctx.u
    ticks = 36
    span = min(width * 0.55, u(30))
    left = x + (width - span) / 2

    cycle = (elapsed * 0.55) % 2.0
    phase = cycle if cycle <= 1.0 else 2.0 - cycle
    base = ctx.color("accent5")

    for i in range(ticks):
        position = i / (ticks - 1)
        glow = max(0.0, 1.0 - abs(position - phase) * 5.0)
        cr.set_source_rgba(base[0], base[1], base[2], 0.12 + 0.88 * glow)
        cr.rectangle(left + position * span, y - u(0.31),
                     max(u(0.18), 2.0), u(0.62))
        cr.fill()


def render_png(path: str, width: int = 1920, height: int = 1080,
               message: str = "", elapsed: float = 0.0) -> str:
    """Draw the splash to a file. For previewing it without booting."""
    import cairo
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    draw(cairo.Context(surface), width, height,
         message=message, elapsed=elapsed)
    surface.write_to_png(path)
    return path
