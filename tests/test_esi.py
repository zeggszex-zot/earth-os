"""Earth Semantic Intelligence, phase 1: reading a question by meaning.

``earth_os/lexical.py`` reads a sentence as concepts; the capability corpus
uses that reading when no phrase matches a question as written. The design
and the measurements are in ``docs/ESI_ARCHITECTURE.md``.

What these tests hold the layer to, in order of how much each one matters:

1. **Meaning reaches questions, never acts.** A reading by meaning is a
   looser match than a phrase, and a looser match must only ever look.
2. **A sentence that says not to do something never does it.** "Don't lock
   the screen" locked the screen before this layer existed.
3. **Meaning refines, it never overrules.** Every phrase in the phrasebook
   resolves exactly as it did with the layer off.
4. **It is measured.** The held-out set was committed before the tables were
   written; the figures below are what it scored, and they may only rise.
5. **It holds little.** The word list is not loaded until a typo needs it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

from earth_os import lexical                                       # noqa: E402
from earth_os.capability_corpus import (                           # noqa: E402
    ADJUSTMENTS, ANALYSES, ANALYSIS_PHRASES, MEANING_READINGS,
    PHRASEBOOK, READINGS, CapabilityCorpus,
)
from earth_os.config import Config                                 # noqa: E402
from earth_os.lexical import (                                     # noqa: E402
    CONCEPTS, MACHINE, MULTIWORD, PARTS, Lexicon, is_english,
)
from earth_os.platform.capabilities import CATALOGUE               # noqa: E402
from earth_os.semantics import SemanticStore, seed                 # noqa: E402

import esi_measure                                                 # noqa: E402


def label(intent):
    return None if intent is None else (intent.analysis or intent.capability)


class TestTheTables(unittest.TestCase):
    """The concept table is meaning written by hand; keep it one meaning wide."""

    def test_no_word_means_two_concepts(self):
        owner = {}
        for concept, words in CONCEPTS.items():
            for word in words:
                self.assertNotIn(word, owner,
                                 f"{word!r} is in both {owner.get(word)!r} and {concept!r}")
                owner[word] = concept

    def test_every_concept_is_one_of_its_own_words(self):
        for concept, words in CONCEPTS.items():
            self.assertIn(concept, words, concept)

    def test_multiword_and_parts_name_real_concepts(self):
        for words, concept in MULTIWORD.items():
            self.assertIn(concept, CONCEPTS, words)
        self.assertLessEqual(PARTS, set(CONCEPTS))
        self.assertLessEqual(PARTS, MACHINE)

    def test_the_tables_name_no_mechanism(self):
        """Meaning lives above the boundary; the boundary test also checks this
        module, but say it here, where someone growing the table will look."""
        from test_boundary import MECHANISMS
        text = json.dumps([CONCEPTS, [list(k) + [v] for k, v in MULTIWORD.items()]])
        for mechanism in MECHANISMS:
            self.assertNotIn(mechanism, text)


class TestReadingASentence(unittest.TestCase):

    def setUp(self):
        self.lexicon = Lexicon(["using the most memory", "process count",
                                "run", "take a screenshot"])

    def test_synonyms_collapse(self):
        self.assertEqual(self.lexicon.read("ram").concepts, {"memory", "computer"})
        self.assertIn("computer", self.lexicon.read("my laptop").concepts)
        self.assertIn("slow", self.lexicon.read("sluggish").concepts)

    def test_inflections_reduce_to_known_words(self):
        self.assertEqual(self.lexicon.base("processes"), "process")
        self.assertEqual(self.lexicon.base("running"), "run")
        self.assertEqual(self.lexicon.base("using"), "use")
        self.assertEqual(self.lexicon.base("hogging"), "use")

    def test_an_unknown_word_is_left_as_said(self):
        self.assertEqual(self.lexicon.base("dragons"), "dragons")

    def test_contractions_are_undone(self):
        reading = self.lexicon.read("you're using too much memory")
        self.assertEqual(reading.concepts, {"computer", "use", "much", "memory"})

    def test_negation_is_noticed(self):
        for text in ("don't lock it", "do not lock it", "never lock it",
                     "it isn't hot", "I can't see"):
            self.assertTrue(self.lexicon.read(text).negated, text)
        self.assertFalse(self.lexicon.read("lock it now").negated)

    def test_a_part_implies_the_whole_but_a_phrase_does_not(self):
        self.assertIn("computer", self.lexicon.read("the cpu is slow").concepts)
        self.assertNotIn("computer", self.lexicon.key("the cpu is slow"))

    def test_hogging_is_using_but_a_hog_is_not(self):
        """The noun and the verb collapsed once, and "show me memory use"
        became a question about memory hogs."""
        self.assertIn("use", self.lexicon.read("what is hogging my ram").concepts)
        self.assertNotIn("use", self.lexicon.read("biggest memory hogs").concepts)

    def test_show_me_is_not_about_the_user(self):
        """Phase 3A: "my" now says *what* is the person's ("user>disk")
        instead of a free-standing "user"; "show me" still says nothing."""
        self.assertFalse(any(c.startswith("user") for c in
                             self.lexicon.read("show me memory use").concepts))
        self.assertIn("user>disk", self.lexicon.read("how full is my disk").concepts)

    def test_the_span_is_what_was_said(self):
        reading = self.lexicon.read("Which app is eating the most RAM?")
        self.assertEqual(reading.span({"use", "most", "memory"}), "eating the most ram")


class TestPossessivesAttach(unittest.TestCase):
    """Phase 3A. "My" belongs to the noun after it, not to the sentence.

    The failure that started this: "My computer is struggling because
    several programs are consuming memory" matched "taking up *my* memory"
    because the "my" of "my computer" floated free and satisfied it.
    """

    @classmethod
    def setUpClass(cls):
        cls.corpus = CapabilityCorpus()
        cls.lexicon = cls.corpus._lexicon

    def _attached(self, text):
        return set(self.lexicon.read(text).attachments)

    def test_the_sentence_that_found_it(self):
        text = "My computer is struggling because several programs are consuming memory."
        self.assertEqual(self._attached(text), {"user>computer"})
        intent = self.corpus.resolve(text)
        self.assertNotIn("my memory", (intent.like if intent else ""))
        self.assertNotEqual(label(intent), "top_memory")

    def test_the_same_word_attaches_where_it_is(self):
        self.assertEqual(self._attached("programs are consuming my memory"), {"user>memory"})
        self.assertEqual(label(self.corpus.resolve("programs are consuming my memory")),
                         "top_memory")

    def test_mine_and_yours_stay_apart(self):
        self.assertEqual(self._attached("what is my name"), {"user>call"})
        self.assertEqual(self._attached("what is your name"), {"computer>call"})
        self.assertEqual(label(self.corpus.resolve("what is my name")), "user.identity")
        self.assertEqual(label(self.corpus.resolve("what is your name")), "system.hostname")

    def test_a_modifier_is_skipped_to_find_the_noun(self):
        self.assertEqual(self._attached("my free memory is low"), {"user>memory"})
        self.assertEqual(self._attached("my hard drive is full"), {"user>disk"})

    def test_each_possessive_finds_its_own_noun(self):
        self.assertEqual(self._attached("my computer and my phone are both slow"),
                         {"user>computer", "user>phone"})

    def test_with_nothing_after_it_a_possessive_stays_as_it_was(self):
        self.assertEqual(self._attached("that is my"), set())
        self.assertIn("user", self.lexicon.read("that is my").concepts)

    def test_no_other_word_changes_meaning(self):
        """Only possessives move: every other word keeps the concept phase 1
        gave it ("avoid changing existing synonym behaviour")."""
        from unittest import mock
        texts = [l["text"] for name in ("esi_dev.jsonl", "esi_heldout.jsonl",
                                        "esi_context.jsonl")
                 for l in map(json.loads, (ROOT / "tests" / name).read_text().splitlines())]
        with mock.patch.object(Lexicon, "_attach", staticmethod(lambda reading: None)):
            before = {t: [(w.surface, w.concept) for w in self.lexicon.read(t).words]
                      for t in texts}
        for text in texts:
            after = [(w.surface, w.concept) for w in self.lexicon.read(text).words]
            self.assertEqual(after, [p for p in before[text] if p[0] not in lexical.POSSESSIVE],
                             text)


class TestMeaningOnlyEverLooks(unittest.TestCase):
    """Rule 1. The looser match must not be able to do anything."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = CapabilityCorpus()

    def test_every_meaning_is_a_question(self):
        for key, target, phrase in self.corpus._meanings:
            if target in ANALYSES:
                for read in ANALYSES[target].reads:
                    self.assertFalse(CATALOGUE[read].effectful, (target, read))
                continue
            self.assertIn(target, MEANING_READINGS, phrase)
            spec = CATALOGUE[target]
            self.assertFalse(spec.effectful, target)
            self.assertFalse(spec.params, target)

    def test_the_screenshot_is_not_reached_by_meaning(self):
        self.assertIn("display.screenshot", READINGS)
        self.assertNotIn("display.screenshot", MEANING_READINGS)

    def test_every_meaning_is_about_the_machine(self):
        """"Sluggish" alone is a phrase; by meaning it is "slow", and a slow
        week at work is not a diagnosis."""
        for key, target, phrase in self.corpus._meanings:
            self.assertTrue(key & MACHINE, phrase)
        self.assertIsNone(self.corpus.resolve("work has been slow this week"))

    def test_collisions_are_left_out_and_do_not_grow(self):
        """Two questions that read the same are ambiguous by meaning. The exact
        phrases still work. A new collision fails here so a person decides,
        rather than whichever phrase sorted first."""
        self.assertEqual(sorted(self.corpus.ambiguous.values()),
                         [("system.distro", "system.hostname")])
        keys = [key for key, _t, _p in self.corpus._meanings]
        self.assertEqual(len(keys), len(set(keys)))

    def test_a_lexical_answer_says_so(self):
        intent = self.corpus.resolve("which app is hogging my ram")
        self.assertEqual(intent.via, "lexical")
        self.assertEqual(intent.analysis, "top_memory")
        self.assertIn("read by meaning", intent.explanation)
        self.assertTrue(intent.like)


