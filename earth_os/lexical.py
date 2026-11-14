"""Earth Semantic Intelligence, phase 1: what the words in a sentence mean.

The capability corpus knows how a person asks for things, as phrases.  A
phrase is matched as written, so the corpus could answer "using the most
memory" and not "using the most RAM", "computer is slow" and not "laptop is
slow", "how hot is it" and not "how warm are you".  The learned layer in
``semantics.py`` already knew that RAM is memory; nothing let the corpus use
that.  Every new wording had to be typed into a tuple by hand.

This module reads a sentence as *concepts* instead of characters:

* **contractions** are undone — "you're" is "you are", "don't" is "do not";
* **inflections** are reduced — "hogging" is "hog", "processes" is "process";
* **synonyms** collapse to one concept — ram is memory, laptop is computer,
  sluggish is slow, hogging is using;
* **parts** imply their whole — a sentence about the processor is a sentence
  about the computer, so "the cpu is slow" is the computer being slow;
* **filler** carries nothing and is dropped — "the", "is", "really".

What comes out is a set of concepts, and the corpus asks whether one of its
phrases, read the same way, is contained in it.  Both sides go through the
same reading, so the reading only has to be consistent, not linguistically
perfect.

What this module deliberately does not do:

* **It never decides what to run.**  It answers "what do these words mean";
  ``CapabilityCorpus`` decides what the machine is being asked, and only for
  readings and analyses — never for an act (see ``capability_corpus.py``).
* **It names no mechanism.**  It is meaning, so it lives above the boundary.
* **It holds almost nothing.**  The concept table is a few hundred words.  The
  English word list (Princeton WordNet 3.0, ``assets/lexicon/``) is not loaded
  at start; it is read the first time :func:`is_english` is asked, as one
  compressed blob, because only the learned layer's typo matching needs it.
  Loading it as a Python set would cost 91 ms and 8 MB; the blob costs 3 ms
  and about 1.3 MB, and is never loaded on a machine that is never mistyped at.

The design and its measurements are in ``docs/ESI_ARCHITECTURE.md``.
"""

from __future__ import annotations

import gzip
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

ASSET = Path(__file__).resolve().parent / "assets" / "lexicon" / "english.words.gz"
#: Words a person approved from the CDOS Dictionary Bridge (phase 2). Small,
#: hand-reviewable JSON; ``tools/esi_bridge.py`` is the only thing that writes
#: it, and only on a named person's say-so. See docs/ESI_ARCHITECTURE.md §7.
APPROVED = ASSET.parent / "approved.json"

_PUNCT = re.compile(r"[^\w\s%]")
_WS = re.compile(r"\s+")

# --------------------------------------------------------------------------
# Tables.  Meaning, written down by a person; this is the part to grow.
# --------------------------------------------------------------------------

#: Concept -> the words that mean it, in this machine's world.  The concept's
#: own name is always one of its words.  Keep every group one meaning wide:
#: a word in two groups is a test failure (``tests/test_esi.py``).
CONCEPTS: Dict[str, Tuple[str, ...]] = {
    # the machine and its parts
    "computer": ("computer", "machine", "pc", "laptop", "desktop", "system",
                 "box", "you", "your", "yours", "yourself", "it", "its"),
    "memory": ("memory", "ram"),
    "disk": ("disk", "drive", "storage", "ssd", "hdd", "harddrive", "harddisk"),
    "cpu": ("cpu", "processor", "cpus", "processors"),
    "screen": ("screen", "display", "monitor"),
    "battery": ("battery", "charge"),
    "network": ("network", "internet", "connection", "net"),
    "wifi": ("wifi", "wireless", "wlan"),
    "program": ("program", "programme", "app", "application", "software"),
    "temperature": ("temperature", "temp", "temps", "thermal"),
    # what the parts are doing
    "use": ("use", "eat", "consume", "occupy", "gobble", "devour",
            "chew", "hold"),
    "load": ("load", "usage", "utilisation", "utilization", "busy"),
    "slow": ("slow", "slowly", "sluggish", "laggy", "lag", "crawl",
             "unresponsive"),
    "hot": ("hot", "warm", "heat", "heated"),
    "overheat": ("overheat",),
    "call": ("call", "name", "named"),
    "capacity": ("capacity", "space", "room", "free", "full", "empty"),
    # the person
    "user": ("user", "i", "my", "mine", "myself"),
}

