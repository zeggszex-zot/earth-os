"""Phase 21, step 1: one task record and one lifecycle for every kind of request.

The parts underneath already exist and keep doing their own jobs.  The computer answers questions
(computer/core.py), diagnostics reads the machine (computer/diagnostics.py), the robot controller checks motions
(computer/robotics/controller.py), the registry activates models (computer/neural/registry.py), and the Governor
decides who may do what (computer/governor.py).  This module routes a request to one of them and records what
happened in a single shape.  It holds no facts and no rules of its own, and it grants no permission.

    runner = TaskRunner(computer=..., governor=..., robot=..., executor=..., registry=..., journal=...)
    task = runner.run({"question": "what is 12 times 7"}, identity)
    task = runner.run({"check": "memory"}, identity)
    task = runner.run({"goal": [3.0, 0.0]}, identity)              # robotics: needs the robot role
    task = runner.run({"model_op": "promote", "model_id": ..., "reason": ...}, identity)   # needs an operator

The record (format cdos-task/1):

    {"format", "task_id", "task_type": "knowledge | system | robotics | learning",
     "status": "completed | denied | rejected | unsupported | failed",
     "stage": the last stage reached (STAGES), "input": {...}, "requester": identity,
     "route": {"handler", "capability", "why"},
     "evidence_refs": [...],          facts, readings, observations or a calculation the result rests on
     "proposed_action": null | {...}, what would change the world; never executed unless authorized and validated
     "authorization": {"state": "allowed | denied", "permission", "why", "decision": governor ref | null},
     "result": null | {...},          for questions, the result envelope (computer/envelope.py), unchanged
     "verification": {"ok", "checks": [{"check", "ok", "why"}]},
     "error": null | {"code", "message"},
     "history": [{"stage", "t"}], "result_hash": "sha256:...",
     "claim_hash": "sha256:..."}      knowledge only: status, truth, certainty, value, text, evidence ids, error codes

The lifecycle: received -> routed -> authorized -> planned -> validated -> executed -> verified -> recorded.
A task stops at the first stage that says no, and the record shows where it stopped:

    denied       the Governor refused the requester (nothing planned, nothing changed)
    rejected     a plan was made but a deterministic check refused it (the robot's safety envelope).  Nothing was
                 executed except STOP
    unsupported  CDOS has no capability for that (a system-changing action, an unknown model operation)
    failed       a capability errored, or the result didn't pass verification

Permission is checked before any state changes, including the controller's observation buffer.  A neural model
can propose a plan or a candidate motion, but every handler takes its permission from the Governor.  Nothing in
this module promotes, clears a stop or moves a robot on its own say-so.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field

from . import envelope as envelope_mod

TASK_FORMAT = "cdos-task/1"
TYPES = ("knowledge", "system", "robotics", "learning")
STAGES = ("received", "routed", "authorized", "planned", "validated", "executed", "verified", "recorded")
STATUSES = ("completed", "denied", "rejected", "unsupported", "failed")

# What each kind of request needs from the Governor.  The roles that grant these are in computer/governor.py.
PERMISSION = {"knowledge": "api.ask", "system": "api.read", "robotics": "robot.command"}
MODEL_OPS = {"status": "api.read", "promote": "model.promote", "rollback": "model.rollback",
             "deactivate": "model.deactivate"}

# Read-only diagnostics: what counts as a problem worth a recommendation.  Each threshold is stated in the finding.
THRESHOLDS = {"mem_avail_frac.warn": 0.10, "mem_avail_frac.critical": 0.05,
              "disk_free_frac.warn": 0.10, "disk_free_gb.warn": 1.0,
              "temperature_c.warn": 80.0, "load.warn": 0.90}
SYSTEM_READINGS = ("temperature", "load", "memory", "disk", "battery", "corpus", "uptime")
POSE_TOLERANCE = 1e-6           # the simulator moves by the same kinematics the verifier predicts with


class TaskError(Exception):
    def __init__(self, status: str, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class Task:
    task_id: str
    task_type: str
    input: dict
    requester: dict
    status: str = "failed"
    stage: str = "received"
    route: dict = field(default_factory=dict)
    evidence_refs: list = field(default_factory=list)
    proposed_action: dict | None = None
    authorization: dict = field(default_factory=lambda: {"state": "not_requested"})
    result: dict | None = None
    verification: dict = field(default_factory=lambda: {"ok": None, "checks": []})
    error: dict | None = None
    history: list = field(default_factory=list)

    def to_json(self) -> dict:
        d = {"format": TASK_FORMAT, "task_id": self.task_id, "task_type": self.task_type, "status": self.status,
             "stage": self.stage, "input": self.input, "requester": self.requester, "route": self.route,
             "evidence_refs": self.evidence_refs, "proposed_action": self.proposed_action,
             "authorization": self.authorization, "result": self.result, "verification": self.verification,
             "error": self.error, "history": self.history}
        d["result_hash"] = envelope_mod.canonical_hash({"task_type": self.task_type, "status": self.status,
                                                        "result": self.result})
        if self.task_type == "knowledge" and self.result:
            d["claim_hash"] = claim_hash(self.result)
        return d


CLAIM = ("status", "truth", "certainty", "value", "text", "kind")


def claim_hash(env: dict) -> str:
    """What a result asserts, without which capabilities produced it: a neural planner that tried the navigator
    before the lookup answered changes the envelope's capability list, not the claim."""
    body = {k: env.get(k) for k in CLAIM}
    body["evidence"] = sorted(str(e.get("id")) for e in env.get("evidence", []))
    body["errors"] = sorted(str(e.get("code")) for e in env.get("errors", []))
    return envelope_mod.canonical_hash(body)


