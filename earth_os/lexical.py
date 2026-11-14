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

_PUNCT = re.compile(r"[^\w\s%]")
_WS = re.compile(r"\s+")

# --------------------------------------------------------------------------
# Tables.  Meaning, written down by a person; this is the part to grow.
# --------------------------------------------------------------------------

#: Concept -> the words that mean it, in this machine's world.  The concept's
#: own name is always one of its words.  Keep every group one meaning wide:
#: a word in two groups is a test failure (``test_lexical.py``).
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

    @property
    def concepts(self) -> FrozenSet[str]:
        return frozenset(w.concept for w in self.words) | self.implied

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


class Lexicon:
    """Reads sentences as concepts.

    *vocabulary* is every word the caller's phrases use.  A word is reduced
    to a base form only when that base form is a word the machine knows —
    a concept, a synonym, or a phrase word — so "running" meets "run" and
    "processes" meets "process" without an English dictionary in memory.
    A word the machine does not know is left exactly as said: it is going to
    be leftover either way.
    """

    def __init__(self, vocabulary: Iterable[str] = ()):
        self._synonym: Dict[str, str] = {}
        for concept, words in CONCEPTS.items():
            for word in words:
                self._synonym[word] = concept
        known = set(self._synonym) | set(CONCEPTS) | set(AUXILIARY.values())
        for words in MULTIWORD:
            known.update(words)
        for text in vocabulary:
            known.update(normalise(text).split())
        self._known = frozenset(known)
        self._cache: Dict[str, str] = {}

    # ------------------------------------------------------------------
    def base(self, word: str) -> str:
        """The base form of one word, as far as this machine needs it."""
        cached = self._cache.get(word)
        if cached is not None:
            return cached
        if word in AUXILIARY:
            result = AUXILIARY[word]
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
        longest = max((len(k) for k in MULTIWORD), default=1)
        i = 0
        while i < len(expanded):
            for size in range(longest, 1, -1):
                key = tuple(item[0] for item in expanded[i:i + size])
                if len(key) == size and key in MULTIWORD:
                    surface = norm[expanded[i][1]:expanded[i + size - 1][2]]
                    reading.words.append(Word(MULTIWORD[key], surface,
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

        present = {w.concept for w in reading.words}
        if present & PARTS:
            reading.implied = frozenset({"computer"}) - present
        return reading

    def key(self, phrase: str) -> FrozenSet[str]:
        """A phrase as the set of concepts it requires.

        No part-implies-whole here: a phrase requires exactly what it says.
        """
        return frozenset(w.concept for w in self.read(phrase).words)


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