#: Two or more words that are one concept.  Matched on reduced words, so
#: "taking up" and "takes up" are both ``take up``.
MULTIWORD: Dict[Tuple[str, ...], str] = {
    ("hard", "drive"): "disk",
    ("hard", "disk"): "disk",
    ("solid", "state", "drive"): "disk",
    ("wi", "fi"): "wifi",
    ("take", "up"): "use",
    ("work", "hard"): "load",
    ("run", "hot"): "overheat",
    ("run", "warm"): "overheat",
}

#: A part implies the whole: a sentence about one of these is a sentence about
#: the computer.  Only the *utterance* gains the whole, never a phrase — the
#: utterance may say more than a phrase, never less.
PARTS: FrozenSet[str] = frozenset({
    "memory", "disk", "cpu", "screen", "battery", "network", "wifi",
    "temperature",
})

#: Concepts that make a phrase about this machine.  A phrase read by meaning
#: must require at least one: "sluggish" alone is a phrase, but read by
#: meaning it is just "slow", and a slow week at work is not a diagnosis.
MACHINE: FrozenSet[str] = PARTS | frozenset({
    "computer", "program", "process", "swap", "core", "bluetooth", "volume",
    "resolution", "uptime", "kernel", "hostname", "brightness", "distro",
    "interface",
})

#: Possessives, and whose they say a thing is. "My memory" is the person's
#: memory; "your memory" is the computer's. Each attaches to the noun after
#: it rather than floating free in the sentence (phase 3A, docs §8).
POSSESSIVE: Dict[str, str] = {"my": "user", "your": "computer"}

#: Words that may stand between a possessive and its noun — "my *free*
#: memory", "your *whole* disk". Skipped when looking for the noun, unless
#: nothing else follows.
MODIFIERS: FrozenSet[str] = frozenset("""
    free full empty whole entire own current main new old poor little
    internal external available total used spare local primary default
    wireless big small entire
""".split())

#: How far after a possessive its noun may be.
_ATTACH_WINDOW = 3

#: Question words, and the one they all read as. "Which processor" asks what
#: "what processor" asks (routing audit P1).
QUESTION_WORDS: Dict[str, str] = {"what": "what", "which": "what"}

#: Words a question word passes over to reach its noun: "what *kind of*
#: processor", "what *model* is the cpu".
QUESTION_FILLER: FrozenSet[str] = frozenset("""
    kind kinds type types sort sorts model make brand version
""".split())

#: Words that can end a compound a question word points into: "what is
#: the screen *resolution*" asks about the resolution, not the screen.
_COMPOUND_HEADS: FrozenSet[str] = frozenset({
    "load", "temperature", "resolution", "volume", "brightness", "uptime",
    "capacity", "speed", "level", "size", "model", "usage", "network",
})

#: Concepts whose *identity* this machine can report — "which processor",
#: "what is your name", "what hardware". Only these: "what ram do I have" is
#: answered by how much there is, because no reading names a memory module,
#: and treating it as an identity question would refuse the right answer.
DEVICES: FrozenSet[str] = frozenset({
    "cpu", "computer", "kernel", "distro", "hostname", "call", "hardware", "device",
})

#: Forms of the three auxiliaries, which no suffix rule reduces.
AUXILIARY: Dict[str, str] = {
    "am": "be", "is": "be", "are": "be", "was": "be", "were": "be",
    "been": "be", "being": "be",
    "has": "have", "had": "have", "having": "have",
    "does": "do", "did": "do", "doing": "do", "done": "do",
}

#: Irregular inflections this machine's words need.  The full WordNet list
#: is not loaded for this (see the module docstring); a handful of verbs is
#: all the phrasebook uses.
IRREGULAR: Dict[str, str] = {
    "ate": "eat", "eaten": "eat", "took": "take", "taken": "take",
    "ran": "run", "got": "get", "gotten": "get", "went": "go", "gone": "go",
    "made": "make", "held": "hold", "left": "leave", "became": "become",
    "slower": "slow", "slowest": "slow", "hotter": "hot", "hottest": "hot",
    "busier": "busy", "busiest": "busy", "fuller": "full",
    # "Hogging memory" is using it; "a memory hog" is a program.  Only the
    # verb forms are a synonym, so the noun keeps its own meaning.
    "hogging": "use", "hogged": "use",
}

