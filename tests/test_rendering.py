"""Tests for the tube's optimisations and for what each screen asks to animate.

Two separate promises are checked here.

**The tube still looks like the tube.**  :mod:`earth_os.phosphor` now reuses
its blur surfaces between frames instead of allocating four a frame, and
keeps the finished frame so a repaint GTK asks for on its own is a blit
rather than a second trip through the glow.  Both are supposed to change what
a frame costs and nothing about what it shows, so the reuse is compared pixel
for pixel against a freshly allocated run, and the cache is checked for the
one way it could go wrong: handing back a picture that is out of date.

**Every screen knows what is moving on it.**  The frame scheduler asks the
screen on display how long until its picture changes by itself, and a screen
that answers ``None`` when something *is* moving will appear frozen.  That is
the dangerous direction, so the screens that animate are checked for saying
so, and the ones that do not are checked for the opposite — because a screen
that over-reports costs the CPU this change was made to save.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cairo  # noqa: E402

from earth_os import phosphor  # noqa: E402
from earth_os.config import Config  # noqa: E402
from earth_os.core import Shell  # noqa: E402
from earth_os.screens import ALL_SCREENS  # noqa: E402

SIZE = (480, 300)


def a_shell() -> Shell:
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    shell = Shell(config)
    for cls in ALL_SCREENS:
        shell.register_screen(cls(shell))
    return shell


def draw_a_frame(w: int, h: int) -> cairo.SurfacePattern:
    """Something with lit edges and empty space, which is what glows."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surface)
    cr.set_source_rgb(0.2, 1.0, 0.45)
    cr.rectangle(w * 0.1, h * 0.2, w * 0.3, h * 0.25)
    cr.fill()
    cr.set_source_rgb(1.0, 0.85, 0.2)
    cr.arc(w * 0.7, h * 0.6, min(w, h) * 0.12, 0, 6.2832)
    cr.fill()
    cr.set_source_rgb(0.0, 0.0, 0.0)
    cr.rectangle(w * 0.15, h * 0.26, w * 0.1, h * 0.06)
    cr.fill()
    surface.flush()
    return cairo.SurfacePattern(surface)


def composited(enabled: bool = True) -> bytes:
    w, h = SIZE
    frame = draw_a_frame(w, h)
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(out)

    def paint(c) -> None:
        c.set_source(frame)
        c.paint()

    phosphor.composite(cr, w, h, paint, background=(0.0, 0.02, 0.0),
                       enabled=enabled)
    out.flush()
    return bytes(out.get_data())


