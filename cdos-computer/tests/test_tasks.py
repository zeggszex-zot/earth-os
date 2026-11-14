"""Phase 21, step 1: the task record and lifecycle (computer/tasks.py; docs/PHASE_21.md).  One class per workstream:

    A  unified task execution            TestContract
    B  learning can't corrupt knowledge  TestLearning          (needs numpy)
    C  computer awareness, read-only     TestSystem
    D  robotics through the lifecycle    TestRobotics
    E  CPU-first, neural optional        TestCpuFirst

Uses the tiny corpus (tests/mini_corpus.py) and simulated /proc and /sys folders.  Nothing here touches the real
machine's state, a real robot or the network."""

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from test_platform import HAVE_NUMPY, HELD, REGR, DATA, QUIET, TMP, cfg, controller, copy_registry, digest  # noqa: E402
from test_governor import ALICE, GUEST, ROBOT, gov                                                        # noqa: E402

from computer.core import Computer                                                  # noqa: E402
from computer.diagnostics import Diagnostics                                        # noqa: E402
from computer.neural.experience import ChainLog, read_chain, verify                 # noqa: E402
from computer.robotics.sim import SimWorld                                          # noqa: E402
from computer.tasks import STAGES, TASK_FORMAT, SimExecutor, TaskRunner, verify_envelope  # noqa: E402

_C = {}


def computer() -> Computer:
    if "c" not in _C:
        _C["c"] = Computer(cfg())
    return _C["c"]


def fake_machine(mem_total_kb=8_000_000, mem_avail_kb=6_000_000, temp_millic=None) -> tuple[Path, Path]:
    base = Path(tempfile.mkdtemp(dir=TMP.name))
    proc, sysd = base / "proc", base / "sys"
    proc.mkdir()
    (proc / "meminfo").write_text(f"MemTotal:       {mem_total_kb} kB\nMemFree:        {mem_avail_kb // 2} kB\n"
                                  f"MemAvailable:   {mem_avail_kb} kB\n")
    (proc / "uptime").write_text("12345.6 40000.0\n")
    if temp_millic is not None:
        (sysd / "class" / "thermal" / "thermal_zone0").mkdir(parents=True)
        (sysd / "class" / "thermal" / "thermal_zone0" / "temp").write_text(f"{temp_millic}\n")
    else:
        sysd.mkdir()
    return proc, sysd


def robot_setup(seed=3, obstacles=0, policies=("robot.policy_expert",), cleared=True):
    g = gov()
    if cleared:
        g.estop_clear(ALICE, "test bench")
    ctl, _ = controller(policies)
    ctl.latch = g.latch
    ctl.clock = time.monotonic          # observations are stamped on arrival; the envelope checks their age
    world = SimWorld(seed, n_obstacles=obstacles)
    return TaskRunner(computer=computer(), governor=g, robot=ctl, executor=SimExecutor(world)), g, ctl, world


# ---------------------------------------------------------------------- A the contract


