"""Quantities: numbers with units, read from what a person said or from a corpus fact.

Deterministic and small.  A quantity has a dimension, a value in that dimension's base unit, the words
it was said with, and where it came from (provenance), so an answer built from it can say why.

    dimension   base unit    said as
    energy      watt-hour    Wh, kWh, mWh, J, kJ, watt-hours
    charge      amp-hour     mAh, Ah, milliamp-hours
    voltage     volt         V, mV, kV, volts
    current     amp          A (capital, or "amps"), mA, milliamps
    power       watt         W, kW, mW, watts
    time        hour         h, hr, min, minutes, days, weeks
    fraction    1            %, percent
    + length, mass, area for corpus facts (KM, M, KG ...), so CALC can convert between them

    parse("a 10000 mAh battery at 3.7 volts")  -> [10 Ah (charge), 3.7 V (voltage)]
    parse("a fifty watt hour battery")         -> [50 Wh (energy)]     (number words, for voice input)

Nothing here guesses a unit.  A bare number is not a quantity.
"""

from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass, field

# unit spelling -> (dimension, factor to the base unit).  Longest spellings are tried first.
UNITS: dict[str, tuple[str, float]] = {}
_SPELL = {
    "energy": [(("kilowatt-hours", "kilowatt-hour", "kilowatt hours", "kilowatt hour", "kwh"), 1000.0),
               (("watt-hours", "watt-hour", "watt hours", "watt hour", "watthours", "wh"), 1.0),
               (("milliwatt-hours", "milliwatt hours", "mwh"), 0.001),
               (("kilojoules", "kilojoule", "kj"), 1 / 3.6), (("joules", "joule", "j"), 1 / 3600)],
    "charge": [(("milliamp-hours", "milliamp-hour", "milliamp hours", "milliamp hour", "milliampere-hours",
                 "milliampere hours", "mah"), 0.001),
               (("amp-hours", "amp-hour", "amp hours", "amp hour", "ampere-hours", "ampere hours", "ah"), 1.0)],
    "voltage": [(("kilovolts", "kilovolt", "kv"), 1000.0), (("millivolts", "millivolt", "mv"), 0.001),
                (("volts", "volt", "v"), 1.0)],
    "current": [(("milliamps", "milliamp", "milliamperes", "milliampere", "ma"), 0.001),
                (("amperes", "ampere", "amps", "amp", "A"), 1.0)],
    "power": [(("kilowatts", "kilowatt", "kw"), 1000.0), (("milliwatts", "milliwatt", "mw"), 0.001),
              (("watts", "watt", "w"), 1.0)],
    "time": [(("hours", "hour", "hrs", "hr", "h"), 1.0), (("minutes", "minute", "mins", "min"), 1 / 60),
             (("days", "day"), 24.0), (("weeks", "week"), 168.0)],
    "fraction": [(("percent", "per cent", "%"), 0.01)],
    # Lengths, masses and areas: the dimensions the corpus has figures in (KM, M, KG, KM2), so a stated requirement
    # ("is the Nile at least 6000 km long") can be checked against them.  Base units: metre, kilogram, square metre.
    "length": [(("kilometres", "kilometre", "kilometers", "kilometer", "km"), 1000.0),
               (("metres", "metre", "meters", "meter", "m"), 1.0),
               (("centimetres", "centimetre", "centimeters", "centimeter", "cm"), 0.01),
               (("millimetres", "millimetre", "millimeters", "millimeter", "mm"), 0.001),
               (("miles", "mile"), 1609.344), (("feet", "foot", "ft"), 0.3048)],
    "mass": [(("kilograms", "kilogram", "kg"), 1.0), (("grams", "gram"), 0.001), (("tonnes", "tonne"), 1000.0),
             (("lbs", "lb"), 0.45359237)],
    "area": [(("square kilometres", "square kilometre", "square kilometers", "square kilometer", "sq km", "km2",
               "km\u00b2"), 1e6),
             (("square metres", "square metre", "square meters", "square meter", "m2", "m\u00b2"), 1.0)],
}
ELECTRICAL_DIMS = {"energy", "charge", "voltage", "current", "power"}
for _dim, _rows in _SPELL.items():
    for _names, _f in _rows:
        for _n in _names:
            UNITS[_n] = (_dim, _f)