class TestNotMeansNot(unittest.TestCase):
    """Rule 2."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = CapabilityCorpus()

    def test_a_negated_act_is_not_read_as_that_act(self):
        for text in ("don't lock the screen", "do not lock the screen",
                     "never shut down", "don't turn off wifi",
                     "please don't take a screenshot"):
            self.assertIsNone(self.corpus.resolve(text), text)

    def test_the_same_act_said_plainly_still_works(self):
        self.assertEqual(label(self.corpus.resolve("lock the screen")), "system.lock")
        self.assertEqual(label(self.corpus.resolve("turn off wifi")), "network.wifi_off")

    def test_a_negated_question_is_still_answered_as_written(self):
        self.assertEqual(label(self.corpus.resolve("why isn't the wifi status good")),
                         "network.wireless")

    def test_the_rule_holds_with_meaning_off(self):
        """Not a property of ESI: it holds for the phrasebook on its own."""
        corpus = CapabilityCorpus(lexical=False)
        self.assertIsNone(corpus.resolve("don't lock the screen"))


class TestMeaningRefinesNeverOverrules(unittest.TestCase):
    """Rule 3."""

    def test_every_phrase_resolves_as_it_did(self):
        before = CapabilityCorpus(lexical=False)
        after = CapabilityCorpus()
        phrases = [p for ps in PHRASEBOOK.values() for p in ps]
        phrases += [p for ps in ANALYSIS_PHRASES.values() for p in ps]
        phrases += list(ADJUSTMENTS)
        changed = [(p, label(before.resolve(p)), label(after.resolve(p)))
                   for p in phrases
                   if label(before.resolve(p)) != label(after.resolve(p))]
        self.assertEqual(changed, [])

    def test_a_refinement_wins(self):
        corpus = CapabilityCorpus()
        self.assertEqual(label(corpus.resolve("which app is eating the most memory")),
                         "top_memory")
        self.assertEqual(label(corpus.resolve("you're using too much memory")),
                         "memory_report")

    def test_a_contradiction_does_not(self):
        corpus = CapabilityCorpus()
        self.assertEqual(label(corpus.resolve("what's the cpu temperature")),
                         "hardware.temperature")
        self.assertEqual(label(corpus.resolve("how much memory is free")),
                         "hardware.memory")

    def test_an_act_left_to_the_vocabulary_is_not_reread(self):
        """"Forget the wifi network" needs a name, so the corpus leaves it to
        the shell. Meaning once reread it as "list the wireless networks"."""
        corpus = CapabilityCorpus()
        for text in ("forget the wifi network", "pair bluetooth device",
                     "forget the bluetooth device"):
            self.assertIsNone(corpus.resolve(text), text)

    def test_the_terminal_gets_the_same_analysis(self):
        corpus = CapabilityCorpus()
        self.assertEqual(label(corpus.resolve_analysis("what is hogging my ram")),
                         "top_memory")
        self.assertIsNone(corpus.resolve_analysis("how hot is the computer"))


class TestItIsMeasured(unittest.TestCase):
    """Rule 4. The figures recorded in docs/ESI_ARCHITECTURE.md.

    Lower bounds, not exact counts: an improvement passes, a regression fails.
    Never lower one to make a change pass — the change is what is wrong.
    """

    def _score(self, name, lexical=True):
        return esi_measure.summary(esi_measure.measure(ROOT / "tests" / name, lexical))

    def test_held_out(self):
        s = self._score("esi_heldout.jsonl")
        self.assertGreaterEqual(s["positives_right"], 50)
        self.assertLessEqual(s["positives_wrong"], 2)
        self.assertEqual(s["negatives_right"], s["negatives"])

    def test_held_out_before(self):
        """What the phrasebook scores alone, so the gain is a measured one."""
        s = self._score("esi_heldout.jsonl", lexical=False)
        self.assertEqual((s["positives_right"], s["positives_wrong"]), (27, 5))

    def test_context_three_outcomes(self):
        """Phase 3A/3B, on tests/esi_context.jsonl (developer-labelled).

        Before 3A: recognition 35/44, attachment 0/19, interpretation 19/44.
        Attachment is what 3A fixed. Recognition and interpretation are left
        where they were on purpose: their misses are missing words and
        missing phrases, which 3A was not for.
        """
        s = esi_measure.context_summary(esi_measure.measure_context(
            ROOT / "tests" / "esi_context.jsonl"))["all"]
        self.assertEqual((s["attachment"], s["attachment_lines"]), (19, 19))
        self.assertGreaterEqual(s["recognition"], 35)
        self.assertGreaterEqual(s["interpretation"], 19)
        # False positives are ceilings: answering wrongly, and answering a
        # sentence that was not a request, may only fall. (All three answered
        # non-requests are the phrasebook's bare words, not ESI's.)
        self.assertLessEqual(s["wrong"], 11)
        self.assertLessEqual(s["non_requests_answered"], 3)

    def test_attachment_is_scored_exactly(self):
        """An attachment nobody labelled fails as surely as a missing one.
        The first version only checked the expected ones were present, and
        "my computer and my phone" was labelled with one of its two."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "one.jsonl"
            path.write_text(json.dumps({
                "text": "my computer and my phone are both slow", "expect": "performance_report",
                "concepts": [], "attach": ["user>computer"], "not_attach": []}) + "\n")
            (_row, _rec, attached, _got), = esi_measure.measure_context(path)
        self.assertFalse(attached)

    def test_the_independent_set_is_well_formed_if_it_exists(self):
        """Phase 3C (docs/ESI_INDEPENDENT_SET.md). Shape only — no score is
        held for it, because a floor would invite tuning against it."""
        path = ROOT / "tests" / "esi_independent.jsonl"
        if not path.is_file():
            self.skipTest("no independent set collected yet")
        rows = esi_measure.read_rows(path)          # refuses unknown labels
        for row in rows:
            for key in ("text", "expect", "written_by", "labelled_by"):
                self.assertTrue(str(row.get(key, "")).strip(), (key, row))

    def test_development(self):
        s = self._score("esi_dev.jsonl")
        self.assertGreaterEqual(s["positives_right"], 34)
        self.assertEqual(s["positives_wrong"], 0)


