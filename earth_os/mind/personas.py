"""Personalities: many voices over one model, each with its own identity.

A :class:`Personality` is data — a JSON object a person or a game writes — and
it carries the eight things that make a voice its own:

=================  ============================================================
identity           ``id``, ``name``, ``role``
personality        ``description``, ``traits``, ``style``
context            ``background``, ``knowledge`` (what it knows of its world)
memory namespace   derived, never chosen: ``conversation/<id>`` for an OS
                   personality, ``character/<world>/<id>`` for a character
goals              ``goals``
relationships      ``relationships`` — how it regards others, to start with
permissions        ``permissions`` — what Earth Mind will do on its behalf
environment        ``os``, or ``game:<world>``
=================  ============================================================

Two rules are enforced when a personality is built, not when it is used:

**A personality is not power.** ``permissions`` may only name things in
:data:`PERMISSIONS`. An unknown permission is refused, exactly as a
composition step outside the catalogue is.

**A game character never touches the real machine.** In a ``game:``
environment the only permission that exists is ``converse``. A definition
that asks for ``system.read`` — or anything else — is refused with
:class:`PersonalityError`; it is not quietly dropped, because a world that
*asked* for the operating system is a world somebody should look at.

The memory namespace is derived from the environment and the id, so no
definition can point a character at another character's memories or at the
person's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from .memory import Principal, character_namespace, conversation_namespace

#: Everything a personality can be allowed. Nothing outside this list exists.
PERMISSIONS: Tuple[str, ...] = (
    "converse",        # talk through the model
    "knowledge",       # ask CDOS Computer (POST /v1/ask only)
    "memory.user",     # read what the person asked to be remembered; suggest pending notes
    "system.read",     # have real system readings taken and explained
    "system.act",      # have a system operation *proposed* to the shell's gated path
)
#: The whole of what a game character may hold.
GAME_PERMISSIONS: FrozenSet[str] = frozenset({"converse"})

OS = "os"
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CLEAN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

BUILTIN_DIR = Path(__file__).resolve().parent / "personalities"


class PersonalityError(ValueError):
    """A personality definition that breaks the rules above."""


def _text(value: object, limit: int) -> str:
    return " ".join(_CLEAN.sub(" ", str(value or "")).split())[:limit]


def _lines(value: object, limit: int, each: int) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise PersonalityError("expected a list of short strings")
    out = [_text(v, each) for v in value]
    return tuple(v for v in out if v)[:limit]


@dataclass(frozen=True)
class Personality:
    id: str
    name: str
    role: str = ""
    description: str = ""
    traits: Tuple[str, ...] = ()
    style: str = ""
    background: str = ""
    knowledge: Tuple[str, ...] = ()
    goals: Tuple[str, ...] = ()
    relationships: Tuple[Tuple[str, int, str], ...] = ()     # (other, score -10..10, note)
    permissions: FrozenSet[str] = frozenset({"converse"})
    environment: str = OS
    model: str = ""                    # a model spec of its own; blank = the environment's
    greeting: str = ""
    fallback: Tuple[str, ...] = ()     # what it can say with no model running
    aliases: Tuple[str, ...] = ()      # other things a person may call it: "the shopkeeper"

    # -- construction ------------------------------------------------------------
    @classmethod
    def build(cls, raw: Dict[str, Any], environment: Optional[str] = None) -> "Personality":
        if not isinstance(raw, dict):
            raise PersonalityError("a personality is a JSON object")
        env = str(environment if environment is not None else raw.get("environment", OS)).strip() or OS
        if env != OS and not re.match(r"^game:[a-z0-9][a-z0-9._-]{0,63}$", env):
            raise PersonalityError(f"environment must be 'os' or 'game:<world>', not {env!r}")
        pid = str(raw.get("id", "")).strip().lower()
        if not _ID.match(pid):
            raise PersonalityError(f"a personality id is lower-case letters, digits, . _ -: {pid!r}")
        name = _text(raw.get("name"), 40)
        if not name:
            raise PersonalityError(f"{pid}: a personality needs a name")

        asked = raw.get("permissions", ["converse"])
        if isinstance(asked, str) or not isinstance(asked, (list, tuple, set, frozenset)):
            raise PersonalityError(f"{pid}: permissions is a list")
        permissions = frozenset(str(p) for p in asked)
        unknown = sorted(permissions - set(PERMISSIONS))
        if unknown:
            raise PersonalityError(f"{pid}: no such permission: {', '.join(unknown)}")
        if env != OS:
            beyond = sorted(permissions - GAME_PERMISSIONS)
            if beyond:
                raise PersonalityError(
                    f"{pid}: a game character may only converse; it asked for {', '.join(beyond)}. "
                    f"Game worlds have no access to the operating system or to the person's memories.")

        relationships: List[Tuple[str, int, str]] = []
        rel = raw.get("relationships") or {}
        if not isinstance(rel, dict):
            raise PersonalityError(f"{pid}: relationships is an object of name → score or [score, note]")
        for other, value in rel.items():
            note = ""
            if isinstance(value, (list, tuple)) and value:
                score, note = value[0], (value[1] if len(value) > 1 else "")
            else:
                score = value
            try:
                score = max(-10, min(10, int(score)))
            except (TypeError, ValueError):
                raise PersonalityError(f"{pid}: relationship with {other!r} needs a number") from None
            relationships.append((str(other).strip().lower()[:64], score, _text(note, 160)))

        return cls(
            id=pid, name=name, role=_text(raw.get("role"), 60),
            description=_text(raw.get("description"), 400),
            traits=_lines(raw.get("traits"), 8, 40), style=_text(raw.get("style"), 300),
            background=_text(raw.get("background"), 600),
            knowledge=_lines(raw.get("knowledge"), 24, 240),
            goals=_lines(raw.get("goals"), 6, 160),
            relationships=tuple(relationships), permissions=permissions, environment=env,
            model=_text(raw.get("model"), 120), greeting=_text(raw.get("greeting"), 240),
            fallback=_lines(raw.get("fallback"), 8, 240),
            aliases=tuple(a.lower() for a in _lines(raw.get("aliases"), 8, 40)))

    # -- what it is --------------------------------------------------------------
    @property
    def world(self) -> str:
        return self.environment.split(":", 1)[1] if self.environment.startswith("game:") else ""

    @property
    def in_game(self) -> bool:
        return bool(self.world)

    @property
    def namespace(self) -> str:
        """Where this voice's memories live. Derived, so it cannot be pointed elsewhere."""
        if self.in_game:
            return character_namespace(self.world, self.id)
        return conversation_namespace(self.id)

    def principal(self) -> Principal:
        if self.in_game:
            return Principal("character", self.id, self.world, frozenset(self.permissions))
        return Principal("persona", self.id, "", frozenset(self.permissions))

    def may(self, permission: str) -> bool:
        return permission in self.permissions

    def answers_to(self, words: str) -> bool:
        """Is *words* a way to name this personality? "elara", "the shopkeeper"."""
        said = " ".join(re.sub(r"[^a-z0-9 ]", " ", (words or "").lower()).split())
        said = re.sub(r"^(?:the|a|an|my) ", "", said)
        if not said:
            return False
        names = {self.id.replace("-", " ").replace("_", " "), self.name.lower(), *self.aliases}
        if self.role:
            names.add(self.role.lower())
        return said in names

    def style_lines(self) -> List[str]:
        """This personality as prompt lines: who it is and how it speaks."""
        lines = []
        if self.role:
            lines.append(f"Your role: {self.role}.")
        if self.description:
            lines.append(self.description)
        if self.traits:
            lines.append("Your manner: " + ", ".join(self.traits) + ".")
        if self.style:
            lines.append(self.style)
        return lines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "role": self.role, "description": self.description,
            "traits": list(self.traits), "style": self.style, "background": self.background,
            "knowledge": list(self.knowledge), "goals": list(self.goals),
            "relationships": {o: [s, n] for o, s, n in self.relationships},
            "permissions": sorted(self.permissions), "environment": self.environment,
            "model": self.model, "greeting": self.greeting, "fallback": list(self.fallback),
            "aliases": list(self.aliases),
        }


