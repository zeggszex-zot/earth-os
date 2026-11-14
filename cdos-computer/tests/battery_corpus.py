"""The Milestone 1 battery fixture (docs/cdos-design-capability-acceptance.md §7.1), in the real shard format.

All names are fictional.  The fixture is complete by construction: for every case but Delta, the facts needed are
present, so a wrong or missing answer is an engine defect.  Delta is the designed knowledge gap.

    BATTERY_ALPHA    ENERGY_CAPACITY 0.052 KWH            52 Wh: meets 50 Wh, through an exact conversion
    BATTERY_BETA     RATED_CHARGE 3.5 AH, NOMINAL_VOLTAGE 12 V     3.5 Ah x 12 V = 42 Wh: doesn't
    BATTERY_DELTA    (no capacity, no charge)              the gap: UNKNOWN
    BATTERY_GAMMA    ENERGY_CAPACITY 2.0 KG                a unit of the wrong dimension
    BATTERY_EPSILON  ENERGY_CAPACITY 40 WH and 60 WH       two undated values: CONFLICT under functional.energy_capacity

EXTRA holds the cases for the functional rule's own tests (dated values, one value in two units, two relations,
overlapping open-ended dates).  build(path) writes FACTS only; build(path, FACTS + EXTRA) writes both.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mini_corpus  # noqa: E402


def _f(s, r, k, v, u="", start=None, until=None):
    return (s, r, k, v, u, start, until, "MANUAL", 1.0, "")


FACTS = [
    _f("BATTERY_ALPHA", "IS_A", "S", "BATTERY"),
    _f("BATTERY_ALPHA", "ENERGY_CAPACITY", "N", "0.052", "KWH"),
    _f("BATTERY_BETA", "IS_A", "S", "BATTERY"),
    _f("BATTERY_BETA", "RATED_CHARGE", "N", "3.5", "AH"),
    _f("BATTERY_BETA", "NOMINAL_VOLTAGE", "N", "12", "V"),
    _f("BATTERY_DELTA", "IS_A", "S", "BATTERY"),
    _f("BATTERY_GAMMA", "IS_A", "S", "BATTERY"),
    _f("BATTERY_GAMMA", "ENERGY_CAPACITY", "N", "2.0", "KG"),
    _f("BATTERY_EPSILON", "IS_A", "S", "BATTERY"),
    _f("BATTERY_EPSILON", "ENERGY_CAPACITY", "N", "40", "WH"),
    _f("BATTERY_EPSILON", "ENERGY_CAPACITY", "N", "60", "WH"),
]

EXTRA = [
    # A replacement: 40 Wh until 2020, 60 Wh from 2020.  One value applies in any year: no conflict.
    _f("BATTERY_ZETA", "IS_A", "S", "BATTERY"),
    _f("BATTERY_ZETA", "ENERGY_CAPACITY", "N", "40", "WH", until=2020),
    _f("BATTERY_ZETA", "ENERGY_CAPACITY", "N", "60", "WH", start=2020),
    # One value said twice, in two units: not a conflict.
    _f("BATTERY_ETA", "IS_A", "S", "BATTERY"),
    _f("BATTERY_ETA", "ENERGY_CAPACITY", "N", "0.05", "KWH"),
    _f("BATTERY_ETA", "ENERGY_CAPACITY", "N", "50", "WH"),
    # Two relations (rated and usable): different things, never a conflict with each other.
    _f("BATTERY_THETA", "IS_A", "S", "BATTERY"),
    _f("BATTERY_THETA", "ENERGY_CAPACITY", "N", "52", "WH"),
    _f("BATTERY_THETA", "USABLE_ENERGY", "N", "48", "WH"),
    # Both dated, both still open: both apply now, so it is a conflict.
    _f("BATTERY_IOTA", "IS_A", "S", "BATTERY"),
    _f("BATTERY_IOTA", "ENERGY_CAPACITY", "N", "40", "WH", start=2018),
    _f("BATTERY_IOTA", "ENERGY_CAPACITY", "N", "60", "WH", start=2021),
]


def build(path, facts=None) -> Path:
    return mini_corpus.build(path, FACTS if facts is None else facts)
