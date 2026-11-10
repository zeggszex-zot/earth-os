"""The navigator: plan language, executor, planners, computer integration, training data.

Uses a tiny corpus written in the real shard format (tests/mini_corpus.py), so it runs without data/.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import mini_corpus                                            # noqa: E402
from computer.config import Config                                 # noqa: E402
from computer.corpus import Corpus                                 # noqa: E402
from computer.navdata import LookupSystem, NavigatorSystem, as_chat, evaluate, generate, split_of  # noqa: E402
from computer.navigator import Navigator                           # noqa: E402
from computer.plan import GRAMMAR, MAX_STEPS, OPS, PlanError, parse  # noqa: E402
from computer.planner import LMPlanner, RulePlanner, navigate      # noqa: E402
from computer.core import Computer                                    # noqa: E402

TMP = tempfile.TemporaryDirectory()
DATA = mini_corpus.build(Path(TMP.name) / "data")


def corpus():
    return Corpus(DATA).load()


def run(plan, year=0):
    return Navigator(corpus()).run(plan, year=year)


class TestPlanLanguage(unittest.TestCase):
    def test_good_plan_parses(self):
        p = parse("$1 = FIND france\n$2 = GET $1 CAPITAL\n$3 = GET $2 ~population\nCHECK $3\nANSWER $3")
        self.assertEqual([s.op for s in p.steps], ["FIND", "GET", "GET", "CHECK", "ANSWER"])
        self.assertEqual(str(p).splitlines()[1], "$2 = GET $1 CAPITAL")

    def test_bad_plans_are_rejected(self):
        bad = {
            "": "empty",
            "$1 = FIND x\nANSWER $2": "before it is set",
            "$1 = FIND x\n$2 = DELETE $1\nANSWER $2": "unknown operation",
            "$1 = FIND x\n$2 = GET $1 capital\nANSWER $2": "bad relation",
            "$1 = FIND x\n$2 = FIND y\n$3 = COMPARE $1 $2 MORE\nANSWER $3": "must come from GET",
            "$1 = FIND x\n$2 = GET $1 ~a": "no ANSWER",
            "$1 = FIND x\nANSWER $1\n$2 = GET $1 ~a": "last step",
            "$1 = FIND x\n$1 = FIND y\nANSWER $1": "assigned twice",
            "FIND x\nANSWER $1": "needs result variable",
        }
        for text, why in bad.items():
            with self.assertRaises(PlanError, msg=text) as cm:
                parse(text)
            self.assertIn(why, str(cm.exception), text)

    def test_unknown_relations_rejected_when_vocabulary_known(self):
        with self.assertRaises(PlanError):
            parse("$1 = FIND x\n$2 = GET $1 MADE_UP_REL\nANSWER $2", {"CAPITAL"})
        parse("$1 = FIND x\n$2 = GET $1 ~made,up\nANSWER $2", {"CAPITAL"})   # words are resolved at run time

    def test_step_limit(self):
        lines = ["$1 = FIND x"] + [f"${i + 1} = GET ${i} ~a" for i in range(1, 9)] + ["CHECK $9", "CHECK $9", "ANSWER $9"]
        self.assertGreater(len(lines), MAX_STEPS + 1)
        with self.assertRaises(PlanError):
            parse("\n".join(lines))

    def test_grammar_names_every_op(self):
        for op in OPS:
            self.assertIn(f'"{op}', GRAMMAR)


class TestExecutor(unittest.TestCase):
    def test_two_hops(self):
        r = run("$1 = FIND france\n$2 = GET $1 ~capital\n$3 = GET $2 ~population\nANSWER $3")
        self.assertTrue(r.answered)
        self.assertEqual(r.text, "France's capital is Paris. Paris's population is 2,100,000 (since 2023).")
        self.assertEqual([(f.subject, f.relation) for f in r.evidence], [("FRANCE", "CAPITAL"), ("PARIS", "POPULATION")])

    def test_compare_uses_one_measurement_and_one_unit(self):
        r = run("$1 = FIND jupiter\n$2 = FIND saturn\n$3 = GET $1 ~big\n$4 = GET $2 ~big\n$5 = COMPARE $3 $4 MORE bigger\nANSWER $5")
        self.assertIn("radius", r.text)
        self.assertNotIn("mass", r.text)
        self.assertTrue(r.text.endswith("so Jupiter is bigger."))

    def test_compare_refuses_different_units(self):
        with tempfile.TemporaryDirectory() as d:
            facts = [("ALPHA", "HEIGHT", "N", "10", "M", None, None, "MANUAL", 1.0, ""),
                     ("BETA", "HEIGHT", "N", "5", "FT", None, None, "MANUAL", 1.0, "")]
            nav = Navigator(Corpus(mini_corpus.build(d, facts)).load())
            r = nav.run("$1 = FIND alpha\n$2 = FIND beta\n$3 = GET $1 HEIGHT\n$4 = GET $2 HEIGHT\n$5 = COMPARE $3 $4 MORE\nANSWER $5")
            self.assertEqual(r.status, "unsupported")
            self.assertIn("different units", r.text)

    def test_older_means_earlier(self):
        r = run("$1 = FIND eiffel tower\n$2 = FIND brandenburg gate\n$3 = GET $1 ~old\n$4 = GET $2 ~old\n"
                "$5 = COMPARE $3 $4 LESS older\nANSWER $5")
        self.assertTrue(r.text.endswith("so Brandenburg Gate is older."))

    def test_inheritance_is_shown_not_hidden(self):
        r = run("$1 = FIND whales\n$2 = GET $1 ~breathe INHERIT\nANSWER $2")
        self.assertIn("Whale is a marine mammal. Marine mammal is a mammal. Mammal breathes with Lungs.", r.text)

    def test_yes_and_cant_find(self):
        self.assertTrue(run("$1 = FIND whale\n$2 = GET $1 IS_A\n$3 = TEST $2 mammal\nANSWER $3").text.startswith("Yes."))
        r = run("$1 = FIND whale\n$2 = GET $1 IS_A\n$3 = TEST $2 tower\nANSWER $3")
        self.assertEqual(r.status, "unsupported")
        self.assertNotIn("Yes", r.text)
        self.assertNotRegex(r.text, r"^No\b")      # absence in the corpus is not a "no"
        # A "no" needs a stated rule (computer/data/rules.json): mammals aren't fish.  Without the rules, it's "can't find".
        r = run("$1 = FIND whale\n$2 = GET $1 IS_A\n$3 = TEST $2 fish\nANSWER $3")
        self.assertEqual((r.status, r.truth, r.rules), ("answered", "FALSE", ["disjoint.animal_classes"]))
        self.assertTrue(r.text.startswith("No."))
        from computer.navigator import Navigator
        from computer.rules import Knowledge
        bare = Navigator(Corpus(DATA).load(), knowledge=Knowledge())
        r = bare.run("$1 = FIND whale\n$2 = GET $1 IS_A\n$3 = TEST $2 fish\nANSWER $3")
        self.assertEqual((r.status, r.truth), ("unsupported", "UNKNOWN"))

    def test_transitive_location(self):
        r = run("$1 = FIND eiffel tower\n$2 = GET $1 ~located\n$3 = TEST $2 france\nANSWER $3")
        self.assertEqual(r.text, "Yes. Eiffel Tower is located in Paris. Paris is located in France.")

    def test_time(self):
        now = run("$1 = FIND france\n$2 = GET $1 ~currency\nANSWER $2")
        then = run("$1 = FIND france\n$2 = GET $1 ~currency\nANSWER $2", year=1990)
        self.assertIn("Euro", now.text)
        self.assertIn("French Franc", then.text)
        self.assertNotIn("Euro", then.text)

    def test_latest_number_only(self):
        r = run("$1 = FIND jupiter\n$2 = GET $1 NUMBER_OF_MOONS\nANSWER $2")
        self.assertIn("95", r.text)
        self.assertNotIn("79", r.text)

    def test_backtracks_to_the_next_candidate(self):
        r = run("$1 = FIND whale shark\n$2 = GET $1 ~breathe\nANSWER $2")
        self.assertTrue(r.answered)
        self.assertTrue(any("backtracked" in s for s in r.steps), r.steps)

    def test_missing_is_admitted(self):
        r = run("$1 = FIND jupiter\n$2 = GET $1 ~capital\nANSWER $2")
        self.assertEqual(r.status, "unsupported")
        self.assertEqual(r.text, "I know about Jupiter, but my corpus doesn't say its capital.")
        self.assertEqual(run("$1 = FIND narnia\n$2 = GET $1 ~capital\nANSWER $2").status, "no_entity")

    def test_evidence_comes_from_the_corpus(self):
        c = corpus()
        rp, nav = RulePlanner(c), Navigator(c)
        for q in [json.loads(l)["question"] for l in open(Path(__file__).parent / "navigator_questions.jsonl")]:
            _, r = navigate(q, [rp], nav)
            for f in (r.evidence if r else []):
                stored = [(x.subject, x.relation, x.value) for x in c.facts_for(f.subject)]
                if (f.subject, f.relation, f.value) not in stored:   # WHO facts come from the reverse index
                    self.assertTrue(any(row["subject"] == f.subject and row["relation"] == f.relation
                                        for row in c.reverse(f.value)), (q, f))


class TestRulePlanner(unittest.TestCase):
    def test_kinds(self):
        rp = RulePlanner(corpus())
        expect = {"what is the population of the capital of france": "chain",
                  "which is bigger, jupiter or saturn": "compare", "is jupiter heavier than saturn": "compare",
                  "is a whale a mammal": "test", "do owls have feathers": "test", "what orbits the sun": "reverse",
                  "how do whales breathe": "explain", "how many moons does jupiter have": "count",
                  "what is the capital of france": "hop"}
        for q, kind in expect.items():
            self.assertEqual(rp.plan(q).kind, kind, q)
        self.assertIsNone(rp.plan("should i learn the violin"))
        self.assertIsNone(rp.plan("what is the capital of narnia"))

    def test_handwritten_questions(self):
        c = corpus()
        rep = evaluate([json.loads(l) for l in open(Path(__file__).parent / "navigator_questions.jsonl")],
                       [LookupSystem(c), NavigatorSystem(c, [RulePlanner(c)], "nav")])
        self.assertEqual(rep["nav"]["total"].get("wrong", 0), 0, rep["nav"])
        self.assertGreater(rep["nav"]["accuracy"], 0.9)
        self.assertGreater(rep["nav"]["accuracy"], rep["lookup"]["accuracy"])


class TestComputer(unittest.TestCase):
    def computer(self, **kw):
        return Computer(Config(corpus=str(DATA), llm_endpoint="", log_file="", **kw))

    def test_auto(self):
        o = self.computer()
        r = o.think("what is the population of the capital of france")
        self.assertEqual(r.text, "France's capital is Paris. Paris's population is 2,100,000 (since 2023).")
        self.assertTrue(any(t.startswith("planner rules [chain]") for t in r.trace))
        self.assertEqual(o.think("what is its population").text, "France's population is 68,000,000 (since 2023).")

    def test_off_keeps_the_old_lookup(self):
        r = self.computer(navigator="off").think("what is the population of the capital of france")
        self.assertNotIn("2,100,000", r.text)

    def test_sensitive_and_instinct_untouched(self):
        o = self.computer()
        self.assertIn("doctor", o.think("should I stop taking my medicine").text)
        self.assertEqual(o.think("should I visit jupiter").faculty, "instinct")
        self.assertEqual(o.think("what is 12 times 7").text, "12 times 7 is 84.")

    def test_lm_planner_plan_is_used_and_grammar_sent(self):
        from fake_llm import Fake
        fake = Fake(["$1 = FIND germany\n$2 = GET $1 CAPITAL\n$3 = GET $2 POPULATION\nANSWER $3"])
        try:
            o = Computer(Config(corpus=str(DATA), llm_endpoint=fake.url, llm_mode="off", navigator_planner="lm", log_file=""))
            r = o.think("how many folks are in the german capital")
            self.assertIn("3,800,000", r.text)
            self.assertIn("grammar", fake.requests[0])
            self.assertEqual(fake.requests[0]["temperature"], 0.0)
        finally:
            fake.close()

    def test_invalid_lm_plan_falls_back_to_rules(self):
        from fake_llm import Fake
        fake = Fake(["$1 = FIND germany\n$2 = DROP TABLE\nANSWER $2"])
        try:
            o = Computer(Config(corpus=str(DATA), llm_endpoint=fake.url, llm_mode="off", navigator_planner="lm", log_file=""))
            r = o.think("what is the population of the capital of germany")
            self.assertIn("3,800,000", r.text)
            self.assertTrue(any("invalid plan" in t for t in r.trace), r.trace)
        finally:
            fake.close()


class TestTrainingData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ex = generate(corpus(), 100, seed=1)

    def test_every_example_runs_and_reaches_its_answer(self):
        self.assertGreater(len(self.ex), 30)
        nav = Navigator(corpus())
        for e in self.ex:
            r = nav.run(parse(e["plan"]))
            self.assertEqual(r.text, e["answer"], e)
            if e["expect"] == "answered":
                self.assertTrue(any(g.lower() in r.text.lower() for g in e["gold"]), e)

    def test_split_by_subject(self):
        for e in self.ex:
            self.assertEqual(e["split"], split_of(e["subjects"][0]))

    def test_chat_rows_match_the_runtime_prompt(self):
        row = as_chat(self.ex[0])
        lm = LMPlanner(None, fewshot=False)
        self.assertEqual(row["messages"][:2], lm.prompt(self.ex[0]["question"]))
        parse(row["messages"][2]["content"])


if __name__ == "__main__":
    unittest.main()
