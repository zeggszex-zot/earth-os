"""The prompt: small, ordered, and under a budget.

Order, most important first, because the budget trims from the end of the
list: the rules, the turn's own facts, the persona, the person's relevant
memories, the working memory, then as much recent history as fits.

No telemetry, no capability list, no application index: the companion is not
the thing that runs the machine, and a 3B model given a catalogue of
capabilities starts offering to use them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .intent import CASUAL, DEEP, REFLECTIVE

#: Characters of prompt per mode (about four characters a token).
BUDGET = {CASUAL: 1600, REFLECTIVE: 3000, DEEP: 5000}
#: Recent exchanges offered per mode, before the budget has its say.
HISTORY = {CASUAL: 4, REFLECTIVE: 6, DEEP: 8}

RULES = (
    "You are {name}, a computer with a persistent identity, talking with {user}. "
    "Be warm, curious and thoughtful; speak naturally, without lists unless asked. "
    "You only talk. You cannot run commands, change settings or grant permissions; if {user} wants "
    "something done, say they can ask for it as a command. "
    "State facts only from KNOWN FACTS; if you are not sure of something, say so. "
    "When you imagine or speculate, say that you are. "
    "Never claim to remember anything not listed under MEMORY."
)

MODE_LINES = {
    CASUAL: "Reply in one to three sentences.",
    REFLECTIVE: "Consider it from two or three angles, then say where you land. At most two short paragraphs.",
    DEEP: ("Work through it step by step. Separate what the KNOWN FACTS establish from your own "
           "interpretation. At most four short paragraphs."),
}

KIND_LINES = {
    "imagination": "This is imagination: invent freely, and nothing invented is a fact about the real world.",
    "feeling": "Listen first. Reflect back what you heard before offering anything.",
    "greeting": "A greeting: answer in kind, briefly.",
    "knowledge": "Answer from KNOWN FACTS and keep their figures exactly.",
    "explore": ("Think it through with them. Use KNOWN FACTS where they help; anything beyond them is "
                "your understanding, so say so rather than stating it as established."),
    "calculation": "Answer from KNOWN FACTS and keep their figures exactly.",
}


@dataclass
class Built:
    messages: List[Dict[str, str]]
    chars: int
    dropped: List[str] = field(default_factory=list)
    history_turns: int = 0


def build(text: str, intent, mode: str, persona, name: str, user: str,
          memories: List[str], session, facts_block: str = "",
          not_found: bool = False, rules: Optional[str] = None,
          facts_label: str = "", memory_label: str = "") -> Built:
    """One turn's messages.

    *rules*, *facts_label* and *memory_label* are for the other voices Earth
    Mind hosts (docs/EARTH_MIND.md): a game character has its own rules and
    its facts are its world's, not the corpus's. Left unset, this is the
    companion's own prompt, unchanged. The budget, the order and the trimming
    are the same for every voice, which is the point of sharing the builder.
    """
    budget = BUDGET.get(mode, BUDGET[CASUAL])
    system: List[str] = [(rules or RULES).format(name=name or "CDOS", user=user or "you"),
                         MODE_LINES[mode]]
    if intent.kind in KIND_LINES and rules is None:
        system.append(KIND_LINES[intent.kind])
    if facts_block:
        system.append((facts_label or "KNOWN FACTS (CDOS corpus; checked)") + ":\n" + facts_block)
    if not_found and intent.kind == "explore":
        system.append("The CDOS corpus has no entry on this: offer your understanding as such, not as fact.")
    elif not_found:
        system.append("The CDOS corpus has nothing on this; do not supply facts about it.")
    optional: List[str] = []
    optional.extend(persona.style_lines())
    if memories:
        optional.append((memory_label or
                         "MEMORY (things " + (user or "the person") + " asked you to keep)") + ":\n"
                        + "\n".join(f"- {m}" for m in memories))
    if session.topic or session.facts:
        wm = []
        if session.topic:
            wm.append(f"topic so far: {session.topic}")
        wm.extend(f"earlier fact: {f}" for f in list(session.facts)[-2:])
        optional.append("Working memory:\n" + "\n".join(wm))

    dropped: List[str] = []
    core = "\n\n".join(system)
    used = len(core) + len(text)
    kept: List[str] = []
    for part in optional:
        if used + len(part) + 2 <= budget:
            kept.append(part)
            used += len(part) + 2
        else:
            dropped.append(part.split("\n", 1)[0][:40])
    system_text = "\n\n".join([core] + kept)

    history = session.history(HISTORY.get(mode, 4))
    while history and used + sum(len(h["content"]) for h in history) > budget:
        history = history[2:] if len(history) > 1 else []
        dropped.append("an old turn")
    used += sum(len(h["content"]) for h in history)

    messages = [{"role": "system", "content": system_text}, *history, {"role": "user", "content": text}]
    return Built(messages, sum(len(m["content"]) for m in messages), dropped, len(history) // 2)
