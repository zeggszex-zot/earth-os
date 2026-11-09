"""The system corpus, and the claim the whole architecture rests on.

Two things are protected here.

The first is that **the catalogue and the corpus cannot drift**.  Before this
layer existed, 31 of 38 capabilities had no words at all — the machine could
read its own temperature and there was no sentence that would make it, and
nothing anywhere reported that.  The tests below fail if a capability has no
phrasing, or a phrasing names a capability that does not exist.  Adding a
capability therefore breaks the suite until somebody says how to ask for it.

The second is the claim itself:

    The computer changed.  CDOS didn't.

That is asserted literally.  The same sentences are put to a Linux desktop and
to a robot, and the test checks that the vocabulary is unchanged while the
answers are correctly different — including the case that matters most, where
the machine simply does not have the thing being asked about and says so.
"""

from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling test modules

from earth_os.capability_corpus import (                      # noqa: E402
    ADJUSTMENTS, CapabilityCorpus, PERCENT_TARGETS, PHRASEBOOK, READINGS,
)
from earth_os.config import Config                            # noqa: E402
from earth_os.core import Shell                               # noqa: E402
from earth_os.platform import (                               # noqa: E402
    PlatformError,
    CapabilityRegistry, get_platform, reset_platform,
)
from earth_os.platform.capabilities import (                  # noqa: E402
    CATALOGUE, CapabilityResult,
)
from earth_os.platform.robot_stub import MARKER, RobotPlatform  # noqa: E402


class TestCorpusCoversTheCatalogue(unittest.TestCase):
    """The drift guard. This is the test that earns the module."""

    def test_every_capability_can_be_asked_for(self):
        missing = sorted(set(CATALOGUE) - set(PHRASEBOOK))
        self.assertEqual(
            missing, [],
            "\n\nThese capabilities exist but no words reach them:\n  "
            + "\n  ".join(missing)
            + "\n\nAdd phrasings to PHRASEBOOK, or the machine can do something "
              "nobody can ask for.\n")

    def test_no_phrasing_names_a_capability_that_does_not_exist(self):
        invented = sorted(set(PHRASEBOOK) - set(CATALOGUE))
        self.assertEqual(invented, [], f"phrasings for non-capabilities: {invented}")

    def test_every_capability_has_more_than_one_way_to_ask(self):
        """One phrasing is a keyword, not a vocabulary."""
        thin = sorted(c for c, p in PHRASEBOOK.items() if len(p) < 2)
        self.assertEqual(thin, [], f"only one phrasing: {thin}")

    def test_adjustments_reference_real_capabilities(self):
        for phrase, adjustment in ADJUSTMENTS.items():
            self.assertIn(adjustment.read, CATALOGUE, phrase)
            self.assertIn(adjustment.write, CATALOGUE, phrase)

    def test_percent_targets_reference_real_capabilities(self):
        for word, (capability, _arg) in PERCENT_TARGETS.items():
            self.assertIn(capability, CATALOGUE, word)

    def test_readings_are_real_capabilities(self):
        self.assertEqual(sorted(READINGS - set(CATALOGUE)), [])

    def test_no_phrase_is_claimed_by_two_capabilities(self):
        """An ambiguous phrase resolves by accident of ordering."""
        seen = {}
        clashes = []
        for capability, phrases in PHRASEBOOK.items():
            for phrase in phrases:
                if phrase in seen:
                    clashes.append(f"{phrase!r}: {seen[phrase]} and {capability}")
                seen[phrase] = capability
        self.assertEqual(clashes, [], f"duplicate phrasings: {clashes}")


