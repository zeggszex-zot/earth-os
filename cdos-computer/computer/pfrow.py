"""One definition of a corpus row: how a PF row is finished into the served form, what makes two rows the same fact,
and what makes a field invalid.  tools/build_shards.py, tools/validate_corpus.py and computer/acquire/ all use it, so
the build, the validator and the importer pipeline can't disagree about what a duplicate or a bad row is.

A PF row (corpus.pf) has 11 tab-separated columns:
    subject relation kind value unit valid_from valid_until source_type source_id confidence qualifiers
"""

from __future__ import annotations

import re

from computer import qualifiers as Q
from computer.corpus import key_of

COLUMNS = ("subject", "relation", "kind", "value", "unit", "valid_from", "valid_until", "source_type", "source_id",
           "confidence", "qualifiers")
KINDS = {"S", "N", "T", "D"}
DATE = re.compile(r"^-?\d+(?:-\d{2}(?:-\d{2})?)?(?:T.*)?$")     # any number of year digits: -4527000000 is a date
DEEP_TIME = re.compile(r"^-?\d{5,}")
YEAR = re.compile(r"^-?\d+$")
TEXT_RELS = {"DEFINITION", "EXAMPLE", "DESCRIPTION"}
SYNSET_ID = re.compile(r"^WN_[a-z]_\d{8}$")


def field_problems(kind: str, value: str, start: str, until: str, conf: str, qual: str = "") -> list[str]:
    """The validator's checks on one row's fields, as the names of the problems found (empty: none)."""
    out = []
    if kind not in KINDS:
        out.append("unknown kind")
    if kind == "N":
        try:
            float(value.replace(",", ""))
        except ValueError:
            out.append("N value isn't a number")
    if kind == "D" and not DATE.match(value.strip()):
        out.append("D value isn't a date")
    for y in (start, until):
        if y and not YEAR.match(y):
            out.append("start/until isn't a year")
    if conf:
        try:
            if not 0 <= float(conf) <= 1:
                out.append("confidence outside 0..1")
        except ValueError:
            out.append("confidence isn't a number")
    if Q.problems(qual):
        out.append("qualifier isn't KEY=VALUE[;KEY=VALUE...]")
    return out


def finish(subj, rel, kind, value, vf, vu, stype, sid, qual, fix_keys):
    """The last steps the build takes on every row: the lookup key, text relations, years, and an OEWN row's sense.
    Returns (subject, kind, start, until, qualifiers); start/until are int or None (0 and "" mean no bound)."""
    if fix_keys:
        subj = key_of(subj)
    if rel in TEXT_RELS:
        kind = "T"
    start = int(vf) if vf not in ("", "0") else None
    until = int(vu) if vu not in ("", "0") else None
    if stype == "OEWN" and SYNSET_ID.match(sid) and not qual:
        qual = f"SENSE={sid}"
    return subj, kind, start, until, qual


def fact_key(subj, rel, kind, value, unit, start, until, qual, fold_values) -> tuple:
    """Two rows with the same key are one fact.  One word with two senses (SENSE=), or one name for two things
    (ENTITY=, Q3), stays two facts."""
    sense, entity = Q.get(qual, "SENSE"), Q.get(qual, "ENTITY")
    return (subj, rel, key_of(value) if fold_values and kind == "S" else value, unit, start, until,
            f"SENSE={sense}" if sense else "", f"ENTITY={entity}" if entity else "")
