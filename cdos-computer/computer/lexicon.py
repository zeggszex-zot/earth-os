"""The relation lexicon: which words mean which relation ("food" -> DIET), learned from the corpus itself.

    propose()   candidates from two places:
                  the dictionary  the corpus's own WordNet entries for the words a relation is named with:
                                  their synonyms (LANGUAGE -> tongue) and the head word of their definitions
                                  (DIET: "the usual food and drink consumed by an organism" -> food, drink)
                  examples        questions with known answers (--mine): the left-over word, and the relation
                                  whose value *is* the answer ("grub of an owl" = mice = OWL DIET MICE -> grub)
    validate()  CDOS checks each one against the knowledge graph and records why:
                  - a definition only counts if it is the sense the relation is used in: its words have to
                    overlap the things the relation is really about (DIET's subjects are animals, organisms;
                    "a prescribed selection of foods" doesn't mention them, so "selection" is not DIET)
                  - a word that already names a relation, or is a question word, is not a synonym
                  - a word proposed for two relations is marked ambiguous, and is used only when the thing
                    being asked about has just one of them
    Lexicon     the accepted mappings, each with its confidence and the facts it rests on.  The executor
                (navigator.match_relations) consults it only when a relation's own words found nothing,
                and says in its trace which mapping it used.

The lexicon never changes a fact or a relation.  It is a separate file (lexicon.json) and can be deleted.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import qualifiers as Q
from .clock import current_year
from .corpus import HINT_ALIASES, LEXICAL_RELS, META, STOP, Corpus, key_of, rel_words

MIN_CONF = 0.5
# An unchecked guess: when no sense fits, the dictionary's first noun sense.  Measured on the Phase 1.5 corpus by hand:
# 4 of 36 right (11%) - its senses are in synset-id order, not commonest-first - so it stays below MIN_CONF.
# Kept as a recorded, rejected proposal so a corpus with ranked senses can be measured again.
FIRST_SENSE_CONF = 0.3
SKIP_HEAD = re.compile(r"^(?:(?:a|an|the|any|some|one|each)\s+)?(?:(?:type|kind|sort|form|variety|member|piece|"
                       r"part|group|class|set|state|quality|act|one|any|instance)\s+of\s+)?(?:(?:a|an|the)\s+)?")
BAD_WORDS = STOP | META | set("""thing things something someone person people usual various several many
especially often usually normally etc used also""".split())
SYNONYM_RELS = ("SYNONYM", "WN_SIMILAR_TO")
GENERIC = set("""part thing entity object whole group set form kind type sort unit body matter physical
abstraction abstract item piece member state quality act being living one""".split())
SCHEMA_RELS = {"IS_FUNCTIONAL", "HAS_UNIT", "SYNSET", "DESCRIPTION", "ALSO_KNOWN_AS", "EQUALS"}


def sense_of(f) -> str:
    """The WordNet sense a fact belongs to (tools/build_shards.py keeps it as SENSE=...), or ''."""
    return Q.get(f.qual, "SENSE")


@dataclass
class Mapping:
    word: str
    relation: str
    conf: float
    via: list = field(default_factory=list)     # [{"how": "definition head", "fact": "DIET DEFINITION ...", ...}]
    status: str = "accepted"                    # accepted | ambiguous | rejected
    why: str = ""

    def short(self) -> str:
        return f"'{self.word}' as {self.relation} ({self.via[0]['how'] if self.via else '?'}, {self.conf:.2f})"


class Lexicon:
    def __init__(self, mappings: list[Mapping] | None = None, meta: dict | None = None):
        self.mappings = mappings or []
        self.meta = meta or {}          # what it was learned from: examples used, examples excluded as held out
        self.by_word: dict[str, list[Mapping]] = defaultdict(list)
        self.by_rel: dict[str, set[str]] = defaultdict(set)
        for m in self.mappings:
            if m.status in ("accepted", "ambiguous"):
                self.by_word[m.word].append(m)
                self.by_rel[m.relation].add(m.word)
        for ms in self.by_word.values():
            ms.sort(key=lambda m: -m.conf)

    def lookup(self, word: str) -> list[Mapping]:
        w = word.lower()
        for cand in (w, w[:-1] if w.endswith("s") else "", w[:-2] if w.endswith("es") else "",
                     w[:-3] + "y" if w.endswith("ies") else ""):
            if cand and cand in self.by_word:
                return self.by_word[cand]
        return []

    def words_for(self, relation: str) -> set[str]:
        return self.by_rel.get(relation, set())

    def save(self, path) -> None:
        Path(path).write_text(json.dumps({"format": "cdos-computer-lexicon/1", "meta": self.meta,
                                          "mappings": [asdict(m) for m in self.mappings]}, indent=1))

    @classmethod
    def load(cls, path) -> "Lexicon":
        d = json.loads(Path(path).read_text())
        if d.get("format") not in ("cdos-computer-lexicon/1", "cdos-owl-lexicon/1"):   # old name still loads
            raise ValueError("not a lexicon file")
        return cls([Mapping(**m) for m in d["mappings"]], d.get("meta"))

    def example_subjects(self) -> set[str]:
        """Every subject an example-learned mapping rests on: questions about these aren't a fair test of it."""
        return {k for m in self.mappings for v in m.via if v.get("how") == "examples" for k in v.get("subjects", [])}


