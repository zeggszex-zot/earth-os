"""The worlds this machine hosts, and who is talking to whom in them.

Earth Mind hosts games; a game does not bring a mind of its own. The
:class:`GameHost` is the small piece that makes that true in practice: it
finds world definitions (the shipped ones, then the person's), opens each as a
:class:`~earth_os.mind.game.environment.GameEnvironment` over the shared store
and the shared voice, and remembers — per interface — which character a
person is in the middle of talking to, so that the next thing they type goes
to that character and not to the operating system.

An open conversation is the *only* state here, and it is deliberately not
persistent: restarting the shell puts every interface back at the operating
system. The world itself — its day, its history, its people's memories —
lives in the store and is exactly where it was.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from ..characters import CharacterVoice
from ..memory import MemoryStore
from ..personas import Personality
from .environment import GameEnvironment
from .world import BUILTIN_WORLDS, WorldDefinition, WorldError


class GameHost:
    def __init__(self, store: MemoryStore, voice: CharacterVoice,
                 directories: Iterable[Path] = (BUILTIN_WORLDS,), default: str = ""):
        self.store = store
        self.voice = voice
        self.directories = [Path(d) for d in directories]
        self.default = (default or "").strip().lower()
        self.definitions: Dict[str, WorldDefinition] = {}
        self.unreadable: Dict[str, str] = {}
        self._open: Dict[str, GameEnvironment] = {}
        self._talking: Dict[str, Tuple[str, str]] = {}     # interface → (world, character)
        self._lock = threading.RLock()
        self.reload()

    # -- definitions ---------------------------------------------------------------
    def reload(self) -> None:
        with self._lock:
            self.definitions, self.unreadable = {}, {}
            for directory in self.directories:
                if not directory.is_dir():
                    continue
                for path in sorted(directory.glob("*.json")):
                    try:
                        definition = WorldDefinition.load(path)
                    except WorldError as exc:           # failure is data
                        self.unreadable[str(path)] = str(exc)
                        continue
                    self.definitions[definition.id] = definition

    def install(self, raw: Dict, directory: Optional[Path] = None) -> WorldDefinition:
        """Check a world and add it. Written to *directory* when one is given."""
        definition = WorldDefinition.build(raw)
        with self._lock:
            self.definitions[definition.id] = definition
            self._open.pop(definition.id, None)
        if directory is not None:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{definition.id}.json").write_text(
                json.dumps(definition.to_dict(), indent=2) + "\n", encoding="utf-8")
        return definition

    def worlds(self) -> List[str]:
        return sorted(self.definitions)

    # -- environments ----------------------------------------------------------------
    def open(self, world: str = "") -> Optional[GameEnvironment]:
        """The running world called *world* (the default when blank), opened on first use."""
        wid = (world or self.default).strip().lower()
        with self._lock:
            if wid in self._open:
                return self._open[wid]
            definition = self.definitions.get(wid)
            if definition is None:
                return None
            environment = GameEnvironment(definition, self.store, self.voice)
            self._open[wid] = environment
            return environment

    def find_character(self, words: str, world: str = "") -> Optional[Tuple[GameEnvironment, Personality]]:
        """Who *words* names: in *world*, else the default world, else any world."""
        order = [w for w in dict.fromkeys([world, self.default, *self.worlds()]) if w]
        for wid in order:
            definition = self.definitions.get(wid)
            if definition is None:
                continue
            # The definition answers for the cast it shipped with; arrivals
            # need the running world, which is only opened when one matches.
            person = definition.find(words)
            if person is None and wid in self._open:
                person = self._open[wid].find(words)
            if person is not None:
                environment = self.open(wid)
                if environment is not None:
                    return environment, person
        return None

    # -- who is talking to whom --------------------------------------------------------
    def enter(self, interface: str, environment: GameEnvironment, person: Personality) -> None:
        with self._lock:
            self._talking[interface] = (environment.world, person.id)

    def leave(self, interface: str) -> Optional[Tuple[str, str]]:
        with self._lock:
            return self._talking.pop(interface, None)

    def active(self, interface: str) -> Optional[Tuple[GameEnvironment, Personality]]:
        with self._lock:
            held = self._talking.get(interface)
        if held is None:
            return None
        environment = self.open(held[0])
        person = environment.character(held[1]) if environment is not None else None
        if environment is None or person is None:
            self.leave(interface)
            return None
        return environment, person

    def status(self) -> str:
        worlds = ", ".join(self.worlds()) or "none"
        return f"{len(self.definitions)} world{'s' if len(self.definitions) != 1 else ''} ({worlds})"
