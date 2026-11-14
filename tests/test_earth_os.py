"""EARTH OS: the green-phosphor look and what keeps it honest.

The look is three changes layered on the LCARS shell — a palette, a face and
a tube — and each has one property that is easy to lose by accident:

* the palette is green, and every label on it can be read;
* the face is the one asked for, and a bad name costs a font, not the shell;
* the tube adds light *around* things and never changes the things.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cairo  # noqa: E402

from earth_os import phosphor  # noqa: E402
from earth_os.config import DEFAULTS  # noqa: E402
from earth_os.theme import (  # noqa: E402
    ASSETS, FACES, PALETTE_SLOTS, Palette, Strings, contrast, face,
    font_family, font_scale, set_face,
)


class TestThePalette(unittest.TestCase):

    def test_it_ships_and_is_the_default(self):
        self.assertIn("Earth OS", Palette.list_builtin())
        self.assertEqual(DEFAULTS["shell"]["palette"], "Earth OS")

    def test_everything_but_the_alert_is_green(self):
        """Green dominates every slot. The alert is amber on purpose: the
        second colour a green tube has to say something is wrong."""
        palette = Palette.builtin("Earth OS")
        for slot in PALETTE_SLOTS:
            r, g, b = palette[slot]
            if slot == "alert":
                self.assertGreater(r, g, "alert should read amber")
                continue
            self.assertGreater(g, r, slot)
            self.assertGreater(g, b, slot)

    def test_every_slot_can_carry_a_readable_label(self):
        """Dark fills get lit labels; light fills keep black knock-outs.
        Either way the label clears 3:1 against its own fill."""
        palette = Palette.builtin("Earth OS")
        for slot in PALETTE_SLOTS:
            for fill in (palette[slot], palette.disabled(slot)):
                ink = palette.ink_for(fill)
                self.assertGreaterEqual(contrast(fill, ink), 3.0,
                                        f"{slot}: label unreadable")

    def test_text_on_black_is_never_too_dim_to_read(self):
        """Screens colour captions with any slot, including the darkest
        greens. Drawn on the bare background those are lifted to 4.5:1."""
        palette = Palette.builtin("Earth OS")
        for slot in PALETTE_SLOTS:
            shown = palette.on_background(palette[slot])
            self.assertGreaterEqual(contrast(shown, palette.background),
                                    3.0, slot)
        dim = palette.on_background(palette["accent6"])
        self.assertGreaterEqual(contrast(dim, palette.background), 4.5)

    def test_classic_palettes_draw_their_text_exactly_as_before(self):
        for name in ("Standard Colors", "Nemesis", "Old School"):
            palette = Palette.builtin(name)
            for slot in PALETTE_SLOTS:
                self.assertEqual(palette.on_background(palette[slot]),
                                 palette[slot], f"{name}: {slot}")

    def test_classic_palettes_keep_their_knockout_labels(self):
        """Lit labels exist for dark fills. They must not repaint the
        original palettes, whose light fills always took black text."""
        palette = Palette.builtin("Standard Colors")
        for slot in PALETTE_SLOTS:
            self.assertEqual(palette.ink_for(palette[slot]),
                             palette.background, slot)


class TestTheFace(unittest.TestCase):

    def tearDown(self):
        set_face("VT323")

    def test_every_face_ships_with_its_file(self):
        for name, spec in FACES.items():
            self.assertTrue((ASSETS / "fonts" / str(spec["file"])).exists(),
                            name)

    def test_the_bundled_face_ships_with_its_licence(self):
        self.assertTrue((ASSETS / "fonts" / "VT323-OFL.txt").exists(),
                        "VT323 is OFL: the licence travels with the font")

    def test_the_default_is_the_crt_face(self):
        self.assertEqual(DEFAULTS["shell"]["font"], "VT323")
        self.assertEqual(set_face("VT323"), "VT323")
        self.assertTrue(font_family().startswith("VT323,"))

    def test_the_original_face_is_one_setting_away(self):
        set_face("LCARS")
        self.assertEqual(face(), "LCARS")
        self.assertTrue(font_family().startswith("LCARS,"))
        self.assertEqual(font_scale(), 1.0)

    def test_an_unknown_face_costs_a_font_not_the_shell(self):
        self.assertEqual(set_face("Comic Sans"), "VT323")

    def test_only_our_faces_are_rescaled(self):
        """The terminal asks for a monospace face by name. It must get the
        size it asked for, not one tuned for a different typeface."""
        self.assertEqual(font_scale("DejaVu Sans Mono, monospace"), 1.0)
        self.assertGreater(font_scale("VT323, LCARS"), 1.0)


class TestTheLanguage(unittest.TestCase):

    def test_it_ships_and_is_the_default(self):
        self.assertIn("Earth OS", Strings.list_builtin())
        self.assertEqual(DEFAULTS["shell"]["language"], "Earth OS")

    def test_it_says_nothing_from_the_bridge_of_a_starship(self):
        strings = Strings.builtin("Earth OS")
        words = " ".join(strings.values.values()).upper()
        for trek in ("RED ALERT", "AUTO DESTRUCT", "LCARS", "CREWMAN",
                     "STARFLEET", "ENGINEERING"):
            self.assertNotIn(trek, words)
        self.assertEqual(strings["myReclaim"], "BACK TO EARTH OS")


def _frame(width: int, height: int):
    """A lit block with black lettering knocked out of it, on nothing."""
    def paint(cr):
        cr.set_source_rgb(0.18, 0.75, 0.45)
        cr.rectangle(40, 40, 200, 80)
        cr.fill()
        cr.set_source_rgb(0, 0, 0)
        cr.rectangle(60, 60, 20, 20)
        cr.fill()
    return paint


def _pixel(surface, x: int, y: int):
    surface.flush()
    stride = surface.get_stride()
    data = surface.get_data()
    i = y * stride + x * 4
    b, g, r = data[i], data[i + 1], data[i + 2]
    return r, g, b


class TestTheTube(unittest.TestCase):

    def render(self, enabled: bool, w: int = 320, h: int = 200):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        phosphor.composite(cairo.Context(surface), w, h, _frame(w, h),
                           background=(0, 0, 0), enabled=enabled)
        return surface

    def test_off_means_off(self):
        """Disabled, the composite is background-then-paint, pixel for
        pixel. A setting that half works is worse than none."""
        flat = cairo.ImageSurface(cairo.FORMAT_ARGB32, 320, 200)
        cr = cairo.Context(flat)
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        _frame(320, 200)(cr)
        flat.flush()
        off = self.render(False)
        self.assertEqual(bytes(off.get_data()), bytes(flat.get_data()))

    def test_glow_lights_the_dark_around_a_shape(self):
        lit = _pixel(self.render(True), 120, 122)     # just below the block
        dark = _pixel(self.render(False), 120, 122)
        self.assertGreater(lit[1], dark[1])

    def test_glow_never_lifts_lettering_knocked_out_of_a_fill(self):
        """The glow is drawn underneath the frame. Drawn on top, it turned
        black knock-out lettering green and pale fills pink-white."""
        r, g, b = _pixel(self.render(True), 70, 70)
        self.assertLess(max(r, g, b), 8, "lettering was lit by the glow")

    def test_glow_never_changes_a_fill_beyond_the_scanlines(self):
        """A fill keeps its palette colour, darkened at most by a scanline
        gap and the corner falloff — never brightened or shifted in hue."""
        on = _pixel(self.render(True), 200, 90)
        off = _pixel(self.render(False), 200, 90)
        for a, b in zip(on, off):
            self.assertLessEqual(a, b + 1)
        self.assertGreater(on[1], off[1] * 0.55)

    def test_a_1080p_frame_is_affordable(self):
        """It runs on every frame. 78 ms at 1080p was the first version;
        the window's budget at 30 fps is 33 ms for everything."""
        w, h = 1920, 1080
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surface)
        paint = _frame(w, h)
        phosphor.composite(cr, w, h, paint)          # warm the overlay
        runs = 5
        start = time.perf_counter()
        for _ in range(runs):
            phosphor.composite(cr, w, h, paint)
        each = (time.perf_counter() - start) / runs
        self.assertLess(each, 0.030, f"{each * 1000:.1f} ms a frame")


if __name__ == "__main__":
    unittest.main()
