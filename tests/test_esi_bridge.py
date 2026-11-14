"""ESI phase 2: the CDOS Dictionary Bridge.

The corpus here is a small one, built with CDOS's own test fixture, holding
exactly the traps the real corpus set — each found by reading the bridge's
output against the 1.2-million-fact corpus, and each recorded here so it
cannot come back:

* "net" and "network" share WordNet's fishing-net sense (hairnets, mesh);
* "consume" and "devour" share the eating-greedily sense ("demolish");
* "desktop"'s computing sense is the screen background, not the computer;
* "C.P.U." loses its dots and becomes three single letters;
* "store" is WordNet's synonym for computer memory, and also a supply of
  cigars — which the memory question would then start answering.

What the bridge may never do is the other half: approve anything itself,
approve without a name, approve what it did not propose, or let a word in
that breaks anything phase 1 guarantees.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cdos-computer"))
sys.path.insert(0, str(ROOT / "cdos-computer" / "tests"))

from earth_os import dictionary_bridge as bridge                 # noqa: E402
from earth_os.capability_corpus import CapabilityCorpus          # noqa: E402
from earth_os.lexical import Approved, Lexicon, load_approved    # noqa: E402

import mini_corpus                                               # noqa: E402


def sense(subject, sid, rows, topic=""):
    """Facts for one sense: (relation, value) pairs, all qualified SENSE=sid."""
    out = []
    for relation, value in rows:
        kind = "T" if relation == "DEFINITION" else "S"
        out.append((subject, relation, kind, value, "", None, None, "OEWN", 0.95, f"SENSE={sid}"))
    if topic:
        out.append((subject, "WN_TOPIC_DOMAIN", "S", topic, "", None, None, "OEWN", 0.9,
                    f"SENSE={sid}"))
    return out


FACTS = (
    # memory: a computing sense, and the mind's
    sense("MEMORY", "WN_n_1", [("DEFINITION", "an electronic memory device"),
                               ("SYNONYM", "computer memory"), ("SYNONYM", "store"),
                               ("HYPERNYM", "memory device"),
                               ("HYPONYM", "virtual memory"), ("HYPONYM", "scratchpad")])
    + sense("MEMORY", "WN_n_2", [("DEFINITION", "the power of retaining past experience"),
                                 ("SYNONYM", "retention")])
    # ram: a sheep, and computer memory
    + sense("RAM", "WN_n_3", [("DEFINITION", "uncastrated adult male sheep"), ("SYNONYM", "tup")])
    + sense("RAM", "WN_n_4", [("DEFINITION", "the most common computer memory"),
                              ("SYNONYM", "random access memory"),
                              ("HYPERNYM", "volatile storage")])
    # store: also a supply of things
    + sense("STORE", "WN_n_5", [("DEFINITION",
                                 'a supply of something; "he brought back a large store of cigars"'),
                                ("SYNONYM", "stock")])
    + sense("VIRTUAL_MEMORY", "WN_n_6",
            [("DEFINITION", "memory simulated by the hard disk")], topic="computer science")
    + sense("SCRATCHPAD", "WN_n_7", [("DEFINITION", "a pad of paper for jotting notes")])
    # network: the fishing net
    + sense("NETWORK", "WN_n_8", [("DEFINITION", "an open fabric of string"),
                                  ("SYNONYM", "net"), ("SYNONYM", "mesh"),
                                  ("HYPONYM", "hairnet")])
    # use, and the eating-greedily sense of consume
    + sense("USE", "WN_v_9", [("DEFINITION", "use up (resources or materials)"),
                              ("SYNONYM", "consume"), ("SYNONYM", "use up"),
                              ("SYNONYM", "deplete")])
    + sense("CONSUME", "WN_v_10", [("DEFINITION", "eat immoderately"),
                                   ("SYNONYM", "devour"), ("SYNONYM", "demolish")])
    # desktop: computing, but the screen background
    + sense("DESKTOP", "WN_n_11",
            [("DEFINITION", "(computer science) the area of the screen behind the icons"),
             ("SYNONYM", "screen background")], topic="computer science")
    # cpu: a dotted abbreviation among its synonyms
    + sense("CPU", "WN_n_12", [("DEFINITION", "the part of a computer that does the work"),
                               ("SYNONYM", "c.p.u."), ("SYNONYM", "central processing unit")],
            topic="computer science")
)


class _WithCorpus(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        cls.data = mini_corpus.build(Path(cls._dir.name) / "data", facts=FACTS)
        cls.dictionary = bridge.Dictionary(bridge.open_corpus(cls.data))

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()


class TestFindingWhereAConceptLives(_WithCorpus):

    def _anchors(self, concept):
        return {a.sense for a in bridge.anchors(self.dictionary, concept)}

    def test_the_computing_sense_is_found_and_the_others_are_not(self):
        self.assertEqual(self._anchors("memory"), {"WN_n_1", "WN_n_4"})

    def test_two_of_esis_words_agreeing_is_not_enough_for_the_machine(self):
        """"net" and "network" agree on the fishing net."""
        self.assertNotIn("WN_n_8", self._anchors("network"))

    def test_computing_is_not_enough_either_it_must_name_the_concept(self):
        """Desktop's computing sense is the screen background. Its definition
        starts "(computer science)", which is a label, not a mention."""
        self.assertNotIn("WN_n_11", self._anchors("computer"))

    def test_for_other_concepts_the_name_word_must_be_in_the_sense(self):
        """"consume" and "devour" share eating greedily; "use" does not."""
        self.assertIn("WN_v_9", self._anchors("use"))
        self.assertNotIn("WN_v_10", self._anchors("use"))


class TestProposing(_WithCorpus):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.candidates, _ = bridge.propose(cls.dictionary)
        bridge.check_all(cls.candidates, bridge.Checker(approved=Approved()))
        cls.by_key = {c.key: c for c in cls.candidates}

    def test_good_candidates_pass(self):
        for key in (("random access memory", "memory"), ("computer memory", "memory"),
                    ("central processing unit", "cpu"), ("deplete", "use")):
            self.assertIn(key, self.by_key, key)
            self.assertEqual(self.by_key[key].status, "passed", self.by_key[key].checks)
            self.assertGreater(self.by_key[key].confidence, 0)

    def test_the_traps_are_never_proposed(self):
        words = {c.word for c in self.candidates}
        for word in ("mesh", "hairnet", "demolish", "screen background", "tup", "retention"):
            self.assertNotIn(word, words)

    def test_an_abbreviation_without_its_dots_is_blocked(self):
        self.assertEqual(self.by_key[("c p u", "cpu")].status, "blocked")

    def test_a_kind_must_be_about_computing_itself(self):
        self.assertEqual(self.by_key[("virtual memory", "memory")].status, "passed")
        scratch = self.by_key[("scratchpad", "memory")]
        self.assertEqual(scratch.status, "blocked")
        self.assertIn("own_evidence", scratch.checks)

    def test_a_word_that_would_answer_its_other_meanings_is_blocked(self):
        """WordNet's own example for store-as-supply would start reading memory."""
        store = self.by_key[("store", "memory")]
        self.assertEqual(store.status, "blocked")
        self.assertIn("cigars", store.checks["other_meanings_quiet"])

    def test_blocked_candidates_have_no_confidence(self):
        for cand in self.candidates:
            if cand.status == "blocked":
                self.assertEqual(cand.confidence, 0.0)

    def test_every_candidate_carries_its_evidence(self):
        for cand in self.candidates:
            self.assertTrue(cand.sense and cand.why and cand.anchor, cand)


