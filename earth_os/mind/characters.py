"""A character's turn: the Companion's machinery, wearing another identity.

This is not a second conversation engine. A character's reply is built by the
same prompt builder (``companion.context.build``: the same budgets, the same
order, the same trimming), kept in the same kind of working memory
(``companion.session.Session``), screened by the same deterministic classifier
(``companion.intent``) and spoken through the same shared model
(:class:`~earth_os.mind.models.ModelManager`). What differs is what a
character is *given*:

* its own rules — it is a person in a world, not this computer;
* its own identity, goals and manner (:class:`~earth_os.mind.personas.Personality`);
* its own memories, read through a view that can see **only** its own
  namespace and its world's public facts (:mod:`earth_os.mind.memory`);
* how it regards whoever is speaking to it.

Everything a character might know is fetched through that view, so a
character's private memories cannot reach another character's prompt: there
is no code path here that holds two characters' views at once.

Like the companion, a character talks and does nothing else. This module
imports nothing that can act, a reply is never parsed for commands, and the
only thing a turn changes is the character's own memory. ``tests/test_mind_game.py``
reads the imports to keep it so.

With no model running a character still answers — from its written lines and
from what it remembers — and the reply says so (``source = "scripted"``)
rather than passing a script off as conversation.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..companion import context as ctx
from ..companion.engine import NOTICES
from ..companion.intent import CASUAL, MODES, classify, sensitive_category, normalise
from ..companion.session import Session
from .memory import MemoryStore, MemoryView, words, world_namespace
from .models import ModelManager
from .personas import Personality

#: The rules every character speaks under. ``{user}`` is whoever it is talking to.
CHARACTER_RULES = (
    "You are {name}, a character in a story world. Stay in character: speak as {name} would, in the "
    "first person, and never mention being an AI, a model, a computer or a game. "
    "You are talking with {user}. "
    "You know only what is listed under WORLD and YOUR MEMORIES and what {user} tells you now; if you "
    "do not know something, say so as {name} would rather than inventing it. "
    "Never claim to remember anything not listed under YOUR MEMORIES. "
    "You can only talk. You cannot do anything outside this conversation."
)

#: Working memories kept warm at once; the rest are rebuilt from the store when needed.
MAX_ACTIVE_SESSIONS = 32
HEARD_CHARS = 300


@dataclass
class Scene:
    """What the world tells a character about this moment. Supplied by the game."""

    world_name: str = ""
    speaker: str = "a stranger"            # who is talking to the character
    speaker_id: str = "player"
    day: int = 0
    regard: str = ""                       # how the character regards the speaker, in words
    last_met: Optional[int] = None         # the world day they last spoke, if ever
    facts: Tuple[str, ...] = ()            # extra facts for this moment (where they are, the hour)


@dataclass
class CharacterReply:
    text: str
    character: str
    name: str
    source: str = "model"                  # model | scripted | notice | fixed
    model_calls: int = 0
    prompt_chars: int = 0
    recalled: List[str] = field(default_factory=list)     # the memories this reply was given
    facts: List[str] = field(default_factory=list)
    kind: str = "chat"
    error: str = ""


class CharacterSession:
    """One character's side of one conversation: its view, and its working memory."""

    def __init__(self, personality: Personality, view: MemoryView, clock=time.time):
        self.personality = personality
        self.view = view
        self.session = Session(clock)
        self.lock = threading.Lock()
        self.turns = 0

    @property
    def namespace(self) -> str:
        return self.personality.namespace