class TestResolution(unittest.TestCase):

    def setUp(self):
        reset_platform()
        self.corpus = CapabilityCorpus(CapabilityRegistry(get_platform()))

    def test_plain_questions_resolve(self):
        for phrase, expected in (
                ("who am i", "user.identity"),
                ("what is your name", "system.hostname"),
                ("how much memory", "hardware.memory"),
                ("what is running", "process.list"),
                ("how many cores", "hardware.cpu_count"),
                ("what networks", "network.interfaces")):
            intent = self.corpus.resolve(phrase)
            self.assertIsNotNone(intent, phrase)
            self.assertEqual(intent.capability, expected, phrase)

    def test_nonsense_resolves_to_nothing(self):
        for phrase in ("", "   ", "banana telephone", "xyzzy"):
            self.assertIsNone(self.corpus.resolve(phrase))

    def test_a_percentage_becomes_an_argument(self):
        intent = self.corpus.resolve("set the brightness to 40 percent")
        self.assertEqual(intent.capability, "display.set_brightness")
        self.assertAlmostEqual(intent.arguments["level"], 0.40)

    def test_a_percentage_is_clamped(self):
        intent = self.corpus.resolve("set the volume to 400 percent")
        self.assertAlmostEqual(intent.arguments["level"], 1.0)

    def test_effectfulness_comes_from_the_catalogue(self):
        self.assertTrue(self.corpus.resolve("shut down").effectful)
        self.assertFalse(self.corpus.resolve("who am i").effectful)

    def test_a_phrase_does_not_match_inside_a_word(self):
        """"lock" must not fire inside "o'clock"."""
        intent = self.corpus.resolve("what is the time on the clock")
        if intent is not None:
            self.assertNotEqual(intent.capability, "system.lock")

    def test_an_adjustment_declines_rather_than_guessing(self):
        """"Brighter" needs to know what it is now.

        With no reading available it must refuse, not start from an invented
        value — moving from a number nobody measured puts the screen somewhere
        the person did not ask for.
        """
        class NoReading:
            def invoke(self, capability, **kwargs):
                class R:
                    ok = False
                    error = "no backlight"
                    value = None
                return R()
        corpus = CapabilityCorpus(NoReading())
        intent = corpus.resolve("make the screen brighter")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.arguments, {})
        self.assertIn("cannot tell", intent.explanation)

    def test_an_adjustment_does_the_arithmetic(self):
        class Reading:
            def invoke(self, capability, **kwargs):
                class R:
                    ok = True
                    error = ""
                    value = 0.50
                return R()
        corpus = CapabilityCorpus(Reading())
        self.assertAlmostEqual(
            corpus.resolve("make the screen brighter").arguments["level"], 0.60)
        self.assertAlmostEqual(
            corpus.resolve("make it dimmer").arguments["level"], 0.40)

    def test_an_adjustment_stays_in_range(self):
        class AtTheTop:
            def invoke(self, capability, **kwargs):
                class R:
                    ok = True
                    error = ""
                    value = 0.97
                return R()
        corpus = CapabilityCorpus(AtTheTop())
        self.assertAlmostEqual(
            corpus.resolve("brighter").arguments["level"], 1.0)


