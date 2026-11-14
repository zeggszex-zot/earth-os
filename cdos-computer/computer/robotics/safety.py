"""The safety envelope: deterministic limits every candidate motion is checked against, whoever proposed it.

    Envelope.load(path)          computer/data/robot_safety.json by default; every limit present, every number finite
    Envelope.check(state, action, now) -> Verdict
        allowed      true only if no rule is violated
        violations   every rule broken: {rule_id, why, value, limit}  (all of them, not just the first)
        checked      every rule that was evaluated
        command      the action if allowed, else STOP

Rules (ids as in the JSON): action.valid, e_stop, state.complete, state.max_age, state.min_confidence, battery.min,
motor_temp.max, speed.max, yaw_rate.max, accel.max, duration.max, workspace, clearance.min.  Workspace and clearance
are checked along the whole predicted path (computer/robotics/world.py), not just its end.

Fail closed: missing data, an old reading, or a reading below the confidence floor is a violation.  The one exception
is STOP (zero speed and zero turn), which is always allowed when well formed: stopping can't make things worse.

`now` is an input (the controller's clock), so the same state, action and time always give the same verdict.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from computer.neural.components import Field, InterfaceError, IOSpec
from computer.robotics.world import clearance, path

DEFAULT = Path(__file__).resolve().parent.parent / "data" / "robot_safety.json"
SAFETY_FORMAT = "cdos-robot-safety/1"
LIMITS = ("speed.max", "yaw_rate.max", "accel.max", "duration.max", "workspace", "clearance.min", "motor_temp.max",
          "battery.min", "state.max_age", "state.min_confidence")
ACTION = IOSpec("robot.action", "1.0.0", (
    Field("v", "float", unit="m/s", min=-10.0, max=10.0, doc="forward speed"),
    Field("omega", "float", unit="rad/s", min=-20.0, max=20.0, doc="turn rate, counter-clockwise positive"),
    Field("duration", "float", unit="s", min=0.0, max=60.0, doc="how long to hold it")))
STOP = {"v": 0.0, "omega": 0.0, "duration": 0.0}


class EnvelopeError(ValueError):
    pass


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    violations: tuple
    checked: tuple
    action: dict
    command: dict
    min_clearance: float | None
    digest: str

    def to_json(self) -> dict:
        return {"allowed": self.allowed, "violations": [dict(v) for v in self.violations], "checked": list(self.checked),
                "action": self.action, "command": self.command,
                "min_clearance": None if self.min_clearance is None or not math.isfinite(self.min_clearance)
                else round(self.min_clearance, 4), "digest": self.digest}


class Envelope:
    def __init__(self, spec: dict):
        if not isinstance(spec, dict) or spec.get("format") != SAFETY_FORMAT:
            raise EnvelopeError(f"a safety file is {SAFETY_FORMAT}")
        lim = spec.get("limits")
        if not isinstance(lim, dict) or set(lim) != set(LIMITS):
            raise EnvelopeError(f"limits must be exactly: {', '.join(LIMITS)}")
        for k, d in lim.items():
            v = d.get("value") if isinstance(d, dict) else None
            nums = v if k == "workspace" else [v]
            if not isinstance(nums, list) or not nums or not all(
                    isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in nums):
                raise EnvelopeError(f"{k}: value must be a finite number" + (" list" if k == "workspace" else ""))
            if k != "workspace" and v < 0:
                raise EnvelopeError(f"{k}: must not be negative")
            if not isinstance(d.get("why"), str) or not d["why"]:
                raise EnvelopeError(f"{k}: say why (a limit nobody can explain can't be reviewed)")
        ws = lim["workspace"]["value"]
        if len(ws) != 4 or not (ws[0] < ws[1] and ws[2] < ws[3]):
            raise EnvelopeError("workspace is [x min, x max, y min, y max], min < max")
        r = spec.get("robot_radius_m")
        if not isinstance(r, (int, float)) or not 0 < r < 10:
            raise EnvelopeError("robot_radius_m must be a positive number")
        self.spec, self.version = spec, str(spec.get("version", ""))
        self.lim = {k: d["value"] for k, d in lim.items()}
        self.why = {k: d["why"] for k, d in lim.items()} | dict(spec.get("always", {}))
        self.radius = float(r)
        self.fingerprint = "sha256:" + hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, path=None) -> "Envelope":
        p = Path(path) if path else DEFAULT
        try:
            return cls(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            raise EnvelopeError(f"{p}: {e}") from None

    def check(self, state, action, now: float) -> Verdict:
        bad, checked = [], []

        def rule(rid: str, ok: bool, value=None, limit=None):
            checked.append(rid)
            if not ok:
                bad.append({"rule_id": rid, "why": self.why.get(rid, rid), "value": value, "limit": limit})

        try:
            ACTION.validate(action)
            valid = True
        except InterfaceError as e:
            valid = False
            rule("action.valid", False, str(e))
        if valid:
            rule("action.valid", True)
        clear = None
        if valid and action["v"] == 0 and action["omega"] == 0:
            pass                                   # STOP: always allowed
        elif valid:
            rule("e_stop", not (state is not None and state.e_stop), "set" if state is not None and state.e_stop else None)
            complete = state is not None and state.complete
            rule("state.complete", complete, list(state.missing) if state is not None else "no state")
            if complete:
                age = now - state.received_oldest
                rule("state.max_age", age <= self.lim["state.max_age"], round(age, 4), self.lim["state.max_age"])
                rule("state.min_confidence", state.confidence >= self.lim["state.min_confidence"],
                     state.confidence, self.lim["state.min_confidence"])
                rule("battery.min", state.battery_v >= self.lim["battery.min"], state.battery_v, self.lim["battery.min"])
                rule("motor_temp.max", state.motor_temp_c <= self.lim["motor_temp.max"], state.motor_temp_c,
                     self.lim["motor_temp.max"])
            v, w, dur = action["v"], action["omega"], action["duration"]
            rule("speed.max", abs(v) <= self.lim["speed.max"], v, self.lim["speed.max"])
            rule("yaw_rate.max", abs(w) <= self.lim["yaw_rate.max"], w, self.lim["yaw_rate.max"])
            rule("duration.max", 0 < dur <= self.lim["duration.max"], dur, self.lim["duration.max"])
            if complete:
                acc = abs(v - state.speed) / dur if dur > 0 else math.inf
                rule("accel.max", acc <= self.lim["accel.max"], round(acc, 4) if math.isfinite(acc) else "infinite",
                     self.lim["accel.max"])
                pts = path(state.x, state.y, state.heading, v, w, min(dur, 60.0))
                x0, x1, y0, y1 = self.lim["workspace"]
                r = self.radius
                out = [p for p in pts if not (x0 + r <= p[0] <= x1 - r and y0 + r <= p[1] <= y1 - r)]
                rule("workspace", not out, [round(out[0][0], 3), round(out[0][1], 3)] if out else None,
                     self.lim["workspace"])
                clear, which = clearance(pts, state.obstacles, r)
                rule("clearance.min", clear >= self.lim["clearance.min"],
                     round(clear, 4) if math.isfinite(clear) else None, self.lim["clearance.min"])
        allowed = valid and not bad
        act = dict(action) if isinstance(action, dict) else {"invalid": repr(action)[:200]}
        body = json.dumps({"state": state.summary() if state is not None else None, "action": act, "now": now,
                           "envelope": self.fingerprint, "allowed": allowed}, sort_keys=True, default=str)
        return Verdict(allowed, tuple(bad), tuple(checked), act, dict(action) if allowed else dict(STOP), clear,
                       "sha256:" + hashlib.sha256(body.encode()).hexdigest())
