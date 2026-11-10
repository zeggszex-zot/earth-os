"""The unified request router: what kind of request this is, and who answers.

One router for every interface. It decides, deterministically and before any
model is near the sentence, which of six kinds a request is:

==================  =============================================  =====================
kind                example                                        who takes part
==================  =============================================  =====================
``system_query``    "What is using the most RAM?"                  system (real readings)
``mixed``           "Why is my computer running slowly?"           system, then companion
``system_action``   "Open Firefox." / "Shut down."                 system → shell's gate
``knowledge``       "Explain how neural networks work."            CDOS, then companion
``conversation``    "I had a long day."                            companion
``mixed``           "What do you think about Jupiter?"             companion with CDOS
``game``            "Talk to the shopkeeper."                      game world's character
==================  =============================================  =====================

It adds no classifier of its own. Whether a sentence is about the machine is
the capability corpus's answer (:mod:`earth_os.mind.system`); what kind of
talk it is, is the Companion's own classifier
(:func:`earth_os.companion.intent.classify`); who a name refers to is asked of
the personality registry and the game host. The router's job is the order
they are asked in, and that order is the policy:

1. **A conversation with a character is in progress** → everything goes to
   that character, except the few sentences that are about the conversation
   itself (leaving, a day passing, "remember that I helped her").
2. **A sentence only a person in distress would say** → the Companion's fixed
   notices, whatever else the words resemble.
3. **Something the person asked this computer to remember or forget** → the
   World Model.
4. **A name** — "talk to the shopkeeper", "switch to the technical assistant".
5. **The machine** — a reading, a diagnosis or an operation, unless the
   sentence is plainly talk that merely contains a system word.
6. **Otherwise** knowledge or conversation, as the Companion classifies it.

A wrong guess here costs a slightly odd reply. It cannot cost an action:
nothing downstream of this module can act, and an operation is only ever a
proposal for the shell's gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..companion.intent import Intent, classify, is_conversational, normalise
from . import envelope as env
from .system import ACTION, ANALYSIS, DIAGNOSIS, PROGRAM, READING, SystemIntent

_TALK = re.compile(r"^(?:i (?:want|would like|'d like|need) to |let me |can i |could i |let's )?"
                   r"(?:talk|speak|chat) (?:to|with) (.{2,60})$")
_APPROACH = re.compile(r"^(?:approach|greet|go (?:and )?(?:see|talk to|speak to)) (.{2,60})$")
_SWITCH = re.compile(r"^(?:switch to|change to|use|bring up|i want|give me|be) (?:the |my )?(.{2,50}?)"
                     r"(?: mode| personality| persona| please)?$")
_LEAVE = re.compile(r"^(?:leave|i leave|goodbye|good bye|bye|farewell|exit|quit|walk away|"
                    r"end (?:the |this )?conversation|stop talking|back to earth os|leave the (?:game|world|shop))"
                    r"[.! ]*$")
_NOTE = re.compile(r"^(?:please )?remember (?:that )?(.{3,200})$")
_ADVANCE = re.compile(r"^(?:next day|a day passes|the next day|come back tomorrow|sleep|"
                      r"(?:wait|advance|skip|pass) (a|one|two|three|four|five|six|seven|\d{1,3}) days?|"
                      r"(a|one|two|three|four|five|six|seven|\d{1,3}) days? (?:later|pass(?:es)?))[.! ]*$")
_HISTORY = re.compile(r"^(?:what has happened(?: so far)?|what happened(?: so far)?|world history|"
                      r"event history|show (?:the )?history|recap)[?.! ]*$")
_WHO = re.compile(r"^(?:who is here|who can i talk to|who lives here|who is (?:in|at) .{2,40}|"
                  r"list (?:the )?characters|list (?:the )?worlds|what worlds are there)[?.! ]*$")
_NUMBERS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


@dataclass
class Route:
    kind: str = env.CONVERSATION
    subsystems: Tuple[str, ...] = (env.COMPANION,)
    why: List[str] = field(default_factory=list)
    intent: Optional[Intent] = None              # the Companion's reading of the sentence
    system: Optional[SystemIntent] = None        # the capability corpus's reading
    control: str = ""                            # enter | leave | note | advance | history | who | persona | memory
    argument: str = ""
    target: object = None                        # (environment, personality), or a Personality
    #: True when this outranks the terminal's own English-to-command translator:
    #: the request names something Earth Mind knows and the translator would misread.
    priority: bool = False
    #: True when answering may call a model or load the corpus, so an interface
    #: should take it off its own thread.
    slow: bool = False


class Router:
    def __init__(self, system=None, games=None, personalities=None, world_requests=None):
        self.system = system
        self.games = games
        self.personalities = personalities
        #: ``world_model.parse_request``, when the person's ledger is switched on.
        self.world_requests = world_requests

    # ------------------------------------------------------------------
    def route(self, text: str, in_game=None) -> Route:
        """*in_game* is ``(environment, personality)`` while a character is being talked to."""
        norm = normalise(text)
        if not norm.strip(" ?.!"):
            return Route(why=["nothing said"])
        plain = norm.rstrip("?.! ")

        if in_game is not None:
            return self._in_game(text, plain, in_game)

        intent = classify(text)
        if intent.kind == "sensitive":
            return Route(env.CONVERSATION, (env.COMPANION, env.CDOS), [f"sensitive: {intent.category}"],
                         intent=intent, slow=True)

        if self.world_requests is not None and self.world_requests(text) is not None:
            return Route(env.CONVERSATION, (env.COMPANION,), ["a remember/forget sentence: the World Model"],
                         intent=intent, control="memory")

        named = self._named(plain)
        if named is not None:
            return named
        if self.games is not None and _WHO.match(plain):
            return Route(env.GAME, (env.GAME_ENGINE,), ["asking who is in the world"], control="who",
                         priority=True)

        found = self._system(text, norm, intent)
        if found is not None:
            return found

        if intent.kind in ("knowledge", "calculation"):
            return Route(env.KNOWLEDGE, (env.CDOS, env.COMPANION), intent.why + ["CDOS first"],
                         intent=intent, slow=True)
        if intent.kind == "task":
            # A command the capability corpus did not recognise: the shell's own
            # command path (vocabulary, learned layer, CDOS bridge) may still.
            return Route(env.SYSTEM_ACTION, (env.SYSTEM,), intent.why + ["a task: the shell's command path"],
                         intent=intent, control="handoff")
        if intent.wants_knowledge:
            return Route(env.MIXED, (env.COMPANION, env.CDOS), intent.why + ["conversation that wants facts"],
                         intent=intent, slow=True)
        return Route(env.CONVERSATION, (env.COMPANION,), intent.why, intent=intent,
                     slow=intent.kind not in ("mode", "reset"))

    # -- the machine -----------------------------------------------------
    def _system(self, text: str, norm: str, intent: Intent) -> Optional[Route]:
        if self.system is None:
            return None
        found = self.system.resolve(text)
        if found is None:
            return None
        # The conversation guard, as the shell applies it to speech: a sentence
        # that is plainly talk, and that the corpus recognised only a word or
        # two of, is talk. It can only take a sentence away from the machine.
        if found.kind != DIAGNOSIS and is_conversational(text) \
                and len(found.phrase) < 0.6 * max(len(norm), 1):
            return None
        why = [f"capability corpus: {found.phrase!r} → {found.analysis or found.capability}"]
        if found.kind == DIAGNOSIS:
            return Route(env.MIXED, (env.SYSTEM, env.COMPANION), why + ["a diagnosis: readings, then explained"],
                         intent=intent, system=found, priority=True, slow=True)
        if found.kind in (ANALYSIS, READING):
            return Route(env.SYSTEM_QUERY, (env.SYSTEM,), why, intent=intent, system=found,
                         priority=found.kind == ANALYSIS)
        if found.kind in (ACTION, PROGRAM):
            return Route(env.SYSTEM_ACTION, (env.SYSTEM,), why + ["an operation: proposed to the shell's gate"],
                         intent=intent, system=found, priority=found.kind == PROGRAM)
        return None

    # -- names -------------------------------------------------------------
    def _named(self, plain: str) -> Optional[Route]:
        match = _TALK.match(plain) or _APPROACH.match(plain)
        switch = _SWITCH.match(plain)
        name = match.group(1) if match else (switch.group(1) if switch else "")
        if not name:
            return None
        if self.personalities is not None:
            persona = self.personalities.find(name)
            if persona is not None:
                return Route(env.CONVERSATION, (env.COMPANION,), [f"personality: {persona.id}"],
                             control="persona", target=persona, priority=True)
        if match and self.games is not None:
            found = self.games.find_character(name)
            if found is not None:
                return Route(env.GAME, (env.GAME_ENGINE,), [f"character: {found[1].id} in {found[0].world}"],
                             control="enter", target=found, priority=True)
        return None

    # -- inside a world ------------------------------------------------------
    def _in_game(self, text: str, plain: str, in_game) -> Route:
        environment, person = in_game
        base = dict(kind=env.GAME, subsystems=(env.GAME_ENGINE,), target=in_game, priority=True)
        if _LEAVE.match(plain):
            return Route(why=["leaving the conversation"], control="leave", **base)
        note = _NOTE.match(plain)
        if note:
            return Route(why=["the player's account of something that happened"], control="note",
                         argument=_original(text, note.group(1)), **base)
        advance = _ADVANCE.match(plain)
        if advance:
            raw = advance.group(1) or advance.group(2) or "1"
            days = _NUMBERS.get(raw, int(raw) if raw.isdigit() else 1)
            return Route(why=[f"{days} day(s) pass"], control="advance", argument=str(days), **base)
        if _HISTORY.match(plain):
            return Route(why=["the world's event history"], control="history", **base)
        if _WHO.match(plain):
            return Route(why=["asking who is in the world"], control="who", **base)
        match = _TALK.match(plain) or _APPROACH.match(plain)
        if match:
            other = environment.find(match.group(1))
            if other is not None and other.id != person.id:
                return Route(why=[f"turning to {other.id}"], control="enter",
                             kind=env.GAME, subsystems=(env.GAME_ENGINE,),
                             target=(environment, other), priority=True)
        return Route(why=[f"said to {person.id}"], slow=True, **base)


def _original(text: str, lowered: str) -> str:
    """*lowered* was matched in a normalised copy; give back the person's own capitals."""
    index = text.lower().find(lowered[:20])
    return text[index:].strip() if index >= 0 else lowered