class TestItHoldsLittle(unittest.TestCase):
    """Rule 5."""

    def test_the_word_list_is_not_loaded_to_build_the_corpus(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]);"
                "from earth_os.capability_corpus import CapabilityCorpus;"
                "from earth_os import lexical;"
                "c = CapabilityCorpus(); c.resolve('which app is hogging my ram');"
                "print(lexical._BLOB is None)")
        out = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "True", out.stderr)

    def test_the_word_list_ships(self):
        self.assertTrue(lexical.word_list_available())
        self.assertTrue((lexical.ASSET.parent / "WordNet-LICENSE.txt").is_file())
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn('"assets/lexicon/*"', pyproject)

    def test_it_can_be_switched_off(self):
        config = Config()
        self.assertTrue(config.get("semantics", "lexical", None))
        corpus = CapabilityCorpus(lexical=False)
        self.assertIsNone(corpus.resolve("which app is hogging my ram"))


class TestTyposAreNotWords(unittest.TestCase):
    """The learned layer's fuzzy match is for typos, not for real words."""

    def setUp(self):
        self.store = SemanticStore(Path(tempfile.mkdtemp()) / "semantics.json")
        seed(self.store)

    def test_a_real_word_is_not_fuzzed(self):
        """"getting" ≈ "settings" → parameters, from "you're getting tired"."""
        resolution = self.store.resolve("I think you're getting tired")
        self.assertNotEqual(resolution.via, "fuzzy", resolution.explanation)

    def test_a_typo_still_is(self):
        resolution = self.store.resolve("restrat")
        self.assertEqual((resolution.via, resolution.canonical), ("fuzzy", "reboot"))

    def test_english_words(self):
        for word in ("getting", "settings", "running", "hogging", "wifi"):
            self.assertTrue(is_english(word), word)
        for word in ("rebot", "tempreture", "proccesor", "qwzx"):
            self.assertFalse(is_english(word), word)

    def test_without_the_word_list_it_cannot_tell_and_says_so(self):
        missing = Path(tempfile.mkdtemp()) / "absent.gz"
        with unittest.mock.patch.object(lexical, "ASSET", missing):
            self.assertIsNone(is_english("getting"))
            # ...and the learned layer falls back to what it did before.
            self.assertEqual(self.store.resolve("restrat").via, "fuzzy")


