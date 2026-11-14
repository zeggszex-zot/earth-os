"""ESI phase 2: the CDOS Dictionary Bridge.

Phase 1's concept table is 86 words written by hand.  The CDOS corpus holds
Open English WordNet — about 150,000 words with their senses, synonyms,
kinds and definitions.  This module lets the second grow the first, without
letting it decide anything:

    propose   read the corpus (through CDOS's own reader), find the senses
              where each concept lives, and collect the words WordNet gives
              for those senses, each with the evidence it came from;
    check     try every candidate against everything phase 1 guarantees,
              plus WordNet's own example sentences for the word's *other*
              meanings, which a wrongly added word would start answering;
    approve   a named person accepts a candidate that passed; the word is
              written to ``assets/lexicon/approved.json``, which
              ``lexical.py`` reads and re-checks on every load;
    reject    a named person refuses one, with a reason, and it is never
              proposed again.

**CDOS supplies knowledge; ESI interprets language; a person approves.**
Nothing here runs at start-up and Earth OS never needs the corpus at run
time: the bridge is a tool for growing the tables, not a dependency of them.
Nothing is approved by the bridge itself, at any confidence — confidence
only orders the list a person reads.

Finding where a concept lives
-----------------------------
A word has many senses; "ram" is a sheep, a battering ram, a verb and
computer memory.  A sense is taken as the concept's (an *anchor*) only on
evidence, and the evidence is local — a sense's own tags and its own
immediate parent.  Walking further up the hierarchy was measured and
drifts: "screen > display > show > entertainment", "computer > machine >
motor vehicle".

* For a concept about the machine (memory, disk, computer, …) a sense of one
  of its words is an anchor if WordNet files it under computer science, its
  direct parent is a computing kind (``COMPUTING_KINDS``), or its definition
  says "computer".  Agreement between two of ESI's own words is *not*
  enough here: ESI's "net" and "network" agree on the fishing-net sense.
* For any other concept (slow, use, load, …) a sense is an anchor if two of
  the concept's own words share it — "slow" and "sluggish" do.  Only
  synonyms and similar words come from these; never "kinds of", because the
  kinds of using are abuse and misuse.

A "kind of" candidate (a hyponym: "server" is a kind of computer) must
carry computing evidence of its own: being a kind of monitor does not make a
cardiac monitor part of this computer.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .lexical import (
    APPROVED, CONCEPTS, MACHINE, Approved, load_approved, normalise,
    usable_addition,
)

ROOT = Path(__file__).resolve().parent.parent

#: WordNet topics that place a sense in computing.
COMPUTING_TOPICS = frozenset({"computer science"})

#: Direct parents that place a sense in computing.  Checked one level up
#: only; see the module docstring for why not further.
COMPUTING_KINDS = frozenset({
    "computer", "digital computer", "personal computer", "portable computer",
    "computer memory", "memory device", "volatile storage",
    "non-volatile storage", "storage device", "disk drive", "magnetic disk",
    "central processing unit", "computer hardware", "hardware",
    "electronic equipment", "electronic device", "peripheral",
    "computer screen", "display", "monitor",
    "local area network", "computer network", "network",
    "software", "computer software", "computer program", "program",
    "application", "operating system", "data processor",
})

#: What a "kind of" candidate's own definition must mention.
COMPUTING_WORDS = re.compile(r"\b(?:computer|computers|computing|software|data)\b")

#: Concepts the bridge does not grow: pronouns are grammar, not vocabulary.
NOT_GROWN = frozenset({"user"})

#: How much each kind of evidence says, for ordering only.
RELATION_WEIGHT = {"synonym": 0.8, "similar": 0.6, "kind": 0.4}

_QUOTED = re.compile(r'"([^"]{6,})"')


# --------------------------------------------------------------------------
# Reading the corpus
# --------------------------------------------------------------------------

def cdos_source() -> Path:
    """Where CDOS Computer's code is (it carries the corpus reader)."""
    from .voice.computer_link import find_source
    found = find_source()
    if found is None:
        raise RuntimeError("CDOS Computer's source was not found")
    return found


def open_corpus(data: Path):
    """CDOS's own ``Corpus`` over a built data/ folder (tools/build_shards.py)."""
    source = str(cdos_source())
    if source not in sys.path:
        sys.path.insert(0, source)
    from computer.corpus import Corpus                     # type: ignore
    corpus = Corpus(str(data)).load()
    if not corpus.ready:
        raise RuntimeError(f"no usable CDOS corpus at {data}: {corpus.status()}")
    return corpus


