"""The router: what is the person trying to do, and which faculty answers?

Rules only (deterministic, fast on a Pi Zero).  Every intent says which
faculty owns it:

    knowledge   the corpus answers (facts, definitions)
    logic       code answers (maths, time, small talk, "I can't do that")
    instinct    the I Ching answers (open, personal, "should I" questions)
    care        someone may be hurt or in danger: no coins, no jokes, a person

The *policy* lives here too and is never left to chance or to a model:
a question that touches health or safety is never handed to the coins,
even if it is phrased "should I...".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .calc import calculate
from .diagnostics import STATUS

I = re.I


@dataclass
class Intent:
    id: str
    faculty: str
    rules: list = field(default_factory=list)   # (regex, confidence)
    sensitive: bool = False


INTENTS = [
    Intent("care", "care", [
        (re.compile(r"\b(kill (my|him|her|them)self|kill myself|suicid\w*|hurt(ing)? myself|self[- ]harm|want to die|"
                    r"(someone|somebody|he|she|they) (is|are|keeps?) (hurting|hitting|touching) me|i'?m (being )?(abused|bullied)|"
                    r"emergency|can'?t breathe|i'?m scared for my life)\b", I), 1.0)], sensitive=True),
    Intent("smalltalk", "logic", [
        (re.compile(r"^(hi|hello|hey|hiya|yo|good (morning|afternoon|evening|night))\b[\s!.,]*\w{0,12}[\s!.]*$", I), 0.95),
        (re.compile(r"^(thanks|thank you|cheers|ok|okay|cool|nice|great)\b[\s!.]*$", I), 0.95),
        (re.compile(r"\bhow are you\b|\bhow('s| is) it going\b", I), 0.9),
        (re.compile(r"\b(who|what) are you\b|\byour name\b|\bwhat can you do\b", I), 0.9),
        (re.compile(r"^(bye|goodbye|good night|see you|later)\b", I), 0.95)]),
    Intent("status", "logic", [(STATUS, 0.95)]),    # diagnostics: read from the machine (computer/diagnostics.py)
    Intent("clock", "logic", [
        (re.compile(r"\bwhat('s| is)? (the )?(time|date)\b|\bwhat time is it\b|\bwhat day is (it|today)\b|"
                    r"\btoday'?s date\b|\bwhat year is it\b", I), 0.95)]),
    Intent("calc", "logic"),   # detected by actually parsing the maths
    Intent("more", "instinct", [
        (re.compile(r"^(tell me more|more|go on|what does (that|it) mean|explain (that|it)|and\??|why\??|"
                    r"what do you mean|say more)\b[\s?!.]*$", I), 0.9)]),
    Intent("saying", "instinct", [
        (re.compile(r"\b(say|tell me|teach me|share|give me) (something|anything|a little|some) (wise|clever|deep|nice)\b|"
                    r"\b(wise|owl) (saying|thought|words)\b|\bwords of wisdom\b|\bteach me something\b|"
                    r"\bany wisdom\b", I), 0.95)]),
    Intent("oracle", "instinct", [
        (re.compile(r"\b(i ching|iching|oracle|cast (the )?(coins|lines|hexagram)|hexagram|reading|consult)\b", I), 0.95)]),
    Intent("wisdom", "instinct", [
        (re.compile(r"\bshould (i|we)\b|\bwhat (should|can|do) (i|we) do\b|\bis it (a good idea|wise|smart|time)\b|"
                    r"\b(advice|guidance)\b|\bhelp me (decide|choose)\b|\bwhat do you think (of|about|i)\b|"
                    r"\bdo you think (i|we|my)\b|\bhow (should|can|do) (i|we) (handle|deal with|approach|face)\b|"
                    r"\bwill (i|we|my)\b|\bam i (doing|making)\b|\bwhy (do|does|is) (life|love)\b|"
                    r"\bwhat is the meaning of life\b|\bi feel (lost|stuck|confused|sad|worried|nervous)\b", I), 0.9)]),
    Intent("health", "knowledge", [
        (re.compile(r"\b(symptoms?|diagnos\w*|medicat\w*|medicine|pills?|dose|dosage|side effects?|pain|ache|fever|"
                    r"headache|rash|disease|illness|injur\w*|doctor|pregnan\w*|blood pressure|diabetes|cancer|"
                    r"allerg\w*|infection|vaccine|surgery|therapy|sick|nause\w*|vomit\w*|bleed\w*|dizz\w*)\b", I), 0.85)],
        sensitive=True),
    Intent("money", "knowledge", [
        (re.compile(r"\b(invest\w*|stocks?|crypto\w*|bitcoin|loan|mortgage|gambl\w*|bet|lottery|lawsuit|sue|lawyer|"
                    r"contract|taxes)\b", I), 0.8)], sensitive=True),
    Intent("define", "knowledge", [
        (re.compile(r"\b(define|definition of|meaning of|what does .{1,40} mean|synonyms? (of|for|to)|antonyms? (of|for|to)|"
                    r"opposite (of|to)|(another|other|different|fancy|fancier|simpler) words? for|a word (for|that means)|"
                    r"(use|put) .{1,30} in a sentence|example sentences?|(kinds|types|sorts|examples) of|"
                    r"what (kind|type|sort) of \w+ is|words? related to|synonyms|antonyms|what .{1,30} means)\b", I), 0.9)]),
    Intent("explain", "knowledge", [
        (re.compile(r"^(why|explain|compare|what('s| is) the difference)\b|\b(vs\.?|versus|compared (to|with))\b|"
                    r"\bwhich is (bigger|larger|heavier|smaller|older)\b", I), 0.85)]),
    Intent("fact", "knowledge", [
        (re.compile(r"^(what|which|who|where|when|how (many|much|big|far|old|tall|long|heavy|hot|cold))\b", I), 0.6),
        (re.compile(r"^(is|are|was|were|does|did|has|have)\b", I), 0.55),
        (re.compile(r"\btell me about\b", I), 0.85)]),
    Intent("unavailable", "logic", [
        (re.compile(r"\b(turn|switch) (on|off)\b|\bset (an? |the )?(alarm|timer|thermostat)\b|"
                    r"\b(latest|today'?s|current) (news|weather|price|score)\b|\bsearch the (web|internet)\b", I), 0.9)]),
]
BY_ID = {i.id: i for i in INTENTS}


@dataclass
class Route:
    intent: str
    faculty: str
    conf: float
    why: str
    calc: dict | None = None
    sensitive: bool = False
    also: list = field(default_factory=list)   # every other intent whose rules matched


def classify(text: str) -> Route:
    c = calculate(text)
    if c and not BY_ID["care"].rules[0][0].search(text):
        return Route("calc", "logic", 0.95, f"parsed as maths: {c['expr']}", calc=c)
    hits = []
    for it in INTENTS:
        for rx, w in it.rules:
            if rx.search(text):
                hits.append((w, it))
                break
    if not hits:
        return Route("fact", "knowledge", 0.3, "no rule matched: look it up")
    hits.sort(key=lambda h: -h[0])
    w, best = hits[0]
    also = [h[1].id for h in hits[1:]]
    sensitive = any(h[1].sensitive for h in hits)
    # Policy: the coins never answer a health, money or safety question,
    # however it is phrased ("should I stop taking my medicine?").
    if best.faculty == "instinct" and sensitive and best.id != "saying":
        guard = next(h[1] for h in hits if h[1].sensitive)
        return Route(guard.id, guard.faculty, w, f"'{best.id}' matched, but '{guard.id}' is sensitive: no coins",
                     sensitive=True, also=[best.id] + [a for a in also if a != guard.id])
    return Route(best.id, best.faculty, w, f"rule for '{best.id}'", sensitive=sensitive, also=also)
