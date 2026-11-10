"""A game world as data: who lives there, what is true there, how it starts.

A world is a JSON object, checked when it is read. It can describe as many
characters as it likes; it cannot give any of them anything but a voice
(:data:`earth_os.mind.personas.GAME_PERMISSIONS`), and its id becomes the
namespace everything it remembers is filed under, so two worlds can never see
each other's history.

.. code-block:: json

    {
      "id": "harbour",
      "name": "Greywater Harbour",
      "description": "A fishing town at the edge of the charts.",
      "player": {"id": "player", "name": "the traveller"},
      "facts": ["The lighthouse has been dark for three nights."],
      "state": {"lighthouse": "dark"},
      "characters": [
        {"id": "elara", "name": "Elara", "role": "shopkeeper",
         "background": "…", "goals": ["…"], "knowledge": ["…"],
         "relationships": {"brann": [4, "an old friend"]}}
      ]
    }

Failure is data: a world that does not load says why
(:class:`WorldError`), and is listed with its reason rather than dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..personas import Personality, PersonalityError

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
#: Names a world may not take: they are namespaces of the machine itself.
RESERVED = ("system", "user", "knowledge", "os")
MAX_CHARACTERS = 500

BUILTIN_WORLDS = Path(__file__).resolve().parent.parent / "worlds"


class WorldError(ValueError):
    """A world definition that cannot be used."""


def _text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True)
class WorldDefinition:
    id: str
    name: str
    description: str = ""
    player_id: str = "player"
    player_name: str = "the traveller"
    facts: Tuple[str, ...] = ()
    state: Tuple[Tuple[str, Any], ...] = ()
    characters: Tuple[Personality, ...] = ()

    @classmethod
    def build(cls, raw: Dict[str, Any]) -> "WorldDefinition":
        if not isinstance(raw, dict):
            raise WorldError("a world is a JSON object")
        wid = str(raw.get("id", "")).strip().lower()
        if not _ID.match(wid):
            raise WorldError(f"a world id is lower-case letters, digits, . _ -: {wid!r}")
        if wid in RESERVED:
            raise WorldError(f"{wid!r} names part of the machine, not a world")
        name = _text(raw.get("name"), 60) or wid
        player = raw.get("player") if isinstance(raw.get("player"), dict) else {}
        player_id = str(player.get("id", "player")).strip().lower() or "player"
        if not _ID.match(player_id):
            raise WorldError(f"player id {player_id!r} is not a name")

        listed = raw.get("characters") or []
        if not isinstance(listed, list):
            raise WorldError("characters is a list")
        if len(listed) > MAX_CHARACTERS:
            raise WorldError(f"{len(listed)} characters; the limit is {MAX_CHARACTERS}")
        characters: List[Personality] = []
        seen = {player_id}
        for entry in listed:
            try:
                # The environment is the world's, whatever the entry says: a
                # character cannot declare itself part of the operating system.
                person = Personality.build(dict(entry) if isinstance(entry, dict) else entry,
                                           environment=f"game:{wid}")
            except PersonalityError as exc:
                raise WorldError(str(exc)) from None
            if person.id in seen:
                raise WorldError(f"two characters called {person.id!r}")
            seen.add(person.id)
            characters.append(person)

        facts = raw.get("facts") or []
        if not isinstance(facts, list):
            raise WorldError("facts is a list of sentences")
        state = raw.get("state") or {}
        if not isinstance(state, dict):
            raise WorldError("state is an object")
        for key, value in state.items():
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise WorldError(f"state {key!r} must be a plain value")
        return cls(id=wid, name=name, description=_text(raw.get("description"), 400),
                   player_id=player_id, player_name=_text(player.get("name"), 40) or "the traveller",
                   facts=tuple(f for f in (_text(x, 300) for x in facts) if f)[:200],
                   state=tuple(sorted((str(k)[:64], v) for k, v in state.items())),
                   characters=tuple(characters))

    @classmethod
    def load(cls, path: Path) -> "WorldDefinition":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorldError(f"{path}: {type(exc).__name__}: {exc}") from None
        return cls.build(raw)

    def character(self, cid: str) -> Optional[Personality]:
        cid = (cid or "").strip().lower()
        for person in self.characters:
            if person.id == cid:
                return person
        return None

    def find(self, words: str) -> Optional[Personality]:
        """The character *words* names: "elara", "the shopkeeper"."""
        for person in self.characters:
            if person.answers_to(words):
                return person
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "description": self.description,
                "player": {"id": self.player_id, "name": self.player_name},
                "facts": list(self.facts), "state": dict(self.state),
                "characters": [c.to_dict() for c in self.characters]}
