"""Tests for the capability layer and the shell's use of it.

Two things are being protected here.

The first is the **gate**.  A model asked to interpret an unfamiliar request
will produce a confident, plausible answer, and ``system.format_disk`` is a
very plausible answer.  The catalogue is the list of things that exist, and
nothing outside it may be invoked however it is spelled.  If those tests ever
start passing something they should not, the machine has grown a capability
nobody wrote down.

The second is that **the shell no longer knows any Linux**.  Starting a
program, powering down, asking who is logged in: each has exactly one path,
through the platform.  The tests here assert the shell goes that way by
severing the platform and checking that the shell genuinely cannot do the
thing any more — a second, quieter code path would show up as a pass.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earth_os.config import Config                          # noqa: E402
from earth_os.core import Shell                             # noqa: E402
from earth_os.platform import (                             # noqa: E402
    CATALOGUE, CapabilityRegistry, CapabilityResult, get_platform,
)
from earth_os.platform.api import PlatformError             # noqa: E402


def temp_config() -> Config:
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    return config


def registry() -> CapabilityRegistry:
    return CapabilityRegistry(get_platform())


class TestCatalogue(unittest.TestCase):
    """The catalogue is hand-written, so it can drift from the bindings."""

    def setUp(self):
        self.reg = registry()

    def test_every_entry_is_bound(self):
        unbound = [n for n in CATALOGUE if n not in self.reg._bindings]
        self.assertEqual(unbound, [], "catalogue entries with no platform method")

    def test_every_binding_is_in_the_catalogue(self):
        """A binding with no entry is unreachable — and, worse, is a method
        that was wired up without anyone deciding whether it is effectful."""
        stray = [n for n in self.reg._bindings if n not in CATALOGUE]
        self.assertEqual(stray, [])

    def test_names_are_domain_dot_action(self):
        for name, spec in CATALOGUE.items():
            self.assertIn(".", name)
            self.assertEqual(spec.name, name)
            self.assertTrue(spec.summary, f"{name} has no summary")

    def test_domains_match_the_platform_interface(self):
        platform = get_platform()
        for spec in CATALOGUE.values():
            self.assertTrue(hasattr(platform, spec.domain),
                            f"{spec.name} names a domain the platform lacks")

    def test_destructive_things_are_marked_effectful(self):
        """The marking is the only thing standing between a misheard phrase
        and a machine that powers off, so it is asserted rather than trusted."""
        for name in ("system.reboot", "system.shutdown", "system.logout",
                     "filesystem.delete", "filesystem.write",
                     "process.terminate", "display.set_resolution"):
            self.assertTrue(CATALOGUE[name].effectful, name)

    def test_reversible_things_are_not_effectful(self):
        """Confirming everything would train people to confirm without
        reading, which is worse than confirming nothing."""
        for name in ("process.launch", "system.lock", "audio.set_volume",
                     "display.set_brightness", "filesystem.read",
                     "hardware.cpu_count", "user.identity"):
            self.assertFalse(CATALOGUE[name].effectful, name)


class TestTheGate(unittest.TestCase):
    """Nothing outside the catalogue runs, however it is spelled."""

    def setUp(self):
        self.reg = registry()

    def test_invented_capability_is_refused(self):
        result = self.reg.invoke("system.format_disk")
        self.assertFalse(result.ok)
        self.assertIn("no such capability", result.error)

    def test_plausible_near_miss_is_refused(self):
        """Refused, and *not* helpfully corrected to the real one: guessing
        what was meant is exactly the behaviour this layer exists to stop."""
        for name in ("system.reboot_now", "system.restart", "process.kill",
                     "filesystem.remove", "SYSTEM.REBOOT "):
            with self.subTest(name=name):
                # Check before invoking: "SYSTEM.REBOOT " normalises to a real
                # entry, and invoking it on a live machine reboots it.
                if name.strip().lower() in CATALOGUE:
                    continue
                result = self.reg.invoke(name)
                self.assertFalse(result.ok)
                self.assertIn("no such capability", result.error)

    def test_empty_and_junk_names_are_refused(self):
        for name in ("", "   ", "reboot", "..", "system.", ".reboot"):
            self.assertFalse(self.reg.invoke(name).ok, name)

    def test_known_is_the_gate_in_one_call(self):
        self.assertTrue(self.reg.known("system.reboot"))
        self.assertFalse(self.reg.known("system.format_disk"))

    def test_case_and_space_are_forgiven_but_nothing_else_is(self):
        self.assertTrue(self.reg.invoke("  SYSTEM.HOSTNAME  ").ok)

    def test_a_capability_cannot_be_reached_by_attribute_shape(self):
        """The binding table is written out rather than derived from the
        name, so a name shaped like a method cannot reach one."""
        for name in ("system.get_hostname", "process.list_processes",
                     "user.current_user", "system.__class__"):
            self.assertFalse(self.reg.invoke(name).ok, name)


class TestValidation(unittest.TestCase):

    def setUp(self):
        self.reg = registry()

    def test_missing_argument_is_named(self):
        result = self.reg.invoke("filesystem.read")
        self.assertFalse(result.ok)
        self.assertIn("needs path", result.error)

    def test_unexpected_argument_is_refused(self):
        """Silently dropping an argument a model invented would let it
        believe it had asked for something it had not."""
        result = self.reg.invoke("filesystem.read", path="/etc/hostname",
                                 sudo=True)
        self.assertFalse(result.ok)
        self.assertIn("sudo", result.error)

    def test_optional_argument_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "a.txt")
            self.assertTrue(
                self.reg.invoke("filesystem.write", path=path,
                                data=b"x", append=False).ok)

    def test_validate_matches_invoke(self):
        self.assertEqual(self.reg.validate("system.hostname"), "")
        self.assertTrue(self.reg.validate("system.nope"))

    def test_unavailable_domain_says_so_plainly(self):
        platform = get_platform()
        with unittest.mock.patch.object(type(platform.audio), "available",
                                        return_value=False):
            reg = CapabilityRegistry(platform)
            result = reg.invoke("audio.speak", text="hello")
            self.assertFalse(result.ok)
            self.assertIn("audio is not available", result.error)
            self.assertNotIn("audio.speak", reg.available())


class TestInvocation(unittest.TestCase):

    def setUp(self):
        self.reg = registry()

    def test_reads_come_back_as_values(self):
        self.assertIsInstance(self.reg.invoke("system.hostname").value, str)
        self.assertGreater(self.reg.invoke("hardware.cpu_count").value, 0)
        self.assertTrue(self.reg.invoke("user.identity").value)

    def test_round_trip_through_the_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "note.txt")
            self.assertTrue(self.reg.invoke("filesystem.write", path=path,
                                            data=b"hello").ok)
            self.assertTrue(self.reg.invoke("filesystem.exists", path=path).value)
            self.assertEqual(self.reg.invoke("filesystem.read", path=path).value,
                             b"hello")

    def test_platform_failure_is_data_not_an_exception(self):
        """This sits under a voice loop and a button, neither of which has
        anywhere sensible to put a traceback."""
        result = self.reg.invoke("filesystem.read", path="/no/such/file")
        self.assertFalse(result.ok)
        self.assertTrue(result.error)

    def test_a_broken_adapter_is_contained(self):
        platform = get_platform()
        with unittest.mock.patch.object(type(platform.system), "get_hostname",
                                        side_effect=RuntimeError("boom")):
            result = CapabilityRegistry(platform).invoke("system.hostname")
            self.assertFalse(result.ok)
            self.assertIn("RuntimeError", result.error)

    def test_result_is_falsey_when_it_failed(self):
        self.assertFalse(bool(CapabilityResult(ok=False)))
        self.assertTrue(bool(CapabilityResult(ok=True)))


class TestDescribe(unittest.TestCase):
    """How a model finds out what this computer can do: it is told."""

    def setUp(self):
        self.reg = registry()

    def test_describes_only_what_is_available(self):
        platform = get_platform()
        with unittest.mock.patch.object(type(platform.network), "available",
                                        return_value=False):
            text = CapabilityRegistry(platform).describe()
        self.assertNotIn("network.connect", text)
        self.assertIn("system.reboot", text)

    def test_marks_the_ones_that_need_a_person(self):
        text = self.reg.describe()
        for line in text.splitlines():
            if line.strip().startswith("system.reboot"):
                self.assertIn("confirmation", line)
                break
        else:
            self.fail("system.reboot missing from the description")

    def test_names_required_arguments(self):
        self.assertIn("filesystem.read <path>", self.reg.describe())

    def test_summary_counts_honestly(self):
        summary = self.reg.summary()
        self.assertIn(str(len(CATALOGUE)), summary)
        self.assertIn(get_platform().name, summary)


class TestShellUsesThePlatform(unittest.TestCase):
    """The shell must have no second, Linux-shaped way to do these things."""

    def setUp(self):
        self.shell = Shell(temp_config())

    def test_shell_has_a_platform_and_a_registry(self):
        self.assertIsNotNone(self.shell.platform)
        self.assertIsNotNone(self.shell.capabilities)
        self.assertEqual(self.shell.platform_error, "")

    def test_spawn_goes_through_the_platform(self):
        calls = []

        def fake_launch(capability, /, **kwargs):
            calls.append((capability, kwargs))
            return CapabilityResult(ok=False, error="intercepted")

        with unittest.mock.patch.object(self.shell.capabilities, "invoke",
                                        side_effect=fake_launch):
            self.shell.spawn(["echo", "hi"], label="echo hi")
        self.assertEqual(calls[0][0], "process.launch")
        self.assertEqual(calls[0][1]["argv"], ["echo", "hi"])

    def test_spawn_reports_a_missing_program(self):
        self.assertFalse(self.shell.spawn(["/definitely/not/here"]))
        self.assertIn("not installed", self.shell.status_message)

    def test_spawn_really_starts_something(self):
        self.assertTrue(self.shell.spawn(["true"]))
        self.assertIn("launched", self.shell.status_message)

    def test_without_a_platform_the_shell_cannot_start_programs(self):
        """No quiet Linux-only fallback: severing the platform must actually
        sever it, or the refactor achieved nothing."""
        self.shell.capabilities = None
        self.shell.platform_error = "no adapter"
        self.assertFalse(self.shell.spawn(["true"]))
        self.assertIn("no adapter", self.shell.status_message)

    def _confirm(self) -> None:
        """Press the CONFIRM the shell is now waiting on.

        Powering down is effectful, so it is offered rather than done. This
        used to run straight through: saying "shut down" powered the machine
        off on one phrase, and these tests passed because they asserted that
        behaviour. They assert the capability is still what gets asked for —
        the Phase 2 point — with the press that should always have been there.
        """
        dict(self.shell.pending_options)["CONFIRM"]()

    def test_reboot_asks_for_the_capability(self):
        asked = []
        with unittest.mock.patch.object(
                self.shell.capabilities, "invoke",
                side_effect=lambda n, **k: asked.append(n) or CapabilityResult(ok=True)):
            self.shell.reboot()
            self._confirm()
            self.shell.shutdown()
            self._confirm()
            self.shell.log_off()
            self._confirm()
        self.assertEqual(asked,
                         ["system.reboot", "system.shutdown", "system.logout"])

    def test_powering_down_is_offered_not_done(self):
        """Nothing reaches the platform until a person presses something."""
        asked = []
        with unittest.mock.patch.object(
                self.shell.capabilities, "invoke",
                side_effect=lambda n, **k: asked.append(n) or CapabilityResult(ok=True)):
            self.shell.shutdown()
        self.assertEqual(asked, [])
        self.assertTrue(self.shell.pending_options)

    def test_powering_down_falls_back_to_quitting(self):
        """On a machine that cannot power itself off, ending the shell is the
        nearest thing to what was asked, and beats appearing to ignore it."""
        quits = []
        self.shell.quit_hook = lambda: quits.append(True)
        with unittest.mock.patch.object(
                self.shell.capabilities, "invoke",
                return_value=CapabilityResult(ok=False, error="denied")):
            self.shell.shutdown()
            self._confirm()
        self.assertEqual(quits, [True])
        self.assertIn("denied", self.shell.status_message)

    def _with_user(self, login: str, full_name: str):
        """Patch the platform's user capability and rebuild the registry.

        The registry binds platform methods when it is constructed, so a
        patch applied afterwards would not be seen — rebuilding is what a
        real platform swap would do anyway.
        """
        patches = (
            unittest.mock.patch.object(type(self.shell.platform.user),
                                       "current_user", return_value=login),
            unittest.mock.patch.object(type(self.shell.platform.user),
                                       "user_full_name", return_value=full_name),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.shell.capabilities = CapabilityRegistry(self.shell.platform)

    def test_operator_name_comes_from_the_platform(self):
        self._with_user("spock", "S'chn T'gai Spock")
        self.assertEqual(self.shell.operator_name(), "S'chn T'gai Spock")

    def test_operator_name_falls_back_to_the_login_name(self):
        self._with_user("spock", "")
        self.assertEqual(self.shell.operator_name(), "spock")

    def test_configured_operator_still_wins(self):
        self.shell.config.values.setdefault("shell", {})["operator"] = "CHIEF"
        self.assertEqual(self.shell.operator_name(), "CHIEF")


class TestShellRunCapability(unittest.TestCase):
    """Intent in, with a person in the loop for anything that bites."""

    def setUp(self):
        self.shell = Shell(temp_config())

    def test_harmless_capability_runs_straight_away(self):
        self.assertTrue(self.shell.run_capability("system.hostname"))
        self.assertEqual(self.shell.pending_options, [])

    def test_effectful_capability_only_offers(self):
        ran = []
        with unittest.mock.patch.object(
                self.shell.capabilities, "invoke",
                side_effect=lambda n, **k: ran.append(n) or CapabilityResult(ok=True)):
            self.assertFalse(self.shell.run_capability("system.reboot"))
        self.assertEqual(ran, [])                      # nothing happened yet
        labels = [label for label, _ in self.shell.pending_options]
        self.assertEqual(labels, ["CONFIRM", "CANCEL"])
        self.assertIn("REBOOT", self.shell.pending_prompt.upper())

    def test_confirming_an_effectful_capability_runs_it(self):
        ran = []
        with unittest.mock.patch.object(
                self.shell.capabilities, "invoke",
                side_effect=lambda n, **k: ran.append(n) or CapabilityResult(ok=True)):
            self.shell.run_capability("system.reboot")
            self.shell.pending_options[0][1]()
        self.assertEqual(ran, ["system.reboot"])
        self.assertEqual(self.shell.pending_options, [])

    def test_cancelling_an_effectful_capability_runs_nothing(self):
        with unittest.mock.patch.object(
                self.shell.capabilities, "invoke",
                side_effect=AssertionError("must not run")):
            self.shell.run_capability("system.shutdown")
            self.shell.cancel_pending()
        self.assertEqual(self.shell.pending_options, [])

    def test_unknown_capability_never_reaches_a_confirmation(self):
        """An invented name must be refused outright, not offered to the
        person as a button they could press."""
        self.assertFalse(self.shell.run_capability("system.format_disk"))
        self.assertEqual(self.shell.pending_options, [])
        self.assertIn("no such capability", self.shell.status_message)

    def test_catalogue_is_offered_as_text(self):
        text = self.shell.capability_catalogue()
        self.assertIn("system.reboot", text)
        self.assertIn("process.launch", text)


if __name__ == "__main__":
    unittest.main()
