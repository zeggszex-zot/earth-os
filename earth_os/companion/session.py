"""One conversation, and the little the companion keeps in mind during it.

Working memory is compact on purpose: a 3B model's attention is the scarcest
thing in the system, and a context padded with everything said so far is
slower and no wiser. A session holds the last few turns, the topic, and the
last few facts CDOS Computer supplied — nothing else, and nothing that
outlives the session. What outlives it is the World Model's job, and only
with the person's say-so.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

MAX_TURNS = 16          # kept; fewer are sent, by mode
MAX_FACTS = 4
TURN_CHARS = 400        # a long turn is clipped in history, never in the reply


@dataclass
class Turn:
    role: str            # user | assistant
    text: str
    mode: str = ""
    at: float = field(default_factory=time.time)


class Session:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.id = uuid.uuid4().hex[:8]
        self.started = clock()
        self.last = self.started
        self.turns: Deque[Turn] = deque(maxlen=MAX_TURNS)
        self.mode: Optional[str] = None       # set by the person: "let's go deeper"
        self.topic = ""
        self.facts: Deque[str] = deque(maxlen=MAX_FACTS)

    def add(self, role: str, text: str, mode: str = "") -> None:
        self.turns.append(Turn(role, " ".join(str(text).split()), mode, self.clock()))
        self.last = self.clock()

    def note_fact(self, text: str) -> None:
        text = " ".join(str(text).split())
        if text and text not in self.facts:
            self.facts.append(text[:TURN_CHARS])

    def history(self, turns: int) -> List[Dict[str, str]]:
        """The last *turns* exchanges as chat messages, each clipped."""
        recent = list(self.turns)[-turns * 2:] if turns > 0 else []
        out = []
        for t in recent:
            text = t.text if len(t.text) <= TURN_CHARS else t.text[:TURN_CHARS - 1] + "…"
            out.append({"role": t.role, "content": text})
        return out

    def expired(self, idle_seconds: float) -> bool:
        return idle_seconds > 0 and self.clock() - self.last > idle_seconds


class Sessions:
    """The current session, replaced after a quiet spell or on request."""

    def __init__(self, idle_seconds: float = 1800.0, clock=time.time):
        self.idle_seconds = float(idle_seconds)
        self.clock = clock
        self._current: Optional[Session] = None
        self.count = 0

    def current(self) -> Session:
        if self._current is None or self._current.expired(self.idle_seconds):
            self._current = Session(self.clock)
            self.count += 1
        return self._current

    def reset(self) -> Session:
        self._current = None
        return self.current()