def _check(name: str, ok: bool, why: str = "") -> dict:
    return {"check": name, "ok": bool(ok), "why": why}


def task_type_of(request: dict) -> str:
    """Which kind of task a request is.  Requests are structured; a free-text question is the knowledge kind (the
    router inside the computer then decides between corpus, calculation, analysis, diagnostics and the sampler)."""
    keys = [t for t, k in (("knowledge", "question"), ("system", "check"), ("robotics", "goal"),
                           ("learning", "model_op")) if k in request]
    declared = request.get("type")
    if declared is not None and declared not in TYPES:
        raise TaskError("unsupported", "INVALID_INPUT", f"unknown task type {declared!r}")
    if len(keys) != 1:
        raise TaskError("failed", "INVALID_INPUT", "a request names exactly one of: question, check, goal, model_op")
    if declared is not None and declared != keys[0]:
        raise TaskError("failed", "INVALID_INPUT", f"type {declared!r} doesn't match a {keys[0]} request")
    return keys[0]


class TaskRunner:
    """Every part is optional.  A request for a missing part fails with UNAVAILABLE instead of stopping start-up."""

    def __init__(self, computer=None, governor=None, robot=None, executor=None, registry=None, diagnostics=None,
                 journal=None, clock=time.time):
        self.computer, self.governor, self.robot, self.executor = computer, governor, robot, executor
        self.registry, self.journal, self.clock = registry, journal, clock
        self.diagnostics = diagnostics if diagnostics is not None else getattr(computer, "diagnostics", None)
        self.seq = 0

    @classmethod
    def from_platform(cls, platform, executor=None, journal=None, clock=time.time) -> "TaskRunner":
        from computer.services.platform import resolve
        return cls(platform.computer, platform.governor, platform.robot, executor,
                   resolve(platform.cfg.neural_registry), journal=journal, clock=clock)

    # ------------------------------------------------------------------ the lifecycle

    def _id(self, request: dict) -> str:
        self.seq += 1
        body = json.dumps(request, sort_keys=True, default=str).encode("utf-8")
        return f"task-{self.seq:06d}-{hashlib.sha256(body).hexdigest()[:8]}"

    def _at(self, task: Task, stage: str) -> None:
        task.stage = stage
        task.history.append({"stage": stage, "t": self.clock()})

    def run(self, request: dict, identity) -> Task:
        request = dict(request) if isinstance(request, dict) else {}
        task = Task(self._id(request), "", request, identity.to_json() if identity is not None else {})
        self._at(task, "received")
        try:
            task.task_type = task_type_of(request)
            handler = getattr(self, f"_{task.task_type}")
            self._at(task, "routed")
            handler(task, identity)
        except TaskError as e:
            task.status, task.error = e.status, {"code": e.code, "message": e.message}
        except Exception as e:  # noqa: BLE001 - a failed task is recorded, never a dead runner
            task.status, task.error = "failed", {"code": "UNAVAILABLE", "message": f"{type(e).__name__}: {e}"}
        self._record(task)
        return task

    def _record(self, task: Task) -> None:
        task.history.append({"stage": "recorded", "t": self.clock()})
        if self.journal is not None:
            if not self.journal.append(task.to_json()):
                task.error = task.error or {"code": "UNAVAILABLE", "message": "the task journal couldn't be written"}

    def _authorize(self, task: Task, identity, permission: str, target: str, reason: str = "",
                   privileged: bool = False) -> object:
        """The Governor decides.  Without one, only read and ask are possible.  Privileged decisions are written
        to its audit log; read, ask and motions are checked without a line (as the API does), and every denial is
        written."""
        if self.governor is None:
            if permission not in ("api.read", "api.ask"):
                task.authorization = {"state": "denied", "permission": permission, "why": "no Governor configured",
                                      "decision": None}
                raise TaskError("denied", "FORBIDDEN", f"{permission} needs the Governor, and none is configured")
            task.authorization = {"state": "allowed", "permission": permission, "why": "basic permission, no Governor",
                                  "decision": None}
            self._at(task, "authorized")
            return None
        from computer.governor import Forbidden
        try:
            d = self.governor.authorize(identity, permission, target=target, reason=reason,
                                        audit_allowed=privileged, require_reason=privileged)
        except Forbidden as e:
            task.authorization = {"state": "denied", "permission": permission, "why": e.why, "decision": e.decision}
            raise TaskError("denied", "FORBIDDEN", e.why) from None
        prior = [task.authorization["permission"]] + task.authorization.get("also", []) \
            if task.authorization.get("state") == "allowed" else []
        task.authorization = {"state": "allowed", "permission": permission, "why": d.why,
                              "decision": d.ref if d.record else None}
        if prior:
            task.authorization["also"] = prior
        if task.stage != "authorized":
            self._at(task, "authorized")
        return d

    # ------------------------------------------------------------------ knowledge: a question

    def _knowledge(self, task: Task, identity) -> None:
        q = task.input.get("question")
        if not isinstance(q, str) or not q.strip() or len(q) > 2000:
            raise TaskError("failed", "INVALID_INPUT", "question must be a non-empty string of at most 2000 characters")
        if self.computer is None:
            raise TaskError("failed", "UNAVAILABLE", "no computer configured")
        self._authorize(task, identity, PERMISSION["knowledge"], "task knowledge")
        self._at(task, "planned")              # the computer's router and planners make the plan (computer/core.py)
        self._at(task, "validated")
        r = self.computer.think(q)
        env = r.envelope or {}
        route = r.route
        task.route = {"handler": "computer.think", "capability": _capability_of(env, r),
                      "why": route.why if route else "", "intent": route.intent if route else "",
                      "faculty": r.faculty}
        task.result = env
        if route is not None and route.intent == "calc" and route.calc:
            task.evidence_refs = [{"kind": "calculation", "expr": route.calc.get("expr"),
                                   "value": route.calc.get("value")}]
        else:
            task.evidence_refs = [{"kind": "fact", "id": e.get("id"), "subject": e.get("subject"),
                                   "relation": e.get("relation"), "corpus_version": e.get("corpus_version")}
                                  for e in env.get("evidence", [])]
        self._at(task, "executed")
        task.verification = verify_envelope(env, r.faculty, route.intent if route else "")
        self._at(task, "verified")
        if not task.verification["ok"]:
            bad = [c["check"] for c in task.verification["checks"] if not c["ok"]]
            raise TaskError("failed", "UNVERIFIED", f"the result didn't pass verification: {', '.join(bad)}")
        task.status = "completed"

    # ------------------------------------------------------------------ system: read-only diagnostics

    def _system(self, task: Task, identity) -> None:
        check = task.input.get("check")
        names = list(SYSTEM_READINGS) if check in ("all", "health", "status") else [check]
        if not all(n in SYSTEM_READINGS for n in names):
            raise TaskError("failed", "INVALID_INPUT", f"check must be one of: all, {', '.join(SYSTEM_READINGS)}")
        if task.input.get("action"):
            # Recorded as asked, refused before authorization.  No handler here can change the machine.
            task.proposed_action = {"action": task.input["action"], "executed": False}
            raise TaskError("unsupported", "UNSUPPORTED", "CDOS takes no action on the host system; it reads and "
                                                          "recommends, and a person acts")
        if self.diagnostics is None:
            raise TaskError("failed", "UNAVAILABLE", "diagnostics is off (diagnostics = false)")
        self._authorize(task, identity, PERMISSION["system"], f"task system {check}")
        task.route = {"handler": "diagnostics.read", "capability": "system.diagnostics@1.0.0", "why": f"check {check}"}
        self._at(task, "planned")
        self._at(task, "validated")
        readings = self.diagnostics.read(names)
        self._at(task, "executed")
        observations = observe(readings, names)
        findings, recommendations = assess(observations)
        task.evidence_refs = [{"kind": "reading", "name": o["name"], "source": o["source"]} for o in observations]
        task.result = {"observations": observations, "findings": findings, "recommendations": recommendations,
                       "predictions": [], "actions": [],
                       "notes": ["predictions: none; each check is a single reading, with no history to trend",
                                 "actions: none; recommendations are for a person to act on"]}
        checks = [_check("read_only", True, "diagnostics.read only reads /proc, /sys and the corpus meta"),
                  _check("no_estimates", all(o["value"] is not None or o["status"] == "unavailable"
                                             for o in observations), "an unreadable value is said to be unavailable"),
                  _check("findings_cite_readings", all(set(f["readings"]) <= {o["name"] for o in observations}
                                                       for f in findings))]
        task.verification = {"ok": all(c["ok"] for c in checks), "checks": checks}
        self._at(task, "verified")
        task.status = "completed"

    # ------------------------------------------------------------------ robotics: one motion in simulation

    def _robotics(self, task: Task, identity) -> None:
        goal = task.input.get("goal")
        if not (isinstance(goal, (list, tuple)) and len(goal) == 2
                and all(isinstance(g, (int, float)) and math.isfinite(g) for g in goal)):
            raise TaskError("failed", "INVALID_INPUT", "goal must be [x, y] in metres")
        if self.robot is None:
            raise TaskError("failed", "UNAVAILABLE", "robotics is off (robotics = false)")
        obs = task.input.get("observations")
        if obs is None and self.executor is not None:
            obs = self.executor.observe()
        # Both permissions before the controller's state is touched.
        if obs:
            self._authorize(task, identity, "robot.observe", "task robot observations")
        self._authorize(task, identity, PERMISSION["robotics"], f"task robot goal {list(goal)}")
        task.route = {"handler": "robot.controller", "capability": "robot.controller@0.1.0",
                      "why": "a goal for the robot"}
        submitted = self.robot.submit(obs) if obs else {"accepted": 0, "refused": []}
        decision = self.robot.request_action(list(goal))
        self._at(task, "planned")
        task.proposed_action = {"candidate": decision.get("candidate"), "policy": decision.get("policy"),
                                "command": decision.get("command")}
        task.evidence_refs = ([{"kind": "observations", "accepted": submitted["accepted"],
                                "refused": submitted["refused"]}] +
                              [{"kind": "safety_envelope", "fingerprint": decision.get("envelope"),
                                "digest": decision.get("digest")}])
        result = {"decision": {k: decision.get(k) for k in ("allowed", "candidate", "policy", "violations",
                                                            "command", "policy_error", "min_clearance")},
                  "executed": None, "world_before": None, "world_after": None}
        task.result = result
        if not decision["allowed"]:
            self._at(task, "validated")
            # The motion is refused.  STOP is the only command that may still be sent.
            if self.executor is not None:
                before = self.executor.pose()
                self.executor.apply(decision["command"])
                after = self.executor.pose()
                result.update(executed="stop", world_before=before, world_after=after,
                              status_after=self.executor.status())
                checks = [_check("stopped", decision["command"] == {"v": 0.0, "omega": 0.0, "duration": 0.0}),
                          _check("did_not_move", _dist(before, after) <= POSE_TOLERANCE,
                                 f"moved {_dist(before, after):.3g} m")]
                task.verification = {"ok": all(c["ok"] for c in checks), "checks": checks}
                self._at(task, "verified")
            rules = sorted({v["rule_id"] for v in decision.get("violations", [])})
            raise TaskError("rejected", "SAFETY", "the safety envelope refused the motion: " +
                            (", ".join(rules) or decision.get("policy_error") or "no allowed candidate"))
        self._at(task, "validated")
        if self.executor is None:
            raise TaskError("failed", "UNAVAILABLE", "no executor: the approved command was not sent")
        before = self.executor.pose()
        cmd = decision["command"]
        predicted = self.executor.predict(before, cmd)
        self.executor.apply(cmd)
        self._at(task, "executed")
        after = self.executor.pose()
        status_after = self.executor.status()
        fed = self.robot.submit(self.executor.observe())       # the outcome goes back to the controller
        result.update(executed="command", world_before=before, world_after=after, predicted=predicted,
                      status_after=status_after, feedback={"accepted": fed["accepted"], "refused": fed["refused"]})
        checks = [_check("command_is_the_approved_candidate", cmd == decision["candidate"]),
                  _check("reached_predicted_pose", _dist(predicted, after) <= POSE_TOLERANCE,
                         f"{_dist(predicted, after):.3g} m from the prediction"),
                  _check("no_collision", status_after["status"] != "collided"),
                  _check("inside_room", status_after["status"] != "out_of_bounds"),
                  _check("feedback_accepted", not fed["refused"], str(fed["refused"])[:200])]
        task.verification = {"ok": all(c["ok"] for c in checks), "checks": checks}
        self._at(task, "verified")
        if not task.verification["ok"]:
            # Stop at once, then report.  STOP needs no permission.
            self.robot.set_e_stop(True, {"name": "task.runner"}, f"{task.task_id} failed verification")
            bad = [c["check"] for c in checks if not c["ok"]]
            raise TaskError("failed", "UNVERIFIED", f"the outcome didn't match the plan: {', '.join(bad)}; "
                                                    "the emergency stop is engaged")
        task.status = "completed"

    # ------------------------------------------------------------------ learning: the model registry

    def _learning(self, task: Task, identity) -> None:
        op = task.input.get("model_op")
        if op not in MODEL_OPS:
            raise TaskError("unsupported", "UNSUPPORTED", f"model_op must be one of: {', '.join(MODEL_OPS)}")
        if self.registry is None:
            raise TaskError("failed", "UNAVAILABLE", "neural_registry not set")
        from computer.neural import registry as R
        task.route = {"handler": f"registry.{op}", "capability": "neural.registry", "why": f"model {op}"}
        if op == "status":
            self._authorize(task, identity, MODEL_OPS[op], "task model status")
            self._at(task, "planned")
            self._at(task, "validated")
            task.result = R.status(self.registry)
            self._at(task, "executed")
            task.verification = {"ok": True, "checks": [_check("read_only", True)]}
            self._at(task, "verified")
            task.status = "completed"
            return
        model_id = task.input.get("model_id") or task.input.get("to") or ""
        if op in ("promote", "rollback") and not model_id:
            raise TaskError("failed", "INVALID_INPUT", f"{op} needs model_id")
        task.proposed_action = {"model_op": op, "model_id": model_id or None}
        before = R.status(self.registry).get("active")
        d = self._authorize(task, identity, MODEL_OPS[op], f"model {model_id or 'active'}",
                            reason=str(task.input.get("reason") or ""), privileged=True)
        self._at(task, "planned")
        # The registry's own gates still decide whether the model may become active.
        try:
            if op == "promote":
                R.check_promotable(self.registry, model_id)
            self._at(task, "validated")
            out = {"promote": lambda: R.promote(self.registry, model_id, approve=True, approver=d.ref),
                   "rollback": lambda: R.rollback(self.registry, model_id, approve=True, approver=d.ref),
                   "deactivate": lambda: R.deactivate(self.registry, approve=True, approver=d.ref)}[op]()
        except R.PromotionRefused as e:
            self.governor.result(d, f"refused: {e}")
            raise TaskError("rejected", "GATES", str(e)) from None
        self._at(task, "executed")
        self.governor.result(d, out.get("status", "done"), {k: out.get(k) for k in ("model_id", "model_sha256",
                                                                                   "eval_sha256") if out.get(k)})
        after = R.status(self.registry).get("active")
        task.result = {"registry": out, "active_before": before, "active_after": after}
        checks = [_check("approved_by_recorded", bool(d.record), "the Governor's audit line for the decision")]
        if op in ("promote", "rollback"):
            checks.append(_check("active_is_the_requested_model", after == model_id, f"active: {after}"))
        else:
            checks.append(_check("nothing_active", after is None, f"active: {after}"))
        task.verification = {"ok": all(c["ok"] for c in checks), "checks": checks}
        self._at(task, "verified")
        task.status = "completed" if task.verification["ok"] else "failed"