def words_for(corpus: Corpus, relation: str) -> set[str]:
    """A relation's own words plus the ones the lexicon learned for it."""
    lex = getattr(corpus, "lexicon", None)
    return rel_words(relation) | (lex.words_for(relation) if lex else set())


# ---------------------------------------------------------------------- reading definitions

def heads(definition: str) -> list[str]:
    """The head word(s) of a dictionary definition (its genus): 'the usual food and drink consumed by an
    organism' -> ['food', 'drink'];  'the type of environment in which ...' -> ['environment']."""
    d = re.sub(r'"[^"]*"', "", definition.lower()).split(";")[0]
    d = re.sub(r"\([^)]*\)", "", d).strip()
    d = SKIP_HEAD.sub("", d)
    toks = re.findall(r"[a-z][a-z'\-]*", d)
    cur = []
    for t in toks:
        # the first conjunct is the head: "food and drink" is food (a lion's drink isn't its diet)
        if t in BAD_WORDS and not cur and t not in STOP:
            continue                      # "the usual food": a modifier before the head
        if t in STOP or t in BAD_WORDS or (cur and t.endswith(("ed", "ing", "ly")) and len(t) > 4):
            break
        cur.append(t)
    out = cur[-1:]
    return [w for w in out if w not in BAD_WORDS and len(w) >= 3]


def name_words(relation: str) -> list[str]:
    """CAPITAL -> ['capital'];  OFFICIAL_LANGUAGE -> ['official', 'language'];  HAS_SATELLITE -> ['satellite']."""
    ws = [w for w in relation.lower().split("_") if w not in STOP and w not in ("has", "is", "wn") and len(w) >= 3]
    return ws


