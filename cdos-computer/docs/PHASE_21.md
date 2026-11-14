# CDOS Phase 21, step 1: the task contract (interface audit and first lifecycle)

Phase 21 asks for one coordinated system built from the parts that already exist. This step does two things. It
maps the real call paths in Governor 0.3, and it adds `computer/tasks.py`: one task record and one lifecycle that
routes a request to an existing subsystem. The new module holds no facts or rules and grants no permission. No
existing module was changed.

**Result in one line.** Questions, read-only diagnostics, simulated robot motions and model operations now go through
one lifecycle and come back as one record (`cdos-task/1`). The Governor decides every permission, the safety envelope
decides every motion, and the registry's gates decide every promotion. **512 tests pass**: the 489 from 0.3,
unchanged, plus 23 new in `tests/test_tasks.py`. 4 are skipped (production corpus).

## 1. Interface audit: what is already connected

Baseline before any change: `python3 -m unittest discover -s tests` gave 489 run, OK, 4 skipped, in 95 s on the
build machine. This matches `docs/GOVERNOR.md`.

```
 API (services/api.py) ──► Governor.authorize(peer identity, permission_for(path))
   POST /v1/ask ──► Platform.ask ──► Computer.think(text)                                   core.py
                     router.classify ──► analysis | care | logic (calc, clock, status) | instinct | knowledge
                     knowledge ──► planners (rules, controller, neural) ──► Navigator.run ──► corpus
                     every turn ──► envelope (status, truth, certainty, evidence, canonical_hash)
   POST /v1/robot/observations|action ──► RobotController.submit / request_action
                     policies (components) ──► Envelope.check ──► command or STOP; latch = Governor's e-stop
   POST /v1/robot/e_stop ──► Governor.estop_engage (anyone) / estop_clear (operator + reason)
 CLIs: computer.neural promote|rollback|deactivate, computer.robotics promote-policy, computer.growth answer|run
                     ──► governor.cli_decision ──► registry.promote(approver=decision.ref) ──► promotions.jsonl
 Growth timer ──► Growth.discover / run ──► staging only (never promote)
```

| module (as named in the Phase 21 plan) | what it actually does today | connected to |
|---|---|---|
| `computer/core.py` | `Computer.think(text) -> Reply`, with a sealed result envelope | API `/v1/ask`, growth replay, tools |
| `computer/router.py` | `classify(text) -> Route`, using regex rules and the sensitivity policy | `core.think` only |
| `computer/navigator.py`, `planner.py` | plans written and validated, then executed on the corpus | `core._navigate` |
| `computer/capabilities.py` | declarations, checked against the code at start-up; per-turn usage | `core`, envelope, navigator |
| `computer/state.py` | **analysis working memory saved across restarts**, not machine state | `core.think` (analysis only) |
| `computer/diagnostics.py` | read-only readings from `/proc` and `/sys`, returned as a sentence | `core._logic` ("status" intent) |
| `computer/services/platform.py` | builds the Computer, the Governor and the RobotController; serves health | API |
| `computer/governor.py` | roles, `authorize`, the audit log, the e-stop latch | API, the CLIs, the controller (latch) |
| `computer/robotics/*` | observations become state; a policy candidate goes through the envelope and becomes a command | API, and `simrobot` as a separate process |
| `computer/neural/*`, `growth/*` | train to staging, evaluate, gates; operator promotion | CLIs, growth timer |

**Gaps found** (what this step addresses):

1. **No common result shape outside questions.** Questions have the envelope. Diagnostics return a sentence and a
   trace. Robot decisions return the controller's dict. Model operations return the registry's dict.
2. **Diagnostics doesn't separate observations from conclusions.** A low reading is only said, never flagged.
   Nothing recommends anything.
3. **No one checks that a motion happened as planned.** `simrobot` applies the approved command and asks again. The
   resulting state is never compared with what the command should have done.
4. **Model operations exist only as CLIs.** There is no programmatic path that is authorized and recorded.

**Two corrections to the Phase 21 plan:**
- `computer/state.py` is not "computer awareness". It persists the analysis engine's open calculation. Machine
  readings come from `diagnostics.py`, through `compute.Sensors`. Workstream C builds on `diagnostics.py`.
- The router classifies free text only. Robot goals and model operations aren't sentences the router understands
  ("move the robot to the door" is answered NOT FOUND). Phase 21 routes those as structured requests and leaves the
  deterministic router unchanged.

## 2. The task record

