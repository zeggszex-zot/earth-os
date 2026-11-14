"""Earth Mind routing: the right source for the question, or an honest no.

docs/EARTH_MIND_ROUTING_AUDIT.md found five terminal failures and six causes.
P1–P3 are the language-routing half, and this is what holds them:

* **P1** — a question word binds to its noun: "what processor" asks about the
  processor, and "what processes are using the most cpu" does not;
* **P2** — an answer must be the kind of information asked for: a definition
  or comparison is word knowledge's, a measurement is not an identity, a
  ranking is not a total, and a disk question is not a memory answer;
* **P3** — a question about this machine that nothing reads is said to be
  one, with what *can* be read instead, never handed to a dictionary.

The five terminal failures are driven through the terminal itself, where
they were seen. The response-validation table from the audit's review is
checked as written, row by row.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from earth_os.capability_corpus import (                          # noqa: E402
    IDENTITY_READINGS, MEASUREMENT_READINGS, READINGS, CapabilityCorpus,
)
from earth_os.platform.capabilities import CATALOGUE              # noqa: E402

import mind_routing                                               # noqa: E402


class TestTheValidationTable(unittest.TestCase):
    """The review's table, automated: requested vs returned, accept or reject."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = CapabilityCorpus()

    def _verdict(self, text, returned):
        return "reject" if self.corpus._mismatch(text.lower(), returned) else "accept"

    def test_the_table(self):
        rows = [
            # requested information           sentence                                returned               verdict
            ("CPU utilisation",  "how busy is the processor",              "hardware.cpu_model",  "reject"),
            ("RAM definition",   "what is ram",                            "hardware.memory",     "reject"),
            ("disk capacity",    "how full is the disk",                   "hardware.memory",     "reject"),
            ("process ranking",  "what processes are using the most cpu",  "hardware.cpu_model",  "reject"),
            ("CPU model",        "which processor does this computer have", "hardware.cpu_model", "accept"),
            # and the rest of the audit's cases
            ("comparison",       "what is the difference between ram and storage", "hardware.memory", "reject"),
            ("another thing's",  "what is the temperature of the sun",     "hardware.temperature", "reject"),
            ("CPU identity",     "which cpu do i have",                    "hardware.cpu_load",   "reject"),
            ("memory ranking",   "what is using the most memory",          "top_memory",          "accept"),
            ("RAM measurement",  "how much ram is free",                   "hardware.memory",     "accept"),
            ("my machine's",     "what is the temperature of my cpu",      "hardware.temperature", "accept"),
        ]
        for requested, text, returned, verdict in rows:
            with self.subTest(requested=requested):
                self.assertEqual(self._verdict(text, returned), verdict, text)

    def test_every_reading_has_one_kind_at_most(self):
        """Identity and measurement are disjoint subsets of the readings; the
        rest are statuses. A new reading defaults to status: never checked
        as the wrong kind, never wrongly rejected."""
        self.assertFalse(IDENTITY_READINGS & MEASUREMENT_READINGS)
        self.assertLessEqual(IDENTITY_READINGS | MEASUREMENT_READINGS, READINGS)
        for name in IDENTITY_READINGS | MEASUREMENT_READINGS:
            self.assertFalse(CATALOGUE[name].effectful, name)

    def test_the_scorers_table_agrees_with_the_codes(self):
        """tools/mind_routing.py keeps its own copy, so the check does not
        share a table with what it checks. If they part, one is wrong and a
        person decides which."""
        self.assertEqual(mind_routing.IDENTITY, set(IDENTITY_READINGS))
        self.assertEqual(mind_routing.MEASUREMENT, set(MEASUREMENT_READINGS))


class TestQuestionWordsBind(unittest.TestCase):
    """P1."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = CapabilityCorpus()
        cls.lexicon = cls.corpus._lexicon

    def _asks_about(self, text):
        return {a for a in self.lexicon.read(text).attachments if a.startswith("what>")}

    def test_what_binds_to_its_noun(self):
        self.assertEqual(self._asks_about("what processor is this"), {"what>cpu"})
        self.assertEqual(self._asks_about("what processes are using the most cpu"),
                         {"what>process"})

    def test_which_is_what(self):
        self.assertEqual(self._asks_about("which processor does this computer have"),
                         {"what>cpu"})

    def test_kind_of_and_model_are_passed_over(self):
        self.assertEqual(self._asks_about("what kind of processor do you have"), {"what>cpu"})
        self.assertEqual(self._asks_about("what model is the cpu"), {"what>cpu"})

    def test_a_compound_is_asked_about_by_its_last_noun(self):
        self.assertEqual(self._asks_about("what is the screen resolution"), {"what>resolution"})
        self.assertEqual(self._asks_about("which wifi network am i on"), {"what>network"})

    def test_the_regression_it_fixes(self):
        """ESI phase 1 read "what processor" as {what, cpu}, which this contains."""
        intent, _ = self.corpus.resolve_why("What processes are using the most CPU?")
        self.assertIsNone(intent)


class TestWorldOrMachine(unittest.TestCase):
    """P2: what something *is* goes to word knowledge, unless it is mine."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = CapabilityCorpus()

    def _world(self, text):
        """Not answered from the machine, and not claimed as a machine
        question either — so the router gives it to word knowledge. (Most
        never matched anything; the ones that did are declined as the
        world's.)"""
        intent, why = self.corpus.resolve_why(text)
        return intent is None and self.corpus.unreadable(text, why) is None

    def test_definitions_comparisons_and_other_things_are_the_worlds(self):
        for text in ("what is a cpu", "what is an ssd", "what is ram", "what does ram stand for",
                     "explain what memory is", "how does a processor work",
                     "What is the difference between RAM and storage?",
                     "is an ssd faster than a hard drive",
                     "What is the temperature of the sun?"):
            with self.subTest(text=text):
                self.assertTrue(self._world(text), text)

    def test_pointing_at_this_machine_makes_it_a_reading(self):
        for text, want in (("what is my ram", "hardware.memory"),
                           ("what is the temperature of my cpu", "hardware.temperature"),
                           ("Which processor does this computer have?", "hardware.cpu_model"),
                           ("what is the temperature", "hardware.temperature"),
                           ("what is running", "process.list")):
            with self.subTest(text=text):
                intent = self.corpus.resolve(text)
                self.assertIsNotNone(intent, text)
                self.assertEqual(intent.analysis or intent.capability, want)

    def test_acts_are_not_checked_here(self):
        """Their gate is elsewhere and stays as it was."""
        self.assertEqual(self.corpus.resolve("lock the screen").capability, "system.lock")
        self.assertEqual(self.corpus.resolve("shut down").capability, "system.shutdown")
        self.assertIsNone(self.corpus.resolve("don't lock the screen"))