# ---------------------------------------------------------------------- verification and assessment


def verify_envelope(env: dict, faculty: str, intent: str) -> dict:
    """Checks that need nothing but the envelope.  A factual result must trace to evidence or a calculation, an
    unknown must stay unknown, and the sampler must carry no evidence."""
    checks = []
    if not env or "canonical_hash" not in env:
        return {"ok": False, "checks": [_check("envelope_present", False, env.get("error", "") if env else "")]}
    checks.append(_check("envelope_hash", envelope_mod.canonical_hash(env) == env["canonical_hash"],
                         "the result is the one the computer sealed"))
    answered = env.get("status") == "answered"
    supported = env.get("certainty") in ("ESTABLISHED", "INFERRED")
    kind = env.get("kind")
    if kind in ("knowledge", "analysis") and answered and supported:
        checks.append(_check("traced_to_evidence", bool(env.get("evidence")), f"{len(env.get('evidence', []))} facts"))
    if kind == "calc":
        checks.append(_check("calculation_has_value", env.get("value") is not None))
    if not answered:
        checks.append(_check("unknown_stays_unknown", env.get("truth") not in ("TRUE", "FALSE")
                             and env.get("certainty") in ("UNKNOWN", "CONFLICT"), f"status {env.get('status')}"))
    if faculty == "instinct":
        checks.append(_check("sampler_is_not_evidence", not env.get("evidence")))
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def observe(readings, names: list) -> list[dict]:
    """Diagnostics readings as observations: a value, a unit, where it was read from, or unavailable."""
    v, src = readings.values, readings.sources
    out = []

    def add(name, value, unit, source_key):
        out.append({"kind": "observation", "name": name, "value": value, "unit": unit,
                    "status": "ok" if value is not None else "unavailable", "source": src.get(source_key, "")})
    for n in names:
        if n == "temperature":
            add("temperature_c", v.get("temperature"), "degC", "temperature")
        elif n == "load":
            add("load", v.get("load"), "fraction of cores", "load")
        elif n == "memory":
            add("mem_avail_mb", v.get("mem_avail_mb"), "MB", "memory")
            add("mem_total_mb", v.get("mem_total_mb"), "MB", "memory")
        elif n == "disk":
            add("disk_free_gb", v.get("disk_free_gb"), "GB", "disk")
            add("disk_total_gb", v.get("disk_total_gb"), "GB", "disk")
        elif n == "battery":
            add("battery_pct", v.get("battery"), "percent", "battery")
        elif n == "corpus":
            add("corpus_facts", v.get("facts"), "facts", "corpus")
        elif n == "uptime":
            add("uptime_s", v.get("uptime_s"), "s", "uptime")
    return out