class TestTheShellAnswers(unittest.TestCase):
    """A question whose reply is "user.identity ok" has not been answered."""

    def setUp(self):
        reset_platform()
        self.shell = Shell(Config())
        self.shell.sound.enabled = False

    def _ask(self, phrase: str) -> str:
        self.shell.conversation.clear()
        self.shell.pending_options.clear()
        self.shell.handle_utterance(phrase)
        return next((str(e["text"]) for e in self.shell.conversation
                     if e["role"] == "computer"), "")

    def test_a_question_gets_its_answer_spoken(self):
        reply = self._ask("who am i")
        self.assertTrue(reply)
        self.assertNotIn("ok", reply.lower().split())

    def test_a_reading_with_nothing_to_report_still_replies(self):
        """Silence looks like the question was ignored."""
        self.assertTrue(self._ask("how hot is it"))

    def test_an_effectful_capability_asks_first(self):
        self._ask("shut down")
        self.assertTrue(self.shell.pending_options)

    def test_no_spoken_phrase_powers_the_machine_off_unasked(self):
        """The invariant, tested at the door a person actually speaks through.

        This failed when written.  ``_end_session`` reached the registry by
        ``invoke``, the ungated door, so saying "shut down" powered the
        machine off on one phrase — a speech engine mishearing something close
        to it was enough.  Effectfulness is marked in the catalogue precisely
        to stop that, and the marking meant nothing on this path.
        """
        for phrase in ("shut down", "power off", "reboot",
                       "restart the computer", "log off"):
            shell = Shell(Config())
            shell.sound.enabled = False
            fired = []
            shell.capabilities.invoke = lambda c, **k: (
                fired.append(c) or CapabilityResult(ok=True, capability=c))
            shell.quit_hook = lambda: fired.append("quit")

            shell.handle_utterance(phrase)
            self.assertEqual(
                fired, [],
                f"{phrase!r} acted before anyone confirmed it (ran {fired})")
            self.assertTrue(
                shell.pending_options,
                f"{phrase!r} offered no confirmation")

    def test_confirming_is_what_actually_runs_it(self):
        shell = Shell(Config())
        shell.sound.enabled = False
        fired = []
        shell.capabilities.invoke = lambda c, **k: (
            fired.append(c) or CapabilityResult(ok=True, capability=c))
        shell.quit_hook = lambda: None

        shell.handle_utterance("shut down")
        confirm = dict(shell.pending_options)["CONFIRM"]
        confirm()
        self.assertEqual(fired, ["system.shutdown"])

    def test_cancelling_leaves_the_machine_alone(self):
        shell = Shell(Config())
        shell.sound.enabled = False
        fired = []
        shell.capabilities.invoke = lambda c, **k: (
            fired.append(c) or CapabilityResult(ok=True, capability=c))

        shell.handle_utterance("shut down")
        dict(shell.pending_options)["CANCEL"]()
        self.assertEqual(fired, [])
        self.assertFalse(shell.pending_options)

    def test_the_more_specific_reading_wins(self):
        """The vocabulary matches by containment, so bare "home" fires inside
        "where is my home" and answers a different question."""
        self.assertIn("/", self._ask("where is my home"))

    def test_the_short_vocabulary_phrase_still_works_alone(self):
        """Fixing the above must not break plain navigation."""
        self.shell.conversation.clear()
        self.shell.handle_utterance("home")
        self.assertEqual(self.shell.current, "main")

    def test_an_unreadable_value_is_refused_not_invented(self):
        reply = self._ask("make the screen brighter")
        self.assertIn("unable", reply.lower())


class TestTheComputerChangedCdosDidnt(unittest.TestCase):
    """The architectural claim, asserted rather than described."""

    WORDS = ("who am i", "what is your name", "how is the battery",
             "what hardware", "what networks")

    def tearDown(self):
        os.environ.pop(MARKER, None)
        reset_platform()

    def _shell(self, robot: bool) -> Shell:
        if robot:
            os.environ[MARKER] = "1"
        else:
            os.environ.pop(MARKER, None)
        reset_platform()
        shell = Shell(Config())
        shell.sound.enabled = False
        return shell

    def _answers(self, shell: Shell):
        out = {}
        for phrase in self.WORDS:
            shell.conversation.clear()
            shell.pending_options.clear()
            shell.handle_utterance(phrase)
            out[phrase] = next((str(e["text"]) for e in shell.conversation
                                if e["role"] == "computer"), "")
        return out

    def test_the_robot_adapter_wins_on_a_linux_machine(self):
        """The bug a stub catches that hardware would not.

        This test *is* the justification for building a robot that does
        nothing: the registry ignored its own priority argument, and
        LinuxPlatform.available() is true on any Linux — including a Linux
        robot — so a real robot adapter would have lost every selection.
        """
        self.assertEqual(self._shell(robot=True).platform.name, "Robot")

    def test_the_robot_does_not_take_over_a_desktop(self):
        self.assertEqual(self._shell(robot=False).platform.name, "Linux")

    def test_the_same_words_work_on_both_machines(self):
        desktop = self._answers(self._shell(robot=False))
        robot = self._answers(self._shell(robot=True))
        for phrase in self.WORDS:
            self.assertTrue(desktop[phrase], f"desktop silent on {phrase!r}")
            self.assertTrue(robot[phrase], f"robot silent on {phrase!r}")

    def test_the_answers_differ_because_the_machines_do(self):
        desktop = self._answers(self._shell(robot=False))
        robot = self._answers(self._shell(robot=True))
        self.assertNotEqual(desktop["what is your name"],
                            robot["what is your name"])
        self.assertIn("Gripper", robot["what hardware"])
        self.assertNotIn("Gripper", desktop["what hardware"])

    def test_a_machine_without_the_hardware_says_so(self):
        """The case that matters most: not a crash, not a pretence."""
        shell = self._shell(robot=True)
        shell.conversation.clear()
        shell.handle_utterance("make the screen brighter")
        reply = next(str(e["text"]) for e in shell.conversation
                     if e["role"] == "computer")
        self.assertIn("unable", reply.lower())
        self.assertIn("display", reply.lower())

    def test_the_robot_stops_its_motors_before_cutting_power(self):
        """Order that is a fact about robots, and so lives in the adapter."""
        robot = RobotPlatform()
        robot.system.shutdown()
        self.assertEqual(robot.actions, ["motors.stop", "power.off"])

    def test_the_robot_reports_different_capabilities(self):
        robot = RobotPlatform()
        self.assertFalse(robot.capabilities()["display"])
        self.assertTrue(robot.capabilities()["hardware"])