def singular(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


# ---------------------------------------------------------------------- learning

class Learner:
    def __init__(self, corpus: Corpus, year: int = 0, max_subjects: int = 0, sample: int = 60):
        from .navigator import TAXONOMY, Navigator
        self.corpus = corpus
        # Learning is done offline, not on the Pi: keep every shard in memory (the full corpus needs ~1 GB here)
        # instead of re-reading them while walking the taxonomy.
        corpus.cache_shards = max(corpus.cache_shards, sum((corpus.meta or {}).get(k, 0) for k in ("s_shards", "v_shards")))
        self.nav = Navigator(corpus)
        import time
        self.year = year or current_year()
        self.max_subjects, self.sample = max_subjects, sample
        self.taxonomy = TAXONOMY
        self.uses: dict[str, list] = defaultdict(list)       # relation -> [(subject, fact)]
        self.count: dict[str, int] = defaultdict(int)
        self.proposals: list[Mapping] = []
        self.skipped: dict[str, str] = {}

    def scan(self) -> None:
        """Which relations exist, and a sample of what each one is about."""
        for s in sorted(self.corpus.subjects)[: self.max_subjects or None]:
            for f in self.nav.facts(s, self.year):
                if f.relation in LEXICAL_RELS or f.relation == "IS_A":
                    continue
                self.count[f.relation] += 1
                if len(self.uses[f.relation]) < self.sample:
                    self.uses[f.relation].append((s, f))

    def context(self, relation: str) -> set[str]:
        """The words for what a relation is about: its subjects and values, what the curated taxonomy (IS_A) says
        they are, and one step of WordNet above those in each word's *first* noun sense (animal -> organism).
        Not every WordNet hypernym of every sense: "dog" would bring in frump, andiron and sausage."""
        out, keys = set(), set()
        for s, f in self.uses.get(relation, []):
            for k in [s] + ([key_of(f.value)] if f.kind == "S" else []):
                for path in self.nav.ancestors(k, self.year, "IS_A"):
                    keys.add(path[-1])          # what things are (mammal, country), not their names (lion, japan)
        for k in keys:
            out |= set(k.lower().split("_"))
            first = self.first_noun_sense(k)
            for f in self.facts_of(k, ("HYPERNYM",)):
                if first is None or sense_of(f) in (first, ""):
                    out |= set(key_of(f.value).lower().split("_"))
        return {singular(w) for w in out if w not in STOP and w not in GENERIC}

    def facts_of(self, key: str, rels) -> list:
        return [f for f in self.nav.facts(key, self.year) if f.relation in rels]

    def first_noun_sense(self, key: str):
        """The first noun sense a word has in the dictionary (WordNet lists the commonest first), or None
        if the corpus doesn't tag senses."""
        for f in self.facts_of(key, ("DEFINITION",)):
            sid = sense_of(f)
            if sid.startswith("WN_n_"):
                return sid
        return None

    def senses(self, key: str) -> dict:
        """sense id -> its definitions.  Untagged corpora: each definition is its own sense."""
        out = {}
        for i, f in enumerate(self.facts_of(key, ("DEFINITION",))):
            out.setdefault(sense_of(f) or f"#{i}", []).append(f)
        return out

    def propose_from_dictionary(self) -> None:
        """For each relation people ask about as "the ___ of X": the dictionary entry for its name (or the head
        word of the name), the noun sense that fits what the relation is about, and from that sense only:
        its definition's head words and its synonyms."""
        for rel in sorted(self.count):
            entry, why = self.entry_for(rel)
            if not entry:
                self.skipped[rel] = why
                continue
            ctx = self.context(rel)
            senses = self.senses(entry)
            tagged = any(not sid.startswith("#") for sid in senses)
            fits = {}
            for sid, defs in senses.items():
                if tagged and not sid.startswith("WN_n_"):
                    continue                      # "the ___ of X" asks for a noun
                text = " ".join(re.sub(r'"[^"]*"', "", d.value.lower()) for d in defs)
                words = {singular(x) for x in re.findall(r"[a-z]+", text) if x not in STOP}
                fits[sid] = (sorted(words & ctx), defs)
            best = max((len(v[0]) for v in fits.values()), default=0)
            winners = [sid for sid, v in fits.items() if best and len(v[0]) == best]
            chosen = winners[0] if len(winners) == 1 else None
            fallback = None
            if tagged and chosen is None and not winners and fits:
                fallback = next(iter(fits))       # nothing fits: WordNet's first (commonest) noun sense, unchecked
            for sid, (shared, defs) in fits.items():
                ok = sid == chosen
                first = sid == fallback
                for d in defs:
                    for h in heads(d.value):
                        self.proposals.append(Mapping(h, rel, 0.8 if ok else FIRST_SENSE_CONF if first else 0.35, [{
                            "how": "definition head" if not first else "definition head, first sense",
                            "fact": f"{entry} DEFINITION {d.value}", "sense": sid,
                            "sense_fits": shared, "note": "the sense the relation is used in" if ok else
                            "no sense fits; the dictionary's first noun sense" if first else (
                                "two senses fit equally" if sid in winners else "this sense doesn't fit the relation")}]))
            for f in self.facts_of(entry, SYNONYM_RELS):
                k = key_of(f.value)
                if "_" in k or k == entry:
                    continue          # one-word synonyms only: "home ground" isn't asked as one word
                sid = sense_of(f)
                if tagged and fallback is not None and sid == fallback:
                    self.proposals.append(Mapping(k.lower(), rel, FIRST_SENSE_CONF, [{
                        "how": "synonym, first sense", "fact": f"{entry} SYNONYM {k}", "sense": sid,
                        "note": "no sense fits; the dictionary's first noun sense"}]))
                    continue
                if tagged:
                    ok = chosen is not None and sid == chosen
                    conf, note = (0.75, "same sense as the definition that fits") if ok else (
                        0.3, "a synonym from another sense of the word" if chosen else "no sense of the word fits")
                else:
                    conf, note = (0.6, "senses not tagged in this corpus")
                self.proposals.append(Mapping(k.lower(), rel, conf, [{"how": "synonym", "fact": f"{entry} SYNONYM {k}",
                                                                      "sense": sid, "note": note}]))

    def has_adjective_sense(self, word: str) -> bool:
        """True if the dictionary lists an adjective sense for it.  A corpus without sense tags can't say: allowed."""
        sids = [sense_of(f) for f in self.facts_of(word.upper(), ("DEFINITION",))]
        if not any(sids):
            return True
        return any(x.startswith(("WN_a_", "WN_s_")) for x in sids)

    def tagged_pos(self, word: str) -> str:
        k = word.upper().replace("-", "_")
        sids = [sense_of(f) for f in self.facts_of(k, ("DEFINITION",))] if self.corpus.has(k) else []
        sids = [x for x in sids if x]
        if not sids:
            return "unknown"
        return "noun" if any(x.startswith("WN_n_") for x in sids) else "not a noun"

    def entry_for(self, rel: str):
        """The dictionary entry a relation is named after, or ('', why not)."""
        from .navdata import shape
        if re.match(r"^(IS|CAN|HAS_UNIT$)", rel) or rel in SCHEMA_RELS:
            return "", "a yes/no property or schema, not asked as 'the ___ of X'"
        vals = {f.value.upper() for _, f in self.uses.get(rel, [])}
        if vals and vals <= {"TRUE", "FALSE", "YES", "NO"}:
            return "", "yes/no values"
        if shape(rel)[0] != "noun":
            return "", "not a noun relation"
        name = re.sub(r"^HAS_", "", rel)
        if self.corpus.has(name) and "_" in name:
            return name, ""                       # BOILING_POINT: the compound itself is in the dictionary
        raw = name.lower().split("_")
        words = [w for w in raw if w not in ("from", "wn")]
        if not words or words[-1] in ("count", "number"):
            return "", "a count: asked as 'how many'"
        if len(words) == 1:
            head = words[0].upper()
            return (head, "") if self.corpus.has(head) else ("", f"no dictionary entry for {words[0]}")
        # A compound the dictionary doesn't have: its head word stands for it only when the rest is an adjective
        # ("official language", "equatorial radius").  "conversion factor" is not a factor, "players per side"
        # is not a side.
        if len(words) == 2 and words[0] not in STOP and self.has_adjective_sense(words[0]) and self.corpus.has(words[1].upper()):
            return words[1].upper(), ""
        return "", "a compound name the dictionary doesn't have"

    def propose_from_examples(self, examples: list[dict]) -> None:
        """Questions with known answers: which relation of the thing asked about holds the answer,
        and which word of the question was left over to mean it."""
        from .controller import Reader
        reader = Reader(self.corpus, self.nav)
        seen = defaultdict(set)
        for ex in examples:
            if ex.get("expect") != "answered" or not ex.get("gold"):
                continue
            r = reader.read(ex["question"])
            things = about(self.corpus, ex, reader, self.nav, self.year)   # what it's about: tiger, not "home"
            if len(things) != 1:
                continue                     # about nothing, or about two things: which relation is whose?
            k = next(iter(things))
            left = [r.toks[i] for i in r.free] + [s.phrase for s in r.spans
                                                  if s.phrase.replace(" ", "_").upper() not in (k, k + "S")]
            left = [w for w in left if " " not in w and w not in BAD_WORDS and len(w) >= 3]
            gold = [g.lower() for g in ex["gold"]]
            for _ in (0,):
                facts = [f for f in self.nav.facts(k, self.year) if f.relation not in LEXICAL_RELS]
                said = lambda f: f" {f.value.lower().replace('_', ' ')} "
                rels = {f.relation for f in facts if any(f" {g} " in said(f) for g in gold)}   # "euro", not "europe"
                from .navigator import hint_words
                for rel in rels:
                    rw = rel_words(rel)
                    # "what money does france use": money already says CURRENCY, so "use" is not evidence for it
                    if any(w in rw or set(hint_words([w])) & rw for w in left):
                        continue
                    for w in left:
                        seen[(w, rel)].add(k)
        for (w, rel), subjects in seen.items():
            n = len(subjects)
            # One thing isn't enough: "landmass of france" = europe is CONTINENT *and* LOCATED_IN, and a word seen
            # with a single subject can't tell a relation from a coincidence.
            self.proposals.append(Mapping(w, rel, min(0.9, 0.3 + 0.15 * n), [{
                "how": "examples", "subjects": sorted(subjects)[:8], "n": n,
                "note": "the answer was this relation's value for each of these" if n > 1 else
                "seen with one thing only"}]))

    def validate(self) -> Lexicon:
        vocab = self.corpus.rel_vocab()
        merged: dict[tuple, Mapping] = {}
        for m in self.proposals:
            k = (m.word, m.relation)
            if k in merged:
                old = merged[k]
                old.via += m.via
                old.conf = min(0.95, max(old.conf, m.conf) + 0.1 * min(old.conf, m.conf))   # two sources agree
            else:
                merged[k] = Mapping(m.word, m.relation, m.conf, list(m.via))
        out = []
        by_word = defaultdict(list)
        for m in merged.values():
            if not re.fullmatch(r"[a-z][a-z'\-]+", m.word) or m.word in BAD_WORDS:
                m.status, m.why = "rejected", "not a content word"
            elif m.word in vocab or m.word in HINT_ALIASES:
                m.status, m.why = "rejected", "already names a relation (or is a known alias)"
            elif self.count.get(m.relation, 0) == 0:
                m.status, m.why = "rejected", "no facts use this relation"
            elif self.tagged_pos(m.word) == "not a noun":
                m.status, m.why = "rejected", "not a noun in the dictionary: can't be 'the ___ of X'"
            elif m.conf < MIN_CONF:
                m.status, m.why = "rejected", "too little support"
            else:
                by_word[m.word].append(m)
            out.append(m)
        for w, ms in by_word.items():
            ms.sort(key=lambda m: -m.conf)
            if len(ms) > 1 and ms[0].conf - ms[1].conf < 0.1:
                for m in ms[:2]:
                    m.status = "ambiguous"
                    m.why = f"also proposed for {', '.join(x.relation for x in ms if x is not m)}"
            for m in ms[1:]:
                if m.status == "accepted":
                    m.status, m.why = "rejected", f"weaker than {ms[0].relation}"
        return Lexicon(sorted(out, key=lambda m: (m.status != "accepted", m.word, -m.conf)))


def subjects_of(corpus: Corpus, question: str, reader=None) -> set[str]:
    """The corpus names a question is about (the same reading the controller uses)."""
    from .controller import Reader
    reader = reader or Reader(corpus)
    r = reader.read(question)
    out = set()
    for s in r.spans:
        if s.relish or s.soft:
            continue
        k = s.phrase.replace(" ", "_").upper()
        k = k if corpus.has(k) else (k[:-1] if corpus.has(k[:-1]) else k)
        # A thing, not a dictionary word: it has facts from somewhere other than the dictionary ("japan", not "use").
        if any(f.source != "OEWN" and f.relation not in LEXICAL_RELS for f in corpus.facts_for(k)) or not corpus.has(k):
            out.add(k)
    return out


def about(corpus: Corpus, ex: dict, reader=None, nav=None, year: int = 0) -> set[str]:
    """What a question with a known answer is about.  Usually that's subjects_of().  But a word like "home" can be
    a thing in its own right (it has Wikidata facts) as well as the word for a relation, so "what is the home of a
    tiger" names two things.  When exactly one of them holds the answer in its facts (TIGER HABITAT FOREST), that
    one is what it's about, and the other is a word.  Otherwise all of them, as before."""
    things = subjects_of(corpus, ex["question"], reader)
    gold = [g.lower() for g in ex.get("gold") or []]
    if len(things) < 2 or not gold:
        return things
    from .navigator import Navigator
    nav = nav or Navigator(corpus)
    year = year or current_year()
    said = lambda f: f" {f.value.lower().replace('_', ' ')} "
    holders = {k for k in things
               if any(f" {g} " in said(f) for f in nav.facts(k, year) if f.relation not in LEXICAL_RELS for g in gold)}
    return holders if len(holders) == 1 else things


def norm_q(q: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (q or "").lower()))


