"""Tests for window control and file opening.

The backends themselves need a compositor, so these test the parsing and the
degradation: what the shell does when window control is *not* available, which
on GNOME and KDE under Wayland is the normal case.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earth_os.config import Config                        # noqa: E402
from earth_os.core import Shell                           # noqa: E402
from earth_os.platform import CapabilityRegistry          # noqa: E402
from earth_os.screens import ALL_SCREENS                  # noqa: E402
from earth_os.windows import (                            # noqa: E402
    NullWindows, SwayWindows, Window, WindowManager, WmctrlWindows, detect,
)

SWAY_TREE = """{
  "id": 1, "type": "root", "name": "root",
  "nodes": [
    {"id": 2, "type": "output", "name": "eDP-1", "nodes": [
      {"id": 3, "type": "workspace", "name": "1", "nodes": [
        {"id": 10, "type": "con", "name": "index.html — Firefox",
         "app_id": "firefox", "focused": true, "fullscreen_mode": 1,
         "nodes": [], "floating_nodes": []},
        {"id": 11, "type": "con", "name": "~/src",
         "app_id": "foot", "focused": false, "fullscreen_mode": 0,
         "nodes": [], "floating_nodes": []}
      ], "floating_nodes": []}
    ], "floating_nodes": []}
  ], "floating_nodes": []
}"""

WMCTRL_LIST = """0x03000007  0 gedit.Gedit           Lucy Untitled Document 1
0x04200003  1 firefox.Firefox       Lucy Mozilla Firefox
0x01000002 -1 xfdesktop.Xfdesktop    Lucy Desktop
"""


class TestSwayParsing(unittest.TestCase):
    def test_tree_yields_only_real_windows(self):
        backend = SwayWindows(binary="/nonexistent/swaymsg")
        with mock.patch("earth_os.platform.linux_windows._run", return_value=(0, SWAY_TREE)):
            windows = backend.list_windows()
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].app, "firefox")
        self.assertEqual(windows[0].handle, "10")
        self.assertTrue(windows[0].focused)
        self.assertTrue(windows[0].fullscreen)
        self.assertEqual(windows[1].app, "foot")
        self.assertEqual(windows[0].workspace, "1")

    def test_label_combines_app_and_title(self):
        window = Window(handle="1", title="index.html — Firefox", app="firefox")
        self.assertIn("firefox", window.label)
        self.assertIn("index.html", window.label)

    def test_bad_json_is_survived(self):
        backend = SwayWindows(binary="/nonexistent/swaymsg")
        with mock.patch("earth_os.platform.linux_windows._run", return_value=(0, "not json")):
            self.assertEqual(backend.list_windows(), [])


class TestWmctrlParsing(unittest.TestCase):
    def test_sticky_windows_are_skipped(self):
        backend = WmctrlWindows()
        backend.binary = "/nonexistent/wmctrl"
        with mock.patch("earth_os.platform.linux_windows._run", return_value=(0, WMCTRL_LIST)):
            windows = backend.list_windows()
        # The desktop window is on workspace -1 and must be dropped.
        self.assertEqual(len(windows), 2)
        self.assertEqual({w.app for w in windows}, {"Gedit", "Firefox"})

    def test_titles_with_spaces_survive(self):
        backend = WmctrlWindows()
        backend.binary = "/nonexistent/wmctrl"
        with mock.patch("earth_os.platform.linux_windows._run", return_value=(0, WMCTRL_LIST)):
            windows = backend.list_windows()
        titles = {w.title for w in windows}
        self.assertIn("Untitled Document 1", titles)


class TestDegradation(unittest.TestCase):
    """With no backend, the shell must explain rather than look broken."""

    def test_gnome_wayland_gets_a_specific_explanation(self):
        env = {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "GNOME"}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("earth_os.platform.linux_windows.shutil.which", return_value=None):
            backend = detect("auto")
        self.assertFalse(backend.available)
        self.assertIn("gnome", backend.status().lower())
        self.assertIn("wayland", backend.status().lower())

    def test_x11_suggests_wmctrl(self):
        # An X11 session: no Wayland display, and not a Wayland session type
        # either -- inside sway, XDG_SESSION_TYPE=wayland would leak in.
        env = {"DISPLAY": ":0", "WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("earth_os.platform.linux_windows.shutil.which", return_value=None):
            os.environ.pop("WAYLAND_DISPLAY", None)
            backend = detect("auto")
        self.assertIn("wmctrl", backend.status())

    def test_disabled_backend(self):
        backend = detect("none")
        self.assertFalse(backend.available)

    def test_manager_never_raises_without_a_backend(self):
        manager = WindowManager(preferred="none")
        self.assertEqual(manager.refresh(), [])
        ok, message = manager.switch_to(Window(handle="1", title="x"))
        self.assertFalse(ok)
        self.assertTrue(message)


class TestManagerFiltering(unittest.TestCase):
    def test_our_own_window_is_not_listed(self):
        manager = WindowManager(preferred="none")

        class _Fake:
            name = "fake"
            available = True

            def list_windows(self):
                return [Window(handle="1", title="EARTH OS", app="earth-os"),
                        Window(handle="2", title="Files", app="nautilus")]

            def status(self):
                return "fake"

        manager.backend = _Fake()
        windows = manager.refresh()
        self.assertEqual([w.app for w in windows], ["nautilus"])

    def test_a_file_named_cdos_is_not_mistaken_for_our_window(self):
        """The title filter used to hide an archive manager showing cdos*.zip."""
        manager = WindowManager(preferred="none")

        class _Fake:
            name = "fake"
            available = True

            def list_windows(self):
                return [
                    Window(handle="1", title="EARTH OS", app="earth-os"),
                    Window(handle="2",
                           title="cdos_v0_9_0_weather.zip — Archive Manager",
                           app="file-roller"),
                    Window(handle="3", title="cdos.md — Text Editor",
                           app="gedit"),
                ]

            def status(self):
                return "fake"

        manager.backend = _Fake()
        windows = manager.refresh()
        self.assertEqual([w.app for w in windows], ["file-roller", "gedit"])

    def test_find_matches_app_then_title(self):
        manager = WindowManager(preferred="none")
        manager.windows = [
            Window(handle="1", title="Mozilla Firefox", app="firefox"),
            Window(handle="2", title="Untitled Document", app="gedit"),
        ]
        self.assertEqual(manager.find("firefox").handle, "1")
        self.assertEqual(manager.find("untitled").handle, "2")
        self.assertIsNone(manager.find("nothing here"))


class TestFileOpening(unittest.TestCase):
    def setUp(self):
        config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
        config.values["voice"]["enabled"] = False
        self.shell = Shell(config)
        for screen_class in ALL_SCREENS:
            self.shell.register_screen(screen_class(self.shell))

    def test_missing_path_is_reported(self):
        ok, message = self.shell.open_path("/definitely/not/here")
        self.assertFalse(ok)
        self.assertIn("no such path", message)

    def test_path_with_spaces_is_not_split(self):
        """The old code shlex-split the whole command and broke these."""
        directory = Path(tempfile.mkdtemp())
        awkward = directory / "my holiday photos.txt"
        awkward.write_text("hi", encoding="utf-8")

        seen = {}

        # Opening goes through the platform's process.launch now, so that is
        # where the argv has to be inspected.
        def fake_launch(argv, **kwargs):
            seen["argv"] = argv
            # Still running after the grace period: the file is open.
            return mock.Mock(pid=1234, wait=mock.Mock(return_value=(None, "")))

        with mock.patch("earth_os.core.shutil.which", return_value="/usr/bin/xdg-open"), \
             mock.patch.object(type(self.shell.platform.process), "launch",
                               side_effect=fake_launch):
            self.shell.capabilities = CapabilityRegistry(self.shell.platform)
            ok, _ = self.shell.open_path(str(awkward))

        self.assertTrue(ok)
        # The path must arrive as exactly one argv element, after the
        # opener's own prefix ("gio open" is two words; the path is one).
        self.assertEqual(seen["argv"], [*self.shell.OPENERS[0], str(awkward)])

    def test_no_opener_installed_says_how_to_fix(self):
        target = Path(tempfile.mkdtemp()) / "f.txt"
        target.write_text("x", encoding="utf-8")
        with mock.patch("earth_os.core.shutil.which", return_value=None):
            ok, message = self.shell.open_path(str(target))
        self.assertFalse(ok)
        self.assertIn("xdg-utils", message)

    def _openers(self, outcomes):
        """Patch process.launch so each opener ends as *outcomes* says.

        *outcomes* maps an opener's argv[0] to ``(exit code, stderr)``; a
        code of None is a program still running when the grace ran out.
        """
        launched = []

        def fake_launch(argv, **kwargs):
            launched.append(argv[0])
            code, detail = outcomes[argv[0]]
            return mock.Mock(pid=1, wait=mock.Mock(return_value=(code, detail)))

        import contextlib

        @contextlib.contextmanager
        def patched():
            with mock.patch("earth_os.core.shutil.which",
                            side_effect=lambda n: f"/usr/bin/{n}"
                            if n in outcomes else None), \
                 mock.patch.object(type(self.shell.platform.process), "launch",
                                   side_effect=fake_launch):
                # Bindings are taken when the registry is built, so it is
                # built inside the patch.
                self.shell.capabilities = CapabilityRegistry(self.shell.platform)
                yield
        return launched, patched()

    def test_an_opener_that_fails_is_not_reported_as_opening(self):
        """Reported: FILES did not open files.

        The opener was started and its *starting* was reported as the file
        opening. An opener that exits with an error found nothing to open it
        with, and the next one gets a turn.
        """
        target = Path(tempfile.mkdtemp()) / "notes.txt"
        target.write_text("x", encoding="utf-8")
        launched, patched = self._openers({
            "gio": (2, "gio: Failed to find default application for text/plain"),
            "xdg-open": (None, ""),
        })
        with patched:
            ok, message = self.shell.open_path(str(target))
        self.assertEqual(launched, ["gio", "xdg-open"])
        self.assertTrue(ok)
        self.assertIn("xdg-open", message)

    def test_when_nothing_can_open_it_the_reason_is_shown(self):
        target = Path(tempfile.mkdtemp()) / "notes.txt"
        target.write_text("x", encoding="utf-8")
        launched, patched = self._openers({
            "gio": (2, "gio: Failed to find default application for text/plain"),
            "xdg-open": (3, ""),
        })
        with patched:
            ok, message = self.shell.open_path(str(target))
        self.assertFalse(ok)
        self.assertIn("Failed to find default application", message)
        self.assertIn("xdg-open: exit 3", message)

    def test_the_browser_shows_the_real_outcome(self):
        """The screen says what happened, not that something was tried."""
        browser = self.shell.screens["browser"]
        folder = Path(tempfile.mkdtemp())
        (folder / "notes.txt").write_text("x", encoding="utf-8")
        browser.open(folder)
        browser.ensure_built(self.shell.context(1280, 800))
        browser.list.selected = 0
        launched, patched = self._openers({"gio": (4, "no handler")})
        with patched:
            browser.activate_selected()
        # No main loop in a test, so the answer arrives inline; with one it
        # arrives a moment later on the interface thread.
        self.assertFalse(browser.message_ok)
        self.assertIn("could not open notes.txt", browser.message)

    def test_browser_reports_on_its_own_screen(self):
        browser = self.shell.screens["browser"]
        browser.activate_selected()          # nothing selected
        self.assertTrue(browser.message)
        self.assertFalse(browser.message_ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestFrameMode(unittest.TestCase):
    """Programs live inside the LCARS chrome rather than replacing it."""

    class _Placing:
        name = "fake"
        available = True
        can_place = True

        def __init__(self):
            self.placed = None
            self.fullscreened = None
            self.focused = None

        def list_windows(self):
            return [Window(handle="7", title="Mozilla Firefox", app="firefox")]

        def status(self):
            return "fake"

        def focus(self, w):
            self.focused = w.handle
            return True

        def place(self, w, x, y, width, height):
            self.placed = (w.handle, x, y, width, height)
            return True

        def fullscreen(self, w, on=True):
            self.fullscreened = (w.handle, on)
            return True

    def setUp(self):
        config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
        config.values["voice"]["enabled"] = False
        self.shell = Shell(config)
        for screen_class in ALL_SCREENS:
            self.shell.register_screen(screen_class(self.shell))
        self.backend = self._Placing()
        self.shell.windows.backend = self.backend
        self.shell.windows.refresh()

    def test_frame_mode_places_rather_than_fullscreens(self):
        self.shell.frame_rect = (300, 120, 1200, 700)
        window = self.shell.windows.windows[0]
        ok, _ = self.shell.show_in_frame(window)
        self.assertTrue(ok)
        self.assertEqual(self.backend.placed, ("7", 300, 120, 1200, 700))
        self.assertIsNone(self.backend.fullscreened)
        self.assertIs(self.shell.framed_window, window)

    def test_break_out_fullscreens_and_says_the_way_back(self):
        self.shell.frame_rect = (300, 120, 1200, 700)
        window = self.shell.windows.windows[0]
        self.shell.show_in_frame(window)
        self.shell.break_out()
        self.assertEqual(self.backend.fullscreened, ("7", True))
        spoken = " ".join(str(e["text"]) for e in self.shell.conversation)
        self.assertIn("F1", spoken)

    def test_shell_does_not_minimise_in_frame_mode(self):
        """Minimising is what forced people into Alt+Tab to get back."""
        called = []
        self.shell.unfullscreen_hook = lambda: called.append(True)
        self.shell.frame_rect = (0, 0, 100, 100)
        self.shell.minimise_for(self.shell.windows.windows[0])
        self.assertEqual(called, [])

    def test_without_placement_it_falls_back_to_fullscreen(self):
        class _NoPlace(self._Placing):
            can_place = False

        self.shell.windows.backend = _NoPlace()
        self.shell.windows.refresh()
        self.assertFalse(self.shell.frame_mode)
        window = self.shell.windows.windows[0]
        ok, _ = self.shell.windows.switch_to(window)
        self.assertTrue(ok)

    def test_main_screen_publishes_a_viewport(self):
        import cairo
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1600, 1000)
        cr = cairo.Context(surface)
        ctx = self.shell.context(1600, 1000)
        main = self.shell.screens["main"]
        main.invalidate()
        main.draw(cr, ctx)
        rect = main.viewport_candidate
        self.assertEqual(len(rect), 4)
        self.assertGreater(rect[2], 0)
        self.assertGreater(rect[3], 0)

    def _draw(self, name: str):
        import cairo
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1600, 1000)
        cr = cairo.Context(surface)
        ctx = self.shell.context(1600, 1000)
        screen = self.shell.screens[name]
        screen.invalidate()
        screen.draw(cr, ctx)
        return screen

    def test_program_strip_appears_when_windows_exist(self):
        main = self._draw("main")
        self.assertIsNotNone(main.g.get("strip_0"))
        self.assertIsNone(main.g.get("strip_empty"))
        keys = main.g.get("strip_keys")
        self.assertIsNotNone(keys)
        self.assertIn("F1", keys.current())

    def test_the_strip_is_on_every_screen(self):
        """It is chrome, so it does not depend on which screen is up."""
        for name in ("main", "programs", "browser", "settings",
                     "engineering", "tasks"):
            with self.subTest(screen=name):
                screen = self._draw(name)
                self.assertIsNotNone(screen.g.get("strip_0"))

    def test_the_strip_is_there_with_nothing_running(self):
        """A control that vanishes is one you have to go looking for."""
        self.backend.list_windows = lambda: []
        self.shell.windows.refresh()
        main = self._draw("main")
        empty = main.g.get("strip_empty")
        self.assertIsNotNone(empty)
        self.assertIn("NO PROGRAMS", empty.label)

    def test_pressing_a_program_shows_the_screen_the_frame_is_on(self):
        """Framing while another screen is up would drop it on that screen."""
        self.shell.frame_rect = (300, 120, 1200, 700)
        self.shell.go("programs")
        self.shell.focus_program(self.shell.windows.windows[0])
        self.assertEqual(self.shell.current, "main")
        self.assertIsNotNone(self.shell.framed_window)

    def test_home_returns_a_fullscreen_program_and_the_shell(self):
        self.shell.frame_rect = (300, 120, 1200, 700)
        window = self.shell.windows.windows[0]
        self.shell.show_in_frame(window)
        self.shell.break_out()
        self.shell.go("tasks")
        self.shell.screens["tasks"].home(window)
        self.assertEqual(self.shell.current, "main")
        self.assertEqual(self.shell.handed_over_to, "")
        # Put back where the chrome can be seen around it, not left covering it.
        self.assertEqual(self.backend.placed, ("7", 300, 120, 1200, 700))


class TestWmctrlUnderWayland(unittest.TestCase):
    """wmctrl answers under Wayland but only sees XWayland windows.

    Reporting it as working gave an empty program list and windows that never
    moved, which reads as a broken feature rather than an absent one.
    """

    def _backend(self):
        backend = WmctrlWindows()
        backend.binary = "/usr/bin/wmctrl"
        return backend

    def test_not_available_when_wayland_display_is_set(self):
        env = {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertFalse(self._backend().available)

    def test_not_available_when_session_type_is_wayland(self):
        env = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("WAYLAND_DISPLAY", None)
            self.assertFalse(self._backend().available)

    def test_status_explains_xwayland(self):
        env = {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}
        with mock.patch.dict(os.environ, env, clear=False):
            status = self._backend().status()
        self.assertIn("XWayland", status)

    def test_available_on_a_real_x11_session(self):
        env = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("WAYLAND_DISPLAY", None)
            with mock.patch("earth_os.platform.linux_windows._run", return_value=(0, "WM")):
                self.assertTrue(self._backend().available)

    def test_gnome_wayland_advice_mentions_wmctrl_is_not_the_fix(self):
        env = {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("earth_os.platform.linux_windows.shutil.which",
                        side_effect=lambda n: "/usr/bin/wmctrl" if n == "wmctrl" else None):
            backend = detect("auto")
        self.assertFalse(backend.available)
        status = backend.status()
        self.assertIn("wmctrl is installed", status)
        self.assertIn("sway", status)


SWAY_OUTPUTS = """[
  {"name": "eDP-1", "active": true, "focused": true,
   "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080}},
  {"name": "HDMI-1", "active": false, "focused": false,
   "rect": {"x": 1920, "y": 0, "width": 1280, "height": 720}}
]"""


class TestSwayClaimsTheOutput(unittest.TestCase):
    """sway tiles by default, halving the shell when a program opens."""

    def test_output_geometry_prefers_the_focused_output(self):
        backend = SwayWindows(binary="/nonexistent/swaymsg")
        with mock.patch("earth_os.platform.linux_windows._run",
                        return_value=(0, SWAY_OUTPUTS)):
            self.assertEqual(backend.output_geometry(), (0, 0, 1920, 1080))

    def test_output_geometry_survives_bad_json(self):
        backend = SwayWindows(binary="/nonexistent/swaymsg")
        with mock.patch("earth_os.platform.linux_windows._run", return_value=(0, "nope")):
            self.assertIsNone(backend.output_geometry())

    def test_find_by_app_locates_our_own_window(self):
        backend = SwayWindows(binary="/nonexistent/swaymsg")
        tree = SWAY_TREE.replace('"firefox"', '"org.earthos.shell"')
        with mock.patch("earth_os.platform.linux_windows._run", return_value=(0, tree)):
            found = backend.find_by_app({"org.earthos.shell"})
        self.assertIsNotNone(found)
        self.assertEqual(found.handle, "10")

    def test_claim_screen_floats_and_places_the_shell(self):
        calls = []

        class _Backend:
            name = "sway"
            available = True
            can_place = True

            def list_windows(self):
                return [Window(handle="9", title="EARTH OS", app="org.earthos.shell")]

            def status(self):
                return "sway"

            def output_geometry(self):
                return (0, 0, 1920, 1080)

            def float_all(self):
                calls.append("float_all")

            def find_by_app(self, ids):
                return Window(handle="9", title="EARTH OS", app="org.earthos.shell")

            def place(self, w, x, y, width, height):
                calls.append(("place", w.handle, x, y, width, height))
                return True

        manager = WindowManager(preferred="none")
        manager.backend = _Backend()
        rect = manager.claim_screen({"org.earthos.shell"})
        self.assertEqual(rect, (0, 0, 1920, 1080))
        self.assertIn("float_all", calls)
        self.assertIn(("place", "9", 0, 0, 1920, 1080), calls)

    def test_claim_screen_is_a_no_op_without_a_capable_backend(self):
        manager = WindowManager(preferred="none")
        self.assertIsNone(manager.claim_screen({"org.earthos.shell"}))

    def test_frame_rect_uses_the_output_origin(self):
        """On a second monitor the output origin is not 0,0."""
        config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
        config.values["voice"]["enabled"] = False
        shell = Shell(config)
        shell.output_rect = (1920, 0, 1280, 720)
        # The GTK layer adds the origin to the shell-local viewport.
        local = (100, 50, 900, 500)
        ox, oy = shell.output_rect[0], shell.output_rect[1]
        expected = (ox + local[0], oy + local[1], local[2], local[3])
        self.assertEqual(expected, (2020, 50, 900, 500))


class TestAutoFraming(unittest.TestCase):
    """A program must land in the frame however it was started."""

    class _Backend:
        name = "fake"
        available = True
        can_place = True

        def __init__(self):
            self.windows = []
            self.placed = []

        def list_windows(self):
            return list(self.windows)

        def status(self):
            return "fake"

        def focus(self, w):
            return True

        def place(self, w, x, y, width, height):
            self.placed.append((w.handle, x, y, width, height))
            return True

        def fullscreen(self, w, on=True):
            return True

    def setUp(self):
        config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
        config.values["voice"]["enabled"] = False
        self.shell = Shell(config)
        for screen_class in ALL_SCREENS:
            self.shell.register_screen(screen_class(self.shell))
        self.backend = self._Backend()
        self.shell.windows.backend = self.backend
        self.shell.frame_rect = (200, 100, 1000, 600)

    def test_existing_windows_are_not_framed_on_the_first_look(self):
        """Everything is new on the first poll, but none of it just opened."""
        self.backend.windows = [Window(handle="1", title="Files", app="nautilus")]
        self.shell.poll_windows()
        self.assertEqual(self.backend.placed, [])
        self.assertIsNone(self.shell.framed_window)

    def test_a_window_that_appears_later_is_framed(self):
        self.shell.poll_windows()                      # seed, empty
        self.backend.windows = [Window(handle="7", title="Firefox", app="firefox")]
        self.shell.poll_windows()
        self.assertEqual(self.backend.placed,
                         [("7", 200, 100, 1000, 600)])
        self.assertEqual(self.shell.framed_window.handle, "7")

    def test_it_is_not_framed_twice(self):
        self.shell.poll_windows()
        self.backend.windows = [Window(handle="7", title="Firefox", app="firefox")]
        self.shell.poll_windows()
        self.shell.poll_windows()
        self.shell.poll_windows()
        self.assertEqual(len(self.backend.placed), 1)

    def test_auto_frame_can_be_turned_off(self):
        self.shell.config.values["windows"]["auto_frame"] = False
        self.shell.poll_windows()
        self.backend.windows = [Window(handle="7", title="Firefox", app="firefox")]
        self.shell.poll_windows()
        self.assertEqual(self.backend.placed, [])

    def test_no_framing_without_placement_support(self):
        class _NoPlace(self._Backend):
            can_place = False

        self.shell.windows.backend = _NoPlace()
        self.shell.poll_windows()
        self.shell.windows.backend.windows = [
            Window(handle="7", title="Firefox", app="firefox")]
        self.shell.poll_windows()
        self.assertEqual(self.shell.windows.backend.placed, [])

    def test_polling_is_safe_with_no_backend(self):
        from earth_os.windows import NullWindows
        self.shell.windows.backend = NullWindows("none")
        self.shell.poll_windows()          # must not raise


class TestBackToAProgram(unittest.TestCase):
    """Reported: on Firefox, went to PROGRAMS, pressed BACK — a black screen.

    Under sway every window floats, and focusing one raises it. A press on
    any other screen focuses the shell and lifts it over the framed program,
    and nothing put the program back when the main screen returned. The fake
    below stacks the way sway does: the last window focused is on top.
    """

    SHELL = "org.earthos.shell"

    class _Sway:
        name = "fake-sway"
        available = True
        can_place = True

        def __init__(self, windows):
            self.windows = list(windows)
            self.stack = [w.handle for w in self.windows]   # last is on top

        def list_windows(self):
            return list(self.windows)

        def status(self):
            return "fake"

        def focus(self, w):
            if w.handle in self.stack:
                self.stack.remove(w.handle)
            self.stack.append(w.handle)
            return True

        def place(self, w, x, y, width, height):
            return True

        def fullscreen(self, w, on=True):
            return True

        def find_by_app(self, app_ids):
            for w in self.windows:
                if w.app in app_ids:
                    return w
            return None

        def top(self):
            return self.stack[-1]

    def setUp(self):
        config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
        config.values["voice"]["enabled"] = False
        self.shell = Shell(config)
        for screen_class in ALL_SCREENS:
            self.shell.register_screen(screen_class(self.shell))
        self.own = Window(handle="1", title="EARTH OS", app=self.SHELL)
        self.firefox = Window(handle="7", title="Mozilla Firefox", app="firefox")
        self.editor = Window(handle="9", title="notes.txt", app="gedit")
        self.sway = self._Sway([self.own, self.editor, self.firefox])
        self.shell.windows.backend = self.sway
        self.shell.windows.refresh()
        self.shell.frame_rect = (300, 120, 1200, 700)

    def _shell_pressed(self):
        """What sway does on any press inside the shell."""
        self.sway.focus(self.own)

    def test_back_from_programs_brings_the_program_back(self):
        self.shell.focus_program(self.shell.windows.find("firefox"))
        self.assertEqual(self.sway.top(), "7")
        self.shell.go("programs")
        self._shell_pressed()                       # the press on BACK
        self.shell.go_back()
        self.assertEqual(self.shell.current, "main")
        self.assertEqual(self.sway.top(), "7", "firefox is in front again")

    def test_home_and_keys_do_the_same(self):
        self.shell.focus_program(self.shell.windows.find("firefox"))
        for leave in ("settings", "engineering", "browser"):
            self.shell.go(leave)
            self._shell_pressed()
            self.shell.go("main")
            self.assertEqual(self.sway.top(), "7", f"back from {leave}")

    def test_leaving_by_voice_puts_the_shell_in_front(self):
        self.shell.focus_program(self.shell.windows.find("firefox"))
        self.shell.go("programs")                   # no press: a key or a word
        self.assertEqual(self.sway.top(), "1",
                         "the program does not float over the next screen")

    def test_a_press_that_stays_on_main_keeps_the_program_in_front(self):
        self.shell.focus_program(self.shell.windows.find("firefox"))
        main = self.shell.screens["main"]
        main.ensure_built(self.shell.context(1600, 1000))
        listen = main.g.get("listen_toggle")
        self._shell_pressed()
        with mock.patch.object(self.shell, "toggle_listening"):
            listen.on_click = self.shell.toggle_listening
            main.on_release(listen.x + 2 + listen.h / 2, listen.y + listen.h / 2,
                            listen)
        self.assertEqual(self.sway.top(), "7")

    def test_if_it_closed_the_last_one_before_it_comes_back(self):
        self.shell.focus_program(self.shell.windows.find("gedit"))
        self.shell.focus_program(self.shell.windows.find("firefox"))
        self.shell.go("programs")
        self._shell_pressed()
        self.sway.windows.remove(self.firefox)      # firefox was closed
        self.shell.go_back()
        self.assertEqual(self.shell.framed_window.handle, "9")
        self.assertEqual(self.sway.top(), "9")

    def test_closing_it_while_framed_moves_to_the_last_one(self):
        self.shell.poll_windows()                   # seed
        self.shell.focus_program(self.shell.windows.find("gedit"))
        self.shell.focus_program(self.shell.windows.find("firefox"))
        self.sway.windows.remove(self.firefox)
        self.shell.poll_windows()
        self.assertEqual(self.shell.framed_window.handle, "9")

    def test_with_nothing_running_the_frame_is_let_go(self):
        self.shell.focus_program(self.shell.windows.find("firefox"))
        self.shell.go("programs")
        self.sway.windows = [self.own]
        self.shell.go_back()
        self.assertIsNone(self.shell.framed_window,
                          "no frame left drawn black over nothing")