#: Contraction fragments, as ``normalise`` leaves them ("you re", "don t").
_CONTRACTED_NOT = {
    "don": "do", "doesn": "do", "didn": "do", "isn": "be", "aren": "be",
    "wasn": "be", "weren": "be", "won": "will", "wouldn": "would",
    "couldn": "could", "shouldn": "should", "haven": "have", "hasn": "have",
    "hadn": "have", "can": "can", "ain": "be", "mustn": "must",
}
_CONTRACTED = {"re": "be", "m": "be", "ll": "will", "ve": "have", "d": "would",
               "s": ""}

#: Said in a sentence that says *not* to.  The corpus will not read a negated
#: sentence by meaning at all, and will not let one start an act.
NEGATION: FrozenSet[str] = frozenset({"not", "never", "dont", "cannot"})

#: Words that carry nothing a phrase depends on.  Kept short on purpose:
#: "how", "what", "much", "most", "on" all decide which question is meant.
STOP: FrozenSet[str] = frozenset("""
    a an the be do to of at in for with by from this that these those
    there here so too very really quite just please now currently right
    still even again ever pretty rather s will would can could should
    may might must shall hey ok okay oh um uh well me
""".split())


# --------------------------------------------------------------------------
# Reading a sentence
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Word:
    """One concept found in a sentence, and where it came from."""

    concept: str
    surface: str        # what was actually said, for explanations
    start: int          # character span in the normalised sentence
    end: int


@dataclass
class Reading:
    """A sentence as concepts."""

    text: str                                   # the normalised sentence
    words: List[Word] = field(default_factory=list)
    negated: bool = False
    implied: FrozenSet[str] = frozenset()       # wholes implied by parts

    #: "owner>thing" for each possessive, e.g. "user>memory" for "my memory".
    attachments: FrozenSet[str] = frozenset()

    @property
    def concepts(self) -> FrozenSet[str]:
        """What the sentence means, as a set: its concepts, the wholes its
        parts imply, and whose each possessed thing is ("user>memory").

        A possessive that found its noun contributes only the attachment,
        not a free-standing "user": "my computer is struggling because
        programs are consuming memory" says the computer is the person's,
        and must not satisfy a phrase about the person's memory.
        """
        return (frozenset(w.concept for w in self.words) | self.implied
                | self.attachments)

    def span(self, concepts: Iterable[str]) -> str:
        """The part of the sentence that said *concepts*, for display and
        for the conversation guard, which compares lengths."""
        wanted = set(concepts)
        hits = [w for w in self.words if w.concept in wanted]
        if not hits:
            return ""
        return self.text[min(w.start for w in hits):max(w.end for w in hits)]

    def renamed(self) -> List[Tuple[str, str]]:
        """``(said, read as)`` for every word read as something else."""
        out = []
        for w in self.words:
            if w.surface != w.concept and (w.surface, w.concept) not in out:
                out.append((w.surface, w.concept))
        return out


def normalise(text: str) -> str:
    """Identical to ``capability_corpus.normalise``, so spans line up."""
    text = (text or "").lower().replace("’", "'").strip()
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _candidates(word: str) -> List[str]:
    """Possible base forms of *word*, most reduced first."""
    out: List[str] = []

    def add(stem: str) -> None:
        if len(stem) >= 2 and stem not in out:
            out.append(stem)

    if word.endswith("ing") and len(word) > 4:
        stem = word[:-3]
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "lsz":
            add(stem[:-1])                      # hogging -> hog
        add(stem + "e")                         # using -> use
        add(stem)                               # working -> work
    if word.endswith("ied") and len(word) > 4:
        add(word[:-3] + "y")
    elif word.endswith("ed") and len(word) > 3:
        stem = word[:-2]
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "lsz":
            add(stem[:-1])                      # hogged -> hog
        add(stem + "e")                         # used -> use
        add(stem)                               # crashed -> crash
    if word.endswith("ies") and len(word) > 4:
        add(word[:-3] + "y")                    # utilities -> utility
    elif word.endswith("es") and len(word) > 3:
        add(word[:-2])                          # processes -> process
        add(word[:-1])                          # cores -> core
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")) \
            and len(word) > 3:
        add(word[:-1])                          # programs -> program
    if word.endswith("ly") and len(word) > 4:
        add(word[:-2])                          # slowly -> slow
    return out


