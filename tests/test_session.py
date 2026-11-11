"""Tests for the session files — the sway config, the desktop entries.

These are shipped, installed and executed, and until this file existed nothing
ever read them. That is how ``titlebar_padding 0`` survived: sway rejects any
value below 1, so every session start printed *"Invalid size specified"* at
line 24, and the only thing that ever noticed was a person reading the error.

The line was not merely wrong, it was pointless — padding styles a titlebar,
and with ``default_border none`` there is never a titlebar to pad. A config
directive that configures nothing is the easiest kind of mistake to keep,
because it reads as deliberate.

Two kinds of check below. The static ones always run and encode what sway will
and will not accept. The validating one runs sway's own parser when sway is
installed and skips when it is not, which is the same shape as
``make bootcheck``: assert what can be asserted anywhere, and measure the real
thing where the real thing exists.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SWAY_CONF = ROOT / "session" / "sway.conf"
SESSION_SCRIPT = ROOT / "session" / "earth-os-session"


def directives():
    """Every non-comment, non-blank line, with its 1-based line number."""
    out = []
    for number, line in enumerate(SWAY_CONF.read_text().splitlines(), start=1):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append((number, stripped))
    return out


class TestSwayWillAcceptIt(unittest.TestCase):
    """What sway's parser refuses, checked without needing sway."""

    #: Directives sway requires to be >= 1. Zero is rejected with "Invalid
    #: size specified", which is not a warning — it is an error printed on
    #: every start of the session.
    AT_LEAST_ONE = ("titlebar_padding", "titlebar_border_thickness")

    def test_no_size_directive_is_set_to_zero(self):
        for number, line in directives():
            head = line.split()[0]
            if head not in self.AT_LEAST_ONE:
                continue
            for value in line.split()[1:]:
                if not value.lstrip("-").isdigit():
                    continue
                self.assertGreaterEqual(
                    int(value), 1,
                    f"{SWAY_CONF.name} line {number}: sway rejects "
                    f"{head} {value} — the minimum is 1")

    def test_titlebar_padding_is_absent_entirely(self):
        """Not "set to a legal value" — absent.

        With ``default_border none``, ``default_floating_border none`` and
        ``border none`` on every for_window rule, no window in this session
        has a titlebar. Padding one is configuring something that does not
        render, and the next person to read it will assume it matters.
        """
        for number, line in directives():
            self.assertFalse(
                line.startswith("titlebar_padding"),
                f"line {number}: nothing here has a titlebar to pad")

    def test_decorations_are_actually_switched_off(self):
        """The thing the deleted line was reaching for, done properly."""
        lines = {line for _number, line in directives()}
        for required in ("default_border none", "default_floating_border none"):
            self.assertIn(required, lines,
                          f"{required!r} is missing, so windows would be "
                          "drawn with decorations the LCARS chrome duplicates")

    def test_every_window_rule_switches_borders_off_too(self):
        """``default_border`` covers new windows; the for_window rules cover
        the ones matched by pattern. Both are needed, and it is the pair of
        them that makes titlebar padding meaningless."""
        rules = [line for _n, line in directives()
                 if line.startswith("for_window")]
        self.assertTrue(rules, "no for_window rules at all")
        for rule in rules:
            self.assertIn("border none", rule, f"{rule} leaves a border on")

    @unittest.skipIf(shutil.which("sway") is None, "sway is not installed")
    def test_sway_itself_accepts_the_config(self):
        """The real parser, when it is here. Nothing beats the actual thing.

        ``sway -C`` validates and exits, but it builds a backend first, so on
        a machine with no GPU it dies before reading a single line and reports
        success by never getting far enough to fail. ``WLR_BACKENDS=headless``
        is what makes it actually parse — without it this test passes on a
        config containing the very line it was written to catch, which is the
        most expensive kind of green.
        """
        complaints = self._validate(SWAY_CONF.read_text())
        self.assertEqual(complaints, [], "sway rejected the shipped config")

    @unittest.skipIf(shutil.which("sway") is None, "sway is not installed")
    def test_the_check_would_have_caught_the_old_line(self):
        """Proof the guard bites, by handing sway the defect on purpose.

        A validator that has never rejected anything is not known to work.
        """
        complaints = self._validate(
            "default_border none\ntitlebar_padding 0\n")
        self.assertTrue(
            any("titlebar_padding" in line for line in complaints),
            f"sway accepted the line it is documented to refuse: {complaints}")

    def _validate(self, text: str):
        """Run sway's own parser over *text* and return what it objected to."""
        runtime = Path(tempfile.mkdtemp())
        runtime.chmod(0o700)
        conf = runtime / "sway.conf"
        conf.write_text(text)
        done = subprocess.run(
            ["sway", "-C", "-c", str(conf)],
            capture_output=True, text=True, timeout=120,
            env={**os.environ,
                 "XDG_RUNTIME_DIR": str(runtime),
                 "WLR_BACKENDS": "headless",
                 "WLR_LIBINPUT_NO_DEVICES": "1"})
        return [line for line in (done.stdout + done.stderr).splitlines()
                if "sway/config.c" in line]


