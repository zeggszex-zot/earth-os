"""Voice command vocabulary and dispatch.

Reads the original ``vCommands.ini`` (spoken phrase to canonical action, with
``//`` comments) and ``commands.xml`` (the SAPI grammar, which is where the
wake word lives).  Keeping both formats means an existing LCARS x32 command
file can be dropped in unchanged, and the vocabulary can be edited without
touching Python.
"""

from __future__ import annotations

import difflib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ASSETS = Path(__file__).parent / "assets"

_PUNCT = re.compile(r"[^\w\s]+")
_WS = re.compile(r"\s+")


def normalise(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


@dataclass
class Vocabulary:
    """Spoken phrases mapped onto canonical action names."""

    phrases: Dict[str, str] = field(default_factory=dict)   # spoken -> action
    descriptions: Dict[str, str] = field(default_factory=dict)
    wake_words: List[str] = field(default_factory=lambda: ["computer"])

    @classmethod
    def load(cls, ini: Optional[Path] = None, grammar: Optional[Path] = None) -> "Vocabulary":
        vocab = cls()
        vocab.load_ini(ini or ASSETS / "vCommands.ini")
        vocab.load_grammar(grammar or ASSETS / "commands.xml")
        return vocab

    def load_ini(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            body, _, comment = line.partition("//")
            if "=" not in body:
                continue
            spoken, _, action = body.partition("=")
            spoken = normalise(spoken)
            action = normalise(action)
            if not spoken or not action:
                continue
            self.phrases[spoken] = action
            if comment.strip():
                self.descriptions.setdefault(action, comment.strip())

    def load_grammar(self, path: Path) -> None:
        try:
            root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ET.ParseError):
            return
        for rule in root.iter("RULE"):
            phrases = [normalise(p.text or "") for p in rule.iter()
                       if p.tag.upper() == "P" and p.text]
            if (rule.get("NAME") or "").lower() == "init":
                if phrases:
                    self.wake_words = phrases
                continue
            for phrase in phrases:
                self.phrases.setdefault(phrase, phrase)

    # -- matching ----------------------------------------------------------
    def strip_wake(self, text: str) -> Tuple[bool, str]:
        """Remove a leading wake word; report whether one was present."""
        norm = normalise(text)
        for wake in self.wake_words:
            if norm == wake:
                return (True, "")
            if norm.startswith(wake + " "):
                return (True, norm[len(wake) + 1:].strip())
        return (False, norm)

    def match_span(self, text: str) -> int:
        """How many characters of *text* the matched phrase accounted for.

        :meth:`match` throws this away, and it is what decides precedence
        between two layers that both recognise an utterance.  Matching is by
        containment, so the vocabulary's bare ``home`` fires inside *"where is
        my home"* — a four-character reading of a sixteen-character request.
        Whoever recognised more of the sentence understood more of it, and
        that is the layer that should win.

        Returns 0 when nothing matched, and the full length on an exact hit.
        """
        norm = normalise(text)
        if not norm:
            return 0
        if norm in self.phrases:
            return len(norm)
        longest = 0
        for spoken in self.phrases:
            if (norm.startswith(spoken + " ") or norm.endswith(" " + spoken)
                    or f" {spoken} " in f" {norm} "):
                longest = max(longest, len(spoken))
        return longest

    def match(self, text: str, cutoff: float = 0.82) -> Optional[str]:
        """Resolve an utterance to a canonical action.

        Exact match first, then longest-prefix, then a fuzzy pass so that a
        speech engine's near-misses ("shut down the computer") still land.
        """
        norm = normalise(text)
        if not norm:
            return None
        if norm in self.phrases:
            return self.phrases[norm]
        best: Optional[str] = None
        best_len = 0
        for spoken, action in self.phrases.items():
            if (norm.startswith(spoken + " ") or norm.endswith(" " + spoken)
                    or f" {spoken} " in f" {norm} "):
                if len(spoken) > best_len:
                    best, best_len = action, len(spoken)
        if best:
            return best
        close = difflib.get_close_matches(norm, list(self.phrases), n=1, cutoff=cutoff)
        return self.phrases[close[0]] if close else None

    def actions(self) -> List[str]:
        return sorted(set(self.phrases.values()))

    def spoken_for(self, action: str) -> List[str]:
        return sorted(s for s, a in self.phrases.items() if a == action)


def apply_alias(utterance: str, term: str, canonical: str) -> str:
    """Rewrite *utterance* with one learned substitution applied.

    Rewriting and re-matching, rather than mapping an alias straight onto an
    action, is what makes one learned relationship cover a whole family of
    sentences: once *restart* is known to mean *reboot*, every phrasing the
    fixed table already knows for "reboot" works for "restart" too, with
    nothing further learned.
    """
    words = normalise(utterance).split()
    term_words = normalise(term).split()
    if not term_words:
        return normalise(utterance)
    span = len(term_words)
    out: List[str] = []
    i = 0
    while i < len(words):
        if words[i:i + span] == term_words:
            out.extend(normalise(canonical).split())
            i += span
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


class Dispatcher:
    """Maps canonical actions to shell callables.

    Resolution runs in two tiers.  The fixed vocabulary is consulted first and
    is always trusted: somebody wrote those phrases down.  Only when it finds
    nothing does the learned semantic layer get a turn, and what it returns
    carries a confidence the caller is expected to weigh rather than a verdict
    it is expected to obey.
    """

    def __init__(self, vocabulary: Optional[Vocabulary] = None,
                 semantics=None):
        self.vocabulary = vocabulary or Vocabulary.load()
        self.handlers: Dict[str, Callable[[], None]] = {}
        #: Optional :class:`earth_os.semantics.SemanticStore`.
        self.semantics = semantics

    def register(self, action: str, handler: Callable[[], None]) -> None:
        self.handlers[normalise(action)] = handler

    def register_many(self, mapping: Dict[str, Callable[[], None]]) -> None:
        for action, handler in mapping.items():
            self.register(action, handler)

    def resolve(self, utterance: str) -> Optional[str]:
        """The action for *utterance*, fixed table only — unchanged behaviour."""
        return self.vocabulary.match(utterance)

    def resolve_learned(self, utterance: str):
        """Resolve through the fixed table, then the learned layer.

        Returns ``(action, resolution)``.  *resolution* is None when the fixed
        table answered, because that path has no confidence to weigh.  When
        the learned layer answered, *action* may still be None if the alias
        pointed somewhere no handler exists.
        """
        action = self.vocabulary.match(utterance)
        if action:
            return (action, None)
        if self.semantics is None:
            return (None, None)

        resolution = self.semantics.resolve(utterance)
        if not resolution.found:
            return (None, resolution)

        # Substitute the learned term and let the fixed table try again.
        rewritten = apply_alias(utterance, resolution.term, resolution.canonical)
        action = self.vocabulary.match(rewritten)
        if not action and self.known(resolution.canonical):
            action = normalise(resolution.canonical)
        if not action and resolution.target and self.known(resolution.target):
            action = normalise(resolution.target)
        if action:
            resolution.target = action
        return (action, resolution)

    def dispatch(self, utterance: str) -> Optional[str]:
        """Run the handler for *utterance*; return the action that fired."""
        action = self.resolve(utterance)
        if action and action in self.handlers:
            self.handlers[action]()
            return action
        return None

    def known(self, action: str) -> bool:
        return normalise(action) in self.handlers

    def unhandled(self) -> List[str]:
        """Actions in the vocabulary with no handler wired up."""
        return [a for a in self.vocabulary.actions() if a not in self.handlers]
