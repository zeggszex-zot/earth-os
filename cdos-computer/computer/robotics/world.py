"""The world model: where a motion would take the robot.  A differential-drive (unicycle) base, integrated in fixed
small steps.  Deterministic and dependency-free; the envelope checks every point of the predicted path."""

from __future__ import annotations

import math

STEP_S = 0.05


def step(x: float, y: float, h: float, v: float, w: float, dt: float) -> tuple[float, float, float]:
    return x + v * math.cos(h) * dt, y + v * math.sin(h) * dt, h + w * dt


def path(x: float, y: float, h: float, v: float, w: float, duration: float, dt: float = STEP_S) -> list[tuple]:
    """(x, y, heading) from now to the end of the motion, every dt, start and end included."""
    pts = [(x, y, h)]
    n = max(1, int(math.ceil(duration / dt))) if duration > 0 else 0
    for i in range(n):
        d = min(dt, duration - i * dt)
        x, y, h = step(x, y, h, v, w, d)
        pts.append((x, y, h))
    return pts


def clearance(points: list[tuple], obstacles, robot_radius: float) -> tuple[float, int]:
    """The smallest gap between the robot's edge and an obstacle's edge along the path, and which obstacle (-1: none)."""
    best, which = math.inf, -1
    for i, (ox, oy, r) in enumerate(obstacles):
        for (x, y, _) in points:
            g = math.hypot(x - ox, y - oy) - r - robot_radius
            if g < best:
                best, which = g, i
    return best, which
