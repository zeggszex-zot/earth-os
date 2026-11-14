"""Dictionary questions: word senses, other words, opposites, kinds, soft matching, follow-ups.

Uses the WordNet-shaped words in tests/mini_corpus.py (LEXICAL), so it runs without data/.
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
from computer.corpus import Corpus, rank_by_overlap                # noqa: E402
from computer.navdata import LookupSystem, NavigatorSystem, evaluate, generate  # noqa: E402
from computer.navigator import Navigator, word_forms               # noqa: E402
from computer.plan import GRAMMAR, PlanError, parse                # noqa: E402
from computer.planner import LEX, Context, RulePlanner, navigate   # noqa: E402
from computer.proposer import check                                # noqa: E402
from computer.router import classify                               # noqa: E402
from computer.core import Computer                                    # noqa: E402

TMP = tempfile.TemporaryDirectory()
DATA = mini_corpus.build(Path(TMP.name) / "data")
HERE = Path(__file__).parent


def corpus():
    return Corpus(DATA).load()


def run(plan):
    return Navigator(corpus()).run(plan)


def load(name):
    with open(HERE / name) as fh:
        return [json.loads(l) for l in fh if l.strip()]


class TestPlanLanguage(unittest.TestCase):
    def test_rank_and_except_parse(self):
        p = parse("$1 = FIND bank\n$2 = GET $1 ~definition\n$3 = RANK $2 river\n$4 = EXCEPT $3 glad, joyful\nANSWER $4")
        self.assertEqual([s.op for s in p.steps], ["FIND", "GET", "RANK", "EXCEPT", "ANSWER"])
        self.assertIn('"RANK ', GRAMMAR)
        self.assertIn('"EXCEPT ', GRAMMAR)

    def test_rank_and_except_misuse_rejected(self):
        for bad in ("$1 = FIND bank\n$2 = RANK $1 river\nANSWER $2",            # nothing to rank yet
                    "$1 = FIND x\n$2 = GET $1 IS_A\n$3 = TEST $2 y\n$4 = EXCEPT $3 z\nANSWER $4",
                    "$1 = FIND x\n$2 = GET $1 ~a\n$3 = RANK $2 a, b\nANSWER $3"):  # RANK takes words, not a list
            with self.assertRaises(PlanError, msg=bad):
                parse(bad)


class TestExecutor(unittest.TestCase):
    def test_other_words_are_one_sentence(self):
        r = run(f"$1 = FIND happy\n$2 = GET $1 {LEX['synonym']}\nANSWER $2")
        self.assertEqual(r.text, "Other words for happy: glad, joyful and cheerful.")

    def test_rank_picks_the_sense(self):
        river = run(f"$1 = FIND bank\n$2 = GET $1 {LEX['define']}\n$3 = RANK $2 river\nANSWER $3")
        money = run(f"$1 = FIND bank\n$2 = GET $1 {LEX['define']}\n$3 = RANK $2 money\nANSWER $3")
        self.assertIn("sloping land", river.text)
        self.assertNotIn("financial", river.text)
        self.assertIn("financial institution", money.text)
        self.assertNotIn("sloping", money.text)

    def test_rank_with_no_match_keeps_every_sense(self):
        r = run(f"$1 = FIND bank\n$2 = GET $1 {LEX['define']}\n$3 = RANK $2 xylophone\nANSWER $3")
        self.assertIn("sloping", r.text)
        self.assertIn("financial", r.text)

    def test_rank_by_overlap_ties_keep_corpus_order(self):
        order, scores = rank_by_overlap(["a b", "c d"], "nothing here")
        self.assertEqual(order, [0, 1])
        self.assertEqual(scores, [0, 0])

    def test_except_skips_what_was_said_and_admits_the_end(self):
        r = run(f"$1 = FIND happy\n$2 = GET $1 {LEX['synonym']}\n$3 = EXCEPT $2 glad, joyful\nANSWER $3")
        self.assertEqual(r.text, "Other words for happy: cheerful.")
        r = run(f"$1 = FIND happy\n$2 = GET $1 {LEX['synonym']}\n$3 = EXCEPT $2 glad, joyful, cheerful\nANSWER $3")
        self.assertEqual(r.status, "unsupported")
        self.assertIn("That's all", r.text)

    def test_soft_matching_says_what_it_did(self):
        cases = {"happier": "I don't have happier on its own, but I have happy.",
                 "joyful": "I don't have joyful on its own, but my corpus lists it as another word for happy.",
                 "colour": "I think you mean color."}
        for word, said in cases.items():
            r = run(f"$1 = FIND {word}\n$2 = GET $1 {LEX['define']}\nANSWER $2")
            self.assertTrue(r.answered, word)
            self.assertTrue(r.text.startswith(said), r.text)
        self.assertIn("happy", word_forms("happier"))
        self.assertIn("big", word_forms("biggest"))

    def test_soft_matching_never_invents(self):
        self.assertEqual(run("$1 = FIND narnia\n$2 = GET $1 ~definition\nANSWER $2").status, "no_entity")
        # every fact behind a soft answer is a stored fact (or the reverse-index row that linked the word)
        c = corpus()
        r = Navigator(c).run(f"$1 = FIND joyful\n$2 = GET $1 {LEX['define']}\nANSWER $2")
        for f in r.evidence:
            stored = {(x.subject, x.relation, x.value) for x in c.facts_for(f.subject)}
            self.assertIn((f.subject, f.relation, f.value), stored)

    def test_a_kind_of_follows_the_dictionary_and_curated_links(self):
        r = run(f"$1 = FIND dog\n$2 = GET $1 {LEX['hypernym']}\n$3 = TEST $2 animal\nANSWER $3")
        self.assertTrue(r.answered)
        self.assertIn("Carnivore is a kind of mammal. Mammal is a kind of animal.", r.text)

    def test_synonyms_are_not_followed_transitively(self):
        # sad ~ unhappy, unhappy ~ sad: that must not become a "yes" for anything further along
        r = run(f"$1 = FIND sad\n$2 = GET $1 {LEX['synonym']}\n$3 = TEST $2 happy\nANSWER $3")
        self.assertEqual(r.status, "unsupported")
        self.assertFalse(r.text.startswith("Yes"))

    def test_missing_dictionary_relation_is_admitted(self):
        r = run(f"$1 = FIND dog\n$2 = GET $1 {LEX['antonym']}\nANSWER $2")
        self.assertEqual(r.status, "unsupported")
        self.assertEqual(r.text, "I know the word dog, but my corpus doesn't have an opposite for dog.")

    def test_count_meanings(self):
        r = run(f"$1 = FIND bank\n$2 = GET $1 {LEX['define']}\n$3 = COUNT $2\nANSWER $3")
        self.assertTrue(r.text.startswith("My corpus has 2 meanings for bank:"), r.text)

    def test_proposer_check_holds_lexical_words_to_the_evidence(self):
        r = run(f"$1 = FIND happy\n$2 = GET $1 {LEX['antonym']}\nANSWER $2")
        self.assertEqual(check("The opposite of happy is unhappy.", r), "")
        self.assertNotEqual(check("The opposite of happy is Miserable.", r), "")


class TestRulePlanner(unittest.TestCase):
    def test_kinds(self):
        rp = RulePlanner(corpus())
        expect = {"what does happy mean": "define", "define puppy": "define", "carnivore definition": "define",
                  "what's another word for happy": "synonym", "small synonyms": "synonym",
                  "what is the opposite of big": "antonym", "what kind of thing is a dog": "hypernym",
                  "what are some kinds of dog": "hyponym", "what words are related to happy": "derivation",
                  "use happy in a sentence": "example", "what does the opposite of happy mean": "lex_chain",
                  "is glad a synonym of happy": "lex_test", "how many meanings does bank have": "lex_count"}
        for q, kind in expect.items():
            self.assertEqual(rp.plan(q).kind, kind, q)

    def test_sense_words_become_rank(self):
        p = RulePlanner(corpus()).plan("what does bank mean, as in a river")
        self.assertEqual(str(p).splitlines()[-2], "$3 = RANK $2 river")

    def test_old_questions_keep_their_plans(self):
        rp = RulePlanner(corpus())
        self.assertIsNone(rp.plan("should i learn the violin"))
        self.assertIsNone(rp.plan("what is the capital of narnia"))
        self.assertEqual(rp.plan("is a whale a mammal").kind, "test")
        self.assertEqual(str(rp.plan("is a whale a mammal")).splitlines()[1], "$2 = GET $1 IS_A")

    def test_handwritten_and_paraphrased_questions(self):
        c = corpus()
        for name in ("lexical_questions.jsonl", "lexical_paraphrases.jsonl"):
            ex = load(name)
            rep = evaluate(ex, [LookupSystem(c), NavigatorSystem(c, [RulePlanner(c)], "nav")])
            self.assertEqual(rep["nav"]["total"].get("wrong", 0), 0, (name, rep["nav"]))
            self.assertGreater(rep["nav"]["accuracy"], 0.9, name)
            self.assertGreater(rep["nav"]["accuracy"], rep["lookup"]["accuracy"], name)

    def test_lexical_evidence_comes_from_the_corpus(self):
        c = corpus()
        rp, nav = RulePlanner(c), Navigator(c)
        for ex in load("lexical_questions.jsonl"):
            c.last_subject = ""
            _, r = navigate(ex["question"], [rp], nav)
            for f in (r.evidence if r else []):
                if any(row["subject"] == f.subject and row["relation"] == f.relation for row in c.reverse(f.value)):
                    continue
                self.assertIn((f.subject, f.relation, f.value), {(x.subject, x.relation, x.value)
                                                                  for x in c.facts_for(f.subject)}, ex["question"])


class TestConversation(unittest.TestCase):
    def computer(self):
        return Computer(Config(corpus=str(DATA), llm_endpoint="", log_file=""))

    def test_word_follow_ups(self):
        o = self.computer()
        self.assertEqual(o.think("what's another word for happy").text, "Other words for happy: glad, joyful and cheerful.")
        self.assertIn("That's all", o.think("another one").text)
        self.assertEqual(o.think("and the opposite?").text, "The opposite of happy is unhappy.")
        self.assertIn("sadness or sorrow", o.think("what does it mean").text)      # the answer, unhappy
        self.assertTrue(o.think("what about sad").text.startswith("Sad:"))

    def test_sense_follow_up(self):
        o = self.computer()
        self.assertIn("can mean", o.think("what does bank mean").text)
        money = o.think("what about money?").text
        self.assertIn("financial", money)
        self.assertNotIn("sloping", money)
        self.assertNotIn("financial", o.think("no, as in a river").text)

    def test_fact_follow_ups(self):
        o = self.computer()
        o.think("what is the capital of france")
        self.assertEqual(o.think("what about germany").text, "Germany's capital is Berlin.")
        self.assertIn("doesn't list any other capital", o.think("another one").text)
        self.assertIn("Germany borders France", o.think("tell me more").text)

    def test_a_new_question_is_not_taken_as_a_follow_up(self):
        o = self.computer()
        o.think("what is the capital of france")
        self.assertEqual(o.think("and what is the population of spain").text, "Spain's population is 48,000,000 (since 2023).")
        o.think("what does bank mean")
        self.assertEqual(o.think("and what is the capital of germany").text, "Germany's capital is Berlin.")

    def test_another_skips_what_was_said(self):
        c = corpus()
        ctx = Context()
        rp, nav = RulePlanner(c, ctx), Navigator(c)
        p, r = navigate("what are some kinds of dog", [rp], nav)
        ctx.remember(p, r)
        self.assertEqual(ctx.said, ["puppy", "poodle", "dalmatian"])
        p2 = rp.plan("another one")
        self.assertEqual(p2.kind, "more")
        self.assertIn("EXCEPT $2 puppy, poodle, dalmatian", str(p2))

    def test_more_after_the_coins_is_still_the_coins(self):
        o = self.computer()
        o.think("should I get a dog?")
        r = o.think("what does that mean")
        self.assertEqual(r.faculty, "instinct")
        self.assertIsNotNone(r.perspective)

    def test_router(self):
        for q in ("what is the opposite of happy", "use happy in a sentence", "small synonyms",
                  "what kind of animal is a whale", "give me a fancy word for big"):
            self.assertEqual(classify(q).intent, "define", q)
        self.assertEqual(classify("should I get a dog").faculty, "instinct")


class TestTrainingData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ex = generate(corpus(), 200, seed=3)

    def test_lexical_examples_are_made_and_reach_their_answers(self):
        kinds = {e["kind"] for e in self.ex}
        for k in ("define", "synonym", "antonym", "hypernym", "lex_yes"):
            self.assertIn(k, kinds)
        nav = Navigator(corpus())
        for e in self.ex:
            r = nav.run(parse(e["plan"]))
            self.assertEqual(r.text, e["answer"], e)
            for n in e.get("not", []):
                self.assertNotIn(n.lower(), r.text.lower(), e)

    def test_lexical_can_be_turned_off(self):
        ex = generate(corpus(), 200, seed=3, lexical=0)
        self.assertFalse({"define", "synonym", "antonym", "lex_yes", "sense"} & {e["kind"] for e in ex})


if __name__ == "__main__":
    unittest.main()