@dataclass
class Sense:
    """One WordNet sense of one word, as the corpus holds it."""

    word: str
    sense: str
    definition: str = ""
    synonyms: List[str] = field(default_factory=list)
    kinds: List[str] = field(default_factory=list)        # hyponyms
    parents: List[str] = field(default_factory=list)      # hypernyms
    similar: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)

    @property
    def examples(self) -> List[str]:
        return _QUOTED.findall(self.definition)


class Dictionary:
    """The corpus's word senses, read through CDOS's reader."""

    def __init__(self, corpus):
        self.corpus = corpus
        self._cache: Dict[str, List[Sense]] = {}

    def senses(self, word: str) -> List[Sense]:
        word = word.lower()
        if word in self._cache:
            return self._cache[word]
        source = str(cdos_source())
        if source not in sys.path:
            sys.path.insert(0, source)
        from computer.corpus import key_of                  # type: ignore
        from computer.lexicon import sense_of               # type: ignore
        by: Dict[str, Sense] = {}
        for fact in self.corpus.facts_for(key_of(word)):
            sid = sense_of(fact)
            if not sid:
                continue
            sense = by.setdefault(sid, Sense(word, sid))
            value = str(fact.value)
            if fact.relation == "DEFINITION":
                sense.definition = value
            elif fact.relation == "SYNONYM":
                sense.synonyms.append(value.lower())
            elif fact.relation == "HYPONYM":
                sense.kinds.append(value.lower())
            elif fact.relation == "HYPERNYM":
                sense.parents.append(value.lower())
            elif fact.relation == "WN_SIMILAR_TO":
                sense.similar.append(value.lower())
            elif fact.relation == "WN_TOPIC_DOMAIN":
                sense.topics.append(value.lower())
        found = list(by.values())
        if len(self._cache) < 20000:
            self._cache[word] = found
        return found


def computing_evidence(sense: Sense, kinds: bool = True) -> str:
    """Why *sense* is about computing, or "".  *kinds*: accept a computing
    parent as evidence (anchors may; a candidate's own evidence may not)."""
    topics = sorted(set(sense.topics) & COMPUTING_TOPICS)
    if topics:
        return f"filed under {topics[0]}"
    if kinds:
        parents = [p for p in sense.parents if p in COMPUTING_KINDS]
        if parents:
            return f"a kind of {parents[0]}"
        if re.search(r"\bcomputer\b", sense.definition.lower()):
            return "its definition says computer"
    elif COMPUTING_WORDS.search(sense.definition.lower()):
        return "its definition is about computing"
    return ""


# --------------------------------------------------------------------------
# Proposing
# --------------------------------------------------------------------------

@dataclass
class Anchor:
    concept: str
    word: str
    sense: str
    definition: str
    why: str


@dataclass
class Candidate:
    word: str                     # normalised, as it would be stored
    concept: str
    relation: str                 # synonym | similar | kind
    said: str                     # as WordNet spells it
    anchor: str                   # the ESI word whose sense it came from
    sense: str
    definition: str
    why: str                      # why that sense is the concept's
    own_evidence: str = ""        # for a kind: why it is about computing itself
    senses_total: int = 0
    senses_in_scope: int = 0
    examples: List[str] = field(default_factory=list)
    checks: Dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    status: str = "unchecked"     # passed | blocked

    @property
    def key(self) -> Tuple[str, str]:
        return (self.word, self.concept)


def _names_the_concept(sense: Sense, concept: str) -> str:
    """The concept's name, if this sense mentions it.

    Being about computing is not being about *this* concept: WordNet's
    computing sense of "desktop" is the screen background, and its sense of
    "monitor" that is electronic equipment is a monitoring device. A sense
    of any word but the concept's name must say the name — "a portable
    computer", "computer memory", "written programs". Any of the concept's
    words was tried first and was too easy: the computer concept includes
    "you", "it" and "system", which half of all definitions contain.
    """
    # "(computer science) the area of the screen…" — a definition's leading
    # domain label is a topic, not a mention, so it does not count.
    definition = re.sub(r"^\s*\([^)]*\)\s*", "", sense.definition.lower())
    text = " ".join([definition] + sense.synonyms + sense.parents)
    if re.search(rf"\b{re.escape(concept)}(?:s|es|me|mes)?\b", text):
        return concept
    return ""