class TestContract(unittest.TestCase):
    def test_every_task_type_goes_through_one_lifecycle(self):
        tr, g, ctl, world = robot_setup()
        proc, sysd = fake_machine()
        tr.diagnostics = Diagnostics(str(sysd), str(proc), corpus=computer().corpus)
        for req, who, kind in (({"question": "what is 12 times 7"}, GUEST, "knowledge"),
                               ({"check": "memory"}, GUEST, "system"),
                               ({"goal": list(world.goal)}, ROBOT, "robotics")):
            t = tr.run(req, who).to_json()
            self.assertEqual(t["format"], TASK_FORMAT)
            self.assertEqual(t["task_type"], kind)
            self.assertEqual(t["status"], "completed", t["error"])
            self.assertEqual([h["stage"] for h in t["history"]], list(STAGES), kind)
            self.assertTrue(t["verification"]["ok"])
            self.assertEqual(t["authorization"]["state"], "allowed")
            self.assertTrue(t["evidence_refs"], kind)
            self.assertTrue(t["result_hash"].startswith("sha256:"))
            for k in ("task_id", "input", "requester", "route", "proposed_action", "result", "error"):
                self.assertIn(k, t)

    def test_existing_routing_stays_authoritative(self):
        """The runner doesn't re-route a question: the computer's router decides, and the record says what it chose."""
        tr = TaskRunner(computer=computer(), governor=gov())
        cases = {"what is 12 times 7": ("calc", "logic"), "how many moons does jupiter have": ("fact", "knowledge"),
                 "should I stop taking my medicine": ("health", "knowledge")}
        for q, (intent, faculty) in cases.items():
            t = tr.run({"question": q}, GUEST)
            self.assertEqual((t.route["intent"], t.route["faculty"]), (intent, faculty), q)
            self.assertEqual(t.result["text"], computer().think(q).text)
        t = tr.run({"question": "what is 12 times 7"}, GUEST)
        self.assertEqual(t.evidence_refs, [{"kind": "calculation", "expr": "12*7", "value": 84}])

    def test_bad_requests_fail_cleanly(self):
        tr = TaskRunner(computer=computer(), governor=gov())
        for req, code in (({}, "INVALID_INPUT"), ({"question": "x", "check": "memory"}, "INVALID_INPUT"),
                          ({"type": "telepathy", "question": "x"}, "INVALID_INPUT"),
                          ({"type": "system", "question": "x"}, "INVALID_INPUT"),
                          ({"question": ""}, "INVALID_INPUT"), ({"goal": [1, 2]}, "UNAVAILABLE"),
                          ({"goal": "north"}, "INVALID_INPUT"), ({"check": "mood"}, "INVALID_INPUT"),
                          ({"model_op": "train"}, "UNSUPPORTED")):
            t = tr.run(req, GUEST)
            self.assertIn(t.status, ("failed", "unsupported"), req)
            self.assertEqual(t.error["code"], code, req)
            self.assertNotEqual(t.status, "completed")

    def test_the_same_question_gives_the_same_result(self):
        tr = TaskRunner(computer=computer(), governor=gov())
        for q in ("how many moons does jupiter have", "what orbits the sun", "what is the capital of narnia",
                  "what is 12 times 7"):
            a, b = tr.run({"question": q}, GUEST).to_json(), tr.run({"question": q}, GUEST).to_json()
            self.assertEqual(a["result_hash"], b["result_hash"], q)
            self.assertEqual(a["result"]["canonical_hash"], b["result"]["canonical_hash"], q)
            self.assertEqual(a["claim_hash"], b["claim_hash"], q)
            self.assertNotEqual(a["task_id"], b["task_id"])

    def test_tasks_are_journaled_in_a_verified_chain(self):
        path = Path(tempfile.mkdtemp(dir=TMP.name)) / "tasks.jsonl"
        tr = TaskRunner(computer=computer(), governor=gov(), journal=ChainLog(path, fmt=TASK_FORMAT))
        tr.run({"question": "what is 12 times 7"}, GUEST)
        tr.run({"goal": [1.0, 1.0]}, GUEST)                         # fails (no robot): recorded too
        recs = read_chain(path)
        self.assertEqual([r["status"] for r in recs], ["completed", "failed"])
        self.assertTrue(verify(path)["ok"])

    def test_every_factual_result_is_traced_or_refused(self):
        tr = TaskRunner(computer=computer(), governor=gov())
        for q in [json.loads(line)["question"] for line in (ROOT / "tests" / "navigator_questions.jsonl").read_text().splitlines()][:60]:
            t = tr.run({"question": q}, GUEST)
            self.assertEqual(t.status, "completed", (q, t.error))
            env = t.result
            if env["status"] == "answered" and env["kind"] == "knowledge" and env["certainty"] != "UNKNOWN":
                self.assertTrue(t.evidence_refs, q)
                self.assertEqual({r["id"] for r in t.evidence_refs}, {e["id"] for e in env["evidence"]})

    def test_an_untraceable_answer_is_not_completed(self):
        """A result that claims a fact with no evidence (or a tampered envelope) fails verification."""
        from computer import envelope as E
        good = computer().think("how many moons does jupiter have").envelope
        bare = E.seal({**good, "evidence": []})
        self.assertFalse(verify_envelope(bare, "knowledge", "fact")["ok"])
        tampered = {**good, "text": "Jupiter has 12 moons."}
        self.assertFalse(verify_envelope(tampered, "knowledge", "fact")["ok"])
        fabricated = E.seal({**computer().think("what is the capital of narnia").envelope, "truth": "TRUE"})
        self.assertFalse(verify_envelope(fabricated, "knowledge", "fact")["ok"])

        class Liar:
            diagnostics = None

            def think(self, q):
                r = computer().think(q)
                r.envelope = bare
                return r
        t = TaskRunner(computer=Liar(), governor=gov()).run({"question": "how many moons does jupiter have"}, GUEST)
        self.assertEqual((t.status, t.error["code"]), ("failed", "UNVERIFIED"))


