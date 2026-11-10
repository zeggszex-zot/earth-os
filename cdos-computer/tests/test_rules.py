"""Four-valued logic (computer/logic.py), the rule engine and rules file (computer/rules.py, computer/data/rules.json),
FALSE / CONFLICT / LOGIC in the navigator, boolean questions, and the knowledge pathway in the computer."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import mini_corpus                                                       # noqa: E402
from computer import logic                                                    # noqa: E402
from computer.config import Config                                            # noqa: E402
from computer.corpus import Corpus                                            # noqa: E402
from computer.logic import CONFLICT, FALSE, TRUE, UNKNOWN                     # noqa: E402
from computer.navigator import Navigator                                      # noqa: E402
from computer.plan import PlanError, parse                                    # noqa: E402
from computer.planner import RulePlanner, navigate                            # noqa: E402
from computer.rules import (MAX_STEPS, RULES_FILE, Engine, Knowledge, RuleError, evaluate, load,  # noqa: E402
                       parse_knowledge, parse_rules, run_pathway)
from computer.core import PATHWAY_ACTIONS, Computer                              # noqa: E402

TMP = tempfile.TemporaryDirectory()
DATA = mini_corpus.build(Path(TMP.name) / "data")
F = lambda s, r, v: (s, r, "S", v, "", None, None, "MANUAL", 1.0, "")
# The same tiny corpus, contradicting itself twice: a shark that is a fish and a mammal, a Titan with two primaries.
ODD = mini_corpus.build(Path(TMP.name) / "odd", list(mini_corpus.F) + [F("SHARK", "IS_A", "MAMMAL"),
                                                                     F("TITAN", "ORBITS", "JUPITER")])


def computer(data=DATA, **kw):
    return Computer(Config(corpus=str(data), llm_mode="off", llm_endpoint="", log_file="", **kw))


class TestLogic(unittest.TestCase):
    def test_and_or_not_nor(self):
        self.assertEqual(logic.and_(TRUE, TRUE), TRUE)
        self.assertEqual(logic.and_(TRUE, UNKNOWN), UNKNOWN)
        self.assertEqual(logic.and_(FALSE, UNKNOWN), FALSE)          # one FALSE settles an AND
        self.assertEqual(logic.and_(FALSE, CONFLICT), FALSE)
        self.assertEqual(logic.and_(CONFLICT, UNKNOWN), CONFLICT)    # "it disagrees" outranks "it doesn't say"
        self.assertEqual(logic.or_(TRUE, CONFLICT), TRUE)            # one TRUE settles an OR
        self.assertEqual(logic.or_(FALSE, UNKNOWN), UNKNOWN)
        self.assertEqual(logic.or_(FALSE, FALSE), FALSE)
        self.assertEqual([logic.not_(v) for v in (TRUE, FALSE, UNKNOWN, CONFLICT)], [FALSE, TRUE, UNKNOWN, CONFLICT])
        self.assertEqual(logic.nor_(FALSE, FALSE), TRUE)
        self.assertEqual(logic.nor_(FALSE, UNKNOWN), UNKNOWN)
        self.assertEqual(logic.both(True, True), CONFLICT)
        self.assertEqual(logic.both(False, False), UNKNOWN)
        with self.assertRaises(ValueError):
            logic.and_(TRUE, 0.9)              # a relevance score is not a truth value
        with self.assertRaises(ValueError):
            logic.combine("NOT", [TRUE, FALSE])


class TestConditions(unittest.TestCase):
    def test_missing_is_unknown_not_false(self):
        self.assertEqual(evaluate({"equals": ["stage", "x"]}, {}), UNKNOWN)
        self.assertEqual(evaluate({"exists": "gpu"}, {}), UNKNOWN)
        self.assertEqual(evaluate({"exists": "gpu"}, {"gpu": ""}), FALSE)
        self.assertEqual(evaluate({"not": {"fact": "a"}}, {}), UNKNOWN)

    def test_operators(self):
        st = {"n": 5, "tags": ["a", "b"], "ok": True, "t": CONFLICT}
        self.assertEqual(evaluate({"greater_than": ["n", 3]}, st), TRUE)
        self.assertEqual(evaluate({"less_than": ["n", 3]}, st), FALSE)
        self.assertEqual(evaluate({"contains": ["tags", "b"]}, st), TRUE)
        self.assertEqual(evaluate({"in": ["n", [1, 5]]}, st), TRUE)
        self.assertEqual(evaluate({"fact": "t"}, st), CONFLICT)
        self.assertEqual(evaluate({"all": [{"fact": "ok"}, {"none": [{"equals": ["n", 1]}, {"equals": ["n", 2]}]}]}, st), TRUE)
        self.assertEqual(evaluate({"any": [{"equals": ["n", 1]}, {"exists": "missing"}]}, st), UNKNOWN)
        self.assertEqual(evaluate({"greater_than": ["tags", 1]}, st), UNKNOWN)    # not a number: can't say


ACTS = {"go": {"where"}, "stop": set(), "wait": set()}


class TestRules(unittest.TestCase):
    def test_priority_then_else_unknown(self):
        rules = parse_rules([
            {"rule_id": "low", "when": {"fact": "x"}, "then": {"action": "stop"}, "priority": 1},
            {"rule_id": "high", "when": {"equals": ["mode", "a"]}, "then": {"action": "go", "args": {"where": "a"}},
             "else": {"action": "wait"}, "unknown": {"action": "go", "args": {"where": "ask"}}, "priority": 9}], ACTS)
        e = Engine(rules)
        self.assertEqual((e.decide({"mode": "a"}).action, e.decide({"mode": "a"}).args), ("go", {"where": "a"}))
        self.assertEqual(e.decide({"mode": "b"}).branch, "else")
        d = e.decide({})
        self.assertEqual((d.branch, d.args, d.truth), ("unknown", {"where": "ask"}, UNKNOWN))

    def test_unknown_without_a_branch_doesnt_fire(self):
        e = Engine(parse_rules([{"rule_id": "r", "when": {"fact": "x"}, "then": {"action": "stop"}}], ACTS))
        trace = []
        self.assertIsNone(e.decide({}, trace))
        self.assertIn("UNKNOWN", trace[0])

    def test_validation_refuses_what_it_cant_run(self):
        bad = [
            [{"rule_id": "r", "when": {"fact": "x"}, "then": {"action": "rm -rf /"}}],         # not one of the actions
            [{"rule_id": "r", "when": {"fact": "x"}, "then": {"action": "go", "args": {"cmd": "x"}}}],  # arg it doesn't take
            [{"rule_id": "r", "when": {"fact": "x"}, "then": {"action": "go", "args": {"where": ["a"]}}}],
            [{"rule_id": "r", "when": {"xor": []}, "then": {"action": "stop"}}],
            [{"rule_id": "r", "when": {"greater_than": ["n", "3"]}, "then": {"action": "stop"}}],
            [{"rule_id": "r", "when": {"fact": "x"}}],
            [{"rule_id": "r", "when": {"fact": "x"}, "then": {"action": "stop"}, "exec": "x"}],
            [{"rule_id": "r", "when": {"fact": "x"}, "then": {"action": "stop"}}] * 2,
            [{"rule_id": "r", "when": {"not": {"not": {"not": {"not": {"not": {"not": {"not": {"fact": "x"}}}}}}}},
              "then": {"action": "stop"}}],
        ]
        for raw in bad:
            with self.assertRaises(RuleError, msg=json.dumps(raw)[:80]):
                parse_rules(raw, ACTS)

    def test_pathways_are_bounded(self):
        e = Engine(parse_rules([{"rule_id": "loop", "when": {"fact": "x"}, "then": {"action": "wait"}}], ACTS))
        n = []
        trace = []
        out, ds = run_pathway(e, {"x": True}, {"wait": lambda st: n.append(1)}, trace)
        self.assertIsNone(out)
        self.assertEqual(len(n), MAX_STEPS)
        self.assertIn("stopped after", trace[-1])

    def test_the_rules_file(self):
        r = load()
        for name, raw in r["pathways"].items():
            parse_rules(raw, PATHWAY_ACTIONS[name])
        self.assertIn("ORBITS", r["knowledge"].complete)
        from computer.rules import main
        self.assertEqual(main([str(RULES_FILE)]), 0)

    def test_knowledge_validation(self):
        with self.assertRaises(RuleError):
            parse_knowledge({"disjoint": [{"rule_id": "d", "classes": ["mammal", "fish"]}]})   # keys, not words
        with self.assertRaises(RuleError):
            parse_knowledge({"complete": [{"rule_id": "c", "relation": "ORBITS", "max": 0}]})


def run(plan, data=DATA, **kw):
    return Navigator(Corpus(data).load(), **kw).run(plan)


class TestTruthInTheNavigator(unittest.TestCase):
    def test_false_needs_a_rule(self):
        r = run("$1 = FIND shark\n$2 = GET $1 IS_A\n$3 = TEST $2 marine mammal\nANSWER $3")
        self.assertEqual((r.truth, r.status), (FALSE, "answered"))
        self.assertEqual(r.text, "No. Shark is a fish. Marine mammal is a mammal. Nothing is both a fish and a mammal.")
        r = run("$1 = FIND shark\n$2 = GET $1 IS_A\n$3 = TEST $2 marine mammal\nANSWER $3", knowledge=Knowledge())
        self.assertEqual(r.truth, UNKNOWN)

    def test_true_still_true(self):
        r = run("$1 = FIND whale\n$2 = GET $1 IS_A\n$3 = TEST $2 animal\nANSWER $3")
        self.assertEqual((r.truth, r.rules), (TRUE, []))

    def test_conflict_is_said_not_settled(self):
        r = run("$1 = FIND shark\n$2 = GET $1 IS_A\n$3 = TEST $2 mammal\nANSWER $3", data=ODD)
        self.assertEqual((r.status, r.truth), ("conflict", CONFLICT))
        self.assertTrue(r.text.startswith("My corpus disagrees with itself here."))
        self.assertIn("won't pick one", r.text)

    def test_more_values_than_a_rule_allows(self):
        r = run("$1 = FIND titan\n$2 = GET $1 ORBITS\nANSWER $2", data=ODD)
        self.assertEqual(r.status, "conflict")
        self.assertIn("orbits one thing directly", r.text)
        self.assertEqual(run("$1 = FIND titan\n$2 = GET $1 ORBITS\nANSWER $2").status, "answered")

    def test_complete_relations(self):
        self.assertEqual(run("$1 = FIND titan\n$2 = GET $1 ORBITS\n$3 = TEST $2 jupiter\nANSWER $3").truth, FALSE)
        self.assertEqual(run("$1 = FIND titan\n$2 = GET $1 ORBITS\n$3 = TEST $2 sun\nANSWER $3").truth, TRUE)
        self.assertEqual(run("$1 = FIND titan\n$2 = GET $1 ORBITS\n$3 = TEST $2 star\nANSWER $3").truth, TRUE)
        # every relation at once proves nothing about one of them ("food of france" isn't "the capital isn't food")
        self.assertNotEqual(run("$1 = FIND france\n$2 = GET $1 *\n$3 = TEST $2 berlin\nANSWER $3").truth, FALSE)

    def test_logic_step(self):
        r = run("$1 = FIND whale\n$2 = GET $1 IS_A\n$3 = TEST $2 mammal\n$4 = TEST $2 fish\n$5 = LOGIC AND $3 $4\nANSWER $5")
        self.assertEqual((r.truth, r.status), (FALSE, "answered"))
        self.assertTrue(r.text.startswith("No, not both."))
        self.assertEqual(r.final, {"kind": "test", "yes": False, "truth": FALSE})
        r = run("$1 = FIND whale\n$2 = GET $1 IS_A\n$3 = TEST $2 tower\n$4 = TEST $2 fish\n$5 = LOGIC OR $3 $4\nANSWER $5")
        self.assertEqual((r.truth, r.status), (UNKNOWN, "unsupported"))

    def test_logic_plans_are_checked(self):
        ok = "$1 = FIND whale\n$2 = GET $1 IS_A\n$3 = TEST $2 mammal\n"
        for bad in ["$4 = LOGIC NOT $3 $3\nANSWER $4", "$4 = LOGIC AND $3\nANSWER $4", "$4 = LOGIC XOR $3 $3\nANSWER $4",
                    "$4 = LOGIC AND $3 $2\nANSWER $4"]:
            with self.assertRaises(PlanError, msg=bad):
                parse(ok + bad)
        parse(ok + "$4 = LOGIC NOT $3\n$5 = LOGIC OR $3 $4\nANSWER $5")


QUESTIONS = [   # question, truth, how the answer starts
    ("is a shark a mammal", FALSE, "No."),
    ("is a whale a mammal and a fish", FALSE, "No, not both."),
    ("is a whale a mammal or a fish", TRUE, "Yes, at least one."),
    ("is an owl a fish or a mammal", FALSE, "No, neither."),
    ("is a shark neither a mammal nor a bird", TRUE, "Right, neither."),
    ("is a whale not a fish", TRUE, "Right, it isn't."),
    ("are whales mammals or fish", TRUE, "Yes, at least one."),
    ("does titan orbit jupiter", FALSE, "No. Titan orbits Saturn."),
    ("does titan orbit the sun", TRUE, "Yes."),
    ("does the moon orbit a planet", TRUE, "Yes."),
    ("is the capital of france paris", TRUE, "Yes."),
    ("is a whale a plant", FALSE, "No."),
    ("is a whale a tower", UNKNOWN, "I can't find that"),
]


class TestQuestions(unittest.TestCase):
    """Written alongside the code, so they show the machinery works, not how well it generalises."""

    def test_questions(self):
        c = Corpus(DATA).load()
        nav = Navigator(c)
        for q, truth, start in QUESTIONS:
            _, r = navigate(q, [RulePlanner(c)], nav)
            self.assertIsNotNone(r, q)
            self.assertEqual(r.truth, truth, f"{q}: {r.text}")
            self.assertTrue(r.text.startswith(start), f"{q}: {r.text}")


class TestOptInRules(unittest.TestCase):
    def test_capital_is_complete_only_if_you_say_so(self):
        """South Africa has three capitals; a corpus listing one would make "is cape town its capital" a confident
        "No".  So CAPITAL isn't in the built-in rules; a rules_file can add it for a corpus where it holds."""
        from computer.rules import parse_knowledge
        k = parse_knowledge({"complete": [{"rule_id": "complete.capital", "relation": "CAPITAL", "why": "checked"}]})
        r = run("$1 = FIND france\n$2 = GET $1 CAPITAL\n$3 = TEST $2 berlin\nANSWER $3", knowledge=k)
        self.assertEqual((r.truth, r.rules), (FALSE, ["complete.capital"]))
        r = run("$1 = FIND france\n$2 = GET $1 CAPITAL\n$3 = TEST $2 berlin\nANSWER $3")
        self.assertEqual(r.truth, UNKNOWN)
        c = Corpus(DATA).load()           # by default the question gets the fact, not a "No"
        _, r = navigate("is the capital of france berlin", [RulePlanner(c)], Navigator(c))
        self.assertEqual(r.text, "France's capital is Paris.")


