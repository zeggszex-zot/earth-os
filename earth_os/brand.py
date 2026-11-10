"""What this machine calls itself.

EARTH OS is the name on the glass, the package, the command, the session and
the application id. CDOS is the engine inside it — the capability layer and
the deterministic phrase table — and keeps its own name, as an engine does.

Kept in a module of its own with no imports, because the boot splash reads
the name before anything else has loaded and must not pay for anything to
do it.
"""

#: The name on the screen, in the title bar and in the window list.
NAME = "EARTH OS"

#: The version shown in the title cap: major.minor of ``earth_os.__version__``.
VERSION = "2.0"

#: Window titles this shell answers to. Window backends that report no
#: application id are matched on title instead. "CDOS" is deliberately not
#: here: CDOS LCARS may be running beside EARTH OS, and its window is not ours.
TITLES = frozenset({NAME})


def cap() -> str:
    """The label in the title bar's end cap."""
    return f"V{VERSION}"
