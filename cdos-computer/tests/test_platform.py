"""CDOS Neural Platform 0.2: one test class per acceptance criterion (docs/PLATFORM_0_2.md), plus the parts they rest on.

    1 existing tests pass                   the whole suite (not here: python3 -m unittest discover -s tests)
    2 runs with no neural model             TestNoModel
    3 shared, versioned neural interface    TestInterface
    4 Growth Engine finds and tasks         TestGrowthDiscovers
    5 a task stages a model, nothing else   TestGrowthRuns
    6 evaluation rejects a bad candidate    TestBadCandidate
    7 a capability without engine changes   TestPlugins
    8 a simulated robot, rejected motions   TestRobot, TestApi
    9 services keep state across a restart  TestInstallAndRestart

Uses the tiny corpus (tests/mini_corpus.py).  Training needs numpy; those tests are skipped without it."""

import copy
import hashlib
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import mini_corpus                                                                   # noqa: E402
from computer import capabilities as caps                                            # noqa: E402
from computer.config import Config                                                   # noqa: E402
from computer.core import Computer                                                   # noqa: E402
from computer.neural import registry as R                                            # noqa: E402
from computer.neural.components import (Component, ComponentFailure, ComponentRegistry, ComponentSpec, Field,  # noqa: E402
                                        InterfaceError, IOSpec)
from computer.neural.experience import verify                                        # noqa: E402

try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

TMP = tempfile.TemporaryDirectory()
DATA = mini_corpus.build(Path(TMP.name) / "data")
HELD = [str(ROOT / "tests" / f) for f in ("controller_heldout.jsonl", "lexicon_heldout.jsonl")]
REGR = [str(ROOT / "tests" / f) for f in ("navigator_questions.jsonl", "lexical_questions.jsonl",
                                          "lexical_paraphrases.jsonl")]
QUIET = lambda *a: None                                                               # noqa: E731
ENGINE = ["core.py", "navigator.py", "planner.py", "plan.py", "corpus.py", "controller.py", "logic.py", "rules.py"]
_S = {}


