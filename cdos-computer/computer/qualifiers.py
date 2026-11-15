"""The qualifiers column of a corpus row: one or more KEY=VALUE, separated by ";" with no spaces.

    REFERENCE=SUN                       one qualifier (every qualified row in corpus.pf before 2026-11-15 is like this)
    REFERENCE=SUN;ENTITY=wd:Q1150       two

KEY is upper-case letters, digits and "_"; VALUE is not empty and holds no ";", "=" or tab (so the space-separated
form "A=1 B=2" is refused rather than read as one value).  ";" was chosen over a space so
a value can never split into two by containing one, and because no existing qualifier holds it.

Some keys say which fact a row is, not anything about it: SENSE= (which WordNet sense) and ENTITY= (which thing a name
means, Q3).  They keep facts apart and stay in the evidence, but they are never said.
"""

from __future__ import annotations

import re

SEPARATOR = ";"
BOOKKEEPING = frozenset({"SENSE", "ENTITY"})
_PART = re.compile(r"^[A-Z][A-Z0-9_]*=[^;=\t]+$")


def qualifiers(qual: str | None) -> list[tuple[str, str]]:
    """(key, value) for each qualifier, in order.  Lenient: a part without "=" is kept with an empty key, so nothing
    is lost when reading; problems() is the strict check."""
    out = []
    for part in (qual or "").split(SEPARATOR):
        if not part:
            continue
        key, eq, value = part.partition("=")
        out.append((key, value) if eq else ("", part))
    return out


def get(qual: str | None, key: str) -> str:
    """The first value of `key`, or ""."""
    for k, v in qualifiers(qual):
        if k == key:
            return v
    return ""


def shown(qual: str | None) -> str:
    """The qualifiers that are content, without SENSE= and ENTITY=, joined as they were written."""
    return SEPARATOR.join(f"{k}={v}" if k else v for k, v in qualifiers(qual) if k not in BOOKKEEPING)


def problems(qual: str | None) -> list[str]:
    """Empty when the column is "" or KEY=VALUE[;KEY=VALUE...]; else the parts that aren't."""
    if not qual:
        return []
    return [p for p in qual.split(SEPARATOR) if not _PART.match(p)]
