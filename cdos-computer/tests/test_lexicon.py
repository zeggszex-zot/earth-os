"""The relation lexicon (computer/lexicon.py) and subject/object direction (computer/controller.py roles, wrong_way).

Uses the tiny corpus, whose dictionary rows (RELATION_WORDS in tests/mini_corpus.py) are shaped like OEWN's."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import mini_corpus                                                  # noqa: E402
from computer.controller import ControllerPlanner, Reader, roles, wrong_way  # noqa: E402
from computer.corpus import Corpus                                       # noqa: E402
from computer.lexicon import Lexicon, Mapping, heads, learn              # noqa: E402
from computer.navigator import Navigator                                 # noqa: E402
from computer.plan import parse                                          # noqa: E402
from computer.planner import RulePlanner, navigate                       # noqa: E402

TMP = tempfile.TemporaryDirectory()
DATA = mini_corpus.build(Path(TMP.name) / "data")
_LEX = {}


def corpus(with_lexicon=True):
    c = Corpus(DATA).load()
    if with_lexicon:
        if "lex" not in _LEX:
            _LEX["lex"] = learn(Corpus(DATA).load())
        c.lexicon = _LEX["lex"]
    return c


def ask(c, q):
    plan, r = navigate(q, [RulePlanner(c)], Navigator(c))
    return r


class TestLearning(unittest.TestCase):
    def test_definition_heads(self):
        self.assertEqual(heads("the usual food and drink consumed by an organism (person or animal)"), ["food"])
        self.assertEqual(heads("elevation especially above sea level"), ["elevation"])
        self.assertEqual(heads("the type of environment in which an organism or group normally lives"), ["environment"])
        self.assertEqual(heads("a prescribed selection of foods"), ["selection"])
        self.assertEqual(heads('a seat of government; "a capital city"'), ["seat"])

    def test_food_means_diet_and_the_evidence_is_kept(self):
        m = next(m for m in corpus().lexicon.mappings if m.word == "food")
        self.assertEqual((m.relation, m.status), ("DIET", "accepted"))
        self.assertIn("the usual food and drink", m.via[0]["fact"])
        self.assertIn("animal", m.via[0]["sense_fits"])         # why this sense: DIET's subjects are animals

    def test_the_wrong_sense_is_rejected_with_a_reason(self):
        m = next(m for m in corpus().lexicon.mappings if m.word == "selection")
        self.assertEqual(m.status, "rejected")
        self.assertIn("doesn't fit", m.via[0]["note"])

    def test_synonyms_of_the_relation_word(self):
        lex = corpus().lexicon
        self.assertEqual([m.relation for m in lex.lookup("tongue")], ["OFFICIAL_LANGUAGE"])
        self.assertEqual([m.relation for m in lex.lookup("tallness")], ["HEIGHT"])

    def test_words_that_already_name_a_relation_are_not_learned(self):
        for m in corpus().lexicon.mappings:
            if m.word in ("diet", "capital", "people", "population"):
                self.assertNotEqual(m.status, "accepted", m)

    def test_the_corpus_is_not_changed(self):
        before = Corpus(DATA).load()
        after = corpus()
        for k in ("BLUE_WHALE", "DIET", "OWL"):
            self.assertEqual([str(f) for f in before.facts_for(k)], [str(f) for f in after.facts_for(k)])
        self.assertEqual(before.meta, after.meta)

    def test_learning_from_examples(self):
        lex = learn(Corpus(DATA).load(), [{"question": "what is the grub of an owl", "gold": ["mice"], "expect": "answered"},
                                          {"question": "grub of a blue whale", "gold": ["krill"], "expect": "answered"}])
        m = next(m for m in lex.mappings if m.word == "grub")
        self.assertEqual((m.relation, m.status), ("DIET", "accepted"))
        self.assertEqual(m.via[0]["subjects"], ["BLUE_WHALE", "OWL"])

    def test_round_trip(self):
        p = Path(TMP.name) / "lexicon.json"
        corpus().lexicon.save(p)
        self.assertEqual([m.relation for m in Lexicon.load(p).lookup("food")], ["DIET"])


class TestUsing(unittest.TestCase):
    def test_the_milestone(self):
        c = corpus()
        for q in ("What is the food of a blue whale?", "What does a blue whale eat?", "What is a blue whale's diet?"):
            r = ask(c, q)
            self.assertTrue(r.answered, q)
            self.assertEqual([(f.subject, f.relation, f.value) for f in r.evidence], [("BLUE_WHALE", "DIET", "KRILL")], q)
        r = ask(c, "What is the food of a blue whale?")
        self.assertEqual([m.word for m in r.mapped], ["food"])
        self.assertTrue(any("took 'food' as DIET" in s for s in r.steps))

    def test_without_the_lexicon_food_is_unknown(self):
        self.assertFalse(ask(corpus(with_lexicon=False), "What is the food of a blue whale?").answered)

    def test_a_mapping_only_points_at_relations_the_thing_has(self):
        c = corpus()
        self.assertFalse(ask(c, "food of france").answered)
        self.assertFalse(ask(c, "what is the selection of an owl").answered)
        self.assertFalse(ask(c, "what is the tongue of a whale").answered)

    def test_an_ambiguous_word_is_not_guessed(self):
        c = Corpus(DATA).load()
        c.lexicon = Lexicon([Mapping("stuff", "DIET", 0.7, status="ambiguous"),
                             Mapping("stuff", "HABITAT", 0.7, status="ambiguous")])
        self.assertTrue(ask(c, "the stuff of an owl").answered)       # an owl has a diet and no habitat
        nav = Navigator(c)
        r = nav.run("$1 = FIND whale\n$2 = GET $1 ~stuff INHERIT\nANSWER $2")
        self.assertTrue(r.answered)                               # whale: only HABITAT is inherited...
        c.lexicon = Lexicon([Mapping("stuff", "MASS", 0.7, status="ambiguous"),
                             Mapping("stuff", "RADIUS", 0.7, status="ambiguous")])
        self.assertFalse(ask(c, "the stuff of jupiter").answered)     # ...but Jupiter has both: don't pick one


class TestDirection(unittest.TestCase):
    def setUp(self):
        self.c = corpus()
        self.reader = Reader(self.c)

    def role(self, q):
        return [(x.name, x.role) for x in roles(self.reader.read(q), self.c)]

    def test_word_order_gives_the_roles(self):
        self.assertEqual(self.role("anything orbiting saturn"), [("saturn", "object")])
        self.assertEqual(self.role("titan orbits what"), [("titan", "subject")])
        self.assertEqual(self.role("what does ganymede orbit"), [("ganymede", "subject")])
        self.assertEqual(self.role("what is saturn orbited by"), [("saturn", "object")])
        self.assertEqual(self.role("what eats krill"), [("krill", "object")])
        self.assertIn(("france", "subject"), self.role("what tongue is spoken in france"))

    def test_the_wrong_way_round_is_rejected(self):
        ctl = ControllerPlanner(self.c)
        q = "anything orbiting saturn"
        p = parse("$1 = FIND saturn\n$2 = GET $1 ~orbiting,orbit\nANSWER $2")
        r = ctl.nav.run(p)
        self.assertTrue(r.answered)                         # "Saturn orbits Sun" is true...
        self.assertIn("asks what orbiting saturn", ctl.accept(q, p, r))   # ...and not what was asked
        p = parse("$1 = FIND saturn\n$2 = WHO $1 ~orbiting,orbit\nANSWER $2")
        r = ctl.nav.run(p)
        self.assertEqual(ctl.accept(q, p, r), "")
        self.assertIn("Titan", r.text)

    def test_a_reverse_lookup_that_fell_back_counts_as_forward(self):
        ctl = ControllerPlanner(self.c)
        p = parse("$1 = FIND titan\n$2 = WHO $1 ~orbit\nANSWER $2")     # nothing orbits Titan
        r = ctl.nav.run(p)
        self.assertTrue(any("used its own relation" in s for s in r.steps))
        self.assertTrue(wrong_way(self.reader.read("things that orbit titan"), p, r, self.c))

    def test_find_starts_from_a_name_known_only_as_a_value(self):
        r = Navigator(self.c).run("$1 = FIND krill\n$2 = WHO $1 ~eat\nANSWER $2")
        self.assertTrue(r.answered, r.text)
        self.assertEqual(r.text, "Blue Whale's diet is krill.")
        self.assertEqual(Navigator(self.c).run("$1 = FIND narnia\n$2 = WHO $1 ~eat\nANSWER $2").status, "no_entity")


if __name__ == "__main__":
    unittest.main()