def anchors(dictionary: Dictionary, concept: str) -> List[Anchor]:
    """The senses where *concept* lives, with why.

    Two conditions, both required. The sense is in the right world: about
    computing for a concept about the machine, or shared by two of the
    concept's words for any other. And it is about *this* concept: the
    concept's own name word, or a sense that names it — and for a concept
    that is not about the machine, a sense the name word itself belongs to.
    ("Consume" and "devour" share the sense of eating greedily, whose words
    include "demolish"; "use" is not among them, so it is not an anchor.)
    """
    own = set(CONCEPTS[concept])
    found: List[Anchor] = []
    seen = set()
    for word in CONCEPTS[concept]:
        for sense in dictionary.senses(word):
            if sense.sense in seen:
                continue
            members = set(sense.synonyms) | {word}
            if concept in MACHINE:
                why = computing_evidence(sense)
                if why and word != concept:
                    named = _names_the_concept(sense, concept)
                    why = f"{why}, and names {named}" if named else ""
            else:
                agree = sorted((members & own) - {word})
                why = (f"shared with {', '.join(agree)}"
                       if agree and concept in members else "")
            if why:
                seen.add(sense.sense)
                found.append(Anchor(concept, word, sense.sense, sense.definition, why))
    return found


def propose(dictionary: Dictionary, excluded: Iterable[Tuple[str, str]] = ()) -> Tuple[
        List[Candidate], Dict[str, List[Anchor]]]:
    """Every candidate the corpus offers, unchecked, and the anchors used."""
    excluded = set(excluded)
    by_key: Dict[Tuple[str, str], Candidate] = {}
    used: Dict[str, List[Anchor]] = {}
    rank = {"synonym": 0, "similar": 1, "kind": 2}
    for concept in CONCEPTS:
        if concept in NOT_GROWN:
            continue
        found = anchors(dictionary, concept)
        used[concept] = found
        for anchor in found:
            sense = next(s for s in dictionary.senses(anchor.word) if s.sense == anchor.sense)
            offers = [("synonym", w) for w in sense.synonyms]
            offers += [("similar", w) for w in sense.similar]
            if concept in MACHINE:
                offers += [("kind", w) for w in sense.kinds]
            for relation, said in offers:
                word = normalise(said)
                if not word or (word, concept) in excluded:
                    continue
                if word in CONCEPTS[concept]:
                    continue
                key = (word, concept)
                old = by_key.get(key)
                if old is not None and rank[old.relation] <= rank[relation]:
                    continue
                by_key[key] = Candidate(word=word, concept=concept, relation=relation,
                                        said=said, anchor=anchor.word, sense=anchor.sense,
                                        definition=anchor.definition, why=anchor.why)
    candidates = list(by_key.values())
    in_scope_senses = {a.sense for found in used.values() for a in found}
    for cand in candidates:
        senses = dictionary.senses(cand.word)
        cand.senses_total = len(senses)
        cand.senses_in_scope = sum(1 for s in senses
                                   if s.sense in in_scope_senses or computing_evidence(s, kinds=False))
        if cand.relation == "kind":
            own = [computing_evidence(s, kinds=False) for s in senses]
            cand.own_evidence = next((e for e in own if e), "")
        # How the word is used when it means something else: what it must not
        # start answering. Written by WordNet's lexicographers, not by us.
        examples: List[str] = []
        for s in senses:
            if s.sense in in_scope_senses or s.sense == cand.sense:
                continue
            examples.extend(s.examples)
        cand.examples = examples[:12]
    return candidates, used


# --------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------

def _label(intent) -> Optional[str]:
    return None if intent is None else (intent.analysis or intent.capability)