def assess(observations: list) -> tuple[list, list]:
    """Deterministic thresholds (THRESHOLDS) -> findings, and for each a recommendation a person can act on.
    Nothing here acts."""
    v = {o["name"]: o["value"] for o in observations}
    findings, recs = [], []

    def find(fid, severity, says, readings, rec, threshold):
        findings.append({"kind": "finding", "id": fid, "severity": severity, "says": says, "readings": readings,
                         "threshold": threshold})
        recs.append({"kind": "recommendation", "for": fid, "says": rec, "requires": "a person", "executed": False})
    if v.get("mem_avail_mb") is not None and v.get("mem_total_mb"):
        frac = v["mem_avail_mb"] / v["mem_total_mb"]
        if frac < THRESHOLDS["mem_avail_frac.warn"]:
            crit = frac < THRESHOLDS["mem_avail_frac.critical"]
            find("memory.low", "critical" if crit else "warning",
                 f"{v['mem_avail_mb']:,.0f} of {v['mem_total_mb']:,.0f} MB of memory are available ({frac:.1%})",
                 ["mem_avail_mb", "mem_total_mb"],
                 "Find the process using the most memory (ps aux --sort=-rss | head) and stop or restart it. "
                 "If it is cdos-api, set compute_profile = \"eco\" in config.toml and restart the service.",
                 {"mem_avail_frac.warn": THRESHOLDS["mem_avail_frac.warn"],
                  "mem_avail_frac.critical": THRESHOLDS["mem_avail_frac.critical"]})
    if v.get("disk_free_gb") is not None and v.get("disk_total_gb"):
        frac = v["disk_free_gb"] / v["disk_total_gb"]
        if frac < THRESHOLDS["disk_free_frac.warn"] or v["disk_free_gb"] < THRESHOLDS["disk_free_gb.warn"]:
            find("storage.low", "warning",
                 f"{v['disk_free_gb']:,.1f} of {v['disk_total_gb']:,.1f} GB free where the corpus is kept ({frac:.1%})",
                 ["disk_free_gb", "disk_total_gb"],
                 "Free space before the next growth run. Staged models that failed their gates "
                 "(python3 -m computer.neural status) and old releases under /opt/cdos-computer/releases can be "
                 "removed by an administrator. The audit log and journals must be kept.",
                 {k: THRESHOLDS[k] for k in ("disk_free_frac.warn", "disk_free_gb.warn")})
    if v.get("temperature_c") is not None and v["temperature_c"] >= THRESHOLDS["temperature_c.warn"]:
        find("temperature.high", "warning", f"the processor is at {v['temperature_c']:.0f} °C", ["temperature_c"],
             "Check the cooling and airflow. compute_profile = \"auto\" moves to eco when the board is hot.",
             {"temperature_c.warn": THRESHOLDS["temperature_c.warn"]})
    if v.get("load") is not None and v["load"] >= THRESHOLDS["load.warn"]:
        find("load.high", "warning", f"processor load is {v['load']:.0%} of the cores", ["load"],
             "Look for a runaway process (top). A growth run in progress is expected to load the processor.",
             {"load.warn": THRESHOLDS["load.warn"]})
    return findings, recs