class TestTheFixCanReachAnExistingInstall(unittest.TestCase):
    """A fix to the template is worthless if nobody's copy is replaced.

    The session script copies the template to ``~/.config/earth-os/`` on
    first run and only replaces it afterwards when the template's version is
    higher. So a corrected config that keeps the old version number fixes the
    file in ``/usr/share`` and leaves every existing machine exactly as
    broken — which is the specific way this defect could have been "fixed"
    twice and still been reported.
    """

    def version_of(self, path: Path) -> int:
        match = re.search(r"^#\s*earth-os-config-version:\s*(\d+)",
                          path.read_text(), re.MULTILINE)
        self.assertIsNotNone(match, f"{path.name} has no version line")
        return int(match.group(1))

    def test_the_template_carries_a_version(self):
        self.assertGreaterEqual(self.version_of(SWAY_CONF), 1)

    def test_the_session_script_reads_that_exact_marker(self):
        """The script greps for the marker by name. If the two ever disagree
        the upgrade silently stops happening, and nothing else would notice."""
        script = SESSION_SCRIPT.read_text()
        self.assertIn("earth-os-config-version", script)
        self.assertIn(".bak", script,
                      "a replaced config should be kept, not discarded")


class TestTheSessionStaysEscapable(unittest.TestCase):
    """A kiosk session that cannot be left is a brick.

    Not related to the titlebar defect, but this is the file where these
    belong, and they were also untested: every one of these is the difference
    between a bad config and an unusable machine.
    """

    def test_there_is_a_way_back_to_the_shell(self):
        text = SWAY_CONF.read_text()
        self.assertIn("$mod+F1", text)

    def test_alt_tab_cycles_through_the_home_screen_and_programs(self):
        """Has to be a compositor binding: once a program has the keyboard,
        Alt+Tab goes to that program and the shell never sees it. Mod1 is
        Alt; Mod4 is Super, which is what ``$mod`` means here."""
        # sway ignores runs of spaces, and the config aligns its columns.
        lines = {" ".join(line.split()) for _n, line in directives()}
        self.assertIn("bindsym Mod1+Tab exec earth-os --switch", lines)
        self.assertIn("bindsym Mod1+Shift+Tab exec earth-os --switch-back", lines)

    def test_the_switch_flags_the_config_runs_are_ones_the_cli_accepts(self):
        """A binding to a flag that does not exist fails silently, on a key
        press, with no terminal to print the complaint to."""
        from earth_os.cli import build_parser
        flags = {opt for action in build_parser()._actions
                 for opt in action.option_strings}
        for _number, line in directives():
            if "exec earth-os --" in line:
                flag = line.split("exec earth-os ", 1)[1].split()[0]
                self.assertIn(flag, flags, f"{line!r} names an unknown flag")

    def test_there_is_a_terminal_binding(self):
        """Binding this to a terminal the system does not have leaves no way
        to fix anything from inside the session, so it tries several."""
        text = SWAY_CONF.read_text()
        self.assertIn("$mod+Return", text)
        self.assertIn("xterm", text, "no last-resort terminal in the chain")

    def test_there_is_a_way_out_of_the_session(self):
        self.assertIn("$mod+Shift+e", SWAY_CONF.read_text())

    def test_the_session_ends_when_the_shell_stops(self):
        """Without this, sway keeps running with no client and the screen is
        black with a cursor and no way out but a TTY switch."""
        self.assertIn("swaymsg exit", SWAY_CONF.read_text())


if __name__ == "__main__":
    unittest.main()
