"""One running world: its characters, what they remember, and what has happened.

A :class:`GameEnvironment` is what a game holds. It reinvents nothing — the
voice is Earth Mind's (:class:`~earth_os.mind.characters.CharacterVoice`, one
shared model), the memory is Earth Mind's
(:class:`~earth_os.mind.memory.MemoryStore`, one store), and this class only
knows what a *world* adds to those: a calendar, an event history, who has met
whom and how they regard each other.

What a game does with it:

=========================================  ====================================
``talk(character, text)``                  the player speaks; the character
                                           answers from its own memories
``event(text, witnesses=[…])``             something happened: it goes in the
                                           world's history, and into the
                                           memory of everyone who saw it
``remember(character, text)``              one character learns one thing
``adjust(a, b, delta)``                    how *a* regards *b* changes
``set_state(key, value)``                  the world changes
``advance(days)``                          time passes
``exchange(a, b, topic)``                  two characters speak to each other
``add_character({…})``                     somebody new arrives
``history()`` / ``relationship(a, b)``     reading it back
=========================================  ====================================

**That list is the whole of what a world can do**, and every item changes
only this world's own namespaces. There is no method here that reaches the
operating system, and no import that could: a game-world action and a
real-world system action are different types in different packages
(:class:`earth_os.mind.envelope.Proposal` is the only thing that can ask the
machine for anything, and nothing in ``earth_os.mind.game`` can construct a
route to it). ``tests/test_mind_game.py`` checks the imports.

Everything is persistent. Close the program, come back days later, build the
environment again from the same definition and store: the day, the history,
every relationship and every character's memories are where they were left.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..characters import CharacterReply, CharacterVoice, Scene
from ..memory import (MemoryStore, Principal, Record, character_namespace, events_namespace,
                      world_namespace)
from ..personas import Personality, PersonalityError
from .world import WorldDefinition, WorldError

_POSITIVE = re.compile(r"\b(?:help(?:ed)?|saved?|rescued?|gave|paid|healed|defended|protected|"
                       r"returned|found|thanked|freed)\b", re.I)
_NEGATIVE = re.compile(r"\b(?:stole|robbed|attacked|hit|lied|betrayed|insulted|threatened|cheated|"
                       r"abandoned|hurt)\b", re.I)


@dataclass(frozen=True)
class Relationship:
    """How one character regards another. Directional: Elara's view of Brann is not his of her."""

    of: str
    toward: str
    score: int = 0                     # -10 … 10
    note: str = ""

    @property
    def words(self) -> str:
        if self.score <= -6:
            feeling = "an enemy; you do not trust them at all"
        elif self.score <= -2:
            feeling = "you are wary of them"
        elif self.score < 2:
            feeling = "you barely know them"
        elif self.score < 6:
            feeling = "you are on friendly terms"
        else:
            feeling = "a trusted friend"
        return f"{feeling} ({self.note})" if self.note else feeling