class TestThroughTheShell(unittest.TestCase):
    """The same rules, end to end, on the robot adapter (no real hardware)."""

    def setUp(self):
        from earth_os.platform import reset_platform
        from earth_os.platform.robot_stub import MARKER
        self._data = tempfile.TemporaryDirectory()
        self._env = unittest.mock.patch.dict(
            os.environ, {MARKER: "1", "XDG_DATA_HOME": self._data.name})
        self._env.start()
        reset_platform()
        from earth_os.core import Shell
        self.shell = Shell(Config())
        self.shell.sound.enabled = False
        self.invoked = []
        real = self.shell.capabilities.invoke

        def recording(capability, /, **kwargs):
            self.invoked.append(capability)
            return real(capability, **kwargs)
        self.shell.capabilities.invoke = recording
        # A headless shell has no screens attached, so go() cannot change
        # ``current``; record what it was asked to show instead.
        self.shown = []
        self.shell.go = self.shown.append

    def tearDown(self):
        from earth_os.platform import reset_platform
        self._env.stop()
        reset_platform()
        self._data.cleanup()

    def test_dont_lock_the_screen_does_not_lock_the_screen(self):
        self.shell.handle_utterance("don't lock the screen")
        self.assertNotIn("system.lock", self.invoked)

    def test_not_means_not_in_the_vocabulary_too(self):
        """The fixed vocabulary matches by containment as well: "do not open
        the terminal" opened it, and "don't shut down" asked to power off."""
        self.shell.handle_utterance("do not open the terminal")
        self.assertEqual(self.shown, [])
        self.shell.pending_options.clear()
        self.shell.handle_utterance("never mind, don't shut down")
        self.assertEqual(list(self.shell.pending_options), [])
        self.assertEqual(self.invoked, [])

    def test_the_same_commands_said_plainly_still_work(self):
        self.shell.handle_utterance("open the terminal")
        self.assertEqual(self.shown, ["terminal"])
        self.shell.pending_options.clear()
        self.shell.handle_utterance("shut down")
        self.assertTrue(self.shell.pending_options, "an effectful act asks first")
        self.assertNotIn("system.shutdown", self.invoked)

    def test_a_question_by_meaning_is_answered_from_readings(self):
        self.shell.handle_utterance("which app is hogging my ram")
        self.assertIn("process.list", self.invoked)
        self.assertTrue(all(not CATALOGUE[c].effectful for c in self.invoked),
                        self.invoked)


if __name__ == "__main__":
    unittest.main()
