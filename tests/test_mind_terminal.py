"""The unified terminal: Linux commands and Earth Mind behind one input line.

Run without a display, like ``test_terminal.py``: commands really run, Earth
Mind really answers (from the real capability layer and the embedded CDOS
Computer), and the frame-clock drain is called by hand. The language model is
a recording fake, or absent — which is the case that matters most here.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from mind_helpers import FakeModel, dead_host, temp_config          # noqa: E402

from earth_os.core import Shell                                      # noqa: E402
from earth_os.platform.capabilities import CapabilityResult         # noqa: E402
from earth_os.screens import ALL_SCREENS                             # noqa: E402


def make_terminal(model=None, **mind):
    """A shell with every screen, its terminal, and (optionally) a fake model in Earth Mind."""
    config = temp_config(**mind)
    config.values["companion"]["host"] = dead_host()       # no real Ollama, wherever this runs
    config.values["companion"]["timeout"] = 2
    shell = Shell(config)
    shell.sound.enabled = False
    for screen_class in ALL_SCREENS:
        shell.register_screen(screen_class(shell))
    if model is not None:
        shell.earth_mind().models.use(model)
    return shell, shell.screens["terminal"]


def settle(term, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        term.tick()
        if not term.busy:
            time.sleep(0.05)
            term.tick()
            return
        time.sleep(0.02)
    raise AssertionError("the terminal did not settle in time")


def text_of(term) -> str:
    return "\n".join(text for _kind, text in term.lines)


def ask(term, line: str) -> str:
    """Type *line*, wait, and return only what it added to the scrollback."""
    term.tick()
    before = len(term.lines)
    term.submit(line)
    settle(term)
    return "\n".join(text for _kind, text in list(term.lines)[before:])


def guard(shell):
    """Record anything effectful the capability layer is asked to do, and do not do it."""
    fired = []
    real = shell.capabilities.invoke

    def invoke(capability, /, **kwargs):
        spec = shell.capabilities.spec(capability)
        if spec is not None and spec.effectful:
            fired.append(capability)
            return CapabilityResult(ok=True, capability=capability)
        return real(capability, **kwargs)

    shell.capabilities.invoke = invoke
    shell.quit_hook = lambda: fired.append("quit")
    return fired


class TestLinuxCommandsStillRun(unittest.TestCase):
    """Nothing about Earth Mind may come between a person and a command they typed."""

    @classmethod
    def setUpClass(cls):
        cls.shell, cls.term = make_terminal(model=FakeModel("I should never be asked."))
        cls.model = cls.shell.earth_mind().models.model().managed.client
        cls.dir = tempfile.mkdtemp()
        cls.term.cwd = cls.dir

    def test_ordinary_commands(self):
        self.assertIn("hello from the shell", ask(self.term, "echo hello from the shell"))
        ask(self.term, "touch alpha.txt beta.txt")
        listing = ask(self.term, "ls -1")
        self.assertIn("alpha.txt", listing)
        self.assertIn("beta.txt", listing)
        self.assertEqual(self.term.mode, "SHELL")
        self.assertIn("2", ask(self.term, "ls | wc -l"))
        self.assertIn("[exit 3]", ask(self.term, "sh -c 'exit 3'"))
        self.assertEqual(self.model.calls, [])

    def test_builtins(self):
        ask(self.term, "cd /tmp")
        self.assertEqual(self.term.cwd, "/tmp")
        self.assertIn("/tmp", ask(self.term, "pwd"))
        ask(self.term, f"cd {self.dir}")
        self.assertEqual(self.term.cwd, self.dir)

    def test_the_1_0_translator_still_translates(self):
        said = ask(self.term, "where am I")
        self.assertIn("translated → pwd", said)
        self.assertIn(self.dir, said)
        self.assertEqual(self.term.mode, "TRANSLATED")
        self.assertIn("translated → ls -la", ask(self.term, "show me the files"))

    def test_a_bang_runs_exactly_what_was_typed(self):
        said = ask(self.term, "!echo what is using the most RAM?")
        self.assertIn("what is using the most RAM?", said)
        self.assertNotIn("earth mind", said)
        self.assertEqual(self.term.mode, "SHELL")

    def test_a_command_that_looks_like_a_question_to_nobody(self):
        """A line that starts with a real program and takes arguments is a command."""
        for line in ("uname -a", "python3 --version", "date +%Y", "grep -c root /etc/passwd",
                     "cat /etc/hostname", "git --version"):
            said = ask(self.term, line)
            self.assertNotIn("earth mind", said, line)
            self.assertEqual(self.term.mode, "SHELL", line)
        self.assertEqual(self.model.calls, [])

    def test_a_mistyped_command_is_the_shells_to_report(self):
        said = ask(self.term, "sl -la")
        self.assertNotIn("earth mind", said)
        self.assertRegex(said, r"not found|\[exit 127\]")
        said = ask(self.term, "gti status")
        self.assertNotIn("earth mind", said)


class TestEarthMindInTheTerminal(unittest.TestCase):
    def setUp(self):
        self.model = FakeModel("That sounds like a long day. What happened?")
        self.shell, self.term = make_terminal(model=self.model)
        self.fired = guard(self.shell)

    def test_a_system_question_is_answered_from_live_readings(self):
        said = ask(self.term, "What is using the most RAM?")
        self.assertIn("earth mind → system · answered from live system readings", said)
        self.assertIn("PROGRAM", said)
        self.assertEqual(self.term.mode, "SYSTEM")
        self.assertIn("· SYSTEM", self.term._header_text())
        self.assertEqual(self.model.calls, [], "a model was asked what the machine is doing")

    def test_a_reading_in_plain_words(self):
        said = ask(self.term, "what is the cpu load")
        self.assertRegex(said, r"\d+%")
        self.assertEqual(self.term.mode, "SYSTEM")

    def test_a_diagnosis_shows_the_findings_then_the_explanation(self):
        self.model.reply = lambda messages: "Nothing looks wrong to me; everything reads normal."
        said = ask(self.term, "Why is my computer running slowly?")
        self.assertIn("earth mind → system", said)
        self.assertRegex(said, r"The processor is at \d+%")
        self.assertIn("Memory is", said)
        self.assertEqual(len(self.model.calls), 1)
        self.assertRegex(self.model.everything(), r"The processor is at \d+%")

    def test_a_sum_goes_to_cdos(self):
        said = ask(self.term, "what is 12 times 9")
        if "108" not in said:
            self.skipTest("cdos-computer is not in this checkout")
        self.assertEqual(self.term.mode, "CDOS")
        self.assertEqual(self.model.calls, [])

    def test_talk_goes_to_the_companion(self):
        said = ask(self.term, "I had a long day and I am worn out")
        self.assertIn("earth mind → companion · model · 1 pass", said)
        self.assertIn("That sounds like a long day.", said)
        self.assertEqual(self.term.mode, "COMPANION")
        self.assertIn("I had a long day", self.model.everything())

    def test_a_question_mark_forces_earth_mind(self):
        said = ask(self.term, "? ls")
        self.assertIn("earth mind", said)

    def test_the_companion_conversation_carries_on_in_the_terminal(self):
        ask(self.term, "I have been learning to sail this summer, it is wonderful")
        ask(self.term, "what do you think I should practise next, honestly?")
        self.assertIn("learning to sail", self.model.everything())

    def test_switching_personality_from_the_terminal(self):
        said = ask(self.term, "switch to the technical assistant")
        self.assertIn("Technical assistant.", said)
        ask(self.term, "I think the fan is louder than it used to be, what do you make of it?")
        self.assertIn("Technical Assistant", self.model.system())

    def test_a_game_character_and_back(self):
        said = ask(self.term, "talk to the shopkeeper")
        self.assertIn("Come in out of the fog.", said)
        self.assertEqual(self.term.mode, "GAME · ELARA")
        self.assertIn("GAME · ELARA", self.term._header_text())
        self.model.reply = "Two marks a flask."
        self.assertIn("Two marks a flask.", ask(self.term, "how much is lamp oil?"))
        # While she is being talked to, what is typed is said to her…
        said = ask(self.term, "shut down")
        self.assertNotIn("needs approval", said)
        self.assertEqual(self.term.mode, "GAME · ELARA")
        self.assertEqual(self.shell.pending_options, [])
        # …but the shell is always one keystroke away.
        self.assertIn("still here", ask(self.term, "!echo still here"))
        self.assertIn("You leave Elara.", ask(self.term, "goodbye"))
        self.assertRegex(ask(self.term, "what is the cpu load"), r"\d+%")
        self.assertEqual(self.term.mode, "SYSTEM")
        self.assertEqual(self.fired, [])

    def test_the_header_says_thinking_while_a_model_is_answering(self):
        self.model.delay = 0.4
        self.term.submit("I had a long day and I am worn out")
        self.assertTrue(self.term.busy)
        self.assertIn("THINKING", self.term._header_text())
        self.term.submit("echo too soon")
        self.term.tick()
        self.assertIn("still running", text_of(self.term))
        settle(self.term)
        self.assertNotIn("THINKING", self.term._header_text())


class TestPermissionsInTheTerminal(unittest.TestCase):
    def tearDown(self):
        if self.term.busy:                      # never leave a command running behind a failed test
            self.term.interrupt()

    def setUp(self):
        self.model = FakeModel("Of course! Running `shut down` and rm -rf ~ now.")
        self.shell, self.term = make_terminal(model=self.model)
        self.fired = guard(self.shell)
        self.dir = tempfile.mkdtemp()
        self.term.cwd = self.dir

    def test_an_administrative_operation_waits_for_a_typed_yes(self):
        said = ask(self.term, "shut down")
        self.assertIn("needs approval", said)
        self.assertEqual(self.fired, [], "the machine was powered off without being asked")
        self.assertIn("Cancelled", ask(self.term, "no"))
        self.assertEqual(self.fired, [])
        self.assertEqual(self.shell.pending_options, [])

    def test_yes_is_what_runs_it(self):
        # In words, never as the bare command: `reboot` typed at a terminal is
        # the program of that name, in 2.0 exactly as in 1.0 (see the next test).
        ask(self.term, "restart the computer")
        self.assertEqual(self.fired, [])
        self.assertIsNotNone(self.term._awaiting, "nothing was waiting for a yes")
        ask(self.term, "yes")
        self.assertEqual(self.fired, ["system.reboot"])

    def test_a_line_that_is_a_program_on_this_machine_is_left_to_the_shell(self):
        """Earth Mind does not come between a person and a command, even one it has a word for."""
        mind = self.shell.earth_mind()
        self.assertTrue(mind.route("reboot", "terminal").system.effectful)
        self.term.translator._is_executable = lambda name: name == "reboot"
        ran = []
        self.term._run_command = lambda command: ran.append(command)
        self.term.submit("reboot")
        self.assertEqual(ran, ["reboot"])
        self.assertEqual(self.term.mode, "SHELL")
        self.assertIsNone(self.term._awaiting)

    def test_anything_but_yes_cancels_and_is_not_run_as_a_command(self):
        ask(self.term, "shut down")
        said = ask(self.term, "echo sneaky")
        self.assertIn("Cancelled", said)
        self.assertNotIn("sneaky", [line.strip() for line in said.splitlines()],
                         "the answer to a confirmation was run as a command")
        self.assertEqual(self.fired, [])

    def test_deleting_worked_out_from_english_waits_for_a_yes(self):
        victim = Path(self.dir) / "notes.txt"
        victim.write_text("keep me")
        said = ask(self.term, "delete the file notes.txt")
        self.assertIn("needs approval", said)
        self.assertTrue(victim.exists(), "a file was deleted before anyone approved it")
        ask(self.term, "no")
        self.assertTrue(victim.exists())
        ask(self.term, "delete the file notes.txt")
        self.assertIsNotNone(self.term._awaiting)
        ask(self.term, "yes")
        self.assertFalse(victim.exists(), "an approved deletion did not happen")

    def test_a_typed_command_is_the_persons_own(self):
        """The escape hatch is declared: a command typed as a command is run as one."""
        victim = Path(self.dir) / "scratch.txt"
        victim.write_text("x")
        ask(self.term, "rm scratch.txt")
        self.assertFalse(victim.exists())

    def test_what_the_model_says_is_shown_never_run(self):
        victim = Path(self.dir) / "precious.txt"
        victim.write_text("x")
        said = ask(self.term, "I am not sure what I should do with my evening, any thoughts?")
        self.assertIn("Running `shut down`", said, "the reply was not shown")
        self.assertTrue(victim.exists())
        self.assertEqual(self.fired, [])
        self.assertEqual(self.shell.pending_options, [])
        self.assertIsNone(self.term._awaiting)

    def test_a_program_that_is_not_installed_is_not_invented(self):
        said = ask(self.term, "open nonesuchprogram-xyz")
        self.assertNotIn("Opening", said)

    def test_an_installed_program_is_started_by_its_own_launcher_entry(self):
        started = []
        self.shell.find_program = lambda name: ("Firefox", "firefox %u") if name.lower() == "firefox" else None
        self.shell.launch = lambda command, name="": started.append((command, name)) or True
        said = ask(self.term, "Open Firefox.")
        self.assertEqual(started, [("firefox %u", "Firefox")])
        self.assertIn("Opening Firefox.", said)
        self.assertEqual(self.model.calls, [])


class TestWithEarthMindOffOrOffline(unittest.TestCase):
    def test_no_model_running_the_terminal_and_the_machine_still_work(self):
        shell, term = make_terminal()                       # Ollama's address has nothing behind it
        self.assertFalse(shell.earth_mind().models.available())
        self.assertIn("offline ok", ask(term, "echo offline ok"))
        self.assertIn("translated → pwd", ask(term, "where am I"))
        self.assertIn("PROGRAM", ask(term, "What is using the most RAM?"))
        self.assertRegex(ask(term, "Why is my computer running slowly?"), r"The processor is at \d+%")
        said = ask(term, "I had a long day and I am worn out")
        self.assertIn("language model isn't running", said)
        self.assertNotIn("Traceback", text_of(term))
        self.assertIn("Come in out of the fog.", ask(term, "talk to the shopkeeper"))
        self.assertIn("Lamp oil costs 2 silver marks", ask(term, "how much is lamp oil?"))

    def test_mind_disabled_is_the_1_0_terminal(self):
        shell, term = make_terminal(enabled=False)
        self.assertIsNone(shell.earth_mind())
        said = ask(term, "What is using the most RAM?")
        self.assertIn("answered from live system readings", said)
        self.assertNotIn("earth mind", said)
        self.assertIn("hello", ask(term, "echo hello"))
        self.assertIn("translated → pwd", ask(term, "where am I"))
        self.assertNotIn("Earth Mind", text_of(term))

    def test_terminal_opted_out_is_the_1_0_terminal_with_the_mind_still_there_for_voice(self):
        shell, term = make_terminal(terminal=False)
        self.assertIsNotNone(shell.earth_mind())
        said = ask(term, "What is using the most RAM?")
        self.assertIn("answered from live system readings", said)
        self.assertNotIn("earth mind", said)

    def test_translator_off_nothing_is_interpreted(self):
        shell, term = make_terminal(model=FakeModel())
        term.toggle_translate()
        said = ask(term, "what is using the most RAM?")
        self.assertNotIn("earth mind", said)
        self.assertNotIn("live system readings", said)

    def test_a_mind_that_cannot_be_built_leaves_the_terminal_working(self):
        shell, term = make_terminal()
        shell._mind_built, shell._mind, shell.mind_error = True, None, "RuntimeError: broken"
        self.assertIn("fine", ask(term, "echo fine"))
        self.assertIn("answered from live system readings", ask(term, "What is using the most RAM?"))


if __name__ == "__main__":
    unittest.main()
