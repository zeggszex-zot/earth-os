"""Tests for the ported data formats and the shell core.

Run with::

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earth_os.commands import Vocabulary, normalise          # noqa: E402
from earth_os.config import Config                           # noqa: E402
from earth_os.core import Shell                              # noqa: E402
from earth_os.platform import CapabilityRegistry             # noqa: E402
from earth_os.screens import ALL_SCREENS                     # noqa: E402
from earth_os.sysinfo import (                               # noqa: E402
    SystemMonitor, human_bytes, human_duration,
)
from earth_os.theme import (                                  # noqa: E402
    PALETTE_SLOTS, Palette, Strings, parse_color,
)


def temp_config() -> Config:
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    return config


class TestPalette(unittest.TestCase):
    def test_all_shipped_palettes_load(self):
        names = Palette.list_builtin()
        self.assertIn("Standard Colors", names)
        self.assertIn("Nemesis", names)
        for name in names:
            palette = Palette.builtin(name)
            self.assertEqual(len(palette.colors), 11, name)
            for slot, rgb in palette.colors.items():
                self.assertEqual(len(rgb), 3)
                for channel in rgb:
                    self.assertGreaterEqual(channel, 0.0)
                    self.assertLessEqual(channel, 1.0)

    def test_standard_palette_values_match_the_original_file(self):
        palette = Palette.builtin("Standard Colors")
        self.assertAlmostEqual(palette["accent2"][0], 0x99 / 255, places=4)
        self.assertAlmostEqual(palette["accent2"][1], 0xCC / 255, places=4)
        self.assertAlmostEqual(palette["accent2"][2], 0xFF / 255, places=4)
        self.assertEqual(palette["alert"], (1.0, 0.0, 0.0))

    def test_monochrome_palette_is_actually_monochrome(self):
        """Every slot must be a pure grey, or the "black and white" claim is
        false somewhere no one will look until it is on screen."""
        palette = Palette.builtin("Monochrome")
        for slot in PALETTE_SLOTS:
            r, g, b = palette[slot]
            self.assertAlmostEqual(r, g, places=2, msg=slot)
            self.assertAlmostEqual(g, b, places=2, msg=slot)

    def test_monochrome_keeps_a_usable_contrast_range(self):
        """Greys must stay spread out; collapsing them loses the hierarchy
        that colour used to carry."""
        palette = Palette.builtin("Monochrome")
        levels = sorted(palette[s][0] for s in PALETTE_SLOTS)
        self.assertLess(levels[0], 0.35)
        self.assertGreater(levels[-1], 0.95)

    def test_named_dotnet_colours(self):
        # 'test colors.lxcp' uses .NET colour names rather than hex.
        palette = Palette.builtin("test colors")
        self.assertEqual(palette["text"], parse_color("Gray"))
        self.assertEqual(palette["alert"], (1.0, 0.0, 0.0))

    def test_overrides(self):
        palette = Palette.builtin("Standard Colors")
        palette.apply_overrides({"accent4": "#123456"})
        self.assertAlmostEqual(palette["accent4"][0], 0x12 / 255, places=4)


class TestStrings(unittest.TestCase):
    def test_shipped_language_files(self):
        self.assertEqual(sorted(Strings.list_builtin()),
                         ["Earth OS", "Standard", "TNG"])
        standard = Strings.builtin("Standard")
        self.assertEqual(standard["myComp"], "MY COMPUTER")
        tng = Strings.builtin("TNG")
        self.assertEqual(tng["myComp"], "COMPUTER CORE")
        self.assertEqual(tng["myStartMenu"], "PROGRAM INDEX")

    def test_alias_bridges_the_two_files(self):
        # Standard.lng writes ShowTrayButton, TNG.lng writes myShowTrayButton.
        self.assertEqual(Strings.builtin("TNG")["ShowTrayButton"], "ENABLE ICONS")
        self.assertEqual(Strings.builtin("Standard")["myShowTrayButton"],
                         "SHOW TRAY ICONS")

    def test_unknown_key_falls_back(self):
        self.assertEqual(Strings.builtin("Standard")["myNonsense"], "MYNONSENSE")


class TestVocabulary(unittest.TestCase):
    def setUp(self):
        self.vocab = Vocabulary.load()

    def test_loads_original_command_file(self):
        self.assertGreater(len(self.vocab.phrases), 25)
        self.assertEqual(self.vocab.wake_words, ["computer"])

    def test_comments_are_stripped(self):
        for spoken, action in self.vocab.phrases.items():
            self.assertNotIn("//", spoken)
            self.assertNotIn("//", action)

    def test_aliases_resolve_to_the_same_action(self):
        self.assertEqual(self.vocab.phrases["settings"], "specify parameters")
        self.assertEqual(self.vocab.phrases["end continuous commands"],
                         "end commands")

    def test_wake_word_stripping(self):
        self.assertEqual(self.vocab.strip_wake("Computer, my documents"),
                         (True, "my documents"))
        self.assertEqual(self.vocab.strip_wake("computer"), (True, ""))
        self.assertEqual(self.vocab.strip_wake("my documents"),
                         (False, "my documents"))

    def test_matching(self):
        self.assertEqual(self.vocab.match("terminal"), "terminal")
        self.assertEqual(self.vocab.match("please open my documents now"),
                         "my documents")
        self.assertEqual(self.vocab.match("settings"), "specify parameters")
        self.assertIsNone(self.vocab.match("what is the weather in paris"))

    def test_normalise(self):
        self.assertEqual(normalise("  Computer,   MY   Documents! "),
                         "computer my documents")

    def test_trek_furniture_is_gone(self):
        """The Star Trek commands were removed; they must not creep back."""
        for phrase in ("red alert", "cancel red alert", "self destruct",
                       "mode select"):
            self.assertIsNone(self.vocab.phrases.get(phrase), phrase)


class TestSysinfo(unittest.TestCase):
    def test_readings_are_sane(self):
        monitor = SystemMonitor()
        self.assertGreaterEqual(monitor.cpu(), 0.0)
        self.assertLessEqual(monitor.cpu(), 1.0)
        frac, used, total = monitor.memory()
        self.assertGreater(total, 0)
        self.assertLessEqual(used, total)
        self.assertGreater(monitor.uptime(), 0.0)
        self.assertGreater(monitor.processes(), 0)
        self.assertTrue(monitor.hostname())

    def test_formatting(self):
        self.assertEqual(human_bytes(512), "512B")
        self.assertEqual(human_bytes(1536), "1.5K")
        self.assertEqual(human_duration(3661), "01:01")
        self.assertEqual(human_duration(90061), "1d 01:01")


class TestShell(unittest.TestCase):
    def setUp(self):
        self.shell = Shell(temp_config())
        for screen_class in ALL_SCREENS:
            self.shell.register_screen(screen_class(self.shell))

    def test_every_vocabulary_action_has_a_handler(self):
        self.assertEqual(self.shell.dispatcher.unhandled(), [])

    def test_navigation(self):
        self.shell.go("engineering")
        self.assertEqual(self.shell.current, "engineering")
        self.shell.go_back()
        self.assertEqual(self.shell.current, "main")

    def test_operator_name_is_never_blank_on_a_real_account(self):
        self.assertIsInstance(self.shell.operator_name(), str)

    def test_exit_command_parses(self):
        self.assertEqual(self.shell._exit_command(), ["startx"])

    def test_voice_command_runs_without_a_model(self):
        self.shell.handle_utterance("terminal")
        self.assertEqual(self.shell.current, "terminal")
        roles = [entry["role"] for entry in self.shell.conversation]
        self.assertIn("user", roles)
        self.assertIn("computer", roles)

    def test_unknown_utterance_is_refused_not_crashed(self):
        self.shell.handle_utterance("what is the airspeed velocity of a swallow")
        last = self.shell.conversation[-1]
        self.assertEqual(last["role"], "computer")
        self.assertTrue(last["text"])

    def test_telemetry_summary_has_real_values(self):
        summary = self.shell.telemetry_summary()
        self.assertIn("host=", summary)
        self.assertIn("memory=", summary)
        self.assertIn("uptime=", summary)


class TestRendering(unittest.TestCase):
    """Every screen must lay out and paint at a range of sizes."""

    SIZES = ((1024, 600), (1280, 800), (1920, 1080), (3840, 2160))

    def test_all_screens_render_at_all_sizes(self):
        import cairo

        shell = Shell(temp_config())
        for screen_class in ALL_SCREENS:
            shell.register_screen(screen_class(shell))
        shell.say("Test message.", speak=False)

        for width, height in self.SIZES:
            for name, screen in shell.screens.items():
                shell.current = name
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
                cr = cairo.Context(surface)
                ctx = shell.context(width, height)
                screen.invalidate()
                with self.subTest(screen=name, size=f"{width}x{height}"):
                    screen.draw(cr, ctx)

    def test_hit_testing_finds_the_left_column(self):
        import cairo

        shell = Shell(temp_config())
        for screen_class in ALL_SCREENS:
            shell.register_screen(screen_class(shell))
        screen = shell.screens["main"]
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1600, 1000)
        cr = cairo.Context(surface)
        ctx = shell.context(1600, 1000)
        screen.draw(cr, ctx)
        button = screen.g.get("myEngineering")
        self.assertIsNotNone(button)
        hit = screen.hit(button.x + button.w / 2, button.y + button.h / 2)
        self.assertIs(hit, button)


class TestShapes(unittest.TestCase):
    def test_elbow_hit_test(self):
        from earth_os.widgets.shapes import elbow_hit

        # 'tl': vertical arm down the left, horizontal arm across the top.
        self.assertTrue(elbow_hit(5, 50, 0, 0, 100, 100, 20, 15, "tl"))    # arm
        self.assertTrue(elbow_hit(50, 5, 0, 0, 100, 100, 20, 15, "tl"))    # arm
        self.assertFalse(elbow_hit(60, 60, 0, 0, 100, 100, 20, 15, "tl"))  # hole
        self.assertFalse(elbow_hit(150, 50, 0, 0, 100, 100, 20, 15, "tl"))

    def test_pill_path_is_closed_and_bounded(self):
        import cairo

        from earth_os.widgets.shapes import pill

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 100)
        cr = cairo.Context(surface)
        for ends in ("both", "left", "right", "none"):
            cr.new_path()
            pill(cr, 10, 10, 120, 40, ends=ends)
            x1, y1, x2, y2 = cr.path_extents()
            self.assertGreaterEqual(x1, 9.0, ends)
            self.assertLessEqual(x2, 131.0, ends)
            self.assertGreaterEqual(y1, 9.0, ends)
            self.assertLessEqual(y2, 51.0, ends)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestDiagnostics(unittest.TestCase):
    """The doctor must never be the thing that crashes."""

    def test_every_check_returns_a_status(self):
        from earth_os import diagnostics

        for name, fn in diagnostics.CHECKS:
            with self.subTest(check=name):
                status, detail = fn()
                self.assertIn(status, (diagnostics.OK, diagnostics.WARN,
                                       diagnostics.FAIL))
                self.assertIsInstance(detail, str)

    def test_report_survives_a_check_that_raises(self):
        from earth_os import diagnostics

        report = diagnostics.Report()
        diagnostics._safe(report, "boom", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        self.assertEqual(report.failures, 1)
        self.assertIn("boom", report.render())

    def test_openers_check_needs_no_toolkit(self):
        """The doctor is run on the machine where GTK is broken.

        ``check_openers`` used to read the list off ``Shell``, which drags in
        Pango through the widgets — so on a machine with a broken PyGObject the
        diagnosis itself raised ImportError instead of reporting the problem.
        """
        import subprocess
        import sys

        code = (
            "import sys\n"
            "class Blocked:\n"
            "    def find_module(self, name, path=None):\n"
            "        if name == 'gi' or name.startswith('gi.'):\n"
            "            raise ImportError('no gi')\n"
            "        return None\n"
            "sys.meta_path.insert(0, Blocked())\n"
            "from earth_os.diagnostics import check_openers\n"
            "status, detail = check_openers()\n"
            "assert isinstance(detail, str), detail\n"
            "print('ok')\n"
        )
        result = subprocess.run([sys.executable, "-c", code],
                                cwd=str(Path(__file__).resolve().parent.parent),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_dbus_check_is_present(self):
        """The missing session bus is why nothing D-Bus activated will start."""
        from earth_os import diagnostics

        names = [name for name, _ in diagnostics.CHECKS]
        self.assertIn("session bus", names)

        saved = os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
        try:
            status, detail = diagnostics.check_dbus()
            self.assertEqual(status, diagnostics.WARN)
            self.assertIn("dbus-run-session", detail)
        finally:
            if saved is not None:
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = saved

    def test_no_display_is_reported_as_blocking(self):
        from earth_os import diagnostics

        saved = {k: os.environ.pop(k, None) for k in ("DISPLAY", "WAYLAND_DISPLAY")}
        try:
            status, detail = diagnostics.check_display()
            self.assertEqual(status, diagnostics.FAIL)
            window_status, _ = diagnostics.check_can_open_a_window()
            self.assertEqual(window_status, diagnostics.FAIL)
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_cli_parses_without_importing_gtk(self):
        from earth_os.cli import build_parser

        args = build_parser().parse_args(["--doctor"])
        self.assertTrue(args.doctor)
        args = build_parser().parse_args(["--windowed", "--palette", "Nemesis"])
        self.assertTrue(args.windowed)
        self.assertEqual(args.palette, "Nemesis")


class TestDeactivateSafety(unittest.TestCase):
    """DEACTIVATE ends the session, so it must never fire on one press."""

    def setUp(self):
        self.shell = Shell(temp_config())
        for screen_class in ALL_SCREENS:
            self.shell.register_screen(screen_class(self.shell))
        self.quit_calls = []
        self.shell.quit_hook = lambda: self.quit_calls.append(True)

    def test_first_press_only_asks(self):
        self.shell.deactivate()
        self.assertEqual(self.quit_calls, [])
        self.assertTrue(self.shell.pending_options)
        labels = [label for label, _ in self.shell.pending_options]
        self.assertIn("CANCEL", labels)

    def test_confirming_hands_over_and_quits(self):
        """Confirming starts the handover program, then lets go of the display.

        The order matters: LCARS is normally holding the screen, so the
        replacement cannot come up until this process has quit.
        """
        started = []

        def fake_launch(argv, **kwargs):
            started.append((argv, kwargs))
            return unittest.mock.Mock(pid=1234)

        with unittest.mock.patch("earth_os.core.shutil.which",
                                 return_value="/usr/bin/startx"), \
             unittest.mock.patch.object(type(self.shell.platform.process),
                                        "launch", side_effect=fake_launch):
            self.shell.capabilities = CapabilityRegistry(self.shell.platform)
            self.shell.deactivate()
            confirm = self.shell.pending_options[0][1]
            confirm()

        self.assertEqual(len(started), 1)
        argv, kwargs = started[0]
        self.assertEqual(argv, ["startx"])
        # Nothing may be captured from the handover: this process is about to
        # exit, and a program writing into a dead process's pipe gets a broken
        # pipe, which would kill the thing being handed to.
        self.assertFalse(kwargs.get("capture_stderr", True))
        self.assertEqual(self.quit_calls, [True])

    def test_missing_exit_command_does_not_strand_the_user(self):
        """If the handover program is not installed, staying put beats
        quitting into a bare tty with nothing on it."""
        with unittest.mock.patch("earth_os.core.shutil.which", return_value=None):
            self.shell.deactivate()
            self.shell.pending_options[0][1]()
        self.assertEqual(self.quit_calls, [])
        self.assertIn("startx", self.shell.status_message)

    def test_cancelling_does_not_quit(self):
        self.shell.deactivate()
        self.shell.cancel_pending()
        self.assertEqual(self.quit_calls, [])
        self.assertEqual(self.shell.pending_options, [])

    def test_prompt_names_what_it_hands_over_to(self):
        """The prompt has to say where the machine is about to go, because
        DEACTIVATE closes everything the user has open to get there."""
        with unittest.mock.patch.dict(
                os.environ, {"XDG_SESSION_DESKTOP": "earth-os"}, clear=False):
            self.shell.deactivate()
        self.assertIn("STARTX", self.shell.pending_prompt.upper())

    def test_voice_deactivate_also_confirms(self):
        self.shell.run_action("deactivate")
        self.assertEqual(self.quit_calls, [])
        self.assertTrue(self.shell.pending_options)
