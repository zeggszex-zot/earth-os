"""The learned reasoning controller: a tiny neural network that chooses *how* to answer, never *what*.

    question --read()--> words, the names the corpus knows, the words left over
             --features--> a small network --> a score for each ACTION (a plan shape)
             --fill()--> plans in the plan language (computer/plan.py), only with names the corpus has
             --validator, executor--> an answer and its evidence  (computer/navigator.py, unchanged)
             --accept()--> every thing the question names must be in that evidence, or it isn't said

The network doesn't hold facts.  It holds about 100k numbers that map the *shape* of a question
("x vs y, which is heavier", "meaning: cheerful", "jupiter satellites, how many") to the action
most likely to work: compare, define, count ...  The slots are filled by code from the corpus's own
names, the executor is the only thing that reads facts, and accept() is deterministic.  So a wrong
choice costs a plan that finds nothing, never an invented fact.

It runs after the rule planner (computer/planner.py), for the questions the rules have no pattern for.

How it learns (tools/train_controller.py):
  1. Teacher.  The rule planner's verified examples (computer/navdata.py) plus re-worded versions of them,
     and any questions you write yourself with their answers.
  2. Exploration.  For each question CDOS tries *every* action, runs it on the corpus and scores it
     against the known answer.  The actions that reached the answer (and passed accept()) are the
     training target.  Nobody labels the re-worded questions: the corpus and the checker do.
  3. The network learns to predict which action the checker will accept, so at run time the computer
     tries one or two plans instead of all of them.

Inference is plain Python (no numpy) so it runs on the Pi Zero.  Training uses numpy.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .corpus import HINT_ALIASES, STOP, Corpus, fnv1a, key_of, rel_words
from .lexicon import words_for
from .navigator import DIMENSIONS, Navigator, hint_words, word_forms
from .plan import Plan, PlanError, parse
from .planner import AUX, COMPARATIVE, LESS_WORDS, LEX, WH, YEAR

# ---------------------------------------------------------------------- the actions

ACTIONS: dict[str, list[str]] = {
    "hop":          ["$1 = FIND {e1}", "$2 = GET $1 {r}", "ANSWER $2"],
    "hop_inherit":  ["$1 = FIND {e1}", "$2 = GET $1 {r} INHERIT", "ANSWER $2"],
    "chain":        ["$1 = FIND {e1}", "$2 = GET $1 {r1}", "$3 = GET $2 {r2}", "ANSWER $3"],
    "isa":          ["$1 = FIND {e1}", "$2 = GET $1 IS_A", "ANSWER $2"],
    "count":        ["$1 = FIND {e1}", "$2 = GET $1 {r}", "$3 = COUNT $2", "ANSWER $3"],
    "compare_more": ["$1 = FIND {e1}", "$2 = FIND {e2}", "$3 = GET $1 {r}", "$4 = GET $2 {r}",
                     "$5 = COMPARE $3 $4 MORE {label}", "ANSWER $5"],
    "compare_less": ["$1 = FIND {e1}", "$2 = FIND {e2}", "$3 = GET $1 {r}", "$4 = GET $2 {r}",
                     "$5 = COMPARE $3 $4 LESS {label}", "ANSWER $5"],
    "reverse":      ["$1 = FIND {e1}", "$2 = WHO $1 {r}", "ANSWER $2"],
    "reverse_filter": ["$1 = FIND {e1}", "$2 = WHO $1 {r}", "$3 = FILTER $2 {t}", "ANSWER $3"],
    "test_isa":     ["$1 = FIND {e1}", "$2 = GET $1 IS_A", "$3 = TEST $2 {w}", "ANSWER $3"],
    "test_hypernym": ["$1 = FIND {e1}", f"$2 = GET $1 {LEX['hypernym']}", "$3 = TEST $2 {w}", "ANSWER $3"],
    "test_located": ["$1 = FIND {e1}", "$2 = GET $1 ~located,part", "$3 = TEST $2 {w}", "ANSWER $3"],
    "test_have":    ["$1 = FIND {e1}", "$2 = GET $1 * INHERIT", "$3 = TEST $2 {w}", "ANSWER $3"],
    "lex_test_synonym": ["$1 = FIND {e1}", f"$2 = GET $1 {LEX['synonym']}", "$3 = TEST $2 {w}", "ANSWER $3"],
    "lex_test_antonym": ["$1 = FIND {e1}", f"$2 = GET $1 {LEX['antonym']}", "$3 = TEST $2 {w}", "ANSWER $3"],
    "lex_chain":    ["$1 = FIND {e1}", f"$2 = GET $1 {LEX['antonym']}", f"$3 = GET $2 {LEX['define']}", "ANSWER $3"],
    "hyponym_who":  ["$1 = FIND {e1}", f"$2 = WHO $1 {LEX['hypernym']}", "ANSWER $2"],
    # yes/no about a relation ("does titan orbit jupiter"), and tests combined (computer/logic.py)
    "test_rel":     ["$1 = FIND {e1}", "$2 = GET $1 {r}", "$3 = TEST $2 {w}", "ANSWER $3"],
    "logic_not":    ["$1 = FIND {e1}", "$2 = GET $1 IS_A", "$3 = TEST $2 {w}", "$4 = LOGIC NOT $3", "ANSWER $4"],
}
for _op in ("and", "or", "nor"):
    ACTIONS[f"logic_{_op}"] = ["$1 = FIND {e1}", "$2 = GET $1 IS_A", "$3 = TEST $2 {w}", "$4 = TEST $2 {w2}",
                               f"$5 = LOGIC {_op.upper()} $3 $4", "ANSWER $5"]
for _k in ("define", "synonym", "antonym", "hypernym", "hyponym", "derivation", "example"):
    ACTIONS[_k] = ["$1 = FIND {e1}", f"$2 = GET $1 {LEX[_k]}", "ANSWER $2"]
ACTION_NAMES = sorted(ACTIONS)

NEEDS = {   # which slots each action needs filled
    "hop": "e1 r", "hop_inherit": "e1 r", "chain": "e1 r1 r2", "isa": "e1", "count": "e1 r",
    "compare_more": "e1 e2 r", "compare_less": "e1 e2 r", "reverse": "e1 r", "reverse_filter": "e1 r t",
    "test_isa": "e1 w", "test_hypernym": "e1 w", "test_located": "e1 w", "test_have": "e1 w",
    "lex_test_synonym": "e1 w", "lex_test_antonym": "e1 w", "lex_chain": "e1", "hyponym_who": "e1",
    "test_rel": "e1 r w", "logic_not": "e1 w", "logic_and": "e1 w w2", "logic_or": "e1 w w2", "logic_nor": "e1 w w2",
}
CONNECTIVES = {"or", "nor", "neither", "not", "both"}

# Words that say what kind of question it is, not what it's about: never a name, never a relation.
CUE = set("""meaning mean means meant define definition definitions explain understand word words term
synonym synonyms similar another other different differently way ways alternative
opposite opposites antonym antonyms reverse contrary
kind kinds type types sort sorts category class variety varieties examples example sentence sentences
number count many much total
true right correct wrong really isn't aren't wasn't doesn't don't didn't call would right
which one ones vs versus or than compare comparison between
name list things thing anything everything something stuff whatever all
please tell know wonder wondering curious quick question hey ollie um well like even think confirm""".split())
FUNCTION = (STOP - {"owl"}) | WH | AUX | {"i", "you", "me", "it", "its", "they", "them", "their", "that", "this", "these", "those",
                              "am", "be", "been", "being", "of", "in", "on", "at", "to", "for", "with", "by", "from",
                              "into", "about", "s", "'s", "there", "here", "work", "works", "happen", "go"}
UNIT_WORDS = {"m": {"metre", "metres", "meter", "meters", "m"}, "km": {"km", "kilometre", "kilometres",
              "kilometer", "kilometers"}, "kg": {"kg", "kilogram", "kilograms", "kilo", "kilos"},
              "": {"units", "unit"}}
ALL_UNIT_WORDS = set().union(*UNIT_WORDS.values())
TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?|[,:?\-]")


def singular(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if re.search(r"(sh|ch|x|ss)es$", w):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


# ---------------------------------------------------------------------- reading a question

@dataclass
class Span:
    start: int
    end: int
    phrase: str
    soft: bool = False      # found only by the navigator's soft match (spelling, word form)
    relish: bool = False    # also a word relations are named with ("capital", "population")

    @property
    def tokens(self) -> range:
        return range(self.start, self.end)


@dataclass
class Reading:
    question: str
    toks: list[str]
    spans: list[Span] = field(default_factory=list)
    free: list[int] = field(default_factory=list)     # positions of content words that aren't names
    possessive: bool = False

    def word(self, i: int) -> str:
        return self.toks[i]


class Reader:
    """Question -> Reading.  Names are only ever what the corpus has (or the navigator's soft match finds)."""

    def __init__(self, corpus: Corpus, nav: Navigator | None = None):
        self.corpus = corpus
        self.nav = nav or Navigator(corpus)
        self._relish = None
        self._last = None

    def relish(self, w: str) -> bool:
        if self._relish is None:
            self._relish = set(self.corpus.rel_vocab()) | set(HINT_ALIASES) | set(DIMENSIONS) | set(COMPARATIVE)
            lex = getattr(self.corpus, "lexicon", None)
            if lex:
                self._relish |= set(lex.by_word)        # "food": a word the lexicon learned means a relation
        return w in self._relish or singular(w) in self._relish

    def has(self, phrase: str) -> bool:
        k = "_".join(phrase.split()).upper()
        return any(x and self.corpus.has(x) for x in (k, singular(k.lower()).upper(), k[:-1] if k.endswith("S") else ""))

    def is_value(self, phrase: str) -> bool:
        """Known only as the value of other facts ("krill"): a name WHO can start from."""
        k = "_".join(phrase.split()).upper()
        return any(x and self.corpus.reverse(x) for x in (k, k[:-1] if k.endswith("S") else ""))

    def read(self, question: str) -> Reading:
        if self._last and self._last[0] == question:
            return self._last[1]
        r = self._read(question)
        self._last = (question, r)
        return r

    def _read(self, question: str) -> Reading:
        q = (question or "").lower().replace("’", "'")
        q = YEAR.sub(" ", q)
        q = re.sub(r"^\W*(?:(?:hey|hi|ok|okay|so|um)\b[\s,!.:]*|(?:computer|cdos|owl|ollie)\s*[,!:]\s*)+", "", q)  # "hey computer, ..."
        q = re.sub(r"\b(what|who|where|how|that|it|there|one|tall|big|which|when)'s\b", r"\1 is", q)
        poss = "'s " in q + " "
        q = re.sub(r"'s\b", "", q)
        toks = [t for t in TOKEN.findall(q)]
        r = Reading(question, toks, possessive=poss)
        word = lambda i: re.fullmatch(r"[a-z0-9']+", toks[i]) is not None
        bad_edge = lambda t: t in FUNCTION or t in CUE
        used = [False] * len(toks)
        for n in (4, 3, 2, 1):
            for i in range(len(toks) - n + 1):
                if any(used[i:i + n]) or not all(word(j) for j in range(i, i + n)):
                    continue
                if bad_edge(toks[i]) or bad_edge(toks[i + n - 1]):
                    continue
                phrase = " ".join(toks[i:i + n])
                if n == 1 and (len(phrase) < 2 or phrase.isdigit()):
                    continue
                if self.has(phrase) or (n <= 2 and not self.relish(phrase) and self.is_value(phrase)):
                    r.spans.append(Span(i, i + n, phrase, relish=n == 1 and self.relish(phrase)))
                    used[i:i + n] = [True] * n
        for i, t in enumerate(toks):          # spelling slips and word forms: the navigator's own soft match
            if used[i] or not word(i) or t in FUNCTION or t in CUE or len(t) < 4 or self.relish(t):
                continue
            if self.nav.soft_resolve(t) is not None:
                r.spans.append(Span(i, i + 1, t, soft=True))
                used[i] = True
        r.spans.sort(key=lambda s: s.start)
        r.free = [i for i, t in enumerate(toks) if not used[i] and word(i) and t not in FUNCTION and t not in CUE
                  and not t.isdigit()]
        return r


# ---------------------------------------------------------------------- who does what to whom

VERB_ALIASES = {"eat", "eats", "eating", "ate", "speak", "speaks", "spoke", "spoken", "weigh", "weighs"}
JOINS_SUBJECT = {"of", "for", "in", "on", "at", "from"}       # "the orbit of titan", "spoken in france": titan, france own it


@dataclass
class Role:
    name: str       # the name's phrase, as FIND will have it
    word: str       # the verb ("orbiting", "eats")
    role: str       # "subject": the name does it (titan orbits ...);  "object": it's done to the name (... orbit titan)


_VERB_CACHE: dict = {}


def verb_relations(corpus: Corpus, word: str) -> list[str]:
    """Relations a word names *as a verb*: orbiting -> ORBITS, borders -> BORDERS, eats -> DIET (an alias)."""
    ck = (id(corpus), word)
    if ck not in _VERB_CACHE:
        _VERB_CACHE[ck] = _verb_relations(corpus, word)
    return _VERB_CACHE[ck]


def _verb_relations(corpus: Corpus, word: str) -> list[str]:
    from .navdata import shape
    forms = {word, word + "s"} | set(word_forms(word)[:4])
    rels = []
    for rel in (corpus.meta or {}).get("relation_names", []):
        k, _, base = shape(rel)
        if k == "verb" and forms & (rel_words(rel) | {base}):
            rels.append(rel)
    if word in VERB_ALIASES:
        hints = set(HINT_ALIASES.get(word, []))
        rels += [r for r in (corpus.meta or {}).get("relation_names", []) if rel_words(r) & hints]
    return rels


def roles(r: Reading, corpus: Corpus) -> list[Role]:
    """Which way round the question is, from word order alone:
        "anything orbiting saturn", "what eats krill"    -> saturn, krill are the object  (WHO)
        "titan orbits what", "what does ganymede orbit"  -> titan, ganymede are the subject (GET)
        "what is saturn orbited by"                      -> saturn is the object (passive)
        "the orbit of titan", "spoken in france"          -> the subject (it's theirs)"""
    out = []
    # A verb can also be a dictionary word ("orbits" has an entry of its own in a full corpus): look at single words
    # whether or not they were read as names, and don't count the verb as a name.
    one_word = {s.start: s for s in r.spans if s.end - s.start == 1}
    cands = sorted(set(r.free) | set(one_word))
    verbs = [i for i in cands if re.fullmatch(r"[a-z]+", r.toks[i]) and r.toks[i] not in FUNCTION
             and verb_relations(corpus, r.toks[i])]
    if not verbs:
        return out
    skip = {"the", "a", "an", "some", "any", "all", "that", "is", "are", "was", "were", "be", "being"}
    for s in r.spans:
        if s.relish or s.soft or s.start in verbs:
            continue
        best = None
        for i in verbs:
            t = r.toks[i]
            participle = t.endswith(("ed", "en")) and len(t) > 4
            if i < s.start:                                   # verb ... name
                between = [w for w in r.toks[i + 1:s.start] if w not in skip]
                if any(w in JOINS_SUBJECT for w in between) or (participle and not between):
                    role = "subject"
                elif not between:
                    role = "object"
                else:
                    continue
                dist = s.start - i
            else:                                             # name ... verb
                between = [w for w in r.toks[s.end:i] if w not in skip and w not in AUX]
                if between:
                    continue
                after = r.toks[i + 1:i + 2]
                role = "object" if participle and after == ["by"] else "subject"
                dist = i - s.end + 1
            if best is None or dist < best[0]:
                best = (dist, Role(s.phrase, t, role))
        if best:
            out.append(best[1])
    return out


def wrong_way(r: Reading, plan: Plan, res, corpus: Corpus) -> str:
    """'' if the plan goes the way the question does, else what's wrong."""
    var_of = {st.text: st.out for st in plan.steps if st.op == "FIND"}
    fell_back = any("used its own relation instead" in t for t in res.steps)
    for ro in roles(r, corpus):
        v = var_of.get(ro.name)
        if not v:
            continue
        ops = [st.op for st in plan.steps if st.args[:1] == [v] and st.op in ("GET", "WHO")]
        forward = "GET" in ops or ("WHO" in ops and fell_back)
        backward = "WHO" in ops and not fell_back
        if ro.role == "object" and forward:
            return f"the question asks what {ro.word} {ro.name}; the plan found what {ro.name} does"
        if ro.role == "subject" and backward:
            return f"the question asks what {ro.name} {ro.word}; the plan found what is done to {ro.name}"
    return ""


# ---------------------------------------------------------------------- features

DIM = 4096
YEAR_NOW = __import__("time").localtime().tm_year


def features(r: Reading, reader: Reader | None = None) -> list[str]:
    """The question's shape: its words with names masked, word pairs, where the names are, comparatives."""
    in_span = {}
    for s in r.spans:
        for i in s.tokens:
            in_span[i] = s
    seq = []
    for i, t in enumerate(r.toks):
        s = in_span.get(i)
        if s is None:
            seq.append(t)
        elif s.relish and not s.soft:
            seq.append(t)                       # "capital": acting as a relation word
        elif i == s.start:
            seq.append("<e>")
    names = [s for s in r.spans if not s.relish]
    f = ["bias", f"ne:{min(len(names), 3)}", f"nf:{min(len(r.free), 3)}", f"len:{min(len(r.toks) // 3, 5)}"]
    f += [f"w:{t}" for t in seq]
    f += [f"b:{a}_{b}" for a, b in zip(["^"] + seq, seq + ["$"])]
    if seq:
        f += [f"first:{seq[0]}", f"last:{seq[-1]}"]
        if len(seq) > 1:
            f.append(f"first2:{seq[0]}_{seq[1]}")
    if r.possessive:
        f.append("possessive")
    words = set(r.toks)
    if words & set(COMPARATIVE) or any(t.endswith("er") and t[:-2] in DIMENSIONS for t in words):
        f.append("comparative")
    if words & LESS_WORDS:
        f.append("less")
    if any(t.endswith("ing") for t in r.toks):
        f.append("ing")
    if reader is not None:
        f += [f"role:{ro.role}" for ro in roles(r, reader.corpus)]
    if reader is not None and names:
        facts = reader.nav.facts(key_of(names[0].phrase.replace(" ", "_")), YEAR_NOW) if not names[0].soft else []
        rels = {x.relation for x in facts}
        f.append("e:dict" if "DEFINITION" in rels else "e:nodict")
        f.append("e:isa" if "IS_A" in rels else "e:noisa")
    return f


def hashed(feats: list[str], dim: int = DIM) -> list[int]:
    return sorted({fnv1a("f:" + x) % dim for x in feats})


# ---------------------------------------------------------------------- the network

class Network:
    """features (hashed, sparse) -> ReLU hidden layer -> softmax over actions.  Plain Python."""

    def __init__(self, actions: list[str], dim: int = DIM, hidden: int = 32, rows=None, b1=None, w2=None, b2=None):
        self.actions, self.dim, self.hidden = list(actions), dim, hidden
        self.rows = rows or {}                # feature index -> hidden-size row (only rows that were trained)
        self.b1 = b1 or [0.0] * hidden
        self.w2 = w2 or [[0.0] * hidden for _ in actions]
        self.b2 = b2 or [0.0] * len(actions)

    def predict(self, idx: list[int]) -> dict[str, float]:
        h = list(self.b1)
        scale = 1.0 / math.sqrt(max(1, len(idx)))
        for i in idx:
            row = self.rows.get(i)
            if row:
                for j, v in enumerate(row):
                    h[j] += v * scale
        h = [v if v > 0 else 0.0 for v in h]
        z = [b + sum(w * x for w, x in zip(ws, h)) for ws, b in zip(self.w2, self.b2)]
        m = max(z)
        e = [math.exp(v - m) for v in z]
        s = sum(e)
        return {a: v / s for a, v in zip(self.actions, e)}

    def to_json(self) -> dict:
        rnd = lambda xs: [round(x, 5) for x in xs]
        return {"format": "cdos-computer-controller/1", "actions": self.actions, "dim": self.dim, "hidden": self.hidden,
                "rows": {str(k): rnd(v) for k, v in self.rows.items()}, "b1": rnd(self.b1),
                "w2": [rnd(r) for r in self.w2], "b2": rnd(self.b2)}

    @classmethod
    def from_json(cls, d: dict) -> "Network":
        if d.get("format") not in ("cdos-computer-controller/1", "cdos-owl-controller/1"):   # old name still loads
            raise ValueError("not a controller file")
        return cls(d["actions"], d["dim"], d["hidden"], {int(k): v for k, v in d["rows"].items()},
                   d["b1"], d["w2"], d["b2"])

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), separators=(",", ":")))

    @classmethod
    def load(cls, path) -> "Network":
        return cls.from_json(json.loads(Path(path).read_text()))