CASED = {"A": ("current", 1.0), "MW": ("power", 1e6), "MWh": ("energy", 1e6), "mW": ("power", 0.001),
         "mWh": ("energy", 0.001)}

# Corpus unit codes (the shards' unit column) -> (dimension, factor).  Unknown codes compare only to themselves.
CORPUS_UNITS = {
    "WH": ("energy", 1.0), "KWH": ("energy", 1000.0), "MWH": ("energy", 1e6), "J": ("energy", 1 / 3600),
    "MAH": ("charge", 0.001), "AH": ("charge", 1.0), "V": ("voltage", 1.0), "MV": ("voltage", 0.001),
    "A": ("current", 1.0), "MA": ("current", 0.001), "W": ("power", 1.0), "KW": ("power", 1000.0),
    "MW": ("power", 1e6),           # corpus units are upper-case: MW is a megawatt (power stations), not mW
    "M": ("length", 1.0), "KM": ("length", 1000.0), "CM": ("length", 0.01), "MM": ("length", 0.001),
    "MILLION_KM": ("length", 1e9), "KG": ("mass", 1.0), "G": ("mass", 0.001), "T": ("mass", 1000.0),
    "M2": ("area", 1.0), "KM2": ("area", 1e6), "HOURS": ("time", 1.0), "DAYS": ("time", 24.0),
    "YEARS": ("time", 8766.0),
}

SMALL = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                       "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
NUMWORD = "|".join(sorted(list(SMALL) + list(TENS) + list(SCALES), key=len, reverse=True))

_unit_alt = "|".join(re.escape(u) for u in sorted(set(UNITS) | set(CASED), key=len, reverse=True))
_NUM = r"(?P<num>\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
QTY = re.compile(_NUM + r"\s*-?\s*(?P<unit>" + _unit_alt + r")(?![A-Za-z])", re.I)
# A schedule: "24 hours a day", "8 h per day", "3 hours daily".
PER_DAY = re.compile(r"^\s*(?:a|per|each|every|/)\s*day\b|^\s*daily\b", re.I)
WORDNUM = re.compile(r"\b(?P<run>(?:" + NUMWORD + r")(?:[\s-]+(?:" + NUMWORD + r"|and|point|a half))*)\b"
                     r"(?=[\s-]*(?:" + _unit_alt + r")(?![A-Za-z]))", re.I)


@dataclass
class Quantity:
    dim: str                    # energy | charge | voltage | current | power | time | fraction | length ...
    value: float                # in the dimension's base unit (Wh, Ah, V, A, W, h, 1)
    text: str = ""              # how it was said or stored ("10000 mAh", "POWER_DRAW 2.5 W")
    source: str = "stated"      # stated | corpus | assumed | previous | derived | answer
    fact: dict | None = None    # for source="corpus": the fact it came from (id, subject, relation, value, unit ...)
    per_day: bool = False       # a schedule ("24 hours a day"), not a duration
    pos: int = 0                # where it was said in the text
    note: str = ""              # e.g. "assuming the load runs at the battery's 12 volts"
    parts: list = field(default_factory=list)   # quantities it was derived from (Ah x V -> Wh)
    next_word: str = ""         # the word said after it ("of" in "15% of 80"); not saved

    def to_json(self) -> dict:
        d = {k: getattr(self, k) for k in ("dim", "value", "text", "source", "fact", "per_day", "note")}
        d["parts"] = [p.to_json() for p in self.parts]
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Quantity":
        q = cls(**{k: d.get(k) for k in ("dim", "value", "text", "source", "fact")},
                per_day=bool(d.get("per_day")), note=d.get("note") or "")
        q.parts = [cls.from_json(p) for p in d.get("parts") or []]
        return q

    def scaled(self, k: float, source: str = "previous") -> "Quantity":
        return Quantity(self.dim, self.value * k, self.text, source, self.fact, self.per_day, self.pos, self.note,
                        list(self.parts))


