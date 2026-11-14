"""GODCORE is for the AI. It is never displayed.

The computer's own state — mood, energy, attention, the emotional weather — is
read by the Companion in conversation and by Earth Mind and the game
characters in play. It colours how the computer speaks. It was also once drawn
on the engineering screen, which made it something a person reads off a panel
rather than something the machine has.

These checks read source rather than building screens, so they run on a
machine with no GTK, which is where a regression like this should be caught.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PACKAGE = ROOT / "earth_os"
SCREENS = PACKAGE / "screens"

#: The snapshot keys that hold GODCORE, and the accessors that used to turn
#: them into text for a screen.
KEYS = ("godcore", "weather")
ACCESSORS = ("godcore_value", "weather_value")


class TestNothingDrawsGodcore(unittest.TestCase):
    def test_no_screen_reads_godcore_or_its_weather(self):
        for path in sorted(SCREENS.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for accessor in ACCESSORS:
                self.assertNotIn(accessor, text,
                                 f"{path.name} reads {accessor}: GODCORE is not displayed")
            for key in KEYS:
                self.assertIsNone(
                    re.search(r"cdos_snapshot\b[^\n]*[\"']%s[\"']" % key, text),
                    f"{path.name} reads the {key!r} block of the CDOS snapshot")

    def test_no_screen_has_a_godcore_label(self):
        """Any string a screen could draw. Comments are not strings, so a
        note explaining why it is absent does not trip this."""
        for path in sorted(SCREENS.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn("godcore", node.value.lower(),
                                     f"{path.name} puts GODCORE on a screen")

    def test_the_shell_offers_no_accessor_to_draw_it_with(self):
        text = (PACKAGE / "core.py").read_text(encoding="utf-8")
        for accessor in ACCESSORS:
            self.assertNotIn(f"def {accessor}", text)

    def test_the_engineering_screen_does_not_reserve_room_for_it(self):
        text = (SCREENS / "engineering.py").read_text(encoding="utf-8")
        self.assertNotIn("cdos_rect", text)
        self.assertNotIn("hdr_cdos", text)


class TestTheAiStillHasIt(unittest.TestCase):
    """Removing the panel must not starve the readers."""

    def test_the_snapshot_still_collects_godcore_and_weather(self):
        text = (PACKAGE / "core.py").read_text(encoding="utf-8")
        self.assertIn('snapshot["godcore"]', text)
        self.assertIn('snapshot["weather"]', text)

    def test_the_companion_and_earth_mind_are_handed_the_snapshot(self):
        text = (PACKAGE / "core.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            text.count("godcore=lambda: self.cdos_snapshot"), 2,
            "the Companion and Earth Mind must both read GODCORE")

    def test_the_cache_is_kept_warm_by_the_shell_not_by_a_screen(self):
        """It used to be refreshed from the engineering screen's tick, so it
        was only ever fresh while someone looked at that screen. Games and
        conversation do not."""
        app = (PACKAGE / "app.py").read_text(encoding="utf-8")
        # app.py drives it, which is the point. It used to be spelled
        # ``self.shell.refresh_cdos()`` inside the frame-clock callback; the
        # refresh now has its own interval in the background jobs, where it
        # is independent of the frame rate, and the shell is in hand as
        # ``shell``. Asserting on the call rather than on the receiver keeps
        # this test about where the refresh lives instead of about spelling.
        self.assertIn("refresh_cdos()", app)
        for path in sorted(SCREENS.rglob("*.py")):
            self.assertNotIn("refresh_cdos", path.read_text(encoding="utf-8"),
                             f"{path.name} drives the refresh again")

    def test_the_persona_reads_godcore_from_the_snapshot(self):
        from earth_os.companion.persona import Persona
        persona = Persona.from_snapshot({
            "godcore": {"name": "CDOS", "mood": "curious", "energy": "0.2",
                        "attention": "high"},
            "weather": {"tone": "calm", "domain": "system"}})
        self.assertEqual(persona.source, "godcore")
        self.assertEqual(persona.mood, "curious")
        self.assertTrue(any("GODCORE" in line for line in persona.style_lines()))


if __name__ == "__main__":
    unittest.main()
