"""Parsers: a downloaded file -> (line number, raw record) pairs, in file order.  A record is a list of 11 fields, or
None with the reason when the line can't be read as one.  Real formats (Wikidata, OEWN, GeoNames) come in phase 4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator


def parse_pf(path: Path) -> Iterator[tuple[int, list | None, str, str]]:
    """A source that already ships PF rows (11 tab-separated columns), e.g. a curated table.
    Yields (line number, fields or None, why, the raw line)."""
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh, 1):
            try:
                line = raw.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError:
                yield n, None, "not UTF-8", raw[:500].decode("utf-8", "replace")
                continue
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) != 11:
                yield n, None, f"{len(cols)} columns, not 11", line
                continue
            yield n, cols, "", line


PARSERS = {"pf": parse_pf}