# --------------------------------------------------------------------------
# Approved additions (phase 2)
# --------------------------------------------------------------------------

@dataclass
class Approved:
    """Words a person has approved, as this module will use them."""

    words: Dict[str, str] = field(default_factory=dict)              # word -> concept
    phrases: Dict[Tuple[str, ...], str] = field(default_factory=dict)  # normalised words -> concept
    problems: List[str] = field(default_factory=list)                # entries refused, and why


def usable_addition(word: str, concept: str) -> str:
    """Why *word* may not join *concept*, or "" if it may.

    The same rules apply when a word is proposed, when it is approved and
    every time the approved file is read, so a hand edit to the file is held
    to exactly what the bridge would have been.
    """
    norm = normalise(word)
    if concept not in CONCEPTS:
        return f"no concept called {concept!r}"
    if not norm or not all(t.isalpha() for t in norm.split()):
        return "not plain words"
    if any(len(t) < 2 for t in norm.split()):
        return "has a single letter in it (an abbreviation with its dots removed)"
    if len(norm.split()) > 3:
        return "more than three words"
    if norm in STOP or norm in NEGATION or norm in AUXILIARY or norm in IRREGULAR \
            or norm in _CONTRACTED or norm in _CONTRACTED_NOT:
        return "a function word"
    for other, words in CONCEPTS.items():
        if norm in words:
            return ("already one of its words" if other == concept
                    else f"already a word for {other!r}")
    return ""


def load_approved(path: Optional[Path] = None) -> Approved:
    """The approved additions, each re-checked; a bad entry is skipped and named."""
    import json

    out = Approved()
    path = Path(path) if path is not None else APPROVED
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return out
    except (OSError, ValueError) as exc:
        out.problems.append(f"{path.name}: unreadable ({exc})")
        return out
    for entry in raw.get("approved", []) if isinstance(raw, dict) else []:
        try:
            word, concept = str(entry["word"]), str(entry["concept"])
            by = str(entry["approved_by"]).strip()
        except (KeyError, TypeError):
            out.problems.append(f"an entry without word, concept and approved_by: {entry!r}")
            continue
        if not by:
            out.problems.append(f"{word!r}: nobody approved it")
            continue
        why = usable_addition(word, concept)
        norm = normalise(word)
        if not why and (norm in out.words or tuple(norm.split()) in out.phrases):
            why = "approved twice"
        if why:
            out.problems.append(f"{word!r} → {concept}: {why}")
            continue
        if " " in norm:
            out.phrases[tuple(norm.split())] = concept
        else:
            out.words[norm] = concept
    return out