# ---------------------------------------------------------------------- B learning


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestLearning(unittest.TestCase):
    QUESTIONS = ("how many moons does jupiter have", "what orbits the sun", "which is bigger, jupiter or saturn",
                 "what is the capital of narnia", "what is 12 times 7")

    def answers(self, reg=None):
        c = Computer(cfg(neural_registry=str(reg), neural_experience=False) if reg else cfg())
        tr = TaskRunner(computer=c, governor=gov())
        return {q: tr.run({"question": q}, GUEST).to_json()["claim_hash"] for q in self.QUESTIONS}

    def test_training_promotion_and_denials_never_change_a_fact(self):
        from computer.neural.evaluate import evaluate
        from computer.neural.train import train_to_staging
        reg = copy_registry("tasks-reg")
        active = (reg / "ACTIVE.json").read_bytes()
        rules_only, with_model = self.answers(), self.answers(reg)
        self.assertEqual(rules_only, with_model)
        corpus = digest(DATA)

        d = train_to_staging(reg, DATA, heldout=HELD, regression=REGR, log=QUIET, seed=7)
        self.assertEqual((reg / "ACTIVE.json").read_bytes(), active)          # training only stages
        self.assertEqual(self.answers(reg), rules_only)

        g = gov(state=Path(tempfile.mkdtemp(dir=TMP.name)))
        tr = TaskRunner(computer=computer(), governor=g, registry=reg)
        # not evaluated yet: an operator can't push it past the registry's gates
        t = tr.run({"model_op": "promote", "model_id": d.name, "reason": "try it"}, ALICE)
        self.assertEqual((t.status, t.error["code"]), ("rejected", "GATES"), t.error)
        self.assertEqual((reg / "ACTIVE.json").read_bytes(), active)

        evaluate(reg, d.name, DATA, HELD, REGR, log=QUIET)
        for who, reason, why in ((GUEST, "please", "not granted"), (ROBOT, "please", "not granted"),
                                 (ALICE, "", "needs a reason")):
            t = tr.run({"model_op": "promote", "model_id": d.name, "reason": reason}, who)
            self.assertEqual(t.status, "denied", who)
            self.assertIn(why, t.error["message"])
            self.assertEqual((reg / "ACTIVE.json").read_bytes(), active)
        denials = [r for r in g.audit.records() if r.get("outcome") == "denied"]
        self.assertEqual(len(denials), 3)

        st = tr.run({"model_op": "status"}, GUEST)
        self.assertEqual(st.status, "completed")
        self.assertIn(d.name, [m["model_id"] for m in st.result["staged"]])

        t = tr.run({"model_op": "promote", "model_id": d.name, "reason": "beats the active model"}, ALICE)
        if t.status == "rejected":                  # a candidate may honestly lose to the active model
            self.assertEqual(t.error["code"], "GATES")
            self.assertEqual((reg / "ACTIVE.json").read_bytes(), active)
        else:
            self.assertEqual(t.status, "completed", t.error)
            self.assertEqual(t.result["active_after"], d.name)
            last = json.loads((reg / "promotions.jsonl").read_text().splitlines()[-1])
            self.assertEqual(last["approved_by"]["governor_seq"], t.authorization["decision"]["governor_seq"])
            self.assertEqual(last["approved_by"]["approver"]["name"], "alice")
            self.assertTrue(g.reverify())
        self.assertEqual(self.answers(reg), rules_only)
        self.assertEqual(digest(DATA), corpus)                                # nothing wrote the corpus


# ---------------------------------------------------------------------- C the computer itself, read-only