def _capability_of(env: dict, r) -> str:
    used = [f"{c['name']}@{c['version']}" for c in env.get("capabilities", [])]
    return ", ".join(used) or r.faculty


def _dist(a, b) -> float:
    if not a or not b:
        return float("inf")
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


# ---------------------------------------------------------------------- executors


class SimExecutor:
    """The simulated robot (computer/robotics/sim.py) as the thing a robotics task commands.  A hardware adapter
    would offer the same five methods: observe, pose, predict, apply, status."""

    def __init__(self, world):
        self.world = world

    def observe(self) -> list:
        return self.world.observe()

    def pose(self) -> dict:
        w = self.world
        return {"x": w.x, "y": w.y, "heading": w.h, "t": w.t}

    def predict(self, pose: dict, cmd: dict) -> dict:
        """Where the command should leave the robot, by the same kinematics, without moving it."""
        from computer.robotics.world import STEP_S, step
        x, y, h = pose["x"], pose["y"], pose["heading"]
        v, w = float(cmd.get("v", 0.0)), float(cmd.get("omega", 0.0))
        dur = max(float(cmd.get("duration", 0.0)), 0.0)
        if v == 0 and w == 0:
            dur = max(dur, 0.1)
        n = max(1, int(round(dur / STEP_S)))
        for _ in range(n):
            x, y, h = step(x, y, h, v, w, STEP_S)
        return {"x": x, "y": y, "heading": h, "t": pose["t"] + n * STEP_S}

    def apply(self, cmd: dict) -> None:
        self.world.apply(cmd)

    def status(self) -> dict:
        return self.world.status()