class GameEnvironment:
    def __init__(self, definition: WorldDefinition, store: MemoryStore, voice: CharacterVoice):
        self.definition = definition
        self.world = definition.id
        self.store = store
        self.voice = voice
        #: This world's own view: its namespaces and nobody else's.
        self.view = store.view(Principal("game", world=self.world))
        self.ns_world = world_namespace(self.world)
        self.ns_events = events_namespace(self.world)
        self._lock = threading.RLock()
        self._characters: Dict[str, Personality] = {c.id: c for c in definition.characters}
        self._seed()
        self._load_arrivals()

    # -- starting and resuming ----------------------------------------------------
    def _seed(self) -> None:
        """Lay the world down the first time only. A resumed world is left as it was."""
        if self.view.find(self.ns_world, "meta", "seeded") is not None:
            return
        for fact in self.definition.facts:
            self.view.add(self.ns_world, fact, kind="fact", importance=0.6, visibility="public")
        for key, value in self.definition.state:
            self._put_state(key, value)
        self._put_state("day", 1)
        for person in self.definition.characters:
            for other, score, note in person.relationships:
                self._put_relationship(person.id, other, score, note)
        self.view.put(self.ns_world, "meta", "seeded", f"{self.definition.name} began.")

    def _load_arrivals(self) -> None:
        """Characters added after the world began are part of it now."""
        for record in self.view.recent(self.ns_world, limit=1000, kinds=("character",)):
            try:
                person = Personality.build(record.data, environment=f"game:{self.world}")
            except PersonalityError:
                continue                    # a damaged record is skipped, never run
            self._characters.setdefault(person.id, person)

    # -- who is here ----------------------------------------------------------------
    def characters(self) -> List[Personality]:
        return sorted(self._characters.values(), key=lambda p: p.id)

    def character(self, cid: str) -> Optional[Personality]:
        return self._characters.get((cid or "").strip().lower())

    def find(self, words: str) -> Optional[Personality]:
        for person in self.characters():
            if person.answers_to(words):
                return person
        return None

    def add_character(self, raw: Dict[str, Any]) -> Personality:
        """Somebody new, from a personality and world-specific context.

        The same checks as a character in the definition: a voice and nothing
        else. Asking for more is refused, and nothing is added.
        """
        try:
            person = Personality.build(dict(raw), environment=f"game:{self.world}")
        except PersonalityError as exc:
            raise WorldError(str(exc)) from None
        if person.id == self.definition.player_id:
            raise WorldError(f"{person.id!r} is the player")
        with self._lock:
            self._characters[person.id] = person
            self.view.put(self.ns_world, "character", person.id, f"{person.name} ({person.role or 'a newcomer'})",
                          data=person.to_dict())
            for other, score, note in person.relationships:
                if self.view.find(self.ns_world, "relationship", f"{person.id}>{other}") is None:
                    self._put_relationship(person.id, other, score, note)
        return person

    def _name(self, who: str) -> str:
        if who == self.definition.player_id:
            return self.definition.player_name
        person = self._characters.get(who)
        return person.name if person else who

    # -- the world's state and calendar ---------------------------------------------
    def _put_state(self, key: str, value: Any) -> None:
        self.view.put(self.ns_world, "state", str(key)[:64], f"{key} is {value}", data={"value": value})

    def state(self, key: str, default: Any = None) -> Any:
        record = self.view.find(self.ns_world, "state", str(key)[:64])
        return record.data.get("value", default) if record is not None else default

    def set_state(self, key: str, value: Any) -> None:
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise WorldError(f"state {key!r} must be a plain value")
        if str(key) == "day":
            raise WorldError("the day moves with advance()")
        self._put_state(key, value)

    @property
    def day(self) -> int:
        return int(self.state("day", 1) or 1)

    def advance(self, days: int = 1) -> int:
        """Time passes. Returns the new day."""
        days = max(0, int(days))
        with self._lock:
            now = self.day + days
            self._put_state("day", now)
        return now

    # -- relationships ----------------------------------------------------------------
    def _put_relationship(self, of: str, toward: str, score: int, note: str = "") -> None:
        score = max(-10, min(10, int(score)))
        self.view.put(self.ns_world, "relationship", f"{of}>{toward}",
                      f"{self._name(of)} regards {self._name(toward)}: {score}",
                      data={"of": of, "toward": toward, "score": score, "note": note})

    def relationship(self, of: str, toward: str) -> Relationship:
        record = self.view.find(self.ns_world, "relationship", f"{of}>{toward}")
        if record is None:
            return Relationship(of, toward)
        return Relationship(of, toward, int(record.data.get("score", 0)), str(record.data.get("note", "")))

    def adjust(self, of: str, toward: str, delta: int, note: Optional[str] = None) -> Relationship:
        with self._lock:
            current = self.relationship(of, toward)
            self._put_relationship(of, toward, current.score + int(delta),
                                   current.note if note is None else note)
            return self.relationship(of, toward)

    # -- what happens -------------------------------------------------------------------
    def event(self, text: str, witnesses: Iterable[str] = (), kind: str = "event",
              importance: float = 0.6, relationships: Sequence[Tuple[str, str, int]] = (),
              state: Optional[Dict[str, Any]] = None) -> Record:
        """Something happened. History gets it; so does everyone who saw it.

        A witness remembers the event in its own namespace, so it can speak of
        it later; a character who was not there has no record of it and its
        view cannot read the history. *relationships* is ``(of, toward,
        delta)`` changes and *state* is world state to set, applied with it.
        """
        seen = [w for w in dict.fromkeys(str(x).strip().lower() for x in witnesses)
                if w in self._characters]
        with self._lock:
            day = self.day
            record = self.view.add(self.ns_events, text, kind=kind, importance=importance,
                                   data={"day": day, "witnesses": seen})
            for witness in seen:
                self.view.add(character_namespace(self.world, witness), text, kind="event",
                              importance=importance, data={"day": day, "event": record.id})
            for of, toward, delta in relationships:
                self.adjust(of, toward, delta)
            for key, value in (state or {}).items():
                self.set_state(key, value)
        return record

    def remember(self, character: str, text: str, importance: float = 0.6) -> Record:
        """One character learns one thing, privately."""
        person = self._require(character)
        return self.view.add(character_namespace(self.world, person.id), text, kind="memory",
                             importance=importance, data={"day": self.day})

    def player_note(self, character: str, text: str) -> Record:
        """"Remember that I helped this character." — the player's own account.

        Filed as an event the character witnessed, in words about the player
        rather than "I", and it nudges how the character regards them. It is
        marked as the player's say-so (``kind = "note"``): a game with rules of
        its own records what really happened with :meth:`event`.
        """
        person = self._require(character)
        player = self.definition.player_name
        said = " ".join(str(text or "").split()).rstrip(".!")
        said = re.sub(r"\b(?:this|that) character\b|\b(?:him|them)\b", person.name, said, flags=re.I)
        # "helped her" is the character; "carry her stock" is not.
        said = re.sub(r"\bher\b(?=\s*(?:$|[.,;!?]|(?:and|but|when|with|to|at|in|on|out|up|today|yesterday)\b))",
                      person.name, said, flags=re.I)
        said = re.sub(r"^i\b", player[:1].upper() + player[1:], said, flags=re.I)
        said = re.sub(r"\bi\b", player, said, flags=re.I)
        said = re.sub(r"\b(?:my|mine)\b", f"{player}'s", said, flags=re.I)
        said = re.sub(r"\byou\b", person.name, said, flags=re.I)
        delta = 1 if _POSITIVE.search(said) else -1 if _NEGATIVE.search(said) else 0
        changes = [(person.id, self.definition.player_id, delta)] if delta else []
        return self.event(said + ".", witnesses=[person.id], kind="note", importance=0.7,
                          relationships=changes)

    def history(self, limit: int = 20, about: str = "") -> List[Record]:
        """The world's event history, oldest first; *about* narrows it by relevance."""
        if about:
            found = self.view.recall(self.ns_events, about, limit=limit)
            return sorted(found, key=lambda r: r.seq)
        return self.view.recent(self.ns_events, limit=limit)

    # -- talking ------------------------------------------------------------------------
    def _require(self, character: str) -> Personality:
        person = self.character(character) or self.find(character)
        if person is None:
            raise WorldError(f"nobody called {character!r} in {self.definition.name}")
        return person

    def _scene(self, person: Personality, speaker_id: str, text: str = "") -> Scene:
        met = self.view.find(self.ns_world, "met", f"{person.id}>{speaker_id}")
        return Scene(world_name=self.definition.name, speaker=self._name(speaker_id),
                     speaker_id=speaker_id, day=self.day,
                     regard=self.relationship(person.id, speaker_id).words,
                     last_met=int(met.data.get("day", 0)) if met is not None else None,
                     facts=tuple(self._regard_for_those_named(person, speaker_id, text)))

    def _regard_for_those_named(self, person: Personality, speaker_id: str, text: str) -> List[str]:
        """How *person* regards anyone *text* mentions by name: its own view of them, only.

        Asked about the smuggler, the shopkeeper should know what she thinks
        of him. What he thinks of her is his, and is not looked up here.
        """
        said = " " + " ".join(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()) + " "
        out: List[str] = []
        for other in self.characters():
            if other.id in (person.id, speaker_id):
                continue
            names = {other.id.replace("-", " ").replace("_", " "), other.name.lower(), *other.aliases}
            if any(f" {name} " in said for name in names if name):
                out.append(f"How you regard {other.name}: {self.relationship(person.id, other.id).words}.")
        return out[:3]

    def greet(self, character: str, speaker: str = "") -> str:
        """What *character* says as *speaker* walks up. Written lines only; nothing is recorded."""
        person = self._require(character)
        speaker_id = (speaker or self.definition.player_id).strip().lower()
        met = self.view.find(self.ns_world, "met", f"{person.id}>{speaker_id}")
        if met is None and person.greeting:
            return person.greeting
        if met is not None:
            gap = self.day - int(met.data.get("day", self.day))
            again = "You again." if gap <= 0 else f"Back after {gap} day{'s' if gap != 1 else ''}."
            return f"{person.name} looks up. {again}"
        return f"{person.name} looks up."

    def talk(self, character: str, text: str, speaker: str = "") -> CharacterReply:
        """*speaker* (the player, unless named) says *text* to *character*."""
        person = self._require(character)
        speaker_id = (speaker or self.definition.player_id).strip().lower()
        scene = self._scene(person, speaker_id, text)
        reply = self.voice.turn(person, text, scene)
        if reply.source not in ("notice", "fixed"):
            self.view.put(self.ns_world, "met", f"{person.id}>{speaker_id}",
                          f"{person.name} spoke with {scene.speaker} on day {scene.day}",
                          data={"day": scene.day})
        return reply

    def exchange(self, first: str, second: str, topic: str) -> List[CharacterReply]:
        """Two characters speak to each other about *topic*: one line each.

        Each speaks from its own memories and hears only what the other said
        aloud. Neither is shown the other's prompt, memories or regard.
        """
        a, b = self._require(first), self._require(second)
        opening = self.voice.turn(
            a, f"({b.name} is here. Say something to {b.name} about {topic}.)",
            self._scene(a, b.id), remember=False)
        answer = self.talk(b.id, opening.text, speaker=a.id)
        # The first speaker hears the answer; that is all it learns of the other.
        heard = answer.text if len(answer.text) <= 300 else answer.text[:299] + "…"
        self.view.add(character_namespace(self.world, a.id), f'{b.name} said to me: "{heard}"',
                      kind="heard", subject=b.id, data={"day": self.day}, importance=0.35)
        self.view.put(self.ns_world, "met", f"{a.id}>{b.id}",
                      f"{a.name} spoke with {b.name} on day {self.day}", data={"day": self.day})
        return [opening, answer]

    # -- reading it back --------------------------------------------------------------------
    def memories(self, character: str, limit: int = 20) -> List[Record]:
        """A character's memories, for the game's own tools. Never put one in another's prompt."""
        person = self._require(character)
        return self.view.recent(character_namespace(self.world, person.id), limit=limit)

    def summary(self) -> Dict[str, Any]:
        return {"world": self.world, "name": self.definition.name, "day": self.day,
                "characters": [c.id for c in self.characters()],
                "events": self.view.count(self.ns_events)}
