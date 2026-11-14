"""Tests for the recently-used-programs store.

The ranking tests matter most: frecency is the whole reason this is not just
a list sorted by timestamp, and getting the decay wrong turns it back into
one without anything visibly breaking.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earth_os.recent import (                       # noqa: E402
    HALF_LIFE, LinuxRecentProvider, MacRecentProvider, RecentEntry,
    RecentProvider, RecentStore, WindowsRecentProvider, active_provider,
)


class TestScoring(unittest.TestCase):

    def test_frequency_raises_score(self):
        now = time.time()
        rare = RecentEntry("a", "a", count=1, last_used=now)
        often = RecentEntry("b", "b", count=25, last_used=now)
        self.assertGreater(often.score(now), rare.score(now))

    def test_recency_decays(self):
        now = time.time()
        fresh = RecentEntry("a", "a", count=5, last_used=now)
        stale = RecentEntry("b", "b", count=5, last_used=now - 30 * 86400)
        self.assertGreater(fresh.score(now), stale.score(now))

    def test_frequency_can_beat_recency(self):
        """The point of frecency: a heavily used program is not buried by a
        one-off opened five minutes ago."""
        now = time.time()
        workhorse = RecentEntry("a", "a", count=50, last_used=now - 2 * 86400)
        oneoff = RecentEntry("b", "b", count=1, last_used=now - 300)
        self.assertGreater(workhorse.score(now), oneoff.score(now))

    def test_half_life_halves_the_recency_term(self):
        now = time.time()
        fresh = RecentEntry("a", "a", count=1, last_used=now)
        aged = RecentEntry("a", "a", count=1, last_used=now - HALF_LIFE)
        # 0.25 floor + 0.75 * decay, so at one half-life: 0.625 of fresh's 1.0
        self.assertAlmostEqual(aged.score(now) / fresh.score(now), 0.625, places=2)

    def test_nothing_reaches_zero(self):
        ancient = RecentEntry("a", "a", count=1, last_used=0.0)
        self.assertGreater(ancient.score(), 0.0)

    def test_age_labels(self):
        now = time.time()
        self.assertEqual(RecentEntry("a", "a", last_used=now).age_label(), "just now")
        self.assertIn("m ago", RecentEntry("a", "a", last_used=now - 600).age_label())
        self.assertIn("h ago", RecentEntry("a", "a", last_used=now - 7200).age_label())
        self.assertEqual(
            RecentEntry("a", "a", last_used=now - 86400 * 1.2).age_label(),
            "yesterday")


class TestStore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "recent.json"
        self.store = RecentStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_and_increment(self):
        self.store.record("Firefox", "firefox")
        self.store.record("Firefox", "firefox")
        self.assertEqual(self.store.entries["firefox"].count, 2)

    def test_empty_command_ignored(self):
        self.assertIsNone(self.store.record("Nothing", ""))
        self.assertEqual(self.store.entries, {})

    def test_name_defaults_to_command(self):
        entry = self.store.record("", "htop")
        self.assertEqual(entry.name, "htop")

    def test_round_trip(self):
        self.store.record("Firefox", "firefox")
        self.store.toggle_pin("firefox")
        self.assertTrue(self.store.save())
        reloaded = RecentStore.load(self.path)
        self.assertIn("firefox", reloaded.entries)
        self.assertTrue(reloaded.entries["firefox"].pinned)

    def test_corrupt_file_starts_clean(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("not json at all", encoding="utf-8")
        self.assertEqual(RecentStore.load(self.path).entries, {})

    def test_pinned_float_above_ranking(self):
        self.store.record("Rare", "rare")
        self.store.entries["rare"].last_used = time.time() - 60 * 86400
        for _ in range(30):
            self.store.record("Common", "common")
        # Isolate the ranking itself: the just-launched entry is floated to
        # the top by a separate rule, which TestLastLaunched covers.
        self.store.last_command = ""
        self.assertEqual(self.store.top(1)[0].name, "Common")
        self.store.toggle_pin("rare")
        self.assertEqual(self.store.top(1)[0].name, "Rare")

    def test_clear_keeps_pinned(self):
        self.store.record("Keep", "keep")
        self.store.record("Drop", "drop")
        self.store.toggle_pin("keep")
        self.store.clear()
        self.assertEqual(list(self.store.entries), ["keep"])

    def test_forget(self):
        self.store.record("Gone", "gone")
        self.assertTrue(self.store.forget("gone"))
        self.assertFalse(self.store.forget("gone"))

    def test_prune_respects_limit(self):
        store = RecentStore(self.path, limit=5)
        for i in range(30):
            store.record(f"p{i}", f"cmd{i}")
        self.assertLessEqual(len(store.entries), 5)

    def test_pinned_survives_pruning(self):
        """A pin is a promise, so pruning must not quietly break it."""
        store = RecentStore(self.path, limit=5)
        store.record("Keeper", "keeper")
        self.assertTrue(store.toggle_pin("keeper"))
        for i in range(30):
            store.record(f"p{i}", f"cmd{i}")
        self.assertLessEqual(len(store.entries), 5)
        self.assertIn("keeper", store.entries)

    def test_cannot_pin_what_was_already_pruned(self):
        """Pinning something the store has dropped reports failure rather
        than silently creating a half-formed entry."""
        store = RecentStore(self.path, limit=3)
        for i in range(10):
            store.record(f"p{i}", f"cmd{i}")
        self.assertFalse(store.toggle_pin("cmd0"))

    def test_top_is_bounded(self):
        for i in range(30):
            self.store.record(f"p{i}", f"cmd{i}")
        self.assertEqual(len(self.store.top(7)), 7)


class TestLastLaunched(unittest.TestCase):
    """Coming back to the screen must show what you just started, first."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "recent.json"
        self.store = RecentStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_last_launch_floats_to_top(self):
        for _ in range(40):
            self.store.record("Habit", "habit")     # overwhelming frecency
        self.store.record("OneOff", "oneoff")
        self.assertEqual(self.store.top(5)[0].name, "OneOff")

    def test_last_launch_outranks_a_pin(self):
        self.store.record("Pinned", "pinned")
        self.store.toggle_pin("pinned")
        self.store.record("Just Now", "justnow")
        self.assertEqual(self.store.top(5)[0].name, "Just Now")

    def test_last_launch_survives_restart(self):
        self.store.record("Habit", "habit")
        self.store.record("OneOff", "oneoff")
        self.store.save()
        self.assertEqual(RecentStore.load(self.path).top(5)[0].name, "OneOff")

    def test_last_launch_survives_pruning(self):
        store = RecentStore(self.path, limit=3)
        store.record("Keeper", "keeper")
        for i in range(20):
            store.record(f"p{i}", f"cmd{i}")
        store.record("Keeper", "keeper")
        self.assertIn("keeper", store.entries)
        self.assertEqual(store.top(5)[0].name, "Keeper")

    def test_forgetting_the_last_launch_clears_the_pointer(self):
        self.store.record("Gone", "gone")
        self.store.forget("gone")
        self.assertEqual(self.store.last_command, "")
        self.assertEqual(self.store.top(5), [])

    def test_clear_drops_a_dangling_pointer(self):
        self.store.record("Keep", "keep")
        self.store.toggle_pin("keep")
        self.store.record("Temp", "temp")
        self.store.clear()
        self.assertEqual(self.store.last_command, "")
        self.assertEqual([e.name for e in self.store.top(5)], ["Keep"])

    def test_relaunching_the_same_thing_is_stable(self):
        self.store.record("A", "a")
        self.store.record("B", "b")
        self.store.record("B", "b")
        top = self.store.top(5)
        self.assertEqual(top[0].name, "B")
        self.assertEqual(len(top), 2)       # no duplicate row