class Uniform:
    """An untrained controller: every action equally likely.  The baseline the network has to beat."""

    def __init__(self, actions=ACTION_NAMES):
        self.actions = list(actions)

    def predict(self, idx):
        return {a: 1.0 / len(self.actions) for a in self.actions}


# ---------------------------------------------------------------------- filling an action's slots

def rel_terms(words: list[str]) -> str:
    """Words left over -> a relation-by-words: 'orbiting' -> ~orbiting,orbit; 'bigger' -> ~big."""
    out = []
    for w in words:
        if w in COMPARATIVE:
            w = COMPARATIVE[w] or w
        elif w.endswith("er") and w[:-2] in DIMENSIONS:
            w = w[:-2]
        if w in ALL_UNIT_WORDS:
            continue
        out.append(w)
        if len(w) > 4 and w.endswith(("ing", "ed", "s")):
            out += [x for x in word_forms(w)[:3] if len(x) >= 3]
    out = [re.sub(r"[^a-z]", "", w) for w in out]
    out = [w for w in dict.fromkeys(out) if w]
    return "~" + ",".join(out[:6]) if out else ""


def comparative_of(r: Reading) -> tuple[str, str, bool]:
    """(relation, label, less) from a comparative in the question, or ('', '', False)."""
    for t in r.toks:
        base = COMPARATIVE.get(t)
        if base is None and t.endswith("er") and t[:-2] in DIMENSIONS:
            base = t[:-2]
        if base is None and re.search(r"(.)\1er$", t) and t[:-3] in DIMENSIONS:
            base = t[:-3]
        if base:
            return "~" + base, t, t in LESS_WORDS
    return "", "", False


