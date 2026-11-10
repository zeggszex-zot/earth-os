"""Regression tests for the engine defects found by reviewing the production corpus (docs/REAL_CORPUS_REVIEW.md).

Each test rebuilds, in a few facts, the shape of data that exposed a defect in the real corpus, so it runs without
the production data/.  The real case each one stands for is named in its docstring.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import mini_corpus                                            # noqa: E402
from computer.analysis import Analyst                              # noqa: E402
from computer.config import Config                                 # noqa: E402
from computer.corpus import Corpus, Fact, applicable               # noqa: E402
from computer.navigator import Navigator, to_number                # noqa: E402
from computer.phrase import date_text                              # noqa: E402
from computer.planner import RulePlanner, navigate                 # noqa: E402
from computer.quantity import parse                                # noqa: E402
from computer.core import Computer                                    # noqa: E402

TMP = tempfile.TemporaryDirectory()


def _f(s, r, k, v, u="", start=None, until=None, source="WIKIDATA", conf=1.0):
    return (s, r, k, v, u, start, until, source, conf, "")


FACTS = [
    # E1: geological dates (MOON FOUNDED -4527000000 in the real corpus)
    _f("OLD_ROCK", "FOUNDED", "D", "-400000000"), _f("YOUNG_ROCK", "FOUNDED", "D", "-50000000"),
    _f("OLD_ROCK", "IS_A", "S", "ROCK", source="MANUAL"), _f("YOUNG_ROCK", "IS_A", "S", "ROCK", source="MANUAL"),
    # E2: VENUS ORBITS SUN, and nothing orbits Venus
    _f("VENUS", "ORBITS", "S", "SUN", source="DOCUMENT"), _f("VENUS", "IS_A", "S", "PLANET", source="DOCUMENT"),
    # E3: WOLF has no language; LANGUAGE (the word) has facts; OFFICIAL_LANGUAGE is a relation
    _f("WOLF", "IS_A", "S", "MAMMAL", source="DOCUMENT"),
    _f("LANGUAGE", "HAS_PART", "S", "GRAMMAR"), _f("LANGUAGE", "IS_A", "S", "COMMUNICATION"),
    _f("LANGUAGE", "HAS_PART", "S", "SPOKEN_LANGUAGE"), _f("LANGUAGE", "DERIVATION", "S", "SPEAK", source="OEWN"),
    _f("ITALY", "OFFICIAL_LANGUAGE", "S", "ITALIAN", source="TABLE"),
    # E4: lengths in km and m; a mountain whose HEIGHT figures aren't its elevation
    _f("NILE", "LENGTH", "N", "6650.0", "KM", source="TABLE"), _f("NILE", "IS_A", "S", "RIVER", source="TABLE"),
    _f("K2", "HEIGHT", "N", "8611.0", "M", source="TABLE"), _f("K2", "ELEVATION", "N", "8611", "M"),
    _f("BIG_PEAK", "HEIGHT", "N", "3650", "M"), _f("BIG_PEAK", "ELEVATION", "N", "8848.86", "M"),
    _f("BIG_PEAK", "WIDTH", "N", "20", "KM"),
    # E5: several figures for one relation (Everest's four elevations; Jupiter's mass from two sources)
    _f("PEAK_A", "ELEVATION", "N", "8848.86", "M"), _f("PEAK_A", "ELEVATION", "N", "8844.43", "M"),
    _f("PEAK_B", "ELEVATION", "N", "8611", "M"),
    _f("GIANT", "MASS", "N", "1.898e+27", "KG", source="DOCUMENT"), _f("GIANT", "MASS", "N", "1898130", "YOTTAGRAM"),
    _f("DWARF", "MASS", "N", "5.683e+26", "KG", source="DOCUMENT"),
    _f("RIVER_A", "LENGTH", "N", "100", "KM", source="TABLE"), _f("RIVER_A", "LENGTH", "N", "100000", "M"),
    _f("RIVER_B", "LENGTH", "N", "60", "KM", source="TABLE"),
    # E6: a dated series plus undated figures (Germany in 1960; Earth's undated prehistoric estimates)
    _f("GERMANY", "POPULATION", "N", "70945000", start=1954), _f("GERMANY", "POPULATION", "N", "72031000", start=1958),
    _f("GERMANY", "POPULATION", "N", "73147000", start=1960), _f("GERMANY", "POPULATION", "N", "74383000", start=1962),
    _f("GERMANY", "POPULATION", "N", "83122889"), _f("GERMANY", "IS_A", "S", "COUNTRY", source="TABLE"),
    _f("EARTH", "POPULATION", "N", "7000000"), _f("EARTH", "POPULATION", "N", "50000000"),
    _f("EARTH", "POPULATION", "N", "8117059282", start=2025), _f("EARTH", "IS_A", "S", "PLANET", source="DOCUMENT"),
    _f("ISLAND", "POPULATION", "N", "1200"), _f("ISLAND", "IS_A", "S", "PLACE", source="TABLE"),
    # LLM rows (PROXIMA_CENTAURI_B HAS_LIQUID_WATER TRUE, speculative_model_output)
    _f("PROXIMA_CENTAURI_B", "IS_A", "S", "EXOPLANET", source="DOCUMENT", conf=0.9),
    _f("PROXIMA_CENTAURI_B", "HAS_LIQUID_WATER", "S", "TRUE", source="LLM", conf=0.72),
    _f("PROXIMA_CENTAURI", "IS_A", "S", "STAR", source="DOCUMENT"),
    _f("B", "DEFINITION", "T", "the 2nd letter of the Roman alphabet", source="OEWN"),      # WordNet has the letter
    _f("LIQUID_WATER", "DEFINITION", "T", "water in its liquid state", source="OEWN"),
    # E7: reverse lookups cite stored rows (TITAN ORBITS SATURN, DOCUMENT; v rows carry no source)
    _f("RINGED", "IS_A", "S", "PLANET", source="DOCUMENT"),
    _f("TITAN", "ORBITS", "S", "RINGED", source="DOCUMENT", conf=0.98),
    _f("ENCELADUS", "ORBITS", "S", "RINGED", source="DOCUMENT", conf=0.98),
    _f("SPECULATED_MOON", "ORBITS", "S", "RINGED", source="LLM", conf=0.9),
    _f("LOST_MOON", "ORBITS", "S", "RINGED", until=1900, source="DOCUMENT"),
    _f("CHEERFUL", "DEFINITION", "T", "being full of good spirits", source="OEWN"),
    ("CHEERFUL", "SYNONYM", "S", "gleeful", "", None, None, "OEWN", 0.9, "SENSE=WN_a_1"),
]
DATA = mini_corpus.build(Path(TMP.name) / "fixes", FACTS)


def computer():
    return Computer(Config(corpus=str(DATA), llm_endpoint="", llm_mode="off", log_file=""), Corpus(DATA).load())


def say(q):
    return computer().think(q)


class TestE1GeologicalDates(unittest.TestCase):
    """MOON FOUNDED -4527000000 was said as '4527 BC'."""

    def test_said_as_years_ago(self):
        self.assertEqual(date_text("-4527000000"), "about 4.53 billion years ago")
        self.assertEqual(date_text("-400000000"), "about 400 million years ago")
        self.assertEqual(date_text("-753"), "753 BC")                                   # unchanged
        self.assertEqual(date_text("1879-03-14"), "14 March 1879")                       # unchanged
        r = say("when was old rock founded")
        self.assertIn("400 million years ago", r.text)
        self.assertNotIn("BC", r.text)

    def test_compared_in_the_right_order(self):
        # Read as four digits, -400000000 and -50000000 become -4000 and -5000: the younger rock looked older.
        self.assertEqual(to_number(Fact("X", "FOUNDED", "D", "-400000000")), -400000000)
        r = say("which is older, old rock or young rock")
        self.assertIn("so Old Rock is older", r.text)
        self.assertNotIn("-", r.text.split("so")[0].replace("years ago", ""))          # no raw value said


class TestE2ReversedFact(unittest.TestCase):
    """'what orbits venus' got 'Venus orbits Sun': the opposite question's answer."""

    def test_admitted_not_reversed(self):
        r = say("what orbits venus")
        self.assertNotIn("Venus orbits Sun", r.text)
        self.assertIn("doesn't say", r.text)
        self.assertEqual(r.envelope["status"], "unsupported")

    def test_the_forward_question_still_answers(self):
        self.assertIn("Sun", say("what does venus orbit").text)


