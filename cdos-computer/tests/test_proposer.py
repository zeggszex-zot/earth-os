"""The model proposes, the executor checks (computer/proposer.py)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import mini_corpus                                     # noqa: E402
from fake_llm import Fake                              # noqa: E402
from computer.config import Config                          # noqa: E402
from computer.corpus import Corpus                          # noqa: E402
from computer.navdata import as_chat, generate              # noqa: E402
from computer.navigator import Navigator                    # noqa: E402
from computer.planner import RulePlanner, navigate          # noqa: E402
from computer.proposer import PROPOSAL_GRAMMAR, Proposer, check, parse_proposal  # noqa: E402
from computer.core import Computer                             # noqa: E402

TMP = tempfile.TemporaryDirectory()
DATA = mini_corpus.build(Path(TMP.name) / "data")
C = Corpus(DATA).load()
RULES, NAV = RulePlanner(C), Navigator(C)


def result(q):
    C.last_subject = ""
    return navigate(q, [RULES], NAV)[1]


class TestCheck(unittest.TestCase):
    def verdicts(self, q, says):
        r = result(q)
        return {s: check(s, r, q) for s in says}

    def test_accepts_grounded_wording(self):
        for q, s in [("what is the population of the capital of france",
                      "Paris is France's capital, and about 2,100,000 people live there."),
                     ("which is bigger, jupiter or saturn", "Jupiter is bigger than Saturn."),
                     ("which is bigger, jupiter or saturn", "Hoo! Jupiter is the bigger one, with a radius of 69,911 kilometres."),
                     ("is a whale a mammal", "Yes! Whales are marine mammals, and marine mammals are mammals."),
                     ("what orbits the sun", "Earth, Jupiter and Saturn all orbit the Sun."),
                     ("how many moons does saturn have", "Saturn has 2 moons that I know of: Titan and Enceladus.")]:
            self.assertEqual(check(s, result(q), q), "", (q, s))

    def test_rejects_what_the_evidence_doesnt_back(self):
        q = "what is the population of the capital of france"
        v = self.verdicts(q, ["Paris is France's capital, with 3,000,000 people.",       # wrong number
                              "France's capital is Lyon, with 2,100,000 people.",         # invented name
                              "Paris has 2100000 people, the most in Europe.",            # extra claim
                              "The capital of France is Paris.",                          # doesn't answer
                              "Paris is the capital, home to 2.1 million people.",        # number not as evidenced
                              "Paris is not France's capital; 2,100,000 live there."])    # negation
        for s, why in v.items():
            self.assertNotEqual(why, "", s)

    def test_comparison_direction(self):
        q = "which is bigger, jupiter or saturn"
        self.assertIn("doesn't say Jupiter", check("Saturn is bigger than Jupiter.", result(q), q))
        self.assertNotEqual(check("Saturn is smaller than Jupiter.", result(q), q), "")   # conservative: template instead
        self.assertEqual(check("negates", None), "the corpus doesn't support an answer")

    def test_no_words_without_an_answer(self):
        q = "what is the capital of jupiter"
        self.assertEqual(check("Jupiter's capital is Zeus City.", result(q), q), "the corpus doesn't support an answer")
        # A rule-backed "no" is an answer, but only template output say it: a model's "yes" never gets through.
        q = "is a shark a mammal"
        self.assertEqual(check("Yes, a shark is a mammal.", result(q), q),
                         "a no, an unknown or a conflict is output in the computer's template words")

    def test_yes_questions_need_a_yes(self):
        q = "is a whale a mammal"
        self.assertEqual(check("A whale is a fish.", result(q), q), "doesn't say yes")


class TestParse(unittest.TestCase):
    def test_plan_and_words(self):
        p = parse_proposal("$1 = FIND france\n$2 = GET $1 CAPITAL\nANSWER $2\nSAY: It's Paris!")
        self.assertEqual(p.say, "It's Paris!")
        self.assertEqual(len(p.plan.steps), 3)

    def test_words_only_and_bad_plans(self):
        self.assertIsNone(parse_proposal("$1 = FIND x\nANSWER $1"))
        p = parse_proposal("SAY: Paris.")
        self.assertIsNone(p.plan)
        p = parse_proposal("$1 = FIND x\n$2 = EXPLODE\nANSWER $2\nSAY: boom")
        self.assertTrue(p.plan_error)

    def test_grammar(self):
        self.assertIn('"SAY: " say', PROPOSAL_GRAMMAR)


class TestComputerProposes(unittest.TestCase):
    PLAN = "$1 = FIND germany\n$2 = GET $1 CAPITAL\n$3 = GET $2 POPULATION\nANSWER $3\n"

    def computer(self, replies, mode="propose"):
        fake = Fake(replies)
        o = Computer(Config(corpus=str(DATA), llm_endpoint=fake.url, llm_mode="off", navigator_planner=mode, log_file=""))
        return o, fake

    def ask(self, replies, q="how many people live in the german capital", mode="propose"):
        o, fake = self.computer(replies, mode)
        try:
            return o.think(q), fake
        finally:
            fake.close()

    def test_checked_words_are_spoken(self):
        r, fake = self.ask([self.PLAN + "SAY: Berlin is Germany's capital, and 3,800,000 people live there."])
        self.assertEqual(r.text, "Berlin is Germany's capital, and 3,800,000 people live there.")
        self.assertNotEqual(r.voice, "computer")
        self.assertIn("grammar", fake.requests[0])
        self.assertIn("SAY", fake.requests[0]["grammar"])

    def test_invented_number_falls_back_to_the_template(self):
        r, _ = self.ask([self.PLAN + "SAY: Berlin has 4 million people!"])
        self.assertEqual(r.text, "Germany's capital is Berlin. Berlin's population is 3,800,000 (since 2023).")
        self.assertEqual(r.voice, "computer")
        self.assertTrue(any("rejected" in t for t in r.trace), r.trace)

    def test_plan_that_finds_nothing_never_voices(self):
        r, _ = self.ask(["$1 = FIND jupiter\n$2 = GET $1 ~capital\nANSWER $2\nSAY: Jupiter's capital is Io."],
                        q="what is the population of the capital of germany")
        self.assertNotIn("Io", r.text)
        self.assertIn("3,800,000", r.text)       # the rules answered instead
        self.assertEqual(r.voice, "computer")

    def test_direct_answer_is_checked_against_the_rules(self):
        q = "what is the population of the capital of germany"
        r, fake = self.ask(["SAY: About 3,800,000 people live in Berlin, Germany's capital."], q=q, mode="direct")
        self.assertEqual(r.text, "About 3,800,000 people live in Berlin, Germany's capital.")
        self.assertEqual(fake.requests[0]["messages"][0]["content"][:28], "You are a reasoning computer")
        r, _ = self.ask(["SAY: About 5,000,000 people live in Berlin."], q=q, mode="direct")
        self.assertIn("3,800,000", r.text)
        self.assertEqual(r.voice, "computer")

    def test_direct_answer_with_no_grounding_is_dropped(self):
        r, _ = self.ask(["SAY: Narnia's capital is Cair Paravel."], q="what is the capital of narnia", mode="direct")
        self.assertNotIn("Cair", r.text)

    def test_sensitive_and_coins_unchanged(self):
        o, fake = self.computer(["SAY: Yes, stop it."])
        try:
            self.assertIn("doctor", o.think("should I stop taking my medicine").text)
            self.assertEqual(fake.requests, [])     # the proposer was never asked
        finally:
            fake.close()


class TestData(unittest.TestCase):
    def test_formats(self):
        ex = generate(C, 60, seed=2)
        e = next(x for x in ex if x["expect"] == "answered")
        prop = as_chat(e, "propose")["messages"]
        self.assertEqual(prop[:2], Proposer(None, fewshot=False).prompt(e["question"]))
        p = parse_proposal(prop[2]["content"])
        self.assertIsNotNone(p.plan)
        self.assertEqual(check(p.say, NAV.run(p.plan), e["question"]), "")   # template answers pass their own check
        direct = as_chat(e, "direct")["messages"]
        self.assertEqual(direct[:2], Proposer(None, fewshot=False, direct=True).prompt(e["question"]))
        self.assertTrue(direct[2]["content"].startswith("SAY: "))


if __name__ == "__main__":
    unittest.main()