def trailing(r: Reading, skip: set) -> list[str]:
    """The words after the main name, up to the first word that isn't part of a name: 'is ganymede a
    natural satellite' -> ['natural satellite'];  'is dog another word for domestic dog' -> ['domestic dog']."""
    after = [i for i in range(len(r.toks)) if i > max(skip, default=-1)]
    run = []
    for i in reversed(after):
        t = r.toks[i]
        if not re.fullmatch(r"[a-z0-9']+", t) or t in FUNCTION or t in CUE:
            if run:
                break
            continue
        run.insert(0, t)
    return [" ".join(run)] if run else []


def fills(action: str, r: Reading, limit: int = 2) -> list[str]:
    """Plans for one action, its slots filled from the question.  Most likely filling first."""
    need = NEEDS.get(action, "e1").split()
    names = sorted(r.spans, key=lambda s: (s.relish, s.soft, -(s.end - s.start), s.start))
    out = []

    def leftover(skip: set) -> list[str]:
        ws = [r.toks[i] for i in r.free if i not in skip]
        ws += [s.phrase for s in r.spans if not (set(s.tokens) & skip) and s.relish]
        ws += [s.phrase for s in r.spans if not (set(s.tokens) & skip) and not s.relish]
        return [w for w in ws if w]

    def emit(slots: dict):
        try:
            text = "\n".join(ACTIONS[action]).format(**slots)
            parse(text)
        except (KeyError, PlanError, ValueError):
            return
        if text not in out:
            out.append(text)

    if "e2" in need:          # two things, in the order they were asked
        pairs = [(a, b) for a in names for b in names if a.start < b.start]
        pairs.sort(key=lambda ab: (ab[0].relish + ab[1].relish, ab[0].soft + ab[1].soft))
        for a, b in pairs[:2]:
            skip = set(a.tokens) | set(b.tokens)
            rel, label, less = comparative_of(r)
            if not rel:
                rel = rel_terms(leftover(skip))
                label = ""
            if not rel or (action == "compare_less") != less and label:
                continue
            emit({"e1": a.phrase, "e2": b.phrase, "r": rel, "label": label})
        return out[:limit]
    firsts = names[:3]
    if "w" in need and (action.startswith(("test_", "logic_", "lex_test_"))):
        # a yes/no question is about the thing named first ("marine mammal: a kind of whale?" asks about marine
        # mammal); trying the other order can find a true fact that answers the reverse question
        own = sorted((s for s in names if not s.relish), key=lambda s: s.start)
        firsts = own[:1] or names[:1]
    for a in firsts:
        if a.relish and any(not s.relish for s in names):
            continue
        skip = set(a.tokens)
        slots = {"e1": a.phrase}
        rest = leftover(skip)
        if "w2" in need:                # two classes, in the order they were asked
            ts = [s.phrase for s in sorted((s for s in names if s is not a), key=lambda s: s.start)]
            if len(ts) >= 2:
                emit(dict(slots, w=ts[0], w2=ts[1]))
            continue
        if "w" in need or "t" in need:
            others = [s for s in names if s is not a and not s.relish] + [s for s in names if s is not a and s.relish]
            targets = trailing(r, skip) + [s.phrase for s in others]
            if not others:
                targets += [r.toks[i] for i in r.free if i not in skip][-1:]
            targets = [t for t in dict.fromkeys(targets) if t]
            if not targets:
                continue
            for tgt in targets[:2]:
                tskip = skip | {i for s in r.spans if s.phrase == tgt for i in s.tokens}
                slots2 = dict(slots, w=tgt, t=singular(tgt) if " " not in tgt else tgt)
                if "r" in need:
                    rel = rel_terms([w for w in leftover(tskip) if w != tgt])
                    if not rel:
                        continue
                    slots2["r"] = rel
                emit(slots2)
            continue
        if "r1" in need:
            ws = [(abs(i - a.start), r.toks[i]) for i in r.free if i not in skip]
            ws += [(abs(s.start - a.start), s.phrase) for s in r.spans if s is not a and s.relish]
            ws.sort()
            if len(ws) < 2:
                continue
            emit(dict(slots, r1=rel_terms([ws[0][1]]), r2=rel_terms([w for _, w in ws[1:]])))
            continue
        if "r" in need:
            rel = rel_terms(rest)
            if not rel:
                continue
            slots["r"] = rel
        elif rest and action == "isa":
            continue      # "isa" is for one name and nothing else: "what language does a wolf speak" isn't "wolf is a mammal"
        emit(slots)
    return out[:limit]


