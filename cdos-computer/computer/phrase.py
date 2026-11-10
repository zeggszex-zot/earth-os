"""Level 1 wording, ported from cdos-web/js/phrase.js.

Deterministic templates over a fact's own words.  They change case, grammar
and grouping, never the subject, relation or value, so a Level 1 answer
cannot state anything that isn't in the corpus.
"""

from __future__ import annotations

import re

CONCEPT_RELS = {"IS_A", "CATEGORY", "CLASS", "ATTRIBUTE", "DIET", "HYPERNYM", "HYPONYM", "PARITY", "SYNONYM",
                "ANTONYM", "WN_ANTONYM", "WN_SIMILAR_TO", "DERIVATION", "PHASE", "STATE"}
VERB_START = re.compile(r"^(HAS|IS|ORBITS|LOCATED|PART|MEMBER|NAMED|FOUNDED_BY|CONTAINS|BORDERS|FLOWS|SHARES|USES|PRODUCES|CONVERTS|WN_)")
VERB_END = re.compile(r"_(OF|IN|AT|BY|TO|FROM|WITH|ON)$")
UNIT_WORDS = {"KM": "kilometres", "KG": "kilograms", "M": "metres", "CM": "centimetres", "MM": "millimetres",
              "KM2": "square kilometres", "M2": "square metres", "M_PER_S2": "metres per second squared",
              "KG_PER_M3": "kilograms per cubic metre", "HOURS": "hours", "DAYS": "days", "YEARS": "years",
              "MILLION_KM": "million kilometres", "C": "degrees Celsius", "K": "kelvin", "F": "degrees Fahrenheit",
              "W": "watts", "KW": "kilowatts", "MW": "megawatts", "WH": "watt-hours", "KWH": "kilowatt-hours",
              "MAH": "milliamp-hours", "AH": "amp-hours", "V": "volts", "MV": "millivolts", "A": "amps",
              "MA": "milliamps"}
# (Units are spelled out: the computer speaks, and "km²" reads badly aloud.)
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
DATE_VERBS = {"FOUNDED": "was founded", "DISSOLVED": "was dissolved", "BORN": "was born", "DIED": "died",
              "INCEPTION": "began", "FIRST_RELEASED": "was first released", "PUBLICATION_DATE": "was published",
              "OCCURRED_IN": "happened"}


def is_name(s: str) -> bool:
    # A corpus key: upper case, any script (AIGUILLE_DE_LA_GRANDE_SASSIÈRE is a key, not a sentence).
    return bool(re.fullmatch(r"[\w'.\-]+", s)) and not any(ch.islower() for ch in s)


def name_of(key: str) -> str:
    if not is_name(key):
        return key
    return " ".join(w.upper() if len(w) <= 2 and re.search(r"\d", w) else w[:1].upper() + w[1:]
                    for w in key.lower().split("_"))


def value_of(f) -> str:
    v = f.value
    if not is_name(v):
        return v
    if f.relation in CONCEPT_RELS:
        return v.lower().replace("_", " ")
    return name_of(v)


def unit_of(u: str) -> str:
    if not u:
        return ""
    if u in UNIT_WORDS:
        return UNIT_WORDS[u]
    return "" if re.fullmatch(r"WD_Q\d+", u) else u.lower().replace("_per_", " per ").replace("_", " ")


def rel_text(rel: str) -> str:
    return re.sub(r"^wn_", "", rel.lower()).replace("_", " ")


def article(w: str) -> str:
    return "an" if re.match(r"[aeiou]", w, re.I) else "a"


def listing(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def when(f) -> str:
    if f.start is not None and f.until is not None:
        return f" (from {f.start} until {f.until})"
    if f.start is not None:
        return f" (since {f.start})"
    if f.until is not None:
        return f" (until {f.until})"
    return ""


def big(n: int) -> str:
    """4527000000 -> '4.53 billion', 400000000 -> '400 million'."""
    for size, word in ((10 ** 9, "billion"), (10 ** 6, "million")):
        if n >= size:
            return f"{float(f'{n / size:.3g}'):g} {word}"
    return f"{n:,}"


def date_text(v: str) -> str:
    """'1879-03-14' -> '14 March 1879'; '-753' -> '753 BC'; '-4527000000' -> 'about 4.53 billion years ago'
    (a geological date: as a year it would read as 4527 BC)."""
    m = re.match(r"(-?)(\d+)(?:-(\d{2}))?(?:-(\d{2}))?", v.strip())
    if not m:
        return v
    y = int(m.group(2))
    if y >= 100_000:
        return f"about {big(y)} years {'ago' if m.group(1) else 'from now'}"
    year = f"{y:,}{' BC' if m.group(1) else ''}" if y >= 10_000 else f"{y}{' BC' if m.group(1) else ''}"
    if not m.group(3) or m.group(3) == "00":
        return year
    month = MONTHS[int(m.group(3)) - 1] if 1 <= int(m.group(3)) <= 12 else ""
    return f"{int(m.group(4))} {month} {year}" if m.group(4) and m.group(4) != "00" else f"{month} {year}"


def num(v: str) -> str:
    return f"{int(v):,}" if re.fullmatch(r"-?\d{5,}", v) else v


def dedupe(facts):
    by = {}
    for f in facts:
        k = f.kind + ":" + value_of(f).lower()
        cur = by.get(k)
        if cur is None or (cur.start is None and cur.until is None and (f.start is not None or f.until is not None)):
            by[k] = f
    return list(by.values())


def clean_definition(d: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r';\s*"[^"]*"', "", d)).strip()


