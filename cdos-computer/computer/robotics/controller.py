"""The robot controller: observations in, a decision out.  Every decision is journaled; only an approved command is
ever returned as something to execute.

    ctl = RobotController(envelope, components, clock=time.monotonic, journal=ChainLog(...))
    ctl.submit(observations)          each one checked (or converted by its sensor component first), stamped on arrival
    ctl.request_action(goal)          -> {"allowed", "candidate", "policy", "violations", "command", "state", ...}
    ctl.set_e_stop(True)              nothing but STOP until it is cleared

With a latch (the Governor's, computer/governor.py) the stop is persisted and survives a restart, and this controller
can engage it but never clear it: clearing is an operator's decision, made and recorded by the Governor.  The latch is
passed in, not imported, so this module stays free of the clock and the file system.

The policies are components, asked in order: "robot.policy" (the learned one, if active), then "robot.policy_expert".
The first candidate the envelope allows is the command; every candidate tried is in the decision.  A policy that
fails gives no candidate; if nothing is allowed the command is STOP.  Nothing here stops the controller.
"""

from __future__ import annotations

from computer.neural.components import ComponentFailure, InterfaceError
from computer.robotics.policy import policy_input
from computer.robotics.safety import STOP
from computer.robotics.state import ObservationError, build_state, check_observation

JOURNAL_FORMAT = "cdos-robot-decision/1"


class RobotController:
    def __init__(self, envelope, components, clock, journal=None, policies=("robot.policy", "robot.policy_expert"),
                 fallback_on_reject: bool = True, latch=None):
        self.envelope, self.components, self.clock, self.journal = envelope, components, clock, journal
        self.policies = tuple(policies)
        self.fallback_on_reject = fallback_on_reject
        self.latest: dict = {}           # quantity -> (observation, received_at)
        self.scan: list = []             # [(obstacle observation, received_at)] of the newest scan
        self.latch = latch
        self._e_stop = False
        self.decisions = 0
        if latch is not None and self.journal is not None:
            self.journal.append({"event": "e_stop_restored", "on": bool(latch.engaged), "why": latch.why,
                                 "t": self.clock()})

    @property
    def e_stop(self) -> bool:
        return bool(self.latch.engaged) if self.latch is not None else self._e_stop

    def set_e_stop(self, on: bool, by: dict | None = None, reason: str = "") -> None:
        """Without a latch: set or clear (the 0.2 behaviour).  With one: engage only; a clear raises PermissionError."""
        if self.latch is not None:
            if not on:
                raise PermissionError("the emergency stop is latched: only an operator clears it, through the Governor")
            if not self.latch.engaged:
                self.latch.engage(by or {"name": "robot.controller"}, reason or "engaged by the controller")
        else:
            self._e_stop = bool(on)
        self.note_e_stop(by, reason)

    def note_e_stop(self, by: dict | None = None, reason: str = "") -> None:
        """Journal the stop's current state (after the Governor changed it, or set_e_stop)."""
        if self.journal is not None:
            ev = {"event": "e_stop", "on": self.e_stop, "t": self.clock()}
            if by:
                ev.update(by=by.get("name"), uid=by.get("uid"))
            if reason:
                ev["reason"] = reason
            self.journal.append(ev)

    def _convert(self, o: dict) -> dict:
        """A raw reading ({"sensor": <component>, "quantity", "raw", "t", "confidence"}) through its sensor component."""
        comp = o.get("sensor", "")
        out = self.components.call(comp, {"raw": o["raw"]})
        if not out.get("valid", False):
            raise ObservationError(f"{comp} says the reading isn't valid ({out})")
        q = o.get("quantity")
        unit = {"temperature_c": "degC"}
        key = next((k for k in out if k in unit), None)
        if key is None:
            raise ObservationError(f"{comp} gives no quantity this controller knows")
        return {"sensor": comp, "quantity": q, "value": [float(out[key])], "unit": unit[key], "t": o["t"],
                "confidence": min(float(o.get("confidence", 1.0)), 1.0)}

    def submit(self, observations: list) -> dict:
        now = self.clock()
        refused, accepted, scan = [], 0, []
        for i, o in enumerate(observations if isinstance(observations, list) else []):
            try:
                if isinstance(o, dict) and "raw" in o:
                    o = self._convert(o)
                check_observation(o)
            except (InterfaceError, ObservationError, ComponentFailure, KeyError, TypeError) as e:
                refused.append({"index": i, "why": str(e)[:300]})
                continue
            accepted += 1
            if o["quantity"] == "obstacle":
                scan.append((o, now))
                continue
            prev = self.latest.get(o["quantity"])
            if prev is None or o["t"] >= prev[0]["t"]:
                self.latest[o["quantity"]] = (o, now)
        if scan:
            newest = max(o["t"] for o, _ in scan)
            self.scan = [(o, r) for o, r in scan if o["t"] == newest]
        return {"accepted": accepted, "refused": refused, "received_at": now}

    def state(self):
        return build_state(self.latest, self.scan, self.e_stop)

    def request_action(self, goal) -> dict:
        """Ask the policies in order.  The first candidate the envelope allows is the command; a rejected one is
        recorded and the next policy asked (fallback_on_reject).  If none is allowed: STOP, with the first
        candidate's violations."""
        now = self.clock()
        st = self.state()
        tried, why = [], ""
        if st.complete and isinstance(goal, (list, tuple)) and len(goal) == 2:
            for name in self.policies:
                if not self.components.has(name):
                    continue
                try:
                    cand = self.components.call(name, policy_input(st, goal))
                except (ComponentFailure, InterfaceError) as e:
                    why = f"{name}: {e}"
                    continue
                v = self.envelope.check(st, cand, now)
                tried.append((self.components.get(name).spec.ref, cand, v))
                if v.allowed or not self.fallback_on_reject:
                    break
        pick = next((x for x in tried if x[2].allowed), tried[0] if tried else None)
        used, candidate, verdict = pick if pick else (None, None, None)
        if verdict is None:
            # no candidate (incomplete state, no policy, a policy failure): the envelope still says why not
            v = self.envelope.check(st, {"v": 0.1, "omega": 0.0, "duration": 0.5}, now)
            violations = [x for x in v.violations if x["rule_id"].startswith(("state.", "e_stop"))]
            decision = {"allowed": False, "candidate": None, "policy": used, "policy_error": why or
                        ("the state is incomplete" if not st.complete else "no policy available"),
                        "violations": violations, "command": dict(STOP), "checked": list(v.checked)}
        else:
            decision = {"allowed": verdict.allowed, "candidate": candidate, "policy": used,
                        "violations": [dict(x) for x in verdict.violations], "command": verdict.command,
                        "checked": list(verdict.checked), "min_clearance": verdict.to_json()["min_clearance"],
                        "digest": verdict.digest,
                        "tried": [{"policy": p, "candidate": c, "allowed": v.allowed,
                                   "violations": [x["rule_id"] for x in v.violations]} for p, c, v in tried]}
        decision.update(t=now, goal=list(goal) if isinstance(goal, (list, tuple)) else None, state=st.summary(),
                        envelope=self.envelope.fingerprint)
        self.decisions += 1
        if self.journal is not None:
            self.journal.append({"event": "decision", **decision})
        return decision