class TestReusingBuffersChangesNothingVisible(unittest.TestCase):
    """The pyramid is pooled now; the picture must be bit-for-bit the same."""

    def test_a_pooled_run_matches_a_freshly_allocated_one(self):
        w, h = SIZE
        frame = draw_a_frame(w, h)

        # _half with no screen key allocates, as the original did.
        fresh = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        fc = cairo.Context(fresh)
        src, sw, sh = frame, w, h
        for _ in range(4):
            out, sw, sh = phosphor._half(src, sw, sh)
            src = cairo.SurfacePattern(out)
        fc.set_source_surface(out, 0, 0)
        fc.paint()
        fresh.flush()
        allocated = bytes(fresh.get_data())

        # And again through the pool.
        phosphor._PYRAMID.clear()
        pooled_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        pc = cairo.Context(pooled_surface)
        src, sw, sh = frame, w, h
        for _ in range(4):
            out, sw, sh = phosphor._half(src, sw, sh, (w, h))
            src = cairo.SurfacePattern(out)
        pc.set_source_surface(out, 0, 0)
        pc.paint()
        pooled_surface.flush()

        self.assertEqual(allocated, bytes(pooled_surface.get_data()),
                         "pooling the blur pyramid changed the blur")

    def test_the_second_frame_through_the_pool_matches_the_first(self):
        """The risk of reuse: last frame's pixels showing through this one."""
        phosphor._PYRAMID.clear()
        first = composited()
        second = composited()
        third = composited()
        self.assertEqual(first, second,
                         "the second frame differed: a reused surface was "
                         "blended with rather than overwritten")
        self.assertEqual(second, third)

    def test_a_resize_drops_the_pool_rather_than_keeping_both(self):
        phosphor._PYRAMID.clear()
        w, h = SIZE
        phosphor._half(draw_a_frame(w, h), w, h, (w, h))
        self.assertGreater(len(phosphor._PYRAMID), 0)
        phosphor._half(draw_a_frame(640, 400), 640, 400, (640, 400))
        for key in phosphor._PYRAMID:
            self.assertNotEqual(key[1:], (w // 2, h // 2),
                                "the old resolution is still resident")

    def test_the_mix_surface_is_not_a_pyramid_level(self):
        """They are the same size at the default passes, and must not be
        the same surface, or the glow adds light to its own input."""
        phosphor._PYRAMID.clear()
        composited()
        quarter = (SIZE[0] // 4, SIZE[1] // 4)
        slots = {key[0] for key in phosphor._PYRAMID
                 if key[1:] == quarter}
        self.assertEqual(slots, {"level", "mix"},
                         "the mix surface collided with a pyramid level")

    def test_turning_the_tube_off_is_still_background_then_paint(self):
        self.assertNotEqual(composited(True), composited(False))


class TestTheFrameCacheCannotGoStale(unittest.TestCase):
    """The one way a kept frame is worse than no frame."""

    def setUp(self) -> None:
        self.w, self.h = SIZE
        self.frame = draw_a_frame(self.w, self.h)
        self.cache = phosphor.FrameCache()

    def _paint_onto(self, cache, token, colour) -> bytes:
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.w, self.h)
        cr = cairo.Context(out)

        def paint(c) -> None:
            c.set_source_rgb(*colour)
            c.rectangle(0, 0, self.w, self.h)
            c.fill()

        cache.draw(cr, self.w, self.h, paint, background=(0.0, 0.0, 0.0),
                   enabled=True, token=token)
        out.flush()
        return bytes(out.get_data())

    def test_the_same_token_is_served_from_the_cache(self):
        first = self._paint_onto(self.cache, "gen-1", (0.1, 0.9, 0.3))
        self.assertEqual(self.cache.hits, 0)
        second = self._paint_onto(self.cache, "gen-1", (0.1, 0.9, 0.3))
        self.assertEqual(self.cache.hits, 1, "an unchanged frame was redrawn")
        self.assertEqual(first, second)

    def test_a_new_token_is_composited_again(self):
        self._paint_onto(self.cache, "gen-1", (0.1, 0.9, 0.3))
        before = self.cache.misses
        self._paint_onto(self.cache, "gen-2", (0.9, 0.1, 0.3))
        self.assertEqual(self.cache.misses, before + 1)
        self.assertEqual(self.cache.hits, 0)

    def test_a_changed_picture_under_a_new_token_is_actually_redrawn(self):
        green = self._paint_onto(self.cache, "gen-1", (0.1, 0.9, 0.3))
        red = self._paint_onto(self.cache, "gen-2", (0.9, 0.1, 0.3))
        self.assertNotEqual(green, red,
                            "the cache served a stale frame after a change")

    def test_a_none_token_never_caches(self):
        """What the window passes while anything is animating."""
        self._paint_onto(self.cache, None, (0.1, 0.9, 0.3))
        self._paint_onto(self.cache, None, (0.1, 0.9, 0.3))
        self.assertEqual(self.cache.hits, 0)
        self.assertEqual(self.cache.bypassed, 2)
        self.assertIsNone(self.cache.token)

    def test_a_none_token_after_a_cached_frame_draws_the_new_content(self):
        self._paint_onto(self.cache, "gen-1", (0.1, 0.9, 0.3))
        red = self._paint_onto(self.cache, None, (0.9, 0.1, 0.3))
        plain = self._paint_onto(phosphor.FrameCache(), None, (0.9, 0.1, 0.3))
        self.assertEqual(red, plain,
                         "a bypassed frame was served from the cache anyway")

    def test_a_resize_is_not_served_from_the_old_size(self):
        self._paint_onto(self.cache, "gen-1", (0.1, 0.9, 0.3))
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, 640, 400)
        cr = cairo.Context(out)
        hit = self.cache.draw(cr, 640, 400, lambda c: None,
                              background=(0, 0, 0), enabled=True,
                              token="gen-1")
        self.assertFalse(hit, "a frame of the wrong size was handed back")

    def test_a_cached_frame_matches_an_uncached_one(self):
        """The cache must be an optimisation, not a second renderer."""
        cached = self._paint_onto(self.cache, "gen-1", (0.2, 0.8, 0.4))
        direct = self._paint_onto(phosphor.FrameCache(), None, (0.2, 0.8, 0.4))
        self.assertEqual(cached, direct,
                         "the cached path draws a different picture from the "
                         "direct one")

    def test_invalidate_forgets_everything(self):
        self._paint_onto(self.cache, "gen-1", (0.1, 0.9, 0.3))
        self.cache.invalidate()
        self._paint_onto(self.cache, "gen-1", (0.1, 0.9, 0.3))
        self.assertEqual(self.cache.hits, 0)