```json
{"format": "cdos-task/1", "task_id": "task-000001-3f2a9c1b", "task_type": "knowledge | system | robotics | learning",
 "status": "completed | denied | rejected | unsupported | failed", "stage": "verified",
 "input": {}, "requester": {"name", "uid", "verified", "via"},
 "route": {"handler", "capability", "why"}, "evidence_refs": [], "proposed_action": null,
 "authorization": {"state": "allowed | denied", "permission", "why", "decision": null},
 "result": null, "verification": {"ok": true, "checks": [{"check", "ok", "why"}]}, "error": null,
 "history": [{"stage", "t"}], "result_hash": "sha256:...", "claim_hash": "sha256:..."}
```

Lifecycle: `received → routed → authorized → planned → validated → executed → verified → recorded`. A task stops at
the first stage that says no, and `stage` and `status` show where and why:

| status | means | what happened |
|---|---|---|
| `completed` | every stage passed | the result is in `result`, checked in `verification` |
| `denied` | the Governor refused the requester | nothing planned, nothing changed; the Governor recorded the denial |
| `rejected` | a deterministic check refused the plan (safety envelope, registry gates) | nothing executed except STOP |
| `unsupported` | CDOS has no capability for it (a host-system action, an unknown model operation) | the request is recorded, nothing else |
| `failed` | bad input, a missing part, or a result that didn't verify | `error.code` says which (`INVALID_INPUT`, `UNAVAILABLE`, `UNVERIFIED`) |

`result_hash` covers the type, status and result (canonical form from `envelope.py`, so timings are excluded).
`claim_hash` (knowledge tasks only) covers what the answer asserts: status, truth, certainty, value, text, evidence
ids and error codes. It leaves out which capabilities produced the answer. That is the right test for "the same
supported answer". Building this found that an active neural model changes the envelope's capability list for
"what is the capital of narnia": the neural planner tries the navigator before the answer falls through to NOT
FOUND. The claim is identical.

Tasks are appended to a hash-chained journal when one is given (`ChainLog(path, fmt="cdos-task/1")`, the same log
class as the experience log and the robot journal). Every outcome is recorded, including failures.

## 3. The four handlers