class PersonalityRegistry:
    """The OS personalities: the shipped ones, then the person's own.

    Game characters are not kept here — each belongs to its world
    (:mod:`earth_os.mind.game.world`) — so listing the operating system's
    voices can never surface a character, or the other way round.
    """

    def __init__(self, directories: Iterable[Path] = (BUILTIN_DIR,)):
        self.directories = [Path(d) for d in directories]
        self.items: Dict[str, Personality] = {}
        self.unreadable: Dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self.items, self.unreadable = {}, {}
        for directory in self.directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    personality = Personality.build(raw, environment=OS)
                except (OSError, ValueError) as exc:        # failure is data
                    self.unreadable[str(path)] = f"{type(exc).__name__}: {exc}"
                    continue
                self.items[personality.id] = personality

    def add(self, personality: Personality) -> None:
        if personality.in_game:
            raise PersonalityError("game characters belong to their world, not to the OS registry")
        self.items[personality.id] = personality

    def get(self, pid: str) -> Optional[Personality]:
        return self.items.get((pid or "").strip().lower())

    def find(self, words: str) -> Optional[Personality]:
        for personality in self.items.values():
            if personality.answers_to(words):
                return personality
        return None

    def all(self) -> List[Personality]:
        return sorted(self.items.values(), key=lambda p: p.id)
