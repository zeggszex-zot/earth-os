"""Colour palettes, string tables and font handling.

Reads the original LCARS x32 data formats so existing ``.lxcp`` palettes and
``.lng`` language files drop straight in.

``.lxcp`` palette format
------------------------
Eleven lines, one colour per line, either ``#RRGGBB`` or a .NET named colour
(``Orange``, ``Silver``, ``LightGrey`` ...).  The original binaries do not
record what each slot is *called*, only the order they are read in, so the
functional names below are this port's mapping, recovered by comparing the four
shipped palettes against screenshots of the original UI.  Override any slot by
name in ``config.toml`` if you disagree with the mapping.

``.lng`` language format
------------------------
A header line, then ``<Language:"Name">`` ... ``</Language>`` wrapping
``key = "VALUE"`` pairs.  Keys are the WinForms control names of the original
main screen, which this port keeps so the two stay interchangeable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

ASSETS = Path(__file__).parent / "assets"

RGB = Tuple[float, float, float]

#: Order in which colours appear in a ``.lxcp`` file.
PALETTE_SLOTS: Sequence[str] = (
    "accent1",    # 1  dominant structural colour (bars, blocks)
    "accent2",    # 2  light blue - secondary buttons
    "accent3",    # 3  lilac - tertiary buttons
    "accent4",    # 4  gold - primary action buttons
    "accent5",    # 5  cream - highlights and readouts
    "accent6",    # 6  muted secondary
    "text",       # 7  body text on dark background
    "alert",      # 8  red alert / destruct
    "frame",      # 9  elbows and framing furniture
    "highlight",  # 10 hover / active state
    "select",     # 11 selection and focus
)

# .NET System.Drawing named colours used by the shipped palettes, plus the
# handful most likely to appear in a hand-written one.
NAMED_COLORS: Dict[str, str] = {
    "black": "#000000", "white": "#FFFFFF", "red": "#FF0000",
    "lime": "#00FF00", "blue": "#0000FF", "yellow": "#FFFF00",
    "cyan": "#00FFFF", "aqua": "#00FFFF", "magenta": "#FF00FF",
    "fuchsia": "#FF00FF", "silver": "#C0C0C0", "gray": "#808080",
    "grey": "#808080", "maroon": "#800000", "olive": "#808000",
    "green": "#008000", "purple": "#800080", "teal": "#008080",
    "navy": "#000080", "orange": "#FFA500", "orangered": "#FF4500",
    "gold": "#FFD700", "lightgrey": "#D3D3D3", "lightgray": "#D3D3D3",
    "darkgray": "#A9A9A9", "darkgrey": "#A9A9A9", "dimgray": "#696969",
    "lightblue": "#ADD8E6", "steelblue": "#4682B4", "tan": "#D2B48C",
    "salmon": "#FA8072", "crimson": "#DC143C", "indigo": "#4B0082",
    "violet": "#EE82EE", "plum": "#DDA0DD", "khaki": "#F0E68C",
    "transparent": "#000000",
}


def parse_color(value: str) -> RGB:
    """Parse ``#RRGGBB``, ``#RGB`` or a .NET named colour into floats 0..1."""
    v = (value or "").strip()
    if not v:
        return (1.0, 0.8, 0.0)
    if not v.startswith("#"):
        v = NAMED_COLORS.get(v.lower().replace(" ", ""), "#FFA500")
    v = v.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        return (1.0, 0.8, 0.0)
    try:
        return tuple(int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (1.0, 0.8, 0.0)


def rgb_to_hex(c: RGB) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(x * 255))) for x in c)


def mix(a: RGB, b: RGB, t: float) -> RGB:
    """Linear blend, ``t=0`` gives *a*."""
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def lighten(c: RGB, t: float = 0.35) -> RGB:
    return mix(c, (1.0, 1.0, 1.0), t)


def darken(c: RGB, t: float = 0.35) -> RGB:
    return mix(c, (0.0, 0.0, 0.0), t)


