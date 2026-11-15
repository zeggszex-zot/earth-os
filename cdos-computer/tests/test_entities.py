"""One name, several things (D4).  Rows carry ENTITY=; when a name's facts name two or more entities, the reader
answers about the one the question names (by type or place), else the most prominent, and says which, with the
others.  A name with one entity or none answers exactly as before."""

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_shards                                                                          # noqa: E402
from computer.corpus import Corpus, Fact, choose_entity                                      # noqa: E402
from computer.navigator import Navigator                                                     # noqa: E402
from computer.planner import RulePlanner, navigate                                           # noqa: E402

T = "\t"


def r(s, rel, k, v, u="", src="GEONAMES", sid="x", ent=""):
    return T.join([s, rel, k, v, u, "0", "0", src, sid, "1.0", f"ENTITY={ent}" if ent else ""])


def place(name, ent, kind, places, pop=None):
    rows = [r(name, "IS_A", "S", kind, ent=ent)] + [r(name, "LOCATED_IN", "S", p, ent=ent) for p in places]
    return rows + ([r(name, "POPULATION", "N", pop, ent=ent)] if pop else [])


ROWS = (
    place("Springfield", "gn:4409896", "city", ["United States", "Missouri"], "170188")
    + place("Springfield", "gn:4951788", "city", ["United States", "Massachusetts"], "154341")
    + place("Springfield", "gn:4250542", "city", ["United States", "Illinois"], "114394")
    + place("Paris", "gn:2988507", "city", ["France", "Île-de-France"], "2138551")
    + place("Paris", "gn:4717560", "city", ["United States", "Texas"], "24782")
    + [r("Paris", "NAMED_AFTER", "S", "Parisii", src="WIKIDATA", sid="Q90/P138")]           # written without ENTITY
    + place("Georgia", "gn:614540", "country", [], "3731000")
    + [r("Georgia", "CAPITAL", "S", "Tbilisi", ent="gn:614540")]
    + place("Georgia", "gn:4197000", "region", ["United States"])
    + [r("Georgia", "OFFICIAL_LANGUAGE", "S", "Georgian", src="WIKIDATA", sid="Q230/P37")]   # written without ENTITY
    + place("Luxembourg", "gn:2960313", "country", [], "686970")
    + place("Luxembourg", "gn:2960316", "city", ["Luxembourg"], "76684")
    + place("Illinois", "gn:4896861", "region", ["United States"])
    + [r("Illinois", "POPULATION", "N", "12812508", src="WIKIDATA", sid="Q1204/P1082"),
       r("Texas", "POPULATION", "N", "29145505", src="WIKIDATA", sid="Q1439/P1082"),
       r("Lion", "IS_A", "S", "MAMMAL", src="DOCUMENT", sid="bio")]
)


def build(tmp: Path, name: str, rows) -> Corpus:
    (tmp / f"{name}.pf").write_text("\n".join(rows) + "\n")
    build_shards.build(tmp / f"{name}.pf", tmp / name, 4, 4, fix_keys=True, log=lambda *a: None)
    return Corpus(tmp / name).load()


class EntityCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        cls.c = build(t, "entities", ROWS)
        cls.plain = build(t, "plain", [re.sub(r"\tENTITY=[^\t]*$", "\t", x) for x in ROWS])
        cls.nav = Navigator(cls.c)                      # one navigator for every question, as a session has

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ask(self, q, c=None):
        c = c or self.c
        a = navigate(q, [RulePlanner(c)], self.nav if c is self.c else Navigator(c))[1]
        return a.text if a else "(no answer)"


class TestTheDefaultReading(EntityCase):
    def test_the_most_populous_and_the_others_in_a_note(self):
        self.assertEqual(self.ask("what is the population of springfield"),
                         "Springfield's population is 170,188. (That's Springfield in Missouri; there are also "
                         "Springfield in Massachusetts and Springfield in Illinois.)")
        self.assertEqual(self.ask("what is the population of paris"),
                         "Paris's population is 2,138,551. (That's Paris in Île-de-France; there is also Paris in "
                         "Texas.)")

    def test_the_country_and_the_region(self):
        self.assertEqual(self.ask("what is the capital of georgia"),
                         "Georgia's capital is Tbilisi. (That's Georgia, the country; there is also Georgia, a "
                         "region of the United States.)")
        self.assertEqual(self.ask("what is the population of luxembourg"),
                         "Luxembourg's population is 686,970. (That's Luxembourg, the country; there is also "
                         "Luxembourg in Luxembourg.)")

    def test_rows_without_entity_join_the_default_reading(self):
        self.assertTrue(self.ask("what is paris named after").startswith("Paris is named after Parisii."))
        self.assertTrue(self.ask("what is the official language of georgia").startswith(
            "Georgia's official language is Georgian."))


