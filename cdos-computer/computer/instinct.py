"""SAMPLER: format a random I Ching cast as labelled, non-factual computer output.

    question ─► cast (iching.py) ─► Perspective
                                       .brief   the first output (2-3 sentences)
                                       .more    the output for "tell me more"
                                       .card    the structured reading (for the mini LM tool)

Everything here is deterministic given the cast: templates over the
hexagram's own words.  The sampler offers a random *framing*; it never
states a fact about the world, never predicts, and is never used as
evidence.  The core (core.py) decides whether it runs at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .iching import Entropy, Reading, book, cast, draw

STANCE_ING = {
    "advance": "moving forward", "persist": "keeping steady", "wait": "waiting",
    "yield": "yielding", "retreat": "stepping back", "gather": "gathering together",
    "release": "letting go", "nourish": "nourishing", "reflect": "reflecting",
    "change": "changing", "begin": "beginning", "balance": "finding balance",
}

OPENERS = ["SAMPLER OUTPUT (random, non-factual).", "RANDOM SAMPLE: I Ching.",
           "I Ching sample generated.", "Sampler run complete."]

FELL = ["Hexagram: {name}. Image: {image}.",
        "Result: {name}; {image}.",
        "Cast resolved to {name}: {image}."]

TOPIC = ["Re {topic}:", "Query topic, {topic}:", "Applied to {topic}:"]

MOVING = ["Transition: becomes {to}; state {a} to {b}.",
          "Changing lines detected: resolves to {to}, {a} to {b}.",
          "Next state: {to}, from {a} toward {b}."]

STILL = ["No changing lines: state is stable.",
         "No transition detected."]

FUTURE = ["NOTICE: this is not a prediction. The sampler cannot see the future.",
          "NOTICE: random output only; no forecast is made."]

SAYINGS_INTRO = ["STORED APHORISM:", "RANDOM APHORISM:", "APHORISM:"]

# Swap the asker's words so the output can echo them back.
_SWAP = {"i": "you", "me": "you", "my": "your", "mine": "yours", "myself": "yourself",
         "am": "are", "i'm": "you're", "im": "you're", "i've": "you've", "i'll": "you'll",
         "i'd": "you'd", "was": "were", "your": "my", "you": "I", "yours": "mine"}

_TOPIC_PATTERNS = [
    (r"\bis it (?:a good idea|wise|smart|time) (?:to|for me to) (.+)", "whether to {}"),
    (r"\bdo i (?:need|have) to (.+)", "whether you need to {}"),
    (r"\bwhat (?:should|can|do) (?:i|we) do about (.+)", "{}"),
    (r"\bhow (?:should|can|do) (?:i|we) (?:handle|deal with|approach|face) (.+)", "{}"),
    (r"\bwhat do you think (?:of|about) (.+)", "{}"),
    (r"\bshould (?:i|we) (.+)", "whether to {}"),
    (r"\b(?:advice|wisdom|guidance|reading) (?:on|about|for) (.+)", "{}"),
    (r"\bwill my (\w+ .+)", "whether your {}"),          # "will my plan work" (verb fixed below)
    (r"\bwill (?:i|we) (.+)", "whether you will {}"),
    (r"\bwill it (.+)", "whether it will {}"),
    (r"\bask(?:ing)? the (?:oracle|coins|computer|sampler|owl|i ching) (?:about|if|whether) (.+)", "{}"),
]
_FUTURE = re.compile(r"^\s*(will|am i going to|is (?:it|he|she|this) going to|are we going to)\b|\bwill (i|my|we)\b", re.I)


def swap_person(text: str) -> str:
    words = re.findall(r"[\w']+|[^\w\s]", text)
    out = [_SWAP.get(w.lower(), w) for w in words]
    s = " ".join(out)
    return re.sub(r"\s+([,.!?;:])", r"\1", s).strip(" ?.!")


def topic_of(question: str) -> str:
    q = question.strip().rstrip("?!. ")
    for pat, fmt in _TOPIC_PATTERNS:
        m = re.search(pat, q, re.I)
        if m:
            body = m.group(1)
            if fmt == "whether your {}":
                noun, rest = body.split(" ", 1)
                return f"whether your {noun} will {swap_person(rest)}"
            return fmt.format(swap_person(body))
    return ""


@dataclass
class Perspective:
    reading: Reading
    brief: str
    more: str
    saying: str
    mood: str
    card: dict = field(default_factory=dict)


def _pick(rng: Entropy, options: list[str]) -> str:
    return rng.choice(options)


def perspective(question: str = "", method: str = "coins", seed=None, extra=b"",
                reading: Reading | None = None, opener: bool = True) -> Perspective:
    """Cast (or reuse a cast) and format it as sampler output."""
    r = reading or cast(question, method, seed, extra)
    rng = Entropy(seed if seed is not None else r.fingerprint, "voice")   # wording follows the cast
    p, rel = r.primary, r.relating
    info = p.info
    lines = book()["lines"]

    parts = []
    if opener:
        parts.append(_pick(rng, OPENERS))
    parts.append(_pick(rng, FELL).format(name=p.name, image=p.image()))
    topic = topic_of(question)
    counsel = info["counsel"]
    if topic:
        parts.append(_pick(rng, TOPIC).format(topic=topic) + " " + counsel[0].lower() + counsel[1:])
    else:
        parts.append(counsel)
    if rel:
        a, b = STANCE_ING[info["stance"]], STANCE_ING[rel.info["stance"]]
        if a == b:
            parts.append(f"Transition: becomes {rel.name}; state unchanged, {a}.")
        else:
            parts.append(_pick(rng, MOVING).format(to=rel.name, a=a, b=b))
    if question and _FUTURE.search(question):
        parts.append(_pick(rng, FUTURE))
    brief = " ".join(parts)

    if p.upper is p.lower:
        more = [f"{p.name}, number {p.n}: {p.upper['en']} doubled, {p.upper['quality']} upon {p.upper['quality']}."]
    else:
        more = [f"{p.name}, number {p.n}. Upper trigram {p.upper['en']}, lower trigram {p.lower['en']}: "
                f"{p.upper['quality']} over {p.lower['quality']}."]
    if r.changing:
        where = ", and ".join(lines[str(i)] for i in r.changing[:2])
        more.append(f"The moving line{'s are' if len(r.changing) > 1 else ' is'} {where}.")
    if rel:
        more.append(f"Next state {rel.name}: {rel.info['counsel']}")
    hidden = p.nuclear()
    if hidden.n != p.n:
        more.append(f"Nuclear hexagram: {hidden.name}, theme {hidden.info['theme']}.")
    saying = _pick(rng, info["sayings"])
    more.append(f"APHORISM: {saying}")

    return Perspective(r, brief, " ".join(more), saying, info["mood"], card=r.as_dict())


def wise_saying(seed=None) -> tuple[str, int]:
    """A random stored aphorism, for 'say something wise' and idle output."""
    h = draw(seed)
    rng = Entropy(seed if seed is not None else None, "saying")
    return f"{rng.choice(SAYINGS_INTRO)} {rng.choice(h.info['sayings'])}", h.n
