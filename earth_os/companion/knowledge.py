"""Knowledge from CDOS Computer, and a check that a model kept to it.

The companion asks the computer through the shell's existing
:class:`~earth_os.voice.computer_link.ComputerLink`, and only ever with
``ask`` — ``POST /v1/ask``, the request a desktop user's Governor role allows.
It never asks for anything privileged, so the Governor has nothing new to
decide.

What counts as knowledge: a *final* answer whose faculty is ``knowledge``,
``logic`` or ``analysis``. The random sampler (``instinct``) is never a fact,
however it is worded, and a NOT FOUND is a fact about the corpus, not about
the world.

:func:`check_reply` is the evidence check: every number in a model's reply
must appear in the evidence or in what the person said, and when the computer
gave a figure the reply must keep it. It is deliberately about numbers — the
thing a small model most often gets confidently wrong — and it can only say
no.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Set, Tuple

FACT_FACULTIES = ("knowledge", "logic", "analysis")
MAX_QUERIES = {"CASUAL": 1, "REFLECTIVE": 1, "DEEP": 3}

_NUM = re.compile(r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?(?:e[+-]?\d+)?)(?![\w])"
                  r"(?:\s+(thousand|million|billion|trillion))?", re.I)
_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}


def numbers(text: str) -> Set[str]:
    """The numbers in *text*, normalised: "2,100,000", "2100000" and "2.1 million" are one."""
    out = set()
    for raw, scale in _NUM.findall(text or ""):
        token = raw.replace(",", "").rstrip(".")
        try:
            value = float(token) * _SCALE.get(scale.lower(), 1.0)
        except ValueError:
            continue
        out.add(f"{value:.6g}")
    return out


#: The one problem that means the computer's own answer is missing from a reply,
#: rather than that the reply said more than the evidence does.
DROPPED = "the computer's figure was dropped"

# Small numbers a sentence uses as words of grammar, not as claims.
_TRIVIAL = {f"{n:.6g}" for n in range(0, 11)}


@dataclass
class Retrieval:
    answers: list = field(default_factory=list)       # computer_link.Answer
    queries: List[str] = field(default_factory=list)
    reached: bool = False
    error: str = ""
    #: Where these facts came from, as a prompt should name it. The corpus,
    #: unless Earth Mind is asking for verified system readings to be explained.
    origin: str = "the CDOS corpus"

    @property
    def facts(self) -> list:
        return [a for a in self.answers if a.final and a.faculty in FACT_FACULTIES and not a.notice]

    @property
    def primary(self):
        return self.answers[0] if self.answers else None

    @property
    def notice(self):
        for a in self.answers:
            if a.notice:
                return a
        return None

    def block(self, limit_chars: int) -> str:
        """The facts as prompt lines: the computer's sentence and its certainty."""
        lines, used = [], 0
        for a in self.facts:
            line = f"- {a.text} [{(a.certainty or 'unknown').lower()}, from {self.origin}]"
            if used + len(line) > limit_chars:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    def supported_numbers(self) -> Set[str]:
        found: Set[str] = set()
        for a in self.facts:
            found |= numbers(a.text)
            for ev in a.evidence:
                if isinstance(ev, dict):
                    found |= numbers(str(ev.get("value", "")))
        return found

    def key_numbers(self) -> Set[str]:
        """The figures the computer actually said, beyond small grammar numbers."""
        found: Set[str] = set()
        for a in self.facts:
            found |= numbers(a.text)
        return found - _TRIVIAL


def check_reply(reply: str, retrieval: Optional[Retrieval], said: str) -> Tuple[bool, List[str]]:
    """Does *reply* keep to the evidence? Returns (ok, problems)."""
    problems: List[str] = []
    allowed = numbers(said) | _TRIVIAL
    if retrieval is not None:
        allowed |= retrieval.supported_numbers()
    stray = sorted(numbers(reply) - allowed)
    if stray:
        problems.append("figures not in the evidence: " + ", ".join(stray))
    if retrieval is not None:
        key = retrieval.key_numbers()
        if key and not (numbers(reply) & key):
            problems.append(DROPPED)
    return (not problems, problems)


class Knowledge:
    def __init__(self, link=None):
        self.link = link
        self.asked: List[str] = []           # every question sent, for the record and the tests

    @property
    def available(self) -> bool:
        return self.link is not None and self.link.available

    def ask(self, question: str):
        self.asked.append(question)
        return self.link.ask(question)

    def screen(self, text: str):
        """The computer's own policy on a sensitive turn: PRIORITY or NOTICE, or None."""
        if not self.available:
            return None
        answer = self.ask(text)
        return answer if answer.reached and answer.notice else None

    def retrieve(self, text: str, topic: str, kind: str, mode: str,
                 extra: Sequence[str] = ()) -> Retrieval:
        """At most ``MAX_QUERIES[mode]`` questions, the person's own first."""
        result = Retrieval()
        if not self.available:
            result.error = "CDOS Computer is not reachable"
            return result
        queries: List[str] = []
        if kind in ("knowledge", "calculation", "explore") or mode == "DEEP":
            queries.append(text)
        if topic:
            queries.append(f"tell me about {topic}")
        queries.extend(extra)
        seen = set()
        for q in queries:
            key = " ".join(q.lower().split())
            if key in seen:
                continue
            seen.add(key)
            if len(result.queries) >= MAX_QUERIES.get(mode, 1):
                break
            answer = self.ask(q)
            result.queries.append(q)
            if answer.reached:
                result.reached = True
                result.answers.append(answer)
            else:
                result.error = answer.error
        return result
