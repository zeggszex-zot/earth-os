"""The I Ching engine: the computer's random SAMPLER (non-factual; never evidence).

Casting is real chance, done the traditional way:

  coins   three coins per line (heads 3, tails 2):   6 = 1/8, 7 = 3/8, 8 = 3/8, 9 = 1/8
  yarrow  the yarrow-stalk odds:                      6 = 1/16, 7 = 5/16, 8 = 7/16, 9 = 3/16

  6 old yin (changing)   7 young yang   8 young yin   9 old yang (changing)

Lines are built bottom to top.  The changing lines flip to give the
*relating* hexagram: where the situation is heading.

Chance comes from the Pi's hardware random generator (/dev/hwrng) when it is
there, otherwise the OS's secure generator, mixed with the question and the
exact moment of the cast.  `seed=` makes a cast reproducible (tests, replays).

This module only casts and looks things up.  It never decides what is true.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path(__file__).parent / "data" / "iching.json"

# King Wen number by [lower trigram][upper trigram].  Trigram keys are lines
# bottom->top, 1 = yang:  111 Heaven, 100 Thunder, 010 Water, 001 Mountain,
# 000 Earth, 011 Wind, 101 Fire, 110 Lake.
_ORDER = ["111", "100", "010", "001", "000", "011", "101", "110"]
_KING_WEN = [
    # upper:  Heaven Thunder Water Mountain Earth Wind Fire Lake      lower:
    [1, 34, 5, 26, 11, 9, 14, 43],     # Heaven
    [25, 51, 3, 27, 24, 42, 21, 17],   # Thunder
    [6, 40, 29, 4, 7, 59, 64, 47],     # Water
    [33, 62, 39, 52, 15, 53, 56, 31],  # Mountain
    [12, 16, 8, 23, 2, 20, 35, 45],    # Earth
    [44, 32, 48, 18, 46, 57, 50, 28],  # Wind
    [13, 55, 63, 22, 36, 37, 30, 49],  # Fire
    [10, 54, 60, 41, 19, 61, 38, 58],  # Lake
]
KING_WEN = {(lo, up): _KING_WEN[i][j] for i, lo in enumerate(_ORDER) for j, up in enumerate(_ORDER)}
BITS_OF = {n: lo + up for (lo, up), n in KING_WEN.items()}   # 1 -> "111111"

# Out of 16: the chance of 6, 7, 8, 9 for one line.
METHODS = {
    "coins": {6: 2, 7: 6, 8: 6, 9: 2},
    "yarrow": {6: 1, 7: 5, 8: 7, 9: 3},
}

_book = None


def book() -> dict:
    global _book
    if _book is None:
        with open(DATA, encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["by_n"] = {h["n"]: h for h in raw["hexagrams"]}
        _book = raw
    return _book


# --------------------------------------------------------------------------- chance

class Entropy:
    """A stream of random bytes: hardware RNG if present, mixed with the moment."""

    def __init__(self, seed: bytes | str | None = None, extra: bytes | str = b""):
        if isinstance(seed, str):
            seed = seed.encode()
        if isinstance(extra, str):
            extra = extra.encode()
        self.source = "seed" if seed is not None else "os"
        if seed is None:
            hw = self._hw(32)
            if hw:
                self.source = "hwrng"
            seed = hw + secrets.token_bytes(32) + time.perf_counter_ns().to_bytes(8, "little")
        self.key = hashlib.blake2b(seed + b"|" + extra, digest_size=32).digest()
        self.counter = 0

    _hw_cache = (0.0, b"")

    @classmethod
    def _hw(cls, n: int) -> bytes:
        """Hardware random bytes, refreshed at most once a minute and never blocking.

        Some hardware generators are rate-limited; the OS generator and the cast's
        own moment are always mixed in, so every cast still differs.
        """
        when, pool = cls._hw_cache
        if pool and time.monotonic() - when < 60:
            return pool
        try:
            fd = os.open("/dev/hwrng", os.O_RDONLY | os.O_NONBLOCK)
            try:
                pool = os.read(fd, n)
            finally:
                os.close(fd)
        except OSError:
            pool = b""
        cls._hw_cache = (time.monotonic(), pool)
        return pool

    def byte(self) -> int:
        block = hashlib.blake2b(self.counter.to_bytes(8, "little"), key=self.key, digest_size=1).digest()
        self.counter += 1
        return block[0]

    def below(self, n: int) -> int:
        """Uniform integer in [0, n) with no modulo bias."""
        limit = 256 - (256 % n)
        while True:
            b = self.byte()
            if b < limit:
                return b % n

    def choice(self, seq):
        return seq[self.below(len(seq))]

    @property
    def fingerprint(self) -> str:
        return self.key.hex()[:12]


# --------------------------------------------------------------------------- casting

@dataclass
class Hexagram:
    n: int
    bits: str                 # bottom -> top, "1" = yang

    @property
    def info(self) -> dict:
        return book()["by_n"][self.n]

    @property
    def name(self) -> str:
        return self.info["name"]

    @property
    def lower(self) -> dict:
        return book()["trigrams"][self.bits[:3]]

    @property
    def upper(self) -> dict:
        return book()["trigrams"][self.bits[3:]]

    def image(self) -> str:
        """'thunder below, heaven above'"""
        return f"{self.upper['image']} above {self.lower['image']}"

    def glyph(self) -> str:
        """Six lines drawn top to bottom (for logs and screens)."""
        return "\n".join("━━━━━━━" if b == "1" else "━━━ ━━━" for b in reversed(self.bits))

    def nuclear(self) -> "Hexagram":
        """The hidden hexagram inside: lines 2-4 below, 3-5 above."""
        b = self.bits[1:4] + self.bits[2:5]
        return Hexagram(KING_WEN[(b[:3], b[3:])], b)


def hexagram_of(bits: str) -> Hexagram:
    return Hexagram(KING_WEN[(bits[:3], bits[3:])], bits)


def hexagram_n(n: int) -> Hexagram:
    return Hexagram(n, BITS_OF[n])


@dataclass
class Reading:
    lines: list[int]                      # six of 6/7/8/9, bottom -> top
    method: str
    question: str = ""
    entropy: str = ""
    fingerprint: str = ""
    cast_at: float = field(default_factory=time.time)

    @property
    def primary(self) -> Hexagram:
        return hexagram_of("".join("1" if v in (7, 9) else "0" for v in self.lines))

    @property
    def changing(self) -> list[int]:
        """Positions 1..6 of the moving lines."""
        return [i + 1 for i, v in enumerate(self.lines) if v in (6, 9)]

    @property
    def relating(self) -> Hexagram | None:
        if not self.changing:
            return None
        return hexagram_of("".join("1" if v in (7, 6) else "0" for v in self.lines))

    def as_dict(self) -> dict:
        p, r = self.primary, self.relating
        out = {
            "hexagram": p.n, "name": p.name, "image": p.image(),
            "lines": self.lines, "changing_lines": self.changing,
            "theme": p.info["theme"], "stance": p.info["stance"], "counsel": p.info["counsel"],
            "method": self.method, "chance": self.entropy, "fingerprint": self.fingerprint,
        }
        if r:
            out["becoming"] = {"hexagram": r.n, "name": r.name, "image": r.image(),
                               "stance": r.info["stance"], "counsel": r.info["counsel"]}
        return out


def cast(question: str = "", method: str = "coins", seed=None, extra: bytes | str = b"") -> Reading:
    """Cast six lines.  `extra` can carry anything physical, e.g. shake timings."""
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}; use one of {', '.join(METHODS)}")
    rng = Entropy(seed, (question or "").strip().lower().encode() + b"|" +
                  (extra.encode() if isinstance(extra, str) else extra))
    table = [v for v, w in METHODS[method].items() for _ in range(w)]   # 16 slots
    lines = [table[rng.below(16)] for _ in range(6)]
    return Reading(lines, method, question, rng.source, rng.fingerprint)


def draw(seed=None) -> Hexagram:
    """One hexagram, all 64 equally likely (for idle sayings)."""
    return hexagram_n(1 + Entropy(seed).below(64))