def _uniq(seq):
    return list(dict.fromkeys(seq))


def sentence(subject: str, rel: str, facts) -> str:
    S = name_of(subject)
    if rel != "DEFINITION":
        facts = dedupe(facts)
    f0 = facts[0]
    if rel == "DEFINITION":
        defs = _uniq(clean_definition(f.value) for f in facts)[:3]
        return f"{S}: {defs[0]}." if len(defs) == 1 else f"{S} can mean: {'; '.join(defs)}."
    if rel == "IS_A":
        return f"{S} is {listing(_uniq(f'{article(value_of(f))} {value_of(f)}{when(f)}' for f in facts))}."
    if rel in ("SYNONYM", "WN_SIMILAR_TO"):
        return f"Other words for {S.lower()}: {listing(_uniq(value_of(f) for f in facts)[:8])}."
    if rel in ("WN_ANTONYM", "ANTONYM"):
        return f"The opposite of {S.lower()} is {listing(_uniq(value_of(f) for f in facts)[:4])}."
    if rel == "HYPERNYM":
        return f"{S} is a kind of {listing(_uniq(value_of(f) for f in facts)[:6])}."
    if rel == "HYPONYM":
        return f"Kinds of {S.lower()} include {listing(_uniq(value_of(f) for f in facts)[:10])}."
    if rel == "DERIVATION":
        return f"Words related to {S.lower()}: {listing(_uniq(value_of(f) for f in facts)[:8])}."
    if rel == "EXAMPLE":
        return "For example: " + "; ".join(f'"{v}"' for v in _uniq(f.value.strip().strip('"') for f in facts)[:3]) + "."
    if f0.kind == "D":
        dates = [date_text(f.value) for f in facts[:3]]
        verb = DATE_VERBS.get(rel)
        if verb:
            prep = "" if dates[0].startswith("about ") else "on " if len(dates) > 1 or re.match(r"\d+ \w+ ", dates[0]) else "in "
            return f"{S} {verb} {prep}{', or '.join(dates)}."
        return f"{S}'s {rel_text(rel)} is {', or '.join(dates)}."
    if f0.kind == "N":
        if len(facts) > 1 and all(f.start is not None for f in facts):
            facts = [max(facts, key=lambda f: f.start)]
        parts = [f"{num(f.value)}{' ' + unit_of(f.unit) if unit_of(f.unit) else ''}{when(f)}" for f in facts[:3]]
        if len({f.qual for f in facts[:3]}) > 1:
            # Values with different qualifiers (idle 3 W, load 6.4 W): each value keeps its own, so a qualifier is
            # never said of the wrong value.
            parts = [p + (f" ({f.qual.lower().replace('=', ' ').replace('_', ' ')})" if f.qual else "")
                     for p, f in zip(parts, facts[:3])]
            return f"{S}'s {rel_text(rel)} is {', or '.join(parts)}."
        ref = re.match(r"REFERENCE=(.+)$", f0.qual or "", re.I)
        rw = rel_text(rel)
        qual = f" ({f0.qual.lower().replace('=', ' ').replace('_', ' ')})" if f0.qual else ""
        if ref and VERB_END.search(rel):
            rw, qual = f"{rw} {name_of(ref.group(1))}", ""
        elif ref:
            rw, qual = f"{rw} (relative to {name_of(ref.group(1))})", ""
        return f"{S}'s {rw} is {', or '.join(parts)}{qual}."
    uniq = _uniq(value_of(f) + when(f) for f in facts if f.value != subject)[:10]
    if not uniq:
        return ""
    vals = listing(uniq)
    if re.match(r"^(LOCATED|PART_OF|CAPITAL_OF|MEMBER_OF|NAMED_AFTER|MADE_OF|COMPOSED_OF|LOCATED_AT)", rel):
        return f"{S} is {rel_text(rel)} {vals}."
    if VERB_START.search(rel) or VERB_END.search(rel):
        return f"{S} {rel_text(rel)} {vals}."
    return f"{S}'s {rel_text(rel)}s are {vals}." if len(uniq) > 1 else f"{S}'s {rel_text(rel)} is {vals}."


def phrase_block(b: dict) -> str:
    if b.get("reverse"):
        names = [name_of(n) for n in b["names"]]
        verb = rel_text(b["reverse"])
        if len(names) > 1:
            def plural(m):
                w, end = m.group(1), m.group(2)
                whole = m.group(0)
                if re.search(r"(ss|us)$", whole):
                    return whole
                if end == "es" and re.search(r"(sh|ch|x|z)$", w):
                    return w
                return whole[:-1]
            verb = re.sub(r"^(\w+?)(e?s)\b", plural, verb, count=1)
            verb = re.sub(r"^is\b", "are", verb)
            verb = re.sub(r"^has\b", "have", verb)
        shown = names[:15] + [f"{len(names) - 15} more"] if len(names) > 15 else names
        return f"{listing(shown)} {verb} {name_of(b['subject'])}."
    facts = b["facts"][: b.get("nwanted") or 4]
    by_rel: dict[str, list] = {}
    for f in facts:
        by_rel.setdefault(f.relation, []).append(f)
    return " ".join(s for s in (sentence(b["subject"], r, fs) for r, fs in by_rel.items()) if s)