class TestProviders(unittest.TestCase):
    """Portability: the store must work on a host with no provider at all."""

    def test_base_provider_is_inert(self):
        provider = RecentProvider()
        self.assertFalse(provider.available)
        self.assertEqual(provider.entries(), [])

    def test_active_provider_always_returns_something(self):
        self.assertIsInstance(active_provider(), RecentProvider)

    def test_providers_declare_platforms(self):
        self.assertIn("Linux", LinuxRecentProvider.platforms)
        self.assertIn("Windows", WindowsRecentProvider.platforms)
        self.assertIn("Darwin", MacRecentProvider.platforms)

    def test_store_works_with_no_provider(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            store = RecentStore(Path(tmp.name) / "r.json")
            store.provider = RecentProvider()
            store.record("Thing", "thing")
            self.assertEqual(len(store.top(5)), 1)
        finally:
            tmp.cleanup()

    def test_broken_provider_does_not_empty_the_screen(self):
        """A provider that throws must not take the list down with it."""
        class Exploding(RecentProvider):
            @property
            def available(self):
                return True

            def entries(self, limit=20):
                raise RuntimeError("boom")

        tmp = tempfile.TemporaryDirectory()
        try:
            store = RecentStore(Path(tmp.name) / "r.json")
            store.provider = Exploding()
            store.record("Thing", "thing")
            self.assertEqual(len(store.top(5)), 1)
        finally:
            tmp.cleanup()

    def test_os_entries_never_displace_own_history(self):
        class Fake(RecentProvider):
            @property
            def available(self):
                return True

            def entries(self, limit=20):
                return [RecentEntry("Impostor", "mine", count=999,
                                    source="os")]

        tmp = tempfile.TemporaryDirectory()
        try:
            store = RecentStore(Path(tmp.name) / "r.json")
            store.provider = Fake()
            store.record("Mine", "mine")
            top = store.top(5)
            self.assertEqual(len(top), 1)
            self.assertEqual(top[0].name, "Mine")   # ours wins the command slot
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