# ---------------------------------------------------------------------- the deterministic check

def accounted(r: Reading, plan: Plan, res, nav: Navigator, year: int = 0) -> list[str]:
    """The names in the question that the answer ignores.  A learned plan's answer is said only if this is empty:
    "spain's population of birds" must not come out as Spain's population."""
    used = set()
    for st in plan.steps:
        if st.op in ("FIND", "TEST", "FILTER", "RANK") and st.text:
            used |= set(st.text.split())
    rels = {f.relation for f in res.evidence}
    rw = set().union(*(words_for(nav.corpus, x) for x in rels)) if rels else set()
    vals = set()
    for f in res.evidence:
        k = key_of(f.value) if f.kind == "S" else ""
        vals |= set(k.lower().split("_")) | set(f.subject.lower().split("_"))
        if k:
            for g in nav.facts(k, year or YEAR_NOW):
                if g.relation == "IS_A":
                    vals |= set(key_of(g.value).lower().split("_"))
    units = set().union(*(UNIT_WORDS.get((f.unit or "").lower(), set()) for f in res.evidence))
    missing = []
    for s in r.spans:
        if s.soft or s.relish:
            continue
        ws = set(s.phrase.split())
        sing = {singular(w) for w in ws}
        if ws <= used or sing <= used or ws <= vals or sing <= vals or ws <= rw or any(w in used for w in ws if len(ws) > 1):
            continue
        missing.append(s.phrase)
    for i in r.free:
        w = r.toks[i]
        if w in ALL_UNIT_WORDS and w not in units:
            missing.append(w)
    return missing


