"""The learned semantic layer: facts, aliases and IF/THEN rules.

The shell already had two ways to understand a sentence, and a gap between
them.  ``Vocabulary`` matches a fixed table — fast, deterministic, and blind to
any wording nobody wrote down.  The model understands anything, costs a round
trip, and is asked to solve the same problem from scratch every single time.
So "reboot the computer" worked and "restart the computer" fell through to the
model, forever, no matter how many times it had already been explained.

This module is the layer between them.  When the model works out that
*restart* meant *reboot*, that relationship is written down, and the next
"restart the computer" resolves without a model at all.  Understanding
accumulates rather than being re-derived.

The design constraint is the one the rest of CDOS already keeps: **the model
proposes, it never disposes.**  A learning layer is the obvious place to lose
that property, because a mapping learned once is then trusted forever, and a
mapping learned *wrongly* once is trusted wrongly forever.  Three rules keep
the hole shut:

* Nothing is learned that CDOS itself has not validated.  An alias is only
  written down after ``cdos-bridge propose`` accepted the command it points at,
  so the learned table can never contain a command the fixed table would
  refuse.
* Learning does not confer authority.  A new alias starts *unconfirmed*, below
  the execution threshold, so its first few uses are proposals the user agrees
  to.  Confidence is earned by being right in front of a person, not asserted
  by the model that suggested it.
* An effectful command is never auto-executed, at any confidence.  Learning can
  make CDOS understand "wipe the drive" faster; it can never make it act on it
  unasked.  ``effectful`` survives on the alias and forces confirmation
  regardless of how well established the wording is.

Everything is a plain dict on disk (``semantics.json`` in the data directory),
readable and hand-editable, because a knowledge base you cannot inspect is one
you cannot correct.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

_PUNCT = re.compile(r"[^\w\s]+")
_WS = re.compile(r"\s+")

#: Words that carry no intent and only get in the way of matching.
FILLER = frozenset("""
    the a an this that my our please could would can will
    computer system machine now again just go
""".split())

SCHEMA_VERSION = 1

#: How far an established meaning must be shaken before a contradicting one
#: may take its place.  Above this, a competing claim only weakens the
#: incumbent; below it, the word is considered genuinely up for grabs.
CONTEST_FLOOR = 0.3


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, squeeze whitespace.

    Deliberately identical to ``commands.normalise`` so a term learned here
    and a phrase in ``vCommands.ini`` are the same string when they mean the
    same thing.
    """
    return _WS.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


def key_terms(text: str) -> List[str]:
    """The content words of an utterance, filler removed.

    "restart the computer" and "restart this machine now" both reduce to
    ``["restart"]``, which is what makes one learned alias cover a family of
    phrasings instead of just the exact sentence that taught it.
    """
    return [w for w in normalise(text).split() if w not in FILLER]


def content_key(text: str) -> str:
    """A canonical, order-independent signature for an utterance's content."""
    return " ".join(sorted(key_terms(text)))


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@dataclass
class Fact:
    """A term that means something in a domain.

    ``reboot`` is a system operation.  Facts are the anchors aliases point at;
    they are not themselves executable.
    """

    term: str
    domain: str = "system"
    description: str = ""
    kind: str = "action"                # action | command | concept
    target: str = ""                    # vocabulary action, or "READ MEMORY"
    source: str = "builtin"             # builtin | user | llm
    created: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class Alias:
    """A learned relationship: *term* may mean *canonical*, in *domain*.

    Confidence is the whole safety story.  It starts low, rises only when a
    person agrees with the reading, and falls hard when they do not.  The
    execution threshold is read from config, so a cautious install can insist
    on confirming everything while a trusting one lets established wordings
    run straight through.
    """

    term: str                           # "restart"
    canonical: str                      # "reboot"
    domain: str = "system"
    kind: str = "action"                # action | command
    target: str = ""                    # what actually runs
    confidence: float = 0.55
    source: str = "llm"                 # llm | user | builtin
    confirmed: bool = False
    effectful: bool = False             # changes the machine -> always ask
    uses: int = 0
    hits: int = 0                       # times it resolved an utterance
    rejections: int = 0
    examples: List[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    last_used: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)

    def reinforce(self, amount: float = 0.35) -> None:
        """Move confidence toward certainty without ever reaching it.

        Asymptotic on purpose: no amount of agreement should make a learned
        guess indistinguishable from something a human wrote in the fixed
        table, because the fixed table is auditable and this is not.
        """
        self.confidence = min(0.99, self.confidence + (1.0 - self.confidence) * amount)
        self.uses += 1
        self.confirmed = True
        self.last_used = time.time()

    def weaken(self, factor: float = 0.45) -> None:
        self.confidence *= factor
        self.rejections += 1
        self.last_used = time.time()


