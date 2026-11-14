"""Unified memory: one store, seven kinds of memory, kept apart.

The operating system and every game world remember through the same
infrastructure, and none of them can read another's memories. What keeps them
apart is a **namespace** on every record and a **principal** on every read
and write — not care taken by the caller.

==========================  ==========================================================
namespace                   what lives there
==========================  ==========================================================
``user``                    what the person let this computer remember. *Not stored
                            here*: it is the World Model's ledger
                            (``world_model.UserLedger``), with its own rules —
                            only remembered entries are ever read, and the only
                            way to add one from here is a **pending** note.
``conversation/<persona>``  what was said with an OS personality (kept only when
                            ``[mind] keep_conversations`` is on).
``system``                  what the machine was found doing: diagnoses, dated.
``knowledge``               the CDOS corpus. *Not stored here and never written*:
                            CDOS Computer serves it. Listed so the map is whole.
``character/<world>/<id>``  one character's private memories.
``world/<world>``           a game world's shared facts, relationships and state.
``events/<world>``          a world's event history. Append-only.
                            (``events/system`` is the machine's own.)
==========================  ==========================================================

Who may touch what (:func:`allowed`), in one place:

* **mind** — Earth Mind itself. Everything; it is the coordinator, and it only
  ever hands a voice the view that voice is entitled to.
* **persona** — an OS personality. Its own ``conversation/<id>``; ``user`` and
  ``system`` only if its permissions say so; never anything under
  ``character/``, ``world/`` or ``events/<world>``.
* **game** — a world's engine. ``world/<w>``, ``events/<w>`` and
  ``character/<w>/…`` for *its own* world and nothing else: not another
  world, not ``user``, not ``system``.
* **character** — one character. Its own ``character/<w>/<id>``, and the
  *public* records of ``world/<w>``. Nothing else, ever: another character's
  namespace is refused here, at the store, whatever a prompt asks for.

A refusal raises :class:`MemoryAccessError` and is recorded in
``MemoryStore.denials``. It never returns an empty list instead: an empty
list reads as "nothing remembered", which is a different fact.

Storage is SQLite from the standard library — one file, no service, no new
dependency. Retrieval is word overlap weighted by importance and recency;
semantic retrieval stays CDOS's job (the corpus), and this module does not
grow a second one.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1

USER = "user"
SYSTEM = "system"
KNOWLEDGE = "knowledge"
SYSTEM_EVENTS = "events/system"

#: Records a recall looks through, newest first. A turn's cost is bounded by
#: this, not by how long a character has been alive.
SCAN_RECENT = 400
SCAN_IMPORTANT = 60
#: Chatter is pruned beyond this per namespace; events and facts never are.
MAX_CHATTER = 1500
CHATTER_KINDS = ("heard", "said", "turn")

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "is", "are", "was", "were", "it", "i",
         "me", "my", "you", "your", "that", "this", "with", "for", "about", "what", "do", "does", "did",
         "how", "be", "at", "so", "just", "we", "can", "have", "has", "had", "he", "she", "they", "them",
         "his", "her", "their", "there", "here", "who", "when", "where", "why", "not", "but", "as", "by",
         "from", "will", "would", "could", "should", "been", "any", "some", "said", "say", "tell"}


class MemoryAccessError(PermissionError):
    """A principal asked for a namespace it has no right to."""


class MemoryFormatError(ValueError):
    """A malformed namespace or record."""


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in _STOP and len(w) > 2}


# ---------------------------------------------------------------------------
# Who is asking
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Principal:
    kind: str                          # mind | persona | game | character
    id: str = ""
    world: str = ""
    permissions: FrozenSet[str] = frozenset()

    def label(self) -> str:
        if self.kind == "character":
            return f"character:{self.world}/{self.id}"
        if self.kind == "game":
            return f"game:{self.world}"
        return f"{self.kind}:{self.id}" if self.id else self.kind


MIND = Principal("mind")


def character_namespace(world: str, character: str) -> str:
    return f"character/{world}/{character}"


def world_namespace(world: str) -> str:
    return f"world/{world}"


def events_namespace(world: str) -> str:
    return f"events/{world}"


def conversation_namespace(persona: str) -> str:
    return f"conversation/{persona}"


def check_namespace(namespace: str) -> Tuple[str, ...]:
    """The parts of a well-formed namespace, or :class:`MemoryFormatError`."""
    parts = tuple((namespace or "").split("/"))
    head = parts[0]
    ok = (
        (head in (USER, SYSTEM, KNOWLEDGE) and len(parts) == 1)
        or (head in ("conversation", "world", "events") and len(parts) == 2 and _NAME.match(parts[1]))
        or (head == "character" and len(parts) == 3 and _NAME.match(parts[1]) and _NAME.match(parts[2]))
    )
    if not ok:
        raise MemoryFormatError(f"not a memory namespace: {namespace!r}")
    return parts


def allowed(principal: Principal, op: str, namespace: str) -> bool:
    """May *principal* ``read`` / ``write`` / ``forget`` in *namespace*?

    The whole access policy. Everything not granted here is refused.
    """
    parts = check_namespace(namespace)
    head = parts[0]
    if head == KNOWLEDGE:
        return False                       # the corpus is CDOS's; nothing here reads or writes it
    if principal.kind == "mind":
        # Even the coordinator cannot rewrite history.
        return not (head == "events" and op == "forget")

    if principal.kind == "persona":
        if head == "conversation":
            return parts[1] == principal.id
        if head == USER:
            return "memory.user" in principal.permissions and op in ("read", "write")
        if head == SYSTEM or namespace == SYSTEM_EVENTS:
            return "system.read" in principal.permissions and op == "read"
        return False

    if principal.kind == "game":
        if head in ("world", "events") and parts[1] == principal.world:
            if principal.world == "system":
                return False               # no world may call itself the machine
            return not (head == "events" and op == "forget")
        if head == "character" and parts[1] == principal.world:
            return True
        return False

    if principal.kind == "character":
        if head == "character":
            return (parts[1], parts[2]) == (principal.world, principal.id) and op in ("read", "write")
        if head == "world" and parts[1] == principal.world:
            return op == "read"            # and only its public records: see MemoryView
        return False

    return False


# ---------------------------------------------------------------------------
# One memory
# ---------------------------------------------------------------------------
@dataclass
class Record:
    id: str
    namespace: str
    text: str
    kind: str = "memory"
    subject: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5
    visibility: str = "private"        # private | public
    source: str = ""                   # who wrote it
    created: float = 0.0
    updated: float = 0.0
    seq: int = 0
    score: float = 0.0                 # set by recall

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "namespace": self.namespace, "text": self.text, "kind": self.kind,
                "subject": self.subject, "data": dict(self.data), "importance": self.importance,
                "visibility": self.visibility, "source": self.source, "created": self.created,
                "updated": self.updated}


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------
class MemoryStore:
    """Every namespace's records, in one SQLite file (or in memory, for tests)."""

    def __init__(self, path: Optional[Path] = None, ledger=None, clock=time.time):
        self.path = Path(path) if path else None
        self.ledger = ledger
        self.clock = clock
        self.denials: List[Tuple[str, str, str]] = []
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def _db(self) -> sqlite3.Connection:
        """The database, opened on first use: a mind that only talks never creates the file."""
        with self._lock:
            if self._conn is None:
                if self.path is not None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(self.path) if self.path else ":memory:",
                                       check_same_thread=False)
                conn.row_factory = sqlite3.Row
                with conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS memory ("
                        " seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,"
                        " namespace TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,"
                        " subject TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '{}',"
                        " importance REAL NOT NULL DEFAULT 0.5, visibility TEXT NOT NULL DEFAULT 'private',"
                        " source TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL)")
                    conn.execute("CREATE INDEX IF NOT EXISTS memory_ns ON memory(namespace, seq)")
                    conn.execute("CREATE INDEX IF NOT EXISTS memory_key ON memory(namespace, kind, subject)")
                    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
                self._conn = conn
            return self._conn

    @property
    def opened(self) -> bool:
        return self._conn is not None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None

    # -- views -----------------------------------------------------------------
    def view(self, principal: Principal) -> "MemoryView":
        return MemoryView(self, principal)

    def _require(self, principal: Principal, op: str, namespace: str) -> None:
        if not allowed(principal, op, namespace):
            self.denials.append((principal.label(), op, namespace))
            raise MemoryAccessError(f"{principal.label()} may not {op} {namespace}")

    # -- raw access (always behind _require in MemoryView) ------------------------
    @staticmethod
    def _record(row: sqlite3.Row) -> Record:
        try:
            data = json.loads(row["data"] or "{}")
        except ValueError:
            data = {}
        return Record(id=row["id"], namespace=row["namespace"], text=row["text"], kind=row["kind"],
                      subject=row["subject"], data=data if isinstance(data, dict) else {},
                      importance=float(row["importance"]), visibility=row["visibility"],
                      source=row["source"], created=float(row["created"]),
                      updated=float(row["updated"]), seq=int(row["seq"]))

    def _insert(self, namespace: str, text: str, kind: str, subject: str, data: Optional[Dict],
                importance: float, visibility: str, source: str) -> Record:
        text = " ".join(str(text or "").split())
        if not text:
            raise MemoryFormatError("an empty memory")
        if visibility not in ("private", "public"):
            raise MemoryFormatError(f"visibility must be private or public, not {visibility!r}")
        now = self.clock()
        record = Record(id=uuid.uuid4().hex[:12], namespace=namespace, text=text[:2000],
                        kind=str(kind or "memory")[:32], subject=str(subject or "")[:120],
                        data=dict(data or {}), importance=max(0.0, min(1.0, float(importance))),
                        visibility=visibility, source=source, created=now, updated=now)
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO memory (id, namespace, kind, text, subject, data, importance, visibility,"
                " source, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (record.id, namespace, record.kind, record.text, record.subject,
                 json.dumps(record.data, separators=(",", ":"), default=str), record.importance,
                 record.visibility, source, now, now))
            record.seq = int(cur.lastrowid or 0)
            if record.kind in CHATTER_KINDS:
                self._prune(namespace)
        return record

    def _prune(self, namespace: str) -> None:
        marks = ",".join("?" for _ in CHATTER_KINDS)
        row = self._db.execute(
            f"SELECT COUNT(*) AS n FROM memory WHERE namespace = ? AND kind IN ({marks})",
            (namespace, *CHATTER_KINDS)).fetchone()
        extra = int(row["n"]) - MAX_CHATTER
        if extra > 0:
            self._db.execute(
                f"DELETE FROM memory WHERE seq IN (SELECT seq FROM memory WHERE namespace = ?"
                f" AND kind IN ({marks}) ORDER BY importance ASC, seq ASC LIMIT ?)",
                (namespace, *CHATTER_KINDS, extra))

    def _select(self, namespace: str, kinds: Optional[Sequence[str]], public_only: bool,
                order: str, limit: int, subject: Optional[str] = None) -> List[Record]:
        sql = "SELECT * FROM memory WHERE namespace = ?"
        args: List[Any] = [namespace]
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            args.extend(kinds)
        if subject is not None:
            sql += " AND subject = ?"
            args.append(subject)
        if public_only:
            sql += " AND visibility = 'public'"
        sql += f" ORDER BY {order} LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._record(r) for r in rows]

    # -- reporting ---------------------------------------------------------------
    def namespaces(self, prefix: str = "") -> List[Tuple[str, int]]:
        """``[(namespace, records)]`` — for diagnostics and tests, not for prompts."""
        with self._lock:
            rows = self._db.execute(
                "SELECT namespace, COUNT(*) AS n FROM memory WHERE namespace LIKE ? "
                "GROUP BY namespace ORDER BY namespace", (prefix + "%",)).fetchall()
        return [(r["namespace"], int(r["n"])) for r in rows]

    def size(self) -> int:
        """How many records are stored. Does not create the file just to count nothing."""
        if self._conn is None and (self.path is None or not self.path.exists()):
            return 0
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) AS n FROM memory").fetchone()["n"])