# What a dictionary question asks for, by the words that ask it.  If the question asks for one of these and the
# plan never looked it up, the answer is to a different question ("the opposite of another word for happy").
LEX_ASKS = {
    "antonym": ({"opposite", "opposites", "antonym", "antonyms", "contrary"}, set()),
    "synonym": ({"synonym", "synonyms", "similar"}, {"another word", "other word", "other words", "another way",
                                                      "same meaning", "different way", "different word",
                                                      "alternative word", "alternative words"}),
    "define": ({"mean", "means", "meaning", "meanings", "definition", "definitions", "define"}, set()),
    "example": ({"sentence", "sentences"}, set()),
}
LEX_FETCHES = {"antonym": {"ANTONYM", "WN_ANTONYM"}, "synonym": {"SYNONYM", "WN_SIMILAR_TO"},
               "definition": {"DEFINITION"}, "example": {"EXAMPLE"}}
LEX_SATISFIED = {"antonym": {"ANTONYM", "WN_ANTONYM"}, "synonym": {"SYNONYM", "WN_SIMILAR_TO"},
                 "define": {"DEFINITION", "SYNONYM", "WN_SIMILAR_TO"}, "example": {"EXAMPLE"}}


def lex_unasked(r: Reading, plan: Plan, res) -> list[str]:
    """Dictionary things the question asks for that the plan never fetched."""
    toks = set(r.toks)
    pairs = {" ".join(p) for p in zip(r.toks, r.toks[1:])}
    looked = {f.relation for f in res.evidence}
    for st in plan.steps:              # a TEST that found nothing still looked
        if st.op == "GET" and st.args[1].startswith("~"):
            looked |= {x for k, rels in LEX_FETCHES.items() if k in st.args[1] for x in rels}
    out = []
    for fam, (words, bigrams) in LEX_ASKS.items():
        if (toks & words or pairs & bigrams) and not (looked & LEX_SATISFIED[fam]):
            out.append(fam)
    return out


