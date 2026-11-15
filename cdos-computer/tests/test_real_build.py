"""Building shards from a corpus.pf (tools/build_shards.py), the WordNet repair, sense-checked lexicon learning,
the held-out guard, and calibration.  Uses a small corpus.pf written here with the same quirks as the
Phase 1.5 file: swapped ANTONYM/HYPERNYM labels, synset-id values, senses in source_id."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).parent))

import build_shards                                         # noqa: E402
import calibrate_lexicon                                    # noqa: E402
from computer.corpus import Corpus                               # noqa: E402
from computer.lexicon import learn, split_examples               # noqa: E402
from computer.navigator import Navigator                         # noqa: E402
from computer.planner import RulePlanner, navigate               # noqa: E402

ROWS = [
    # curated facts
    ("LION", "IS_A", "MAMMAL", "DOCUMENT", "bio"), ("LION", "DIET", "CARNIVORE", "DOCUMENT", "bio"),
    ("TIGER", "IS_A", "MAMMAL", "DOCUMENT", "bio"), ("TIGER", "DIET", "CARNIVORE", "DOCUMENT", "bio"),
    ("TIGER", "HABITAT", "FOREST", "DOCUMENT", "bio"), ("ZEBRA", "IS_A", "MAMMAL", "DOCUMENT", "bio"),
    ("ZEBRA", "HABITAT", "SAVANNA", "DOCUMENT", "bio"), ("ZEBRA", "DIET", "HERBIVORE", "DOCUMENT", "bio"),
    ("MAMMAL", "IS_A", "ANIMAL", "MANUAL", "schema"), ("ANIMAL", "IS_A", "LIVING_THING", "MANUAL", "schema"),
    ("DIET", "IS_FUNCTIONAL", "TRUE", "MANUAL", "schema"),
    # the dictionary, as the Phase 1.5 import has it
    ("DIET", "SYNSET", "WN_n_00000001", "OEWN", "WN_n_00000001"),
    ("DIET", "DEFINITION", "a prescribed selection of foods", "OEWN", "WN_n_00000001"),
    ("DIET", "SYNONYM", "regime", "OEWN", "WN_n_00000001"),
    ("DIET", "SYNSET", "WN_n_00000002", "OEWN", "WN_n_00000002"),
    ("DIET", "DEFINITION", "the usual food and drink consumed by an organism (person or animal)", "OEWN", "WN_n_00000002"),
    ("DIET", "SYNSET", "WN_v_00000003", "OEWN", "WN_v_00000003"),
    ("DIET", "DEFINITION", "eat sparingly, for health reasons", "OEWN", "WN_v_00000003"),
    ("DIET", "SYNONYM", "slim", "OEWN", "WN_v_00000003"),
    ("ANIMAL", "SYNSET", "WN_n_00000010", "OEWN", "WN_n_00000010"),
    ("ANIMAL", "DEFINITION", "a living organism characterized by voluntary movement", "OEWN", "WN_n_00000010"),
    ("ORGANISM", "SYNSET", "WN_n_00000011", "OEWN", "WN_n_00000011"),
    ("ANIMAL", "ANTONYM", "WN_n_00000011", "OEWN", "WN_n_00000010"),      # really animal's hypernym: organism
    ("PUPPY", "SYNSET", "WN_n_00000020", "OEWN", "WN_n_00000020"),
    ("DOG", "SYNSET", "WN_n_00000021", "OEWN", "WN_n_00000021"),
    ("DOG", "DEFINITION", "a domesticated canid", "OEWN", "WN_n_00000021"),
    ("DOG", "HYPERNYM", "WN_n_00000020", "OEWN", "WN_n_00000021"),         # really a hyponym: puppy
    ("HAPPY", "SYNSET", "WN_a_00000030", "OEWN", "WN_a_00000030"),
    ("UNHAPPY", "SYNSET", "WN_a_00000031", "OEWN", "WN_a_00000031"),
    ("HAPPY", "DEFINITION", "enjoying or showing joy", "OEWN", "WN_a_00000030"),
    ("HAPPY", "WORDNET_POINTER_!", "WN_a_00000031", "OEWN", "WN_a_00000030"),   # the real antonym
    ("HAPPY", "WORDNET_POINTER_;C", "WN_a_00000031", "OEWN", "WN_a_00000030"),
    ("SLIM", "SYNSET", "WN_v_00000004", "OEWN", "WN_v_00000004"),
    ("SLIM", "DEFINITION", "take off weight", "OEWN", "WN_v_00000004"),
]


def write_pf(path, rows=ROWS):
    lines = ["# test corpus", "# subject\trelation\tkind\tvalue\tunit\tvalid_from\tvalid_until\tsource_type\tsource_id\tconfidence\tqualifiers"]
    for s, r, v, st, sid in rows:
        lines.append("\t".join([s, r, "S", v, "", "0", "0", st, sid, "0.95", ""]))
    Path(path).write_text("\n".join(lines) + "\n")


TMP = tempfile.TemporaryDirectory()
PF = Path(TMP.name) / "corpus.pf"
write_pf(PF)
FIXED = Path(TMP.name) / "data"
RAW = Path(TMP.name) / "data-raw"
build_shards.build(PF, FIXED, 8, 8, fix_wordnet=True, log=lambda *a: None)
build_shards.build(PF, RAW, 8, 8, fix_wordnet=False, log=lambda *a: None)


def ask(c, q):
    return navigate(q, [RulePlanner(c)], Navigator(c))[1]


# The same dictionary, beside rows from other sources that use the relation names the WordNet repair renames.
OTHER_SOURCES = [
    ("MIDNIGHT", "ANTONYM", "noon", "WIKIDATA", "Q12143/P461"),        # Wikidata "opposite of": a real antonym
    ("CAT", "HYPERNYM", "feline", "MANUAL", "schema"),                  # a hypernym that means hypernym
]
MIXED_PF = Path(TMP.name) / "mixed.pf"
write_pf(MIXED_PF, ROWS + OTHER_SOURCES)
MIXED = Path(TMP.name) / "data-mixed"
build_shards.build(MIXED_PF, MIXED, 8, 8, fix_wordnet=True, log=lambda *a: None)


class TestTheWordNetRepairIsOnlyForWordNet(unittest.TestCase):
    """D1 (docs/CORPUS_GROWTH_AUDIT.md §4): --fix-wordnet renamed ANTONYM and HYPERNYM for every source, so 425
    Wikidata "opposite of" facts were served as HYPERNYM (MIDNIGHT HYPERNYM noon).  The swapped labels are the OEWN
    import's; another source's rows keep the relation they were written with."""

    def setUp(self):
        self.c = Corpus(MIXED).load()

    def rel(self, key):
        return {(f.relation, f.value) for f in self.c.facts_for(key)}

    def test_another_sources_antonym_stays_an_antonym(self):
        self.assertIn(("ANTONYM", "noon"), self.rel("MIDNIGHT"))
        self.assertNotIn(("HYPERNYM", "noon"), self.rel("MIDNIGHT"))

    def test_another_sources_hypernym_stays_a_hypernym(self):
        self.assertIn(("HYPERNYM", "feline"), self.rel("CAT"))
        self.assertNotIn(("HYPONYM", "feline"), self.rel("CAT"))

    def test_the_oewn_rows_are_still_repaired(self):
        self.assertIn(("HYPERNYM", "organism"), self.rel("ANIMAL"))
        self.assertIn(("HYPONYM", "puppy"), self.rel("DOG"))
        self.assertIn(("WN_ANTONYM", "unhappy"), self.rel("HAPPY"))

    def test_the_opposite_is_answered_as_an_opposite(self):
        self.assertEqual(ask(self.c, "what is the opposite of midnight").text, "The opposite of midnight is noon.")


class TestBuild(unittest.TestCase):
    def test_the_source_file_is_not_written(self):
        self.assertIn("WORDNET_POINTER_!", PF.read_text())

    def test_repair_relabels_and_resolves(self):
        c = Corpus(FIXED).load()
        rel = lambda k: {(f.relation, f.value) for f in c.facts_for(k)}
        self.assertIn(("HYPERNYM", "organism"), rel("ANIMAL"))
        self.assertIn(("HYPONYM", "puppy"), rel("DOG"))
        self.assertIn(("WN_ANTONYM", "unhappy"), rel("HAPPY"))
        self.assertIn(("WN_TOPIC_DOMAIN", "unhappy"), rel("HAPPY"))           # a name the plan language accepts
        self.assertNotIn("SYNSET", c.meta["relation_names"])
        self.assertEqual(ask(c, "what is the opposite of happy").text, "The opposite of happy is unhappy.")

    def test_without_the_repair_the_labels_are_as_imported(self):
        c = Corpus(RAW).load()
        self.assertIn(("ANTONYM", "WN_n_00000011"), {(f.relation, f.value) for f in c.facts_for("ANIMAL")})

    def test_dates_text_and_senses(self):
        c = Corpus(FIXED).load()
        d = [f for f in c.facts_for("DIET") if f.relation == "DEFINITION"]
        self.assertTrue(all(f.kind == "T" and f.start is None for f in d))
        self.assertEqual({f.qual for f in d}, {"SENSE=WN_n_00000001", "SENSE=WN_n_00000002", "SENSE=WN_v_00000003"})
        self.assertNotIn("sense", d[0].sentence().lower())                      # bookkeeping, not content


class TestSenses(unittest.TestCase):
    def setUp(self):
        self.lex = learn(Corpus(FIXED).load())

    def find(self, word, rel="DIET"):
        return next((m for m in self.lex.mappings if m.word == word and m.relation == rel), None)

    def test_the_sense_that_fits_wins(self):
        m = self.find("food")
        self.assertEqual(m.status, "accepted")
        self.assertEqual(m.via[0]["sense"], "WN_n_00000002")
        self.assertIn("animal", m.via[0]["sense_fits"])

    def test_synonyms_of_other_senses_are_not_used(self):
        self.assertEqual(self.find("regime").status, "rejected")       # the "prescribed selection" sense
        self.assertIn("another sense", self.find("regime").via[0]["note"])
        m = self.find("slim")
        self.assertTrue(m is None or m.status == "rejected")            # a verb sense: not "the ___ of X"

    def test_yes_no_and_schema_relations_are_skipped(self):
        self.assertNotIn("IS_FUNCTIONAL", {m.relation for m in self.lex.mappings})

    def test_the_milestone_on_the_built_corpus(self):
        c = Corpus(FIXED).load()
        c.lexicon = self.lex
        r = ask(c, "What is the food of a lion?")
        self.assertTrue(r.answered)
        self.assertEqual([(f.subject, f.relation, f.value) for f in r.evidence], [("LION", "DIET", "CARNIVORE")])


class TestExamplesAndLeaks(unittest.TestCase):
    def test_held_out_questions_and_their_subjects_are_not_learned_from(self):
        c = Corpus(FIXED).load()
        ex = [{"question": "what is the home of a tiger", "gold": ["forest"], "expect": "answered"},
              {"question": "the home of a zebra", "gold": ["savanna"], "expect": "answered"},
              {"question": "what is the grub of a lion", "gold": ["carnivore"], "expect": "answered"}]
        held = [{"question": "the home of a zebra", "gold": ["savanna"], "expect": "answered"},
                {"question": "what does a lion eat", "gold": ["carnivore"], "expect": "answered"}]
        keep, drop = split_examples(c, ex, held)
        self.assertEqual([e["question"] for e in keep], ["what is the home of a tiger"])
        self.assertEqual(sorted(d["dropped"][:5] for d in drop), ["about", "the s"])

    def test_one_thing_is_not_enough(self):
        lex = learn(Corpus(FIXED).load(), [{"question": "what is the home of a tiger", "gold": ["forest"], "expect": "answered"}])
        m = next(m for m in lex.mappings if m.word == "home")
        self.assertEqual(m.status, "rejected")
        lex = learn(Corpus(FIXED).load(), [{"question": "what is the home of a tiger", "gold": ["forest"], "expect": "answered"},
                                           {"question": "the home of a zebra", "gold": ["savanna"], "expect": "answered"}])
        self.assertEqual(next(m for m in lex.mappings if m.word == "home").status, "accepted")

    def test_a_word_the_question_already_explains_is_not_learned(self):
        lex = learn(Corpus(FIXED).load(), [{"question": "what diet does a tiger follow", "gold": ["carnivore"], "expect": "answered"},
                                           {"question": "what diet does a zebra follow", "gold": ["herbivore"], "expect": "answered"}])
        self.assertFalse(any(m.word == "follow" for m in lex.mappings))


class TestCalibration(unittest.TestCase):
    def test_report(self):
        rows = [{"word": "a", "relation": "X", "conf": 0.8, "how": "synonym", "status": "accepted", "correct": True},
                {"word": "b", "relation": "X", "conf": 0.8, "how": "synonym", "status": "accepted", "correct": False},
                {"word": "c", "relation": "X", "conf": 0.6, "how": "examples", "status": "accepted", "correct": None}]
        out = calibrate_lexicon.report(rows)
        self.assertIn("3 mappings, 2 reviewed", out)
        self.assertIn("1/2", out)
        self.assertIn("b->X", out)
        lo, hi = calibrate_lexicon.wilson(1, 2)
        self.assertTrue(0 < lo < 0.5 < hi < 1)


if __name__ == "__main__":
    unittest.main()


class TestWikidataAndKeys(unittest.TestCase):
    """--fix-wikidata and --fix-keys, on rows shaped like the Phase 1.5 Wikidata import."""

    PF = [
        # subject, relation, kind, value, valid_from, valid_until, source_type, source_id
        ("FRANCE", "CAPITAL", "S", "PARIS", "0", "0", "MANUAL", "seed"),
        ("France", "CAPITAL", "S", "Q90", "1789", "0", "WIKIDATA", "Q142/P36"),
        ("France", "CAPITAL", "S", "Q621", "1871", "1879", "WIKIDATA", "Q142/P36"),
        ("France", "IS_A", "S", "Q3624078", "0", "0", "WIKIDATA", "Q142/P31"),        # never imported: dropped
        ("Paris", "AREA", "N", "105.4", "0", "0", "WIKIDATA", "Q90/P2046"),
        ("Versailles", "AREA", "N", "26.2", "0", "0", "WIKIDATA", "Q621/P2046"),
        ("Q937", "OCCUPATION", "S", "Q19350898", "0", "0", "WIKIDATA", "Q937/P106"),
        ("Albert Einstein", "BORN", "D", "1879", "0", "0", "WIKIDATA", "Q937/P569"),
        ("theoretical physicist", "DEFINITION", "S", "a physicist", "0", "0", "WIKIDATA", "Q19350898/description"),
        ("Yemen", "CAPITAL", "S", "Q2471", "1990", "0", "WIKIDATA", "Q805/P36"),
        ("Sanaa", "AREA", "N", "126", "0", "0", "WIKIDATA", "Q2471/P2046"),
        ("Q2471", "ELEVATION", "N", "2250", "0", "0", "WIKIDATA", "Q2471/P2044"),     # a bare-id subject with a name
        # two different things called Mars, and a quantity in yottagrams
        ("Mars", "DEFINITION", "S", "fourth planet", "0", "0", "WIKIDATA", "Q111/description"),
        ("Mars", "DEFINITION", "S", "Roman god of war", "0", "0", "WIKIDATA", "Q112/description"),
        ("Mars", "NAMED_AFTER", "S", "Q112", "0", "0", "WIKIDATA", "Q111/P138"),
        ("Mars", "SAME_AS", "S", "Q111", "0", "0", "WIKIDATA", "Q111/P460"),           # really points at itself
        ("Mars", "MASS", "N", "641.691", "0", "0", "WIKIDATA", "Q111/P2067"),
    ]

    def build(self, **fix):
        d = Path(tempfile.mkdtemp())
        unit = lambda r, v: "WD_Q613726" if (r, v) == ("MASS", "641.691") else ""
        lines = ["# t", "# header"] + ["\t".join([s, r, k, v, unit(r, v), vf, vu, st, sid, "1.00", ""])
                                       for s, r, k, v, vf, vu, st, sid in self.PF]
        (d / "corpus.pf").write_text("\n".join(lines) + "\n")
        meta, report = build_shards.build(d / "corpus.pf", d / "data", 4, 4, log=lambda *a: None, **fix)
        return Corpus(d / "data").load(), report

    def test_without_the_fixes_the_wikidata_rows_cant_be_found(self):
        c, _ = self.build()
        self.assertFalse(c.has("YEMEN"))             # stored as "Yemen"; the computer asks for YEMEN
        self.assertTrue(any(f.value == "Q90" for f in c.facts_for("France")))

    def test_ids_resolve_and_keys_match(self):
        c, report = self.build(fix_wikidata=True, fix_keys=True)
        self.assertTrue(c.has("YEMEN"))
        caps = {(f.value, f.start, f.until) for f in c.facts_for("YEMEN") if f.relation == "CAPITAL"}
        self.assertEqual(caps, {("Sanaa", 1990, None)})
        france = {f.value for f in c.facts_for("FRANCE") if f.relation == "CAPITAL"}
        self.assertEqual(france, {"PARIS", "Paris", "Versailles"})     # curated and Wikidata twins, one subject
        self.assertFalse([f for f in c.facts_for("FRANCE") if f.relation == "IS_A"])   # Q3624078: no name, dropped
        self.assertEqual(report["dropped: Wikidata value with no name in the corpus"], 1)
        self.assertTrue(c.has("ALBERT_EINSTEIN"))
        self.assertEqual([f.value for f in c.facts_for("ALBERT_EINSTEIN") if f.relation == "OCCUPATION"],
                         ["theoretical physicist"])                      # Q937 subject and Q19350898 value, both named
        self.assertTrue(any(f.relation == "ELEVATION" for f in c.facts_for("SANAA")))   # Q2471 subject -> Sanaa
        self.assertFalse(any(QID_LIKE(f.value) for k in c.subjects for f in c.facts_for(k)))

    def test_same_name_is_not_the_same_thing(self):
        c, report = self.build(fix_wikidata=True, fix_keys=True)
        rels = {(f.relation, f.value, f.unit) for f in c.facts_for("MARS")}
        self.assertIn(("NAMED_AFTER", "Mars", ""), rels)                 # the planet is named after the god: kept
        self.assertFalse([r for r in rels if r[0] == "SAME_AS"])          # Q111 on Q111's own row: dropped
        self.assertIn(("MASS", "641.691", "YOTTAGRAM"), rels)             # a checked unit id, named

    def test_one_word_two_senses_stays_two_facts(self):
        d = Path(tempfile.mkdtemp())
        rows = [("CHERRY", "SYNONYM", "S", "cherry red", "", "", "", "OEWN", "WN_a_00383741", "1.0", ""),
                ("Cherry", "SYNONYM", "S", "cherry red", "", "", "", "OEWN", "WN_n_07757132", "1.0", "")]
        (d / "corpus.pf").write_text("# t\n" + "\n".join("\t".join(r) for r in rows) + "\n")
        build_shards.build(d / "corpus.pf", d / "data", 4, 4, fix_keys=True, log=lambda *a: None)
        c = Corpus(d / "data").load()
        self.assertEqual(len([f for f in c.facts_for("CHERRY") if f.relation == "SYNONYM"]), 2)

    def test_the_computer_answers_from_the_fixed_build(self):
        c, _ = self.build(fix_wikidata=True, fix_keys=True)
        nav = Navigator(c)
        _, r = navigate("what is the capital of yemen", [RulePlanner(c)], nav, year=2026)
        self.assertIn("Sanaa", r.text)
        r = nav.run("$1 = FIND france\n$2 = GET $1 CAPITAL\nANSWER $2", year=1875)
        self.assertIn("Versailles", r.text)           # dated facts keep their dates


def QID_LIKE(v):
    import re
    return bool(re.fullmatch(r"Q\d+", v or ""))


class TestWordsThatAreAlsoThings(unittest.TestCase):
    def test_the_thing_holding_the_answer_is_what_its_about(self):
        from computer.lexicon import about
        import mini_corpus
        F = lambda s, r, v: (s, r, "S", v, "", None, None, "WIKIDATA", 1.0, "")
        d = Path(tempfile.mkdtemp())
        c = Corpus(mini_corpus.build(d, list(mini_corpus.F) + [F("HOME", "IS_A", "DWELLING"),
                                                               F("TIGER", "HABITAT", "FOREST"),
                                                               F("TIGER", "IS_A", "MAMMAL")])).load()
        ex = {"question": "what is the home of a tiger", "gold": ["forest"], "expect": "answered"}
        self.assertEqual(about(c, ex), {"TIGER"})
        self.assertEqual(about(c, {**ex, "gold": []}), {"HOME", "TIGER"})      # no answer to go by: both, as before
