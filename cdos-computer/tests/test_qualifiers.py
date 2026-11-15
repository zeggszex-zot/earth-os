"""The qualifiers column (computer/qualifiers.py): KEY=VALUE separated by ";".  SENSE= and ENTITY= say which fact a
row is and are never said, so a row carrying them answers exactly as the same row without them; content qualifiers
(REFERENCE=, MODE=, ...) still shape the answer."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_shards                                                                          # noqa: E402
from computer import pfrow                                                                   # noqa: E402
from computer.corpus import Corpus, Fact                                                     # noqa: E402
from computer.lexicon import sense_of                                                        # noqa: E402
from computer.navigator import Navigator                                                     # noqa: E402
from computer.planner import RulePlanner, navigate                                           # noqa: E402
from computer.qualifiers import get, problems, qualifiers, shown                             # noqa: E402

T = "\t"

# (subject, relation, kind, value, unit, the qualifier the row has without any bookkeeping)
ROWS = [
    ("LION", "IS_A", "S", "MAMMAL", "", ""),
    ("MAMMAL", "IS_A", "S", "ANIMAL", "", ""),
    ("FRANCE", "CAPITAL", "S", "Paris", "", ""),
    ("EARTH", "ORBITAL_PERIOD", "N", "365.25", "DAY", "REFERENCE=SUN"),
    ("EARTH", "MASS", "N", "5.97e24", "KILOGRAM", ""),
    ("ROUTER", "POWER_DRAW", "N", "3", "WATT", "MODE=IDLE"),
    ("ROUTER", "POWER_DRAW", "N", "6.4", "WATT", "MODE=LOAD"),
    ("HAPPY", "DEFINITION", "T", "enjoying or showing joy", "", "SENSE=s1"),
    ("HAPPY", "DEFINITION", "T", "marked by good fortune", "", "SENSE=s2"),
    ("HAPPY", "WN_ANTONYM", "S", "unhappy", "", "SENSE=s1"),
    ("HAPPY", "SYNONYM", "S", "glad", "", "SENSE=s1"),
    ("GERMANY", "POPULATION", "N", "83200000", "", ""),
]
QUESTIONS = [
    "is a lion a mammal", "is a lion an animal", "what is the capital of france",
    "what is the orbital period of earth", "what is the mass of earth", "what is the power draw of the router",
    "what does happy mean", "what is the opposite of happy", "what is a synonym for happy",
    "what is the population of germany",
]


def entity_added(i, q):
    """The same qualifier with an ENTITY= after it (a different entity on every row)."""
    return (q + ";" if q else "") + f"ENTITY=wd:Q{100 + i}"


def several(i, q):
    """ENTITY= first, then the row's own qualifier, then (for rows that have none) a second bookkeeping SENSE=."""
    return ";".join(x for x in (f"ENTITY=wd:Q{200 + i}", q, "" if q else f"SENSE=x{i}") if x)


def build(tmp: Path, name: str, qual_of) -> Corpus:
    pf = tmp / f"{name}.pf"
    pf.write_text("\n".join(T.join([s, r, k, v, u, "0", "0", "DOCUMENT", f"src{i}", "1.0", qual_of(i, q)])
                            for i, (s, r, k, v, u, q) in enumerate(ROWS)) + "\n")
    build_shards.build(pf, tmp / name, 4, 4, fix_keys=True, log=lambda *a: None)
    return Corpus(tmp / name).load()


def answer(c: Corpus, q: str) -> str:
    a = navigate(q, [RulePlanner(c)], Navigator(c))[1]
    return a.text if a else "(no answer)"