class CharacterVoice:
    """Speaks for any number of characters through one shared model."""

    def __init__(self, models: ModelManager, store: MemoryStore, clock=time.time,
                 max_active: int = MAX_ACTIVE_SESSIONS):
        self.models = models
        self.store = store
        self.clock = clock
        self.max_active = max_active
        self._sessions: "OrderedDict[Tuple[str, str, str], CharacterSession]" = OrderedDict()
        self._lock = threading.Lock()

    # -- sessions ----------------------------------------------------------------
    def session(self, personality: Personality, speaker_id: str = "player") -> CharacterSession:
        """This character's conversation with *speaker_id*, made on first use.

        Many can be open at once. Only the working memory is per-session; the
        model is shared and what a character knows long-term is in the store,
        so an evicted session costs nothing but its last few turns.
        """
        key = (personality.world, personality.id, speaker_id)
        with self._lock:
            found = self._sessions.get(key)
            if found is not None:
                self._sessions.move_to_end(key)
                return found
            made = CharacterSession(personality, self.store.view(personality.principal()), self.clock)
            self._sessions[key] = made
            while len(self._sessions) > self.max_active:
                self._sessions.popitem(last=False)
            return made

    def active(self) -> List[Tuple[str, str, str]]:
        with self._lock:
            return list(self._sessions)

    def forget_sessions(self, world: str = "") -> None:
        with self._lock:
            for key in [k for k in self._sessions if not world or k[0] == world]:
                del self._sessions[key]

    # -- one turn -----------------------------------------------------------------
    def turn(self, personality: Personality, text: str, scene: Optional[Scene] = None,
             remember: bool = True) -> CharacterReply:
        scene = scene or Scene()
        held = self.session(personality, scene.speaker_id)
        with held.lock:                      # one turn at a time per conversation
            return self._turn(held, text, scene, remember)

    def _turn(self, held: CharacterSession, text: str, scene: Scene, remember: bool) -> CharacterReply:
        me = held.personality
        text = " ".join(str(text or "").split())
        reply = CharacterReply("", me.id, me.name)
        if not text:
            reply.text, reply.source = me.greeting or f"{me.name} waits.", "fixed"
            return reply

        # Someone in real distress is answered as a person, not as a shopkeeper:
        # out of character, from the fixed notice, with no model and no record.
        found = sensitive_category(normalise(text))
        if found and found[0] == "self_harm":
            reply.text, reply.source, reply.kind = NOTICES["self_harm"], "notice", "sensitive"
            return reply

        intent = classify(text, held.session.mode)
        if intent.kind == "mode":            # "let's go deeper" is about the telling, not the tale
            held.session.mode = intent.sticky_mode
        mode = intent.mode if intent.mode in MODES else CASUAL
        reply.kind = intent.kind if intent.kind not in ("task", "sensitive", "reset") else "chat"

        memories = self._memories(held, text, scene)
        facts = self._facts(held, text, scene)
        reply.recalled, reply.facts = list(memories), list(facts)

        model = self.models.model(me.environment, me.model)
        if model is None or not model.available:
            reply.text, reply.source = self._scripted(me, text, memories, facts), "scripted"
        else:
            built = ctx.build(
                text, intent, mode, me, me.name, scene.speaker, memories, held.session,
                facts_block="\n".join(f"- {f}" for f in facts),
                rules=CHARACTER_RULES,
                facts_label=f"WORLD ({scene.world_name or me.world}; what you know to be true)",
                memory_label="YOUR MEMORIES (things that happened to you)")
            reply.prompt_chars = built.chars
            try:
                out = model.chat(built.messages, mode)
                reply.model_calls = 1
                reply.text = out.strip() or "…"
            except Exception as exc:
                reply.model_calls = 1
                reply.error = f"{type(exc).__name__}: {exc}"
                reply.text, reply.source = self._scripted(me, text, memories, facts), "scripted"

        held.session.add("user", text, mode)
        held.session.add("assistant", reply.text, mode)
        held.turns += 1
        if remember:
            self._remember(held, text, reply, scene)
        return reply

    # -- what the character is given ------------------------------------------------
    def _memories(self, held: CharacterSession, text: str, scene: Scene) -> List[str]:
        """Its own memories that bear on this turn. Read through its own view, only."""
        me = held.personality
        query = f"{text} {scene.speaker} {scene.speaker_id}"
        out: List[str] = []
        # What happened (events the game recorded, things it was told to keep) first…
        lasting = held.view.recall(me.namespace, query, limit=4, kinds=("event", "memory"), always=2)
        # …then what was said, and only what bears on this.
        talk = held.view.recall(me.namespace, query, limit=2, kinds=("heard",))
        for record in list(lasting) + list(talk):
            day = record.data.get("day") if isinstance(record.data, dict) else None
            when = ""
            if isinstance(day, int):
                ago = scene.day - day
                when = " (today)" if ago <= 0 else f" ({ago} day{'s' if ago != 1 else ''} ago)"
            line = record.text + when
            if line not in out:
                out.append(line)
        return out

    def _facts(self, held: CharacterSession, text: str, scene: Scene) -> List[str]:
        me = held.personality
        facts: List[str] = []
        if me.background:
            facts.append(f"About you: {me.background}")
        if me.goals:
            facts.append("What you want: " + "; ".join(me.goals) + ".")
        facts.extend(me.knowledge[:8])
        try:
            shared = held.view.recall(world_namespace(me.world), text, limit=4, kinds=("fact",), always=2)
        except Exception:
            shared = []
        facts.extend(r.text for r in shared)
        if scene.day:
            facts.append(f"It is day {scene.day}.")
        if scene.regard:
            facts.append(f"How you regard {scene.speaker}: {scene.regard}.")
        if scene.last_met is not None and scene.day > scene.last_met:
            gap = scene.day - scene.last_met
            facts.append(f"You last spoke with {scene.speaker} {gap} day{'s' if gap != 1 else ''} ago.")
        elif scene.last_met is None:
            facts.append(f"You have not spoken with {scene.speaker} before.")
        facts.extend(scene.facts)
        seen, out = set(), []
        for fact in facts:
            if fact and fact not in seen:
                seen.add(fact)
                out.append(fact)
        return out

    def _scripted(self, me: Personality, text: str, memories: List[str], facts: List[str]) -> str:
        """No model: what the character was written to say, and what it honestly knows.

        Three parts, each only if there is something true to put in it: one of
        its written lines; the thing it knows that best matches what was asked;
        and a memory that bears on it. Nothing is generated, so nothing is
        invented.
        """
        said = words(text)
        parts = [me.fallback[sum(map(ord, text)) % len(me.fallback)] if me.fallback else f"{me.name} nods."]
        known = [f for f in facts if not f.startswith(("About you:", "What you want:", "It is day",
                                                       "How you regard", "You last spoke", "You have not"))]
        best = max(known, key=lambda f: len(said & words(f)), default="")
        if best and said & words(best):
            parts.append(best)
        if memories:
            parts.append("I remember: " + memories[0].rstrip(".") + ".")
        return " ".join(p for p in parts if p)

    def _remember(self, held: CharacterSession, text: str, reply: CharacterReply, scene: Scene) -> None:
        """The character keeps what it heard, in its own namespace.

        Only the speaker's own words are kept as a memory. What a model made
        the character say is not: a small model's flourish ("I promised you my
        boat") would otherwise become a fact the character acts on next week.
        What *happened* is the game's to record, through
        :meth:`earth_os.mind.game.environment.GameEnvironment.event`.
        """
        me = held.personality
        heard = text if len(text) <= HEARD_CHARS else text[:HEARD_CHARS - 1] + "…"
        try:
            held.view.add(me.namespace, f'{scene.speaker} said to me: "{heard}"', kind="heard",
                          subject=scene.speaker_id, data={"day": scene.day}, importance=0.35)
        except Exception as exc:              # memory must never break the conversation
            reply.error = reply.error or f"memory: {type(exc).__name__}: {exc}"
