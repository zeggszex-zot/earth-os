"""Learning from the corpus: the example records, the truth/logic examples, the leak-free split, held-out phrasings,
the controller's logic actions and checks, and the experiment (tools/experiment_learning.py).  Needs numpy to train."""

import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).parent))

import mini_corpus                                                       # noqa: E402
from computer.controller import ACTIONS, ControllerPlanner, action_of, connective_unasked, fills  # noqa: E402
from computer.controller_train import reword                                  # noqa: E402
from computer.corpus import Corpus                                            # noqa: E402
from computer.navdata import HEADS, HEAD_KINDS, generate, leak_free           # noqa: E402
from computer.navigator import Navigator                                      # noqa: E402
from computer.plan import parse                                               # noqa: E402

try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

TMP = tempfile.TemporaryDirectory()
DATA = mini_corpus.build(Path(TMP.name) / "data")
_EX = []


def examples():
    if not _EX:
        _EX.extend(generate(Corpus(DATA).load(), 5000))
    return _EX


class TestRecords(unittest.TestCase):
    def test_every_example_says_what_it_used(self):
        for e in examples():
            for k in ("question", "plan", "evidence", "rules", "steps", "final", "answer", "concepts", "split"):
                self.assertIn(k, e, e["question"])
            parse(e["plan"])

    def test_inherited_facts_are_marked(self):
        nav = Navigator(Corpus(DATA).load())
        r = nav.run("$1 = FIND whale\n$2 = GET $1 ~breathe INHERIT\nANSWER $2")
        self.assertIn(["WHALE", "MARINE_MAMMAL", "MAMMAL"], r.inherited)
        from computer.navdata import record
        ex = {"subjects": ["WHALE"]}
        record(ex, r, parse("$1 = FIND whale\n$2 = GET $1 ~breathe INHERIT\nANSWER $2"))
        lungs = [x for x in ex["evidence"] if x["r"] == "BREATHES_WITH"][0]
        self.assertEqual(lungs["derived"], "WHALE>MARINE_MAMMAL>MAMMAL")

    def test_rule_backed_answers_name_their_rule(self):
        no = [e for e in examples() if e["kind"] == "test_false"]
        self.assertTrue(no)
        for e in no:
            self.assertEqual(e["truth"], "FALSE")
            self.assertTrue(e["rules"], e["question"])


class TestTruthExamples(unittest.TestCase):
    def test_kinds_and_heads(self):
        kinds = {e["kind"] for e in examples()}
        self.assertTrue(HEAD_KINDS <= kinds, HEAD_KINDS - kinds)
        self.assertEqual(len(set(HEADS.values())), len(HEADS))      # no head is a prefix of the same text twice
        for e in examples():
            if e["kind"] in HEAD_KINDS:
                self.assertTrue(e["answer"].lower().startswith(e["gold"][0]), e["question"])

    def test_wrong_value_is_never_the_subject(self):
        self.assertFalse([e for e in examples() if e["kind"] == "rel_test" and
                          e["question"].split()[1] == e["question"].split()[-1]])


class TestSplit(unittest.TestCase):
    def test_no_held_out_thing_in_training(self):
        kept, dropped = leak_free(examples())
        held = {s for e in kept if e["split"] != "train" for s in e["subjects"]}
        self.assertTrue(held)
        for e in kept:
            if e["split"] == "train":
                self.assertFalse(held & set(e["concepts"]), e["question"])
        self.assertGreater(dropped, 0)

    def test_held_out_phrasings(self):
        ex = next(e for e in examples() if e["kind"] == "logic" and "LOGIC OR" in e["plan"])
        tr = {r["question"] for r in reword(ex, random.Random(0), 9, which="train")}
        te = {r["question"] for r in reword(ex, random.Random(0), 9, which="test")}
        self.assertTrue(tr and te)
        self.assertFalse(tr & te)


class TestControllerLogic(unittest.TestCase):
    def setUp(self):
        self.ctl = ControllerPlanner(Corpus(DATA).load())

    def test_actions(self):
        for a in ("logic_and", "logic_or", "logic_nor", "logic_not", "test_rel"):
            self.assertIn(a, ACTIONS)
        r = self.ctl.reader.read("whale: mammal or fish?")
        plans = fills("logic_or", r)
        self.assertEqual(plans[0].splitlines()[2:5], ["$3 = TEST $2 mammal", "$4 = TEST $2 fish", "$5 = LOGIC OR $3 $4"])
        self.assertEqual(action_of(plans[0]), "logic_or")
        self.assertEqual(action_of("$1 = FIND titan\n$2 = GET $1 ORBITS\n$3 = TEST $2 saturn\nANSWER $3"), "test_rel")

    def test_one_test_doesnt_answer_an_or_question(self):
        r = self.ctl.reader.read("sun: star or planet?")
        plain = parse("$1 = FIND sun\n$2 = GET $1 IS_A\n$3 = TEST $2 planet\nANSWER $3")
        self.assertIn("or", connective_unasked(r, plain))
        self.assertEqual(connective_unasked(r, parse(fills("logic_or", r)[0])), "")

    def test_a_connective_the_question_never_said(self):
        r = self.ctl.reader.read("would you call a whale a fish")
        plan = parse(fills("logic_not", r)[0])
        self.assertIn("NOT", connective_unasked(r, plan))
        r = self.ctl.reader.read("is a whale not a fish")
        self.assertEqual(connective_unasked(r, parse(fills("logic_not", r)[0])), "")

    def test_yes_no_is_about_the_first_thing_named(self):
        r = self.ctl.reader.read("marine mammal: a kind of whale?")
        for a in ("test_have", "test_isa"):
            for p in fills(a, r):
                self.assertTrue(p.startswith("$1 = FIND marine mammal"), p)


class TestControllerGuards(unittest.TestCase):
    """Two mistakes the real-corpus experiment found (see the README, "On your corpus")."""

    def test_a_kind_of_answer_needs_a_question_about_nothing_else(self):
        ctl = ControllerPlanner(Corpus(DATA).load())
        self.assertEqual(fills("isa", ctl.reader.read("what language does a whale speak")), [])
        self.assertTrue(fills("isa", ctl.reader.read("what is a whale")))

    def test_the_floor_holds_after_the_top_action(self):
        class Fixed:      # top action can't be filled from this question; the next one is a 1% guess
            actions = ["compare_more", "define"]
            def predict(self, idx):
                return {"compare_more": 0.99, "define": 0.01}
        ctl = ControllerPlanner(Corpus(DATA).load(), Fixed(), min_p=0.08)
        self.assertEqual(ctl.plans("whale kinds"), [])


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestExperiment(unittest.TestCase):
    def test_it_runs_and_holds_things_out(self):
        import argparse
        import experiment_learning as xl
        a = argparse.Namespace(corpus=str(DATA), subjects=5000, rewordings=2, fractions=[0.5, 1.0], epochs=20, things=False,
                               heldout=[str(ROOT / "tests" / "controller_heldout.jsonl")], lexicon=None, seed=0, out=None)
        rep = xl.run(a)
        self.assertEqual(rep["split"]["leaks_left"], 0)
        self.assertIn("rules+trained@1", rep["results"]["B unseen things, unseen phrasings"])
        d = rep["results"]["D controller_heldout.jsonl"]
        self.assertGreater(d["rules+trained@1"]["right"], d["rules"]["right"])      # learning helped here
        self.assertLess(d["rules+trained@1"]["wrong"], d["rules+untrained"]["wrong"])  # and beat trying everything
        self.assertIn("unseen things", xl.table(rep))


if __name__ == "__main__":
    unittest.main()
