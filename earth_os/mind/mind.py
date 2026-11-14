"""Earth Mind: the one coordination layer.

Earth OS is one computer, and this is its one mind. It is deliberately thin:
it owns no knowledge, holds no conversation and touches no hardware. It
*coordinates* the subsystems that do —

* **CDOS** (``cdos-computer/`` through ``voice/computer_link.ComputerLink``):
  facts, sums and policy. Deterministic; works with no model at all.
* **Companion** (``earth_os/companion``): conversation. One engine per OS
  personality, all speaking through one shared model.
* **System** (:mod:`.system`): the real machine, read through the closed
  capability catalogue; operations are proposals for the shell's gate.
* **Game** (:mod:`.game`): worlds whose characters borrow the Companion's
  machinery and the shared model, and can reach nothing else.

— and it is the only thing that holds all four. Each interface (the
terminal, the voice path, a game) hands :meth:`EarthMind.handle` a sentence
and gets a :class:`~earth_os.mind.envelope.Response` back: the words, which
route was taken, who took part, and where the words came from.

What Earth Mind itself is responsible for, and nothing else is:

==========================  ====================================================
request interpretation      :mod:`.router` — six kinds, decided with no model
context management          which personality an interface is talking to, which
                            world and character, per interface
routing / decision          :meth:`handle` — the order subsystems are asked in
memory coordination         :mod:`.memory` — one store, a view per voice
knowledge retrieval         CDOS first for anything factual; a model never fills
                            a NOT FOUND
personality selection       :mod:`.personas` — "switch to the technical assistant"
model management            :mod:`.models` — one client per model, shared
tool selection              readings vs. analysis vs. proposal (:mod:`.system`)
environment awareness       :meth:`environment` — what is up, what is not
==========================  ====================================================

Earth Mind cannot act. It imports no platform adapter, starts no process and
never reads a model's words as instructions; the most it can do about the
machine is return a :class:`~earth_os.mind.envelope.Proposal`, which the
shell's gated path may carry out (and which waits there for a press when the
capability is effectful).

If the language model is not running, nothing here fails: system readings,
CDOS's answers, remember/forget and the routing itself need no model, and
the turns that do need one say so plainly.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ..companion import CompanionEngine
from . import envelope as env
from .characters import CharacterVoice
from .envelope import Response
from .game import BUILTIN_WORLDS, GameHost
from .memory import MIND, SYSTEM as SYSTEM_MEMORY, MemoryStore
from .models import DEFAULT_ENVIRONMENT, ModelManager
from .personas import OS, Personality, PersonalityRegistry
from .redact import redact
from .router import Route, Router
from .system import ANALYSIS, DIAGNOSIS, UNREADABLE, SystemSubsystem

#: The source of a reply that says no reading answers the question (routing
#: audit P3). Shown on the terminal status line as it stands.
NO_READING = "no reading answers this"

DEFAULT_PERSONA = "companion"
#: Kept exchanges put back into working memory when a conversation resumes.
RESUME_TURNS = 6


class EarthMind:
    def __init__(self, models: Optional[ModelManager] = None, store: Optional[MemoryStore] = None,
                 link=None, ledger=None, system: Optional[SystemSubsystem] = None,
                 personalities: Optional[PersonalityRegistry] = None,
                 games: Optional[GameHost] = None,
                 godcore: Optional[Callable[[], Dict]] = None,
                 world_requests=None, answer_world_request=None,
                 persona: str = DEFAULT_PERSONA, name: str = "EARTH OS", user: str = "",
                 companion_options: Optional[Dict[str, object]] = None, explain: bool = True,
                 world_dirs: Optional[List[Path]] = None, default_world: str = "",
                 keep_conversations: bool = False, clock=time.monotonic):
        self.models = models or ModelManager()
        self.memory = store or MemoryStore(ledger=ledger)
        self.link = link                       # CDOS Computer
        self.ledger = ledger                   # the person's memories (World Model)
        self.system = system
        self.personalities = personalities or PersonalityRegistry()
        self.voice = CharacterVoice(self.models, self.memory)
        self.games = games if games is not None else GameHost(
            self.memory, self.voice, directories=world_dirs or (BUILTIN_WORLDS,), default=default_world)
        self.godcore = godcore or (lambda: {})
        self.answer_world_request = answer_world_request
        self.name, self.user = name or "EARTH OS", user
        self.companion_options = dict(companion_options or {})
        self.explain = bool(explain)
        #: Conversation memory across restarts. Off unless the person turns it
        #: on: by default nothing said to this computer outlives the session.
        self.keep_conversations = bool(keep_conversations)
        self.clock = clock
        self.default_persona = persona if self.personalities.get(persona) else DEFAULT_PERSONA
        self.router = Router(system=system, games=self.games, personalities=self.personalities,
                             world_requests=world_requests if ledger is not None else None)
        self._companions: Dict[str, CompanionEngine] = {}
        self._persona: Dict[str, str] = {}     # interface → the OS personality it is talking to
        self._lock = threading.RLock()
        self.last: Optional[Response] = None
        self.handled = 0

    # ------------------------------------------------------------------
    # Personalities
    # ------------------------------------------------------------------
    def persona(self, interface: str = "terminal") -> Personality:
        """The OS personality *interface* is talking to."""
        with self._lock:
            pid = self._persona.get(interface, self.default_persona)
        found = self.personalities.get(pid) or self.personalities.get(DEFAULT_PERSONA)
        if found is None:
            # No personality files at all: a bare companion, so the machine still talks.
            found = Personality.build({"id": DEFAULT_PERSONA, "name": self.name,
                                       "permissions": ["converse", "knowledge", "memory.user",
                                                       "system.read", "system.act"]}, environment=OS)
            self.personalities.items[found.id] = found
        return found

    def select_persona(self, interface: str, pid: str) -> Optional[Personality]:
        found = self.personalities.get(pid)
        if found is not None:
            with self._lock:
                self._persona[interface] = found.id
        return found

    def companion(self, pid: str = "") -> CompanionEngine:
        """The Companion Engine speaking as OS personality *pid*. Built once each.

        Every engine shares the one model client. An engine is given the link
        to CDOS only if its personality may ask for knowledge, and the
        person's ledger only if it may read their memories — so a personality
        without the permission has no path to either.
        """
        person = self.personalities.get(pid) if pid else None
        person = person or self.persona("")
        with self._lock:
            engine = self._companions.get(person.id)
            if engine is not None:
                return engine
            opts = self.companion_options
            engine = CompanionEngine(
                model=self.models.model(DEFAULT_ENVIRONMENT, person.model),
                link=self.link if person.may("knowledge") else None,
                ledger=self.ledger if person.may("memory.user") else None,
                godcore=self.godcore,
                name=person.name if person.id != DEFAULT_PERSONA else self.name,
                user=self.user,
                observe=bool(opts.get("observe", True)),
                casual_facts=str(opts.get("casual_facts", "worded")),
                free_answers=bool(opts.get("free_answers", False)),
                idle_minutes=float(opts.get("idle_minutes", 30)),
                style=person.style_lines,
                profile=opts.get("profile") or "grounded")
            self._resume_conversation(person, engine)
            self._companions[person.id] = engine
            return engine

    # -- conversation memory ([mind] keep_conversations) -------------------------------
    def _resume_conversation(self, person: Personality, engine: CompanionEngine) -> None:
        """Put the last few kept exchanges back into a new engine's working memory.

        Read through the personality's own view, so one personality can never
        be handed another's conversation.
        """
        if not self.keep_conversations:
            return
        try:
            kept = self.memory.view(person.principal()).recent(person.namespace, limit=RESUME_TURNS,
                                                               kinds=("turn",))
        except Exception:
            return                                # memory is a record, never a blocker
        session = engine.sessions.current()
        for record in kept:
            session.add("user", record.text, str(record.data.get("mode", "")))
            session.add("assistant", str(record.data.get("reply", "")), str(record.data.get("mode", "")))

    def _keep_turn(self, person: Personality, text: str, reply) -> None:
        """One exchange, kept in ``conversation/<personality>`` — only when switched on.

        Never a sensitive turn, and "start over" clears what was kept: the
        Companion's own rules, carried across restarts. Secrets are redacted
        before anything is written.
        """
        if not self.keep_conversations:
            return
        try:
            view = self.memory.view(person.principal())
            if reply.kind == "reset":
                view.clear(person.namespace)
                return
            if reply.kind in ("sensitive", "mode", "task") or reply.source == "notice" or not reply.text:
                return
            said, _ = redact(text)
            answered, _ = redact(reply.text)
            view.add(person.namespace, said, kind="turn", importance=0.3,
                     data={"reply": answered[:2000], "mode": reply.mode})
        except Exception:
            pass

    # ------------------------------------------------------------------
    # One request
    # ------------------------------------------------------------------
    def route(self, text: str, interface: str = "terminal") -> Route:
        """How *text* would be handled, without handling it. Cheap, and needs no model."""
        return self.router.route(text, in_game=self.games.active(interface))

    def handle(self, text: str, interface: str = "terminal", route: Optional[Route] = None) -> Response:
        """Answer *text* from *interface*. Never raises: a failure is a Response too."""
        started = self.clock()
        text = (text or "").strip()
        try:
            route = route or self.route(text, interface)
            response = self._dispatch(text, interface, route)
        except Exception as exc:                 # one subsystem failing must not take the mind down
            response = Response(f"Earth Mind could not answer that ({type(exc).__name__}: {exc}).",
                                route=getattr(route, "kind", env.CONVERSATION), source="fixed", ok=False)
        response.why = list(getattr(route, "why", [])) + response.why
        response.elapsed_ms = round((self.clock() - started) * 1000.0, 1)
        self.last = response
        self.handled += 1
        return response

    async def ahandle(self, text: str, interface: str = "terminal",
                      route: Optional[Route] = None) -> Response:
        """:meth:`handle` for an event loop: the turn runs on a worker thread.

        A turn may wait on a model or on CDOS, so it must not sit on a loop any
        more than on a frame clock. Many may be awaited at once; calls to one
        model still queue, one generation at a time (:mod:`.models`).
        """
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(None, self.handle, text, interface, route)

    def _dispatch(self, text: str, interface: str, route: Route) -> Response:
        if not text:
            return Response("", source="fixed", ok=False)
        if route.kind == env.GAME:
            return self._game(text, interface, route)
        if route.control == "persona":
            return self._switch_persona(interface, route.target)
        if route.control == "memory":
            return self._user_memory(text, interface)
        if route.system is not None:
            return self._system(text, interface, route)
        if route.control == "handoff":
            return Response("", route=env.SYSTEM_ACTION, subsystems=(env.SYSTEM,), source="handoff",
                            handoff=True, persona=self.persona(interface).name)
        return self._converse(text, interface, route)

    # -- conversation and knowledge: the Companion, with CDOS underneath it ----------
    def _converse(self, text: str, interface: str, route: Route) -> Response:
        person = self.persona(interface)
        reply = self.companion(person.id).respond(text)
        if reply.handoff:
            return Response("", route=env.SYSTEM_ACTION, subsystems=(env.SYSTEM,), source="handoff",
                            handoff=True, persona=person.name)
        self._keep_turn(person, text, reply)
        took_part: List[str] = []
        if reply.queries:
            took_part.append(env.CDOS)
        if reply.model_calls or reply.source in ("model", "fixed", "notice") or not took_part:
            took_part.append(env.COMPANION)
        if route.kind == env.KNOWLEDGE:
            took_part.sort(key=lambda s: s != env.CDOS)          # CDOS answers first there
        return Response(reply.text, route=route.kind, subsystems=tuple(took_part), source=reply.source,
                        persona=person.role or person.name, model_calls=reply.model_calls,
                        checked=reply.checked, why=list(reply.why),
                        extra={"kind": reply.kind, "mode": reply.mode, "queries": list(reply.queries),
                               "noticed": bool(reply.noticed), "prompt_chars": reply.prompt_chars})

    def _user_memory(self, text: str, interface: str) -> Response:
        person = self.persona(interface)
        if not person.may("memory.user") or self.answer_world_request is None:
            return Response(f"{person.role or person.name} does not keep personal memories. "
                            f"Switch to the companion for that.", source="fixed", ok=False,
                            subsystems=(env.COMPANION,), persona=person.role or person.name)
        return Response(self.answer_world_request(text), source="fixed", subsystems=(env.COMPANION,),
                        persona=person.role or person.name, extra={"kind": "memory"})

    def _switch_persona(self, interface: str, target: Personality) -> Response:
        self.select_persona(interface, target.id)
        return Response(target.greeting or f"{target.role or target.name} here.", source="fixed",
                        subsystems=(env.COMPANION,), persona=target.role or target.name,
                        extra={"kind": "persona", "persona": target.id})

    # -- the machine ------------------------------------------------------------------
    def _system(self, text: str, interface: str, route: Route) -> Response:
        person = self.persona(interface)
        intent = route.system
        who = person.role or person.name
        if self.system is None or not self.system.available:
            return Response("System readings are unavailable: "
                            + (self.system.status() if self.system else "no system subsystem") + ".",
                            route=route.kind, subsystems=(env.SYSTEM,), source="fixed", ok=False, persona=who)

        if route.kind == env.SYSTEM_ACTION:
            if not person.may("system.act"):
                return Response(f"{who} is not allowed to ask for system operations.", route=route.kind,
                                subsystems=(env.SYSTEM,), source="fixed", ok=False, persona=who)
            proposal = self.system.propose(intent, text)
            return Response("", route=route.kind, subsystems=(env.SYSTEM,), source="handoff",
                            proposal=proposal, persona=who,
                            why=["effectful: the shell will wait for a press"] if proposal and proposal.effectful else [])

        if not person.may("system.read"):
            return Response(f"{who} has no access to system readings.", route=route.kind,
                            subsystems=(env.SYSTEM,), source="fixed", ok=False, persona=who)

        if intent.kind == UNREADABLE:
            # About this machine, and nothing reads it (routing audit P3): say
            # so, and what can be read instead. No reading is taken, no model
            # is asked, and nothing else is offered in its place.
            return Response(intent.summary, route=route.kind, subsystems=(env.SYSTEM,),
                            source=NO_READING, ok=False, persona=who,
                            extra={"kind": UNREADABLE})

        answer = self.system.answer(intent, table=(interface == "terminal" and intent.kind == ANALYSIS))
        if not answer.ok:
            return Response(answer.text, route=route.kind, subsystems=(env.SYSTEM,), source="readings",
                            ok=False, persona=who)
        if intent.kind != DIAGNOSIS:
            return Response(answer.text, route=route.kind, subsystems=(env.SYSTEM,), source="readings",
                            persona=who, extra={"capability": intent.analysis or intent.capability})

        # A diagnosis: the findings are fixed; the Companion may put them in plain words.
        self._record_diagnosis(text, answer.findings)
        detail = "\n".join(answer.findings)
        if not (self.explain and person.may("converse")):
            return Response(answer.text, route=env.MIXED, subsystems=(env.SYSTEM,), source="readings",
                            persona=who, detail=detail)
        reply = self.companion(person.id).explain(text, answer.findings, origin="live system readings")
        explained = reply.source == "model"
        return Response(reply.text if explained else answer.text, route=env.MIXED,
                        subsystems=(env.SYSTEM, env.COMPANION) if explained else (env.SYSTEM,),
                        source="model" if explained else "readings", persona=who,
                        detail=detail, model_calls=reply.model_calls, checked=reply.checked,
                        why=list(reply.why), extra={"capability": intent.analysis})

    def _record_diagnosis(self, question: str, findings: List[str]) -> None:
        """System memory: what the machine was found doing, and when."""
        if not findings:
            return
        try:
            self.memory.view(MIND).add(SYSTEM_MEMORY, findings[0], kind="diagnosis", subject="performance",
                                       data={"question": question[:200], "findings": findings[:12]},
                                       importance=0.6)
        except Exception:
            pass                                  # memory is a record, never a blocker

    # -- game worlds --------------------------------------------------------------------
    def _game(self, text: str, interface: str, route: Route) -> Response:
        base = dict(route=env.GAME, subsystems=(env.GAME_ENGINE,))
        if route.control == "who":
            held = self.games.active(interface)
            environment = held[0] if held else self.games.open()
            if environment is None:
                return Response("No game world is loaded.", source="fixed", ok=False, **base)
            cast = ", ".join(f"{c.name} the {c.role}" if c.role else c.name for c in environment.characters())
            return Response(f"In {environment.definition.name}, day {environment.day}: {cast}. "
                            f"Say \"talk to\" and a name.", source="fixed", **base)

        environment, person = route.target
        if route.control == "enter":
            self.games.enter(interface, environment, person)
            return Response(environment.greet(person.id), source="scripted", persona=person.name,
                            extra={"world": environment.world, "character": person.id, "entered": True,
                                   "hint": f"You are talking to {person.name} in "
                                           f"{environment.definition.name}. Say goodbye to leave."},
                            **base)
        if route.control == "leave":
            self.games.leave(interface)
            return Response(f"You leave {person.name}. Back to {self.name}.", source="fixed",
                            persona=person.name, extra={"left": True}, **base)
        if route.control == "note":
            record = environment.player_note(person.id, route.argument)
            return Response(f"Noted in {environment.definition.name}: {record.text} "
                            f"{person.name} will remember it.", source="fixed", persona=person.name,
                            extra={"event": record.id}, **base)
        if route.control == "advance":
            day = environment.advance(int(route.argument or 1))
            return Response(f"It is now day {day} in {environment.definition.name}.", source="fixed",
                            persona=person.name, extra={"day": day}, **base)
        if route.control == "history":
            events = environment.history(limit=12)
            if not events:
                return Response("Nothing has happened here yet.", source="fixed", persona=person.name, **base)
            lines = [f"Day {e.data.get('day', '?')}: {e.text}" for e in events]
            return Response("\n".join(lines), source="fixed", persona=person.name, **base)

        reply = environment.talk(person.id, text)
        return Response(reply.text, source=reply.source, persona=person.name,
                        model_calls=reply.model_calls,
                        extra={"world": environment.world, "character": person.id,
                               "recalled": list(reply.recalled), "prompt_chars": reply.prompt_chars,
                               "error": reply.error}, **base)

    # ------------------------------------------------------------------
    # Environment awareness
    # ------------------------------------------------------------------
    def environment(self, interface: str = "terminal") -> Dict[str, object]:
        """What is up and what is not, as data. Asks no model and takes no reading."""
        held = self.games.active(interface)
        return {
            "interface": interface,
            "persona": self.persona(interface).id,
            "talking_to": {"world": held[0].world, "character": held[1].id} if held else None,
            "model": {"spec": str(self.models.spec_for(DEFAULT_ENVIRONMENT)),
                      "available": self.models.available(DEFAULT_ENVIRONMENT)},
            "cdos": {"linked": self.link is not None,
                     "available": bool(self.link is not None and self.link.available)},
            "system": bool(self.system is not None and self.system.available),
            "memory": {"records": self.memory.size(), "user": self.ledger is not None},
            "worlds": self.games.worlds(),
        }

    def status_rows(self) -> List[Tuple[str, str]]:
        """Label/value rows for a diagnostics panel. Takes no reading and calls no model."""
        rows = [("EARTH MIND", f"{self.handled} request{'s' if self.handled != 1 else ''} handled"),
                ("PERSONALITIES", ", ".join(p.id for p in self.personalities.all()) or "none")]
        rows.extend(self.models.status_rows())
        rows.append(("CDOS", self.link.status() if self.link is not None else "not linked"))
        rows.append(("SYSTEM", self.system.status() if self.system is not None else "not connected"))
        where = str(self.memory.path) if self.memory.path else "in memory only"
        rows.append(("MEMORY", f"{self.memory.size()} records · {where}"))
        rows.append(("WORLDS", self.games.status()))
        for path, why in sorted({**self.personalities.unreadable, **self.games.unreadable}.items()):
            rows.append((f"UNREADABLE {Path(path).name}", why))
        return rows

    def close(self) -> None:
        self.memory.close()