class TestSystem(unittest.TestCase):
    def runner(self, **machine):
        proc, sysd = fake_machine(**machine)
        tr = TaskRunner(computer=computer(), governor=gov(),
                        diagnostics=Diagnostics(str(sysd), str(proc), corpus=computer().corpus))
        return tr, proc.parent

    def test_low_memory_is_found_and_a_recommendation_made_but_nothing_done(self):
        tr, base = self.runner(mem_total_kb=4_000_000, mem_avail_kb=150_000)
        before = digest(base)
        t = tr.run({"check": "memory"}, GUEST)
        self.assertEqual(t.status, "completed", t.error)
        f = t.result["findings"]
        self.assertEqual([(x["id"], x["severity"]) for x in f], [("memory.low", "critical")])
        self.assertEqual(f[0]["readings"], ["mem_avail_mb", "mem_total_mb"])
        recs = t.result["recommendations"]
        self.assertEqual(len(recs), 1)
        self.assertFalse(recs[0]["executed"])
        self.assertEqual(t.result["actions"], [])
        self.assertIsNone(t.proposed_action)
        self.assertEqual(digest(base), before)                       # the machine's files are untouched
        kinds = {o["kind"] for o in t.result["observations"]}
        self.assertEqual(kinds, {"observation"})
        self.assertTrue(all(r["source"].endswith("meminfo, getrusage") for r in t.evidence_refs))

    def test_healthy_memory_gives_no_finding(self):
        tr, _ = self.runner(mem_total_kb=4_000_000, mem_avail_kb=3_000_000)
        t = tr.run({"check": "memory"}, GUEST)
        self.assertEqual((t.status, t.result["findings"], t.result["recommendations"]), ("completed", [], []))

    def test_low_storage_and_heat(self):
        from collections import namedtuple
        du = namedtuple("du", "total used free")(64e9, 63.5e9, 0.5e9)
        tr, _ = self.runner(temp_millic=83500)
        with mock.patch("computer.diagnostics.shutil.disk_usage", return_value=du):
            t = tr.run({"check": "all"}, GUEST)
        self.assertEqual(t.status, "completed", t.error)
        ids = {x["id"] for x in t.result["findings"]}
        self.assertIn("storage.low", ids)
        self.assertIn("temperature.high", ids)
        self.assertTrue(all(not r["executed"] for r in t.result["recommendations"]))

    def test_an_unreadable_value_is_unavailable_never_estimated(self):
        tr, _ = self.runner()                                        # no thermal zone in the fake /sys
        t = tr.run({"check": "temperature"}, GUEST)
        self.assertEqual(t.status, "completed")
        self.assertEqual(t.result["observations"][0]["status"], "unavailable")
        self.assertIsNone(t.result["observations"][0]["value"])
        self.assertEqual(t.result["findings"], [])

    def test_system_changing_actions_are_refused_for_everyone(self):
        tr, base = self.runner(mem_total_kb=4_000_000, mem_avail_kb=150_000)
        before = digest(base)
        for who in (GUEST, ALICE):
            t = tr.run({"check": "memory", "action": "drop_caches"}, who)
            self.assertEqual((t.status, t.error["code"]), ("unsupported", "UNSUPPORTED"))
            self.assertFalse(t.proposed_action["executed"])
            self.assertIsNone(t.result)
        self.assertEqual(digest(base), before)


# ---------------------------------------------------------------------- D robotics, simulated