@dataclass
class Rule:
    """An IF/THEN relationship, for things an alias cannot express.

    An alias is a word swap.  A rule is conditional: *if* the utterance
    mentions these terms *and* the domain is this, *then* read it as that.
    It is what lets "start the computer over" reach SYSTEM_REBOOT without
    pretending "over" is a synonym for anything.
    """

    name: str
    when_terms: List[str] = field(default_factory=list)   # all must be present
    when_absent: List[str] = field(default_factory=list)  # none may be present
    domain: str = "system"
    then_canonical: str = ""
    kind: str = "action"
    target: str = ""
    confidence: float = 0.6
    source: str = "llm"
    effectful: bool = False
    uses: int = 0
    created: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return asdict(self)

    def matches(self, terms: Iterable[str]) -> bool:
        present = set(terms)
        if not self.when_terms:
            return False
        if not present.issuperset(self.when_terms):
            return False
        return not present.intersection(self.when_absent)


@dataclass
class Resolution:
    """What the semantic layer concluded, and how sure it is."""

    canonical: str
    target: str
    kind: str = "action"
    confidence: float = 0.0
    via: str = "none"                   # alias | rule | fuzzy | vocabulary
    term: str = ""
    effectful: bool = False
    explanation: str = ""

    @property
    def found(self) -> bool:
        return bool(self.canonical or self.target)

    def should_execute(self, threshold: float) -> bool:
        """Confidence high enough to act without asking.

        An effectful command answers no to this question at any confidence.
        That is the single most important line in this module.
        """
        if self.effectful:
            return False
        return self.found and self.confidence >= threshold


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------