DICTIONARY_RELS = {"DEFINITION", "SYNONYM", "WN_SIMILAR_TO", "ANTONYM", "WN_ANTONYM", "HYPERNYM", "HYPONYM",
                   "DERIVATION", "EXAMPLE"}


def dictionary_unasked(r: Reading, res, plan: Plan) -> list[str]:
    """In a full corpus every word has a definition and synonyms, so a dictionary answer is always there to be had.
    It answers the question only if the question is about the word: every content word other than the name is one
    of the dictionary cues ("mean", "opposite", "another word" are cues and never 'free').  'what language does a
    wolf speak' is not answered by the definition of wolf."""
    rels = {f.relation for f in res.evidence}
    if not rels or not rels <= DICTIONARY_RELS | {"IS_A"} or not rels & DICTIONARY_RELS:
        return []
    asked = {st.text for st in plan.steps if st.op == "FIND"}           # the word itself ("ways to say big")
    ok = {"used", "use", "using"} if "EXAMPLE" in rels else set()        # "happy used in a sentence"
    return [w for w in [r.toks[i] for i in r.free] + [s.phrase for s in r.spans if s.relish]
            if w not in asked and w not in ok]


def mixed(r: Reading, res, corpus=None) -> list[str]:
    """Relations of one thing that different words of the question picked: 'population of the capital of spain'
    read as one hop gives Spain's capital *and* Spain's population, which answers neither."""
    words = [r.toks[i] for i in r.free] + [s.phrase for s in r.spans if s.relish]
    words = [(COMPARATIVE.get(w) or w) if w in COMPARATIVE else w for w in words]     # "bigger" asks about size
    by_subject: dict[str, dict[str, set]] = {}
    for f in res.evidence:
        if f.relation == "IS_A":
            continue
        rw = words_for(corpus, f.relation) if corpus is not None else rel_words(f.relation)
        hit = {w for w in words if set(hint_words([w])) & rw or w in rw or singular(w) in rw}
        if hit:
            by_subject.setdefault(f.subject, {})[f.relation] = hit
    for subj, rels in by_subject.items():
        sets = list(rels.values())
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                if not (sets[i] & sets[j]):
                    return sorted(rels)
    return []