if __name__ == "__main__":
    unittest.main()


class TestWindowDomainIsMounted(unittest.TestCase):
    """The `window` domain, and the shape of the migration that created it.

    `windows.py` was not unarchitected — it was *separately* architected, as a
    WindowBackend ABC with four implementations written before the boundary
    existed.  Mounting it meant moving the mechanisms below the line and
    leaving the policy above, not rewriting either.  These tests pin both
    halves, because the value of the move is entirely in where things ended
    up.
    """

    SWAY = ('{"nodes":[{"type":"con","id":7,"name":"Firefox",'
            '"app_id":"firefox","focused":true,"pid":9}]}')

    def _sway(self):
        from earth_os.platform.linux_adapter import LinuxWindowControl
        return LinuxWindowControl("sway")

    def test_the_domain_is_in_the_catalogue(self):
        window = {n for n in CATALOGUE if n.startswith("window.")}
        self.assertEqual(
            window,
            {"window.list", "window.focus", "window.close",
             "window.fullscreen", "window.place", "window.can_place"})

    def test_rearranging_the_screen_is_effectful(self):
        """Closing or moving something the person is looking at needs a press;
        focusing does not, because focusing something else undoes it."""
        for name in ("window.close", "window.fullscreen", "window.place"):
            self.assertTrue(CATALOGUE[name].effectful, name)
        for name in ("window.list", "window.focus", "window.can_place"):
            self.assertFalse(CATALOGUE[name].effectful, name)

    def test_it_delegates_to_the_backend_that_moved(self):
        import unittest.mock as mock
        control = self._sway()
        with mock.patch("earth_os.platform.linux_windows.shutil.which",
                        return_value="/usr/bin/swaymsg"), \
             mock.patch("earth_os.platform.linux_windows._run",
                        return_value=(0, self.SWAY)):
            self.assertTrue(control.available())
            windows = control.list_windows()
            self.assertEqual(len(windows), 1)
            self.assertEqual(windows[0].title, "Firefox")
            self.assertTrue(control.focus(windows[0].handle))

    def test_an_unknown_handle_is_refused_not_guessed(self):
        import unittest.mock as mock
        control = self._sway()
        with mock.patch("earth_os.platform.linux_windows.shutil.which",
                        return_value="/usr/bin/swaymsg"), \
             mock.patch("earth_os.platform.linux_windows._run",
                        return_value=(0, self.SWAY)):
            with self.assertRaises(PlatformError):
                control.focus("nonexistent")

    def test_a_machine_with_no_compositor_says_so(self):
        """GNOME and KDE on Wayland expose no such protocol, deliberately.
        That is a permanent normal state, not a fault."""
        reset_platform()
        registry = CapabilityRegistry(get_platform())
        result = registry.invoke("window.list")
        if not get_platform().window.available():
            self.assertFalse(result.ok)
            self.assertIn("window", result.error)

    def test_a_robot_has_no_windows_at_all(self):
        robot = RobotPlatform()
        self.assertFalse(robot.window.available())
        self.assertEqual(robot.window.list_windows(), [])
        self.assertFalse(robot.window.can_place())

    def test_the_policy_layer_stayed_above(self):
        """WindowManager is a decision, not a mechanism: it reads the same on
        sway, on X11 and on a machine with no cooperation at all."""
        from earth_os import windows
        self.assertTrue(hasattr(windows, "WindowManager"))
        self.assertTrue(hasattr(windows, "SELF_APP_IDS"))

    def test_the_mechanisms_went_below(self):
        from earth_os.platform import linux_windows
        for backend in ("SwayWindows", "WlrctlWindows", "WmctrlWindows",
                        "NullWindows", "detect"):
            self.assertTrue(hasattr(linux_windows, backend), backend)