class Checker:
    """Everything phase 1 guarantees, asked again with one word added."""

    SETS = ("esi_dev.jsonl", "esi_heldout.jsonl")

    def __init__(self, approved: Optional[Approved] = None, tests: Optional[Path] = None):
        from .capability_corpus import (
            ADJUSTMENTS, ANALYSIS_PHRASES, PHRASEBOOK, CapabilityCorpus,
        )
        self._Corpus = CapabilityCorpus
        self.approved = load_approved() if approved is None else approved
        tests = tests or ROOT / "tests"
        self.phrases = [p for ps in PHRASEBOOK.values() for p in ps]
        self.phrases += [p for ps in ANALYSIS_PHRASES.values() for p in ps]
        self.phrases += list(ADJUSTMENTS)
        self.sets: Dict[str, List[Tuple[str, str]]] = {}
        for name in self.SETS:
            path = tests / name
            if path.is_file():
                rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
                self.sets[name] = [(r["text"], r["expect"]) for r in rows]
        self.base = CapabilityCorpus(approved=self.approved)
        self.base_phrases = [_label(self.base.resolve(p)) for p in self.phrases]
        self.base_scores = {n: self._score(self.base, rows) for n, rows in self.sets.items()}

    @staticmethod
    def _score(corpus, rows) -> Tuple[int, int, int]:
        right = wrong = quiet = 0
        for text, want in rows:
            got = _label(corpus.resolve(text)) or "-"
            if want == "-":
                quiet += got == "-"
            elif got == want:
                right += 1
            elif got != "-":
                wrong += 1
        return right, wrong, quiet

    def trial(self, word: str, concept: str) -> Approved:
        trial = Approved(dict(self.approved.words), dict(self.approved.phrases))
        norm = normalise(word)
        if " " in norm:
            trial.phrases[tuple(norm.split())] = concept
        else:
            trial.words[norm] = concept
        return trial

    def check(self, word: str, concept: str, examples: Sequence[str] = ()) -> Dict[str, str]:
        """Each check's name, with "ok" or the reason it failed."""
        out: Dict[str, str] = {}
        why = usable_addition(word, concept)
        norm = normalise(word)
        if not why and (norm in self.approved.words or tuple(norm.split()) in self.approved.phrases):
            why = "already approved"
        out["usable"] = why or "ok"
        if why:
            return out
        corpus = self._Corpus(approved=self.trial(word, concept))

        changed = [p for p, before in zip(self.phrases, self.base_phrases)
                   if _label(corpus.resolve(p)) != before]
        out["phrases_unchanged"] = ("ok" if not changed else
                                    f"changes {len(changed)} phrase(s), e.g. {changed[0]!r}")

        new = {tuple(v) for v in corpus.ambiguous.values()} - \
              {tuple(v) for v in self.base.ambiguous.values()}
        out["no_new_ambiguity"] = ("ok" if not new else
                                   f"makes {' and '.join(sorted(new)[0])} read the same")

        drops = []
        for name, rows in self.sets.items():
            right, wrong, quiet = self._score(corpus, rows)
            b_right, b_wrong, b_quiet = self.base_scores[name]
            if right < b_right or wrong > b_wrong or quiet < b_quiet:
                drops.append(f"{name}: right {b_right}->{right}, wrong {b_wrong}->{wrong}, "
                             f"left alone {b_quiet}->{quiet}")
        out["scores_hold"] = "ok" if not drops else "; ".join(drops)

        woke = []
        for example in examples:
            after = _label(corpus.resolve(example))
            if after is not None and after != _label(self.base.resolve(example)):
                woke.append(f"{example!r} -> {after}")
        out["other_meanings_quiet"] = ("ok" if not woke else
                                       f"would answer {woke[0]}"
                                       + (f" (+{len(woke) - 1} more)" if len(woke) > 1 else ""))
        return out


def score(candidate: Candidate) -> float:
    """For ordering a review list. Never a reason to approve anything."""
    if candidate.status != "passed":
        return 0.0
    share = candidate.senses_in_scope / candidate.senses_total if candidate.senses_total else 1.0
    weight = RELATION_WEIGHT.get(candidate.relation, 0.5)
    return round(weight * math.sqrt(max(share, 0.0)), 2)


def check_all(candidates: List[Candidate], checker: Checker) -> None:
    concepts_of: Dict[str, set] = defaultdict(set)
    for cand in candidates:
        concepts_of[cand.word].add(cand.concept)
    for cand in candidates:
        if cand.relation == "kind" and not cand.own_evidence:
            cand.checks = {"own_evidence": "a kind of an anchor, but nothing in its own "
                                           "senses is about computing"}
        elif len(concepts_of[cand.word]) > 1:
            others = sorted(concepts_of[cand.word] - {cand.concept})
            cand.checks = {"one_concept": f"the corpus also offers it for {', '.join(others)}"}
        else:
            cand.checks = checker.check(cand.word, cand.concept, cand.examples)
        cand.status = "passed" if all(v == "ok" for v in cand.checks.values()) else "blocked"
        cand.confidence = score(cand)


