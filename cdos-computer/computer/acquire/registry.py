"""sources.toml: the sources a person has approved (Q1: approval is per source, not per fact).

    [source.example]
    source_type  = "EXAMPLE"                     # written into every row's source_type column
    licence      = "CC0-1.0"
    attribution  = "Example data, https://example.org"
    url          = "https://example.org/dump/example.pf"
    sha256       = "sha256:…"                    # or checksum_url = "…/SHA256SUMS"; one of the two is required
    parser       = "pf"
    relations    = { CAPITAL = "CAPITAL", POP = "POPULATION" }   # source relation -> CDOS relation; the allowlist
    entity       = "ex"                          # optional: ENTITY=ex:<id> on rows that don't carry one (Q3)
    max_bytes    = 50_000_000
    bandwidth    = 0                             # bytes per second; 0: no limit
    approved_by  = "richie"
    approved_at  = "2026-11-15"

A source missing any required field is refused with the field named.  Nothing is guessed or defaulted that would
let an unapproved or unverifiable download through.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import AcquireError
from .parsers import PARSERS

SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SOURCE_TYPE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
RELATION = re.compile(r"^[A-Z][A-Z0-9_]*$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED = ("source_type", "licence", "attribution", "url", "parser", "relations", "max_bytes", "approved_by",
            "approved_at")


@dataclass(frozen=True)
class Source:
    id: str
    source_type: str
    licence: str
    attribution: str
    url: str
    parser: str
    relations: dict
    max_bytes: int
    approved_by: str
    approved_at: str
    sha256: str = ""
    checksum_url: str = ""
    entity: str = ""
    bandwidth: int = 0
    raw: dict = field(default_factory=dict, compare=False)

    @property
    def filename(self) -> str:
        name = Path(urlparse(self.url).path).name
        return name or f"{self.id}.download"

    def fingerprint(self) -> str:
        """Changes when anything about the source that affects a run changes (mapping, url, checksum, ...)."""
        return "sha256:" + hashlib.sha256(json.dumps(self.raw, sort_keys=True, default=str).encode()).hexdigest()


def _check(sid: str, d: dict) -> Source:
    def bad(why):
        raise AcquireError(f"source {sid!r} is refused: {why}")

    if not SOURCE_ID.match(sid):
        bad("its name must be lower-case letters, digits, '-' or '_'")
    if not isinstance(d, dict):
        bad("it is not a table")
    missing = [k for k in REQUIRED if d.get(k) in (None, "", {}, [])]
    if missing:
        bad("missing " + ", ".join(missing) + (" (it isn't approved)" if any(k.startswith("approved") for k in missing)
                                               else ""))
    unknown = set(d) - set(REQUIRED) - {"sha256", "checksum_url", "entity", "bandwidth"}
    if unknown:
        bad(f"unknown field(s) {sorted(unknown)}")
    if not SOURCE_TYPE.match(str(d["source_type"])):
        bad("source_type must be upper-case letters, digits or '_'")
    if d["parser"] not in PARSERS:
        bad(f"no parser {d['parser']!r} (known: {', '.join(sorted(PARSERS))})")
    if urlparse(str(d["url"])).scheme not in ("http", "https", "file"):
        bad("url must be http, https or file")
    sha, curl = str(d.get("sha256", "")), str(d.get("checksum_url", ""))
    if not sha and not curl:
        bad("no sha256 and no checksum_url: a download that can't be verified isn't fetched")
    if sha and not SHA256.match(sha):
        bad("sha256 must be 'sha256:' and 64 hex digits")
    rels = d["relations"]
    if not isinstance(rels, dict) or not all(isinstance(k, str) and isinstance(v, str) and RELATION.match(v)
                                             for k, v in rels.items()):
        bad("relations must map source relation names to upper-case CDOS relation names")
    for k in ("max_bytes", "bandwidth"):
        if k in d and (not isinstance(d[k], int) or d[k] < 0 or (k == "max_bytes" and d[k] == 0)):
            bad(f"{k} must be a whole number" + (" above 0" if k == "max_bytes" else ", 0 or more"))
    return Source(id=sid, source_type=d["source_type"], licence=str(d["licence"]),
                  attribution=str(d["attribution"]), url=str(d["url"]), parser=d["parser"], relations=dict(rels),
                  max_bytes=d["max_bytes"], approved_by=str(d["approved_by"]), approved_at=str(d["approved_at"]),
                  sha256=sha, checksum_url=curl, entity=str(d.get("entity", "")),
                  bandwidth=int(d.get("bandwidth", 0)), raw=dict(d))


def load(path: Path) -> tuple[dict[str, Source], dict[str, str]]:
    """(approved sources by name, refused sources -> why).  An unreadable file is an error, not an empty registry."""
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AcquireError(f"no registry at {path}") from None
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise AcquireError(f"the registry {path} can't be read: {e}") from None
    extra = set(data) - {"source"}
    if extra:
        raise AcquireError(f"the registry has unknown top-level entries {sorted(extra)}; only [source.<name>] tables")
    ok, refused = {}, {}
    for sid, d in (data.get("source") or {}).items():
        try:
            ok[sid] = _check(sid, d)
        except AcquireError as e:
            refused[sid] = str(e)
    return ok, refused


def get(path: Path, sid: str) -> Source:
    ok, refused = load(path)
    if sid in refused:
        raise AcquireError(refused[sid])
    if sid not in ok:
        raise AcquireError(f"no source {sid!r} in {path}")
    return ok[sid]