def split_examples(corpus: Corpus, examples: list[dict], heldout: list[dict]) -> tuple[list[dict], list[dict]]:
    """(examples to learn from, examples dropped).  An example is dropped if it *is* a held-out question or is
    about any thing a held-out question is about: then the test can't be passed by having seen the answer."""
    from .controller import Reader
    from .navigator import Navigator
    reader, nav = Reader(corpus), Navigator(corpus)
    hq = {norm_q(h["question"]) for h in heldout}
    hs = set()
    for h in heldout:
        hs |= {x for k in about(corpus, h, reader, nav) for x in (k, k[:-1] if k.endswith("S") else k)}
    keep, drop = [], []
    for ex in examples:
        subs = {x for k in about(corpus, ex, reader, nav) for x in (k, k[:-1] if k.endswith("S") else k)}
        if norm_q(ex["question"]) in hq:
            drop.append({**ex, "dropped": "the same question is held out"})
        elif subs & hs:
            drop.append({**ex, "dropped": f"about {', '.join(sorted(subs & hs)).lower()}, which the held-out set asks about"})
        else:
            keep.append(ex)
    return keep, drop


def learn(corpus: Corpus, examples: list[dict] | None = None, heldout: list[dict] | None = None, **kw) -> Lexicon:
    examples = examples or []
    dropped = []
    if heldout:
        examples, dropped = split_examples(corpus, examples, heldout)
    ln = Learner(corpus, **kw)
    ln.scan()
    ln.propose_from_dictionary()
    if examples:
        ln.propose_from_examples(examples)
    lex = ln.validate()
    lex.meta = {"examples_used": len(examples), "examples_dropped": dropped, "heldout_questions": len(heldout or []),
                "relations_skipped": ln.skipped}
    return lex