class TestTheChecks(unittest.TestCase):
    """Every phase 1 guarantee, asked again with the word added."""

    @classmethod
    def setUpClass(cls):
        cls.checker = bridge.Checker(approved=Approved())

    def test_a_word_that_changes_a_phrase_is_blocked(self):
        self.assertIn("network status", self.checker.check("status", "wifi")["phrases_unchanged"])

    def test_a_word_that_lowers_the_scores_is_blocked(self):
        """Adding "hog" as using would bring back the phase 1 bug where "show
        me memory use" became a question about memory hogs."""
        self.assertNotEqual(self.checker.check("hog", "use")["scores_hold"], "ok")

    def test_a_word_that_wakes_a_non_question_is_blocked(self):
        self.assertIn("left alone", self.checker.check("day", "computer")["scores_hold"])

    def test_a_word_already_meaning_something_else_is_refused_first(self):
        self.assertIn("load", self.checker.check("busy", "slow")["usable"])

    def test_a_good_word_passes(self):
        checks = self.checker.check("random access memory", "memory",
                                    ["the ram butted the gate"])
        self.assertEqual(set(checks.values()), {"ok"}, checks)


class TestApproval(unittest.TestCase):
    """Only a named person, only what was proposed, only what passes."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.store = Path(self._dir.name) / "approved.json"
        self.proposals = {"candidates": [
            {"word": "random access memory", "concept": "memory", "relation": "synonym",
             "said": "random-access memory", "anchor": "ram", "sense": "WN_n_4",
             "definition": "the most common computer memory", "why": "test",
             "examples": []},
            {"word": "microcomputer", "concept": "computer", "relation": "synonym",
             "said": "microcomputer", "anchor": "pc", "sense": "WN_n_pc",
             "definition": "a small digital computer", "why": "test", "examples": []},
            {"word": "hog", "concept": "use", "relation": "synonym", "said": "hog",
             "anchor": "use", "sense": "WN_v_x", "definition": "", "why": "test",
             "examples": []},
            {"word": "c p u", "concept": "cpu", "relation": "synonym", "said": "c.p.u.",
             "anchor": "cpu", "sense": "WN_n_12", "definition": "", "why": "test",
             "examples": []},
        ]}

    def tearDown(self):
        self._dir.cleanup()

    def test_nobody_approves_nothing(self):
        ok, why = bridge.approve("random access memory", "memory", "", self.proposals, self.store)
        self.assertFalse(ok)
        self.assertIn("--by", why)
        self.assertFalse(self.store.exists())

    def test_only_what_was_proposed(self):
        ok, why = bridge.approve("gigabytes", "memory", "Ada", self.proposals, self.store)
        self.assertFalse(ok)
        self.assertIn("not proposed", why)

    def test_approved_words_are_used(self):
        before = CapabilityCorpus(approved=load_approved(self.store))
        self.assertIsNone(before.resolve("my microcomputer is so slow"))
        ok, why = bridge.approve("microcomputer", "computer", "Ada", self.proposals,
                                 self.store)
        self.assertTrue(ok, why)
        entry = json.loads(self.store.read_text())["approved"][0]
        self.assertEqual((entry["approved_by"], entry["sense"]), ("Ada", "WN_n_pc"))
        after = CapabilityCorpus(approved=load_approved(self.store))
        self.assertEqual(after.resolve("my microcomputer is so slow").analysis,
                         "performance_report")

    def test_a_failing_word_needs_an_override_and_records_it(self):
        ok, why = bridge.approve("hog", "use", "Ada", self.proposals, self.store)
        self.assertFalse(ok)
        self.assertIn("scores_hold", why)
        ok, why = bridge.approve("hog", "use", "Ada", self.proposals, self.store,
                                 override="I accept the regression")
        self.assertTrue(ok, why)
        self.assertEqual(json.loads(self.store.read_text())["approved"][0]["override"],
                         "I accept the regression")

    def test_an_unusable_word_cannot_be_overridden(self):
        ok, _ = bridge.approve("c p u", "cpu", "Ada", self.proposals, self.store,
                               override="please")
        self.assertFalse(ok)

    def test_a_rejection_needs_a_reason_and_is_final(self):
        self.assertFalse(bridge.reject("hog", "use", "Ada", "", self.store)[0])
        self.assertTrue(bridge.reject("hog", "use", "Ada", "a hog is a program", self.store)[0])
        self.assertIn(("hog", "use"), bridge.decided(bridge.read_store(self.store)))
        ok, why = bridge.approve("hog", "use", "Ada", self.proposals, self.store,
                                 override="changed my mind")
        self.assertFalse(ok)
        self.assertIn("already been decided", why)


class TestTheApprovedFile(unittest.TestCase):

    def _load(self, entries):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "approved.json"
            path.write_text(json.dumps({"approved": entries}))
            return load_approved(path)

    def test_a_hand_edit_is_held_to_the_same_rules(self):
        loaded = self._load([
            {"word": "gigs", "concept": "memory"},                               # nobody
            {"word": "gigs", "concept": "memory", "approved_by": " "},           # blank
            {"word": "ram", "concept": "disk", "approved_by": "Ada"},            # taken
            {"word": "thingy", "concept": "widget", "approved_by": "Ada"},       # no concept
            {"word": "the", "concept": "memory", "approved_by": "Ada"},          # function word
            {"word": "random access memory", "concept": "memory", "approved_by": "Ada"},
        ])
        self.assertEqual(loaded.phrases, {("random", "access", "memory"): "memory"})
        self.assertEqual(loaded.words, {})
        self.assertEqual(len(loaded.problems), 5)

    def test_the_shipped_file_is_clean(self):
        loaded = load_approved()
        self.assertEqual(loaded.problems, [])

    def test_an_unreadable_file_is_a_problem_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "approved.json"
            path.write_text("{not json")
            loaded = load_approved(path)
        self.assertEqual((loaded.words, loaded.phrases), ({}, {}))
        self.assertTrue(loaded.problems)

    def test_an_approved_phrase_meets_its_inflections(self):
        lexicon = Lexicon(["disk usage"], approved=Approved(phrases={("disk", "drive"): "disk"}))
        self.assertIn("disk", lexicon.read("both disk drives are full").concepts)


class TestEarthOsDoesNotNeedTheCorpus(unittest.TestCase):
    """The bridge grows the tables; nothing that runs at start-up uses it."""

    def test_the_runtime_never_imports_the_bridge_or_cdos(self):
        for name in ("lexical.py", "capability_corpus.py", "semantics.py", "core.py"):
            tree = ast.parse((ROOT / "earth_os" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertNotIn("dictionary_bridge", module, name)
                    self.assertFalse(module.startswith("computer"), name)
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "dictionary_bridge", name)


if __name__ == "__main__":
    unittest.main()