class TestTheQuestionSaysWhich(EntityCase):
    def test_by_place(self):
        self.assertEqual(self.ask("what is the population of springfield illinois"), "Springfield's population is 114,394.")
        self.assertEqual(self.ask("what is the population of springfield, illinois"),
                         "Springfield's population is 114,394.")
        self.assertEqual(self.ask("where is springfield, massachusetts"),
                         "Springfield is located in United States and Massachusetts.")
        self.assertEqual(self.ask("where is paris texas"), "Paris is located in United States and Texas.")

    def test_by_type(self):
        self.assertEqual(self.ask("where is georgia the state"), "Georgia is located in United States.")
        self.assertEqual(self.ask("what is the population of luxembourg city"), "Luxembourg's population is 76,684.")

    def test_rows_without_entity_stay_with_the_default(self):
        # the US state is chosen: the country's legacy facts (its language) are not its
        self.assertNotIn("Georgian", self.ask("what is the official language of georgia the state"))

    def test_a_question_s_words_dont_carry_over_to_the_next(self):
        self.ask("where is paris texas")
        self.assertTrue(self.ask("what is the population of paris").startswith("Paris's population is 2,138,551."))
        # a later question that reaches Paris without naming it (a reverse lookup, a chain) mustn't find "texas"
        self.ask("where is paris texas")
        self.ask("is a lion a mammal")
        self.assertNotIn("PARIS", self.nav.hints)


class TestNamesWithOneThing(EntityCase):
    def test_answers_are_as_without_entities(self):
        for q in ("what is the population of illinois", "what is the population of texas", "is a lion a mammal"):
            with self.subTest(q):
                self.assertEqual(self.ask(q), self.ask(q, self.plain))
        self.assertEqual(self.ask("what is the population of illinois"), "Illinois's population is 12,812,508.")

    def test_the_lookup_path_says_which_too(self):
        ans = self.c.ask("population of springfield")
        self.assertIn("SPRINGFIELD population: 170188.", ans["text"])
        self.assertIn("(That's Springfield in Missouri; there are also Springfield in Massachusetts and Springfield "
                      "in Illinois.)", ans["notes"])


def facts(*rows):
    return [Fact.from_cols(x.split(T)[:4] + ["", "", "", "GEONAMES", "1.0", x.split(T)[10]]) for x in rows]


class TestTheChoice(unittest.TestCase):
    def test_sitelinks_then_population_then_type_then_id(self):
        both = facts(r("X", "IS_A", "S", "city", ent="a"), r("X", "POPULATION", "N", "900", ent="a"),
                     r("X", "IS_A", "S", "country", ent="b"), r("X", "POPULATION", "N", "100", ent="b"))
        self.assertEqual(choose_entity("X", both)[1].entity, "a")                               # population
        linked = both + facts(r("X", "SITELINKS", "N", "50", ent="b"))
        self.assertEqual(choose_entity("X", linked)[1].entity, "b")                             # sitelinks first
        untyped = facts(r("X", "IS_A", "S", "city", ent="a"), r("X", "IS_A", "S", "region", ent="b"))
        self.assertEqual(choose_entity("X", untyped)[1].entity, "b")                            # region > city
        same = facts(r("X", "IS_A", "S", "city", ent="b"), r("X", "IS_A", "S", "city", ent="a"))
        self.assertEqual(choose_entity("X", same)[1].entity, "a")                               # then the id

    def test_one_entity_or_none_is_left_alone(self):
        one = facts(r("X", "IS_A", "S", "city", ent="a"), r("X", "POPULATION", "N", "9", src="WIKIDATA"))
        self.assertEqual(choose_entity("X", one), (one, None))
        self.assertEqual(choose_entity("X", []), ([], None))


class TestThePublicPath(EntityCase):
    """Computer.think: the path a person's question takes (the one-shot lookup answers simple questions there)."""

    def think(self, q):
        from computer.config import Config
        from computer.core import Computer
        cfg = Config(corpus=str(Path(self.tmp.name) / "entities"), llm_endpoint="", llm_mode="off", log_file="",
                     state_file="")
        return Computer(cfg, self.c).think(q).text

    def test_the_note_is_said_there_too(self):
        self.assertEqual(self.think("what is the population of springfield"),
                         "Springfield's population is 170,188. (That's Springfield in Missouri; there are also "
                         "Springfield in Massachusetts and Springfield in Illinois.)")

    def test_and_a_named_one_is_answered_there(self):
        said = self.think("what is the population of springfield, illinois")
        self.assertIn("114,394", said)
        self.assertNotIn("That's", said)


class TestKinds(unittest.TestCase):
    def test_class_names_say_what_kind_of_thing(self):
        from computer.corpus import kind_of
        for v, k in (("sovereign state", "country"), ("country", "country"), ("U.S. state", "region"),
                     ("state of Australia", "region"), ("province of Canada", "region"), ("commune of France", "city"),
                     ("big city", "city"), ("city", "city"), ("planet", "")):
            with self.subTest(v):
                self.assertEqual(kind_of(v), k)


if __name__ == "__main__":
    unittest.main()
