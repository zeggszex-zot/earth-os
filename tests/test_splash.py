"""Tests for the boot splash.

The splash used to promise that it **loaded nothing** — a centred wordmark
drawn from constants, so it could not be delayed by a disk that was not ready
yet. The tests checked that promise scrupulously, and the promise was the
wrong one: what the screen actually cost was a hard cut, because when the
shell arrived it replaced a wordmark with a completely different layout and
boot looked like two unrelated programs starting in sequence.

The splash now draws the real frame. So the promise has changed, and these
tests change with it. There are two things worth checking and they pull in
opposite directions:

* **It must not wait for the shell.** Building the shell is the interval this
  screen exists to cover, so importing the splash must not import it.
* **It must not diverge from the shell.** A splash whose frame sits two pixels
  off is worse than no splash, because the handover draws attention to itself.

The second is the one that rots quietly, so it is checked against pixels from
the real chrome builder rather than against a description of it.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cairo                                                   # noqa: E402

from earth_os import chrome, splash                          # noqa: E402


def surface(width: int = 800, height: int = 450):
    image = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    return image, cairo.Context(image)


class TestItDoesNotWaitForWhatItIsCovering(unittest.TestCase):
    """The part of the old promise that was worth keeping."""

    #: Importing the splash may reach these and no further. The frame, the
    #: palette and the widgets that draw it — nothing that talks to the
    #: machine, and nothing that has to be constructed before it can answer.
    ALLOWED = {
        "earth_os",
        # The name on the glass. A module of constants with no imports at
        # all; it is here so the splash and the shell cannot disagree about
        # what the machine is called.
        "earth_os.brand",
        "earth_os.chrome",
        "earth_os.splash",
        "earth_os.theme",
        "earth_os.widgets",
        "earth_os.widgets.base",
        "earth_os.widgets.controls",
        "earth_os.widgets.shapes",
    }

    def _imported_by_the_splash(self) -> set:
        """What a fresh interpreter pulls in, measured in a fresh interpreter.

        The first version of this test cleared ``earth_os`` out of
        ``sys.modules`` in place. It passed on its own and broke six tests in
        another file, which held references to classes from the modules it had
        just discarded. A test that needs a clean interpreter should ask for
        one rather than improvising it inside a shared one.
        """
        probe = (
            "import sys; sys.path.insert(0, %r);"
            "import earth_os.splash;"
            "print(' '.join(sorted(m for m in sys.modules"
            " if m.startswith('earth_os'))))" % str(ROOT)
        )
        done = subprocess.run([sys.executable, "-c", probe],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        return set(done.stdout.split())

    def test_it_does_not_import_the_shell(self):
        """``Shell`` is the thing being waited for.

        If importing the splash also imports the shell, showing the splash
        costs exactly as much as not showing it, and the screen is decoration
        over a delay it caused.
        """
        pulled = self._imported_by_the_splash()
        self.assertNotIn("earth_os.core", pulled)
        self.assertNotIn("earth_os.app", pulled)

    def test_it_does_not_import_the_machine(self):
        """Nor the platform layer, the window manager or the screens.

        This is the rule that was actually broken, and not by carelessness:
        ``Frame`` lived in ``screens/base.py``, so asking for the frame ran
        ``screens/__init__``, which imports every screen, which imports the
        window manager and the platform adapters. 72 ms and twenty-seven
        modules to draw an empty rectangle. The frame moved to
        ``earth_os/chrome.py`` for that reason, and this test is what stops
        it drifting back.
        """
        pulled = self._imported_by_the_splash()
        strayed = pulled - self.ALLOWED
        self.assertEqual(strayed, set(),
                         f"the splash now also imports {sorted(strayed)}")


class TestItIsTheSameFrame(unittest.TestCase):
    """The reason this screen was rewritten.

    The first rewrite built the chrome by copying ``add_chrome``'s
    construction, and the copy had drifted before it was a day old: the bottom
    bar lost its right cap and the clock pill beside it went missing
    altogether. Both were invisible in isolation and obvious at handover,
    where the frame appeared to twitch.

    So there is now one description of the chrome and both callers use it. The
    tests below check that it stayed one — first structurally, then in pixels,
    because a shared function can still be called with different arguments.
    """

    SIZE = (1200, 700)

    def _ctx(self):
        return splash.context(*self.SIZE)

    def _render(self, painter) -> bytes:
        image, cr = surface(*self.SIZE)
        painter(cr)
        image.flush()
        return bytes(image.get_data())

    def test_the_splash_draws_the_shared_pieces(self):
        """Not a lookalike: the same objects, from the same builder."""
        ctx = self._ctx()
        names = {p.name for p in chrome.pieces(ctx, chrome.Frame(ctx),
                                               title=splash.NAME)}
        self.assertIn("chrome_tl", names)
        self.assertIn("chrome_bl", names)
        self.assertIn("chrome_titlebar", names)
        self.assertIn("chrome_strip", names)
        self.assertIn("chrome_clock", names)

    def test_the_program_strip_is_not_among_them(self):
        """It needs the application, and the whole point is that there is not
        one yet. If it ever appears here, the splash has acquired a dependency
        on the thing it is covering."""
        ctx = self._ctx()
        names = {p.name for p in chrome.pieces(ctx, chrome.Frame(ctx))}
        for needs_the_app in ("strip_label", "strip_empty", "chrome_back",
                              "chrome_home"):
            self.assertNotIn(needs_the_app, names)

    def test_the_chrome_lands_on_the_same_pixels(self):
        """The check that would have caught the missing cap and clock.

        Both are drawn with an identical context and compared where the chrome
        is — the two elbows, the title band and the bottom band. The content
        rectangle is excluded: the shell fills it with panels and the splash
        deliberately leaves it empty, which is the one difference there is
        supposed to be.
        """
        ctx = self._ctx()
        f = chrome.Frame(ctx)

        def paint_shared(cr):
            cr.set_source_rgb(*ctx.palette.background)
            cr.rectangle(0, 0, *self.SIZE)
            cr.fill()
            for piece in chrome.pieces(ctx, f, title=splash.NAME):
                if piece.name == "chrome_clock":
                    piece.label = chrome.clock_label()
                piece.draw(cr, ctx)

        shared = self._render(paint_shared)
        drawn = self._render(lambda cr: splash.draw(cr, *self.SIZE))

        cx, cy, cw, ch = f.content
        width, height = self.SIZE
        # The outline is stroked *on* the border of the content rectangle and
        # antialiased, so it lands a pixel or two outside it. Excluding the
        # rectangle alone leaves that halo behind and the test fails on the
        # splash's own outline rather than on any disagreement about the
        # frame, which is not what is being asked.
        edge = max(ctx.u(0.09), 1.0) + 2.0

        differing = []
        for y in range(0, height, 3):
            for x in range(0, width, 3):
                if (cx - edge <= x <= cx + cw + edge
                        and cy - edge <= y <= cy + ch + edge):
                    continue            # the middle is meant to differ
                at = (y * width + x) * 4
                if shared[at:at + 4] != drawn[at:at + 4]:
                    differing.append((x, y))

        self.assertEqual(differing[:12], [],
                         f"{len(differing)} chrome pixels differ from the "
                         f"shell's own frame, first at {differing[:12]}")

    def test_the_frame_is_asked_for_not_measured_out(self):
        """The middle is ``f.content`` exactly.

        An earlier version approximated it with its own ``u(1.4)`` and
        ``u(0.6)``, which put the outline a few pixels off the panel that
        replaced it — visible as the panel jumping on arrival.
        """
        ctx = self._ctx()
        f = chrome.Frame(ctx)
        cx, cy, cw, ch = f.content

        image, cr = surface(*self.SIZE)
        splash.draw(cr, *self.SIZE)
        image.flush()
        data = bytes(image.get_data())
        width = self.SIZE[0]

        def lit(x, y):
            at = (int(y) * width + int(x)) * 4
            return any(data[at:at + 3])

        # The outline sits on the border of the content rectangle: lit along
        # the edge, dark a little way inside it.
        self.assertTrue(lit(cx, cy + ch * 0.5),
                        "no outline down the left edge of the content area")
        self.assertTrue(lit(cx + cw * 0.5, cy),
                        "no outline along the top edge of the content area")
        self.assertFalse(lit(cx + cw * 0.25, cy + ch * 0.25),
                         "the middle should be empty")


class TestItDraws(unittest.TestCase):

    def test_it_draws_at_ordinary_sizes(self):
        for width, height in ((1920, 1080), (1280, 800), (800, 450),
                              (3840, 2160)):
            image, cr = surface(width, height)
            splash.draw(cr, width, height, message="starting", elapsed=0.3)
            self.assertEqual(image.get_width(), width)

    def test_it_survives_a_tiny_surface(self):
        """A compositor can hand over a 1x1 before it settles, and the boot
        screen is the worst possible place to raise."""
        _image, cr = surface(1, 1)
        splash.draw(cr, 1, 1)
        splash.draw(cr, 0, 0)

    def test_the_message_is_optional(self):
        _image, cr = surface()
        splash.draw(cr, 800, 450)

    def test_a_long_message_does_not_raise(self):
        _image, cr = surface()
        splash.draw(cr, 800, 450, message="failed: " + "x" * 400)

    def test_it_actually_puts_pixels_down(self):
        """A splash that draws nothing is indistinguishable from no splash."""
        image, cr = surface(400, 300)
        splash.draw(cr, 400, 300, message="starting", elapsed=0.5)
        image.flush()
        data = bytes(image.get_data())
        self.assertTrue(any(byte for byte in data), "nothing was drawn")

    def test_a_missing_theme_does_not_stop_the_boot(self):
        """The palette is 88 bytes and reading it is the one thing here that
        can fail. If it does, the frame is still worth drawing in the wrong
        colours — a machine that will not boot because a colour scheme is
        missing is a worse machine."""
        ctx = splash.context(800, 450, palette_name="no such palette at all")
        self.assertIsNotNone(ctx.palette)
        _image, cr = surface()
        splash.draw(cr, 800, 450)


class TestTheMovingPart(unittest.TestCase):
    """The sweep is the only thing distinguishing "slow" from "stopped"."""

    def _row(self, elapsed: float) -> bytes:
        image, cr = surface(600, 400)
        splash.draw(cr, 600, 400, elapsed=elapsed)
        image.flush()
        return bytes(image.get_data())

    def test_it_moves_between_frames(self):
        self.assertNotEqual(self._row(0.0), self._row(0.9))

    def test_it_returns_rather_than_jumping(self):
        """Ping-pong, not wrap. A wrapping phase lights both ends at once as
        it crosses zero, which reads as a rendering fault — and on a boot
        screen anything that looks like a fault is one."""
        near_start = self._row(0.01)
        just_after_a_full_cycle = self._row(2.0 / 0.55 - 0.01)
        self.assertEqual(near_start[:2000],
                         just_after_a_full_cycle[:2000])

    def test_it_is_given_no_progress_to_report(self):
        """A progress bar here would be a fabricated answer.

        Progress cannot be known: the module deliberately does not import the
        thing it is waiting for, so any fraction it drew would be invented.
        This codebase has already spent an audit removing plausible numbers
        returned in place of real ones, and a boot screen is not the place to
        put one back.

        Checked at the signature, because that is where it would have to
        arrive. ``draw`` is told the time and nothing else about how far
        along anything is, which leaves it nothing to lie with.
        """
        import inspect
        # draw_frame is draw without the background fill, for the phosphor
        # composite. It is held to the same rule: a second door is still a
        # door.
        for function in (splash.draw, splash.draw_frame):
            taken = set(inspect.signature(function).parameters)
            self.assertEqual(taken, {"cr", "width", "height", "message",
                                     "elapsed"}, function.__name__)


class TestThereIsOnlyOneWindow(unittest.TestCase):
    """The splash is a *state* of the window, not a window of its own.

    There used to be two, with a handover: present the real window, then
    destroy the splash, in that order, and hope the compositor never showed a
    frame in between. The old test here pinned that order, which was the best
    available answer while there were two windows to order.

    There is one now, so the failure mode is gone rather than guarded. These
    tests check that it stays gone — an ordering can be got right again after
    someone reintroduces the second window, and the point is that nobody
    should.
    """

    SOURCE = (ROOT / "earth_os" / "app.py").read_text()

    def _tree(self):
        import ast
        return ast.parse(self.SOURCE)

    def test_only_one_window_class_exists(self):
        import ast
        windows = [node.name for node in ast.walk(self._tree())
                   if isinstance(node, ast.ClassDef)
                   and any(getattr(base, "attr", "") == "ApplicationWindow"
                           for base in node.bases)]
        self.assertEqual(windows, ["EarthWindow"],
                         f"more than one window again: {windows}")

    def test_nothing_is_destroyed_during_boot(self):
        """A destroy during boot means a handover, and a handover means a
        frame the compositor gets to fill with whatever it likes."""
        self.assertNotIn(".destroy()", self.SOURCE)

    def test_the_window_opens_before_the_shell_is_built(self):
        """``do_activate`` presents, then boots. The other way round is the
        original defect: 128 ms of console while the shell was constructed."""
        import ast
        activate = next(n for n in ast.walk(self._tree())
                        if isinstance(n, ast.FunctionDef)
                        and n.name == "do_activate")
        body = ast.dump(activate)
        self.assertIn("present", body)
        # the shell is built by the boot steps, not here
        self.assertNotIn("Shell(", ast.unparse(activate))

    def test_the_shell_starts_absent(self):
        """``EarthWindow.shell`` is Optional and begins as None: that is what
        makes the boot frame a state of this window rather than a stand-in
        for it."""
        from earth_os import app
        self.assertIn("shell", app.EarthWindow.__init__.__doc__ or ""
                      or self.SOURCE)
        self.assertIn("self.shell: Optional[\"Shell\"] = None", self.SOURCE)

    def test_the_draw_falls_back_to_the_frame(self):
        """With no shell, the window draws the splash — checked by calling
        the draw function against a stand-in, since a real window needs a
        display and this has to hold on a build machine too."""
        import types
        from earth_os import app

        image, cr = surface(600, 400)
        window = types.SimpleNamespace(shell=None, message="starting",
                                       _opened=0.0)
        app.EarthWindow._on_draw(window, None, cr, 600, 400)
        image.flush()
        self.assertTrue(any(bytes(image.get_data())),
                        "the window drew nothing while the shell was absent")


class TestTheCursorContract(unittest.TestCase):
    """No pointer means not ready; a pointer means ready.

    The drawing cannot enforce that — the window does — so what is checked
    here is that the contract is written down where whoever changes the
    window will see it.
    """

    def test_the_contract_is_documented_beside_the_code(self):
        text = (ROOT / "earth_os" / "splash.py").read_text()
        self.assertIn("cursor", text.lower())
        self.assertIn("ready", text.lower())

    def test_the_window_hides_the_pointer_then_reveals_it(self):
        """Hidden when the window is constructed, revealed in ``attach``.

        With one window this is a property change at the moment the shell can
        take input, rather than a second window arriving with a different
        cursor — so the signal now means exactly what it says.
        """
        import ast
        source = (ROOT / "earth_os" / "app.py").read_text()
        tree = ast.parse(source)

        def body_of(class_name, method):
            cls = next(n for n in ast.walk(tree)
                       if isinstance(n, ast.ClassDef) and n.name == class_name)
            fn = next(n for n in cls.body
                      if isinstance(n, ast.FunctionDef) and n.name == method)
            # ast.unparse normalises string quotes, so compare without them.
            return ast.unparse(fn).replace("'", '"')

        self.assertIn('Gdk.Cursor.new_from_name("none", None)',
                      body_of("EarthWindow", "__init__"))
        self.assertIn("self.set_cursor(None)",
                      body_of("EarthWindow", "attach"))


if __name__ == "__main__":
    unittest.main()