# --------------------------------------------------------------------------
# The review file, and approval
# --------------------------------------------------------------------------

def read_store(path: Path = APPROVED) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    raw.setdefault("schema", 1)
    raw.setdefault("approved", [])
    raw.setdefault("rejected", [])
    return raw


def write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def decided(store: dict) -> set:
    """(word, concept) pairs a person has already ruled on."""
    out = set()
    for entry in store.get("approved", []) + store.get("rejected", []):
        try:
            out.add((normalise(entry["word"]), entry["concept"]))
        except (KeyError, TypeError):
            continue
    return out


def build_proposals(data: Path, approved_path: Path = APPROVED) -> dict:
    """Everything ``tools/esi_bridge.py propose`` writes."""
    corpus = open_corpus(data)
    dictionary = Dictionary(corpus)
    store = read_store(approved_path)
    candidates, used = propose(dictionary, excluded=decided(store))
    check_all(candidates, Checker(approved=load_approved(approved_path)))
    candidates.sort(key=lambda c: (c.status != "passed", -c.confidence, c.concept, c.word))
    return {
        "schema": 1,
        "about": "Proposals from the CDOS Dictionary Bridge. Nothing here is in use until "
                 "a person approves it: tools/esi_bridge.py approve WORD CONCEPT --by NAME.",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "corpus": {"folder": Path(data).name, "version": corpus.version,
                   "status": corpus.status()},
        "anchors": {c: [asdict(a) for a in found] for c, found in used.items()},
        "without_anchor": sorted(c for c, found in used.items() if not found),
        "candidates": [asdict(c) for c in candidates],
    }


def _find(proposals: dict, word: str, concept: str) -> Optional[dict]:
    word = normalise(word)
    return next((c for c in proposals.get("candidates", [])
                 if c["word"] == word and c["concept"] == concept), None)


def approve(word: str, concept: str, by: str, proposals: dict,
            approved_path: Path = APPROVED, override: str = "") -> Tuple[bool, str]:
    """A named person accepts a proposed word. Re-checked now, against the
    tables as they are now, not as they were when it was proposed."""
    by = (by or "").strip()
    if not by:
        return False, "Say who is approving it (--by NAME): approval is a person's act."
    candidate = _find(proposals, word, concept)
    if candidate is None:
        return False, (f"{word!r} was not proposed for {concept!r}. The bridge only "
                       f"approves what the corpus offered and the checks have seen.")
    store = read_store(approved_path)
    if (normalise(word), concept) in decided(store):
        return False, f"{word!r} → {concept} has already been decided."
    checks = Checker(approved=load_approved(approved_path)).check(
        candidate["word"], concept, candidate.get("examples", []))
    failed = {k: v for k, v in checks.items() if v != "ok"}
    if failed and not override.strip():
        return False, ("Not approved; it fails now: "
                       + "; ".join(f"{k}: {v}" for k, v in failed.items())
                       + ". Pass --override REASON to approve it anyway.")
    if "usable" in failed:
        return False, f"Not approved: {failed['usable']}. No override for that."
    entry = {
        "word": candidate["word"], "concept": concept,
        "relation": candidate["relation"], "said": candidate["said"],
        "anchor": candidate["anchor"], "sense": candidate["sense"],
        "definition": candidate["definition"], "why": candidate["why"],
        "approved_by": by,
        "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": checks,
    }
    if failed:
        entry["override"] = override.strip()
    store["approved"].append(entry)
    write_json(approved_path, store)
    return True, f"Approved: {candidate['word']!r} now reads as {concept}."


def reject(word: str, concept: str, by: str, reason: str,
           approved_path: Path = APPROVED) -> Tuple[bool, str]:
    by = (by or "").strip()
    if not by or not (reason or "").strip():
        return False, "Say who is rejecting it and why (--by NAME --reason TEXT)."
    store = read_store(approved_path)
    if (normalise(word), concept) in decided(store):
        return False, f"{word!r} → {concept} has already been decided."
    store["rejected"].append({
        "word": normalise(word), "concept": concept, "rejected_by": by,
        "reason": reason.strip(),
        "rejected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    write_json(approved_path, store)
    return True, f"Rejected: {normalise(word)!r} will not be proposed for {concept} again."
