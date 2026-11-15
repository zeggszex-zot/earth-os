"""The dedupe index: every fact key in the master (computer/pfrow.py, the build's own key), and the undated values of
its functional relations (for the conflict check).  SQLite, rebuilt whenever the master's sha256 or the key's code
changes, built into a temporary file and renamed into place.

The master is read as written, with the build's last steps (pfrow.finish) but not its legacy repairs
(--fix-wordnet / --fix-wikidata): a new row can only be matched against a legacy row the repairs would have changed
once that legacy source is re-imported as a new generation (Q2).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from computer.corpus_guard import CorpusGuard, GuardError
from computer.governor import sha256_file

from .. import pfrow, qualifiers, served
from ..corpus import key_of
from ..served import served_rows

#: The build's production repairs (tools/corpus_guard.py BUILD_FLAGS): the index sees the master as it is served.
PRODUCTION = {"fix_wordnet": True, "fix_wikidata": True, "fix_keys": True}
from . import AcquireError

BATCH = 50_000
SCHEMA = "2"          # 2: names held without entities (`plain`); an index of another schema is rebuilt


def key_hash(key: tuple) -> bytes:
    return hashlib.blake2b(repr(key).encode("utf-8"), digest_size=16).digest()


def code_fingerprint() -> str:
    """Changes when the code that decides what a fact is (key, repairs, qualifiers) changes: the index is rebuilt."""
    h = hashlib.sha256()
    for mod in (pfrow, served, qualifiers):
        h.update(Path(mod.__file__).read_bytes())
    return "sha256:" + h.hexdigest()


def replaced_by(spec: dict | None):
    """A test for the rows of the generation a source replaces: same source_type, source_id matching."""
    if not spec:
        return lambda c: False
    st, rx = spec["source_type"], re.compile(spec["source_id"])
    return lambda c: c[7] == st and rx.search(c[8]) is not None


def master_rows(path: Path, exclude: dict | None = None):
    """(row, finished) for every fact row of the master as the build serves it (computer/served.py, with the
    production repairs), leaving out the rows of a generation being replaced.  `row` is the served row; `finished` is
    (subject, kind, start, until, qualifiers), what the fact key needs."""
    skip = replaced_by(exclude)
    for r in served_rows(path, **PRODUCTION):
        if skip(r):
            continue
        yield r, (r[0], r[2], r[5], r[6], r[10])


def functional_relations(path: Path) -> set[str]:
    """The relations the master declares functional (X IS_FUNCTIONAL TRUE), by a quick pass of the raw rows: these
    schema rows are never repaired, so their subject is just its lookup key."""
    out = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            c = line.rstrip("\n").split("\t")
            if len(c) > 3 and c[1] == "IS_FUNCTIONAL" and c[3].strip().upper() == "TRUE":
                out.add(key_of(c[0]))
    return out


class MasterIndex:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._db = None

    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self._db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def meta(self) -> dict:
        if not self.path.exists():
            return {}
        with contextlib.closing(sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)) as db:
            try:
                return dict(db.execute("SELECT k, v FROM meta"))
            except sqlite3.DatabaseError:
                return {}

    def ensure(self, master: Path, exclude: dict | None = None) -> tuple[dict, bool]:
        """(meta, rebuilt).  Rebuilds when the master, the key code or the excluded generation changed."""
        sha = sha256_file(master)
        m = self.meta()
        excl = json.dumps(exclude or {}, sort_keys=True)
        if (m.get("master_sha256") == sha and m.get("code") == code_fingerprint() and m.get("exclude", "{}") == excl
                and m.get("schema") == SCHEMA):
            return m, False
        self.close()
        guard = CorpusGuard(master)
        try:
            with (guard.reading() if guard.guarded else contextlib.nullcontext()):
                if sha256_file(master) != sha:
                    raise AcquireError("the master changed while the index was being built")
                m = self._build(master, sha, exclude)
        except GuardError as e:
            raise AcquireError(f"the master can't be read for the dedupe index: {e}") from None
        return m, True

    def _build(self, master: Path, sha: str, exclude: dict | None = None) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        functional = functional_relations(master)
        db = sqlite3.connect(tmp)
        try:
            db.executescript("""
                PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;
                CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
                CREATE TABLE keys (h BLOB PRIMARY KEY) WITHOUT ROWID;
                CREATE TABLE functional (relation TEXT PRIMARY KEY);
                CREATE TABLE fvals (subject TEXT, relation TEXT, kind TEXT, value TEXT);
                CREATE TABLE plain (subject TEXT PRIMARY KEY) WITHOUT ROWID;
            """)
            db.executemany("INSERT INTO functional VALUES (?)", [(r,) for r in sorted(functional)])
            keys, fvals, n, plain = [], [], 0, set()
            for c, (subj, kind, start, until, qual) in master_rows(master, exclude):
                rel, value, unit = c[1], c[3], c[4]
                keys.append((key_hash(pfrow.fact_key(subj, rel, kind, value, unit, start, until, qual, True)),))
                if rel in functional and start is None and until is None:
                    fvals.append((subj, rel, kind, value))
                if c[7] != "OEWN" and not qualifiers.get(qual, "ENTITY"):
                    plain.add(subj)            # the master says things about this name without saying which thing
                n += 1
                if len(keys) >= BATCH:
                    db.executemany("INSERT OR IGNORE INTO keys VALUES (?)", keys)
                    keys.clear()
            db.executemany("INSERT OR IGNORE INTO keys VALUES (?)", keys)
            db.executemany("INSERT INTO fvals VALUES (?, ?, ?, ?)", fvals)
            db.execute("CREATE INDEX fv ON fvals (subject, relation)")
            db.executemany("INSERT INTO plain VALUES (?)", [(x,) for x in sorted(plain)])
            distinct = db.execute("SELECT count(*) FROM keys").fetchone()[0]
            meta = {"master": str(master), "master_sha256": sha, "code": code_fingerprint(), "rows": str(n),
                    "keys": str(distinct), "functional": str(len(functional)),
                    "exclude": json.dumps(exclude or {}, sort_keys=True), "schema": SCHEMA}
            db.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
            db.commit()
        finally:
            db.close()
        os.replace(tmp, self.path)
        return meta

    def has(self, h: bytes) -> bool:
        return self.db().execute("SELECT 1 FROM keys WHERE h = ?", (h,)).fetchone() is not None

    def held_plain(self, subject: str) -> bool:
        """The master has rows about this name that don't say which thing they are about (no ENTITY=, not OEWN)."""
        return self.db().execute("SELECT 1 FROM plain WHERE subject = ?", (subject,)).fetchone() is not None

    def functional(self) -> set[str]:
        return {r for (r,) in self.db().execute("SELECT relation FROM functional")}

    def values(self, subject: str, relation: str) -> list[tuple[str, str]]:
        return list(self.db().execute("SELECT kind, value FROM fvals WHERE subject = ? AND relation = ?",
                                      (subject, relation)))