class TestEveryScreenSaysWhatIsMovingOnIt(unittest.TestCase):
    """``next_frame_in`` is the whole of what the scheduler knows."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.shell = a_shell()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, *SIZE)
        cls.cr = cairo.Context(surface)
        cls.ctx = cls.shell.context(*SIZE)
        for screen in cls.shell.screens.values():
            screen.ensure_built(cls.ctx)

    def test_every_screen_answers_the_question(self):
        for name, screen in sorted(self.shell.screens.items()):
            with self.subTest(screen=name):
                answer = screen.next_frame_in()
                self.assertTrue(answer is None or isinstance(answer, float),
                                f"{name} answered {answer!r}")
                if answer is not None:
                    self.assertGreaterEqual(answer, 0.0)

    def test_the_screens_with_live_readings_ask_for_a_frame_a_second(self):
        for name in ("main", "engineering"):
            with self.subTest(screen=name):
                screen = self.shell.screens[name]
                self.assertTrue(screen.live,
                                f"{name} shows live readings and must say so")
                delay = screen.next_frame_in()
                self.assertIsNotNone(delay, f"{name} would freeze")
                self.assertLessEqual(delay, 1.0)

    def test_a_static_screen_asks_for_nothing(self):
        """Where the 34.6% went. PROGRAM INDEX does not change on its own."""
        for name in ("programs", "tasks", "recent", "settings"):
            with self.subTest(screen=name):
                screen = self.shell.screens[name]
                self.assertIsNone(
                    screen.next_frame_in(),
                    f"{name} asks for frames it does not need")

    def test_the_terminal_asks_for_its_caret(self):
        terminal = self.shell.screens["terminal"]
        delay = terminal.next_frame_in()
        self.assertIsNotNone(delay, "the caret would stop blinking")
        self.assertLessEqual(delay, 0.5)

    def test_a_flash_after_a_press_is_animated_and_then_is_not(self):
        screen = self.shell.screens["programs"]
        self.assertIsNone(screen.next_frame_in())
        element = next((e for e in screen.g.items if e.interactive), None)
        self.assertIsNotNone(element, "no interactive element to flash")
        element.flash_until = time.monotonic() + 0.18
        delay = screen.next_frame_in()
        self.assertIsNotNone(delay, "a flash would stick on screen")
        self.assertLessEqual(delay, 0.18)
        element.flash_until = 0.0
        self.assertIsNone(screen.next_frame_in(),
                          "the screen kept animating after the flash ended")

    def test_a_toast_is_animated_until_it_expires(self):
        screen = self.shell.screens["programs"]
        self.shell.toast_text = "SAVED"
        self.shell.toast_until = time.monotonic() + 1.5
        try:
            delay = screen.next_frame_in()
            self.assertIsNotNone(delay,
                                 "the toast would never be cleared")
            self.assertLessEqual(delay, 1.5)
        finally:
            self.shell.toast_text = ""
            self.shell.toast_until = 0.0
        self.assertIsNone(screen.next_frame_in())


class TestTheShellKnowsWhatChanged(unittest.TestCase):
    """The signatures the background jobs compare, instead of redrawing."""

    def setUp(self) -> None:
        self.shell = a_shell()

    def test_the_telemetry_signature_is_stable_while_nothing_moves(self):
        first = self.shell.telemetry_signature()
        second = self.shell.telemetry_signature()
        self.assertEqual(first, second,
                         "sampling twice looks like a change, so the gauges "
                         "would redraw every interval regardless")

    def test_the_telemetry_signature_notices_a_reading_moving(self):
        before = self.shell.telemetry_signature()

        class Moved:
            def __getattr__(self, name):
                raise AttributeError(name)

            def cpu(self): return 0.99
            def cores(self): return [0.9]
            def memory(self): return (0.9, 9, 10)
            def swap(self): return (0.5, 5, 10)
            def disk(self, path="/"): return (0.9, 9, 10)
            def load(self): return (9.0, 9.0, 9.0)
            def uptime(self): return 99999.0
            def temperature(self): return 99.0
            def battery(self): return None
            def net_rate(self): return (9.0, 9.0)
            def processes(self): return 999

        self.shell.monitor = Moved()
        self.assertNotEqual(self.shell.telemetry_signature(), before)

    def test_a_monitor_that_throws_still_yields_frames(self):
        class Broken:
            def __getattr__(self, name):
                def boom(*a, **kw):
                    raise OSError("/proc is not there")
                return boom

        self.shell.monitor = Broken()
        first = self.shell.telemetry_signature()
        second = self.shell.telemetry_signature()
        # Deliberately unequal: a monitor that cannot be read must not look
        # like a machine where nothing is happening.
        self.assertNotEqual(first, second)

    def test_the_window_signature_is_stable_while_nothing_opens(self):
        self.assertEqual(self.shell.window_signature(),
                         self.shell.window_signature())


if __name__ == "__main__":
    unittest.main()
