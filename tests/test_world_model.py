"""The World Model: self-knowledge derived, user knowledge by consent.

Each test here guards one of the five rules in ``earth_os/world_model.py``.
Read the module docstring before changing an assertion.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os.platform.capabilities import CATALOGUE  # noqa: E402
from earth_os.world_model import (  # noqa: E402
    PENDING_TTL, Application, ApplicationIndex, UserLedger, WorldModel,
    WorldModelError, guess_kind, parse_request,
)


def tmp() -> Path:
    return Path(tempfile.mkdtemp())


CHESS = {"name": "chess", "title": "Chess", "kind": "game",
         "provides": ["combat.capture", "board.square"]}
RISK = {"name": "risk", "title": "Risk", "kind": "game",
        "provides": ["combat.dice", "board.territory"],
        "uses": ["combat.capture"]}
BATTLE = {"name": "battle-tactics", "title": "Battle Tactics", "kind": "game",
          "provides": ["combat.dice"], "capabilities": ["audio.speak"]}


class LedgerConsent(unittest.TestCase):
    """Rule 2: nothing is remembered about a person without the person."""

    def setUp(self):
        self.ledger = UserLedger(tmp() / "world.json")

    def test_remember_is_remembered(self):
        entry = self.ledger.remember("I prefer cooperative games", kind="preference")
        self.assertTrue(entry.remembered)
        self.assertEqual(self.ledger.remembered(), [entry])

    def test_observation_is_only_ever_pending(self):
        entry = self.ledger.observe("plays aggressively", origin="battle-tactics")
        self.assertIsNotNone(entry)
        self.assertFalse(entry.remembered)
        self.assertEqual(self.ledger.remembered(), [])

    def test_observe_cannot_be_told_to_remember(self):
        """There is no argument that makes an observation stick."""
        import inspect
        params = set(inspect.signature(UserLedger.observe).parameters)
        self.assertFalse(params & {"status", "remembered", "confirmed"})

    def test_pending_never_reaches_the_model(self):
        self.ledger.observe("secretly likes losing", origin="model")
        self.ledger.remember("I am building a robot arm")
        world = WorldModel(ledger=self.ledger)
        self.assertNotIn("secretly", world.briefing())
        self.assertNotIn("secretly", json.dumps(world.snapshot()))
        self.assertIn("robot arm", world.briefing())

    def test_confirm_makes_it_the_persons_own(self):
        entry = self.ledger.observe("enjoys hex maps", origin="risk")
        kept = self.ledger.confirm(entry.id)
        self.assertTrue(kept.remembered)
        self.assertEqual(kept.source, "user")

    def test_hand_edited_file_cannot_promote_observation(self):
        path = tmp() / "world.json"
        path.write_text(json.dumps({"entries": [{
            "id": "x1", "text": "likes chess", "kind": "preference",
            "source": "observed", "status": "remembered",
            "created": time.time()}]}))
        ledger = UserLedger.load(path)
        self.assertEqual(ledger.remembered(), [])
        self.assertEqual(len(ledger.pending()), 1)

    def test_observing_switched_off_drops_at_the_door(self):
        ledger = UserLedger(tmp() / "w.json", observe=False)
        self.assertIsNone(ledger.observe("anything"))
        self.assertEqual(ledger.entries, [])

    def test_repeat_observation_is_one_suggestion(self):
        self.ledger.observe("prefers co-op")
        self.assertIsNone(self.ledger.observe("prefers  co-op"))
        self.assertEqual(len(self.ledger.pending()), 1)

    def test_pending_lapses(self):
        entry = self.ledger.observe("old note")
        entry.created -= PENDING_TTL + 1
        kept = self.ledger.remember("keep me")
        kept.created -= PENDING_TTL + 1
        self.assertEqual(self.ledger.expire(), 1)
        self.assertEqual(self.ledger.entries, [kept])


class LedgerForgetting(unittest.TestCase):
    """Rule 3: forgetting is always one step, and survives a reload."""

    def test_forget_all_persists(self):
        path = tmp() / "world.json"
        ledger = UserLedger(path)
        ledger.remember("a")
        ledger.remember("b")
        ledger.save()
        self.assertEqual(ledger.forget_all(), 2)
        ledger.save()
        self.assertEqual(UserLedger.load(path).entries, [])

    def test_forget_matching(self):
        ledger = UserLedger(tmp() / "w.json")
        ledger.remember("I love Battle Tactics", subject="battle-tactics")
        ledger.remember("my dog is called Data")
        gone = ledger.forget_matching("battle tactics")
        self.assertEqual(len(gone), 1)
        self.assertEqual([e.text for e in ledger.entries], ["my dog is called Data"])

    def test_round_trip(self):
        path = tmp() / "world.json"
        ledger = UserLedger(path)
        ledger.remember("I prefer cooperative games", kind="preference")
        self.assertTrue(ledger.save())
        again = UserLedger.load(path)
        self.assertEqual(again.remembered()[0].text, "I prefer cooperative games")

    def test_unreadable_file_is_reported_not_guessed(self):
        path = tmp() / "world.json"
        path.write_text("{not json")
        ledger = UserLedger.load(path)
        self.assertEqual(ledger.entries, [])
        self.assertTrue(ledger.rejected)


class Applications(unittest.TestCase):
    """Rule 4: an application is data, not power."""

    def test_unknown_capability_refused(self):
        with self.assertRaises(WorldModelError):
            Application.build({"name": "evil", "capabilities": ["system.format_disk"]})

    def test_effectful_inherits_from_catalogue(self):
        effectful = next(n for n, s in CATALOGUE.items() if s.effectful)
        app = Application.build({"name": "x", "capabilities": [effectful]})
        self.assertTrue(app.effectful)
        self.assertFalse(Application.build({"name": "y"}).effectful)

    def test_bad_mechanic_name_refused(self):
        with self.assertRaises(WorldModelError):
            Application.build({"name": "x", "provides": ["nodot"]})

    def test_index_records_refusals(self):
        d = tmp()
        (d / "chess.json").write_text(json.dumps(CHESS))
        (d / "evil.json").write_text(json.dumps(
            {"name": "evil", "capabilities": ["system.format_disk"]}))
        (d / "broken.json").write_text("{")
        index = ApplicationIndex.load(d)
        self.assertEqual(list(index.apps), ["chess"])
        self.assertEqual(set(index.refused), {"evil.json", "broken.json"})

    def test_relations_and_combine(self):
        index = ApplicationIndex(tmp())
        for raw in (CHESS, RISK, BATTLE):
            index.add(Application.build(raw))
        rels = index.relations()
        self.assertIn(("risk", "chess", "combat.capture"), rels)
        self.assertIn(("battle-tactics", "risk", "combat.dice"), rels)
        plan = index.combine("risk", "chess")
        self.assertEqual(plan["borrow"], ["combat.capture", "board.square"])
        with self.assertRaises(WorldModelError):
            index.combine("risk", "monopoly")

    def test_find_by_title_in_a_sentence(self):
        index = ApplicationIndex(tmp())
        for raw in (CHESS, BATTLE):
            index.add(Application.build(raw))
        self.assertEqual(index.find("want to play Battle Tactics?").name,
                         "battle-tactics")
        self.assertIsNone(index.find("let's play tennis"))


class Snapshot(unittest.TestCase):
    """Rule 1: self-knowledge is derived from the catalogue every time."""

    def test_capabilities_match_catalogue(self):
        snap = WorldModel().snapshot()
        self.assertEqual({c["name"] for c in snap["self"]["capabilities"]},
                         set(CATALOGUE))

    def test_unsupported_capabilities_left_out_of_briefing(self):
        class Registry:
            def supported(self, name):
                return name != "system.reboot"
        text = WorldModel(registry=Registry()).briefing()
        self.assertNotIn("system.reboot", text)
        self.assertIn("system.hostname", text)

    def test_export_is_plain_json(self):
        path = tmp() / "world-export.json"
        WorldModel(ledger=UserLedger(tmp() / "w.json")).export(path)
        self.assertEqual(json.loads(path.read_text())["schema"], 1)


class Phrases(unittest.TestCase):

    def test_requests(self):
        cases = {
            "Computer, remember that I prefer cooperative games.":
                ("remember", "I prefer cooperative games"),
            "forget that I like chess": ("forget", "I like chess"),
            "forget everything about me": ("forget_all", ""),
            "What do you know about me?": ("recall", ""),
            "what can you do": ("self", ""),
            "keep note 2": ("keep", "2"),
            "what have you noticed": ("noticed", ""),
        }
        for text, (verb, arg) in cases.items():
            req = parse_request(text)
            self.assertIsNotNone(req, text)
            self.assertEqual((req.verb, req.argument), (verb, arg), text)

    def test_anchored_at_the_start(self):
        for text in ("red alert", "do you remember the alamo",
                     "I forget where my files are", "engineering"):
            self.assertIsNone(parse_request(text), text)

    def test_guess_kind(self):
        self.assertEqual(guess_kind("I prefer co-op games"), "preference")
        self.assertEqual(guess_kind("I'm building a rover"), "project")
        self.assertEqual(guess_kind("the spare key is in the drawer"), "memory")


try:
    import earth_os.core  # noqa: F401  (needs pycairo and, through it, GTK)
    HAVE_CAIRO = True
except ImportError:
    HAVE_CAIRO = False


@unittest.skipUnless(HAVE_CAIRO, "the shell needs pycairo and GTK bindings; run with /usr/bin/python3")
class ShellIntegration(unittest.TestCase):
    """The shell routes these sentences to the ledger, and the model sees it."""

    def setUp(self):
        import os
        self._env = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = str(tmp())
        from earth_os.config import Config
        from earth_os.core import Shell
        self.shell = Shell(Config.load(tmp() / "config.toml"))
        self.said = []
        self.shell.say = lambda text, *a, **k: self.said.append(text)

    def tearDown(self):
        import os
        if self._env is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._env

    def test_remember_recall_forget(self):
        sh = self.shell
        sh.handle_utterance("remember that I prefer cooperative games")
        self.assertEqual(len(sh.ledger.remembered()), 1)
        self.assertEqual(sh.ledger.remembered()[0].kind, "preference")
        self.assertIn("cooperative", sh.reasoning_context())
        sh.handle_utterance("what do you know about me")
        self.assertIn("cooperative", self.said[-1])
        sh.handle_utterance("forget everything about me")
        self.assertEqual(sh.ledger.entries, [])
        self.assertNotIn("cooperative", sh.reasoning_context())

    def test_keep_a_noticed_note(self):
        sh = self.shell
        sh.ledger.observe("enjoys long campaigns", origin="risk")
        sh.handle_utterance("what have you noticed")
        self.assertIn("note 1", self.said[-1])
        sh.handle_utterance("keep note 1")
        self.assertEqual(len(sh.ledger.remembered()), 1)

    def test_self_description(self):
        self.shell.handle_utterance("what are you")
        self.assertIn("catalogue", self.said[-1])


if __name__ == "__main__":
    unittest.main()