class TestOneRealityPerReading(unittest.TestCase):
    """The gauge and the spoken answer must be the same number.

    `sysinfo.py` used to read /proc and /sys itself while `LinuxHardware` read
    the same files for the catalogue.  Both were "right", both were measured,
    and nothing in the system could say which to believe — so the engineering
    screen could show 57 degrees while the machine said 52.  Worse, the one
    that drifted would be the one nobody was testing, because the tests were
    written against the other.
    """

    def setUp(self):
        reset_platform()
        self.shell = Shell(Config())
        self.shell.sound.enabled = False

    def test_the_gauge_and_the_catalogue_share_one_platform(self):
        self.assertIs(self.shell.monitor.platform, self.shell.platform)

    def test_memory_agrees_to_the_byte(self):
        _frac, _used, total = self.shell.monitor.memory()
        capability = self.shell.capabilities.invoke("hardware.memory")
        self.assertTrue(capability.ok)
        self.assertEqual(total, capability.value[0])

    def test_the_monitor_reads_nothing_itself(self):
        """The guarantee, not just today's values.

        Two readers can agree this morning and drift by evening, so agreement
        is not the thing to assert — the absence of a second reader is. Checked
        against the parsed module rather than its text, because the docstring
        discusses ``/proc`` at length and should be allowed to.
        """
        from test_boundary import mechanism_names           # noqa: E402
        source = (Path(__file__).resolve().parent.parent
                  / "earth_os" / "sysinfo.py")
        self.assertEqual(mechanism_names(source), [],
                         "sysinfo.py names a mechanism again")
        self.assertNotIn("read_text", source.read_text().split('"""')[-1],
                         "sysinfo.py reads a file again")

    def test_every_reading_comes_from_the_platform(self):
        """Sever the platform and every gauge must fall back, not improvise."""
        monitor = self.shell.monitor
        monitor._platform = None
        monitor._cache.clear()
        with unittest.mock.patch("earth_os.sysinfo.get_platform",
                                 side_effect=PlatformError("no adapter")):
            self.assertEqual(monitor.cpu(), 0.0)
            self.assertEqual(monitor.memory(), (0.0, 0, 0))
            self.assertEqual(monitor.processes(), 0)
            self.assertIsNone(monitor.temperature())
            self.assertEqual(monitor.distro(), "unknown")

    def test_a_failed_reading_is_not_re_asked_every_frame(self):
        """A machine with no thermal sensor should be asked once a second,
        not thirty times, forever, for a number it has already refused."""
        asked = []

        class Counting:
            class hardware:
                @staticmethod
                def temperature():
                    asked.append(1)
                    raise PlatformError("no sensor")
        monitor = self.shell.monitor
        monitor._platform = Counting()
        monitor._cache.clear(); monitor._taken.clear()
        for _ in range(30):
            monitor.temperature()
        self.assertEqual(len(asked), 1)

    def test_the_readings_still_have_the_shapes_the_gauges_want(self):
        """The public surface did not change, so nothing above had to."""
        monitor = self.shell.monitor
        self.assertIsInstance(monitor.cpu(), float)
        self.assertEqual(len(monitor.memory()), 3)
        self.assertEqual(len(monitor.disk()), 3)
        self.assertEqual(len(monitor.load()), 3)
        self.assertEqual(len(monitor.net_rate()), 2)

    def test_a_robot_answers_the_same_questions_differently(self):
        from earth_os.sysinfo import SystemMonitor
        monitor = SystemMonitor(platform=RobotPlatform())
        self.assertEqual(monitor.distro(), "CDOS Robot Edition")
        self.assertEqual(monitor.swap(), (0.0, 0, 0))
        self.assertGreater(monitor.cpu(), 0.0)