class Lexicon:
    """Reads sentences as concepts.

    *vocabulary* is every word the caller's phrases use.  A word is reduced
    to a base form only when that base form is a word the machine knows —
    a concept, a synonym, or a phrase word — so "running" meets "run" and
    "processes" meets "process" without an English dictionary in memory.
    A word the machine does not know is left exactly as said: it is going to
    be leftover either way.
    """

    def __init__(self, vocabulary: Iterable[str] = (),
                 approved: Optional[Approved] = None):
        """*approved* is the phase 2 additions; None reads ``approved.json``.
        Pass ``Approved()`` for the hand-written tables alone."""
        self.approved = load_approved() if approved is None else approved
        self._synonym: Dict[str, str] = {}
        for concept, words in CONCEPTS.items():
            for word in words:
                self._synonym[word] = concept
        self._synonym.update(self.approved.words)
        known = set(self._synonym) | set(CONCEPTS) | set(AUXILIARY.values())
        for words in list(MULTIWORD) + list(self.approved.phrases):
            known.update(words)
        for text in vocabulary:
            known.update(normalise(text).split())
        self._known = frozenset(known)
        self._cache: Dict[str, str] = {}
        # Multi-word concepts keyed by reduced words, so "disk drives" meets an
        # approved "disk drive" the way "taking up" meets "take up".
        self._multiword: Dict[Tuple[str, ...], str] = dict(MULTIWORD)
        for words, concept in self.approved.phrases.items():
            self._multiword.setdefault(tuple(self.base(w) for w in words), concept)
        self._longest = max((len(k) for k in self._multiword), default=1)

    # ------------------------------------------------------------------
    def base(self, word: str) -> str:
        """The base form of one word, as far as this machine needs it."""
        cached = self._cache.get(word)
        if cached is not None:
            return cached
        if word in AUXILIARY:
            result = AUXILIARY[word]
        elif word in QUESTION_WORDS:
            result = QUESTION_WORDS[word]
        elif word in IRREGULAR:
            result = IRREGULAR[word]
        elif word in self._synonym:
            result = word
        else:
            result = word
            for candidate in _candidates(word):
                if candidate in self._known:
                    result = candidate
                    break
        if len(self._cache) < 4096:
            self._cache[word] = result
        return result

    def concept(self, base: str) -> str:
        return self._synonym.get(base, base)

    # ------------------------------------------------------------------
    def read(self, text: str) -> Reading:
        """*text* as concepts, with where each one was said."""
        norm = normalise(text)
        reading = Reading(text=norm)
        tokens = [(m.group(0), m.start(), m.end())
                  for m in re.finditer(r"\S+", norm)]

        # Undo contractions; note negation.
        expanded: List[Tuple[str, int, int, str]] = []    # base, start, end, surface
        i = 0
        while i < len(tokens):
            word, start, end = tokens[i]
            after = tokens[i + 1][0] if i + 1 < len(tokens) else ""
            if word in _CONTRACTED_NOT and after == "t":
                expanded.append((_CONTRACTED_NOT[word], start, end, word))
                reading.negated = True
                i += 2
                continue
            if expanded and word in _CONTRACTED:
                replacement = _CONTRACTED[word]
                if replacement:
                    expanded.append((replacement, start, end, word))
                i += 1
                continue
            if word in NEGATION:
                reading.negated = True
            expanded.append((self.base(word), start, end, word))
            i += 1

        # Multi-word concepts, longest first, then single words.
        i = 0
        while i < len(expanded):
            for size in range(self._longest, 1, -1):
                key = tuple(item[0] for item in expanded[i:i + size])
                if len(key) == size and key in self._multiword:
                    surface = norm[expanded[i][1]:expanded[i + size - 1][2]]
                    reading.words.append(Word(self._multiword[key], surface,
                                              expanded[i][1],
                                              expanded[i + size - 1][2]))
                    i += size
                    break
            else:
                base, start, end, surface = expanded[i]
                i += 1
                if base in STOP or base in NEGATION:
                    continue
                reading.words.append(Word(self.concept(base), surface, start, end))

        self._attach(reading)
        present = {w.concept for w in reading.words}
        if present & PARTS:
            reading.implied = frozenset({"computer"}) - present
        return reading

    @staticmethod
    def _attach(reading: Reading) -> None:
        """Bind each "my"/"your", and each "what"/"which", to the noun after it.

        The noun is the next word that is not a modifier, within a few
        words; failing that, the next word at all. A possessive or question
        word with nothing after it stays as it was. Only these words move:
        every other word keeps exactly the concept phase 1 gave it.

        Question words (routing audit P1) are what phase 1 lost by reading a
        sentence as a set: "what processor" asks *which processor*, and as
        {what, cpu} it was satisfied by "**what** processes are using the
        most **cpu**". Bound, it is ``what>cpu``, and that sentence carries
        ``what>process``. A question word passes over "kind of", "model" and
        possessives ("what is *your* name") to reach its noun.
        """
        words = reading.words
        keep: List[Word] = []
        attachments = set()
        for i, word in enumerate(words):
            owner = POSSESSIVE.get(word.surface)
            asking = word.concept == "what" and word.surface in QUESTION_WORDS
            if owner is None and not asking:
                keep.append(word)
                continue
            skip = MODIFIERS | (QUESTION_FILLER if asking else frozenset())
            following = [w for w in words[i + 1:i + 1 + _ATTACH_WINDOW]
                         if w.surface not in POSSESSIVE and w.surface not in QUESTION_WORDS]
            noun = next((w for w in following if w.surface not in skip),
                        following[0] if following else None)
            if noun is None:
                keep.append(word)
                continue
            if asking:
                # A compound asks about its last noun: "screen resolution",
                # "cpu usage". Followed only through machine and quantity
                # words, so a verb ("which wifi keeps dropping") never is.
                at = words.index(noun)
                while at + 1 < len(words):
                    nxt = words[at + 1]
                    if nxt.start != words[at].end + 1 or not (
                            nxt.concept in _COMPOUND_HEADS or nxt.concept in MACHINE):
                        break
                    at += 1
                noun = words[at]
            attachments.add(f"{owner or 'what'}>{noun.concept}")
        reading.words = keep
        reading.attachments = frozenset(attachments)

    def frame(self, text: str, reading: Optional[Reading] = None) -> "Frame":
        """What *text* asks for (see :class:`Frame`)."""
        return _frame(self, reading if reading is not None else self.read(text))

    def key(self, phrase: str) -> FrozenSet[str]:
        """A phrase as the set of concepts it requires, attachments included.

        No part-implies-whole here: a phrase requires exactly what it says.
        """
        reading = self.read(phrase)
        return frozenset(w.concept for w in reading.words) | reading.attachments