class TestE3AnswerAboutTheRelationWord(unittest.TestCase):
    """'what language does a wolf speak' got LANGUAGE's facts instead of 'Wolf has no language'."""

    def test_the_navigators_finding_is_said(self):
        r = say("what language does a wolf speak")
        self.assertEqual(r.text, "I know about Wolf, but my corpus doesn't say its language.")
        self.assertNotIn("grammar", r.text.lower())

    def test_a_question_about_the_word_itself_still_answers(self):
        self.assertIn("grammar", say("tell me about language").text.lower())


class TestE4RequirementsInLengthUnits(unittest.TestCase):
    """'is the nile at least 6000 km long' answered with the length and no verdict; km and m weren't stated units."""

    def env(self, q):
        return say(q).envelope

    def test_stated_lengths_masses_areas_parse(self):
        self.assertEqual([(q.dim, q.value) for q in parse("6000 km, 8000 m, 2 tonnes, 500000 km2")],
                         [("length", 6e6), ("length", 8000.0), ("mass", 2000.0), ("area", 5e11)])

    def test_verdicts(self):
        self.assertEqual(self.env("is the nile at least 6000 km long")["truth"], "TRUE")
        self.assertEqual(self.env("is the nile at least 7000 km long")["truth"], "FALSE")
        self.assertEqual(self.env("is the nile at least 6000000 m long")["truth"], "TRUE")
        e = self.env("is the nile at least 6000 km long")
        self.assertIn("unit.km_to_m", e["rules"])
        self.assertEqual(e["certainty"], "ESTABLISHED")
        self.assertNotIn("which is 6,650 kilometres", say("is the nile at least 6000 km long").text)   # nothing converted

    def test_the_word_picks_the_relations(self):
        e = self.env("is big peak at least 8000 m tall")
        self.assertIn("'tall' is read as Big Peak's elevation and height", e["assumptions"])
        self.assertEqual(e["truth"], "UNKNOWN")                    # HEIGHT 3650 and ELEVATION 8848.86 disagree
        self.assertNotIn("20", say("is big peak at least 8000 m tall").text)   # WIDTH isn't a height
        self.assertEqual(self.env("is k2 at least 8000 m tall")["truth"], "TRUE")     # two relations, one value

    def test_running_distance_is_not_a_battery_question(self):
        self.assertIsNone(Analyst(Corpus(DATA).load()).handle("how long can a cheetah run 100 m"))