def connective_unasked(r: Reading, plan: Plan) -> str:
    """'or', 'neither', 'not' ... asked, but the plan tests one thing plainly: it answers a different question.
    ("is a whale a mammal or a fish" isn't answered by "No. ... nothing is both a mammal and a fish.")"""
    ops = {st.op for st in plan.steps}
    if "TEST" not in ops:
        return ""
    in_span = {i for s in r.spans for i in s.tokens}
    free = {t for i, t in enumerate(r.toks) if i not in in_span}      # r.free leaves out cue words like "or"
    if "LOGIC" in ops:
        # the other way round: a connective the question never said ("would you call X a Y" read as NOT)
        need = {"NOT": {"not", "isn't", "aren't", "wasn't", "n't"}, "OR": {"or", "either"}, "AND": {"and", "both"},
                "NOR": {"neither", "nor"}}
        for st in plan.steps:
            if st.op == "LOGIC" and not (free & need[st.args[0]] or any(t.endswith("n't") for t in free
                                                                          if st.args[0] == "NOT")):
                return f"the plan says {st.args[0]} and the question doesn't"
        return ""
    said = sorted(free & CONNECTIVES)
    names = [s for s in r.spans if not s.relish]
    if "and" in free and len(names) >= 3:
        said.append("and")
    return f"the question says {', '.join(said)} and the plan tests one thing" if said else ""


# ---------------------------------------------------------------------- the planner