class TestThePathway(unittest.TestCase):
    def test_rules_show_in_the_trace(self):
        r = computer().think("is a shark a mammal")
        self.assertTrue(r.text.startswith("No."))
        t = "\n".join(r.trace)
        self.assertIn("rule knowledge.navigate_first", t)
        self.assertIn("rule knowledge.navigator_answers", t)
        self.assertIn("knowledge rules: disjoint.animal_classes (FALSE)", t)

    def test_a_conflict_isnt_papered_over_by_the_lookup(self):
        r = computer(ODD).think("what does titan orbit")
        self.assertIn("disagrees with itself", r.text)
        self.assertIn("rule knowledge.conflict_is_said", "\n".join(r.trace))

    def test_the_pathway_is_data(self):
        raw = json.loads(RULES_FILE.read_text())
        for rule in raw["pathways"]["knowledge"]:
            if rule["rule_id"] == "knowledge.navigate_first":     # skip the navigator: lookup only
                rule["then"] = {"action": "lookup"}
        p = Path(TMP.name) / "lookup_only.json"
        p.write_text(json.dumps(raw))
        o = computer(rules_file=str(p))
        self.assertEqual(o.rules_error, "")
        r = o.think("what is the capital of france")
        self.assertIn("Paris", r.text)
        self.assertFalse(any(t.startswith("planner ") for t in r.trace))

    def test_a_broken_rules_file_falls_back(self):
        p = Path(TMP.name) / "broken.json"
        p.write_text(json.dumps({"format": "cdos-computer-rules/1", "pathways": {"knowledge": [
            {"rule_id": "x", "when": {"fact": "a"}, "then": {"action": "run_shell"}}]}}))
        o = computer(rules_file=str(p))
        self.assertIn("unknown action 'run_shell'", o.rules_error)
        self.assertIn("Paris", o.think("what is the capital of france").text)

    def test_health_and_money_still_get_a_person(self):
        o = computer()
        self.assertIn("doctor", o.think("what is a good dose of aspirin").text)


if __name__ == "__main__":
    unittest.main()