class SemanticStore:
    """Facts, aliases and rules, persisted as one JSON document.

    Writes are atomic (temp file plus rename) and guarded by a lock, because
    the voice worker learns on a background thread while the UI thread may be
    reading the same table to draw the engineering screen.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_path()
        self.facts: Dict[str, Fact] = {}
        self.aliases: Dict[str, Alias] = {}
        self.rules: Dict[str, Rule] = {}
        self._lock = threading.RLock()
        self.dirty = False
        self.last_error = ""

    # -- persistence -------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "SemanticStore":
        store = cls(path)
        store.reload()
        return store

    def reload(self) -> None:
        with self._lock:
            self.facts.clear()
            self.aliases.clear()
            self.rules.clear()
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                return                              # absent or corrupt: start clean
            for item in raw.get("facts", []):
                try:
                    fact = Fact(**item)
                except TypeError:
                    continue
                self.facts[normalise(fact.term)] = fact
            for item in raw.get("aliases", []):
                try:
                    alias = Alias(**item)
                except TypeError:
                    continue
                self.aliases[normalise(alias.term)] = alias
            for item in raw.get("rules", []):
                try:
                    rule = Rule(**item)
                except TypeError:
                    continue
                self.rules[rule.name] = rule

    def save(self) -> bool:
        with self._lock:
            payload = {
                "schema": SCHEMA_VERSION,
                "saved": time.time(),
                "facts": [f.to_dict() for f in self.facts.values()],
                "aliases": [a.to_dict() for a in self.aliases.values()],
                "rules": [r.to_dict() for r in self.rules.values()],
            }
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(payload, indent=2, sort_keys=False),
                               encoding="utf-8")
                os.replace(tmp, self.path)
                self.dirty = False
                return True
            except OSError as exc:
                self.last_error = str(exc)
                return False

    # -- resolution --------------------------------------------------------
    def resolve(self, utterance: str, domain: str = "",
                fuzzy_cutoff: float = 0.8) -> Resolution:
        """Interpret *utterance* using what has been learned so far.

        Tried in descending order of trustworthiness: an exact learned alias,
        then a conditional rule, then a fuzzy match against known terms with
        confidence scaled by how close the match actually was.
        """
        terms = key_terms(utterance)
        if not terms:
            return Resolution("", "", confidence=0.0, via="none")

        with self._lock:
            # 1. exact alias on any content word
            for term in terms:
                alias = self.aliases.get(term)
                if alias and (not domain or alias.domain == domain):
                    return Resolution(
                        canonical=alias.canonical, target=alias.target,
                        kind=alias.kind, confidence=alias.confidence,
                        via="alias", term=alias.term,
                        effectful=alias.effectful,
                        explanation=f"{alias.term} → {alias.canonical}",
                    )

            # 2. conditional rules
            for rule in self.rules.values():
                if domain and rule.domain != domain:
                    continue
                if rule.matches(terms):
                    return Resolution(
                        canonical=rule.then_canonical, target=rule.target,
                        kind=rule.kind, confidence=rule.confidence,
                        via="rule", term=rule.name, effectful=rule.effectful,
                        explanation=f"rule {rule.name}: "
                                    f"{'+'.join(rule.when_terms)} → "
                                    f"{rule.then_canonical}",
                    )

            # 3. fuzzy against learned terms, confidence scaled by closeness.
            #
            # Fuzzy matching is for typos: "rebot" is "reboot" mistyped. It
            # is not for a real word that happens to be spelled like a
            # learned one — "getting" is not "settings" mistyped, and reading
            # it that way had "you're getting tired" proposing to open the
            # parameters screen. So a word the dictionary knows is never
            # fuzzed. If the word list is not installed the answer is "cannot
            # tell", and the old behaviour stands rather than none at all.
            from .lexical import is_english
            known = list(self.aliases)
            for term in terms:
                if is_english(term):
                    continue
                close = difflib.get_close_matches(term, known, n=1,
                                                  cutoff=fuzzy_cutoff)
                if not close:
                    continue
                alias = self.aliases[close[0]]
                ratio = difflib.SequenceMatcher(None, term, close[0]).ratio()
                return Resolution(
                    canonical=alias.canonical, target=alias.target,
                    kind=alias.kind, confidence=alias.confidence * ratio,
                    via="fuzzy", term=alias.term, effectful=alias.effectful,
                    explanation=f"{term} ≈ {alias.term} → {alias.canonical}",
                )

        return Resolution("", "", confidence=0.0, via="none")

    # -- learning ----------------------------------------------------------
    def learn_alias(self, term: str, canonical: str, target: str = "",
                    domain: str = "system", kind: str = "action",
                    source: str = "llm", effectful: bool = False,
                    example: str = "", seed: float = 0.55) -> Optional[Alias]:
        """Write down that *term* may mean *canonical*.

        Called only after the proposed command has already survived CDOS's own
        validator, so this records a relationship rather than granting one.
        Re-learning an existing alias reinforces it instead of duplicating it.
        """
        term = normalise(term)
        canonical = normalise(canonical)
        if not term or not canonical or term == canonical:
            return None
        if term in FILLER:
            return None                             # never alias a filler word

        with self._lock:
            existing = self.aliases.get(term)
            if existing is not None:
                if existing.canonical == canonical:
                    existing.hits += 1
                    if example and example not in existing.examples:
                        existing.examples.append(example)
                        del existing.examples[:-8]
                    self.dirty = True
                    return existing
                # Contradiction: the same word now claims a different meaning.
                # Weaken rather than overwrite — the old reading earned its
                # confidence in front of a person, and this one has not yet.
                # Only once the old meaning has been shaken down to nearly
                # nothing does the new one get to take the slot, so a model
                # having one bad night cannot rewrite an established word.
                existing.weaken()
                if existing.confidence >= CONTEST_FLOOR:
                    self.dirty = True
                    return existing

            alias = Alias(
                term=term, canonical=canonical, target=target or canonical,
                domain=domain, kind=kind, source=source,
                effectful=effectful, confidence=seed,
                examples=[example] if example else [],
            )
            self.aliases[term] = alias
            self.dirty = True
            return alias

    def learn_rule(self, name: str, when_terms: List[str], then_canonical: str,
                   target: str = "", domain: str = "system",
                   kind: str = "action", when_absent: Optional[List[str]] = None,
                   source: str = "llm", effectful: bool = False,
                   confidence: float = 0.6) -> Optional[Rule]:
        when_terms = [normalise(t) for t in when_terms if normalise(t)]
        if not name or not when_terms or not then_canonical:
            return None
        with self._lock:
            rule = Rule(
                name=name, when_terms=when_terms,
                when_absent=[normalise(t) for t in (when_absent or [])],
                domain=domain, then_canonical=normalise(then_canonical),
                kind=kind, target=target or then_canonical,
                confidence=confidence, source=source, effectful=effectful,
            )
            self.rules[name] = rule
            self.dirty = True
            return rule

    def add_fact(self, term: str, target: str = "", domain: str = "system",
                 description: str = "", kind: str = "action",
                 source: str = "builtin") -> Fact:
        with self._lock:
            fact = Fact(term=normalise(term), domain=domain,
                        description=description, kind=kind,
                        target=target or normalise(term), source=source)
            self.facts[fact.term] = fact
            self.dirty = True
            return fact

    # -- feedback ----------------------------------------------------------
    def confirm(self, term: str) -> Optional[Alias]:
        """The user agreed with this reading: it earns confidence."""
        with self._lock:
            alias = self.aliases.get(normalise(term))
            if alias is None:
                return None
            alias.reinforce()
            alias.hits += 1
            self.dirty = True
            return alias

    def reject(self, term: str, forget_below: float = 0.2) -> bool:
        """The user rejected this reading. Weaken it, and drop it if it dies."""
        with self._lock:
            key = normalise(term)
            alias = self.aliases.get(key)
            if alias is None:
                return False
            alias.weaken()
            if alias.confidence < forget_below:
                del self.aliases[key]
            self.dirty = True
            return True

    def forget(self, term: str) -> bool:
        with self._lock:
            key = normalise(term)
            gone = self.aliases.pop(key, None) is not None
            gone = self.rules.pop(term, None) is not None or gone
            if gone:
                self.dirty = True
            return gone

    def touch(self, term: str) -> None:
        with self._lock:
            alias = self.aliases.get(normalise(term))
            if alias is not None:
                alias.hits += 1
                alias.last_used = time.time()
                self.dirty = True

    # -- inspection --------------------------------------------------------
    def stats(self) -> Dict[str, int]:
        with self._lock:
            confirmed = sum(1 for a in self.aliases.values() if a.confirmed)
            return {
                "facts": len(self.facts),
                "aliases": len(self.aliases),
                "confirmed": confirmed,
                "provisional": len(self.aliases) - confirmed,
                "rules": len(self.rules),
            }

    def table(self) -> List[Tuple[str, str, float, bool, int]]:
        """Learned aliases as rows, strongest first, for display."""
        with self._lock:
            rows = [(a.term, a.canonical, a.confidence, a.confirmed, a.hits)
                    for a in self.aliases.values()]
        rows.sort(key=lambda r: (-r[2], r[0]))
        return rows

    def describe(self) -> str:
        counts = self.stats()
        return (f"{counts['aliases']} aliases "
                f"({counts['confirmed']} confirmed, "
                f"{counts['provisional']} provisional), "
                f"{counts['rules']} rules, {counts['facts']} facts")


def default_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "earth-os" / "semantics.json"


# --------------------------------------------------------------------------
# Seed knowledge
# --------------------------------------------------------------------------

#: Relationships worth knowing before the model has taught anything, chosen
#: because they are the ones a fixed table most obviously misses.  Every one
#: of these is a word-level synonym, never a command: the target still has to
#: exist in the vocabulary for it to resolve to anything.
SEED_ALIASES: List[Tuple[str, str, str, bool]] = [
    # term          canonical     domain     effectful
    ("restart",     "reboot",     "system",  True),
    ("reset",       "reboot",     "system",  True),
    ("powerdown",   "shutdown",   "system",  True),
    ("poweroff",    "shutdown",   "system",  True),
    ("halt",        "shutdown",   "system",  True),
    ("ram",         "memory",     "system",  False),
    ("processor",   "cpu",        "system",  False),
    ("disk",        "storage",    "system",  False),
    ("drive",       "storage",    "system",  False),
    ("temperature", "thermal",    "system",  False),
    ("temp",        "thermal",    "system",  False),
    ("heat",        "thermal",    "system",  False),
    ("wifi",        "network",    "system",  False),
    ("internet",    "network",    "system",  False),
    ("connection",  "network",    "system",  False),
    ("apps",        "programs",   "shell",   False),
    ("applications", "programs",  "shell",   False),
    ("windows",     "programs",   "shell",   False),
    ("shell",       "terminal",   "shell",   False),
    ("prompt",      "terminal",   "shell",   False),
    ("settings",    "parameters", "shell",   False),
    ("preferences", "parameters", "shell",   False),
    ("config",      "parameters", "shell",   False),
]

SEED_RULES: List[Dict] = [
    {
        "name": "start-over-is-reboot",
        "when_terms": ["start", "over"],
        "then_canonical": "reboot",
        "domain": "system",
        "effectful": True,
        "confidence": 0.7,
    },
    {
        "name": "how-much-memory",
        "when_terms": ["how", "much", "memory"],
        "then_canonical": "memory",
        "domain": "system",
        "confidence": 0.75,
    },
    {
        "name": "running-hot",
        "when_terms": ["running", "hot"],
        "then_canonical": "thermal",
        "domain": "system",
        "confidence": 0.7,
    },
]


def seed(store: SemanticStore, source: str = "builtin") -> int:
    """Install the starter relationships into an empty store.

    Seeded aliases are marked ``builtin`` and start confirmed, because they
    were written by a person — the same standing as ``vCommands.ini``, not the
    standing of something a model guessed at three in the morning.
    """
    added = 0
    for term, canonical, domain, effectful in SEED_ALIASES:
        if normalise(term) in store.aliases:
            continue
        alias = store.learn_alias(term, canonical, domain=domain,
                                  source=source, effectful=effectful,
                                  seed=0.8)
        if alias is not None:
            alias.confirmed = True
            added += 1
    for spec in SEED_RULES:
        if spec["name"] in store.rules:
            continue
        if store.learn_rule(source=source, **spec) is not None:
            added += 1
    return added