def words_to_number(run: str) -> float | None:
    """'fifty' -> 50, 'ten thousand' -> 10000, 'three point seven' -> 3.7, 'two and a half' -> 2.5."""
    toks = [t for t in re.split(r"[\s-]+", run.lower().strip()) if t]
    half = False
    if toks[-2:] == ["a", "half"]:
        toks, half = toks[:-2], True
        if toks and toks[-1] == "and":
            toks = toks[:-1]
    if "point" in toks:
        i = toks.index("point")
        whole, frac = toks[:i], toks[i + 1:]
        if not frac or any(t not in SMALL or SMALL[t] > 9 for t in frac):
            return None
        w = words_to_number(" ".join(whole)) if whole else 0.0
        return None if w is None else w + float("0." + "".join(str(SMALL[t]) for t in frac))
    total = current = 0
    seen = False
    for t in toks:
        if t == "and":
            continue
        if t in SMALL:
            current += SMALL[t]
        elif t in TENS:
            current += TENS[t]
        elif t == "hundred":
            current = (current or 1) * 100
        elif t in SCALES:
            total += (current or 1) * SCALES[t]
            current = 0
        else:
            return None
        seen = True
    if not seen:
        return 0.5 if half else None
    return total + current + (0.5 if half else 0)


def spell_out(text: str) -> str:
    """Number words in front of a unit become digits ('fifty watt hours' -> '50 watt hours').  Only before a
    unit, so 'another one' or 'one more' are left alone."""
    def rep(m):
        n = words_to_number(m["run"])
        return m["run"] if n is None else fmt_plain(n)
    return WORDNUM.sub(rep, text)


def parse(text: str) -> list[Quantity]:
    """Every quantity said in `text`, in order."""
    s = spell_out(text)
    out = []
    for m in QTY.finditer(s):
        raw = m["unit"]
        dim, f = CASED.get(raw) or UNITS.get(raw.lower(), (None, 0.0))
        if raw == "a":                      # "2 a day": an article, not amps (amps need a capital A or "amps")
            continue
        if raw.lower() == "a" and raw != "A":
            continue
        if dim is None:
            continue
        v = float(m["num"].replace(",", ""))
        per_day = dim == "time" and bool(PER_DAY.match(s[m.end():]))
        nxt = re.match(r"\s*([a-z]+)", s[m.end():], re.I)
        out.append(Quantity(dim, v * f, f"{m['num']} {raw}", "stated", per_day=per_day, pos=m.start(),
                            next_word=nxt.group(1).lower() if nxt else ""))
    return out


BASE_UNITS = {"energy": "Wh", "charge": "Ah", "voltage": "V", "current": "A", "power": "W", "time": "h", "fraction": ""}


def unit_dimension(unit: str) -> str | None:
    """'Wh' -> 'energy', 'KWH' -> 'energy', 'kg' -> None (a corpus mass unit, not a stated one)."""
    got = CASED.get(unit) or UNITS.get(unit.lower()) or CORPUS_UNITS.get(unit.upper())
    return got[0] if got else None


def unit_factor(unit: str) -> float:
    got = CASED.get(unit) or UNITS.get(unit.lower()) or CORPUS_UNITS.get(unit.upper())
    return got[1]


def from_fact(f) -> Quantity | None:
    """A numeric corpus fact with a unit this module knows, as a quantity (provenance kept)."""
    if f.kind != "N" or f.unit.upper() not in CORPUS_UNITS:
        return None
    try:
        v = float(f.value.replace(",", ""))
    except ValueError:
        return None
    dim, k = CORPUS_UNITS[f.unit.upper()]
    return Quantity(dim, v * k, f"{f.value} {f.unit}", "corpus", fact=fact_record(f))


def fact_record(f) -> dict:
    return {"id": f.id, "subject": f.subject, "relation": f.relation, "value": f.value, "unit": f.unit,
            "source": f.source, "conf": f.conf, "qual": f.qual, "start": f.start, "until": f.until}


def same_dimension(unit_a: str, unit_b: str) -> tuple[float, float] | None:
    """Factors that put two corpus units on one scale, or None if they measure different things."""
    a, b = (unit_a or "").upper(), (unit_b or "").upper()
    if a == b:
        return 1.0, 1.0
    if a in CORPUS_UNITS and b in CORPUS_UNITS and CORPUS_UNITS[a][0] == CORPUS_UNITS[b][0]:
        return CORPUS_UNITS[a][1], CORPUS_UNITS[b][1]
    return None


# ---------------------------------------------------------------------- saying numbers