class TestRobotics(unittest.TestCase):
    def test_an_allowed_motion_is_executed_and_checked_against_the_world(self):
        tr, g, ctl, world = robot_setup()
        t = tr.run({"goal": list(world.goal)}, ROBOT)
        self.assertEqual(t.status, "completed", t.error)
        r = t.result
        self.assertTrue(r["decision"]["allowed"])
        self.assertEqual(r["executed"], "command")
        self.assertNotEqual(r["world_before"], r["world_after"])
        self.assertAlmostEqual(r["predicted"]["x"], r["world_after"]["x"], places=9)
        self.assertEqual(r["status_after"]["status"], "running")
        self.assertEqual({c["check"] for c in t.verification["checks"] if c["ok"]},
                         {"command_is_the_approved_candidate", "reached_predicted_pose", "no_collision",
                          "inside_room", "feedback_accepted"})

    def test_repeated_tasks_reach_the_goal(self):
        tr, g, ctl, world = robot_setup(seed=5, obstacles=3)
        for _ in range(400):
            if world.status()["status"] != "running":
                break
            t = tr.run({"goal": list(world.goal)}, ROBOT)
            self.assertIn(t.status, ("completed", "rejected"), t.error)
        self.assertEqual(world.status()["status"], "reached")

    def test_a_motion_the_envelope_blocks_is_refused_and_the_robot_stays_put(self):
        tr, g, ctl, world = robot_setup(policies=("robot.policy_naive",))
        t = tr.run({"goal": list(world.goal)}, ROBOT)
        self.assertEqual((t.status, t.error["code"]), ("rejected", "SAFETY"))
        self.assertIn("speed.max", t.error["message"])
        self.assertEqual(t.result["executed"], "stop")
        self.assertEqual(t.result["decision"]["command"], {"v": 0.0, "omega": 0.0, "duration": 0.0})
        self.assertTrue(t.verification["ok"])                         # did_not_move, stopped
        self.assertEqual((world.x, world.y), (t.result["world_before"]["x"], t.result["world_before"]["y"]))

    def test_an_engaged_stop_refuses_every_motion(self):
        tr, g, ctl, world = robot_setup(cleared=False)                # a fresh Governor starts engaged
        t = tr.run({"goal": list(world.goal)}, ROBOT)
        self.assertEqual(t.status, "rejected")
        self.assertIn("e_stop", t.error["message"])
        g.estop_clear(ALICE, "inspected")
        self.assertEqual(tr.run({"goal": list(world.goal)}, ROBOT).status, "completed")
        g.estop_engage(GUEST, "someone walked in")
        self.assertEqual(tr.run({"goal": list(world.goal)}, ROBOT).status, "rejected")

    def test_permission_comes_before_the_controller_is_touched(self):
        tr, g, ctl, world = robot_setup()
        for who in (GUEST, ALICE.__class__(None, "unverified", False, "tcp-loopback")):
            t = tr.run({"goal": list(world.goal)}, who)
            self.assertEqual(t.status, "denied")
            self.assertEqual(t.authorization["state"], "denied")
        self.assertEqual(ctl.latest, {})
        self.assertEqual(ctl.decisions, 0)
        self.assertEqual(world.t, 0.0)

    def test_an_outcome_that_doesnt_match_the_plan_stops_the_robot(self):
        tr, g, ctl, world = robot_setup()

        class Slipping(SimExecutor):
            def apply(self, cmd):
                super().apply(cmd)
                self.world.x += 0.05                                 # a wheel slipped
        tr.executor = Slipping(world)
        t = tr.run({"goal": list(world.goal)}, ROBOT)
        self.assertEqual((t.status, t.error["code"]), ("failed", "UNVERIFIED"))
        self.assertIn("reached_predicted_pose", t.error["message"])
        self.assertTrue(g.latch.engaged)
        self.assertEqual(tr.run({"goal": list(world.goal)}, ROBOT).status, "rejected")

    def test_a_neural_proposal_cant_grant_itself_permission(self):
        """The learned policy is just another proposer: the runner asks the Governor, never the request."""
        tr, g, ctl, world = robot_setup()
        t = tr.run({"goal": list(world.goal), "authorization": "allowed", "roles": ["operator"]}, GUEST)
        self.assertEqual(t.status, "denied")
        t = tr.run({"model_op": "deactivate", "reason": "x", "approved": True}, GUEST)
        self.assertIn(t.status, ("denied", "failed"))
        self.assertNotEqual(t.status, "completed")


# ---------------------------------------------------------------------- E CPU first, neural optional


class TestCpuFirst(unittest.TestCase):
    def test_no_model_no_llm_no_governor_still_answers(self):
        c = Computer(cfg(neural_registry="", llm_endpoint="", llm_mode="off"))
        self.assertIsNone(c.neural)
        tr = TaskRunner(computer=c)                                  # no Governor: read and ask only
        for q in ("what is 12 times 7", "how many moons does jupiter have", "what orbits the sun"):
            t = tr.run({"question": q}, GUEST)
            self.assertEqual(t.status, "completed", t.error)
            self.assertEqual(t.result["status"], "answered")
        t = tr.run({"model_op": "deactivate", "reason": "x"}, ALICE)
        self.assertIn(t.status, ("denied", "failed"))

    def test_without_a_governor_nothing_privileged_happens(self):
        tr, g, ctl, world = robot_setup()
        tr.governor = None
        t = tr.run({"goal": list(world.goal)}, ROBOT)
        self.assertEqual((t.status, t.error["code"]), ("denied", "FORBIDDEN"))
        self.assertEqual(world.t, 0.0)

    def test_overhead_of_the_task_layer(self):
        """The record and checks cost little next to the question itself (measured here, not on the Pi)."""
        tr = TaskRunner(computer=computer(), governor=gov())
        q = "how many moons does jupiter have"
        n = 30
        t0 = time.perf_counter()
        for _ in range(n):
            computer().think(q)
        bare = (time.perf_counter() - t0) / n
        t0 = time.perf_counter()
        for _ in range(n):
            tr.run({"question": q}, GUEST)
        wrapped = (time.perf_counter() - t0) / n
        print(f"\n  task overhead: think {bare * 1000:.2f} ms, task {wrapped * 1000:.2f} ms, "
              f"+{(wrapped - bare) * 1000:.2f} ms")
        self.assertLess(wrapped - bare, max(0.005, bare))


if __name__ == "__main__":
    unittest.main()
