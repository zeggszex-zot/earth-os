"""The exit menu is always in front, and a press anywhere else closes it.

EXIT offers LOG OUT, SHUT DOWN and REBOOT on a strip along the bottom of the
shell's own window. A program is a *separate* window, stacked by the
compositor in the order things were last focused, so the strip was at the
mercy of whichever program had been touched last:

* a framed program lay over it, because pressing EXIT focuses the shell and
  releasing the press lifted the program straight back;
* a loose program covered it wherever it happened to be;
* a fullscreen program covered the whole shell.

Nothing the shell draws can fix that — only putting its window in front can.
So while the menu is up it is modal: the shell comes forward, fullscreen is
released, nothing lifts a program back over it, and a press that is not on the
menu closes it and does nothing else.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os.config import Config                           # noqa: E402
from earth_os.core import Shell                              # noqa: E402
from earth_os.platform.capabilities import CapabilityResult  # noqa: E402
from earth_os.screens import ALL_SCREENS                     # noqa: E402
from earth_os.windows import Window, WindowBackend           # noqa: E402

from earth_os import chrome                                  # noqa: E402


def built(shell, name, w, h):
    screen = shell.screens[name]
    ctx = shell.context(w, h)
    screen.invalidate()
    screen.ensure_built(ctx)
    return screen, chrome.Frame(ctx), ctx


class RecordingBackend(WindowBackend):
    """A compositor that records what it was asked, in order."""

    name = "recording"

    def __init__(self, windows):
        self._windows = windows
        self.calls = []

    @property
    def available(self):
        return True

    @property
    def can_place(self):
        return True

    def list_windows(self):
        return list(self._windows)

    def find_by_app(self, app_ids):
        wanted = {a.lower() for a in app_ids}
        for w in self._windows:
            if (w.app or "").lower() in wanted:
                return w
        return None

    def focus(self, window):
        self.calls.append(("focus", window.app))
        return True

    def fullscreen(self, window, on=True):
        self.calls.append(("fullscreen", window.app, on))
        return True

    def place(self, window, x, y, w, h):
        self.calls.append(("place", window.app))
        return True


def make_shell(windows):
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    shell = Shell(config)
    shell.sound.enabled = False
    for screen_class in ALL_SCREENS:
        shell.register_screen(screen_class(shell))
    backend = RecordingBackend(windows)
    shell.windows.backend = backend
    shell.fired = []
    real = shell.capabilities.invoke

    def invoke(capability, /, **kwargs):
        if not capability.startswith("system."):
            return real(capability, **kwargs)
        shell.fired.append(capability)
        return CapabilityResult(ok=True, capability=capability)

    shell.capabilities.invoke = invoke
    shell.quit_hook = lambda: None
    return shell, backend


def shell_window():
    return Window(handle="1", title="EARTH OS", app="org.earthos.shell")


def program(handle="2", app="firefox", fullscreen=False):
    return Window(handle=handle, title=app, app=app, fullscreen=fullscreen)


def centre(element):
    return (element.x + element.w / 2, element.y + element.h / 2)


class ExitCase(unittest.TestCase):
    def setUp(self):
        self.window = program()
        self.shell, self.backend = make_shell([shell_window(), self.window])
        self.shell.framed_window = self.window
        self.shell.frame_rect = (200, 100, 1000, 600)

    def press_exit(self):
        screen, _, _ = built(self.shell, "main", 1600, 1000)
        screen.g.get("myExit").click()
        return screen


class TestTheMenuComesForward(ExitCase):
    def test_the_shell_is_focused_when_the_menu_opens(self):
        self.press_exit()
        self.assertTrue(self.shell.exit_menu_open)
        self.assertEqual(self.backend.calls[-1], ("focus", "org.earthos.shell"),
                         "the shell must be the last thing focused, or a "
                         "program is in front of the menu")

    def test_a_fullscreen_program_is_released_first(self):
        """A fullscreen program stays on top of whatever is focused next."""
        self.window.fullscreen = True
        self.press_exit()
        self.assertIn(("fullscreen", "firefox", False), self.backend.calls)
        self.assertLess(
            self.backend.calls.index(("fullscreen", "firefox", False)),
            self.backend.calls.index(("focus", "org.earthos.shell")))

    def test_a_windowed_program_is_left_as_it_was(self):
        self.press_exit()
        self.assertNotIn(("fullscreen", "firefox", False), self.backend.calls)

    def test_exit_from_another_screen_still_ends_with_the_shell_in_front(self):
        """Going to the main screen puts the framed program back in the
        frame, which focuses it. The shell has to be focused after that."""
        self.shell.go("engineering")
        screen, _, _ = built(self.shell, "engineering", 1600, 1000)
        self.backend.calls.clear()
        screen.g.get("myExit").click()
        self.assertEqual(self.shell.current, "main")
        self.assertEqual(self.backend.calls[-1], ("focus", "org.earthos.shell"))

    def test_the_confirm_step_is_in_front_too(self):
        """A spoken "shut down" asks for a press; the question is the same
        strip and must not be coverable either."""
        self.backend.calls.clear()
        self.shell.handle_utterance("shut down")
        self.assertTrue(self.shell.exit_menu_open)
        self.assertEqual(self.backend.calls[-1], ("focus", "org.earthos.shell"))


class TestNothingLiftsAProgramOverIt(ExitCase):
    def test_releasing_the_exit_press_does_not_lift_the_program(self):
        """The bug itself. Pressing EXIT focuses the shell; main's release
        handler then lifted the framed program straight back over the strip."""
        screen = self.press_exit()
        self.backend.calls.clear()
        screen.on_release(10, 10, None)
        self.assertNotIn(("focus", "firefox"), self.backend.calls)

    def test_without_the_menu_the_program_is_still_lifted(self):
        """The guard must not break what raise_framed is for."""
        screen, _, _ = built(self.shell, "main", 1600, 1000)
        self.backend.calls.clear()
        screen.on_release(10, 10, None)
        self.assertIn(("focus", "firefox"), self.backend.calls)

    def test_a_program_opening_does_not_take_the_front(self):
        self.press_exit()
        self.backend.calls.clear()
        newcomer = program(handle="3", app="gedit")
        self.backend._windows.append(newcomer)
        self.shell._seeded_windows = True
        self.shell._known_windows = {"2"}
        self.shell.poll_windows()
        self.assertNotIn(("focus", "gedit"), self.backend.calls)
        self.assertTrue(self.shell.exit_menu_open)


class TestPressingElsewhereClosesIt(ExitCase):
    def test_a_press_off_the_menu_closes_it_and_does_nothing_else(self):
        screen = self.press_exit()
        target = screen.g.get("myDocuments")
        x, y = centre(target)
        self.assertIs(screen.g.hit(x, y), target, "the press must land on it")
        self.assertIsNone(screen.on_press(x, y))
        self.assertFalse(self.shell.exit_menu_open)
        self.assertEqual(self.shell.pending_options, [])
        self.assertFalse(target.pressed, "the press that closed it also pressed")
        self.assertEqual(self.shell.fired, [])

    def test_pressing_exit_again_closes_it(self):
        screen = self.press_exit()
        button = screen.g.get("myExit")
        self.assertIs(screen.g.hit(*centre(button)), button)
        screen.on_press(*centre(button))
        self.assertFalse(self.shell.exit_menu_open)

    def test_a_press_on_a_menu_button_is_the_menu_being_used(self):
        screen = self.press_exit()
        screen, _, _ = built(self.shell, "main", 1600, 1000)
        button = screen.g.get("choice_2")
        pressed = screen.on_press(*centre(button))
        self.assertIs(pressed, button)
        self.assertTrue(self.shell.exit_menu_open)

    def test_a_missed_press_cannot_power_the_machine_off(self):
        screen = self.press_exit()
        screen.on_press(1, 1)
        screen.on_release(1, 1, None)
        self.assertEqual(self.shell.fired, [])

    def test_closing_it_brings_the_program_back(self):
        self.press_exit()
        self.backend.calls.clear()
        self.assertTrue(self.shell.dismiss_exit_menu())
        self.assertEqual(self.backend.calls[-1], ("focus", "firefox"))

    def test_dismissing_with_nothing_open_does_nothing(self):
        self.assertFalse(self.shell.dismiss_exit_menu())
        self.assertEqual(self.backend.calls, [])

    def test_closing_it_is_quiet(self):
        self.press_exit()
        before = len(self.shell.conversation)
        self.shell.dismiss_exit_menu()
        self.assertEqual(len(self.shell.conversation), before)


class TestLookingAway(ExitCase):
    def test_losing_focus_after_the_menu_settles_closes_it(self):
        self.press_exit()
        self.shell._exit_grace = 0.0
        self.shell.shell_lost_focus()
        self.assertFalse(self.shell.exit_menu_open)

    def test_the_focus_churn_as_it_opens_does_not_close_it(self):
        self.press_exit()
        self.shell._exit_grace = time.monotonic() + 5
        self.shell.shell_lost_focus()
        self.assertTrue(self.shell.exit_menu_open)

    def test_losing_focus_with_no_menu_changes_nothing(self):
        self.shell.shell_lost_focus()
        self.assertEqual(self.backend.calls, [])


class TestOnlyTheExitMenuIsModal(ExitCase):
    def test_an_ambiguity_question_is_not_the_exit_menu(self):
        self.shell.pending_prompt = "WHICH DID YOU MEAN?"
        self.shell.pending_options = [("A", lambda: None), ("B", lambda: None)]
        self.assertFalse(self.shell.exit_menu_open)
        screen, _, _ = built(self.shell, "main", 1600, 1000)
        target = screen.g.get("myDocuments")
        self.assertIs(screen.g.hit(*centre(target)), target)
        screen.on_press(*centre(target))
        self.assertTrue(self.shell.pending_options,
                        "a click away must not discard a question that is "
                        "not the exit menu")

    def test_an_ambiguity_question_after_the_menu_replaces_it(self):
        self.press_exit()
        self.shell.pending_options = [("A", lambda: None)]
        self.assertFalse(self.shell.exit_menu_open)

    def test_once_chosen_it_is_no_longer_open(self):
        self.press_exit()
        dict(self.shell.pending_options)["CANCEL"]()
        self.assertFalse(self.shell.exit_menu_open)


if __name__ == "__main__":
    unittest.main(verbosity=2)