# --------------------------------------------------------------------------
# What a question asks for (routing audit P2)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    """What kind of information a sentence asks for, and about what.

    ``asks`` is one of: ``definition`` (what something *is*), ``comparison``,
    ``elsewhere`` (a quantity of something that is not this machine — "the
    temperature *of the sun*"), ``identity`` (which one this machine has),
    ``measurement`` (how this machine's one is doing), ``ranking`` (what is
    using it), or ``other``. ``machine`` is True when the sentence points at
    this computer: "my", "your", "this computer", "do you have", "right now".

    Read from a handful of function words and the attachments, not from a
    list of questions: "what is a cpu" and "what is an ssd" are the same
    frame, and neither had to be written down.
    """

    asks: str = "other"
    machine: bool = False
    why: str = ""

    @property
    def about_the_world(self) -> bool:
        """Asks what something is, or compares, or is about something else —
        and does not point at this machine. Word knowledge answers these."""
        return self.asks in ("definition", "comparison", "elsewhere") and not self.machine


_HERE = re.compile(
    r"\b(?:this|these) (?:computer|machine|pc|laptop|desktop|system|box|device|processor|"
    r"cpu|disk|drive|screen|battery)s?\b"
    r"|\b(?:do|does|did|have) (?:i|you|we|it|this \w+) (?:have|got|use|run|got)\b"
    r"|\b(?:i|you|we) (?:have|got)\b|\binstalled\b|\bright now\b|\bcurrently\b"
    r"|\bat the moment\b|\b(?:on|in) here\b|\byou\b|\byourself\b")
_DEFINE = re.compile(
    r"^(?:so |and |but )?what (?:is|are|s) (?:a|an) \w+"
    r"|\bwhat (?:does|do) (?:a |an |the )?[\w ]{1,30}? (?:mean|stand for)\b"
    r"|^(?:define|explain|describe)\b|\bwhat is meant by\b|\b(?:meaning|definition) of\b"
    r"|\bhow (?:does|do) (?:a |an |the )?[\w ]{1,30}? work\b|\bwhat is the (?:purpose|point) of\b")
_BARE = re.compile(r"^what (?:is|are|s) (?P<x>[a-z]+(?: [a-z]+)?)$")
_COMPARE = re.compile(
    r"\bdifferences? between\b|\b(?:vs|versus)\b|\bcompared? (?:to|with)\b|\bthe same as\b"
    r"|\b(?:faster|slower|better|worse|bigger|smaller|cheaper) than\b")
_OF_OWNER = re.compile(
    r"\b(?:temperature|temp|speed|size|weight|age|capacity|volume|load|name|height|length|"
    r"mass|population|usage) of (?:the |a |an )?(?P<o>[a-z]+)")
_MEASURE = re.compile(
    r"\bhow (?:much|many|hot|warm|full|busy|fast|slow|big|long|loud|bright|old)\b"
    r"|\b(?:usage|utilisation|utilization|used|free|left|remaining|level|percent|percentage|"
    r"speed|temperature|temp|load)\b")
_RANK = re.compile(r"\b(?:most|top|biggest|heaviest|hungriest|hogging|hogs?)\b")


def _frame(lexicon: "Lexicon", reading: Reading) -> Frame:
    norm = reading.text
    words = [w.concept for w in reading.words]
    attached = set(reading.attachments)
    machine = bool(_HERE.search(norm)) or any(
        a.startswith(("user>", "computer>")) for a in attached)

    if _COMPARE.search(norm):
        return Frame("comparison", machine, "compares")
    if _DEFINE.search(norm):
        return Frame("definition", machine, "asks what something is")
    bare = _BARE.match(norm)
    if bare:
        # "what is ram", "what is uptime": a bare noun, no article. Only a
        # word for something this machine has — "what is running" is not.
        concepts = [lexicon.concept(lexicon.base(t)) for t in bare.group("x").split()]
        if concepts and all(c in MACHINE for c in concepts):
            return Frame("definition", machine, "asks what something is")
    owner = _OF_OWNER.search(norm)
    if owner:
        whose = lexicon.concept(lexicon.base(owner.group("o")))
        if whose not in MACHINE and not machine and whose not in ("it", "this"):
            return Frame("elsewhere", machine, f"about the {owner.group('o')}")

    asked = {a.split(">", 1)[1] for a in attached if a.startswith("what>")}
    if (_RANK.search(norm) and "use" in words) or "use" in asked or \
            (asked & {"program", "process"} and "use" in words):
        return Frame("ranking", machine, "asks what is using it")
    if asked & DEVICES and not _MEASURE.search(norm):
        return Frame("identity", machine, "asks which one")
    if _MEASURE.search(norm) or (asked and not asked & DEVICES):
        return Frame("measurement", machine, "asks how it is doing")
    return Frame("other", machine, "")


#: "not", "never", "cannot", and every "n't" (which ``normalise`` leaves as a
#: separate "t": "don t", "isn t").
_SAYS_NOT = re.compile(r"(?:^|\s)(?:not|never|cannot|dont|\w+n t)(?:\s|$)")


def says_not(text: str) -> bool:
    """Whether *text* says not to do something.

    The one definition, used by the capability corpus and by the shell's
    command layers alike, so that "don't" means the same thing everywhere.
    """
    return _SAYS_NOT.search(normalise(text)) is not None


# --------------------------------------------------------------------------
# Is it an English word?  For the learned layer's typo matching only.
# --------------------------------------------------------------------------

_BLOB: Optional[bytes] = None
_IRREGULAR_BLOB: Optional[bytes] = None
_BLOB_LOCK = threading.Lock()


def _load_blob() -> Tuple[bytes, bytes]:
    global _BLOB, _IRREGULAR_BLOB
    with _BLOB_LOCK:
        if _BLOB is None:
            try:
                raw = gzip.decompress(ASSET.read_bytes())
            except OSError:
                raw = b"[words]\n[irregular]\n"
            words, _, irregular = raw.partition(b"[irregular]\n")
            _BLOB = b"\n" + words
            _IRREGULAR_BLOB = b"\n" + irregular
        return _BLOB, _IRREGULAR_BLOB or b""


def word_list_available() -> bool:
    return ASSET.is_file()


def is_english(word: str) -> Optional[bool]:
    """Whether *word* is an English word, an inflection of one, or a word
    this machine's tables know.

    Returns None when the word list is not installed: "cannot tell" is a
    different answer from "no", and the caller decides what to do with it.
    """
    word = normalise(word)
    if not word or " " in word or not word.isalpha():
        return False
    if word in AUXILIARY or word in IRREGULAR or word in STOP or word in NEGATION:
        return True
    if any(word in words for words in CONCEPTS.values()):
        return True
    if not word_list_available():
        return None
    words, irregular = _load_blob()
    probe = word.encode("ascii", "ignore")
    if b"\n" + probe + b"\n" in words or b"\n" + probe + b" " in irregular:
        return True
    return any(b"\n" + c.encode() + b"\n" in words for c in _candidates(word))
