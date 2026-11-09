"""Compositions: naming a sequence of things the machine can already do.

This is rung 1 of ``docs/SELF_MODIFICATION.md``, and the reason it comes first
is that it delivers most of what "a computer that modifies itself" is wanted
for **while needing no model at all**::

    "movie mode"  =  display.set_brightness(0.2)
                     audio.set_volume(0.7)

A composition is a new **name**, not new **power**. Every step is already in
the catalogue, already gated, already phrase-able. Nothing about the machine's
reach changes, which is what makes this safe by construction rather than by
trust.

## The five rules that keep it that way

**1. The catalogue stays closed.** Every step names a capability in
``CATALOGUE``, checked when the composition is *built*, not when it is run. A
composition referring to something that does not exist is refused at the door
rather than discovered halfway through — which would leave the machine in a
state nobody asked for.

**2. Effectfulness inherits.** A composition containing one effectful step is
effectful. Without this, composition is a laundering route: wrap
``system.shutdown`` in something called "tidy up" and the confirmation
disappears. :attr:`Composition.effectful` is computed from the steps and
cannot be set by hand — there is no field for it, on purpose.

**3. Compositions are flat.** A step names a capability, never another
composition. Nesting would bring cycles, unbounded expansion and an
effectfulness that has to be computed transitively through data a person
edited by hand. Flat means a composition can be read and understood in one
screen, and it is the difference between data and a programming language. If
this ever needs to change it is a new rung, not a tweak.

**4. Arguments are literals.** ``display.set_brightness(0.2)``, never
``window.fullscreen(the focused one)``. The moment a step can refer to
something resolved at run time, a composition stops being data and starts
being code, and everything above stops being checkable in advance.

**5. Nothing here can run anything.** This module holds no registry and has no
``run`` method, so there is no way to execute a composition except through
:meth:`earth_os.core.Shell.run_composition`, which applies the confirmation.
That is deliberate and it is not theoretical: this codebase has already
shipped an effectful gate that was bypassed because one caller reached for
``registry.invoke`` instead of the gated path, and "shut down" powered the
machine off with nothing to press. A convenience runner here would rebuild
that hole in a place where nobody would think to look for it.

## Where they live

A composition is data. Built-ins are in :data:`BUILTIN`; everything else is
JSON in the data directory beside ``semantics.json``, and deleting that file
removes every learned composition without touching the program. That is the
same property the semantic store has, and for the same reason: a machine that
learns must be a machine you can ask to forget.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .platform.capabilities import CATALOGUE

SCHEMA_VERSION = 1

#: A composition name may not look like a capability name. ``system.reboot``
#: as a composition would shadow the real one everywhere it is mentioned, and
#: the shadowing would be invisible in every place that prints a name.
_LOOKS_LIKE_CAPABILITY = re.compile(r"^[a-z_]+\.[a-z_]+$")


class CompositionError(ValueError):
    """A composition that must not be allowed to exist.

    Raised at construction. Every reason is a rule from the module docstring,
    so the message says which one — a composition rejected without saying why
    teaches nobody anything.
    """


def normalise(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace.

    The same shape as the corpus's own normaliser, because a composition is
    matched against the same utterances by the same rules.
    """
    text = (text or "").lower().strip()
    text = re.sub(r"[^\w\s%]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class Step:
    """One capability call inside a composition.

    Arguments are literals only (rule 4). There is no mechanism by which a
    step can refer to something resolved at run time, and that absence is the
    feature.
    """

    capability: str
    arguments: Dict[str, object] = field(default_factory=dict)

    @property
    def effectful(self) -> bool:
        spec = CATALOGUE.get(self.capability)
        return bool(spec and spec.effectful)

    @property
    def summary(self) -> str:
        spec = CATALOGUE.get(self.capability)
        return spec.summary if spec else self.capability

    def to_dict(self) -> dict:
        return {"capability": self.capability,
                "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class Composition:
    """A named sequence of capabilities.

    Construct through :meth:`build`, never directly: the rules are checked
    there, and a ``Composition`` that exists is one that has already passed
    them. Frozen so it cannot be edited into an invalid state afterwards.
    """

    name: str
    steps: Tuple[Step, ...]
    phrasings: Tuple[str, ...]
    summary: str = ""
    source: str = "learned"
    created: float = 0.0

    # ------------------------------------------------------------------
    @property
    def effectful(self) -> bool:
        """True if *any* step is effectful. Rule 2, and there is no override.

        Deliberately a property rather than a field: a stored composition
        cannot claim to be harmless, because nothing reads a claim. The
        catalogue is asked about each step every time.
        """
        return any(step.effectful for step in self.steps)

    @property
    def effectful_steps(self) -> Tuple[str, ...]:
        """Which steps made it effectful, for saying so in the confirmation."""
        return tuple(s.capability for s in self.steps if s.effectful)

    def describe(self) -> str:
        """What this will do, in the order it will do it."""
        return "\n".join(f"  {i + 1}. {s.summary}"
                         for i, s in enumerate(self.steps))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
            "phrasings": list(self.phrasings),
            "summary": self.summary,
            "source": self.source,
            "created": self.created,
        }

    # ------------------------------------------------------------------
    @classmethod
    def build(cls, name: str, steps, phrasings, summary: str = "",
              source: str = "learned",
              created: Optional[float] = None) -> "Composition":
        """Check every rule, then make one. The only way in.

        Raises :class:`CompositionError` naming the rule that was broken.
        """
        raw_name = (name or "").strip().lower()
        clean_name = normalise(name)
        if not clean_name:
            raise CompositionError("a composition needs a name")

        # Two separate questions, and running them together gets it wrong in
        # both directions.
        #
        # *Shape* applies to the name as typed, because normalising strips the
        # dot: "system.reboot" becomes "system reboot", which sails past a
        # shape test applied to the cleaned form. That hole was real.
        #
        # *Collision* applies to the dotted form of the cleaned name, so
        # "system reboot" is caught too — but only as a lookup, never as a
        # shape test. Every two-word name dots into something capability-
        # shaped, so shape-testing it would refuse "movie mode". That
        # overshoot was also real, and it rejected the built-ins.
        if _LOOKS_LIKE_CAPABILITY.match(raw_name):
            raise CompositionError(
                f"{name!r} is shaped like a capability name; "
                "a composition must not be able to shadow one")
        if raw_name in CATALOGUE or clean_name.replace(" ", ".") in CATALOGUE:
            raise CompositionError(f"{name!r} is already a capability")

        built: List[Step] = []
        for raw in steps or ():
            step = raw if isinstance(raw, Step) else Step(
                capability=str(raw.get("capability", "")).strip().lower(),
                arguments=dict(raw.get("arguments", {})))
            spec = CATALOGUE.get(step.capability)
            if spec is None:
                # Rule 1. This is the gate, and it is the whole reason a
                # composition cannot extend the machine.
                raise CompositionError(
                    f"{step.capability!r} is not a capability, so "
                    f"{name!r} cannot contain it")
            # Rule 4, enforced by the catalogue's own signature check: a step
            # that does not supply what its capability requires can never run,
            # and finding that out mid-composition is exactly the partial
            # application this refuses to allow.
            missing = [p for p in spec.params if p not in step.arguments]
            if missing:
                raise CompositionError(
                    f"{step.capability} needs {', '.join(missing)}, and a "
                    "composition supplies literals only")
            allowed = set(spec.params) | set(spec.optional)
            extra = [k for k in step.arguments if k not in allowed]
            if extra:
                raise CompositionError(
                    f"{step.capability} does not take {', '.join(sorted(extra))}")
            built.append(step)

        if not built:
            raise CompositionError(f"{name!r} has no steps")

        spoken = tuple(dict.fromkeys(
            normalise(p) for p in (phrasings or ()) if normalise(p)))
        if not spoken:
            # The corpus drift-guard, applied to compositions: a composition
            # nobody can ask for is a thing the machine does that nobody
            # wanted. The name itself counts, so this is easy to satisfy.
            raise CompositionError(
                f"{name!r} has no phrasings, so nothing could ever ask for it")

        return cls(name=clean_name, steps=tuple(built), phrasings=spoken,
                   summary=summary.strip(), source=source,
                   created=time.time() if created is None else created)


@dataclass
class CompositionMatch:
    """A composition an utterance named, and the phrase that named it."""

    composition: Composition
    phrase: str


#: Compositions this machine ships with.
#:
#: Two, and both reversible. A built-in is a policy decision about what the
#: machine does out of the box, so the restraint the catalogue asks for
#: applies here too: this is not a number to raise. Neither is effectful,
#: which is not an accident — shipping a composition that powers something off
#: would be exactly the kind of convenience this architecture exists to refuse.
BUILTIN: Tuple[dict, ...] = (
    {
        "name": "status report",
        "summary": "how the machine is doing, in one answer",
        "phrasings": ("status report", "status", "how are you doing",
                      "report", "systems report", "how are things"),
        "steps": (
            {"capability": "hardware.cpu_load", "arguments": {}},
            {"capability": "hardware.memory", "arguments": {}},
            {"capability": "hardware.disk", "arguments": {}},
            {"capability": "hardware.uptime", "arguments": {}},
        ),
    },
    {
        "name": "movie mode",
        "summary": "dim the screen and set a comfortable volume",
        "phrasings": ("movie mode", "film mode", "cinema mode",
                      "watching a film", "movie time"),
        "steps": (
            {"capability": "display.set_brightness", "arguments": {"level": 0.2}},
            {"capability": "audio.set_volume", "arguments": {"level": 0.7}},
        ),
    },
)


def default_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "earth-os" / "compositions.json"


class CompositionStore:
    """The compositions this machine knows, and how they are asked for.

    Holds no platform, no registry and no way to invoke anything (rule 5). It
    answers two questions: what compositions exist, and which one did somebody
    just name.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_path()
        self.compositions: Dict[str, Composition] = {}
        self.rejected: List[str] = []
        self.last_error = ""
        self._lock = threading.RLock()
        self._ordered: List[Tuple[str, str]] = []

    # -- persistence -------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None,
             builtins: bool = True) -> "CompositionStore":
        store = cls(path)
        store.reload(builtins=builtins)
        return store

    def reload(self, builtins: bool = True) -> None:
        with self._lock:
            self.compositions.clear()
            self.rejected.clear()
            if builtins:
                for raw in BUILTIN:
                    self._accept(dict(raw, source="builtin"), quiet=False)
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                self._reindex()
                return                      # absent or corrupt: builtins only
            for raw in payload.get("compositions", []):
                if isinstance(raw, dict):
                    self._accept(raw, quiet=False)
            self._reindex()

    def _accept(self, raw: dict, quiet: bool = True) -> Optional[Composition]:
        """Build one from stored data, or record why it was refused.

        A bad composition on disk must not stop the others loading, and must
        not load silently either: an edited file with a typo in a capability
        name should produce a visible refusal rather than a machine that has
        quietly forgotten something the person wrote.
        """
        try:
            composition = Composition.build(
                name=raw.get("name", ""),
                steps=raw.get("steps", ()),
                phrasings=raw.get("phrasings", ()),
                summary=raw.get("summary", ""),
                source=raw.get("source", "learned"),
                created=raw.get("created"))
        except CompositionError as exc:
            self.rejected.append(str(exc))
            return None
        self.compositions[composition.name] = composition
        return composition

    def save(self) -> bool:
        """Write the learned ones. Built-ins are code and are not written out."""
        with self._lock:
            payload = {
                "schema": SCHEMA_VERSION,
                "saved": time.time(),
                "compositions": [c.to_dict()
                                 for c in self.compositions.values()
                                 if c.source != "builtin"],
            }
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                os.replace(tmp, self.path)
                return True
            except OSError as exc:
                self.last_error = str(exc)
                return False

    # -- definition --------------------------------------------------------
    def define(self, name: str, steps, phrasings=(), summary: str = "",
               source: str = "learned") -> Composition:
        """Add one, or replace a learned one of the same name.

        A built-in cannot be replaced from here: it is code, and something
        that edits code is a different rung with a different safety argument.
        """
        with self._lock:
            clean = normalise(name)
            existing = self.compositions.get(clean)
            if existing is not None and existing.source == "builtin":
                raise CompositionError(
                    f"{name!r} is built in; choose another name")
            spoken = tuple(phrasings) or (clean,)
            composition = Composition.build(
                name=name, steps=steps, phrasings=spoken,
                summary=summary, source=source)
            self.compositions[composition.name] = composition
            self._reindex()
            return composition

    def forget(self, name: str) -> bool:
        """Remove a learned composition. Built-ins stay."""
        with self._lock:
            clean = normalise(name)
            existing = self.compositions.get(clean)
            if existing is None or existing.source == "builtin":
                return False
            del self.compositions[clean]
            self._reindex()
            return True

    # -- resolution --------------------------------------------------------
    def _reindex(self) -> None:
        """Phrase to composition, longest phrase first.

        Same ordering rule as the corpus: whoever recognised more of the
        sentence understood more of it.
        """
        self._ordered = sorted(
            ((phrase, composition.name)
             for composition in self.compositions.values()
             for phrase in composition.phrasings),
            key=lambda pair: len(pair[0]), reverse=True)

    def resolve(self, text: str) -> Optional[CompositionMatch]:
        """The composition this utterance names, or None."""
        cleaned = normalise(text)
        if not cleaned:
            return None
        for phrase, name in self._ordered:
            if re.search(rf"(?:^|\s){re.escape(phrase)}(?:\s|$)", cleaned):
                composition = self.compositions.get(name)
                if composition is not None:
                    return CompositionMatch(composition, phrase)
        return None

    # -- introspection -----------------------------------------------------
    def names(self) -> List[str]:
        return sorted(self.compositions)

    def get(self, name: str) -> Optional[Composition]:
        return self.compositions.get(normalise(name))

    def unsupported(self, registry) -> Dict[str, List[str]]:
        """Compositions this machine cannot run, and which step stops them.

        A robot has no screen, so *movie mode* is not a thing it can do. Saying
        which step is missing is more useful than discovering it at step two,
        and it lets a screen grey out what will not work instead of offering
        a control that cannot succeed.
        """
        out: Dict[str, List[str]] = {}
        for name, composition in self.compositions.items():
            missing = [s.capability for s in composition.steps
                       if not registry.supported(s.capability)]
            if missing:
                out[name] = missing
        return out

    def describe(self) -> str:
        """The compositions as text, for a person or a model."""
        lines: List[str] = []
        for name in self.names():
            composition = self.compositions[name]
            mark = " [asks first]" if composition.effectful else ""
            lines.append(f"{name}{mark} — {composition.summary}")
            lines.append(composition.describe())
            lines.append(f"    said as: {' · '.join(composition.phrasings)}")
        return "\n".join(lines)
