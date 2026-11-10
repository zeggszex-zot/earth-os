"""The Companion Engine: one turn of conversation, start to finish.

    text
     ├─ classify (intent.py)                       no model
     ├─ mode / reset turn            → acknowledged, no model
     ├─ sensitive                    → the computer's PRIORITY/NOTICE, or a fixed notice; no model
     ├─ task                         → handed back to the shell's command path; no model
     ├─ knowledge / calculation      → CDOS Computer first
     │     final     → worded by the model and checked, or said as the computer said it
     │     not found → said as not knowing (a model only with [computer] free_answers)
     └─ conversation                 → one model pass, with the topic's facts in REFLECTIVE/DEEP
                                       DEEP only: one revision if the evidence check fails

The engine holds a model, a link to the computer, the ledger and a way to read
GODCORE's cached state. It holds nothing that can act on the machine: no
capability registry, no compositions, no agent, no bridge. That is what
"conversation is separate from execution" means here, and a test reads this
package's imports to keep it so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import context as ctx
from .intent import CASUAL, DEEP, REFLECTIVE, Intent, classify
from .knowledge import Knowledge, Retrieval, check_reply
from .memory import CompanionMemory
from .persona import Persona
from .session import Sessions

NOTICES = {
    "self_harm": ("It sounds like you might be in real pain, and I'm glad you said it. Please talk to someone "
                  "you trust right now, or contact your local emergency number or a crisis line — in the US "
                  "you can call or text 988, any time. I'm still here to talk."),
    "health": ("I can't give medical advice, and I don't want to guess about your health. Please check with "
               "a doctor, nurse or pharmacist — and if it feels urgent, contact emergency services."),
    "money": ("I don't give financial advice. For a decision like that, a qualified financial adviser is the "
              "right person to ask."),
    "legal": ("I don't give legal advice. A qualified lawyer, or a legal aid service, is the right place for that."),
}

MODE_ACK = {
    CASUAL: "Keeping it light.",
    REFLECTIVE: "All right — let's think it over properly.",
    DEEP: "Let's go deeper. I'll take it step by step and keep to what I know.",
}

NO_MODEL_SHORT = "That's what my corpus says; my language model isn't running, so I can't talk it through."
NO_MODEL = ("My language model isn't running, so I can't hold an open conversation just now. "
            "I can still answer from my knowledge, do sums, and run commands.")


@dataclass
class Reply:
    text: str
    kind: str
    mode: str
    source: str                      # model | computer | notice | fixed | handoff
    model_calls: int = 0
    prompt_chars: int = 0
    queries: List[str] = field(default_factory=list)
    checked: Optional[bool] = None   # the evidence check, when there was one
    problems: List[str] = field(default_factory=list)
    noticed: str = ""                # a pending note filed this turn
    handoff: bool = False            # the shell's command path should take this turn
    session: str = ""
    why: List[str] = field(default_factory=list)

    def note(self) -> str:
        """The status line: "companion · reflective · 1 pass · 2 facts"."""
        parts = ["companion", self.mode.lower(), self.kind]
        parts.append(f"{self.model_calls} pass{'es' if self.model_calls != 1 else ''}")
        if self.queries:
            parts.append(f"{len(self.queries)} lookup{'s' if len(self.queries) != 1 else ''}")
        if self.checked is True:
            parts.append("checked")
        elif self.checked is False:
            parts.append("check failed: computer's words used")
        if self.noticed:
            parts.append("1 note pending")
        return " · ".join(parts)


class CompanionEngine:
    def __init__(self, model=None, link=None, ledger=None,
                 godcore: Optional[Callable[[], Dict]] = None,
                 name: str = "CDOS", user: str = "", observe: bool = True,
                 casual_facts: str = "worded", free_answers: bool = False,
                 idle_minutes: float = 30.0, clock=time.time,
                 style: Optional[Callable[[], List[str]]] = None):
        self.model = model
        #: Extra style lines from a configured personality (docs/EARTH_MIND.md).
        #: Style only: routing, facts and permissions are decided before any
        #: persona is consulted, exactly as for GODCORE.
        self.style = style
        self.knowledge = Knowledge(link)
        self.memory = CompanionMemory(ledger, observe=observe)
        self.godcore = godcore or (lambda: {})
        self.name, self.user = name or "CDOS", user
        self.casual_facts = casual_facts if casual_facts in ("worded", "verbatim") else "worded"
        self.free_answers = bool(free_answers)
        self.sessions = Sessions(idle_seconds=idle_minutes * 60.0, clock=clock)
        self.last: Optional[Reply] = None

    # ------------------------------------------------------------------
    @property
    def model_ready(self) -> bool:
        return self.model is not None and self.model.available

    def persona(self) -> Persona:
        try:
            snapshot = self.godcore() or {}
        except Exception:
            snapshot = {}
        persona = Persona.from_snapshot(snapshot, name=self.name)
        if self.style is not None:
            try:
                persona.extra = tuple(str(line) for line in (self.style() or ()) if line)
            except Exception:
                persona.extra = ()
        return persona

    def status(self) -> str:
        model = self.model.status() if self.model is not None else "no model configured"
        know = "computer linked" if self.knowledge.available else "no computer"
        return f"{model} · {know} · session {self.sessions.current().id}"

    # ------------------------------------------------------------------
    def respond(self, text: str) -> Reply:
        session = self.sessions.current()
        intent = classify(text, session.mode)
        reply = self._turn(text, intent, session)
        if intent.kind == "reset":
            session = self.sessions.current()
        reply.session = session.id
        reply.why = intent.why + reply.why
        if not reply.handoff:
            session.add("user", text, intent.mode)
            session.add("assistant", reply.text, reply.mode)
            if intent.topic and intent.kind not in ("sensitive", "task"):
                session.topic = intent.topic
        self.last = reply
        return reply

    def _turn(self, text: str, intent: Intent, session) -> Reply:
        mode = intent.mode
        if intent.kind == "mode":
            session.mode = intent.sticky_mode
            return Reply(MODE_ACK[intent.sticky_mode], "mode", intent.sticky_mode, "fixed")
        if intent.kind == "reset":
            fresh = self.sessions.reset()
            return Reply("Fresh start. What's on your mind?", "reset", CASUAL, "fixed",
                         why=[f"new session {fresh.id}"])

        if intent.kind == "sensitive":
            screened = self.knowledge.screen(text)
            if screened is not None:
                return Reply(screened.text, "sensitive", mode, "computer", queries=[text])
            return Reply(NOTICES[intent.category], "sensitive", mode, "notice",
                         queries=list(self.knowledge.asked[-1:]) if self.knowledge.available else [])

        if intent.kind == "task":
            return Reply("", "task", mode, "handoff", handoff=True)

        persona = self.persona()
        retrieval: Optional[Retrieval] = None
        if intent.wants_knowledge:
            retrieval = self.knowledge.retrieve(text, intent.topic, intent.kind, mode)
            notice = retrieval.notice
            if notice is not None:                       # the computer's policy, verbatim
                return Reply(notice.text, intent.kind, mode, "computer", queries=retrieval.queries)

        if intent.kind in ("knowledge", "calculation"):
            return self._factual(text, intent, session, persona, retrieval)

        noticed = ""
        if intent.kind in ("chat", "feeling", "opinion"):
            entry = self.memory.notice(text)
            noticed = getattr(entry, "id", "") if entry is not None else ""

        if not self.model_ready:
            if intent.kind == "greeting":
                text_out = f"Hello{', ' + self.user if self.user else ''}. I'm here."
            elif retrieval is not None and retrieval.facts:
                text_out = retrieval.facts[0].text + " " + NO_MODEL_SHORT
            else:
                text_out = NO_MODEL
            return Reply(text_out, intent.kind, mode, "fixed", noticed=noticed,
                         queries=retrieval.queries if retrieval else [])
        unknown = (intent.kind == "explore" and retrieval is not None and retrieval.reached
                   and not retrieval.facts)
        reply = self._generate(text, intent, session, persona, retrieval, not_found=unknown)
        reply.noticed = noticed
        return reply

    # ------------------------------------------------------------------
    def explain(self, text: str, findings: List[str], origin: str = "live system readings",
                certainty: str = "ESTABLISHED") -> Reply:
        """Put findings somebody else verified into plain words.

        Earth Mind's way to have the companion explain what the system
        subsystem measured (docs/EARTH_MIND.md): *findings* are sentences built
        from real readings, and they are treated exactly as the corpus's facts
        are — one model pass, every figure in the reply checked against them,
        and the findings themselves said when there is no model or the check
        fails. Nothing here reads the machine; the companion still only talks.
        """
        from ..voice.computer_link import Answer
        session = self.sessions.current()
        findings = [" ".join(str(f).split()) for f in findings if str(f).strip()]
        plain = " ".join(findings)
        retrieval = Retrieval(
            answers=[Answer(text=f, faculty="analysis", status="answered", certainty=certainty,
                            via="system", reached=True) for f in findings],
            queries=[text], reached=True, origin=origin)
        intent = Intent("knowledge", mode=session.mode or REFLECTIVE, wants_knowledge=True,
                        why=[f"explaining {len(findings)} finding(s) from {origin}"])
        if not findings:
            reply = Reply("", "knowledge", intent.mode, "fixed", queries=[text])
        elif not self.model_ready:
            reply = Reply(plain, "knowledge", intent.mode, "computer", queries=[text])
        else:
            reply = self._generate(text, intent, session, self.persona(), retrieval)
            if reply.checked is False or reply.source != "model":
                reply.text, reply.source = plain, "computer"
        reply.session, reply.why = session.id, intent.why + reply.why
        if reply.text:
            session.add("user", text, intent.mode)
            session.add("assistant", reply.text, reply.mode)
        self.last = reply
        return reply

    def _factual(self, text, intent, session, persona, retrieval: Optional[Retrieval]) -> Reply:
        mode = intent.mode
        queries = retrieval.queries if retrieval else []
        primary = retrieval.facts[0] if retrieval and retrieval.facts else None
        if primary is None:
            if retrieval is None or not retrieval.reached:
                why = retrieval.error if retrieval else "no link"
                return Reply(f"I can't reach my knowledge core right now ({why}), so I won't guess.",
                             intent.kind, mode, "fixed", queries=queries)
            if self.free_answers and self.model_ready:
                reply = self._generate(text, intent, session, persona, retrieval, not_found=True)
                reply.text = "That isn't in my corpus, so this is unverified: " + reply.text
                return reply
            return Reply("I don't have that in my knowledge — my corpus has nothing on it, and I'd rather "
                         "not guess. I'm happy to think it through with you as speculation, if you like.",
                         intent.kind, mode, "computer", queries=queries)

        session.note_fact(primary.text)
        if (intent.kind == "calculation" or self.casual_facts == "verbatim" or not self.model_ready):
            return Reply(primary.text, intent.kind, mode, "computer", queries=queries)
        reply = self._generate(text, intent, session, persona, retrieval)
        if reply.checked is False:
            # The model wandered from the evidence: the computer's own sentence.
            reply.text, reply.source = primary.text, "computer"
        return reply

    def _generate(self, text, intent, session, persona, retrieval: Optional[Retrieval],
                  not_found: bool = False) -> Reply:
        mode = intent.mode
        budget = ctx.BUDGET[mode]
        facts = retrieval.block(int(budget * 0.4)) if retrieval else ""
        for f in retrieval.facts if retrieval else []:
            session.note_fact(f.text)
        memories = self.memory.relevant(text, always=1 if mode != CASUAL else 0)
        built = ctx.build(text, intent, mode, persona, persona.name or self.name, self.user,
                          memories, session, facts_block=facts, not_found=not_found)
        reply = Reply("", intent.kind, mode, "model", queries=retrieval.queries if retrieval else [])
        try:
            out = self.model.chat(built.messages, mode, persona.length_factor, persona.temperature_shift)
        except Exception as exc:
            reply.model_calls = 1
            reply.prompt_chars = built.chars
            reply.text, reply.source = (f"I lost my train of thought — my language model didn't answer "
                                        f"({type(exc).__name__})."), "fixed"
            return reply
        reply.model_calls, reply.prompt_chars = 1, built.chars
        out = out.strip() or "…"

        if facts:
            ok, problems = check_reply(out, retrieval, text)
            if not ok and mode == DEEP:
                # One revision, and only when it is justified: name the problem.
                revise = built.messages + [
                    {"role": "assistant", "content": out},
                    {"role": "user", "content": ("Revise your answer: " + "; ".join(problems)
                                                 + ". Use only the figures in KNOWN FACTS.")}]
                try:
                    out2 = self.model.chat(revise, mode, persona.length_factor, persona.temperature_shift)
                    reply.model_calls += 1
                    reply.prompt_chars += sum(len(m["content"]) for m in revise)
                    ok2, problems2 = check_reply(out2, retrieval, text)
                    if ok2:
                        out, ok, problems = out2.strip(), True, []
                except Exception:
                    pass
            reply.checked, reply.problems = ok, problems
            if not ok and intent.kind not in ("knowledge", "calculation"):
                out += " (One figure there isn't from my corpus — treat it as unverified.)"
        reply.text = out
        return reply
