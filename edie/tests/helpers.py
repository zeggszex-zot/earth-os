import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "thompson-mini"
FIXTURE = FIXTURE_DIR / "mini.tex"


def line_of(text: str, needle: str, nth: int = 1) -> int:
    """The 1-based line of the nth occurrence of needle, counted independently of EDIE."""
    i = -1
    for _ in range(nth):
        i = text.index(needle, i + 1)
    return text.count("\n", 0, i) + 1