def digest(base) -> str:
    h = hashlib.sha256()
    for p in sorted(Path(base).rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(base)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


CORPUS = digest(DATA)


def cfg(**kw):
    base = dict(corpus=str(DATA), llm_mode="off", llm_endpoint="", log_file="", navigator_controller="", state_file="")
    base.update(kw)
    return Config(**base)


def trained_registry() -> Path:
    """A registry with a promoted reasoning selector (trained once for the module)."""
    if "reg" not in _S:
        from computer.neural.evaluate import evaluate
        from computer.neural.train import train_to_staging
        reg = Path(TMP.name) / "reg"
        d = train_to_staging(reg, DATA, heldout=HELD, regression=REGR, log=QUIET)
        evaluate(reg, d.name, DATA, HELD, REGR, log=QUIET)
        R.promote(reg, d.name, approve=True)
        _S.update(reg=reg, id=d.name)
    return _S["reg"]


def copy_registry(name: str) -> Path:
    dest = Path(TMP.name) / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(trained_registry(), dest)
    return dest


def policy_registry() -> Path:
    """A registry with a promoted robot policy."""
    if "preg" not in _S:
        from computer.robotics.learn import evaluate_policy, promote_policy, train_policy
        reg = Path(TMP.name) / "preg"
        d = train_policy(reg, seed=0, rooms=40)
        _S["prep"] = evaluate_policy(reg, d.name, rooms=20)
        promote_policy(reg, d.name, approve=True)
        _S.update(preg=reg, pid=d.name)
    return _S["preg"]


# ---------------------------------------------------------------------- 2 no model

class TestNoModel(unittest.TestCase):
    def test_no_registry_configured(self):
        c = Computer(cfg())
        self.assertIsNone(c.neural)
        self.assertEqual([p.label for p in c.planners], ["rules"])
        self.assertEqual(c.components.names(), [])
        self.assertIn("2,100,000", c.think("What's the population of the capital of France?").text)
        self.assertIn("84", c.think("what is 12 times 7").text)

    def test_an_empty_registry(self):
        c = Computer(cfg(neural_registry=str(Path(TMP.name) / "empty-reg")))
        self.assertIsNone(c.neural)
        self.assertIn("no neural registry", c.neural_error)
        self.assertIn("Paris", c.think("france capital?").text)

    def test_the_platform_starts_with_nothing_optional(self):
        from computer.services.platform import Platform
        p = Platform(cfg(robotics=False))
        h = p.health()
        self.assertEqual((h["neural"]["model_id"], h["robotics"], h["components"]), (None, None, []))


# ---------------------------------------------------------------------- 3 the shared interface

class Doubler(Component):
    spec = ComponentSpec("test.doubler", "1.2.0", "deterministic",
                         IOSpec("test.number", "1.0.0", (Field("x", "float", min=-10, max=10),
                                                          Field("label", "str", required=False, choices=("a", "b")))),
                         IOSpec("test.doubled", "2.0.0", (Field("y", "float"),)))

    def run(self, i):
        return {"y": 2 * i["x"]}


class TestInterface(unittest.TestCase):
    def test_inputs_are_checked(self):
        d = Doubler()
        self.assertEqual(d({"x": 3}), {"y": 6})
        self.assertEqual(d({"x": 1.5, "label": "a"}), {"y": 3.0})
        for bad, why in (({}, "missing x"), ({"x": 1, "z": 2}, "unknown"), ({"x": "1"}, "finite number"),
                         ({"x": float("nan")}, "finite"), ({"x": 11}, "above the maximum"), ({"x": True}, "finite"),
                         ({"x": 1, "label": "c"}, "one of"), ([1], "object")):
            with self.assertRaises(InterfaceError) as cm:
                d(bad)
            self.assertIn(why, str(cm.exception), bad)

    def test_outputs_are_checked_and_failures_isolated(self):
        class Wrong(Doubler):
            def run(self, i):
                return {"y": "six"}

        class Broken(Doubler):
            def run(self, i):
                raise ZeroDivisionError("oops")
        with self.assertRaises(ComponentFailure) as cm:
            Wrong()({"x": 3})
        self.assertIn("output doesn't match test.doubled@2.0.0", str(cm.exception))
        with self.assertRaises(ComponentFailure) as cm:
            Broken()({"x": 3})
        self.assertIn("ZeroDivisionError", str(cm.exception))

    def test_versions_and_registration(self):
        reg = ComponentRegistry()
        reg.register(Doubler())
        self.assertEqual(reg.call("test.doubler", {"x": 1}, input_major=1, output_major=2), {"y": 2})
        with self.assertRaises(ComponentFailure):
            reg.call("test.doubler", {"x": 1}, input_major=2)       # the caller speaks another contract
        with self.assertRaises(ComponentFailure):
            reg.call("test.doubler", {"x": 1}, output_major=1)
        with self.assertRaises(InterfaceError):
            reg.register(Doubler())                                   # once only
        with self.assertRaises(ComponentFailure) as cm:
            reg.call("test.nothing", {})
        self.assertIn("not registered", str(cm.exception))
        for bad in (lambda: ComponentSpec("NoDots", "1.0.0", "neural", Doubler.spec.input, Doubler.spec.output),
                    lambda: ComponentSpec("a.b", "1.0", "neural", Doubler.spec.input, Doubler.spec.output),
                    lambda: ComponentSpec("a.b", "1.0.0", "magic", Doubler.spec.input, Doubler.spec.output),
                    lambda: IOSpec("a.b", "1.0.0", (Field("x", "int"), Field("x", "int"))),
                    lambda: Field.from_json({"name": "x", "type": "complex"})):
            with self.assertRaises(InterfaceError):
                bad()
        self.assertEqual(IOSpec.from_json(Doubler.spec.input.to_json()), Doubler.spec.input)

    @unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
    def test_every_neural_part_is_a_versioned_component(self):
        from computer.services.platform import Platform
        reg = copy_registry("iface-reg")
        shutil.copytree(policy_registry() / "components", reg / "components")
        p = Platform(cfg(neural_registry=str(reg), neural_experience=False, robotics=True,
                         plugins_dir=str(ROOT / "plugins")))
        names = p.components.names()
        self.assertEqual(names, ["reasoning.selector", "robot.policy", "robot.policy_expert", "sensor.thermometer"])
        sel = p.components.call("reasoning.selector", {"question": "jupiter vs saturn, which is heavier"}, 1, 1)
        self.assertEqual(sel["top"], "compare_more")
        self.assertAlmostEqual(sum(sel["scores"].values()), 1.0, places=4)
        d = {c["name"]: c for c in p.components.describe()}
        self.assertEqual(d["reasoning.selector"]["model"]["id"], _S["id"])
        self.assertEqual(d["robot.policy"]["model"]["id"], _S["pid"])
        self.assertEqual(d["robot.policy"]["input"]["name"] + "@" + d["robot.policy"]["input"]["version"],
                         "robot.state@1.0.0")
        with self.assertRaises(InterfaceError):
            p.components.call("reasoning.selector", {"question": 42})

    @unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
    def test_one_lifecycle_for_every_component(self):
        """The robot policy is staged, evaluated and promoted (with approval) through the same registry code."""
        from computer.robotics.learn import COMPONENT, load_manifest
        root = R.component_root(policy_registry(), COMPONENT)
        self.assertEqual(root, policy_registry() / "components" / "robot.policy")
        self.assertEqual(json.loads((root / "ACTIVE.json").read_text())["model_id"], _S["pid"])
        audit = [json.loads(l) for l in (root / "promotions.jsonl").read_text().splitlines()]
        self.assertEqual([a["action"] for a in audit], ["promote"])
        self.assertTrue(_S["prep"]["passed"], _S["prep"]["gates"])
        st = R.status(root, load=load_manifest)
        self.assertEqual(st["active"], _S["pid"])
        self.assertEqual(R.component_root(Path("/x"), "reasoning.selector"), Path("/x"))
        with self.assertRaises(R.PromotionRefused):
            R.component_root(Path("/x"), "../../etc")


# ---------------------------------------------------------------------- 4-5 the Growth Engine

def growth_setup(name: str):
    """A registry with an active selector, and a computer that has logged some failures."""
    reg = copy_registry(name)
    log = Path(TMP.name) / f"{name}-turns.jsonl"
    c = Computer(cfg(neural_registry=str(reg), log_file=str(log)))
    for q in ("which things orbit jupiter", "glorbs", "which things orbit the sun", "glorbs", "owl diet",
              "is mars red", "what is 12 times 7"):
        c.think(q)
    from computer.growth.engine import Growth
    return reg, Growth(reg, DATA, log)


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestGrowthDiscovers(unittest.TestCase):
    def test_it_finds_unanswered_questions_and_makes_tasks(self):
        reg, g = growth_setup("grow-find")
        found = {f.question: f for f in g.discover()}
        self.assertNotIn("owl diet", found)                         # answered
        self.assertNotIn("what is 12 times 7", found)               # not a knowledge question
        self.assertEqual(found["glorbs"].count, 2)                  # in both logs, counted once per turn
        self.assertEqual(found["glorbs"].category, "corpus_gap")
        self.assertEqual(found["which things orbit jupiter"].category, "selector")
        top = found["which things orbit jupiter"].candidates[0]
        self.assertEqual(top["action"], "reverse")                   # the answer that covers the question comes first
        self.assertIn("Ganymede", top["answer"])
        self.assertEqual(found["is mars red"].category, "missing_relation")
        self.assertTrue({s["log"] for s in found["glorbs"].sources} == {"turns", "experience"})
        tasks = g.create_tasks(list(found.values()))
        kinds = {t["kind"]: t for t in tasks}
        self.assertEqual(set(kinds), {"retrain_selector", "corpus_gap"})
        self.assertEqual(kinds["retrain_selector"]["status"], "needs_answers")
        self.assertEqual(kinds["corpus_gap"]["status"], "report")
        self.assertEqual(g.create_tasks(list(found.values())), [])  # nothing twice
        v = verify(reg / "growth" / "journal.jsonl")
        self.assertTrue(v["ok"], v)

    def test_answers_need_a_person_and_are_checked(self):
        reg, g = growth_setup("grow-answer")
        task = next(t for t in g.create_tasks(g.discover()) if t["kind"] == "retrain_selector")
        from computer.growth.engine import GrowthError
        for kw, why in ((dict(question="which things orbit jupiter", gold=["ganymede"], by=""), "who is answering"),
                        (dict(question="what is love", gold=["x"], by="ann"), "isn't one of"),
                        (dict(question="which things orbit jupiter", gold=[], by="ann"), "needs the answer")):
            with self.assertRaises(GrowthError) as cm:
                g.answer(task["id"], **kw)
            self.assertIn(why, str(cm.exception))
        t = g.answer(task["id"], "which things orbit jupiter", ["ganymede"], by="Ann")
        self.assertEqual(t["status"], "ready")
        self.assertEqual(t["answers"][0]["reachable_by"], ["reverse"])
        self.assertEqual(t["answers"][0]["by"], "Ann")
        self.assertTrue(verify(reg / "growth" / "answers.jsonl")["ok"])
        with self.assertRaises(GrowthError):
            g.answer(next(x for x in g.tasks() if x["kind"] == "corpus_gap")["id"], "glorbs", ["x"], by="Ann")

    def test_only_manual_approval_exists(self):
        from computer.growth.engine import Growth, GrowthError
        with self.assertRaises(GrowthError):
            Growth(Path(TMP.name) / "g-auto", approval="automatic")


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestGrowthRuns(unittest.TestCase):
    def test_a_task_stages_and_evaluates_a_candidate_and_changes_nothing_else(self):
        reg, g = growth_setup("grow-run")
        task = next(t for t in g.create_tasks(g.discover()) if t["kind"] == "retrain_selector")
        g.answer(task["id"], "which things orbit the sun", ["jupiter", "saturn", "earth"], by="Ann")
        before = (digest(reg / "models"), (reg / "ACTIVE.json").read_bytes(), digest(DATA))
        t = g.run(task["id"], HELD, REGR)                     # only a held-out subject (SUN): refused, and said why
        self.assertEqual(t["status"], "needs_answers")
        self.assertIn("held-out subject", t["candidate"]["error"])
        self.assertEqual(t["candidate"]["not_used"][0]["question"], "which things orbit the sun")
        g.answer(task["id"], "which things orbit jupiter", ["ganymede"], by="Ann")
        t = g.run(task["id"], HELD, REGR)
        c = t["candidate"]
        self.assertEqual(t["status"], "evaluated", c.get("failed_gates"))
        self.assertTrue((reg / "staging" / c["model_id"] / "eval.json").exists())
        self.assertEqual([u["question"] for u in c["used"]], ["which things orbit jupiter"])
        own = {d["question"]: d for d in c["task_questions"]["detail"]}
        self.assertEqual(own["which things orbit jupiter"]["score"], "right")
        self.assertTrue(own["which things orbit jupiter"]["trained_on"])
        self.assertFalse(own["which things orbit the sun"]["trained_on"])
        self.assertIn("--approve", c["promote"])
        self.assertEqual(before, (digest(reg / "models"), (reg / "ACTIVE.json").read_bytes(), digest(DATA)))
        self.assertEqual(digest(DATA), CORPUS)
        self.assertTrue(verify(reg / "growth" / "journal.jsonl")["ok"])

    def test_auto_prepares_but_never_promotes(self):
        reg, g = growth_setup("grow-auto")
        active = (reg / "ACTIVE.json").read_bytes()
        out = g.auto(HELD, REGR)
        self.assertEqual(out["approval"], "manual")
        self.assertTrue(out["tasks_created"])
        self.assertEqual(out["tasks_run"], [])                 # nobody has answered anything yet
        self.assertEqual((reg / "ACTIVE.json").read_bytes(), active)


# ---------------------------------------------------------------------- 6 a deliberately bad candidate

def stage_bad(reg: Path, mid: str, net) -> Path:
    from computer.neural.model import sha256_bytes
    from computer.neural.train import canonical_json, model_bytes
    src = reg / "models" / _S["id"]
    d = reg / "staging" / mid
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(src, d)
    for f in d.rglob("*"):
        if f.is_file():
            f.chmod(0o644)
    (d / "eval.json").unlink()
    (d / "eval.txt").unlink(missing_ok=True)
    b = model_bytes(net)
    (d / "model.json").write_bytes(b)
    m = json.loads((d / "manifest.json").read_text())
    m.update(model_id=mid, model_sha256=sha256_bytes(b))
    (d / "manifest.json").write_bytes(canonical_json(m, indent=1))
    return d


def shuffled_model():
    """Trained on the same questions with the labels shuffled between them: it learns which actions are common,
    not how to read a question."""
    from computer.neural.records import rows_of
    from computer.neural.train import read_records, train_network
    rows = rows_of(read_records(trained_registry() / "models" / _S["id"] / "records.jsonl.gz"), "train")
    rng = random.Random(7)
    return train_network([dict(r, targets=rows[rng.randrange(len(rows))]["targets"]) for r in rows], epochs=40)[0]


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestBadCandidate(unittest.TestCase):
    def test_rejected_and_never_promoted(self):
        from computer.neural.evaluate import evaluate
        reg = copy_registry("bad")
        stage_bad(reg, "nc1-bad-shuffled", shuffled_model())
        rep = evaluate(reg, "nc1-bad-shuffled", DATA, HELD, REGR, log=QUIET)
        self.assertFalse(rep["passed"])
        failed = {g for g, v in rep["gates"].items() if not v["pass"]}
        self.assertIn("beats_majority", failed)
        self.assertIn("not_worse_than_active", failed)
        with self.assertRaises(R.PromotionRefused) as cm:
            R.promote(reg, "nc1-bad-shuffled", approve=True)
        self.assertIn("beats_majority", str(cm.exception))
        self.assertEqual(json.loads((reg / "ACTIVE.json").read_text())["model_id"], _S["id"])

    def test_rejected_without_an_active_model_to_compare(self):
        from computer.neural.evaluate import evaluate
        reg = copy_registry("bad-alone")
        stage_bad(reg, "nc1-bad-shuffled", shuffled_model())
        R.deactivate(reg, approve=True)
        rep = evaluate(reg, "nc1-bad-shuffled", DATA, HELD, REGR, log=QUIET)
        self.assertFalse(rep["gates"]["beats_majority"]["pass"])
        self.assertTrue(rep["gates"]["not_worse_than_active"]["pass"])        # nothing to compare: the other gate holds
        self.assertFalse(rep["passed"])

    def test_regressions_are_detected_and_listed(self):
        """Without the neural path's extra answer checks (as the legacy controller runs), the bad model answers
        questions the rules got right with true facts about something else; the evaluation lists every one."""
        from computer.neural.evaluate import evaluate
        reg = copy_registry("bad-regress")
        stage_bad(reg, "nc1-bad-shuffled", shuffled_model())
        rep = evaluate(reg, "nc1-bad-shuffled", DATA, HELD, REGR, log=QUIET, strict=False)
        self.assertFalse(rep["gates"]["no_rule_regressions"]["pass"])
        lost = rep["comparisons"]["rules+trained"]["regressions"] + \
            rep["computer"]["comparisons"]["computer+trained"]["regressions"]
        self.assertTrue(lost)
        self.assertTrue(all({"set", "question", "was", "now"} <= set(x) for x in lost))
        self.assertIn("what is the population of the capital of spain", {x["question"] for x in lost})
        with self.assertRaises(R.PromotionRefused) as cm:      # and a report made that way can't promote anything
            R.promote(reg, "nc1-bad-shuffled", approve=True)
        self.assertIn("strict=False", str(cm.exception))

    def test_the_stricter_checks_stop_those_answers_at_run_time(self):
        from computer.controller import Network
        from computer.corpus import Corpus
        from computer.neural.runtime import NeuralPlanner
        from computer.plan import parse
        net = Network.from_json(json.loads((trained_registry() / "models" / _S["id"] / "model.json").read_text()))
        c = Corpus(DATA).load()
        strict, legacy = NeuralPlanner(c, net), NeuralPlanner(c, net, strict=False)
        for q, plan in (("what is the population of the capital of spain", "$1 = FIND spain\n$2 = GET $1 ~isa,hypernym\nANSWER $2"),
                        ("explain happy to me", "$1 = FIND happy\n$2 = GET $1 ~antonym\nANSWER $2"),
                        ("berlin population size", "$1 = FIND berlin\n$2 = GET $1 ~isa,hypernym\nANSWER $2")):
            p = parse(plan)
            res = strict.nav.run(p)
            self.assertTrue(res.answered, q)
            self.assertEqual(legacy.accept(q, p, res), "", q)       # the controller's own check lets it through
            self.assertNotEqual(strict.accept(q, p, res), "", q)
        for q, plan in (("owl diet", "$1 = FIND owl\n$2 = GET $1 ~diet\nANSWER $2"),
                        ("jupiter vs saturn, which is heavier", "$1 = FIND jupiter\n$2 = FIND saturn\n$3 = GET $1 ~heavy\n"
                         "$4 = GET $2 ~heavy\n$5 = COMPARE $3 $4 MORE heavier\nANSWER $5"),
                        ("the opposite of happy", "$1 = FIND happy\n$2 = GET $1 ~antonym\nANSWER $2")):
            p = parse(plan)
            self.assertEqual(strict.accept(q, p, strict.nav.run(p)), "", q)


# ---------------------------------------------------------------------- 7 plugins

def plugin_dir(name: str, mutate=None) -> Path:
    d = Path(TMP.name) / "plugins" / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    pj = json.loads((ROOT / "plugins" / "tmp117_temperature" / "plugin.json").read_text())
    py = (ROOT / "plugins" / "tmp117_temperature" / "plugin.py").read_text()
    pj, py = mutate(pj, py) if mutate else (pj, py)
    (d / "plugin.json").write_text(pj if isinstance(pj, str) else json.dumps(pj))
    (d / "plugin.py").write_text(py)
    return d


class TestPlugins(unittest.TestCase):
    def test_a_capability_arrives_by_folder_and_the_engine_is_untouched(self):
        engine = {f: hashlib.sha256((ROOT / "computer" / f).read_bytes()).hexdigest() for f in ENGINE}
        folder = plugin_dir("new").parent
        c = Computer(cfg(plugins_dir=str(folder)))
        self.assertEqual([(p.name, p.error) for p in c.plugins], [("sensor.thermometer", "")])
        self.assertTrue(c.registry.available("sensor.thermometer")[0])
        self.assertTrue(c.registry.caps["sensor.thermometer"].deterministic)
        self.assertEqual(c.components.call("sensor.thermometer", {"raw": 3200}),
                         {"temperature_c": 25.0, "valid": True, "accuracy_c": 0.1})
        self.assertEqual(engine, {f: hashlib.sha256((ROOT / "computer" / f).read_bytes()).hexdigest() for f in ENGINE})
        self.assertIn("84", c.think("what is 12 times 7").text)

    def test_bad_plugins_are_refused_and_the_computer_starts(self):
        cases = {
            "notjson": (lambda pj, py: ("{", py), "Expecting"),
            "clock": (lambda pj, py: (pj, py.replace("from computer.neural", "import time\nT = time.time()\nfrom computer.neural")),
                      "calls time.time()"),
            "random": (lambda pj, py: (pj, "import random\n" + py), "imports random"),
            "writes": (lambda pj, py: ({**pj, "capability": {**pj["capability"], "writes_state": True}}, py), "allowlist"),
            "builtin": (lambda pj, py: ({**pj, "name": "corpus.lookup"}, py), "already a capability"),
            "mismatch": (lambda pj, py: ({**pj, "version": "2.0.0"}, py), "plugin.json says"),
            "example": (lambda pj, py: ({**pj, "examples": [{"input": {"raw": 3200}, "output": {"temperature_c": 99.0,
                                                                                               "valid": True, "accuracy_c": 0.1}}]}, py),
                        "expected"),
            "noexample": (lambda pj, py: ({**pj, "examples": []}, py), "at least one example"),
            "crash": (lambda pj, py: (pj, py + "\nraise RuntimeError('boom at import')\n"), "boom at import"),
            "notcomponent": (lambda pj, py: ({**pj, "entry": "plugin.py:LSB_C"}, py), "isn't a Component"),
            "escape": (lambda pj, py: ({**pj, "capability": {**pj["capability"], "modules": ["../x.py"]}}, py), "file names"),
            "engine": (lambda pj, py: (pj, py.replace("from computer.neural.components",
                                                      "from computer.navdata import generate\nfrom computer.neural.components")),
                       "checked or reviewed"),
        }
        for name, (mutate, why) in cases.items():
            folder = Path(TMP.name) / f"badplugins-{name}"
            if folder.exists():
                shutil.rmtree(folder)
            d = plugin_dir(name, mutate)
            shutil.move(str(d), str(folder / name))
            c = Computer(cfg(plugins_dir=str(folder)))
            self.assertEqual(len(c.plugins), 1, name)
            self.assertIn(why, c.plugins[0].error, name)
            self.assertFalse(c.components.has("sensor.thermometer"), name)
            self.assertIn("Paris", c.think("france capital?").text, name)

    def test_two_good_plugins_and_one_bad(self):
        folder = Path(TMP.name) / "mixed"
        if folder.exists():
            shutil.rmtree(folder)
        for name, newname in (("a", "sensor.thermometer"), ("b", "sensor.cabin_thermometer")):
            d = plugin_dir(name, lambda pj, py, n=newname: ({**pj, "name": n}, py.replace('"sensor.thermometer"', f'"{n}"')))
            shutil.move(str(d), str(folder / name))
        d = plugin_dir("c", lambda pj, py: ({**pj, "name": "sensor.thermometer"}, py))     # a duplicate name
        shutil.move(str(d), str(folder / "c"))
        c = Computer(cfg(plugins_dir=str(folder)))
        self.assertEqual(c.components.names(), ["sensor.cabin_thermometer", "sensor.thermometer"])
        self.assertIn("already a capability", [p for p in c.plugins if p.folder.name == "c"][0].error)


# ---------------------------------------------------------------------- 8 the robot

class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def good_obs(t=1.0, temp=40.0, conf=0.98, battery=12.2, x=0.0, obstacles=((2.0, 0.0, 0.3),)):
    o = lambda q, v, u, c=conf: {"sensor": "s", "quantity": q, "value": v, "unit": u, "t": t, "confidence": c}   # noqa: E731
    return [o("pose_x", [x], "m"), o("pose_y", [0.0], "m"), o("heading", [0.0], "rad"), o("speed", [0.2], "m/s"),
            o("yaw_rate", [0.0], "rad/s"), o("battery_voltage", [battery], "V"), o("motor_temp", [temp], "degC")] + \
        [o("obstacle", list(ob), "m", 0.95) for ob in obstacles]


def controller(policies=("robot.policy_expert",), journal=None, plugins=False):
    from computer.robotics.controller import RobotController
    from computer.robotics.policy import ExpertPolicy, NaivePolicy
    from computer.robotics.safety import Envelope
    reg = ComponentRegistry()
    reg.register(ExpertPolicy())
    reg.register(NaivePolicy())
    if plugins:
        from computer.plugins import load_plugins
        reg.register(load_plugins(ROOT / "plugins")[0].component)
    clk = Clock()
    return RobotController(Envelope.load(), reg, clk, journal, policies=policies), clk


class TestRobot(unittest.TestCase):
    def test_a_motion_that_breaks_a_limit_is_rejected(self):
        ctl, clk = controller(("robot.policy_naive",))
        ctl.submit(good_obs())
        d = ctl.request_action([4.0, 3.0])
        self.assertFalse(d["allowed"])
        self.assertEqual(d["policy"], "robot.policy_naive@1.0.0")
        self.assertGreater(d["candidate"]["v"], 0.5)
        rules = {v["rule_id"] for v in d["violations"]}
        self.assertIn("speed.max", rules)
        self.assertEqual(d["command"], {"v": 0.0, "omega": 0.0, "duration": 0.0})   # STOP, never the candidate
        sp = next(v for v in d["violations"] if v["rule_id"] == "speed.max")
        self.assertEqual(sp["limit"], 0.5)

    def test_a_safe_motion_is_allowed_and_is_the_command(self):
        ctl, clk = controller()
        ctl.submit(good_obs(obstacles=()))
        d = ctl.request_action([3.0, 0.0])
        self.assertTrue(d["allowed"], d["violations"])
        self.assertEqual(d["command"], d["candidate"])
        self.assertEqual(d["violations"], [])

    def test_fail_closed(self):
        from computer.robotics.safety import Envelope
        env = Envelope.load()
        go = {"v": 0.3, "omega": 0.0, "duration": 0.5}
        for name, setup, rule in (
                ("stale", lambda c, k: (c.submit(good_obs()), setattr(k, "t", k.t + 2.0)), "state.max_age"),
                ("uncertain", lambda c, k: c.submit(good_obs(conf=0.3)), "state.min_confidence"),
                ("hot", lambda c, k: c.submit(good_obs(temp=85.0)), "motor_temp.max"),
                ("flat", lambda c, k: c.submit(good_obs(battery=9.8)), "battery.min"),
                ("e_stop", lambda c, k: (c.submit(good_obs()), c.set_e_stop(True)), "e_stop"),
                ("wall", lambda c, k: c.submit(good_obs(x=4.2)), "workspace"),
                ("obstacle", lambda c, k: c.submit(good_obs(obstacles=((0.6, 0.0, 0.3),))), "clearance.min"),
                ("missing", lambda c, k: c.submit(good_obs()[1:]), "state.complete")):
            ctl, clk = controller()
            setup(ctl, clk)
            v = env.check(ctl.state(), go, clk())
            self.assertFalse(v.allowed, name)
            self.assertIn(rule, {x["rule_id"] for x in v.violations}, name)
            stop = env.check(ctl.state(), {"v": 0.0, "omega": 0.0, "duration": 0.5}, clk())
            self.assertTrue(stop.allowed, name)                     # stopping is always allowed
        for bad in ({"v": float("nan"), "omega": 0.0, "duration": 0.5}, {"v": 0.1}, "fast", None):
            v = env.check(None, bad, 0.0)
            self.assertFalse(v.allowed)
            self.assertEqual(v.violations[0]["rule_id"], "action.valid")

    def test_the_same_inputs_give_the_same_verdict(self):
        from computer.robotics.safety import Envelope
        ctl, clk = controller()
        ctl.submit(good_obs())
        env = Envelope.load()
        a = env.check(ctl.state(), {"v": 0.4, "omega": 0.5, "duration": 0.5}, clk())
        b = env.check(ctl.state(), {"v": 0.4, "omega": 0.5, "duration": 0.5}, clk())
        self.assertEqual(a, b)

    def test_observations_are_checked(self):
        ctl, _ = controller()
        out = ctl.submit([{"sensor": "s", "quantity": "speed", "value": [1.0], "unit": "km/h", "t": 1.0, "confidence": 1.0},
                          {"sensor": "s", "quantity": "warp", "value": [1.0], "unit": "c", "t": 1.0, "confidence": 1.0},
                          {"sensor": "s", "quantity": "obstacle", "value": [1.0, 1.0, -0.2], "unit": "m", "t": 1.0,
                           "confidence": 1.0},
                          {"sensor": "s", "quantity": "speed", "value": [float("inf")], "unit": "m/s", "t": 1.0,
                           "confidence": 1.0}, "junk"])
        self.assertEqual(out["accepted"], 0)
        whys = " | ".join(r["why"] for r in out["refused"])
        for w in ("not 'km/h'", "one of", "radius must be positive", "finite", "expected an object"):
            self.assertIn(w, whys)

    def test_a_plugin_sensor_feeds_the_envelope(self):
        """The thermometer plugin turns a raw register into a temperature, and the envelope stops a hot motor."""
        from computer.robotics.safety import Envelope
        ctl, clk = controller(plugins=True)
        obs = [o for o in good_obs() if o["quantity"] != "motor_temp"]
        obs.append({"sensor": "sensor.thermometer", "quantity": "motor_temp", "raw": int(85.0 / 0.0078125), "t": 1.0,
                    "confidence": 0.99})
        self.assertEqual(ctl.submit(obs)["refused"], [])
        self.assertEqual(ctl.state().motor_temp_c, 85.0)
        v = Envelope.load().check(ctl.state(), {"v": 0.3, "omega": 0.0, "duration": 0.5}, clk())
        self.assertIn("motor_temp.max", {x["rule_id"] for x in v.violations})
        out = ctl.submit([{"sensor": "sensor.thermometer", "quantity": "motor_temp", "raw": -32768, "t": 2.0,
                           "confidence": 1.0}])
        self.assertIn("isn't valid", out["refused"][0]["why"])            # "no conversion yet" isn't a temperature

    def test_a_rejected_candidate_falls_back_and_everything_is_journaled(self):
        from computer.neural.experience import ChainLog
        jp = Path(TMP.name) / "robot-journal.jsonl"
        jp.unlink(missing_ok=True)
        ctl, clk = controller(("robot.policy_naive", "robot.policy_expert"), ChainLog(jp, fmt="cdos-robot-decision/1"))
        ctl.submit(good_obs(obstacles=()))
        d = ctl.request_action([3.0, 1.0])
        self.assertTrue(d["allowed"])
        self.assertEqual(d["policy"], "robot.policy_expert@1.0.0")
        self.assertEqual([(t["policy"], t["allowed"]) for t in d["tried"]],
                         [("robot.policy_naive@1.0.0", False), ("robot.policy_expert@1.0.0", True)])
        ctl.set_e_stop(True)
        d = ctl.request_action([3.0, 1.0])
        self.assertFalse(d["allowed"])
        lines = [json.loads(l) for l in jp.read_text().splitlines()]
        self.assertEqual([l["event"] for l in lines], ["decision", "e_stop", "decision"])
        self.assertTrue(verify(jp)["ok"])

    def test_safety_files_are_checked(self):
        from computer.robotics.safety import DEFAULT, Envelope, EnvelopeError
        good = json.loads(DEFAULT.read_text())
        for mutate in (lambda s: s["limits"].pop("speed.max"), lambda s: s["limits"]["speed.max"].update(value=-1),
                       lambda s: s["limits"]["speed.max"].update(why=""), lambda s: s["limits"]["workspace"].update(value=[1, 0, 0, 1]),
                       lambda s: s["limits"]["clearance.min"].update(value=float("nan")), lambda s: s.update(format="x"),
                       lambda s: s.update(robot_radius_m=0)):
            s = copy.deepcopy(good)
            mutate(s)
            with self.assertRaises(EnvelopeError):
                Envelope(s)

    def test_the_sim_never_collides_under_any_policy(self):
        from computer.robotics.learn import run_episode
        from computer.robotics.policy import ExpertPolicy, NaivePolicy
        from computer.robotics.safety import Envelope
        env = Envelope.load()
        for pol in (ExpertPolicy(), NaivePolicy()):
            for seed in range(10000, 10008):
                out = run_episode(seed, pol, env, max_steps=120)
                self.assertNotIn(out["status"], ("collided", "out_of_bounds"), (pol.spec.name, seed))

    @unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
    def test_the_learned_policy_is_evaluated_like_any_model(self):
        rep = (policy_registry(), _S["prep"])[1]
        r = rep["results"]
        self.assertGreater(r["trained"]["reached"], r["untrained"]["reached"])
        self.assertEqual(r["trained"]["collided"] + r["trained"]["out_of_bounds"], 0)
        self.assertEqual(r["naive"]["rejection_rate"], 1.0)
        from computer.robotics.learn import load_policy
        d = R.component_root(policy_registry(), "robot.policy") / "models" / _S["pid"]
        p, m = load_policy(d)
        self.assertEqual(m["interface"], {"input": "robot.state@1.0.0", "output": "robot.action@1.0.0"})


# ---------------------------------------------------------------------- the API

class ApiServer:
    def __init__(self, c: Config):
        from computer.services.api import make_server
        from computer.services.platform import Platform
        self.sock = str(Path(tempfile.mkdtemp(dir=TMP.name)) / "api.sock")
        self.platform = Platform(c)
        self.srv = make_server(self.platform, self.sock)
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def operator_policy() -> str:
    """A Governor policy that makes the user running the tests an operator (Neural Platform 0.3: clearing the
    emergency stop needs one)."""
    p = Path(tempfile.mkdtemp(dir=TMP.name)) / "governor.json"
    p.write_text(json.dumps({"format": "cdos-governor-policy/1", "roles": {"operator": [f"uid:{os.getuid()}"]}}))
    return str(p)


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from computer.services.client import Client
        cls.api = ApiServer(cfg(robotics=True, plugins_dir=str(ROOT / "plugins"),
                                robotics_journal=str(Path(TMP.name) / "api-journal.jsonl"),
                                governor_policy=operator_policy(),
                                governor_dir=tempfile.mkdtemp(dir=TMP.name)))
        cls.c = Client(cls.api.sock)

    @classmethod
    def tearDownClass(cls):
        cls.api.close()

    def test_health_and_capabilities(self):
        h = self.c.get("/v1/health")
        self.assertEqual(h["status"], "ok")
        self.assertTrue(h["corpus"]["ready"])
        self.assertEqual(h["components"], ["robot.policy_expert", "sensor.thermometer"])
        names = {x["name"]: x for x in self.c.get("/v1/capabilities")["capabilities"]}
        self.assertTrue(names["sensor.thermometer"]["available"])
        self.assertFalse(names["planner.neural"]["available"])

    def test_ask_and_components(self):
        a = self.c.post("/v1/ask", {"question": "What's the population of the capital of France?"})
        self.assertIn("2,100,000", a["text"])
        self.assertEqual(a["status"], "answered")
        self.assertTrue(a["envelope"]["canonical_hash"].startswith("sha256:"))
        self.assertEqual(self.c.post("/v1/components/sensor.thermometer", {"input": {"raw": 6400}})["output"]["temperature_c"],
                         50.0)
        for path, body, status, code in (("/v1/components/sensor.thermometer", {"input": {"raw": 1.5}}, 400, "INVALID_INPUT"),
                                         ("/v1/components/nothing.here", {"input": {}}, 404, "UNAVAILABLE"),
                                         ("/v1/ask", {"question": ""}, 400, "INVALID_INPUT"),
                                         ("/v1/nowhere", {}, 404, "INVALID_INPUT")):
            s, d = self.c.request("POST", path, body)
            self.assertEqual((s, d["error"]["code"]), (status, code), path)
        try:                                   # over the limit: a 413, or the connection closed mid-send
            s, d = self.c.request("POST", "/v1/ask", {"question": "x" * 300000})
            self.assertEqual(s, 413)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.assertEqual(self.c.get("/v1/health")["status"], "ok")          # and the server carries on

    def test_a_robot_client_gets_rejected_and_approved_motions(self):
        # 0.3: the stop starts engaged and only an operator, with a reason, clears it (tests/test_governor.py)
        self.c.post("/v1/robot/e_stop", {"on": False, "reason": "test: robot ready"})
        sub = self.c.post("/v1/robot/observations", {"observations": good_obs(temp=90.0)})
        self.assertEqual(sub["refused"], [])
        d = self.c.post("/v1/robot/action", {"goal": [3.0, 0.0]})
        self.assertFalse(d["allowed"])
        self.assertIn("motor_temp.max", {v["rule_id"] for v in d["violations"]})
        self.assertEqual(d["command"]["v"], 0.0)
        self.c.post("/v1/robot/observations", {"observations": good_obs(temp=40.0, obstacles=())})
        d = self.c.post("/v1/robot/action", {"goal": [3.0, 0.0]})
        self.assertTrue(d["allowed"], d["violations"])
        self.assertEqual(self.c.post("/v1/robot/e_stop", {"on": True})["e_stop"], True)
        self.c.post("/v1/robot/observations", {"observations": good_obs(temp=40.0, obstacles=())})
        d = self.c.post("/v1/robot/action", {"goal": [3.0, 0.0]})
        self.assertIn("e_stop", {v["rule_id"] for v in d["violations"]})
        self.c.post("/v1/robot/e_stop", {"on": False, "reason": "test: done"})

    def test_robot_endpoints_are_off_without_robotics(self):
        from computer.services.client import Client
        api = ApiServer(cfg(robotics=False))
        try:
            s, d = Client(api.sock).request("POST", "/v1/robot/action", {"goal": [1, 1]})
            self.assertEqual((s, d["error"]["code"]), (503, "UNAVAILABLE"))
        finally:
            api.close()

    def test_tcp_is_loopback_only(self):
        from computer.services.api import make_server
        import socket as so
        s = so.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        srv = make_server(self.api.platform, tcp_port=port)
        try:
            self.assertEqual(srv.server_address[0], "127.0.0.1")
        finally:
            srv.server_close()


# ---------------------------------------------------------------------- 9 install and restart

@unittest.skipUnless(HAVE_NUMPY and shutil.which("bash") and shutil.which("tar") and shutil.which("sha256sum"),
                     "needs numpy, bash, tar and sha256sum")
class TestInstallAndRestart(unittest.TestCase):
    def test_install_start_kill_restart_keeps_everything(self):
        base = Path(tempfile.mkdtemp(dir=TMP.name))
        prefix, conf, state, units = base / "opt", base / "etc", base / "var", base / "units"
        r = subprocess.run(["bash", str(ROOT / "tools" / "install_services.sh"), "--prefix", str(prefix),
                            "--config-dir", str(conf), "--state-dir", str(state), "--unit-dir", str(units),
                            "--no-systemctl", "--no-user", "--python", sys.executable, "--robot-sim"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        cur = prefix / "current"
        self.assertTrue((cur / "computer" / "core.py").exists())
        self.assertTrue((conf / "plugins" / "tmp117_temperature" / "plugin.json").exists())
        for u in ("cdos-api.service", "cdos-growth.service", "cdos-growth.timer", "cdos-robot-sim.service"):
            text = (units / u).read_text()
            self.assertNotIn("@", text.replace("Documentation=file://", ""), u)
            if shutil.which("systemd-analyze"):
                v = subprocess.run(["systemd-analyze", "verify", str(units / u)], capture_output=True, text=True)
                self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", (units / "cdos-api.service").read_text())
        self.assertIn("IPAddressDeny=any", (units / "cdos-growth.service").read_text())
        # the operator copies the corpus and a promoted model into state, and turns robotics on
        shutil.rmtree(state / "data")
        shutil.copytree(DATA, state / "data")
        shutil.rmtree(state / "models" / "neural")
        shutil.copytree(trained_registry(), state / "models" / "neural")
        shutil.copytree(policy_registry() / "components", state / "models" / "neural" / "components")
        cfgtext = (conf / "config.toml").read_text()
        self.assertIn("robotics = true", cfgtext)
        sock = str(base / "run" / "api.sock")
        env = {**os.environ, "PYTHONPATH": ""}

        def start():
            p = subprocess.Popen([sys.executable, "-m", "computer.services.api", "--config", str(conf / "config.toml"),
                                  "--socket", sock], cwd=str(cur), env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE)
            from computer.services.client import Client
            cl = Client(sock, timeout=2)
            for _ in range(120):
                try:
                    return p, cl, cl.get("/v1/health")
                except (OSError, RuntimeError, ValueError):
                    time.sleep(0.25)
            p.kill()
            self.fail(p.stderr.read().decode())

        p, cl, h1 = start()
        cl.post("/v1/ask", {"question": "owl diet"})
        cl.post("/v1/ask", {"question": "glorbs"})
        # 0.3: a fresh install starts with the emergency stop engaged; the operator (root here, as the installer's
        # policy says) clears it with a reason, through the Governor's command and the service's socket
        self.assertTrue(h1["governor"]["latch"]["engaged"])
        gc = subprocess.run([sys.executable, "-m", "computer.governor", "--config", str(conf / "config.toml"),
                             "--socket", sock, "estop", "clear", "--reason", "install test: robot ready"],
                            cwd=str(cur), env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(gc.returncode, 0, gc.stdout + gc.stderr)
        sim = subprocess.run([sys.executable, "-m", "computer.robotics", "simrobot", "--config", str(conf / "config.toml"),
                              "--socket", sock, "--seed", "10003", "--steps", "15", "--raw-temperature"],
                             cwd=str(cur), env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(sim.returncode, 0, sim.stderr)
        self.assertEqual(json.loads(sim.stdout)["allowed"], 15)
        g = subprocess.run([sys.executable, "-m", "computer.growth", "--config", str(conf / "config.toml"), "discover"],
                           cwd=str(cur), env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(g.returncode, 0, g.stderr)
        tasks1 = cl.get("/v1/growth/tasks")["tasks"]
        self.assertTrue(tasks1)
        keep = {f: (state / f).read_bytes() for f in ("models/neural/ACTIVE.json",
                                                       "models/neural/components/robot.policy/ACTIVE.json")}
        conf_before, corpus_before = (conf / "config.toml").read_bytes(), digest(state / "data")

        p.send_signal(signal.SIGKILL)                              # a power cut, as far as the process can tell
        p.wait(timeout=10)
        p.stderr.close()
        p, cl, h2 = start()                                        # and the machine coming back
        try:
            for k in ("corpus", "neural", "components", "plugins"):
                self.assertEqual(h1[k], h2[k], k)
            self.assertEqual(h2["neural"]["model_id"], _S["id"])
            self.assertEqual(h2["robotics"]["learned_policy"], "active")
            self.assertFalse(h2["governor"]["latch"]["engaged"], h2["governor"])   # the operator's clear persisted
            self.assertTrue(h2["governor"]["audit"]["ok"])
            self.assertEqual(cl.get("/v1/growth/tasks")["tasks"], tasks1)
            self.assertEqual({f: (state / f).read_bytes() for f in keep}, keep)
            self.assertEqual((conf / "config.toml").read_bytes(), conf_before)
            self.assertEqual(digest(state / "data"), corpus_before)
            d = cl.post("/v1/robot/action", {"goal": [1.0, 1.0]})
            self.assertFalse(d["allowed"])                         # nothing seen since the restart: not trusted
            self.assertIn("state", d["policy_error"])
            self.assertIn("mice", cl.post("/v1/ask", {"question": "owl diet"})["text"])
        finally:
            p.terminate()
            p.wait(timeout=10)
            p.stderr.close()
        for log in ("models/neural/experience/experience.jsonl", "robotics/decisions.jsonl",
                    "models/neural/growth/journal.jsonl"):
            v = verify(state / log)
            self.assertTrue(v["ok"], (log, v))
            self.assertGreater(v["lines"], 0, log)
        from computer.governor import AuditLog
        v = AuditLog(state / "governor" / "audit.jsonl").verify()
        self.assertTrue(v["ok"], v)
        self.assertGreater(v["lines"], 0)


if __name__ == "__main__":
    unittest.main()
