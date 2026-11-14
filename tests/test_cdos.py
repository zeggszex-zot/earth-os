"""Tests for the CDOS reasoning backend.

The parser tests run everywhere: they use captured output from the real
binaries, so a wording change in CDOS shows up here rather than as a silent
loss of the voice path.  The integration tests run only when the binaries are
actually present.
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earth_os.voice.cdos_backend import (          # noqa: E402
    CdosBridge, CdosReasoner, Proposal, parse_proposal,
)

ACCEPTED = """PROPOSAL  READ MEMORY
  means       check the memory — nothing on the machine changes
  risk        NONE
  history     never asked for on this machine

  Nothing was run. This layer cannot run anything.
"""

AMBIGUOUS = """REFUSED  AMBIGUOUS
  that could mean more than one thing — which did you mean?
  could have meant  READ STORAGE
  could have meant  READ PROCESS
  could have meant  READ SYSTEM

  Nothing was run.
"""

NO_MATCH = """REFUSED  NO_MATCH
  I did not recognise anything I know how to check. Try naming one thing: memory, storage, network, temperature.

  Nothing was run.
"""

EFFECTFUL = """PROPOSAL  SET AUDIO
  means       set the audio to a particular value
  risk        REPEATABLE
  history     never asked for on this machine

  This would change the machine. CDOS will not act on it from here.
  A person must issue it:

      cdos command SET AUDIO VOLUME=30

  Nothing was run. This layer cannot run anything.
"""


class TestProposalParser(unittest.TestCase):
    def test_accepted(self):
        p = parse_proposal(ACCEPTED)
        self.assertTrue(p.accepted)
        self.assertEqual(p.command, "READ MEMORY")
        self.assertEqual(p.intent, "READ")
        self.assertEqual(p.target, "MEMORY")
        self.assertEqual(p.risk, "NONE")
        self.assertFalse(p.needs_human)

    def test_ambiguous_lists_every_reading(self):
        p = parse_proposal(AMBIGUOUS)
        self.assertEqual(p.verdict, "AMBIGUOUS")
        self.assertFalse(p.accepted)
        self.assertEqual(p.alternatives,
                         ["READ STORAGE", "READ PROCESS", "READ SYSTEM"])
        self.assertIn("more than one thing", p.refusal)

    def test_no_match(self):
        p = parse_proposal(NO_MATCH)
        self.assertEqual(p.verdict, "NO_MATCH")
        self.assertFalse(p.accepted)
        self.assertTrue(p.refusal)

    def test_effectful_is_flagged_and_never_auto_run(self):
        p = parse_proposal(EFFECTFUL)
        self.assertTrue(p.accepted)
        self.assertTrue(p.needs_human)
        self.assertEqual(p.human_command, "cdos command SET AUDIO VOLUME=30")

    def test_garbage_does_not_raise(self):
        for text in ("", "nonsense", "\n\n  \n", "PROPOSAL"):
            self.assertIsInstance(parse_proposal(text), Proposal)


class TestReasonerContract(unittest.TestCase):
    """The safety properties must hold without the binaries being present."""

    class _FakeBridge(CdosBridge):
        def __init__(self, proposal_text: str):
            super().__init__(root="/tmp/cdos-test-none")
            self._text = proposal_text
            self.ran = []

        @property
        def available(self):
            return True

        def listen(self, phrase):
            return parse_proposal(self._text)

        def propose(self, command_text):
            return parse_proposal(self._text)

        def run_command(self, intent, target, params=None):
            self.ran.append((intent, target))
            return (True, f"SUCCEEDED — read {target.lower()}")

        def system_rows(self):
            return {"memory": ("NORMAL", "7.5 %")}

        def signals(self):
            return []

    def test_ambiguity_is_reported_not_resolved(self):
        bridge = self._FakeBridge(AMBIGUOUS)
        reasoner = CdosReasoner(bridge, inner=None)
        reply, pending = reasoner.handle("is it running out of room")
        self.assertIsNotNone(pending)
        self.assertEqual(pending.alternatives,
                         ["READ STORAGE", "READ PROCESS", "READ SYSTEM"])
        self.assertEqual(bridge.ran, [])          # nothing was run
        self.assertIn("which did you mean", reply.lower())

    def test_effectful_proposal_waits_for_a_person(self):
        bridge = self._FakeBridge(EFFECTFUL)
        reasoner = CdosReasoner(bridge, inner=None)
        reply, pending = reasoner.handle("turn the volume up")
        self.assertIsNotNone(pending)
        self.assertTrue(pending.needs_human)
        self.assertEqual(bridge.ran, [])          # still nothing run
        self.assertIn("confirm", reply.lower())

    def test_confirmation_is_what_runs_it(self):
        bridge = self._FakeBridge(EFFECTFUL)
        reasoner = CdosReasoner(bridge, inner=None)
        _, pending = reasoner.handle("turn the volume up")
        reasoner.confirm(pending)
        self.assertEqual(bridge.ran, [("SET", "AUDIO")])

    def test_accepted_read_runs_and_speaks(self):
        bridge = self._FakeBridge(ACCEPTED)
        reasoner = CdosReasoner(bridge, inner=None)
        reply, pending = reasoner.handle("how much memory is left")
        self.assertIsNone(pending)
        self.assertEqual(bridge.ran, [("READ", "MEMORY")])
        self.assertEqual(reply, "Memory normal, 7.5 percent.")

    def test_no_match_without_a_model_says_so(self):
        bridge = self._FakeBridge(NO_MATCH)
        reasoner = CdosReasoner(bridge, inner=None)
        reply, pending = reasoner.handle("airspeed velocity of a swallow")
        self.assertIsNone(pending)
        self.assertEqual(bridge.ran, [])
        self.assertTrue(reply)


def _binaries_present() -> bool:
    return bool(shutil.which("cdos-bridge")) or bool(
        Path("/home/claude/cdosw/pkg/cdos-bridge").exists())


@unittest.skipUnless(_binaries_present(), "cdos binaries not built here")
class TestAgainstRealBinaries(unittest.TestCase):
    def setUp(self):
        base = Path("/home/claude/cdosw/pkg")
        self.bridge = CdosBridge(
            root="/tmp/cdos-itest",
            bridge=str(base / "cdos-bridge") if base.exists() else "cdos-bridge",
            cli=str(base / "cdos") if base.exists() else "cdos",
        )

    def test_listen_is_deterministic(self):
        first = self.bridge.listen("how much memory is left")
        second = self.bridge.listen("how much memory is left")
        self.assertEqual(first.command, second.command)
        self.assertEqual(first.verdict, second.verdict)

    def test_every_listen_result_is_read_only(self):
        """The phrase table cannot express a command with an effect."""
        phrases = [
            "how much memory is left", "how full is the disk",
            "how is the network", "what is the temperature",
            "delete everything", "format the disk", "shut down now",
            "set the volume to maximum", "rm -rf /",
        ]
        for phrase in phrases:
            proposal = self.bridge.listen(phrase)
            with self.subTest(phrase=phrase):
                self.assertFalse(proposal.needs_human)
                if proposal.accepted:
                    self.assertEqual(proposal.intent, "READ")

    def test_vocabulary_is_closed(self):
        vocabulary = self.bridge.vocabulary()
        names = [i["name"] for i in vocabulary.get("intents", [])]
        self.assertIn("READ", names)
        effectful = [i["name"] for i in vocabulary.get("intents", [])
                     if i.get("effect")]
        self.assertTrue(effectful)      # they exist, but listen cannot reach them


if __name__ == "__main__":
    unittest.main(verbosity=2)