class TestE5OneFigurePolicy(unittest.TestCase):
    """CALC and COMPARE used the first of several figures (Everest's four elevations) without saying so."""

    def test_equally_ranked_figures_are_not_picked(self):
        r = say("what is the difference in elevation between peak a and peak b")
        self.assertIn("more than one elevation", r.text)
        self.assertIn("won't pick one", r.text)
        self.assertIn("CONFLICT", [e["code"] for e in r.envelope["errors"]])
        self.assertIn("more than one elevation", say("which is higher, peak a or peak b").text)

    def test_a_better_source_is_used_and_the_choice_is_said(self):
        r = say("how many times heavier is giant than dwarf")
        self.assertIn("3.34 times", r.text)
        self.assertTrue(any("by source rank" in a and "document" in a for a in r.envelope["assumptions"]))

    def test_figures_that_agree_are_one_figure(self):
        r = say("how much longer is river a than river b")
        self.assertIn("40 kilometres longer", r.text)
        self.assertFalse(any("source rank" in a for a in r.envelope["assumptions"]))


class TestE6DatedAndUndatedFigures(unittest.TestCase):
    """'population of germany in 1960' got the 1954-58 figures; Earth's population came out as 7,000,000."""

    def test_the_figure_for_the_year(self):
        r = say("what was the population of germany in 1960")
        self.assertIn("73,147,000", r.text)
        for old in ("70,945,000", "72,031,000", "83,122,889"):
            self.assertNotIn(old, r.text)

    def test_the_latest_dated_figure_over_undated_ones(self):
        r = say("what is the population of earth")
        self.assertIn("8,117,059,282", r.text)
        self.assertNotIn("7,000,000", r.text)

    def test_undated_figures_stay_when_nothing_is_dated(self):
        self.assertIn("1200", say("what is the population of island").text)

    def test_the_lookup_and_the_navigator_agree(self):
        c = Corpus(DATA).load()
        facts = applicable(c.facts_for("GERMANY"), 1960, 0.5)
        self.assertEqual([f.value for f in facts if f.relation == "POPULATION"], ["73147000"])
        self.assertEqual([f.value for f in Navigator(c).facts("GERMANY", 1960) if f.relation == "POPULATION"],
                         ["73147000"])


