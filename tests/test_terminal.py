"""Tests for the LCARS terminal screen and its Universal Translator.

These exercise the screen's logic without a display: commands really run,
output really streams through the worker thread, and the frame-clock drain is
called by hand instead of by GTK.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earth_os.config import Config                      # noqa: E402
from earth_os.core import Shell                         # noqa: E402
from earth_os.screens import ALL_SCREENS                # noqa: E402
from earth_os.translator import CommandTranslator       # noqa: E402


def temp_config() -> Config:
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    return config


def make_terminal():
    shell = Shell(temp_config())
    for screen_class in ALL_SCREENS:
        shell.register_screen(screen_class(shell))
    return shell, shell.screens["terminal"]


def settle(term, timeout: float = 15.0) -> None:
    """Wait for the running command to finish, draining like the frame clock."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        term.tick()
        if not term.busy:
            # One more drain: the worker may still be flushing its last lines.
            time.sleep(0.05)
            term.tick()
            return
        time.sleep(0.02)
    raise AssertionError("command did not finish in time")


def text_of(term) -> str:
    return "\n".join(text for _kind, text in term.lines)


class TestTranslator(unittest.TestCase):
    def setUp(self):
        self.t = CommandTranslator()

    def test_natural_language_becomes_commands(self):
        cases = {
            "Show me the files": "ls -la",
            "show me the files in downloads": "ls -la ~/Downloads",
            "where am I": "pwd",
            "go home": "cd ~",
            "Go to downloads": "cd ~/Downloads",
            "go back two directories": "cd ../../",
            "go up": "cd ..",
            "who am I": "whoami",
            "create a file called Hello.txt": "touch Hello.txt",
            "read notes.txt": "cat notes.txt",
            "how much memory": "free -h",
            "count the lines in data.csv": "wc -l data.csv",
            "rename a.txt to b.txt": "mv a.txt b.txt",
        }
        for phrase, expected in cases.items():
            self.assertEqual(self.t.translate(phrase), expected, phrase)

    def test_filenames_keep_their_capitals_and_spaces(self):
        self.assertEqual(self.t.translate("create a folder called My Projects"),
                         "mkdir -p 'My Projects'")
        self.assertEqual(self.t.translate("create a file called My Notes.txt"),
                         "touch 'My Notes.txt'")

    def test_real_shell_commands_pass_through_untouched(self):
        for cmd in ("ls -la", "cd /tmp", "git status", "sudo apt update",
                    "python3 script.py", "ls | grep py", "echo hello",
                    "grep -rn TODO .", "make -j4"):
            self.assertEqual(self.t.translate(cmd), cmd, cmd)

    def test_deletion_asks_before_destroying(self):
        # A misheard filename must not silently delete something.
        self.assertIn("-i", self.t.translate("delete the file notes.txt"))

    def test_no_pattern_matches_a_fragment(self):
        # The original bug: a pattern of optional groups matched the "i"
        # inside "time", so "What time is it?" translated to pwd.
        self.assertEqual(self.t.translate("What time is it?"),
                         "date '+%-I:%M %p'")

    def test_unknown_phrases_are_returned_unchanged(self):
        for phrase in ("flibbertigibbet", "do the thing", "xyzzy plugh"):
            self.assertEqual(self.t.translate(phrase), phrase)