class TestParsing(unittest.TestCase):
    def test_every_existing_qualifier_is_one_pair(self):
        for q in ("REFERENCE=SUN", "TARGET=GRAM", "YEAR_TYPE=LEAP_YEAR", "STAGE=INFANT", "DEFINITION=EXACT",
                  "METHOD=COSMIC_MICROWAVE_BACKGROUND", "SENSE=WN_n_02084071"):
            with self.subTest(q):
                self.assertEqual(qualifiers(q), [tuple(q.split("=", 1))])
                self.assertEqual(problems(q), [])

    def test_several_and_what_is_shown(self):
        q = "REFERENCE=SUN;ENTITY=wd:Q1150;SENSE=x"
        self.assertEqual(qualifiers(q), [("REFERENCE", "SUN"), ("ENTITY", "wd:Q1150"), ("SENSE", "x")])
        self.assertEqual(shown(q), "REFERENCE=SUN")
        self.assertEqual(shown("ENTITY=wd:Q1"), "")
        self.assertEqual(get(q, "ENTITY"), "wd:Q1150")

    def test_malformed_qualifiers_are_problems(self):
        for q in ("A=", "=1", "A=1;;B=2", "a=1", "A=1 B=2", "A=1;B"):
            with self.subTest(q):
                self.assertTrue(problems(q))
                self.assertIn("qualifier isn't KEY=VALUE[;KEY=VALUE...]", pfrow.field_problems("S", "x", "", "", "", q))

    def test_the_fact_key_reads_sense_and_entity_wherever_they_are(self):
        k = lambda q: pfrow.fact_key("MERCURY", "IS_A", "S", "x", "", None, None, q, True)  # noqa: E731
        self.assertEqual(k("ENTITY=wd:Q1;REFERENCE=SUN")[-1], "ENTITY=wd:Q1")
        self.assertEqual(k("REFERENCE=SUN;SENSE=s1")[-2], "SENSE=s1")
        self.assertNotEqual(k("REFERENCE=SUN;ENTITY=wd:Q1"), k("REFERENCE=SUN;ENTITY=wd:Q2"))

    def test_sense_of_finds_the_sense_anywhere(self):
        self.assertEqual(sense_of(Fact("A", "SYNONYM", "S", "b", qual="ENTITY=wd:Q1;SENSE=s9")), "s9")
        self.assertEqual(sense_of(Fact("A", "SYNONYM", "S", "b", qual="ENTITY=wd:Q1")), "")


class TestSameAnswers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        cls.plain = build(t, "plain", lambda i, q: q)
        cls.entity = build(t, "entity", entity_added)
        cls.several = build(t, "several", several)
        cls.bare = build(t, "bare", lambda i, q: "")                  # no qualifiers at all

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_questions_are_answered_at_all(self):
        for q in QUESTIONS:
            with self.subTest(q):
                self.assertNotEqual(answer(self.plain, q), "(no answer)")

    def test_entity_and_several_qualifiers_answer_exactly_as_without(self):
        for q in QUESTIONS:
            with self.subTest(q):
                expected = answer(self.plain, q)
                self.assertEqual(answer(self.entity, q), expected)
                self.assertEqual(answer(self.several, q), expected)

    def test_each_fact_is_said_the_same(self):
        def said(c):
            return sorted(f.sentence() for s in ("LION", "FRANCE", "EARTH", "ROUTER", "HAPPY", "GERMANY")
                          for f in c.facts_for(s))
        self.assertEqual(said(self.entity), said(self.plain))
        self.assertEqual(said(self.several), said(self.plain))
        self.assertFalse(any("entity" in s or "sense" in s for s in said(self.several)))

    def test_content_qualifiers_still_count(self):
        self.assertIn("relative to Sun", answer(self.several, "what is the orbital period of earth"))
        self.assertIn("mode idle", answer(self.several, "what is the power draw of the router"))
        self.assertNotEqual(answer(self.bare, "what is the orbital period of earth"),
                            answer(self.plain, "what is the orbital period of earth"))

    def test_the_evidence_keeps_the_entity(self):
        f = next(f for f in self.entity.facts_for("FRANCE") if f.relation == "CAPITAL")
        self.assertEqual(get(f.qual, "ENTITY"), "wd:Q102")
        g = next(f for f in self.plain.facts_for("FRANCE") if f.relation == "CAPITAL")
        self.assertNotEqual(f.id, g.id)                              # a different fact, said the same way


if __name__ == "__main__":
    unittest.main()