class TestAnHonestNo(unittest.TestCase):
    """P3."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = CapabilityCorpus()

    def _no(self, text):
        intent, why = self.corpus.resolve_why(text)
        return None if intent is not None else self.corpus.unreadable(text, why)

    def test_a_machine_question_nothing_reads_says_so(self):
        found = self._no("Which applications are using the most resources?")
        self.assertIsNotNone(found)
        self.assertIn("none of this computer's readings answers it", found.text)
        self.assertIn("which programs are holding the most memory", found.text)

    def test_it_offers_the_nearest_true_answer_first(self):
        found = self._no("what is using my cpu")
        self.assertTrue(found.text.split("I can tell you ", 1)[1].startswith(
            "how busy the processor is"), found.text)

    def test_it_never_takes_the_worlds_questions_talk_or_negations(self):
        for text in ("what is a cpu", "What is the temperature of the sun?",
                     "I love my computer", "tell me a story about a computer",
                     "don't tell me how busy the cpu is", "how hot is the sun",
                     # "you" points at the machine but names nothing to read; the
                     # first version claimed this, and test_acceptance caught it.
                     "could you tell me a little about what you are able to do, please?"):
            with self.subTest(text=text):
                self.assertIsNone(self._no(text), text)


class TestThroughEarthMind(unittest.TestCase):
    """The labelled set, routed by a real Earth Mind with no model."""

    @classmethod
    def setUpClass(cls):
        cls.results = mind_routing.measure(ROOT / "tests" / "mind_routing.jsonl")
        cls.summary = mind_routing.summary(cls.results)

    def test_no_answer_is_the_wrong_kind_of_information(self):
        wrong = [(r[0]["text"], r[3]) for r in self.results if r[3]]
        self.assertEqual(wrong, [])

    def test_routing_floor(self):
        """Before P1–P3: 28/45 routed, 9 answers of the wrong kind."""
        self.assertGreaterEqual(self.summary["all"]["route"], 42)
        self.assertGreaterEqual(self.summary["all"]["answer"], 42)

    def test_the_five_terminal_failures_route_correctly(self):
        for row, route, got, _why in self.results:
            if row.get("terminal"):
                with self.subTest(case=row["terminal"]):
                    self.assertEqual((route, got), (row["route"], row["expect"]), row["text"])

    @unittest.expectedFailure
    def test_follow_ups(self):
        """The deferred step (audit §4.6). Expected to fail until it is built;
        when it passes, this decorator must come off."""
        self.assertEqual(self.summary["follow_ups"]["answer"], self.summary["follow_ups"]["lines"])


class TestThroughTheTerminal(unittest.TestCase):
    """Where the failures were seen: typed into the terminal, no model."""

    @classmethod
    def setUpClass(cls):
        from test_mind_terminal import make_terminal, guard
        cls.shell, cls.term = make_terminal()
        guard(cls.shell)
        cls.read = []
        real = cls.shell.capabilities.invoke

        def recording(capability, /, **kwargs):
            cls.read.append(capability)
            return real(capability, **kwargs)
        cls.shell.capabilities.invoke = recording

    def _ask(self, line):
        from test_mind_terminal import ask
        self.read.clear()
        return ask(self.term, line)

    def test_a_ranking_nobody_can_read_is_declined_honestly(self):
        for line in ("Which applications are using the most resources?",
                     "What processes are using the most CPU?"):
            with self.subTest(line=line):
                out = self._ask(line)
                self.assertIn("no reading answers this", out)
                self.assertNotIn("What processor this is", out)
                self.assertNotIn("can mean", out)                 # no dictionary senses
                self.assertEqual(self.read, [])                   # nothing read in its place

    def test_b_this_computers_processor_is_read(self):
        out = self._ask("Which processor does this computer have?")
        self.assertIn("answered from live system readings", out)
        self.assertEqual(self.read, ["hardware.cpu_model"])

    def test_d_a_comparison_goes_to_word_knowledge(self):
        out = self._ask("What is the difference between RAM and storage?")
        self.assertIn("earth mind → cdos", out)
        self.assertNotIn("in use", out)
        self.assertEqual(self.read, [])

    def test_e_the_core_count_is_read(self):
        """P6 will change what it is called; the route is right already."""
        self._ask("How many cores does this processor have?")
        self.assertEqual(self.read, ["hardware.cpu_count"])


if __name__ == "__main__":
    unittest.main()