class TestTerminalScreen(unittest.TestCase):
    def setUp(self):
        self.shell, self.term = make_terminal()
        self.tmp = tempfile.mkdtemp()
        self.term.cwd = self.tmp

    # -- registration --------------------------------------------------
    def test_screen_is_registered_and_reachable(self):
        self.assertIn("terminal", self.shell.screens)
        self.assertTrue(self.shell.run_action("terminal"))
        self.assertEqual(self.shell.current, "terminal")

    def test_voice_synonyms_reach_the_terminal(self):
        for phrase in ("command line", "console", "shell"):
            self.shell.go("main")
            self.assertTrue(self.shell.run_action(phrase), phrase)
            self.assertEqual(self.shell.current, "terminal", phrase)

    # -- running commands ----------------------------------------------
    def test_a_real_command_runs_and_streams_output(self):
        self.term.submit("echo hello from cdos")
        settle(self.term)
        self.assertIn("hello from cdos", text_of(self.term))

    def test_translated_command_runs_and_shows_what_it_decided(self):
        open(os.path.join(self.tmp, "marker.txt"), "w").close()
        self.term.submit("Show me the files")
        settle(self.term)
        body = text_of(self.term)
        self.assertIn("translated → ls -la", body)
        self.assertIn("marker.txt", body)

    def test_failing_command_reports_its_exit_code(self):
        # Not a bare "exit 3": `exit` is a builtin here that leaves the screen.
        self.term.submit("sh -c 'exit 3'")
        settle(self.term)
        self.assertIn("[exit 3]", text_of(self.term))

    def test_cd_onto_a_file_says_so(self):
        path = os.path.join(self.tmp, "afile.txt")
        open(path, "w").close()
        self.term.submit(f"cd {path}")
        self.term.tick()
        self.assertIn("Not a directory", text_of(self.term))

    def test_stderr_is_captured(self):
        self.term.submit("ls /definitely/not/here")
        settle(self.term)
        self.assertIn("No such file", text_of(self.term))

    def test_command_reading_stdin_does_not_hang(self):
        # stdin is /dev/null, so `cat` gets EOF instead of blocking forever.
        self.term.submit("cat")
        settle(self.term, timeout=10)
        self.assertFalse(self.term.busy)

    # -- builtins -------------------------------------------------------
    def test_cd_persists_between_commands(self):
        sub = os.path.join(self.tmp, "sub")
        os.mkdir(sub)
        self.term.submit("cd sub")
        self.term.tick()
        self.assertEqual(self.term.cwd, os.path.realpath(sub))

        self.term.submit("pwd")
        self.term.tick()
        self.assertIn(os.path.realpath(sub), text_of(self.term))

    def test_cd_handles_tilde_and_quoted_names_with_spaces(self):
        spaced = os.path.join(self.tmp, "My Folder")
        os.mkdir(spaced)
        self.term.submit("cd 'My Folder'")
        self.term.tick()
        self.assertEqual(self.term.cwd, os.path.realpath(spaced))

        self.term.submit("cd ~")
        self.term.tick()
        self.assertEqual(self.term.cwd, os.path.realpath(os.path.expanduser("~")))

    def test_cd_to_missing_directory_reports_clearly(self):
        before = self.term.cwd
        self.term.submit("cd /no/such/place")
        self.term.tick()
        self.assertEqual(self.term.cwd, before)
        self.assertIn("No such directory", text_of(self.term))

    def test_cd_dash_returns_to_the_previous_directory(self):
        sub = os.path.join(self.tmp, "sub")
        os.mkdir(sub)
        start = self.term.cwd
        self.term.submit("cd sub")
        self.term.tick()
        self.term.submit("cd -")
        self.term.tick()
        self.assertEqual(self.term.cwd, start)

    def test_natural_language_navigation_changes_directory(self):
        # "go to <path>" must reach the cd builtin, not a subprocess whose
        # directory change would die with it.
        sub = os.path.join(self.tmp, "sub")
        os.mkdir(sub)
        self.term.submit(f"go to {sub}")
        self.term.tick()
        self.assertEqual(self.term.cwd, os.path.realpath(sub))

    def test_clear_empties_the_scrollback(self):
        self.term.submit("echo something")
        settle(self.term)
        self.assertTrue(self.term.lines)
        self.term.submit("clear")
        self.term.tick()
        self.assertEqual(len(self.term.lines), 0)

    def test_exit_leaves_the_screen_rather_than_quitting_the_shell(self):
        self.shell.go("terminal")
        self.term.submit("exit")
        self.assertNotEqual(self.shell.current, "terminal")

    def test_interactive_programs_are_refused_with_an_explanation(self):
        self.term.submit("nano notes.txt")
        self.term.tick()
        body = text_of(self.term)
        self.assertIn("needs a full terminal", body)
        self.assertFalse(self.term.busy)

    # -- input line -----------------------------------------------------
    def test_typing_and_editing(self):
        for ch in "echo hi":
            self.term.on_key(ord(ch), ch)
        self.assertEqual(self.term.input, "echo hi")

        self.term.on_key(0, "BackSpace")
        self.assertEqual(self.term.input, "echo h")

        self.term.on_key(0, "Home")
        self.assertEqual(self.term.cursor, 0)
        self.term.on_key(ord("X"), "X")
        self.assertEqual(self.term.input, "Xecho h")

    def test_history_recall(self):
        self.term.submit("echo one")
        settle(self.term)
        self.term.submit("echo two")
        settle(self.term)

        self.term.on_key(0, "Up")
        self.assertEqual(self.term.input, "echo two")
        self.term.on_key(0, "Up")
        self.assertEqual(self.term.input, "echo one")
        self.term.on_key(0, "Down")
        self.assertEqual(self.term.input, "echo two")

    def test_ctrl_c_clears_the_line_when_nothing_is_running(self):
        for ch in "rm -rf":
            self.term.on_key(ord(ch), ch)
        self.term.on_key(ord("c"), "c", True)
        self.assertEqual(self.term.input, "")

    def test_escape_and_function_keys_are_never_swallowed(self):
        # The screen must not become a trap: these have to reach the app.
        for name in ("Escape", "F1", "F2", "F5", "F7", "F11", "F12"):
            self.assertFalse(self.term.on_key(0, name), name)

    def test_ctrl_q_is_left_to_the_application(self):
        self.assertFalse(self.term.on_key(ord("q"), "q", True))

    def test_tab_completes_a_filename(self):
        open(os.path.join(self.tmp, "uniquename.txt"), "w").close()
        for ch in "cat uniq":
            self.term.on_key(ord(ch), ch)
        self.term.on_key(0, "Tab")
        self.assertEqual(self.term.input, "cat uniquename.txt")

    def test_translator_can_be_turned_off(self):
        self.term.toggle_translate()
        self.assertFalse(self.term.translate_enabled)
        self.term.submit("Show me the files")
        settle(self.term)
        self.assertNotIn("translated", text_of(self.term))

    def test_a_question_about_memory_is_answered_not_run(self):
        """1.0.0 ran "What is using the most RAM?" as a command called what."""
        self.term.submit("What is using the most RAM?")
        self.assertFalse(self.term.busy, "nothing should have been spawned")
        settle(self.term)
        body = text_of(self.term)
        self.assertIn("answered from live system readings", body)
        self.assertIn("PROGRAM", body)
        self.assertNotIn("command not found", body)

    def test_a_real_command_about_memory_is_left_alone(self):
        self.term.submit("ps -eo rss,comm --sort=-rss")
        settle(self.term)
        body = text_of(self.term)
        self.assertNotIn("answered from live system readings", body)
        self.assertIn("RSS", body)

    def test_scrollback_is_bounded(self):
        self.term.submit("seq 1 6000")
        settle(self.term, timeout=30)
        self.assertLessEqual(len(self.term.lines), self.term.lines.maxlen)

    def test_interrupt_stops_a_running_command(self):
        self.term.submit("sleep 30")
        # Give the process a moment to actually start.
        deadline = time.time() + 5
        while time.time() < deadline and not self.term.busy:
            time.sleep(0.02)
        self.assertTrue(self.term.busy)
        self.term.interrupt()
        settle(self.term, timeout=10)
        self.assertFalse(self.term.busy)

    def test_second_command_is_refused_while_one_is_running(self):
        self.term.submit("sleep 5")
        deadline = time.time() + 5
        while time.time() < deadline and not self.term.busy:
            time.sleep(0.02)
        self.term.submit("echo nope")
        self.term.tick()
        self.assertIn("still running", text_of(self.term))
        self.term.interrupt()
        settle(self.term, timeout=10)


if __name__ == "__main__":
    unittest.main()
