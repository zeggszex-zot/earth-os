"""What kind of turn this is, and how much thought it deserves. No model.

Every decision that matters for safety or cost is made here, deterministically,
before a model is anywhere near the sentence:

* **sensitive** turns (self-harm, health, money, law) never reach a model;
* **task** turns are handed back to the shell's command path;
* **knowledge** and **calculation** turns go to CDOS Computer first;
* everything else is conversation, and the mode says how much of it.

The classifier is deliberately simple and readable. A wrong guess costs a
slightly odd reply, never an action: the companion cannot act.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

CASUAL, REFLECTIVE, DEEP = "CASUAL", "REFLECTIVE", "DEEP"
MODES = (CASUAL, REFLECTIVE, DEEP)

KINDS = ("greeting", "chat", "feeling", "opinion", "imagination", "explore", "knowledge",
         "calculation", "task", "sensitive", "mode", "reset")

_WAKE = re.compile(r"^\s*(?:hey\s+|ok\s+)?(?:computer|cdos)\b[\s,:;.!-]*", re.I)


def normalise(text: str) -> str:
    """Lower case, wake word dropped, apostrophes kept, other punctuation gone."""
    raw = _WAKE.sub("", text or "")
    raw = raw.replace("’", "'").lower()
    return " ".join(re.sub(r"[^\w\s'%.+\-*/?]", " ", raw).split())


def _any(patterns, text: str) -> Optional[str]:
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(0)
    return None


# -- sensitive ---------------------------------------------------------------
SENSITIVE = {
    "self_harm": (
        r"\b(?:kill|hurt|harm|cut) (?:myself|my self)\b", r"\bsuicid", r"\bend (?:my|it all)\b.*\blife\b|\bend it all\b",
        r"\b(?:want|wanted|going) to die\b", r"\bself[- ]harm", r"\bdon'?t want to (?:live|be alive|be here)\b",
        r"\bno reason to live\b", r"\boverdose\b",
    ),
    "health": (
        r"\b(?:medicine|medication|meds|dosage|dose|prescription|antibiotics?|painkillers?)\b",
        r"\b(?:symptoms?|diagnos\w*|fever|bleeding|rash|lump|infection)\b",
        r"\bchest (?:pain|hurts|is tight)\b", r"\bpain in my\b",
        r"\bmy (?:chest|head|stomach|heart|back|tooth|throat|arm|leg|side) (?:hurts|aches|is hurting|is killing me)\b",
        r"\bshould i (?:take|stop taking|see a doctor)\b", r"\bam i (?:sick|ill|pregnant)\b",
        r"\b(?:heart attack|stroke|seizure)\b",
    ),
    "money": (
        r"\b(?:invest\w*|stocks?|shares|crypto\w*|bitcoin|mortgage|loan|debt|taxes|tax return|retirement fund)\b",
        r"\bshould i (?:buy|sell) (?:a |my |the )?(?:house|car|shares|stock|bonds?)\b", r"\bfinancial advice\b",
    ),
    "legal": (
        r"\b(?:lawyer|attorney|lawsuit|sue|legal advice|court case|custody|is it legal|against the law)\b",
    ),
}

# -- turns about the computer's own conversation -----------------------------
MODE_SET = (
    (DEEP, (r"\blet'?s go deep(?:er)?\b", r"\bgo deeper\b", r"\bdeep mode\b", r"\bthink harder\b")),
    (REFLECTIVE, (r"\blet'?s reflect\b", r"\breflective mode\b", r"\bthink it over with me\b")),
    (CASUAL, (r"\bkeep it light\b", r"\bkeep it (?:short|simple|casual)\b", r"\bcasual mode\b",
              r"\bjust chat(?:ting)?\b", r"\bless detail\b")),
)
RESET = (r"^(?:let'?s )?(?:start over|start again|new topic|change the subject|forget this conversation|reset)\b",)

# -- conversation ------------------------------------------------------------
GREETING = (
    r"^(?:hi|hello|hey|hiya|howdy|greetings|good (?:morning|afternoon|evening|night)|yo)\b",
    r"^(?:how are you|how'?s it going|how are things|what'?s up|how have you been|how do you do)\b",
    r"^(?:thanks|thank you|cheers|bye|goodbye|see you|good night|night)\b",
)
IMAGINATION = (
    r"\bimagine\b", r"\bwhat if\b", r"\bpretend\b", r"\bsuppose\b", r"\bdream up\b", r"\bmake up\b",
    r"\binvent (?:a|an|some)\b", r"\b(?:tell|write) me (?:a|an) (?:story|poem|tale|limerick|haiku)\b",
    r"\bstory about\b", r"\bpoem about\b", r"\bonce upon a time\b",
)
OPINION = (
    r"\bwhat do you think\b", r"\bdo you think\b", r"\byour (?:opinion|view|take)\b", r"\bdo you (?:believe|like|love|enjoy|prefer|ever)\b",
    r"\bwould you rather\b", r"\bhow do you feel about\b", r"\bwhat'?s your fav(?:ou)?rite\b", r"\bwhich do you prefer\b",
    r"\bis it better to\b", r"\bpros and cons\b", r"\bwhat would you do\b", r"\bwhat matters\b",
    r"\bwhat makes (?:a|an|something|someone)\b", r"\bmeaning of (?:life|it all)\b",
)
FEELING = (
    r"^i (?:feel|felt|am feeling|'m feeling)\b", r"^i'?m (?:feeling )?(?:so |really |a bit |kind of |very )?"
    r"(?:sad|happy|tired|lonely|anxious|stressed|excited|bored|worried|angry|upset|down|nervous|scared|"
    r"frustrated|overwhelmed|exhausted|proud|grateful|confused|lost)\b",
    r"^i am (?:so |really |a bit |very )?(?:sad|happy|tired|lonely|anxious|stressed|excited|bored|worried|angry|"
    r"upset|down|nervous|scared|frustrated|overwhelmed|exhausted|proud|grateful|confused|lost)\b",
    r"^(?:today|this week|yesterday) (?:was|has been)\b", r"^i had a (?:bad|good|rough|long|great|hard|weird) (?:day|week|night)\b",
)
DEPTH = (r"\bin depth\b", r"\bdeep dive\b", r"\bstep by step\b", r"\bthink (?:carefully|it through|this through)\b",
         r"\bthoroughly\b", r"\bexplore\b", r"\belaborate\b", r"\bin detail\b", r"\bwalk me through\b")
REFLECT = (r"^why\b", r"\bwhy (?:do|does|did|is|are|would|should|can't|don't)\b", r"\bhow come\b",
           r"\bperspectives?\b", r"\bon the other hand\b", r"\bthe point of\b")

# -- knowledge and sums ------------------------------------------------------
KNOWLEDGE = (
    r"^(?:what|who|where|when|which)(?: is| are| was| were|'s| does| did| do)?\b",
    r"^how (?:many|much|big|far|old|tall|long|heavy|hot|cold|large|small|fast|deep|high)\b",
    r"^(?:is|are|was|were|does|do|did|can|has|have) (?:a|an|the)?\s*\w+",
    r"^(?:tell me (?:about|something about)|explain|define|describe|walk me through|help me understand)\b",
    r"\b(?:another word for|opposite of|synonym|definition of|meaning of the word)\b",
)
ABOUT_YOU = (r"\b(?:you|your|yourself|you're|youre)\b",)
CALC = (
    r"\d\s*[-+*/x×^]\s*\d", r"\b\d+(?:\.\d+)?\s*(?:percent|%)\s*of\b",
    r"\b(?:plus|minus|times|divided by|multiplied by|squared|square root)\b.*\d|\d.*\b(?:plus|minus|times|divided by|multiplied by|squared)\b",
    r"^convert\b", r"\bhow many \w+ (?:in|are in) (?:a|an|one|\d)",
)

# -- tasks: what a command is for, not a conversation ------------------------
TASK = (
    r"^(?:please )?(?:open|close|launch|start|run|quit|exit|shut ?down|power off|reboot|restart|suspend|"
    r"lock|unlock|mute|unmute|delete|remove|install|uninstall|kill|set|switch to|go to|show me the|"
    r"turn (?:\w+ ){0,3}?(?:on|off|up|down)|increase|decrease|raise|lower|dim|brighten|take a screenshot|play|pause|"
    r"skip|navigate to|copy|move|rename|format|download|upload|send|email|call)\b",
    r"\b(?:red alert|auto ?destruct|self ?destruct)\b",
)

# -- markers that a sentence is talk, for the conversation guard -------------
CONVERSATIONAL = (
    r"^i (?:was|am|'m|have been|'ve been|keep|kept|just|really|sometimes|often|never|always)?\s*"
    r"(?:thinking|wondering|imagining|remembering|dreaming|feel|felt|feeling|think|thought|wonder|wish|hope|"
    r"like|love|enjoy|miss|remember|believe|guess|suppose)\b",
    r"\bimagine\b", r"\bwhat if\b", r"\btell me about\b", r"\bwhat do you think\b", r"\bdo you think\b",
    r"\blet'?s talk\b", r"\bcan we talk\b", r"\bhow do you feel\b", r"\bi wonder\b", r"\bpretend\b",
    r"\bstory\b", r"\bwhy (?:do|does|is|are|would)\b", r"\bwhat'?s it like\b", r"^do you\b",
)


@dataclass
class Intent:
    kind: str
    mode: str = CASUAL
    topic: str = ""
    category: str = ""                 # for sensitive: self_harm | health | money | legal
    wants_knowledge: bool = False
    sticky_mode: Optional[str] = None  # a mode the person set for the session
    why: List[str] = field(default_factory=list)


def sensitive_category(norm: str) -> Optional[Tuple[str, str]]:
    for category, patterns in SENSITIVE.items():
        hit = _any(patterns, norm)
        if hit:
            return category, hit
    return None


_TOPIC_STOP = {"the", "a", "an", "it", "that", "this", "things", "something", "stuff", "me", "you", "us",
               "my", "your", "our", "life", "everything"}


_DEPTH_WORDS = re.compile(r"\b(?:step by step|in depth|in detail|thoroughly|deep dive|carefully)\b")
_LEAD_Q = {"how", "why", "what", "whether", "when", "where", "who"}


def extract_topic(norm: str) -> str:
    """A short subject for retrieval: "what do you think about jupiter" → "jupiter"."""
    norm = " ".join(_DEPTH_WORDS.sub(" ", norm).split())
    m = re.search(r"\b(?:about|of|on|regarding|re) (?:the |a |an )?([a-z][\w' -]{1,40}?)(?:\?|$| and | or | because | if | when |,)", norm + " ")
    if not m:
        m = re.search(r"^(?:tell me about|explain|describe|define|walk me through|help me understand) (?:the |a |an )?([a-z][\w' -]{1,40})", norm)
    if not m:
        return ""
    words = [w for w in m.group(1).strip(" ?.").split() if w]
    while words and (words[0] in _TOPIC_STOP or words[0] in _LEAD_Q):
        words.pop(0)
    words = words[:4]
    if not words or all(w in _TOPIC_STOP for w in words):
        return ""
    return " ".join(words)


def classify(text: str, sticky_mode: Optional[str] = None) -> Intent:
    """One turn's intent and mode. *sticky_mode* is the session's chosen mode."""
    norm = normalise(text)
    why: List[str] = []

    for mode, patterns in MODE_SET:
        hit = _any(patterns, norm)
        if hit and len(norm.split()) <= 6:
            return Intent("mode", mode=mode, sticky_mode=mode, why=[f"mode request: {hit!r}"])
    if _any(RESET, norm):
        return Intent("reset", why=["reset request"])

    found = sensitive_category(norm)
    if found:
        return Intent("sensitive", category=found[0], why=[f"{found[0]}: {found[1]!r}"])

    # Asking what the computer thinks, or imagining, is conversation even when
    # it starts like a fact question ("what do you think of Jupiter").
    if _any(IMAGINATION, norm):
        kind = "imagination"
    elif _any(OPINION, norm):
        kind = "opinion"
    elif _any(FEELING, norm):
        kind = "feeling"
    elif _any(GREETING, norm) and len(norm.split()) <= 6:
        kind = "greeting"
    elif _any(TASK, norm):
        kind = "task"
    elif _any(CALC, norm):
        kind = "calculation"
    elif _any(REFLECT, norm) and not _any(ABOUT_YOU, norm) and not norm.startswith(("explain", "describe", "define", "tell me")):
        kind = "explore"
    elif _any(KNOWLEDGE, norm) and not _any(ABOUT_YOU, norm):
        kind = "knowledge"
    else:
        kind = "chat"
    why.append(f"kind {kind}")

    # Mode: the turn's own signal, then the session's, then the kind's default.
    depth = _any(DEPTH, norm)
    long_multi = norm.count("?") >= 2 and len(norm.split()) >= 25
    if depth or long_multi:
        mode = DEEP
        why.append(f"deep: {depth!r}" if depth else "deep: several questions at once")
    elif sticky_mode in MODES:
        mode = sticky_mode
        why.append(f"session mode {sticky_mode}")
    elif kind in ("opinion", "feeling") or _any(REFLECT, norm):
        mode = REFLECTIVE
        why.append("reflective: opinion, feeling or why")
    else:
        mode = CASUAL

    topic = extract_topic(norm)
    wants = kind in ("knowledge", "calculation", "explore") or (mode in (REFLECTIVE, DEEP) and bool(topic)
                                                    and kind not in ("feeling", "imagination", "greeting"))
    return Intent(kind, mode=mode, topic=topic, wants_knowledge=wants, why=why)


def is_conversational(text: str) -> bool:
    """Plainly talk, whatever words of a command it happens to contain."""
    norm = normalise(text)
    if _any(TASK, norm):
        return False
    return bool(_any(CONVERSATIONAL, norm) or _any(FEELING, norm) or _any(OPINION, norm)
                or _any(IMAGINATION, norm))
