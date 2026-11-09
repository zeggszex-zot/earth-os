"""Cairo path primitives for the LCARS shape vocabulary.

Every routine here only *builds a path* on the supplied context; filling and
stroking is the caller's business.  Keeping paths separate from paint is what
lets the same geometry be used for drawing and for hit-testing.

The vocabulary matches the control set of the original ``LCARS.dll``:
pill, half pill, block, elbow, arrow, endcap, level indicator, pie.
"""

from __future__ import annotations

import math
from typing import Literal, Tuple

Corner = Literal["tl", "tr", "bl", "br"]
Side = Literal["left", "right", "top", "bottom", "both", "none"]

TAU = math.pi * 2


# --------------------------------------------------------------------------
# Basic blocks
# --------------------------------------------------------------------------

def block(cr, x: float, y: float, w: float, h: float, radius: float = 0.0) -> None:
    """A rectangle, optionally with uniformly rounded corners."""
    r = max(0.0, min(radius, w / 2, h / 2))
    if r <= 0.01:
        cr.rectangle(x, y, w, h)
        return
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def pill(cr, x: float, y: float, w: float, h: float, ends: Side = "both") -> None:
    """A bar with semicircular ends.

    ``ends`` selects which ends are rounded, giving the original's
    StandardButton (both), HalfPillButton (one) and FlatButton (none).

    A single-ended cap keeps the full ``h/2`` corner radius even when it is
    narrower than it is tall — that is what makes the short LCARS end-caps
    read as half-discs rather than as wedges.
    """
    if ends == "none" or h <= 0 or w <= 0:
        cr.rectangle(x, y, w, h)
        return

    both = ends == "both"
    r = min(h / 2.0, w / 2.0) if both else min(h / 2.0, w)

    left_round = ends in ("both", "left")
    right_round = ends in ("both", "right")

    cr.new_sub_path()
    if right_round:
        cr.move_to(x + w - r, y)
        cr.arc(x + w - r, y + r, r, -math.pi / 2, math.pi / 2)
    else:
        cr.move_to(x + w, y)
        cr.line_to(x + w, y + h)
    if left_round:
        cr.line_to(x + r, y + h)
        cr.arc(x + r, y + r, r, math.pi / 2, 3 * math.pi / 2)
    else:
        cr.line_to(x, y + h)
        cr.line_to(x, y)
    cr.close_path()


def endcap(cr, x: float, y: float, w: float, h: float, side: Side = "left") -> None:
    """The short rounded cap that opens or closes an LCARS bar run."""
    pill(cr, x, y, w, h, ends="left" if side == "left" else "right")


# --------------------------------------------------------------------------
# Elbow
# --------------------------------------------------------------------------

def elbow(cr, x: float, y: float, w: float, h: float,
          bar_w: float, bar_h: float,
          corner: Corner = "tl",
          outer_radius: float | None = None,
          inner_radius: float | None = None) -> None:
    """The signature LCARS L-bracket.

    ``corner`` names which corner of the bounding box carries the big outer
    curve.  ``bar_w`` is the width of the vertical arm, ``bar_h`` the height of
    the horizontal arm.  Radii default to the proportions used by the original
    artwork: the outer curve is a little wider than the vertical arm, the inner
    curve about a third of the horizontal arm.
    """
    ro = outer_radius if outer_radius is not None else max(bar_w, bar_h) * 1.7
    ri = inner_radius if inner_radius is not None else max(bar_h * 0.85, 2.0)
    ro = max(0.0, min(ro, w, h))
    ri = max(0.0, min(ri, max(w - bar_w - 0.5, 0.0), max(h - bar_h - 0.5, 0.0)))

    # Build in "tl" space, then mirror into the requested corner.
    cr.save()
    if corner in ("tr", "br"):
        cr.translate(x + w, 0)
        cr.scale(-1, 1)
        cr.translate(-x, 0)
    if corner in ("bl", "br"):
        cr.translate(0, y + h)
        cr.scale(1, -1)
        cr.translate(0, -y)

    x0, y0 = x, y
    cr.new_sub_path()
    cr.move_to(x0 + w, y0)
    cr.line_to(x0 + ro, y0)
    # Outer corner: sweep anticlockwise from the top of the circle round to its
    # left, i.e. a 90 degree decrease from -pi/2 to -pi.
    cr.arc_negative(x0 + ro, y0 + ro, ro, -math.pi / 2, math.pi)
    cr.line_to(x0, y0 + h)
    cr.line_to(x0 + bar_w, y0 + h)
    cr.line_to(x0 + bar_w, y0 + bar_h + ri)
    # Inner fillet: clockwise from the left of the circle round to its top.
    cr.arc(x0 + bar_w + ri, y0 + bar_h + ri, ri, math.pi, 3 * math.pi / 2)
    cr.line_to(x0 + w, y0 + bar_h)
    cr.close_path()
    cr.restore()


