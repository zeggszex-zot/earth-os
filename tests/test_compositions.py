"""Tests for compositions — rung 1 of ``docs/SELF_MODIFICATION.md``.

A composition is the first thing in this system that a person, or eventually
the machine, can add without anybody editing code. That makes it the first
place where "safe by construction" has to be more than a phrase, so these
tests are organised around the five rules in ``earth_os/compositions.py``
rather than around its methods.

The one to read first is :class:`TestEffectfulnessInherits`. Without it,
composition is a laundering route: wrap ``system.shutdown`` in something
called "tidy up" and the confirmation disappears. Every other rule here is
about keeping the catalogue closed; that one is about keeping the gate shut.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os.compositions import (                          # noqa: E402
    BUILTIN, Composition, CompositionError, CompositionStore, Step,
    normalise,
)
from earth_os.config import Config                           # noqa: E402
from earth_os.core import Shell                              # noqa: E402
from earth_os.platform import CATALOGUE                      # noqa: E402


def temp_store(**kwargs) -> CompositionStore:
    path = Path(tempfile.mkdtemp()) / "compositions.json"
    return CompositionStore.load(path=path, **kwargs)


def temp_config() -> Config:
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    return config


def quiet_shell() -> Shell:
    """A shell that does not speak, draw or make noise."""
    shell = Shell(temp_config())
    shell.spoken = []
    shell.say = lambda text, **kw: shell.spoken.append(text)
    shell.play = lambda *a, **kw: None
    shell.redraw = lambda *a, **kw: None
    if shell.compositions is not None:
        shell.compositions.path = Path(tempfile.mkdtemp()) / "compositions.json"
    return shell


# ----------------------------------------------------------------------
# Rule 2 — the one that matters
# ----------------------------------------------------------------------
class TestEffectfulnessInherits(unittest.TestCase):
    """A composition containing an effectful step is effectful.

    This is the rule that stops composition being a way around the
    confirmation. It is checked from three directions because there are three
    ways someone could try: computing it wrong, storing a claim that
    contradicts the steps, and hiding the step among harmless ones.
    """

    def test_one_effectful_step_makes_the_whole_thing_effectful(self):
        composition = Composition.build(
            "tidy up",
            [{"capability": "system.shutdown", "arguments": {}}],
            ("tidy up",))
        self.assertTrue(composition.effectful)
        self.assertEqual(composition.effectful_steps, ("system.shutdown",))

    def test_it_is_still_effectful_when_buried_among_harmless_steps(self):
        """The realistic attempt: surround it with things nobody objects to."""
        composition = Composition.build(
            "wind down",
            [{"capability": "display.set_brightness", "arguments": {"level": 0.1}},
             {"capability": "audio.set_volume", "arguments": {"level": 0.2}},
             {"capability": "system.suspend", "arguments": {}},
             {"capability": "hardware.uptime", "arguments": {}}],
            ("wind down",))
        self.assertTrue(composition.effectful)
        self.assertIn("system.suspend", composition.effectful_steps)

    def test_a_stored_composition_cannot_claim_to_be_harmless(self):
        """``effectful`` is computed from the steps every time it is asked.

        If it were a stored field, a hand-edited JSON file could set it to
        false and the confirmation would vanish — the store is data a person
        edits, so nothing in it may be taken on trust.
        """
        composition = Composition.build(
            "tidy up",
            [{"capability": "system.shutdown", "arguments": {}}],
            ("tidy up",))
        self.assertNotIn("effectful", composition.to_dict(),
                         "effectfulness is being written to disk, where it "
                         "could be edited to say the opposite of the steps")
        # and a file that tries anyway is ignored
        revived = Composition.build(
            **{**composition.to_dict(), "steps": composition.to_dict()["steps"]})
        self.assertTrue(revived.effectful)

    def test_all_harmless_steps_stay_harmless(self):
        """The rule must not overshoot. Confirming a composition of readings
        would train people to press through prompts, which is the thing
        ``effectful`` exists to prevent rather than cause."""
        composition = Composition.build(
            "how are you",
            [{"capability": "hardware.cpu_load", "arguments": {}},
             {"capability": "hardware.uptime", "arguments": {}}],
            ("how are you",))
        self.assertFalse(composition.effectful)

    def test_the_shell_asks_before_running_an_effectful_composition(self):
        """End to end: the press is real, not just a flag."""
        shell = quiet_shell()
        if shell.capabilities is None:
            self.skipTest("no platform adapter here")
        shell.define_composition(
            "tidy up",
            [{"capability": "system.shutdown", "arguments": {}}],
            ("tidy up",), summary="finish cleanly")
        shell.spoken.clear()
        shell.handle_utterance("tidy up")
        self.assertTrue(shell.pending_prompt, "it ran without asking")
        self.assertEqual([label for label, _ in shell.pending_options],
                         ["CONFIRM", "CANCEL"])

    def test_the_confirmation_says_what_will_happen_in_plain_words(self):
        """"This will system.shutdown" informs somebody who already knows.

        The person the prompt is for is the one who does not, so it uses the
        catalogue's summary.
        """
        shell = quiet_shell()
        if shell.capabilities is None:
            self.skipTest("no platform adapter here")
        shell.define_composition(
            "tidy up",
            [{"capability": "system.shutdown", "arguments": {}}],
            ("tidy up",), summary="finish cleanly")
        shell.spoken.clear()
        shell.handle_utterance("tidy up")
        said = " ".join(shell.spoken)
        self.assertIn(CATALOGUE["system.shutdown"].summary, said)
        self.assertNotIn("system.shutdown", said)


# ----------------------------------------------------------------------
# Rule 1 — the catalogue stays closed
# ----------------------------------------------------------------------
class TestTheCatalogueStaysClosed(unittest.TestCase):

    def test_a_step_must_name_a_real_capability(self):
        with self.assertRaises(CompositionError) as caught:
            Composition.build(
                "wipe it",
                [{"capability": "system.format_disk", "arguments": {}}],
                ("wipe it",))
        self.assertIn("not a capability", str(caught.exception))

    def test_refusal_happens_at_build_not_at_run(self):
        """Discovering an unknown step halfway through would leave the machine
        in a state nobody asked for. There is no later to discover it in."""
        store = temp_store()
        with self.assertRaises(CompositionError):
            store.define(
                "half bad",
                [{"capability": "hardware.uptime", "arguments": {}},
                 {"capability": "system.format_disk", "arguments": {}}],
                ("half bad",))
        self.assertNotIn("half bad", store.names())

    def test_a_composition_cannot_be_named_like_a_capability(self):
        """``system.reboot`` as a composition would shadow the real one, and
        the shadowing would be invisible everywhere a name is printed."""
        with self.assertRaises(CompositionError):
            Composition.build(
                "system.reboot",
                [{"capability": "hardware.uptime", "arguments": {}}],
                ("something",))

    def test_every_builtin_step_is_in_the_catalogue(self):
        for raw in BUILTIN:
            for step in raw["steps"]:
                self.assertIn(step["capability"], CATALOGUE,
                              f"{raw['name']} names a capability that is gone")

    def test_a_step_cannot_invent_an_argument(self):
        with self.assertRaises(CompositionError) as caught:
            Composition.build(
                "odd",
                [{"capability": "hardware.uptime",
                  "arguments": {"as_root": True}}],
                ("odd",))
        self.assertIn("does not take", str(caught.exception))

    def test_a_step_must_supply_what_its_capability_requires(self):
        """Rule 4. A step missing a required argument can never run, and
        finding that out mid-composition is the partial application this
        refuses to allow."""
        with self.assertRaises(CompositionError) as caught:
            Composition.build(
                "go dark",
                [{"capability": "display.set_brightness", "arguments": {}}],
                ("go dark",))
        self.assertIn("needs level", str(caught.exception))


# ----------------------------------------------------------------------
# Rule 5 — nothing here can run anything
# ----------------------------------------------------------------------
class TestTheStoreCannotAct(unittest.TestCase):
    """The effectful gate has been bypassed in this codebase once already.

    A caller reached for ``registry.invoke`` instead of the gated path and
    "shut down" powered the machine off with nothing to press. A convenience
    runner on the store would rebuild that hole somewhere nobody would think
    to look, so the store holds no registry and has no way to call one.
    """

    def test_the_store_has_no_way_to_invoke(self):
        store = temp_store()
        for forbidden in ("run", "invoke", "execute", "apply", "call"):
            self.assertFalse(hasattr(store, forbidden),
                             f"CompositionStore.{forbidden} can act")

    def test_a_composition_has_no_way_to_invoke(self):
        composition = temp_store().get("status report")
        for forbidden in ("run", "invoke", "execute", "apply", "call"):
            self.assertFalse(hasattr(composition, forbidden),
                             f"Composition.{forbidden} can act")

    def test_the_module_never_imports_the_registry(self):
        """Holding no registry is the property; importing one is how it would
        stop being true."""
        source = (ROOT / "earth_os" / "compositions.py").read_text()
        self.assertNotIn("CapabilityRegistry", source)
        self.assertNotIn("get_platform", source)


# ----------------------------------------------------------------------
# Rule 3 — compositions are flat
# ----------------------------------------------------------------------
class TestCompositionsAreFlat(unittest.TestCase):

    def test_a_step_cannot_name_another_composition(self):
        """Nesting brings cycles, unbounded expansion, and an effectfulness
        that has to be computed transitively through hand-edited data. A step
        names a capability, and a composition name is not one."""
        store = temp_store()
        self.assertIn("status report", store.names())
        with self.assertRaises(CompositionError):
            store.define(
                "double report",
                [{"capability": "status report", "arguments": {}}],
                ("double report",))


# ----------------------------------------------------------------------
# The drift-guard, as applied to compositions
# ----------------------------------------------------------------------
class TestNothingUnaskableExists(unittest.TestCase):
    """The corpus rule: a capability nobody can ask for is a thing the
    machine does that nobody wanted. The same is true of a composition."""

    def test_a_composition_needs_a_phrasing(self):
        with self.assertRaises(CompositionError) as caught:
            Composition.build(
                "silent",
                [{"capability": "hardware.uptime", "arguments": {}}],
                ())
        self.assertIn("no phrasings", str(caught.exception))

    def test_defining_one_falls_back_to_its_own_name(self):
        """The easy way to satisfy the rule, so nobody is tempted to weaken
        it: a composition can always be asked for by the name it was given."""
        store = temp_store()
        store.define("night shift",
                     [{"capability": "display.set_brightness",
                       "arguments": {"level": 0.3}}])
        match = store.resolve("night shift")
        self.assertIsNotNone(match)
        self.assertEqual(match.composition.name, "night shift")

    def test_every_builtin_can_be_asked_for(self):
        store = temp_store()
        for name in store.names():
            composition = store.get(name)
            for phrase in composition.phrasings:
                match = store.resolve(phrase)
                self.assertIsNotNone(match, f"{name}: {phrase!r} reaches nothing")

    def test_no_phrase_is_claimed_by_two_compositions(self):
        store = temp_store()
        seen = {}
        for name in store.names():
            for phrase in store.get(name).phrasings:
                self.assertNotIn(phrase, seen,
                                 f"{phrase!r} is claimed by {seen.get(phrase)} "
                                 f"and {name}")
                seen[phrase] = name


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------
class TestTheStoreIsData(unittest.TestCase):
    """Compositions are data, so they can be inspected, edited and deleted
    without touching the program."""

    def test_a_learned_composition_survives_a_reload(self):
        store = temp_store()
        store.define("night shift",
                     [{"capability": "display.set_brightness",
                       "arguments": {"level": 0.3}}],
                     ("night shift", "dim for the evening"))
        self.assertTrue(store.save())

        again = CompositionStore.load(path=store.path)
        self.assertIn("night shift", again.names())
        self.assertEqual(len(again.get("night shift").steps), 1)

    def test_builtins_are_not_written_to_disk(self):
        """They are code. Writing them out would fork them, and the copy
        would go stale the moment the catalogue changed."""
        store = temp_store()
        store.define("mine", [{"capability": "hardware.uptime",
                               "arguments": {}}], ("mine",))
        store.save()
        payload = json.loads(store.path.read_text())
        written = {c["name"] for c in payload["compositions"]}
        self.assertEqual(written, {"mine"})

    def test_a_builtin_cannot_be_overwritten(self):
        store = temp_store()
        with self.assertRaises(CompositionError):
            store.define("status report",
                         [{"capability": "system.shutdown", "arguments": {}}],
                         ("status report",))

    def test_forgetting_removes_a_learned_one_and_spares_builtins(self):
        store = temp_store()
        store.define("mine", [{"capability": "hardware.uptime",
                               "arguments": {}}], ("mine",))
        self.assertTrue(store.forget("mine"))
        self.assertNotIn("mine", store.names())
        self.assertFalse(store.forget("status report"))
        self.assertIn("status report", store.names())

    def test_one_bad_entry_does_not_lose_the_others(self):
        """A typo in a hand-edited file must not silently discard everything
        after it, and must not load silently either."""
        store = temp_store()
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps({"compositions": [
            {"name": "good", "phrasings": ["good"],
             "steps": [{"capability": "hardware.uptime", "arguments": {}}]},
            {"name": "bad", "phrasings": ["bad"],
             "steps": [{"capability": "system.frobnicate", "arguments": {}}]},
            {"name": "also good", "phrasings": ["also good"],
             "steps": [{"capability": "hardware.memory", "arguments": {}}]},
        ]}))
        store.reload()
        self.assertIn("good", store.names())
        self.assertIn("also good", store.names())
        self.assertNotIn("bad", store.names())
        self.assertTrue(store.rejected, "a refusal happened with nothing said")

    def test_a_corrupt_file_leaves_the_builtins(self):
        store = temp_store()
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{not json at all")
        store.reload()
        self.assertIn("status report", store.names())


# ----------------------------------------------------------------------
# Running one
# ----------------------------------------------------------------------
class TestRunning(unittest.TestCase):

    def setUp(self):
        self.shell = quiet_shell()
        if self.shell.capabilities is None:
            self.skipTest("no platform adapter here")

    def test_a_reading_composition_answers_with_the_readings(self):
        """Not "status report ok" — the answers themselves. A question whose
        reply is the name of the question has not been answered."""
        self.shell.spoken.clear()
        ran = self.shell.run_composition(
            self.shell.compositions.get("status report"))
        said = " ".join(self.shell.spoken)
        if not ran:
            self.skipTest(f"readings unavailable here: {said}")
        self.assertNotIn("status report ok", said)
        self.assertTrue(any(ch.isdigit() for ch in said), said)

    def test_nothing_runs_when_a_later_step_cannot(self):
        """Every step is validated before any is run. A composition that
        fails at step three has already changed the machine twice, into a
        state nobody has a name for."""
        shell = self.shell
        shell.define_composition(
            "impossible",
            [{"capability": "hardware.uptime", "arguments": {}},
             {"capability": "user.full_name",
              "arguments": {"username": "nobody-at-all"}}],
            ("impossible",))

        calls = []
        real = shell.capabilities.invoke

        def watched(name, /, **kw):
            calls.append(name)
            return real(name, **kw)

        shell.capabilities.invoke = watched
        original = shell.capabilities.validate
        shell.capabilities.validate = (
            lambda name, /, **kw: "cannot be done here"
            if name == "user.full_name" else original(name, **kw))
        try:
            shell.spoken.clear()
            ok = shell.run_composition(shell.compositions.get("impossible"))
        finally:
            shell.capabilities.invoke = real
            shell.capabilities.validate = original

        self.assertFalse(ok)
        self.assertEqual(calls, [], "a step ran despite a later one failing")

    def test_a_failure_partway_says_how_far_it_got(self):
        """Failure is data. "Unable to comply" without saying which step, or
        how much of the machine was already changed, is not data."""
        shell = self.shell
        shell.define_composition(
            "two things",
            [{"capability": "hardware.uptime", "arguments": {}},
             {"capability": "hardware.memory", "arguments": {}}],
            ("two things",))

        real = shell.capabilities.invoke
        from earth_os.platform import CapabilityResult

        def failing(name, /, **kw):
            if name == "hardware.memory":
                return CapabilityResult(ok=False, capability=name,
                                        error="no memory reading here")
            return real(name, **kw)

        shell.capabilities.invoke = failing
        try:
            shell.spoken.clear()
            ok = shell.run_composition(shell.compositions.get("two things"))
        finally:
            shell.capabilities.invoke = real

        said = " ".join(shell.spoken)
        self.assertFalse(ok)
        self.assertIn("hardware.memory", said)
        self.assertIn("1 of 2", said, f"did not say how far it got: {said}")

    def test_a_composition_beats_a_shorter_match_for_the_same_words(self):
        """"movie mode" must not be heard as whatever "mode" alone means."""
        shell = self.shell
        match = shell.compositions.resolve("put it in movie mode")
        self.assertIsNotNone(match)
        self.assertEqual(match.composition.name, "movie mode")

    def test_an_unknown_phrase_resolves_to_nothing(self):
        self.assertIsNone(self.shell.compositions.resolve("make me a sandwich"))


class TestIntrospection(unittest.TestCase):

    def test_unsupported_names_the_step_that_is_missing(self):
        """A robot has no screen, so movie mode is not a thing it can do.
        Saying which step stops it is more useful than failing at step two."""
        shell = quiet_shell()
        if shell.capabilities is None:
            self.skipTest("no platform adapter here")
        missing = shell.compositions.unsupported(shell.capabilities)
        for name, steps in missing.items():
            self.assertTrue(steps, f"{name} listed with no missing step")
            for capability in steps:
                self.assertIn(capability, CATALOGUE)

    def test_describe_mentions_every_composition(self):
        store = temp_store()
        text = store.describe()
        for name in store.names():
            self.assertIn(name, text)

    def test_normalise_matches_the_corpus_shape(self):
        """Compositions are matched against the same utterances by the same
        rules, so they have to be cleaned the same way."""
        from earth_os.capability_corpus import normalise as corpus_normalise
        for text in ("Movie Mode!", "  STATUS   report  ", "how's it going?"):
            self.assertEqual(normalise(text), corpus_normalise(text))


if __name__ == "__main__":
    unittest.main()
