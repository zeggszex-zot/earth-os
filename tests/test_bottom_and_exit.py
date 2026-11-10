"""No bottom bar, the clock block, and EXIT's three ways out.

Three requests, each with the way it could quietly come undone:

* The bottom of the frame — an elbow, a run of unlabelled bars and a clock
  pill — is gone, and the side column and content run to the foot of the
  screen. Checked by measuring, so a band cannot creep back.
* The clock is a rectangle the width of the rail, from under the voice state
  to the bottom of the screen, and it shows the time.
* EXIT offers LOG OUT, SHUT DOWN and REBOOT. Pressing EXIT changes nothing;
  pressing one of the three is what reaches the platform.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os import chrome                                  # noqa: E402
from earth_os.config import Config                           # noqa: E402
from earth_os.core import Shell                              # noqa: E402
from earth_os.platform.capabilities import CapabilityResult  # noqa: E402
from earth_os.screens import ALL_SCREENS                     # noqa: E402
from earth_os.screens.main import COLUMN                     # noqa: E402

SIZES = ((1920, 1080), (1600, 1000), (1280, 720), (1024, 600))


def make_shell() -> Shell:
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    shell = Shell(config)
    shell.sound.enabled = False
    for screen_class in ALL_SCREENS:
        shell.register_screen(screen_class(shell))
    return shell


def built(shell: Shell, name: str, w: int, h: int):
    screen = shell.screens[name]
    ctx = shell.context(w, h)
    screen.invalidate()
    screen.ensure_built(ctx)
    return screen, chrome.Frame(ctx), ctx


class TestNothingAlongTheBottom(unittest.TestCase):

    def test_the_chrome_has_no_bottom_pieces(self):
        ctx = make_shell().context(1600, 1000)
        names = {p.name for p in chrome.pieces(ctx, chrome.Frame(ctx))}
        for removed in ("chrome_bl", "chrome_strip", "chrome_clock"):
            self.assertNotIn(removed, names)

    def test_content_reaches_the_foot_of_the_screen(self):
        for w, h in SIZES:
            f = chrome.Frame(make_shell().context(w, h))
            _, cy, _, ch = f.content
            self.assertAlmostEqual(cy + ch, h - f.margin, places=3)

    def test_the_menu_runs_to_the_bottom(self):
        shell = make_shell()
        for w, h in SIZES:
            screen, f, _ = built(shell, "main", w, h)
            last = screen.g.get(COLUMN[-1][0])
            self.assertAlmostEqual(last.y + last.h, f.bottom, places=3,
                                   msg=f"menu stops short at {w}x{h}")

    def test_the_centre_runs_to_the_bottom(self):
        shell = make_shell()
        for w, h in SIZES:
            screen, f, _ = built(shell, "main", w, h)
            panel = screen.g.get("transcript")
            self.assertAlmostEqual(panel.y + panel.h, f.bottom, places=3)

    def test_other_screens_keep_back_at_the_foot(self):
        shell = make_shell()
        screen, f, _ = built(shell, "programs", 1920, 1080)
        back = screen.g.get("chrome_back")
        self.assertAlmostEqual(back.y + back.h, f.bottom, places=3)


class TestTheClockBlock(unittest.TestCase):

    def test_it_fills_the_rail_below_the_voice_state(self):
        shell = make_shell()
        for w, h in SIZES:
            screen, f, _ = built(shell, "main", w, h)
            clock = screen.g.get("chrome_clock")
            state = screen.g.get("status_state")
            listen = screen.g.get("listen_toggle")
            self.assertIsNotNone(clock)
            # As wide as the rail, flush with its edges.
            self.assertAlmostEqual(clock.x, listen.x, places=3)
            self.assertAlmostEqual(clock.w, listen.w, places=3)
            # Below VOICE OFFLINE, down to the bottom of the screen.
            self.assertGreaterEqual(clock.y, state.y + state.h - 0.01,
                                    f"clock overlaps the voice state at {w}x{h}")
            self.assertAlmostEqual(clock.y + clock.h, f.bottom, places=3)
            # Bigger than the old pill, which was one bar high.
            self.assertGreater(clock.h, f.bar_h * 2)

    def test_it_shows_the_time(self):
        import cairo
        shell = make_shell()
        screen, _, ctx = built(shell, "main", 1920, 1080)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1920, 1080)
        screen.draw(cairo.Context(surface), ctx)
        label = screen.g.get("chrome_clock").label
        self.assertRegex(label, r"\n\d\d:\d\d:\d\d$")


class TestExit(unittest.TestCase):

    def setUp(self):
        self.shell = make_shell()
        self.fired = []
        real = self.shell.capabilities.invoke

        def invoke(capability, /, **kwargs):
            if not capability.startswith("system."):
                return real(capability, **kwargs)
            self.fired.append(capability)
            return CapabilityResult(ok=True, capability=capability)

        self.shell.capabilities.invoke = invoke
        self.quits = []
        self.shell.quit_hook = lambda: self.quits.append(True)

    def _press_exit(self):
        screen, _, _ = built(self.shell, "main", 1600, 1000)
        screen.g.get("myExit").click()

    def test_the_column_button_says_exit(self):
        screen, _, _ = built(self.shell, "main", 1600, 1000)
        self.assertEqual(screen.g.get("myExit").label, "EXIT")

    def test_pressing_exit_only_offers(self):
        self._press_exit()
        labels = [label for label, _ in self.shell.pending_options]
        self.assertEqual(labels, ["LOG OUT", "SHUT DOWN", "REBOOT", "CANCEL"])
        self.assertEqual(self.fired, [])
        self.assertEqual(self.quits, [])

    def test_each_choice_runs_its_capability(self):
        for label, capability in (("LOG OUT", "system.logout"),
                                  ("SHUT DOWN", "system.shutdown"),
                                  ("REBOOT", "system.reboot")):
            self.fired.clear()
            self._press_exit()
            dict(self.shell.pending_options)[label]()
            self.assertEqual(self.fired, [capability])
            self.assertEqual(self.shell.pending_options, [])

    def test_cancel_runs_nothing(self):
        self._press_exit()
        dict(self.shell.pending_options)["CANCEL"]()
        self.assertEqual(self.fired, [])
        self.assertEqual(self.shell.pending_options, [])

    def test_the_choices_appear_on_the_main_screen(self):
        self._press_exit()
        screen, _, _ = built(self.shell, "main", 1600, 1000)
        names = {e.name for e in screen.g}
        self.assertIn("choice_0", names)

    def test_exit_from_diagnostics_goes_where_the_choices_are(self):
        self.shell.go("engineering")
        screen, _, _ = built(self.shell, "engineering", 1600, 1000)
        screen.g.get("myExit").click()
        self.assertEqual(self.shell.current, "main")
        self.assertTrue(self.shell.pending_options)

    def test_a_spoken_power_word_still_asks_while_exit_is_up(self):
        """With EXIT's choices showing, "shut down" must not skip its press."""
        self._press_exit()
        self.shell.handle_utterance("shut down")
        self.assertEqual(self.fired, [])
        self.assertIn("CONFIRM", dict(self.shell.pending_options))


if __name__ == "__main__":
    unittest.main(verbosity=2)
