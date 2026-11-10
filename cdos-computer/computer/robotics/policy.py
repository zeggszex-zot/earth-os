"""Policies: robot state + goal -> a candidate motion.  Each is a component behind the shared interface
(computer/neural/components.py), and every output is only a proposal: the safety envelope decides.

    robot.policy_naive    heads straight for the goal, as fast as the distance suggests; knows no limit and no obstacle.
                          A baseline, and a way to see the envelope work.
    robot.policy_expert   the same goal-seeking, with the limits in mind and obstacles steered around (it tries a
                          handful of turns and speeds against the world model).  Demonstrator for training, and the
                          fallback when no learned policy is active.
    robot.policy          learned: a small network trained to imitate the expert in simulation (computer/robotics/learn.py),
                          plain-Python inference, versioned and promoted like the reasoning selector.

Input robot.state@1.0.0, output robot.action@1.0.0 (computer/robotics/safety.py ACTION).
"""

from __future__ import annotations

import math

from computer.neural.components import Component, ComponentSpec, Field, IOSpec
from computer.robotics.safety import ACTION
from computer.robotics.world import clearance, path

ROBOT_STATE = IOSpec("robot.state", "1.0.0", (
    Field("x", "float", unit="m"), Field("y", "float", unit="m"), Field("heading", "float", unit="rad"),
    Field("speed", "float", unit="m/s"), Field("yaw_rate", "float", unit="rad/s"),
    Field("obstacles", "list[list[float]]", doc="[[x, y, radius], ...] in metres"),
    Field("goal_x", "float", unit="m"), Field("goal_y", "float", unit="m")))
DURATION = 0.5


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def policy_input(state, goal) -> dict:
    return {"x": state.x, "y": state.y, "heading": state.heading, "speed": state.speed, "yaw_rate": state.yaw_rate,
            "obstacles": [list(o) for o in state.obstacles], "goal_x": float(goal[0]), "goal_y": float(goal[1])}


class NaivePolicy(Component):
    spec = ComponentSpec("robot.policy_naive", "1.0.0", "heuristic", ROBOT_STATE, ACTION,
                         "straight for the goal at a speed proportional to the distance; no limits, no obstacles")

    def run(self, s: dict) -> dict:
        dx, dy = s["goal_x"] - s["x"], s["goal_y"] - s["y"]
        bearing = wrap(math.atan2(dy, dx) - s["heading"])
        return {"v": round(min(1.0 * math.hypot(dx, dy), 9.0), 6), "omega": round(max(-15.0, min(15.0, 3.0 * bearing)), 6),
                "duration": DURATION}


class ExpertPolicy(Component):
    spec = ComponentSpec("robot.policy_expert", "1.0.0", "deterministic", ROBOT_STATE, ACTION,
                         "goal-seeking within the limits, steering around obstacles by trying turns against the world model")
    V_MAX, W_MAX, ACCEL, RADIUS, MARGIN = 0.45, 1.2, 0.45, 0.15, 0.2

    def run(self, s: dict) -> dict:
        dx, dy = s["goal_x"] - s["x"], s["goal_y"] - s["y"]
        dist = math.hypot(dx, dy)
        if dist < 0.1:
            return {"v": 0.0, "omega": 0.0, "duration": DURATION}
        bearing = wrap(math.atan2(dy, dx) - s["heading"])
        lo, hi = s["speed"] - self.ACCEL * DURATION, s["speed"] + self.ACCEL * DURATION
        want_v = min(self.V_MAX, 0.8 * dist) * max(0.0, math.cos(bearing))
        want_w = max(-self.W_MAX, min(self.W_MAX, 2.0 * bearing))
        best = None
        for w in [want_w] + [k * 0.3 for k in range(-4, 5)]:
            for frac in (1.0, 0.6, 0.3):
                v = max(lo, min(hi, max(0.0, want_v * frac)))
                pts = path(s["x"], s["y"], s["heading"], v, w, DURATION)
                gap, _ = clearance(pts, s["obstacles"], self.RADIUS)
                if gap < self.MARGIN:
                    continue
                ex, ey, _ = pts[-1]
                score = math.hypot(s["goal_x"] - ex, s["goal_y"] - ey) + 0.05 * abs(w - want_w)
                if best is None or score < best[0]:
                    best = (score, v, w)
        if best is None:      # nothing safe makes progress: slow down and turn away from the nearest obstacle
            v = max(lo, min(hi, 0.0))
            near = min(s["obstacles"], key=lambda o: math.hypot(o[0] - s["x"], o[1] - s["y"]), default=None)
            w = 0.0 if near is None else (-self.W_MAX if wrap(math.atan2(near[1] - s["y"], near[0] - s["x"])
                                                               - s["heading"]) > 0 else self.W_MAX)
            return {"v": round(v, 6), "omega": w, "duration": DURATION}
        return {"v": round(best[1], 6), "omega": round(best[2], 6), "duration": DURATION}


# ---------------------------------------------------------------------- the learned policy

N_FEATURES = 11
V_SCALE, W_SCALE = 0.6, 2.0       # output range: a little beyond the envelope's limits, so the envelope still matters


def features(s: dict) -> list[float]:
    """Robot-frame view: goal direction and distance, current motion, and the nearest obstacle's position and gap."""
    c, sn = math.cos(-s["heading"]), math.sin(-s["heading"])
    gx, gy = s["goal_x"] - s["x"], s["goal_y"] - s["y"]
    gxr, gyr = gx * c - gy * sn, gx * sn + gy * c
    dist = math.hypot(gx, gy)
    b = math.atan2(gyr, gxr)
    near = min(s["obstacles"], key=lambda o: math.hypot(o[0] - s["x"], o[1] - s["y"]) - o[2], default=None)
    if near is None:
        oxr, oyr, gap = 0.0, 0.0, 3.0
    else:
        ox, oy = near[0] - s["x"], near[1] - s["y"]
        oxr, oyr = ox * c - oy * sn, ox * sn + oy * c
        gap = math.hypot(ox, oy) - near[2] - 0.15
    clip = lambda v, m: max(-m, min(m, v))   # noqa: E731
    return [clip(gxr, 5) / 5, clip(gyr, 5) / 5, min(dist, 5) / 5, math.cos(b), math.sin(b), s["speed"] / 0.5,
            s["yaw_rate"] / 1.5, clip(oxr, 3) / 3, clip(oyr, 3) / 3, min(max(gap, 0.0), 3.0) / 3, 1.0 if dist < 0.1 else 0.0]


class LearnedPolicy(Component):
    """features -> tanh hidden layer -> (v, omega), tanh-scaled.  Weights from a checked model file."""

    def __init__(self, weights: dict, manifest: dict | None = None):
        self.w1, self.b1, self.w2, self.b2 = weights["w1"], weights["b1"], weights["w2"], weights["b2"]
        m = manifest or {}
        self.spec = ComponentSpec("robot.policy", "1.0.0", "neural", ROBOT_STATE, ACTION,
                                  "a small network imitating the expert in simulation (every output still goes through "
                                  "the safety envelope)",
                                  {"id": m.get("model_id", ""), "sha256": m.get("model_sha256", "")})

    def run(self, s: dict) -> dict:
        f = features(s)
        h = [math.tanh(b + sum(w * x for w, x in zip(row, f))) for row, b in zip(self.w1, self.b1)]
        o = [b + sum(w * x for w, x in zip(row, h)) for row, b in zip(self.w2, self.b2)]
        return {"v": round(V_SCALE * math.tanh(o[0]), 6), "omega": round(W_SCALE * math.tanh(o[1]), 6), "duration": DURATION}