def elbow_hit(px: float, py: float, x: float, y: float, w: float, h: float,
              bar_w: float, bar_h: float, corner: Corner = "tl") -> bool:
    """Cheap analytic hit-test for an elbow: inside either arm."""
    if not (x <= px <= x + w and y <= py <= y + h):
        return False
    left = corner in ("tl", "bl")
    top = corner in ("tl", "tr")
    in_vertical = (px <= x + bar_w) if left else (px >= x + w - bar_w)
    in_horizontal = (py <= y + bar_h) if top else (py >= y + h - bar_h)
    return in_vertical or in_horizontal


# --------------------------------------------------------------------------
# Arrows and indicators
# --------------------------------------------------------------------------

def arrow(cr, x: float, y: float, w: float, h: float,
          direction: Literal["up", "down", "left", "right"] = "up") -> None:
    """A solid triangle filling its box, used by the original ArrowButton."""
    cx, cy = x + w / 2, y + h / 2
    if direction == "up":
        pts = [(cx, y), (x + w, y + h), (x, y + h)]
    elif direction == "down":
        pts = [(cx, y + h), (x + w, y), (x, y)]
    elif direction == "left":
        pts = [(x, cy), (x + w, y), (x + w, y + h)]
    else:
        pts = [(x + w, cy), (x, y), (x, y + h)]
    cr.new_sub_path()
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    cr.close_path()


def chevron(cr, x: float, y: float, w: float, h: float,
            direction: Literal["up", "down", "left", "right"] = "up",
            thickness: float = 0.30) -> None:
    """An outlined arrow head; reads better than a solid triangle at size."""
    t = min(w, h) * thickness
    cx, cy = x + w / 2, y + h / 2
    if direction in ("up", "down"):
        sign = -1 if direction == "up" else 1
        cr.new_sub_path()
        cr.move_to(x, cy - sign * h * 0.15)
        cr.line_to(cx, cy + sign * h * 0.35)
        cr.line_to(x + w, cy - sign * h * 0.15)
        cr.line_to(x + w, cy - sign * h * 0.15 - sign * t)
        cr.line_to(cx, cy + sign * h * 0.35 - sign * t)
        cr.line_to(x, cy - sign * h * 0.15 - sign * t)
    else:
        sign = -1 if direction == "left" else 1
        cr.new_sub_path()
        cr.move_to(cx - sign * w * 0.15, y)
        cr.line_to(cx + sign * w * 0.35, cy)
        cr.line_to(cx - sign * w * 0.15, y + h)
        cr.line_to(cx - sign * w * 0.15 - sign * t, y + h)
        cr.line_to(cx + sign * w * 0.35 - sign * t, cy)
        cr.line_to(cx - sign * w * 0.15 - sign * t, y)
    cr.close_path()


def pie(cr, cx: float, cy: float, radius: float,
        start_deg: float, sweep_deg: float, inner: float = 0.0) -> None:
    """A pie or annulus wedge, matching the original PieButton."""
    a0 = math.radians(start_deg)
    a1 = math.radians(start_deg + sweep_deg)
    cr.new_sub_path()
    if inner <= 0.01:
        cr.move_to(cx, cy)
        cr.arc(cx, cy, radius, a0, a1)
        cr.close_path()
    else:
        cr.arc(cx, cy, radius, a0, a1)
        cr.arc_negative(cx, cy, inner, a1, a0)
        cr.close_path()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def set_source(cr, rgb: Tuple[float, float, float], alpha: float = 1.0) -> None:
    cr.set_source_rgba(rgb[0], rgb[1], rgb[2], alpha)


def fill(cr, rgb: Tuple[float, float, float], alpha: float = 1.0) -> None:
    set_source(cr, rgb, alpha)
    cr.fill()


def point_in_path(cr, px: float, py: float) -> bool:
    """Hit-test the current path without consuming it."""
    return bool(cr.in_fill(px, py))