| type | request | permission | plan, validation | execution | verification |
|---|---|---|---|---|---|
| knowledge | `{"question"}` | `api.ask` | the computer's own router and planners | `Computer.think` | envelope hash re-computed; an answered fact has evidence; a calculation has a value; an unknown is neither TRUE nor FALSE; the sampler carries no evidence |
| system | `{"check": "memory" \| … \| "all"}` | `api.read` | none | `Diagnostics.read` | read-only; unreadable values are `unavailable`, never estimated; every finding cites its readings |
| robotics | `{"goal": [x, y]}` (+ observations, or the executor's) | `robot.observe`, `robot.command` | `RobotController.request_action`: policies propose, `Envelope.check` decides | the approved command, or STOP if rejected, sent to an executor | the command is the approved candidate; the pose matches the kinematic prediction; no collision; inside the room; the feedback observations are accepted. A mismatch **engages the e-stop** |
| learning | `{"model_op": "status" \| "promote" \| "rollback" \| "deactivate"}` | `api.read`, or `model.*` (operator with a reason, audited) | `registry.check_promotable` (every gate) | `registry.promote(approver=decision.ref)` | the active model is the one asked for; the approval is in the Governor's log and `promotions.jsonl` |

System results keep four kinds apart. `observations` are readings with their source. `findings` are threshold
crossings, each with the threshold stated. `recommendations` are things for a person to do, always
`"executed": false`. `predictions` and `actions` are always empty, because a single reading has no trend to predict
and no system-changing action exists. A request with an `action` is `unsupported` for every caller, the operator
included.

The robot executor is an interface with five methods: `observe`, `pose`, `predict`, `apply`, `status`.
`SimExecutor` wraps `SimWorld`. A ROS 2 or direct-driver adapter would implement the same five, but **must not**
be connected until the hardware interlock in `GOVERNOR.md` §6.1 exists.

## 4. Acceptance criteria (from the Phase 21 plan)

| workstream | acceptance test | evidence (`tests/test_tasks.py`) | status |
|---|---|---|---|
| A unified execution | one request reaches the right subsystem without bypassing routing or safety | `TestContract`: all three request types pass all 8 stages; the computer's router stays authoritative (calc, fact, health); bad requests fail cleanly; journaled in a verified chain | **met** |
| A | every factual result traces to evidence or a calculation | `test_every_factual_result_is_traced_or_refused` (60 navigator questions); `test_an_untraceable_answer_is_not_completed` (no evidence, a tampered text, a fabricated TRUE: all `UNVERIFIED`) | **met** |
| A | the same question gives the same supported answer | `test_the_same_question_gives_the_same_result` (result_hash, envelope hash and claim_hash) | **met** |
| B learning without corruption | training can't change an established answer or activate an unapproved model | `TestLearning`: a candidate is trained (ACTIVE.json unchanged, answers unchanged); promotion before evaluation is rejected by the gates; guest, robot and an operator without a reason are denied (3 audited denials); the operator's promotion activates it with `approved_by` pointing at the Governor's record; answers unchanged afterwards; the corpus is byte-identical | **met** (a candidate that loses its gates is accepted as `rejected` too) |
| C computer awareness | a simulated memory or storage problem gets a recommendation and no unauthorized change | `TestSystem`: fake `/proc` with 3.75% memory free gives `memory.low` critical and one unexecuted recommendation, and the fake machine's files are unchanged; low storage and 83.5 °C give findings; a missing sensor is `unavailable`; `drop_caches` is refused for everyone | **met** |
| D robotics | a task produces a simulated action, checks the resulting state, and refuses a blocked action | `TestRobotics`: the motion runs and the pose matches the prediction to 1e-9 m; repeated tasks reach the goal through obstacles; a naive policy's speed violation becomes STOP and the robot doesn't move; an engaged latch refuses every motion; permission is checked before the controller is touched; a wheel slip fails verification and engages the stop | **met** (simulation only) |
| A/D | a model may propose but can't grant itself permission | `test_a_neural_proposal_cant_grant_itself_permission`: `"authorization": "allowed"` and `"roles"` in a request are ignored | **met** |
| E CPU first | reasoning and orchestration work with no GPU, model or remote service | `TestCpuFirst`: no neural registry, no LM, no Governor: questions answer; privileged requests are denied | **met** |
| E | resource use measured on the target CPU | task-layer overhead: about +0.1–0.3 ms per question here (0.55 → 0.65–0.81 ms) | **partly**: measured on the build machine, **not on the Pi or the NUC** |

## 5. Limits

- **Nothing is wired into the API yet.** The runner is a library (`TaskRunner.from_platform(platform)`). The
  existing `/v1/ask` and `/v1/robot/*` endpoints are unchanged. A `/v1/task` endpoint is the next step (§6).
- **The robot executor is the simulator, in process.** `simrobot` still talks to the API as before. With a
  `SimExecutor`, verification is exact because the simulator and the predictor use the same kinematics. A real base
  needs tolerances measured from its odometry.
- **One motion per task.** A goal is reached by repeated tasks, as `simrobot` repeats requests. Multi-step plans
  (a route with several waypoints, a plan the planner writes) are not in this step.
- **Diagnostics thresholds are fixed constants** (`THRESHOLDS` in `tasks.py`). They aren't in `config.toml` yet.
  Each finding states the threshold it used.
- **Model operations cover the reasoning selector only.** The robot policy's `promote-policy` keeps its CLI. Its
  root is `component_root(reg, "robot.policy")`, and adding it is a one-line change once its load function is passed
  through.
- **The task journal has the same limit as every other log here.** The service account can rewrite it
  consistently (`GOVERNOR.md` §5).

## 6. Next steps (weeks 3–6 of the plan)

1. **Week 3, evidence and verification.** Add a `/v1/task` endpoint, with the same Governor check and the identity
   from `SO_PEERCRED`. Add `task.runner` to `capabilities.DECLARATIONS`. Then test missing facts, contradictory
   claims (the `conflict` status) and a capability switched off (`Registry.disable`).
2. **Week 4, governor integration.** Put the robot-policy promotion behind `model_op`. Test a task under a degraded
   Governor (a broken audit log), and a restart in the middle of a task: the journal shows `received` with no
   `recorded`.
3. **Week 5, robotics.** Add multi-waypoint goals, and run `simrobot` against `/v1/task` in place of
   `/v1/robot/action`. Add verification tolerances for a noisy executor.
4. **Week 6, regression.** Compare `claim_hash` across the whole regression fixture set with neural on and off, and
   benchmark the task layer on the Pi Zero 2 W and the NUC.

## 7. Commands

```sh
python3 -m unittest tests.test_tasks -v           # about 13 s; the learning test trains one candidate (numpy)
python3 -m unittest discover -s tests             # 512 tests, about 100 s
```

```python
from computer.governor import local_identity
from computer.services.platform import Platform
from computer.tasks import TaskRunner
runner = TaskRunner.from_platform(Platform(cfg))
runner.run({"question": "how many moons does jupiter have"}, local_identity()).to_json()
runner.run({"check": "all"}, local_identity()).to_json()["result"]["findings"]
```
