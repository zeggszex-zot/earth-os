"""The structured data that passes between Earth Mind and everything else.

Three shapes, and nothing else crosses the line:

:class:`Request`
    what an interface hands in — the words, and which interface they came from.

:class:`Response`
    what comes back — the words to say, which route was taken, which
    subsystems took part, and where the words came from (readings, the
    corpus, a model, a fixed notice). An interface shows the route so a person
    can tell a fact from a guess.

:class:`Proposal`
    a system operation Earth Mind would like carried out. **Earth Mind never
    carries one out itself.** It hands the proposal to the shell, whose gated
    path (``Shell.run_capability_intent`` / ``Shell.open_program``) decides —
    and an effectful capability waits there for a deliberate press, exactly
    as it does when spoken. That is what "requests an authorized System
    operation" means here.

All three are plain dataclasses with ``to_dict``, so they can be logged,
tested and, later, sent over a socket without changing shape.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: The six kinds of request the router tells apart (docs/EARTH_MIND.md §5).
KNOWLEDGE = "knowledge"
CONVERSATION = "conversation"
SYSTEM_QUERY = "system_query"
SYSTEM_ACTION = "system_action"
GAME = "game"
MIXED = "mixed"
ROUTES: Tuple[str, ...] = (KNOWLEDGE, CONVERSATION, SYSTEM_QUERY, SYSTEM_ACTION, GAME, MIXED)

#: The subsystems a route can involve.
CDOS, COMPANION, SYSTEM, GAME_ENGINE = "cdos", "companion", "system", "game"
SUBSYSTEMS: Tuple[str, ...] = (CDOS, COMPANION, SYSTEM, GAME_ENGINE)

#: The interfaces that talk to Earth Mind.
INTERFACES: Tuple[str, ...] = ("terminal", "voice", "api", "game")


@dataclass
class Request:
    text: str
    interface: str = "terminal"
    persona: str = ""                 # an OS personality to answer as; blank = the interface's own
    at: float = field(default_factory=time.time)


@dataclass
class Proposal:
    """Something for the shell's gated path to do. Data, not an action."""

    kind: str                         # "capability" | "program"
    text: str                         # the person's own sentence
    target: str = ""                  # the capability, or the program's name
    effectful: bool = False           # True: the shell will wait for a press
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Response:
    text: str = ""
    route: str = CONVERSATION
    subsystems: Tuple[str, ...] = ()
    source: str = "fixed"             # readings | computer | model | notice | fixed | scripted | handoff
    persona: str = ""                 # who answered: a personality's name, or a character's
    ok: bool = True
    detail: str = ""                  # the verified findings behind an explanation, or a table
    proposal: Optional[Proposal] = None
    handoff: bool = False             # the shell's own command path should take this turn
    model_calls: int = 0
    checked: Optional[bool] = None    # the evidence check, when there was one
    elapsed_ms: float = 0.0
    why: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def mode(self) -> str:
        """The processing mode, for an interface to show: ``SYSTEM + COMPANION``."""
        if self.route == GAME:
            return f"GAME · {self.persona.upper()}" if self.persona else "GAME"
        names = {CDOS: "CDOS", COMPANION: "COMPANION", SYSTEM: "SYSTEM", GAME_ENGINE: "GAME"}
        parts = [names.get(s, s.upper()) for s in self.subsystems]
        return " + ".join(parts) if parts else self.route.upper().replace("_", " ")

    def note(self) -> str:
        """The status line: ``earth mind · system + companion · readings · 1 pass``."""
        parts = ["earth mind", self.mode().lower(), self.source]
        if self.model_calls:
            parts.append(f"{self.model_calls} pass{'es' if self.model_calls != 1 else ''}")
        if self.checked is True:
            parts.append("checked")
        elif self.checked is False:
            parts.append("check failed: verified words used")
        if self.proposal is not None and self.proposal.effectful:
            parts.append("waits for a press")
        return " · ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["mode"] = self.mode()
        return out