def fmt_plain(x: float) -> str:
    """A number with no grouping, for re-parsing: 50, 3.7, 10000."""
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def fmt_num(x: float, sig: int = 3) -> str:
    """For speech: whole numbers in full with commas (152,000,000), others to `sig` significant figures."""
    if not math.isfinite(x):
        return str(x)
    if abs(x) >= 1e15:
        return f"{x:.{sig}g}".replace("e+", "e")
    if float(x).is_integer():
        return f"{int(x):,}"
    if abs(x) >= 10 ** sig:
        return f"{round(x):,}"
    s = f"{float(f'{x:.{sig}g}'):f}".rstrip("0").rstrip(".")
    return s if "." in s else f"{int(float(s)):,}"


def _unit(n: float, one: str, many: str) -> str:
    return f"{n} {one if n in ('1', '1.0') else many}"


def fmt_energy(wh: float) -> str:
    if abs(wh) >= 1000:
        return _unit(fmt_num(wh / 1000), "kilowatt-hour", "kilowatt-hours")
    if 0 < abs(wh) < 1:
        return _unit(fmt_num(wh * 1000), "milliwatt-hour", "milliwatt-hours")
    return _unit(fmt_num(wh), "watt-hour", "watt-hours")


def fmt_power(w: float) -> str:
    if abs(w) >= 1000:
        return _unit(fmt_num(w / 1000), "kilowatt", "kilowatts")
    if 0 < abs(w) < 1:
        return _unit(fmt_num(w * 1000), "milliwatt", "milliwatts")
    return _unit(fmt_num(w), "watt", "watts")


def fmt_charge(ah: float) -> str:
    if abs(ah) < 1:
        return _unit(fmt_num(ah * 1000), "milliamp-hour", "milliamp-hours")
    return _unit(fmt_num(ah), "amp-hour", "amp-hours")


def fmt_voltage(v: float) -> str:
    return _unit(fmt_num(v), "volt", "volts")


def fmt_current(a: float) -> str:
    if 0 < abs(a) < 1:
        return _unit(fmt_num(a * 1000), "milliamp", "milliamps")
    return _unit(fmt_num(a), "amp", "amps")


def round_half_up(x: float, places: int = 0) -> float:
    """4.25 -> 4.3, as a person rounds (Python's round() gives 4.2: half to even, on a binary float)."""
    q = Decimal(1).scaleb(-places)
    return float(Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP))


def fmt_hours(h: float) -> str:
    """A duration: minutes under an hour, one decimal under 100 hours, and days alongside from 48 hours."""
    if h < 1:
        m = max(1, int(round_half_up(h * 60)))
        return _unit(str(m), "minute", "minutes")
    s = f"{round_half_up(h, 1):g}" if h < 100 else f"{int(round_half_up(h)):,}"
    out = _unit(s, "hour", "hours")
    if h >= 48:
        out += f" (about {round_half_up(h / 24, 1):g} days)"
    return out


def fmt_length(m: float) -> str:
    if abs(m) >= 10_000:                               # 8,849 metres for a mountain; 6,650 kilometres for a river
        return _unit(fmt_num(m / 1000), "kilometre", "kilometres")
    return _unit(fmt_num(m), "metre", "metres")


def fmt_mass(kg: float) -> str:
    return _unit(fmt_num(kg), "kilogram", "kilograms")


def fmt_area(m2: float) -> str:
    if abs(m2) >= 1e6:
        return _unit(fmt_num(m2 / 1e6), "square kilometre", "square kilometres")
    return _unit(fmt_num(m2), "square metre", "square metres")


def fmt_percent(f: float) -> str:
    return f"{fmt_num(f * 100)}%"


FORMAT = {"energy": fmt_energy, "power": fmt_power, "charge": fmt_charge, "voltage": fmt_voltage,
          "current": fmt_current, "time": fmt_hours, "fraction": fmt_percent, "length": fmt_length, "mass": fmt_mass,
          "area": fmt_area}


def say(q: Quantity) -> str:
    if q.dim == "charge" and re.search(r"m(?:a|illiamp)", q.text, re.I):     # said in mAh: keep saying it in mAh
        return _unit(fmt_num(q.value * 1000), "milliamp-hour", "milliamp-hours")
    if q.dim == "time" and q.per_day:
        return f"{fmt_num(q.value)} hours a day"
    return FORMAT.get(q.dim, lambda v: fmt_num(v))(q.value)
