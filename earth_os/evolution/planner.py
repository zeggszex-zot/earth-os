"""The Evolution Planner and the Approval Manager: the roadmap and who moves it.

::

    DISCOVERY → ANALYSIS → PROPOSAL → USER APPROVAL → IMPLEMENTATION → TESTING → VERIFICATION
     DISCOVERED            PROPOSED     APPROVED       IN DEVELOPMENT   TESTING    COMPLETED
                               └──────── REJECTED                └──────── ROLLED BACK

A proposal is a record. Moving one along the line changes that record and
nothing else — except at one point, :meth:`Planner.implement`, where a
proposal that carries a setting change has that change applied by
:mod:`.changes`, and only because the proposal is ``APPROVED``.

Two kinds of method, and the difference is the safety property:

**What the engine may do** — :meth:`Planner.discover` and
:meth:`Planner.propose`. They write down that something was noticed and what
could be done about it. The rest of ``earth_os/evolution/`` calls these and
no others; ``tests/test_evolution.py`` reads the source to check.

**What only a person may do** — everything in :data:`DECISIONS`. Each takes
``by``, the name of whoever pressed, refuses without one, and records it. No
code path in the engine supplies a name: the screens do, from a button.

*Postponed* is a mark on a proposal, not a ninth status: a postponed proposal
keeps its place in the line and simply stops being offered until resumed.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from .changes import ChangeApplier, ChangeRefused
from .model import (
    APPROVED, COMPLETED, DISCOVERED, IN_DEVELOPMENT, PROPOSED, REJECTED,
    ROLLED_BACK, STATUSES, TESTING,
)
from .store import Store

#: Which status may follow which.
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    DISCOVERED: (PROPOSED, REJECTED),
    PROPOSED: (APPROVED, REJECTED),
    APPROVED: (IN_DEVELOPMENT, REJECTED),
    IN_DEVELOPMENT: (TESTING, ROLLED_BACK),
    TESTING: (COMPLETED, IN_DEVELOPMENT, ROLLED_BACK),
    COMPLETED: (ROLLED_BACK,),
    # A decision can be reconsidered: both of these go back to the table.
    REJECTED: (PROPOSED,),
    ROLLED_BACK: (PROPOSED,),
}

#: The methods that move a proposal on a person's say-so. Nothing under
#: ``earth_os/evolution/`` other than this module may call one.
DECISIONS = ("approve", "reject", "reconsider", "postpone", "resume", "prioritize",
             "implement", "begin_testing", "complete", "roll_back", "annotate")

PRIORITIES = ("LOW", "NORMAL", "HIGH", "URGENT")
DIFFICULTIES = ("LOW", "MODERATE", "HIGH")
#: Statuses in which a proposal is still waiting on someone.
OPEN = (DISCOVERED, PROPOSED, APPROVED, IN_DEVELOPMENT, TESTING)


class Refused(Exception):
    """The roadmap was not changed, and this says why."""


@dataclass
class Proposal:
    id: str
    title: str
    #: "optimization", "capability" or "health": which part of the center it came from.
    kind: str
    #: The thing it came from, e.g. ``optimization:phosphor-off``. One
    #: proposal per source: noticing the same thing twice is not two proposals.
    source: str
    summary: str = ""
    status: str = DISCOVERED
    priority: str = "NORMAL"
    difficulty: str = "MODERATE"
    dependencies: List[str] = field(default_factory=list)
    testing: List[str] = field(default_factory=list)
    #: docs/SELF_MODIFICATION.md rung: 0 configuration, 1–2 a change the
    #: built-in agent's gate can verify, 3 new power only a person may grant.
    rung: int = 0
    restart: bool = False
    reverse: str = ""
    #: A setting change the center can apply once approved, or None when a
    #: person (or the gated agent) has to carry the work out.
    change: Optional[Dict[str, object]] = None
    postponed: bool = False
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    #: Completion history: every status this proposal has been in, when, and
    #: on whose say-so.
    events: List[Dict[str, object]] = field(default_factory=list)
    #: What :mod:`.changes` recorded when the change was applied.
    applied: Optional[Dict[str, object]] = None
    #: Readings taken at application and at verification, when there are any.
    measured: Dict[str, object] = field(default_factory=dict)

    @property
    def open(self) -> bool:
        return self.status in OPEN

    @property
    def automatic(self) -> bool:
        """Whether the center itself can carry this out once approved."""
        return isinstance(self.change, dict)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Proposal":
        known = {f for f in cls.__dataclass_fields__}           # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


class Planner:
    """The roadmap: proposals, their order, and the rules for moving them."""

    def __init__(self, store: Store, applier: Optional[ChangeApplier] = None):
        self.store = store
        self.applier = applier
        self.proposals: Dict[str, Proposal] = {}
        self._next = 1
        self.load()

    # -- persistence -------------------------------------------------------------
    def load(self) -> None:
        data = self.store.load_roadmap()
        self.proposals = {}
        for raw in data.get("proposals", []) if isinstance(data, dict) else []:
            if not isinstance(raw, dict):
                continue
            try:
                proposal = Proposal.from_dict(raw)
            except TypeError:
                continue
            if proposal.status in STATUSES:
                self.proposals[proposal.id] = proposal
        self._next = int(data.get("next", 1) or 1) if isinstance(data, dict) else 1
        for pid in self.proposals:
            try:
                self._next = max(self._next, int(pid.split("-", 1)[1]) + 1)
            except (IndexError, ValueError):
                pass

    def save(self) -> bool:
        return self.store.save_roadmap({
            "version": 1, "next": self._next,
            "proposals": [p.to_dict() for p in self.ordered()],
        })

    # -- reading -----------------------------------------------------------------
    def get(self, pid: str) -> Proposal:
        proposal = self.proposals.get(pid)
        if proposal is None:
            raise Refused(f"no such proposal: {pid}")
        return proposal

    def by_source(self, source: str) -> Optional[Proposal]:
        for proposal in self.proposals.values():
            if proposal.source == source:
                return proposal
        return None

    def ordered(self) -> List[Proposal]:
        """Open work first: most urgent, then furthest along; postponed last.

        Furthest along first because that is what is waiting on someone — a
        proposal in TESTING needs a verdict more than a discovery needs a look.
        """
        def key(p: Proposal):
            return (0 if p.open else 1, 1 if p.postponed else 0,
                    -PRIORITIES.index(p.priority) if p.priority in PRIORITIES else 0,
                    -STATUSES.index(p.status) if p.open else STATUSES.index(p.status),
                    p.created, p.id)
        return sorted(self.proposals.values(), key=key)

    def counts(self) -> Dict[str, int]:
        out = {status: 0 for status in STATUSES}
        for proposal in self.proposals.values():
            out[proposal.status] += 1
        return out

    def blocked_by(self, proposal: Proposal) -> List[str]:
        """Dependencies of *proposal* that are not COMPLETED yet."""
        waiting = []
        for dep in proposal.dependencies:
            other = self.proposals.get(dep) or self.by_source(dep)
            if other is None or other.status != COMPLETED:
                waiting.append(dep if other is None else f"{other.id} ({other.status})")
        return waiting

    def allowed(self, proposal: Proposal) -> Tuple[str, ...]:
        return TRANSITIONS.get(proposal.status, ())

    # -- what the engine may do --------------------------------------------------
    def discover(self, *, title: str, kind: str, source: str, summary: str = "",
                 priority: str = "NORMAL", difficulty: str = "MODERATE",
                 dependencies: Optional[List[str]] = None,
                 testing: Optional[List[str]] = None, rung: int = 0,
                 restart: bool = False, reverse: str = "",
                 change: Optional[Dict[str, object]] = None) -> Tuple[Proposal, bool]:
        """Write down that something was noticed. Returns ``(proposal, created)``.

        Noticing again what is already on the roadmap creates nothing, in any
        status — including REJECTED. A person who said no is not asked again
        by the machine re-discovering the same thing every minute.
        """
        existing = self.by_source(source)
        if existing is not None:
            return existing, False
        proposal = Proposal(
            id=f"P-{self._next:04d}", title=title, kind=kind, source=source,
            summary=summary, priority=priority if priority in PRIORITIES else "NORMAL",
            difficulty=difficulty if difficulty in DIFFICULTIES else "MODERATE",
            dependencies=list(dependencies or []), testing=list(testing or []),
            rung=int(rung), restart=bool(restart), reverse=reverse,
            change=dict(change) if isinstance(change, dict) else None)
        self._next += 1
        self.proposals[proposal.id] = proposal
        self._record(proposal, DISCOVERED, by="engine", note="noticed by the center")
        self.save()
        return proposal, True

    def propose(self, pid: str, by: str = "engine", note: str = "") -> Proposal:
        """Put a discovered item on the table. Changes nothing but its status."""
        proposal = self.get(pid)
        if proposal.status == PROPOSED:
            return proposal
        self._move(proposal, PROPOSED, by or "engine", note or "written up as a proposal",
                   only_from=(DISCOVERED,))
        return proposal

    # -- what only a person may do -----------------------------------------------
    def approve(self, pid: str, by: str, note: str = "") -> Proposal:
        proposal = self.get(pid)
        self._person(by)
        if proposal.status == DISCOVERED:
            # Approving something straight off the discovery list is still
            # two decisions; record both rather than skipping one.
            self._move(proposal, PROPOSED, by, "taken up from discovery")
        self._move(proposal, APPROVED, by, note or "approved")
        proposal.postponed = False
        self.save()
        return proposal

    def reject(self, pid: str, by: str, note: str = "") -> Proposal:
        proposal = self.get(pid)
        self._person(by)
        self._move(proposal, REJECTED, by, note or "rejected")
        return proposal

    def reconsider(self, pid: str, by: str, note: str = "") -> Proposal:
        """Bring a rejected or rolled-back proposal back to the table."""
        proposal = self.get(pid)
        self._person(by)
        self._move(proposal, PROPOSED, by, note or "reconsidered",
                   only_from=(REJECTED, ROLLED_BACK))
        proposal.applied = None
        self.save()
        return proposal

    def postpone(self, pid: str, by: str, note: str = "") -> Proposal:
        proposal = self.get(pid)
        self._person(by)
        if not proposal.open:
            raise Refused(f"{proposal.id} is {proposal.status}; there is nothing to postpone")
        proposal.postponed = True
        self._record(proposal, proposal.status, by, note or "postponed", event="postponed")
        self.save()
        return proposal

    def resume(self, pid: str, by: str, note: str = "") -> Proposal:
        proposal = self.get(pid)
        self._person(by)
        proposal.postponed = False
        self._record(proposal, proposal.status, by, note or "resumed", event="resumed")
        self.save()
        return proposal

    def prioritize(self, pid: str, by: str, step: int = 1) -> Proposal:
        """Raise (*step* > 0) or lower a proposal's priority by one place."""
        proposal = self.get(pid)
        self._person(by)
        at = PRIORITIES.index(proposal.priority) if proposal.priority in PRIORITIES else 1
        new = PRIORITIES[max(0, min(len(PRIORITIES) - 1, at + (1 if step > 0 else -1)))]
        if new != proposal.priority:
            proposal.priority = new
            self._record(proposal, proposal.status, by, f"priority {new}", event="prioritized")
            self.save()
        return proposal

    def annotate(self, pid: str, by: str, note: str) -> Proposal:
        proposal = self.get(pid)
        self._person(by)
        self._record(proposal, proposal.status, by, note, event="note")
        self.save()
        return proposal

    def implement(self, pid: str, by: str, note: str = "",
                  before: Optional[Dict[str, object]] = None) -> Proposal:
        """Begin carrying out an approved proposal.

        For a proposal with a setting change this is the moment the setting
        is changed: backed up first, recorded, and refused outright unless
        the proposal is APPROVED and its dependencies are complete. For any
        other proposal nothing on the machine changes here — the record moves
        to IN DEVELOPMENT and the work is a person's, or the gated agent's.

        *before* is the readings at this moment, kept so that verification
        has something measured to compare against.
        """
        proposal = self.get(pid)
        self._person(by)
        if proposal.status != APPROVED:
            raise Refused(f"{proposal.id} is {proposal.status}, not APPROVED: "
                          "nothing is implemented without approval")
        waiting = self.blocked_by(proposal)
        if waiting:
            raise Refused(f"{proposal.id} depends on work that is not complete: "
                          + ", ".join(waiting))
        if proposal.automatic:
            if self.applier is None:
                raise Refused("this center has no way to apply a setting change")
            try:
                proposal.applied = self.applier.apply(proposal, by=by)
            except ChangeRefused as exc:
                raise Refused(str(exc)) from exc
        if before:
            proposal.measured["before"] = dict(before)
        self._move(proposal, IN_DEVELOPMENT, by,
                   note or ("setting changed; backup taken" if proposal.automatic
                            else "work started"))
        return proposal

    def begin_testing(self, pid: str, by: str, note: str = "") -> Proposal:
        proposal = self.get(pid)
        self._person(by)
        self._move(proposal, TESTING, by, note or "testing")
        return proposal

    def complete(self, pid: str, by: str, note: str = "",
                 after: Optional[Dict[str, object]] = None) -> Proposal:
        """Verification: the testing requirements were met, on *by*'s word."""
        proposal = self.get(pid)
        self._person(by)
        if after:
            proposal.measured["after"] = dict(after)
        self._move(proposal, COMPLETED, by, note or "verified", only_from=(TESTING,))
        return proposal

    def roll_back(self, pid: str, by: str, note: str = "") -> Proposal:
        """Undo a proposal. A setting change is put back; other work is the
        person's to undo, and the record says it was."""
        proposal = self.get(pid)
        self._person(by)
        if ROLLED_BACK not in self.allowed(proposal):
            raise Refused(f"{proposal.id} is {proposal.status}; there is nothing to roll back")
        if proposal.automatic and proposal.applied:
            if self.applier is None:
                raise Refused("this center has no way to roll a setting change back")
            try:
                reverted = self.applier.revert(proposal, by=by)
            except ChangeRefused as exc:
                raise Refused(str(exc)) from exc
            if isinstance(proposal.applied, dict) and isinstance(reverted, dict):
                proposal.applied["reverted"] = {
                    k: reverted.get(k) for k in ("restored", "whole_file_restored",
                                                 "in_effect_now", "backup")}
        self._move(proposal, ROLLED_BACK, by,
                   note or ("setting restored" if proposal.automatic else "rolled back"))
        return proposal

    # -- internals ---------------------------------------------------------------
    @staticmethod
    def _person(by: str) -> None:
        if not str(by or "").strip() or str(by).strip().lower() == "engine":
            raise Refused("this needs a person: no decision is recorded without a name")

    def _move(self, proposal: Proposal, status: str, by: str, note: str,
              only_from: Optional[Tuple[str, ...]] = None) -> None:
        if only_from is not None and proposal.status not in only_from:
            raise Refused(f"{proposal.id} is {proposal.status}; it cannot become {status}")
        if status not in self.allowed(proposal):
            raise Refused(f"{proposal.id} is {proposal.status}; it cannot become {status}")
        previous = proposal.status
        proposal.status = status
        self._record(proposal, status, by, note, previous=previous)
        self.save()

    def _record(self, proposal: Proposal, status: str, by: str, note: str,
                event: str = "status", previous: str = "") -> None:
        now = time.time()
        proposal.updated = now
        entry = {"at": now, "event": event, "status": status, "by": by, "note": note}
        proposal.events.append(entry)
        self.store.append_history({
            "at": now, "event": event, "proposal": proposal.id, "title": proposal.title,
            "status": status, "from": previous, "by": by, "note": note,
        })
