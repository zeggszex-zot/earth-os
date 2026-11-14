"""The upper-left box, its volume spot, and the column DIAGNOSTICS keeps.

Three requests, each with the way it could quietly come undone:

* The box is a *square* of four spots. It used to be whatever height the
  title band left it; the test measures it so it cannot drift back.
* One spot is system volume, opening a slider. The slider must reach the
  machine through ``audio.set_volume`` — the same capability "computer,
  louder" uses — and must say so when it cannot, rather than move a track
  that changes nothing.
* DIAGNOSTICS shows the home screen's side column, not its own. "The same"
  is checked button by button, position by position.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os import chrome                                  # noqa: E402
from earth_os.config import Config                           # noqa: E402
from earth_os.core import Shell                              # noqa: E402
from earth_os.platform import PlatformError                  # noqa: E402
from earth_os.platform.capabilities import CapabilityResult  # noqa: E402
from earth_os.platform.linux_adapter import LinuxAudio       # noqa: E402
from earth_os.screens import ALL_SCREENS                     # noqa: E402
from earth_os.screens.main import COLUMN                     # noqa: E402

SIZES = ((1600, 1000), (1280, 720), (1024, 600), (1920, 1080))


def make_shell() -> Shell:
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    shell = Shell(config)
    for screen_class in ALL_SCREENS:
        shell.register_screen(screen_class(shell))
    return shell


class FakeVolume:
    """A machine whose volume can be read and set, and remembers the calls."""

    def __init__(self, level=0.4, fails=""):
        self.level = level
        self.fails = fails
        self.sets = []

    def install(self, shell: Shell) -> None:
        real = shell.capabilities

        def invoke(capability, /, **kwargs):
            if not capability.startswith("audio."):
                return real.invoke(capability, **kwargs)
            if self.fails:
                return CapabilityResult(ok=False, capability=capability,
                                        error=self.fails)
            if capability == "audio.get_volume":
                return CapabilityResult(ok=True, capability=capability,
                                        value=self.level)
            if capability == "audio.set_volume":
                self.sets.append(kwargs["level"])
                self.level = kwargs["level"]
                return CapabilityResult(ok=True, capability=capability,
                                        value=True)
            raise AssertionError(f"unexpected capability {capability}")
        shell.capabilities = mock.Mock(invoke=mock.Mock(side_effect=invoke))


class TestTheBoxIsASquareOfFourSpots(unittest.TestCase):

    def test_square_at_every_size(self):
        shell = make_shell()
        for w, h in SIZES:
            f = chrome.Frame(shell.context(w, h))
            x, y, bw, bh = f.box
            self.assertAlmostEqual(bw, bh, msg=f"box not square at {w}x{h}")
            self.assertAlmostEqual(bw, f.col_w, msg="box is the column's width")

    def test_four_equal_spots_inside_it(self):
        f = chrome.Frame(make_shell().context(1600, 1000))
        bx, by, side, _ = f.box
        spots = f.spots
        self.assertEqual(len(spots), 4)
        for sx, sy, sw, sh in spots:
            self.assertAlmostEqual(sw, sh)
            self.assertAlmostEqual(sw, spots[0][2])
            self.assertGreaterEqual(sx, bx - 0.01)
            self.assertGreaterEqual(sy, by - 0.01)
            self.assertLessEqual(sx + sw, bx + side + 0.01)
            self.assertLessEqual(sy + sh, by + side + 0.01)

    def test_the_column_starts_below_the_box(self):
        shell = make_shell()
        for w, h in SIZES:
            ctx = shell.context(w, h)
            f = chrome.Frame(ctx)
            screen = shell.screens["main"]
            screen.invalidate()
            screen.ensure_built(ctx)
            first = screen.g.get(COLUMN[0][0])
            self.assertGreater(first.y, f.box[1] + f.box[3],
                               f"column overlaps the box at {w}x{h}")

    def test_the_splash_draws_the_spots_too(self):
        """The box is chrome, so it is there before the shell exists."""
        ctx = make_shell().context(1600, 1000)
        names = {p.name for p in chrome.pieces(ctx, chrome.Frame(ctx))}
        for name in ("chrome_spot_0", "chrome_tl", "chrome_spot_2",
                     "chrome_spot_3"):
            self.assertIn(name, names)


class TestVolumeSpot(unittest.TestCase):

    def setUp(self):
        self.shell = make_shell()
        self.machine = FakeVolume(level=0.4)
        self.machine.install(self.shell)
        self.ctx = self.shell.context(1600, 1000)

    def _screen(self, name="main"):
        self.shell.go(name) if name != self.shell.current else None
        screen = self.shell.screens[name]
        screen.ensure_built(self.ctx)
        return screen

    def _press(self, screen, element, at=None):
        x, y, w, h = element.rect
        px, py = at if at is not None else (x + w / 2, y + h / 2)
        pressed = screen.on_press(px, py)
        screen.on_release(px, py, pressed)
        screen.ensure_built(self.ctx)

    def test_every_screen_has_it_on_the_first_spot(self):
        f = chrome.Frame(self.ctx)
        for name, screen in self.shell.screens.items():
            screen.invalidate()
            screen.ensure_built(self.ctx)
            spot = screen.g.get("volume_spot")
            self.assertIsNotNone(spot, f"no volume spot on {name}")
            self.assertEqual(spot.rect, f.spots[0])

    def test_it_shows_the_machine_level(self):
        self._screen()
        self.assertEqual(self.shell.volume.label(), "40%")

    def test_pressing_it_opens_the_slider_and_again_closes_it(self):
        screen = self._screen()
        self._press(screen, screen.g.get("volume_spot"))
        self.assertTrue(self.shell.volume.panel_open)
        self.assertIsNotNone(screen.g.get("volume_slider"))
        self.assertIsNone(screen.g.get("strip_empty"),
                          "the slider takes the strip's band while open")
        self._press(screen, screen.g.get("volume_spot"))
        self.assertFalse(self.shell.volume.panel_open)
        self.assertIsNone(screen.g.get("volume_slider"))

    def test_dragging_sets_the_system_volume(self):
        screen = self._screen("engineering")
        self._press(screen, screen.g.get("volume_spot"))
        slider = screen.g.get("volume_slider")
        x, y, w, h = slider.rect
        start, width = slider._track()
        pressed = screen.on_press(start + width * 0.2, y + h / 2)
        self.assertIs(pressed, slider)
        screen.on_motion(start + width * 0.5, y + h / 2)
        screen.on_motion(start + width * 0.8, y + h / 2)
        screen.on_release(start + width * 0.8, y + h / 2, pressed)
        self.assertAlmostEqual(self.machine.sets[-1], 0.8, places=2)
        self.assertAlmostEqual(self.shell.volume.level, 0.8, places=2)
        self.assertTrue(self.shell.volume.panel_open,
                        "using the slider does not close it")

    def test_steps_move_five_percent(self):
        screen = self._screen()
        self._press(screen, screen.g.get("volume_spot"))
        self._press(screen, screen.g.get("volume_up"))
        self.assertAlmostEqual(self.machine.sets[-1], 0.45)
        self._press(screen, screen.g.get("volume_down"))
        self._press(screen, screen.g.get("volume_down"))
        self.assertAlmostEqual(self.machine.sets[-1], 0.35)

    def test_a_press_elsewhere_only_closes_it(self):
        screen = self._screen()
        self._press(screen, screen.g.get("volume_spot"))
        files = screen.g.get("myComp")
        self._press(screen, files)
        self.assertFalse(self.shell.volume.panel_open)
        self.assertEqual(self.shell.current, "main",
                         "the press that closed the slider pressed nothing else")

    def test_when_it_cannot_be_read_it_says_why(self):
        shell = make_shell()
        FakeVolume(fails="no volume control: install pulseaudio-utils").install(shell)
        screen = shell.screens["main"]
        screen.ensure_built(shell.context(1600, 1000))
        self.assertIsNone(shell.volume.level, "no level is made up")
        self.assertEqual(shell.volume.label(), "--")
        self.assertIn("pulseaudio-utils", shell.volume.error)

    def test_a_failed_set_is_reported(self):
        screen = self._screen()
        self.machine.fails = "pactl: Connection failure"
        self.shell.volume.set(0.9)
        self.assertIn("Connection failure", self.shell.volume.error)
        self.assertIn("Connection failure", self.shell.toast_text)

    def test_a_drag_is_coalesced(self):
        """A worker busy applying one level applies only the newest next."""
        volume = self.shell.volume
        volume._busy = True               # as if a set were in flight
        for level in (0.1, 0.2, 0.3, 0.7):
            volume.set(level)
        self.assertEqual(self.machine.sets, [], "queued, not applied inline")
        volume._apply(self.shell.capabilities)
        self.assertEqual(self.machine.sets, [0.7])


class TestDiagnosticsKeepsTheHomeColumn(unittest.TestCase):

    def test_same_buttons_same_places(self):
        shell = make_shell()
        for w, h in SIZES:
            ctx = shell.context(w, h)
            home = shell.screens["main"]
            diag = shell.screens["engineering"]
            for screen in (home, diag):
                screen.invalidate()
                screen.ensure_built(ctx)
            for key, _action, _colour in COLUMN:
                a, b = home.g.get(key), diag.g.get(key)
                self.assertIsNotNone(b, f"{key} missing from DIAGNOSTICS")
                self.assertEqual(a.rect, b.rect, f"{key} moved at {w}x{h}")
                self.assertEqual(a.label, b.label)

    def test_no_back_and_home_pills_instead(self):
        shell = make_shell()
        diag = shell.screens["engineering"]
        diag.ensure_built(shell.context(1600, 1000))
        self.assertIsNone(diag.g.get("chrome_back"))
        self.assertIsNone(diag.g.get("chrome_home"))

    def test_its_own_entry_leads_home_and_the_others_go_on(self):
        shell = make_shell()
        shell.go("engineering")
        diag = shell.screens["engineering"]
        diag.ensure_built(shell.context(1600, 1000))
        diag.g.get("myEngineering").click()
        self.assertEqual(shell.current, "main")
        shell.go("engineering")
        diag.g.get("mySettings").click()
        self.assertEqual(shell.current, "settings")


class TestLinuxVolumeIsHonest(unittest.TestCase):
    """The adapter used to answer 0.5 for a volume it never read."""

    def test_no_tool_raises_rather_than_guessing(self):
        with mock.patch("earth_os.platform.linux_adapter.shutil.which",
                        return_value=None):
            with self.assertRaises(PlatformError):
                LinuxAudio().get_volume()
            with self.assertRaises(PlatformError):
                LinuxAudio().set_volume(0.5)

    def _only(self, tool):
        return mock.patch("earth_os.platform.linux_adapter.shutil.which",
                          side_effect=lambda n: f"/usr/bin/{n}" if n == tool else None)

    def test_amixer_brackets_are_read(self):
        output = ("Simple mixer control 'Master',0\n"
                  "  Mono: Playback 41 [64%] [-17.25dB] [on]\n")
        with self._only("amixer"), mock.patch(
                "earth_os.platform.linux_adapter.subprocess.check_output",
                return_value=output):
            self.assertAlmostEqual(LinuxAudio().get_volume(), 0.64)

    def test_wpctl_fraction_is_read(self):
        with self._only("wpctl"), mock.patch(
                "earth_os.platform.linux_adapter.subprocess.check_output",
                return_value="Volume: 0.37 [MUTED]\n"):
            self.assertAlmostEqual(LinuxAudio().get_volume(), 0.37)

    def test_pactl_percent_is_read(self):
        output = ("Volume: front-left: 52428 /  80% / -5.81 dB,   "
                  "front-right: 52428 /  80% / -5.81 dB\n")
        with self._only("pactl"), mock.patch(
                "earth_os.platform.linux_adapter.subprocess.check_output",
                return_value=output):
            self.assertAlmostEqual(LinuxAudio().get_volume(), 0.80)

    def test_a_failed_set_raises_with_the_reason(self):
        error = subprocess.CalledProcessError(
            1, ["pactl"], stderr=b"Connection failure: Connection refused\n")
        with self._only("pactl"), mock.patch(
                "earth_os.platform.linux_adapter.subprocess.run",
                side_effect=error):
            with self.assertRaises(PlatformError) as caught:
                LinuxAudio().set_volume(0.3)
        self.assertIn("Connection refused", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