class TestModelRowsAreNotFacts(unittest.TestCase):
    """PROXIMA_CENTAURI_B HAS_LIQUID_WATER TRUE (source LLM, 'speculative_model_output') was said as a fact."""

    def test_never_said(self):
        for q in ("what is proxima centauri b", "tell me about proxima centauri b",
                  "does proxima centauri b have liquid water"):
            self.assertNotIn("liquid water true", say(q).text.lower(), q)
        self.assertIn("exoplanet", say("what is proxima centauri b").text.lower())    # its other facts still are

    def test_kept_in_the_corpus(self):
        self.assertIn("LLM", {f.source for f in Corpus(DATA).load().facts_for("PROXIMA_CENTAURI_B")})


class TestOneLetterIsNotARelation(unittest.TestCase):
    """'does proxima centauri b have liquid water' was read as Proxima Centauri's relation 'b'."""

    def test_the_name_is_kept_whole(self):
        c = Corpus(DATA).load()
        plans = RulePlanner(c).plans("does proxima centauri b have liquid water")
        self.assertFalse(any("~b" in str(p) for p in plans), [str(p) for p in plans])
        self.assertNotIn("its b", say("does proxima centauri b have liquid water").text)


if __name__ == "__main__":
    unittest.main()


class TestE7ReverseLookupsCiteStoredRows(unittest.TestCase):
    """'what orbits saturn' cited TITAN ORBITS SATURN with source '?' and an id no stored row has: the v row was
    turned into a made-up fact.  It also skipped the source and temporal policy of forward lookups."""

    def _stored(self, env, c):
        for x in env["evidence"]:
            if not x.get("derived"):
                self.assertIn(x["id"], {f.id for f in c.facts_for(x["subject"])}, x)
                self.assertNotEqual(x["source"], "?", x)

    def test_who_cites_the_stored_rows(self):
        o = computer()
        r = o.think("what orbits ringed")
        self.assertIn("Titan", r.text)
        self.assertIn("Enceladus", r.text)
        cited = {x["subject"] for x in r.envelope["evidence"]}
        self.assertIn("TITAN", cited)
        self._stored(r.envelope, o.corpus)

    def test_model_rows_and_expired_rows_are_not_named(self):
        r = say("what orbits ringed")
        self.assertNotIn("Speculated", r.text)
        self.assertNotIn("Lost", r.text)

    def test_reverse_facts_resolves_to_forward_rows(self):
        c = Corpus(DATA).load()
        got = c.reverse_facts("RINGED", 2026, 0.0)
        self.assertEqual({f.subject for f in got}, {"TITAN", "ENCELADUS"})
        self.assertTrue(all(f.source == "DOCUMENT" for f in got))
        self.assertEqual(len(c.reverse("RINGED")), 4)            # the v rows themselves are all still there

    def test_a_listed_word_cites_its_stored_row(self):
        o = computer()
        r = o.think("what does gleeful mean")
        self.assertIn("good spirits", r.text)
        self._stored(r.envelope, o.corpus)


class TestE8AccentedKeysAreNames(unittest.TestCase):
    """'what is located in france' said AIGUILLE_DE_LA_GRANDE_SASSIÈRE raw: a key with a non-ASCII capital
    wasn't recognised as a key, so it wasn't turned into a name."""

    def test_named(self):
        from computer.phrase import is_name, name_of  # noqa: F401
        self.assertEqual(name_of("AIGUILLE_DE_LA_GRANDE_SASSIÈRE"), "Aiguille De La Grande Sassière")
        self.assertEqual(name_of("ÉCOLE_NORMALE"), "École Normale")
        self.assertFalse(is_name("Hg"))                         # mixed case is a value, not a key
        self.assertFalse(is_name("theoretical physicist"))
