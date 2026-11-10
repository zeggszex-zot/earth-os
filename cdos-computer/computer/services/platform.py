"""The platform a service runs: the computer, its components, and (with robotics = true) the robot controller.

Nothing here can stop start-up except the computer's own built-in checks: a missing neural model, a refused plugin,
an unreadable safety file or a damaged robot policy each leave that part off, with the reason in health().

The Governor (computer/governor.py) is always built: it decides what each caller of the API may do, owns the robot's
emergency-stop latch, and records every privileged decision.  A missing or broken policy or audit log never stops
start-up either; it leaves privileged actions refused and the stop engaged.
"""

from __future__ import annotations

import time
from pathlib import Path

from computer.config import HERE, Config


def resolve(p: str) -> Path | None:
    if not p:
        return None
    q = Path(p)
    return q if q.is_absolute() else HERE / q


class Platform:
    def __init__(self, cfg: Config, clock=time.monotonic, computer=None):
        from computer.core import Computer
        self.cfg, self.clock = cfg, clock
        self.computer = computer if computer is not None else Computer(cfg)
        self.components = self.computer.components
        from computer.governor import Governor
        self.governor = Governor.from_config(cfg)
        self.started = clock()
        self.robot = None
        self.robot_error = ""
        self.policy_error = ""
        if cfg.robotics:
            self._robotics()

    def _robotics(self) -> None:
        from computer.neural.experience import ChainLog
        from computer.robotics.controller import JOURNAL_FORMAT, RobotController
        from computer.robotics.policy import ExpertPolicy
        from computer.robotics.safety import Envelope, EnvelopeError
        try:
            envelope = Envelope.load(resolve(self.cfg.robot_safety_file))
        except EnvelopeError as e:
            self.robot_error = f"robotics off: the safety envelope didn't load ({e})"
            return
        if not self.components.has("robot.policy_expert"):
            self.components.register(ExpertPolicy())
        reg = resolve(self.cfg.neural_registry)
        if reg is not None:
            from computer.robotics.learn import load_active_policy
            pol, why = load_active_policy(reg)
            if pol is not None:
                self.components.register(pol)
            else:
                self.policy_error = why
        else:
            self.policy_error = "neural_registry not set: the expert policy is used"
        journal = None
        jp = resolve(self.cfg.robotics_journal)
        if jp is not None:
            try:
                journal = ChainLog(jp, fmt=JOURNAL_FORMAT)
            except OSError as e:
                self.robot_error = f"robot journal off: {e}"
        self.robot = RobotController(envelope, self.components, self.clock, journal, latch=self.governor.latch)

    # ------------------------------------------------------------------ what the API serves

    def health(self) -> dict:
        c = self.computer
        n = c.neural
        return {"status": "ok", "uptime_s": round(self.clock() - self.started, 3),
                "corpus": {"ready": bool(c.corpus.ready), "version": c.corpus.version if c.corpus.ready else None,
                           "integrity": c.corpus.integrity, "status": c.corpus.status()},
                "neural": {"model_id": n.model_id if n else None, "error": c.neural_error or None,
                           "failed": (n.failed or None) if n else None},
                "plugins": [{"name": p.name, "version": p.version, "error": p.error or None} for p in c.plugins],
                "components": self.components.names(),
                "components_refused": dict(self.components.refused),
                "robotics": None if not self.cfg.robotics else {
                    "on": self.robot is not None, "error": self.robot_error or None,
                    "policies": [x for x in (self.robot.policies if self.robot else ()) if self.components.has(x)],
                    "learned_policy": self.policy_error or "active",
                    "envelope": self.robot.envelope.fingerprint if self.robot else None,
                    "e_stop": self.robot.e_stop if self.robot else None},
                "capabilities_off": dict(c.registry.off),
                "governor": self.governor.status()}

    def estop(self, ident, on: bool, reason: str = "") -> dict:
        """Engage (anyone) or clear (an operator, with a reason) the emergency stop, through the Governor."""
        g = self.governor
        latch = g.estop_engage(ident, reason, "api") if on else g.estop_clear(ident, reason)
        if self.robot is not None:
            self.robot.note_e_stop(ident.to_json(), reason)
        return latch

    def ask(self, question: str) -> dict:
        r = self.computer.think(question)
        e = r.envelope or {}
        return {"text": r.text, "faculty": r.faculty, "status": e.get("status"), "certainty": e.get("certainty"),
                "truth": e.get("truth"), "envelope": e}

    def capabilities(self) -> list[dict]:
        reg = self.computer.registry
        return [{"name": c.name, "version": c.version, "deterministic": c.deterministic, "optional": c.optional,
                 "writes_state": c.writes_state, "available": reg.available(c.name)[0],
                 "why_off": reg.off.get(c.name), "summary": c.summary} for c in reg.caps.values()]
