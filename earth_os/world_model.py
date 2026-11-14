"""The CDOS World Model: what this computer knows about itself and its person.

A companion that can be asked *"want to play Battle Tactics?"* has to know
which games exist, what they can do, what the person has made with them and
what the person has said they like. That knowledge has to belong to **CDOS**,
not to whichever model happens to be putting it into sentences this week. The
model is replaceable; the world it talks about is not.

So the World Model is the one place that answers four questions, for any
reasoner, in a plain shape any reasoner can read:

* **What am I?** — the name, and the catalogue of what this machine can do.
* **What is here?** — compositions, programs, and applications (games
  included) with the mechanics they provide and borrow from one another.
* **Who am I with?** — what the person has *chosen* to let it remember.
* **How does it connect?** — which applications share a mechanic, so
  *"Risk, but with the combat from our chess game"* has an answer.

## The rules that keep it honest

**1. Self-knowledge is derived, never stored.** The catalogue, compositions
and application index are read every time a snapshot is taken. A stored copy
of "what I can do" is a second catalogue, and the second copy is always the
stale one. Only the person's ledger is persisted, because only it cannot be
re-derived.

**2. Nothing is remembered about a person without the person.** There are two
ways in. :meth:`UserLedger.remember` is the person saying *"remember that…"*,
and it is remembered. :meth:`UserLedger.observe` is anything else — a game
noticing a preference, a model inferring a habit — and it is only ever
*pending*. There is no argument to ``observe`` that makes an entry
remembered, on purpose: the same shape as the semantic layer, where a learned
alias starts below the execution threshold. Pending notes never reach a
model's context. Playing a game is not consent to a personality profile.

**3. Forgetting is always one step.** :meth:`UserLedger.forget`,
:meth:`UserLedger.forget_all`, or deleting ``world.json`` — any of the three
removes it, and nothing else holds a copy.

**4. An application is data, not power.** A manifest lists which catalogue
capabilities it uses. A name outside :data:`CATALOGUE` is refused when the
manifest is read, exactly as a composition step is — an application cannot
claim ``system.format_disk`` into existence. Mechanics (``combat.dice``) are
just names shared between applications; they confer nothing on the machine.

**5. Failure is data.** A manifest that cannot be read is listed with its
reason, not silently dropped and not guessed at.

This module names no mechanism and runs nothing. ``tests/test_boundary.py``
holds it to zero.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .platform.capabilities import CATALOGUE

SCHEMA_VERSION = 1

#: What a remembered thing can be about.
KINDS: Tuple[str, ...] = ("preference", "project", "memory", "activity")

#: Pending notes are suggestions, not records; they lapse unless kept.
PENDING_TTL = 7 * 24 * 3600
MAX_PENDING = 50

#: An application or mechanic name: ``battle-tactics``, ``combat.dice``.
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class WorldModelError(ValueError):
    """A manifest or entry that breaks the rules above."""


def _data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "earth-os"


# ======================================================================
# The person
# ======================================================================
@dataclass
class Entry:
    """One thing known about the person, and how it came to be known."""

    id: str
    text: str
    kind: str = "memory"
    subject: str = ""            # an application, project or topic, if any
    source: str = "user"         # user | observed
    origin: str = ""             # who observed it: "battle-tactics", "model"
    status: str = "remembered"   # remembered | pending
    created: float = field(default_factory=time.time)

    @property
    def remembered(self) -> bool:
        return self.status == "remembered"

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict) -> "Entry":
        entry = cls(
            id=str(raw["id"]), text=str(raw["text"]).strip(),
            kind=str(raw.get("kind", "memory")),
            subject=str(raw.get("subject", "")),
            source=str(raw.get("source", "user")),
            origin=str(raw.get("origin", "")),
            status=str(raw.get("status", "pending")),
            created=float(raw.get("created", time.time())),
        )
        if not entry.text or entry.kind not in KINDS \
                or entry.status not in ("remembered", "pending") \
                or entry.source not in ("user", "observed"):
            raise WorldModelError(f"malformed entry {entry.id}")
        # A hand-edited file cannot promote an observation: only a person's
        # confirmation does that, and confirmation rewrites the source.
        if entry.source == "observed" and entry.status == "remembered":
            entry.status = "pending"
        return entry


class UserLedger:
    """What the person has allowed this computer to know about them.

    A plain JSON file, readable and hand-editable, because a memory you cannot
    inspect is one you cannot correct.
    """

    def __init__(self, path: Optional[Path] = None, observe: bool = True):
        self.path = Path(path) if path else _data_dir() / "world.json"
        #: False drops observations at the door instead of queueing them.
        self.observing = observe
        self.entries: List[Entry] = []
        self.rejected: List[str] = []
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path: Optional[Path] = None, observe: bool = True) -> "UserLedger":
        ledger = cls(path, observe=observe)
        ledger.reload()
        return ledger

    def reload(self) -> None:
        self.entries, self.rejected = [], []
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.rejected.append(f"{self.path.name}: unreadable ({exc})")
            return
        for item in raw.get("entries", []) if isinstance(raw, dict) else []:
            try:
                self.entries.append(Entry.from_dict(item))
            except (KeyError, TypeError, ValueError) as exc:
                self.rejected.append(f"entry skipped: {exc}")
        self.expire()

    def save(self) -> bool:
        payload = {"schema": SCHEMA_VERSION,
                   "entries": [e.to_dict() for e in self.entries]}
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                os.replace(tmp, self.path)
                return True
            except OSError:
                return False

    # -- the two ways in ------------------------------------------------
    def remember(self, text: str, kind: str = "memory", subject: str = "") -> Entry:
        """The person said *remember that…*. It is remembered."""
        entry = self._make(text, kind, subject, source="user", origin="person")
        entry.status = "remembered"
        self.entries.append(entry)
        return entry

    def observe(self, text: str, kind: str = "activity", subject: str = "",
                origin: str = "") -> Optional[Entry]:
        """Something noticed, not something said. Always pending.

        Returns ``None`` when observing is switched off, or when the same note
        is already known — a game reporting the same preference every session
        is one suggestion, not fifty.
        """
        if not self.observing:
            return None
        clean = " ".join(str(text).split())
        for existing in self.entries:
            if existing.text.lower() == clean.lower():
                return None
        entry = self._make(clean, kind, subject, source="observed",
                           origin=origin or "unknown")
        entry.status = "pending"
        self.entries.append(entry)
        self._cap_pending()
        return entry

    # -- the person's answers -----------------------------------------
    def confirm(self, entry_id: str) -> Optional[Entry]:
        """Keep a pending note. It becomes the person's own statement."""
        entry = self.get(entry_id)
        if entry is None or entry.remembered:
            return entry
        entry.status = "remembered"
        entry.source = "user"
        return entry

    def forget(self, entry_id: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.id != entry_id]
        return len(self.entries) != before

    def forget_matching(self, words: str) -> List[Entry]:
        """Forget every entry whose text or subject contains *words*."""
        needle = " ".join(words.lower().split())
        if not needle:
            return []
        gone = [e for e in self.entries
                if needle in e.text.lower() or needle == e.subject.lower()]
        ids = {e.id for e in gone}
        self.entries = [e for e in self.entries if e.id not in ids]
        return gone

    def forget_all(self) -> int:
        count = len(self.entries)
        self.entries = []
        return count

    # -- reading ------------------------------------------------------
    def get(self, entry_id: str) -> Optional[Entry]:
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    def remembered(self) -> List[Entry]:
        return [e for e in self.entries if e.remembered]

    def pending(self) -> List[Entry]:
        return [e for e in self.entries if not e.remembered]

    def about(self, subject: str) -> List[Entry]:
        return [e for e in self.remembered() if e.subject == subject]

    def expire(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        before = len(self.entries)
        self.entries = [e for e in self.entries
                        if e.remembered or now - e.created < PENDING_TTL]
        return before - len(self.entries)

    # ------------------------------------------------------------------
    @staticmethod
    def _make(text: str, kind: str, subject: str, source: str,
              origin: str) -> Entry:
        clean = " ".join(str(text).split())
        if not clean:
            raise WorldModelError("nothing to remember")
        if kind not in KINDS:
            raise WorldModelError(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
        return Entry(id=uuid.uuid4().hex[:8], text=clean, kind=kind,
                     subject=str(subject or ""), source=source, origin=origin)

    def _cap_pending(self) -> None:
        pending = self.pending()
        if len(pending) <= MAX_PENDING:
            return
        drop = {e.id for e in sorted(pending, key=lambda e: e.created)
                [:len(pending) - MAX_PENDING]}
        self.entries = [e for e in self.entries if e.id not in drop]


# ======================================================================
# Applications
# ======================================================================
@dataclass(frozen=True)
class Application:
    """An installed application, described as data.

    ``provides`` and ``uses`` are mechanics — named pieces of behaviour one
    application can lend another (``combat.dice``, ``board.hex``). They are
    how *"use the combat from our chess game"* is understood. They are not
    capabilities and grant nothing.
    """

    name: str
    title: str
    kind: str = "tool"                     # game | tool | simulation | …
    summary: str = ""
    capabilities: Tuple[str, ...] = ()     # every one must be in CATALOGUE
    provides: Tuple[str, ...] = ()
    uses: Tuple[str, ...] = ()
    derived_from: Tuple[str, ...] = ()     # "Battle Tactics Chess" <- chess, battle-tactics

    @classmethod
    def build(cls, raw: Dict) -> "Application":
        if not isinstance(raw, dict):
            raise WorldModelError("a manifest is an object")
        name = str(raw.get("name", "")).strip().lower()
        if not _NAME.match(name):
            raise WorldModelError(f"bad application name {name!r}")
        caps = tuple(str(c) for c in raw.get("capabilities", ()))
        unknown = [c for c in caps if c not in CATALOGUE]
        if unknown:
            # Rule 4: an application cannot claim power into existence.
            raise WorldModelError(
                f"{name}: not in the catalogue: {', '.join(unknown)}")
        mechanics = []
        for key in ("provides", "uses"):
            values = tuple(str(m).strip().lower() for m in raw.get(key, ()))
            bad = [m for m in values if not _NAME.match(m) or "." not in m]
            if bad:
                raise WorldModelError(
                    f"{name}: mechanics are 'area.name': {', '.join(bad)}")
            mechanics.append(values)
        derived = tuple(str(d).strip().lower() for d in raw.get("derived_from", ()))
        bad = [d for d in derived if not _NAME.match(d)]
        if bad:
            raise WorldModelError(f"{name}: bad derived_from name(s): {', '.join(bad)}")
        return cls(name=name,
                   title=str(raw.get("title") or name.replace("-", " ").title()),
                   kind=str(raw.get("kind", "tool")).strip().lower() or "tool",
                   summary=str(raw.get("summary", "")).strip(),
                   capabilities=caps, provides=mechanics[0], uses=mechanics[1],
                   derived_from=derived)

    @property
    def effectful(self) -> bool:
        """Inherited from the catalogue, like a composition's. Not a field."""
        return any(CATALOGUE[c].effectful for c in self.capabilities)

    def to_dict(self) -> Dict:
        out = asdict(self)
        out["effectful"] = self.effectful
        return out


class ApplicationIndex:
    """Every manifest in the ``apps`` directory, each one checked."""

    def __init__(self, directory: Optional[Path] = None):
        self.directory = Path(directory) if directory else _data_dir() / "apps"
        self.apps: Dict[str, Application] = {}
        self.refused: Dict[str, str] = {}

    @classmethod
    def load(cls, directory: Optional[Path] = None) -> "ApplicationIndex":
        index = cls(directory)
        index.reload()
        return index

    def reload(self) -> None:
        self.apps, self.refused = {}, {}
        if not self.directory.is_dir():
            return
        for path in sorted(self.directory.glob("*.json")):
            try:
                app = Application.build(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                self.refused[path.name] = str(exc)
                continue
            if app.name in self.apps:
                self.refused[path.name] = f"{app.name} is already defined"
                continue
            self.apps[app.name] = app

    def add(self, app: Application) -> None:
        self.apps[app.name] = app

    def find(self, words: str) -> Optional[Application]:
        """An application named in a sentence, longest title first."""
        text = " " + " ".join(re.sub(r"[^\w\s-]", " ", words.lower()).split()) + " "
        best, best_len = None, 0
        for app in self.apps.values():
            for label in (app.title.lower(), app.name.replace("-", " ")):
                if len(label) > best_len and f" {label} " in text:
                    best, best_len = app, len(label)
        return best

    def of_kind(self, kind: str) -> List[Application]:
        return [a for a in self.apps.values() if a.kind == kind]

    def providers(self, mechanic: str) -> List[Application]:
        return [a for a in self.apps.values() if mechanic in a.provides]

    def relations(self) -> List[Tuple[str, str, str]]:
        """``(borrower, lender, mechanic)`` for every mechanic one app uses
        and another provides, plus ``(a, b, mechanic)`` for every mechanic two
        apps both provide — the second is what makes a mix-up possible."""
        out = []
        apps = sorted(self.apps.values(), key=lambda a: a.name)
        for app in apps:
            for mech in app.uses:
                for lender in self.providers(mech):
                    if lender.name != app.name:
                        out.append((app.name, lender.name, mech))
        for i, a in enumerate(apps):
            for b in apps[i + 1:]:
                for mech in sorted(set(a.provides) & set(b.provides)):
                    out.append((a.name, b.name, mech))
        return out

    def combine(self, base: str, lender: str) -> Dict:
        """What *base* would gain by borrowing from *lender*.

        A plan, never an action: it says which of the lender's mechanics the
        base does not already have, so an application (or a person) can decide
        what to do with it. Raises for an application that is not installed
        rather than inventing one.
        """
        a, b = self.apps.get(base), self.apps.get(lender)
        if a is None or b is None:
            missing = base if a is None else lender
            raise WorldModelError(f"no application called {missing}")
        borrow = tuple(m for m in b.provides if m not in a.provides)
        return {"base": a.name, "lender": b.name, "borrow": list(borrow),
                "shared": sorted(set(a.provides) & set(b.provides))}


# ======================================================================
# The whole picture
# ======================================================================
class WorldModel:
    """Assembled on demand from the parts the shell already owns."""

    def __init__(self, name: str = "CDOS", registry=None, compositions=None,
                 ledger: Optional[UserLedger] = None,
                 apps: Optional[ApplicationIndex] = None,
                 programs: Sequence[Dict] = ()):
        self.name = name
        self.registry = registry
        self.compositions = compositions
        self.ledger = ledger
        self.apps = apps
        self.programs = list(programs)

    # -- what am I ----------------------------------------------------
    def capabilities(self) -> List[Dict]:
        out = []
        for spec in CATALOGUE.values():
            supported = None
            if self.registry is not None:
                try:
                    supported = bool(self.registry.supported(spec.name))
                except Exception:
                    supported = None
            out.append({"name": spec.name, "summary": spec.summary,
                        "effectful": spec.effectful, "supported": supported})
        return out

    # -- what is here -------------------------------------------------
    def composition_list(self) -> List[Dict]:
        if self.compositions is None:
            return []
        out = []
        for name in self.compositions.names():
            comp = self.compositions.get(name)
            if comp is not None:
                out.append({"name": comp.name, "summary": comp.summary,
                            "steps": [s.capability for s in comp.steps],
                            "effectful": comp.effectful})
        return out

    def snapshot(self) -> Dict:
        """Everything, as plain data any reasoner — or a person — can read."""
        apps = self.apps
        return {
            "schema": SCHEMA_VERSION,
            "self": {"name": self.name,
                     "capabilities": self.capabilities(),
                     "compositions": self.composition_list(),
                     "programs": [str(p.get("label", "")) for p in self.programs
                                  if isinstance(p, dict) and p.get("label")]},
            "applications": ([a.to_dict() for a in apps.apps.values()]
                             if apps else []),
            "relations": ([{"from": a, "to": b, "mechanic": m}
                           for a, b, m in apps.relations()] if apps else []),
            "refused": dict(apps.refused) if apps else {},
            # Rule 2: only what the person kept. Pending notes stay home.
            "user": ([{"kind": e.kind, "text": e.text, "subject": e.subject}
                      for e in self.ledger.remembered()]
                     if self.ledger else []),
        }

    def briefing(self, limit: int = 40) -> str:
        """A compact text block for a model's context.

        Short on purpose: a model gets the shape of the world, not a dump of
        it. Capabilities the platform cannot actually do are left out, so the
        model is not told the machine can do something it cannot.
        """
        snap = self.snapshot()
        lines = [f"world_model: {snap['self']['name']}"]
        caps = [c["name"] for c in snap["self"]["capabilities"]
                if c["supported"] is not False]
        lines.append(f"can_do: {', '.join(caps)}")
        comps = [c["name"] for c in snap["self"]["compositions"]]
        if comps:
            lines.append(f"compositions: {', '.join(comps)}")
        for app in snap["applications"][:limit]:
            extra = ""
            if app["derived_from"]:
                extra += f" derived from {' + '.join(app['derived_from'])}"
            if app["provides"]:
                extra += f" provides {', '.join(app['provides'])}"
            lines.append(f"app: {app['title']} ({app['kind']}){extra}")
        for rel in snap["relations"][:limit]:
            lines.append(f"link: {rel['from']} ~ {rel['to']} via {rel['mechanic']}")
        if snap["user"]:
            lines.append("the person has asked me to remember:")
            for item in snap["user"][-limit:]:
                about = f" [{item['subject']}]" if item["subject"] else ""
                lines.append(f"  - {item['text']}{about}")
        return "\n".join(lines)

    def export(self, path: Path) -> Path:
        """Write the snapshot out — to move it to another machine or model."""
        path = Path(path)
        path.write_text(json.dumps(self.snapshot(), indent=2), encoding="utf-8")
        return path


# ======================================================================
# How a person talks to it
# ======================================================================
@dataclass(frozen=True)
class WorldRequest:
    verb: str          # remember | forget | forget_all | recall | self | noticed | keep | discard
    argument: str = ""


_LEAD = re.compile(r"^(computer|cdos)[,\s]+", re.I)


def parse_request(text: str) -> Optional[WorldRequest]:
    """Recognise the handful of sentences that address the World Model.

    Anchored at the start of the utterance, so *"remember"* inside some other
    sentence is never taken as an instruction to store something.
    """
    raw = _LEAD.sub("", (text or "").strip()).strip()
    low = " ".join(re.sub(r"[^\w\s']", " ", raw.lower()).split())
    if not low:
        return None

    if low in ("forget everything about me", "forget everything you know about me",
               "forget all about me", "forget everything"):
        return WorldRequest("forget_all")
    if low in ("what do you know about me", "what do you remember about me",
               "what have you remembered"):
        return WorldRequest("recall")
    if low in ("what are you", "describe yourself", "what do you know about yourself",
               "what can you do"):
        return WorldRequest("self")
    if low in ("what have you noticed", "what have you noticed about me",
               "any notes", "pending notes"):
        return WorldRequest("noticed")

    m = re.match(r"^(keep|discard) note (\d+)$", low)
    if m:
        return WorldRequest(m.group(1), m.group(2))

    body = re.sub(r"[.!?]+$", "", raw)
    m = re.match(r"^remember(?:\s+that)?\s+(.+)$", body, re.I)
    if m:
        return WorldRequest("remember", m.group(1).strip())
    m = re.match(r"^forget(?:\s+that)?\s+(.+)$", body, re.I)
    if m:
        return WorldRequest("forget", m.group(1).strip())
    return None


def guess_kind(text: str) -> str:
    """A rough label for something the person asked to remember."""
    low = text.lower()
    if re.search(r"\b(i (?:like|love|prefer|enjoy|hate|dislike)|my favou?rite)\b", low):
        return "preference"
    if re.search(r"\b(i'?m (?:working on|building|making)|my project)\b", low):
        return "project"
    return "memory"