class ControllerPlanner:
    """navigate()'s interface: plans(question) and accept(question, plan, result)."""
    label = "learned"

    def __init__(self, corpus: Corpus, model=None, min_p: float = 0.08, max_actions: int = 3, max_plans: int = 4):
        self.corpus = corpus
        self.nav = Navigator(corpus)
        self.reader = Reader(corpus, self.nav)
        self.model = model if model is not None else Uniform()
        self.min_p, self.max_actions, self.max_plans = min_p, max_actions, max_plans
        self.last_scores: dict[str, float] = {}
        self.last_error = ""

    @classmethod
    def from_file(cls, corpus: Corpus, path, **kw) -> "ControllerPlanner":
        return cls(corpus, Network.load(path), **kw)

    def scores(self, question: str) -> tuple[Reading, dict[str, float]]:
        r = self.reader.read(question)
        return r, self.model.predict(hashed(features(r, self.reader)))

    def plans(self, question: str) -> list[Plan]:
        if not self.corpus.ready:
            return []
        r, sc = self.scores(question)
        self.last_scores = sc
        ranked = sorted(sc.items(), key=lambda kv: -kv[1])
        out = []
        for a, p in ranked[: self.max_actions]:
            # Below the floor, only the top action may be tried, and only as the first try: when it can't be filled
            # from the question, a 0.01 action is a guess ("birdhouse kinds" answered with birdhouse's definition).
            if p < self.min_p and (out or a != ranked[0][0]):
                break
            texts = fills(a, r)
            if a == "hop":        # if the thing itself has nothing, what it's a kind of may (said as inherited)
                texts = [t for x in texts for t in (x, x.replace("\nANSWER", " INHERIT\nANSWER", 1))]
            for text in texts:
                pl = parse(text)
                pl.kind, pl.source = "learned", f"{self.label}:{a} {p:.2f}"
                if str(pl) in {str(x) for x in out}:
                    continue
                out.append(pl)
                if len(out) >= self.max_plans:
                    return out
        if not out:
            self.last_error = "no action could be filled from the names in the question"
        return out

    def accept(self, question: str, plan: Plan, res) -> str:
        """'' if the answer may be said, else why not."""
        r = self.reader.read(question)
        miss = accounted(r, plan, res, self.nav)
        if miss:
            return f"the answer ignores {', '.join(miss)}"
        stray = dictionary_unasked(r, res, plan)
        if stray:
            return f"a dictionary answer to a question about {', '.join(stray)}, not about the word"
        lex = lex_unasked(r, plan, res)
        if lex:
            return f"the question asks for {', '.join(lex)} and the plan never looked it up"
        way = wrong_way(r, plan, res, self.corpus)
        if way:
            return way
        mix = mixed(r, res, self.corpus)
        if mix:
            return f"one step mixed {' and '.join(mix)}: the question needs them in order"
        conn = connective_unasked(r, plan)
        if conn:
            return conn
        return ""


# ---------------------------------------------------------------------- what the rules' plans were

def action_of(plan: Plan | str) -> str | None:
    """A rule plan -> the action it is an instance of (for teacher labels).  None if it's not one of them."""
    p = parse(plan) if isinstance(plan, str) else plan
    ops = [s.op for s in p.steps if s.op not in ("CHECK", "RANK", "EXCEPT")]
    rel = lambda i: p.steps[i].args[1]
    lex = {v: k for k, v in LEX.items()}
    if ops == ["FIND", "GET", "ANSWER"]:
        r = rel(1)
        if r == "IS_A":
            return "isa"
        if r in lex:
            return lex[r]
        return "hop_inherit" if "INHERIT" in p.steps[1].flags else "hop"
    if ops == ["FIND", "GET", "GET", "ANSWER"]:
        if (rel(1), rel(2)) == (LEX["antonym"], LEX["define"]):
            return "lex_chain"
        return "chain" if rel(1) not in lex and rel(2) not in lex else None
    if ops == ["FIND", "GET", "TEST", "ANSWER"]:
        r = rel(1)
        return {"IS_A": "test_isa", LEX["hypernym"]: "test_hypernym", "~located,part": "test_located",
                "*": "test_have", LEX["synonym"]: "lex_test_synonym", LEX["antonym"]: "lex_test_antonym"}.get(r, "test_rel")
    if ops == ["FIND", "GET", "TEST", "LOGIC", "ANSWER"] and p.steps[3].args[0] == "NOT":
        return "logic_not"
    if ops == ["FIND", "GET", "TEST", "TEST", "LOGIC", "ANSWER"] and p.steps[4].args[0] in ("AND", "OR", "NOR"):
        return "logic_" + p.steps[4].args[0].lower()
    if ops == ["FIND", "FIND", "GET", "GET", "COMPARE", "ANSWER"]:
        return "compare_less" if p.steps[4].args[2] == "LESS" else "compare_more"
    if ops == ["FIND", "WHO", "ANSWER"]:
        return "hyponym_who" if rel(1) == LEX["hypernym"] else "reverse"
    if ops == ["FIND", "WHO", "FILTER", "ANSWER"]:
        return "reverse_filter"
    if ops == ["FIND", "GET", "COUNT", "ANSWER"] and rel(1) not in lex:
        return "count"
    return None