# ---------------------------------------------------------------------------
# What one principal can see
# ---------------------------------------------------------------------------
class MemoryView:
    """The store as one principal is allowed to use it. Every call is checked."""

    def __init__(self, store: MemoryStore, principal: Principal):
        self.store = store
        self.principal = principal

    def _public_only(self, namespace: str) -> bool:
        return self.principal.kind == "character" and namespace.startswith("world/")

    # -- writing -----------------------------------------------------------------
    def add(self, namespace: str, text: str, kind: str = "memory", subject: str = "",
            data: Optional[Dict[str, Any]] = None, importance: float = 0.5,
            visibility: str = "private") -> Optional[Record]:
        """Remember *text*. In ``user`` this files a **pending** note and nothing more."""
        self.store._require(self.principal, "write", namespace)
        if namespace == USER:
            return self._suggest_to_user(text, kind, subject)
        return self.store._insert(namespace, text, kind, subject, data, importance, visibility,
                                  self.principal.label())

    def put(self, namespace: str, kind: str, subject: str, text: str,
            data: Optional[Dict[str, Any]] = None, importance: float = 0.5,
            visibility: str = "private") -> Record:
        """One record per (namespace, kind, subject): state, a relationship, a setting."""
        self.store._require(self.principal, "write", namespace)
        if namespace == USER or namespace.startswith("events/"):
            raise MemoryFormatError(f"{namespace} holds records, not keyed state")
        with self.store._lock:
            existing = self.store._select(namespace, (kind,), False, "seq DESC", 1, subject=subject)
            if not existing:
                return self.store._insert(namespace, text, kind, subject, data, importance,
                                          visibility, self.principal.label())
            record = existing[0]
            record.text = " ".join(str(text or "").split())[:2000] or record.text
            record.data = dict(data or {})
            record.importance = max(0.0, min(1.0, float(importance)))
            record.visibility = visibility
            record.updated = self.store.clock()
            with self.store._db:
                self.store._db.execute(
                    "UPDATE memory SET text = ?, data = ?, importance = ?, visibility = ?, updated = ?,"
                    " source = ? WHERE seq = ?",
                    (record.text, json.dumps(record.data, separators=(",", ":"), default=str),
                     record.importance, record.visibility, record.updated,
                     self.principal.label(), record.seq))
            return record

    def _suggest_to_user(self, text: str, kind: str, subject: str):
        ledger = self.store.ledger
        if ledger is None:
            return None
        kind = kind if kind in ("preference", "project", "memory", "activity") else "activity"
        entry = ledger.observe(text, kind=kind, subject=subject, origin=self.principal.label())
        if entry is None:
            return None
        ledger.save()
        return Record(id=entry.id, namespace=USER, text=entry.text, kind=entry.kind,
                      subject=entry.subject, data={"status": entry.status}, source=entry.origin,
                      created=entry.created, updated=entry.created)

    # -- reading -----------------------------------------------------------------
    def recent(self, namespace: str, limit: int = 10, kinds: Optional[Sequence[str]] = None) -> List[Record]:
        """The latest records, oldest first."""
        self.store._require(self.principal, "read", namespace)
        if namespace == USER:
            return self._user_records()[-limit:]
        rows = self.store._select(namespace, kinds, self._public_only(namespace), "seq DESC", limit)
        return list(reversed(rows))

    def find(self, namespace: str, kind: str, subject: str) -> Optional[Record]:
        self.store._require(self.principal, "read", namespace)
        if namespace == USER:
            return None
        rows = self.store._select(namespace, (kind,), self._public_only(namespace), "seq DESC", 1,
                                  subject=subject)
        return rows[0] if rows else None

    def recall(self, namespace: str, query: str = "", limit: int = 5,
               kinds: Optional[Sequence[str]] = None, always: int = 0) -> List[Record]:
        """What bears on *query*, best first; *always* adds the newest regardless.

        Relevance is shared words, lifted by importance and by being recent.
        With no query at all this is simply the most important recent records.
        """
        self.store._require(self.principal, "read", namespace)
        if namespace == USER:
            candidates = self._user_records()
        else:
            public = self._public_only(namespace)
            seen: Dict[int, Record] = {}
            for record in self.store._select(namespace, kinds, public, "seq DESC", SCAN_RECENT):
                seen[record.seq] = record
            for record in self.store._select(namespace, kinds, public,
                                             "importance DESC, seq DESC", SCAN_IMPORTANT):
                seen.setdefault(record.seq, record)
            candidates = sorted(seen.values(), key=lambda r: r.seq)
        if not candidates:
            return []
        said = words(query)
        newest = max(r.seq for r in candidates) or 1
        oldest = min(r.seq for r in candidates)
        span = float(max(newest - oldest, 1))
        ranked: List[Record] = []
        for record in candidates:
            overlap = len(said & (words(record.text) | words(record.subject))) if said else 0
            if said and not overlap:
                continue
            recency = (record.seq - oldest) / span
            record.score = (overlap / max(len(said), 1)) * 2.0 + record.importance + 0.4 * recency
            ranked.append(record)
        ranked.sort(key=lambda r: -r.score)
        out = ranked[:limit]
        if always:
            for record in reversed(candidates[-always:]):
                if record not in out:
                    out.append(record)
        return out

    def _user_records(self) -> List[Record]:
        """The ledger's remembered entries. Pending notes are never read — the World Model's rule."""
        ledger = self.store.ledger
        if ledger is None:
            return []
        out = []
        for i, entry in enumerate(ledger.remembered()):
            out.append(Record(id=entry.id, namespace=USER, text=entry.text, kind=entry.kind,
                              subject=entry.subject, source=entry.source, importance=0.7,
                              created=entry.created, updated=entry.created, seq=i + 1))
        return out

    def count(self, namespace: str) -> int:
        self.store._require(self.principal, "read", namespace)
        if namespace == USER:
            return len(self._user_records())
        with self.store._lock:
            sql = "SELECT COUNT(*) AS n FROM memory WHERE namespace = ?"
            if self._public_only(namespace):
                sql += " AND visibility = 'public'"
            return int(self.store._db.execute(sql, (namespace,)).fetchone()["n"])

    # -- forgetting --------------------------------------------------------------
    def forget(self, namespace: str, record_id: str) -> bool:
        self.store._require(self.principal, "forget", namespace)
        if namespace == USER:
            raise MemoryAccessError("the person's memories are forgotten by the person, through the World Model")
        with self.store._lock, self.store._db:
            cur = self.store._db.execute("DELETE FROM memory WHERE namespace = ? AND id = ?",
                                         (namespace, record_id))
            return cur.rowcount > 0

    def clear(self, namespace: str) -> int:
        self.store._require(self.principal, "forget", namespace)
        if namespace == USER:
            raise MemoryAccessError("the person's memories are forgotten by the person, through the World Model")
        with self.store._lock, self.store._db:
            cur = self.store._db.execute("DELETE FROM memory WHERE namespace = ?", (namespace,))
            return int(cur.rowcount)


def texts(records: Iterable[Record]) -> List[str]:
    return [r.text for r in records]