def luminance(c: RGB) -> float:
    """WCAG relative luminance, 0 for black and 1 for white."""
    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: RGB, b: RGB) -> float:
    """WCAG contrast ratio between two colours, from 1 to 21."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


#: Text drawn straight on the background in a colour darker than this is
#: lifted (see :meth:`Palette.on_background`). Low enough that the classic
#: palettes, all of whose colours clear it, are drawn exactly as before.
TEXT_FLOOR_CONTRAST = 3.0

#: ...and how far it is lifted: the WCAG AA level for normal text.
TEXT_LIFT_CONTRAST = 4.5

#: Below this, text knocked out of a fill in the background colour is hard
#: to read, and a lit label is used instead. 3:1 is the WCAG floor for large
#: text — which button labels are — and it is low enough that every shipped
#: LCARS palette keeps its classic black-on-colour labels.
KNOCKOUT_MIN_CONTRAST = 3.0


@dataclass
class Palette:
    """Eleven LCARS colours plus derived shades."""

    name: str = "Standard Colors"
    colors: Dict[str, RGB] = field(default_factory=dict)
    background: RGB = (0.0, 0.0, 0.0)

    @classmethod
    def from_file(cls, path: str | Path) -> "Palette":
        path = Path(path)
        raw = path.read_text(encoding="utf-8", errors="replace")
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        return cls.from_values(path.stem, lines)

    @classmethod
    def from_values(cls, name: str, values: Iterable[str]) -> "Palette":
        vals: List[str] = list(values)
        colors: Dict[str, RGB] = {}
        for i, slot in enumerate(PALETTE_SLOTS):
            colors[slot] = parse_color(vals[i]) if i < len(vals) else (1.0, 0.65, 0.0)
        return cls(name=name, colors=colors)

    @classmethod
    def builtin(cls, name: str = "Standard Colors") -> "Palette":
        path = ASSETS / "palettes" / f"{name}.lxcp"
        if not path.exists():
            path = ASSETS / "palettes" / "Standard Colors.lxcp"
        return cls.from_file(path)

    @staticmethod
    def list_builtin() -> List[str]:
        return sorted(p.stem for p in (ASSETS / "palettes").glob("*.lxcp"))

    def __getitem__(self, slot: str) -> RGB:
        return self.colors.get(slot, (1.0, 0.65, 0.0))

    def get(self, slot: str, default: RGB = (1.0, 0.65, 0.0)) -> RGB:
        return self.colors.get(slot, default)

    def hover(self, slot: str) -> RGB:
        return lighten(self[slot], 0.30)

    def pressed(self, slot: str) -> RGB:
        return darken(self[slot], 0.30)

    def disabled(self, slot: str) -> RGB:
        return mix(self[slot], self.background, 0.62)

    def on_background(self, color: RGB) -> RGB:
        """*color* made legible as text on the bare background.

        A phosphor palette wants its darkest greens for fills and dim
        furniture, and screens also use those slots to colour captions —
        which on black are barely there. A colour under
        :data:`TEXT_FLOOR_CONTRAST` is mixed toward the palette's brightest
        text colour until it reaches :data:`TEXT_LIFT_CONTRAST`; anything
        already readable is returned untouched.
        """
        bg = self.background
        if contrast(color, bg) >= TEXT_FLOOR_CONTRAST:
            return color
        target = max((self.get(s, (1.0, 1.0, 1.0)) for s in
                      ("accent5", "text", "highlight", "select")),
                     key=lambda c: contrast(c, bg))
        for step in range(1, 21):
            lifted = mix(color, target, step / 20.0)
            if contrast(lifted, bg) >= TEXT_LIFT_CONTRAST:
                return lifted
        return target

    def ink_for(self, fill: RGB) -> RGB:
        """The colour to write a label in on top of *fill*.

        LCARS knocks its labels out of the fill in the background colour,
        which only works while the fill is light. A green-phosphor palette
        needs dark fills too, and black on dark green is not a label — so a
        fill too dark to knock out of gets the palette's brightest text
        colour instead.
        """
        if contrast(fill, self.background) >= KNOCKOUT_MIN_CONTRAST:
            return self.background
        lit = max((self.get(s, (1.0, 1.0, 1.0)) for s in
                   ("accent5", "text", "highlight", "select")),
                  key=lambda c: contrast(fill, c))
        if contrast(fill, lit) > contrast(fill, self.background):
            return lit
        return self.background

    def apply_overrides(self, overrides: Dict[str, str]) -> None:
        for slot, value in (overrides or {}).items():
            if slot in PALETTE_SLOTS:
                self.colors[slot] = parse_color(value)


LNG_HEADER = re.compile(r'<Language:"(?P<name>[^"]*)">')
LNG_PAIR = re.compile(r'^\s*(?P<key>\w+)\s*=\s*"(?P<value>[^"]*)"')


@dataclass
class Strings:
    """A ``.lng`` string table, keyed by the original control names."""

    name: str = "Standard"
    values: Dict[str, str] = field(default_factory=dict)

    #: Fallbacks for keys a given .lng may omit.  TNG.lng, for example, writes
    #: ``myShowTrayButton`` where Standard.lng writes ``ShowTrayButton``.
    ALIASES: Dict[str, str] = field(default_factory=lambda: {
        "ShowTrayButton": "myShowTrayButton",
        "myShowTrayButton": "ShowTrayButton",
    })

    DEFAULTS: Dict[str, str] = field(default_factory=lambda: {
        "myStartMenu": "START MENU", "myComp": "MY COMPUTER",
        "mySettings": "SETTINGS", "myEngineering": "ENGINEERING",
        "myDeactivate": "DEACTIVATE",
        "myPhoto": "PHOTO VIEWER", "myButtonManager": "MANAGE PERSONAL PROGRAMS",
        "myUserButtons": "PERSONAL PROGRAMS", "myDocuments": "MY DOCUMENTS",
        "myPictures": "MY PICTURES", "myBrowser": "BROWSER", "ShowTrayButton": "SHOW TRAY ICONS",
        "myOSK": "OSK", "mySpeech": "SPEECH",
        # Added by this port.
        "myComputerCore": "COMPUTER CORE", "myConversation": "COMPUTER",
        "myTerminal": "TERMINAL", "myClaude": "CLAUDE CODE", "myBack": "BACK", "myHome": "HOME",
        "myTasks": "RUNNING PROGRAMS", "myRecent": "RECENT",
        "myReclaim": "BACK TO EARTH OS",
        "myExit": "EXIT", "myPower": "POWER", "myNetwork": "NETWORK",
        "myStatus": "SYSTEM STATUS", "myListening": "LISTENING",
        "myStandby": "STANDBY", "myWorking": "WORKING",
        "myComms": "COMMUNICATIONS", "myScreenshot": "SCREEN SHOT",
        "myEvolution": "EVOLUTION", "myEvolutionTitle": "SYSTEM EVOLUTION CENTER",
    })

    @classmethod
    def from_file(cls, path: str | Path) -> "Strings":
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        name = Path(path).stem
        m = LNG_HEADER.search(text)
        if m:
            name = m.group("name") or name
        values: Dict[str, str] = {}
        for line in text.splitlines():
            pm = LNG_PAIR.match(line)
            if pm:
                values[pm.group("key")] = pm.group("value")
        return cls(name=name, values=values)

    @classmethod
    def builtin(cls, name: str = "Standard") -> "Strings":
        path = ASSETS / "lang" / f"{name}.lng"
        if not path.exists():
            path = ASSETS / "lang" / "Standard.lng"
        return cls.from_file(path)

    @staticmethod
    def list_builtin() -> List[str]:
        return sorted(p.stem for p in (ASSETS / "lang").glob("*.lng"))

    def __getitem__(self, key: str) -> str:
        if key in self.values:
            return self.values[key]
        alias = self.ALIASES.get(key)
        if alias and alias in self.values:
            return self.values[alias]
        return self.DEFAULTS.get(key, key.upper())

    def get(self, key: str, default: str | None = None) -> str:
        if key in self.values:
            return self.values[key]
        alias = self.ALIASES.get(key)
        if alias and alias in self.values:
            return self.values[alias]
        if default is not None:
            return default
        return self.DEFAULTS.get(key, key.upper())


_FONT_REGISTERED = False

#: The interface faces that ship in ``assets/fonts``.
#:
#: ``scale`` evens out how big the capitals look. Pango sizes a face by its
#: em, and the two faces put very different cap heights inside one: VT323's
#: capitals are 56% of the em where the LCARS face's are 73%. Every layout in
#: this shell sizes text as a fraction of a bar's height, so without the scale
#: switching faces would quietly shrink every label on every screen.
FACES: Dict[str, Dict[str, object]] = {
    # EARTH OS: the green-phosphor CRT lettering of a late-80s dashboard.
    "VT323": {"file": "VT323-Regular.ttf", "scale": 1.15,
              "fallbacks": "LCARS, DejaVu Sans Mono, monospace"},
    # The original LCARS x32 face, kept so the old look is one setting away.
    "LCARS": {"file": "lcars.ttf", "scale": 1.0,
              "fallbacks": ("Swiss911 UCm BT, Antonio, Oswald, "
                            "DejaVu Sans Condensed, sans-serif")},
}

#: What the shell draws in unless ``[shell] font`` says otherwise.
DEFAULT_FACE = "VT323"

_FACE = DEFAULT_FACE


def ensure_font() -> str:
    """Make the bundled faces available to Pango; return the family string.

    Every face in :data:`FACES` is registered, not only the active one, so
    switching face in Settings is a redraw rather than a restart. If
    registration fails (no fontconfig, read-only home) Pango falls through
    the family list to a system face that keeps the layout honest.
    """
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return font_family()
    try:
        import ctypes
        import ctypes.util

        lib = ctypes.util.find_library("fontconfig")
        if lib:
            fc = ctypes.CDLL(lib)
            fc.FcConfigAppFontAddFile.restype = ctypes.c_int
            fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            for face in FACES.values():
                path = str(ASSETS / "fonts" / str(face["file"])).encode()
                fc.FcConfigAppFontAddFile(None, path)
    except Exception:
        pass
    _FONT_REGISTERED = True
    return font_family()


def set_face(name: str) -> str:
    """Choose the interface face by its :data:`FACES` key; return the one used.

    An unknown name keeps the default rather than failing: a typo in
    ``config.toml`` should cost a font, not the shell.
    """
    global _FACE
    _FACE = name if name in FACES else DEFAULT_FACE
    return _FACE


def face() -> str:
    """The key of the face currently in use."""
    return _FACE


def font_family() -> str:
    """Family string Pango should be asked for, with fallbacks appended."""
    return f"{_FACE}, {FACES[_FACE]['fallbacks']}"


def font_scale(family: str | None = None) -> float:
    """How much to enlarge text set in *family* so its capitals match.

    *family* is a Pango family string as passed to ``draw_text``; anything
    that is not one of ours (the terminal's monospace, say) is left at 1.
    """
    family = family if family is not None else font_family()
    first = family.split(",", 1)[0].strip()
    spec = FACES.get(first)
    return float(spec["scale"]) if spec else 1.0
