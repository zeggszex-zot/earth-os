"""Tests for the learned semantic layer.

Most of these are about the properties that keep learning from becoming a
hole in CDOS's boundary — effectful commands never auto-running, a new alias
starting below the threshold, a contradiction weakening rather than silently
overwriting.  A learning layer that gets those wrong is worse than none.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earth_os.commands import (                       # noqa: E402
    Dispatcher, Vocabulary, apply_alias,
)
from earth_os.semantics import (                      # noqa: E402
    Alias, Resolution, SemanticStore, content_key, key_terms, seed,
)


class TestNormalisation(unittest.TestCase):

    def test_filler_is_stripped(self):
        self.assertEqual(key_terms("restart the computer"), ["restart"])
        self.assertEqual(key_terms("restart this machine now"), ["restart"])

    def test_content_key_is_order_independent(self):
        self.assertEqual(content_key("how much memory"),
                         content_key("memory how much"))

    def test_punctuation_does_not_survive(self):
        self.assertEqual(key_terms("Reboot, please!"), ["reboot"])


class TestStore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "semantics.json"
        self.store = SemanticStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    # -- learning ------------------------------------------------------
    def test_learns_and_resolves(self):
        self.store.learn_alias("restart", "reboot")
        resolution = self.store.resolve("restart the computer")
        self.assertTrue(resolution.found)
        self.assertEqual(resolution.canonical, "reboot")
        self.assertEqual(resolution.via, "alias")

    def test_new_alias_starts_below_threshold(self):
        """A freshly learned reading must ask before it acts."""
        self.store.learn_alias("restart", "reboot", seed=0.55)
        resolution = self.store.resolve("restart the computer")
        self.assertFalse(resolution.should_execute(0.75))

    def test_confidence_is_earned_by_confirmation(self):
        self.store.learn_alias("restart", "reboot", seed=0.55)
        for _ in range(4):
            self.store.confirm("restart")
        resolution = self.store.resolve("restart the computer")
        self.assertTrue(resolution.should_execute(0.75))

    def test_confidence_never_reaches_certainty(self):
        self.store.learn_alias("restart", "reboot", seed=0.55)
        for _ in range(200):
            self.store.confirm("restart")
        self.assertLess(self.store.aliases["restart"].confidence, 1.0)

    def test_rejection_weakens_then_forgets(self):
        self.store.learn_alias("restart", "reboot", seed=0.55)
        self.store.reject("restart")
        self.assertLess(self.store.aliases["restart"].confidence, 0.55)
        for _ in range(6):
            self.store.reject("restart")
        self.assertNotIn("restart", self.store.aliases)

    def test_filler_words_are_never_aliased(self):
        self.assertIsNone(self.store.learn_alias("the", "reboot"))

    def test_self_alias_rejected(self):
        self.assertIsNone(self.store.learn_alias("reboot", "reboot"))

    def test_contradiction_weakens_rather_than_overwrites(self):
        """A second meaning must not silently replace one already earned."""
        self.store.learn_alias("restart", "reboot", seed=0.55)
        for _ in range(5):
            self.store.confirm("restart")
        strong = self.store.aliases["restart"].confidence

        self.store.learn_alias("restart", "shutdown", seed=0.55)
        alias = self.store.aliases["restart"]
        self.assertEqual(alias.canonical, "reboot")     # old reading survives
        self.assertLess(alias.confidence, strong)       # but is shaken

    # -- the effectful guarantee ---------------------------------------
    def test_effectful_never_auto_executes(self):
        """The single most important property in the module."""
        self.store.learn_alias("restart", "reboot", effectful=True, seed=0.55)
        for _ in range(200):
            self.store.confirm("restart")
        resolution = self.store.resolve("restart the computer")
        self.assertGreater(resolution.confidence, 0.95)
        self.assertFalse(resolution.should_execute(0.75))
        self.assertFalse(resolution.should_execute(0.0))

    # -- rules ---------------------------------------------------------
    def test_rule_matches_all_terms(self):
        self.store.learn_rule("start-over", ["start", "over"], "reboot")
        self.assertTrue(self.store.resolve("start the computer over").found)
        self.assertFalse(self.store.resolve("start the computer").found)

    def test_rule_absent_terms_block_match(self):
        self.store.learn_rule("hot", ["running", "hot"], "thermal",
                              when_absent=["not"])
        self.assertTrue(self.store.resolve("is it running hot").found)
        self.assertFalse(self.store.resolve("is it not running hot").found)

    # -- fuzzy ---------------------------------------------------------
    def test_fuzzy_match_is_discounted(self):
        self.store.learn_alias("restart", "reboot", seed=0.9)
        exact = self.store.resolve("restart the computer")
        near = self.store.resolve("restert the computer")
        self.assertEqual(near.via, "fuzzy")
        self.assertLess(near.confidence, exact.confidence)

    # -- persistence ---------------------------------------------------
    def test_round_trip(self):
        self.store.learn_alias("restart", "reboot", effectful=True)
        self.store.learn_rule("start-over", ["start", "over"], "reboot")
        self.store.confirm("restart")
        self.assertTrue(self.store.save())

        reloaded = SemanticStore.load(self.path)
        self.assertIn("restart", reloaded.aliases)
        self.assertTrue(reloaded.aliases["restart"].effectful)
        self.assertIn("start-over", reloaded.rules)

    def test_corrupt_file_starts_clean(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ this is not json", encoding="utf-8")
        store = SemanticStore.load(self.path)
        self.assertEqual(store.aliases, {})

    def test_seed_is_idempotent(self):
        seed(self.store)
        first = len(self.store.aliases)
        seed(self.store)
        self.assertEqual(len(self.store.aliases), first)


class TestRewriting(unittest.TestCase):

    def test_apply_alias_substitutes(self):
        self.assertEqual(apply_alias("restart the computer", "restart", "reboot"),
                         "reboot the computer")

    def test_apply_alias_leaves_others_alone(self):
        self.assertEqual(apply_alias("show me the memory", "restart", "reboot"),
                         "show me the memory")

    def test_multi_word_term(self):
        self.assertEqual(apply_alias("power down the computer",
                                     "power down", "shutdown"),
                         "shutdown the computer")


class TestDispatcherIntegration(unittest.TestCase):
    """The tier ordering: fixed table first, learned layer only after."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SemanticStore(Path(self.tmp.name) / "s.json")
        vocab = Vocabulary()
        vocab.phrases = {
            "reboot the computer": "reboot",
            "show network": "network",
        }
        self.dispatcher = Dispatcher(vocab, semantics=self.store)
        self.dispatcher.register("reboot", lambda: None)
        self.dispatcher.register("network", lambda: None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixed_table_wins_and_reports_no_resolution(self):
        action, resolution = self.dispatcher.resolve_learned("reboot the computer")
        self.assertEqual(action, "reboot")
        self.assertIsNone(resolution)

    def test_fixed_table_already_absorbs_near_misses(self):
        """difflib in ``Vocabulary.match`` handles typos and close wordings.

        Worth pinning down, because it marks the boundary of what the learned
        layer is actually for: it earns its keep on words that are
        *semantically* distant, not orthographically close ones.
        """
        action, resolution = self.dispatcher.resolve_learned("restart the computer")
        self.assertEqual(action, "reboot")
        self.assertIsNone(resolution)           # the fixed table, not learning

    def test_learned_alias_reaches_the_fixed_table(self):
        """One alias should cover every phrasing the table already knows."""
        self.assertIsNone(self.dispatcher.resolve_learned("show wifi")[0])
        self.store.learn_alias("wifi", "network")
        action, resolution = self.dispatcher.resolve_learned("show wifi")
        self.assertEqual(action, "network")
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.via, "alias")

    def test_one_alias_covers_many_phrasings(self):
        """The point of rewrite-and-rematch rather than alias-to-action."""
        self.store.learn_alias("wifi", "network")
        for phrase in ("show wifi", "show the wifi", "wifi please"):
            action, _ = self.dispatcher.resolve_learned(phrase)
            self.assertEqual(action, "network", phrase)

    def test_alias_pointing_nowhere_yields_no_action(self):
        self.store.learn_alias("frobnicate", "nonexistent")
        action, resolution = self.dispatcher.resolve_learned("frobnicate it")
        self.assertIsNone(action)
        self.assertTrue(resolution.found)

    def test_unknown_utterance_falls_through(self):
        action, resolution = self.dispatcher.resolve_learned("make me a sandwich")
        self.assertIsNone(action)
        self.assertFalse(resolution.found)


if __name__ == "__main__":
    unittest.main()
