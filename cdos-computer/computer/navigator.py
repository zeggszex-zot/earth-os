"""The Navigator: runs a plan against the CDOS Corpus and keeps the evidence.

    question --planner--> plan (computer/plan.py) --validator--> --executor (this file)--> answer + evidence

The planner may be rules (computer/planner.py) or a small model.  Either way the plan is
checked before anything runs, and the executor is the only thing that touches the
corpus.  Every sentence in the answer is rendered from a fact the executor fetched
(phrase.py templates), so a planner can choose the wrong path but cannot invent a fact:
the worst it can do is make the computer say "my corpus doesn't say".

Bounded on purpose (a Pi Zero, and no endless searches): at most MAX_STEPS steps,
FANOUT entities per step, INHERIT_DEPTH levels of IS_A, BACKTRACKS retries.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .clock import current_year
from .corpus import choose_entity  # noqa: E402
from .corpus import (HINT_ALIASES, NOT_KNOWLEDGE, SOURCE_RANK, applicable, LEXICAL_RELS, META, STOP, Corpus, Fact, expand_hints, key_of, rank_by_overlap,
                     rel_words)
from .phrase import clean_definition, listing, name_of, phrase_block, rel_text, sentence, unit_of, value_of
from .quantity import fmt_num, fmt_plain, say as say_quantity, same_dimension, unit_dimension, unit_factor
from .quantity import Quantity
from .analysis import BASE_CODE, DIM_NOUN, measure
from . import logic
from .logic import CONFLICT, FALSE, TRUE, UNKNOWN
from .plan import MAX_STEPS, Plan, PlanError, Step, parse
from .rules import Knowledge, default_knowledge

FANOUT = 8
LESS_LABELS = {"smaller", "lighter", "shorter", "lower", "younger", "colder", "slower", "closer", "nearer"}
INHERIT_DEPTH = 4
BACKTRACKS = 6
MIN_CONF = 0.5

# Extra hint words for comparisons and questions ("how big" -> radius/area ...), in priority order.
DIMENSIONS = {
    "size": ["radius", "diameter", "area", "length", "size", "height"],
    "big": ["radius", "diameter", "area", "length", "size"], "large": ["radius", "diameter", "area", "size"],
    "small": ["radius", "diameter", "area", "length", "size"], "heavy": ["mass", "weight"],
    "light": ["mass", "weight"], "tall": ["height", "elevation"], "short": ["height", "length"],
    "long": ["length"], "old": ["inception", "founded", "born", "date"], "new": ["inception", "founded", "born"],
    "young": ["born", "inception"], "hot": ["temperature"], "cold": ["temperature"], "far": ["distance"],
    "near": ["distance"], "close": ["distance"], "fast": ["speed"], "slow": ["speed"],
    "populous": ["population"], "people": ["population"], "high": ["height", "elevation"],
    "deep": ["depth"], "wide": ["width", "area"],
}


# Extra words a relation answers to, beyond its own name.  "~isa,hypernym" is "what kind of thing":
# the curated IS_A and WordNet's HYPERNYM.
REL_ALIASES = {"IS_A": {"isa"}}
TAXONOMY = ("IS_A", "HYPERNYM")
# Merged when spoken: one sentence for "other words for happy", not one per relation.
SAY_AS = {"WN_SIMILAR_TO": "SYNONYM", "WN_ANTONYM": "ANTONYM"}
# Word-to-word links a name can be found through when it isn't a subject itself ("joyful" -> happy).
SOFT_LINKS = {"SYNONYM": "another word for", "WN_SIMILAR_TO": "a word like", "DERIVATION": "a word related to"}
# What a dictionary relation is called when the computer talks about it.
LEX_NAME = {"DEFINITION": "definition", "SYNONYM": "synonym", "WN_SIMILAR_TO": "synonym", "ANTONYM": "antonym",
            "WN_ANTONYM": "antonym", "HYPERNYM": "hypernym", "HYPONYM": "hyponym", "DERIVATION": "derivation",
            "EXAMPLE": "example", "IS_A": "isa"}
LEX_MISSING = {"definition": "what {w} means", "meaning": "what {w} means", "synonym": "another word for {w}",
               "similar": "another word for {w}", "antonym": "an opposite for {w}", "opposite": "an opposite for {w}",
               "isa": "what kind of thing {w} is", "hypernym": "what kind of thing {w} is",
               "hyponym": "any kinds of {w}", "derivation": "any words related to {w}",
               "example": "an example sentence with {w}"}
LEX_PLURAL = {"definition": "meanings", "synonym": "other words", "antonym": "opposites", "hypernym": "broader words",
              "hyponym": "kinds", "derivation": "related words", "example": "examples"}
# Word endings to try when a word isn't in the corpus as written: (ending, replacement).
ENDINGS = [("iness", "y"), ("ness", ""), ("iest", "y"), ("ier", "y"), ("ies", "y"), ("ied", "y"), ("ily", "y"),
           ("est", ""), ("er", ""), ("ing", ""), ("ing", "e"), ("ed", ""), ("ed", "e"), ("ly", ""), ("ves", "f"),
           ("es", ""), ("s", "")]


def strip_the(phrase: str) -> str:
    return re.sub(r"^(?:the|a|an)\s+", "", phrase.lower().strip())


def word_forms(word: str) -> list[str]:
    """happier -> happy, biggest -> big, running -> run, making -> make.  Candidates only: the
    caller keeps the ones the corpus really has."""
    out = []
    for end, new in ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= 2:
            base = word[: -len(end)] + new
            out.append(base)
            if not new and len(base) >= 3 and base[-1] == base[-2] and base[-1] not in "aeiouls":
                out.append(base[:-1])
    return list(dict.fromkeys(out))


def edits1(word: str) -> set[str]:
    """Every spelling one letter away (colour -> color)."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    return ({a + b[1:] for a, b in splits if b} | {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
            | {a + c + b[1:] for a, b in splits if b for c in letters} | {a + c + b for a, b in splits for c in letters})


def hint_words(words) -> list[str]:
    """Ordered hint words for a relation phrase: dimension words first, then corpus aliases, then the words."""
    out = []
    for w in words:
        out += DIMENSIONS.get(w, [])
    for w in sorted(expand_hints(words), key=lambda h: (h not in words, h)):
        if w not in out:
            out.append(w)
    return out


@dataclass
class Item:
    key: str                    # entity key (FIND / WHO) or the value's key (GET)
    fact: Fact | None = None    # the supporting fact (None for FIND)
    via: list = field(default_factory=list)   # IS_A path when inherited: [WHALE, MARINE_MAMMAL, MAMMAL]
    note: tuple = ()            # FIND only: how a name not in the corpus as written was found ("form", "colour")


@dataclass
class Binding:
    step: Step
    items: list[Item]
    note: str = ""
    verdict: dict | None = None   # COMPARE / COUNT / TEST / LOGIC result
    conflict: dict | None = None  # GET: more values than a knowledge rule allows (computer/data/rules.json)
    said: list = field(default_factory=list)   # sentences this step contributes to the answer


#: Words in a name's phrase that never pick out which thing it is ("the city of luxembourg": of)
HINT_STOP = frozenset({"the", "a", "an", "of", "in", "at", "on", "near", "called", "named"})


@dataclass
class Result:
    status: str                     # "answered" | "unsupported" | "conflict" | "no_entity" | "invalid"
    text: str = ""
    plan: Plan | None = None
    steps: list = field(default_factory=list)      # human-readable trace of each step
    evidence: list = field(default_factory=list)   # Fact objects the answer rests on
    confidence: float | None = None
    subjects: list = field(default_factory=list)   # what the plan FOUND (for "it" in the next question)
    final: dict = field(default_factory=dict)      # what ANSWER pointed at, for checking a model's wording
    mapped: list = field(default_factory=list)     # lexicon mappings it relied on ("food" -> DIET), with their evidence
    rules: list = field(default_factory=list)      # knowledge rules a FALSE or CONFLICT rests on (rule ids)
    truth: str = ""                                # TRUE / FALSE / UNKNOWN / CONFLICT for yes/no questions
    inherited: list = field(default_factory=list)  # per evidence fact: the IS_A path it was inherited through, or []
    derived: list = field(default_factory=list)    # CALC steps: {"op", "expr", "result", "unit", "facts": [ids]}
    year: int = 0                                  # the year facts had to hold in (an input: the envelope records it)
    errors: list = field(default_factory=list)     # structured errors (computer/capabilities.py ERROR_CODES)
    inferences: list = field(default_factory=list) # steps that make the answer INFERRED, not ESTABLISHED (computer/envelope.py)
    notes: list = field(default_factory=list)      # how the planner read a name, said before the answer
    assumptions: list = field(default_factory=list)  # conditions the answer depends on that the evidence doesn't establish
    fact_derived: dict = field(default_factory=dict) # fact id -> how it was used ("energy = charge x voltage")
    ms: int = 0

    @property
    def answered(self) -> bool:
        return self.status == "answered"


class Unsupported(Exception):
    def __init__(self, msg, status="unsupported", error: dict | None = None):
        super().__init__(msg)
        self.status = status
        self.error = error


_SHARED: list = []


def shared_registry():
    """The declared capabilities, validated once, for navigators made without a computer (tools, tests)."""
    if not _SHARED:
        from .capabilities import default_registry
        _SHARED.append(default_registry())
    return _SHARED[0]


class Navigator:
    def __init__(self, corpus: Corpus, min_conf: float = MIN_CONF, knowledge: Knowledge | None = None, registry=None):
        self.corpus = corpus
        self.min_conf = min_conf
        self.knowledge = knowledge if knowledge is not None else default_knowledge()
        self.registry = registry
        self.rules_used: list[str] = []
        self.errors: list[dict] = []
        self.inferences: list[str] = []
        self.assumptions: list[str] = []
        self.fact_derived: dict = {}
        self._cache: dict[tuple, list[Fact]] = {}
        self.mapped: list = []          # lexicon mappings used by the plan being run
        self.hints: dict[str, frozenset] = {}     # key -> the question's other words about it ("illinois", "state")
        self.choices: dict = {}                   # key -> corpus.Choice: which of a name's entities was meant (D4)

    # ------------------------------------------------------------------ corpus access

    def facts(self, key: str, year: int) -> list[Fact]:
        """Facts that hold in `year`, latest value only for dated numbers (same rules as Corpus.ask)."""
        hints = self.hints.get(key, frozenset())
        ck = (key, year, hints)
        if ck in self._cache:
            out, choice = self._cache[ck]
        else:
            # One name, several things (ENTITY=): the entity first, then the time policy, so one thing's dated
            # figures never hide another's (computer/corpus.py choose_entity, applicable).
            raw, choice = choose_entity(key, self.corpus.facts_for(key), hints, self.corpus.place_kind)
            out = applicable(raw, year, self.min_conf)
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[ck] = (out, choice)
        if choice:
            self.choices[key] = choice
        return out

    def resolve(self, phrase: str) -> list[str]:
        """Name -> ranked entity keys.  The whole phrase first, then the names inside it."""
        words = [re.sub(r"'s$", "", w) for w in re.findall(r"[a-z0-9][a-z0-9'\-]*", phrase.lower())]
        while words and words[0] in ("the", "a", "an"):
            words = words[1:]
        out = []
        whole = "_".join(words).upper()
        for k in (whole, whole[:-1] if whole.endswith("S") else "", whole[:-2] if whole.endswith("ES") else "",
                  whole[:-3] + "Y" if whole.endswith("IES") else ""):
            if k and self.corpus.has(k) and k not in out:
                out.append(k)
                self.hints[k] = frozenset()          # named whole: nothing else in the phrase says which thing
        cands = sorted(self.corpus.names_in(words), key=lambda c: (-c["n"], -c["pos"]))
        vocab = self.corpus.rel_vocab()
        for c in cands:
            if c["key"] in out:
                continue
            if c["n"] == 1 and (c["words"][0] in vocab or c["words"][0] in HINT_ALIASES or c["words"][0] in META):
                continue
            out.append(c["key"])
            # The phrase's other words may say which of the name's things is meant ("springfield illinois")
            self.hints[c["key"]] = frozenset(w for w in words if w not in c["words"] and w not in HINT_STOP)
        # A name whose things the rest of the phrase tells apart is the one meant: in "springfield, illinois" that's
        # Springfield (in Illinois), not Illinois, though both are names.
        for k in out[1:]:
            if self.hints.get(k) and (lambda ch: ch and ch.by == "hint")(choose_entity(k, self.corpus.facts_for(k),
                                                                                       self.hints[k])[1]):
                out.remove(k)
                out.insert(0, k)
                break
        return out[:FANOUT]

    def soft_resolve(self, phrase: str) -> Item | None:
        """When a name isn't in the corpus as written: another form of the word (happier -> happy),
        a word the corpus lists it under (joyful is another word for happy), or one spelling slip
        (colour -> color, only when exactly one word is that close).  Returns the entity with a note
        (and, for a listed word, the fact that links them), or None.  It finds; it never invents."""
        words = [re.sub(r"'s$", "", w) for w in re.findall(r"[a-z0-9][a-z0-9'\-]*", phrase.lower())]
        while words and words[0] in ("the", "a", "an", "word"):
            words = words[1:]
        if not words or len(words) > 4:
            return None
        head, last = words[:-1], words[-1]
        key = lambda w: "_".join(head + [w]).upper()
        for w in word_forms(last):
            k = key(w)
            if len(w) >= 3 and self.corpus.has(k):
                return Item(k, None, note=("form", " ".join(words)))
        whole = key(last)
        for f in self.corpus.reverse_facts(whole, current_year(), self.min_conf, SOFT_LINKS):
            if self.corpus.has(f.subject):
                return Item(f.subject, f, note=("listed", " ".join(words)))
        if not head and len(last) >= 4 and last not in STOP:
            near = sorted(k for k in (w.upper() for w in edits1(last)) if self.corpus.has(k))
            if len(near) == 1:
                return Item(near[0], None, note=("spelling", last))
        return None

    def match_relations(self, facts: list[Fact], rel: str) -> list[str]:
        """Relations among `facts` that `rel` names, in priority order."""
        present = list(dict.fromkeys(f.relation for f in facts))
        if rel == "*":
            return present
        if not rel.startswith("~"):
            return [rel] if rel in present else []
        hints = hint_words(rel[1:].split(","))
        scored = []
        for r in present:
            rw = rel_words(r) | REL_ALIASES.get(r, set())
            pos = [i for i, h in enumerate(hints) if h in rw]
            if pos:
                scored.append((min(pos), r))
        if not scored and getattr(self.corpus, "lexicon", None):
            # Nothing is named that way.  The learned lexicon may know the word ("food" -> DIET); it is only
            # a pointer to a relation this thing really has, and the mapping is recorded with the step.
            for i, w in enumerate(rel[1:].split(",")):
                ms = [m for m in self.corpus.lexicon.lookup(w) if m.relation in present]
                if len(ms) > 1 and ms[0].status == "ambiguous" and ms[1].status == "ambiguous":
                    continue          # this thing has both relations the word could mean: don't pick one
                for m in ms[:1]:
                    scored.append((100 + i, m.relation))
                    if m not in self.mapped:
                        self.mapped.append(m)
        return [r for _, r in sorted(scored)]

    def value_matches(self, facts: list[Fact], rel: str) -> list[Fact]:
        """Facts whose *value* carries the words ("whale SURFACES_TO breathe air" for ~breathe)."""
        if not rel.startswith("~"):
            return []
        hints = set(hint_words(rel[1:].split(",")))
        return [f for f in facts if f.kind in ("S", "T") and len(f.value) <= 40 and f.relation != "IS_A"
                and any(len(w) > 2 and w in hints for w in key_of(f.value).lower().split("_"))]

    def ancestors(self, key: str, year: int, relation: str | tuple = "IS_A") -> list[list[str]]:
        """IS_A paths upward, nearest first: [[WHALE, MARINE_MAMMAL], [WHALE, MARINE_MAMMAL, MAMMAL], ...].
        `relation` may be a tuple: TAXONOMY walks IS_A and WordNet's HYPERNYM together."""
        rels = (relation,) if isinstance(relation, str) else tuple(relation)
        out, frontier, seen = [], [[key]], {key}
        for _ in range(INHERIT_DEPTH):
            nxt = []
            for path in frontier:
                for f in self.facts(path[-1], year):
                    if f.relation in rels and f.kind == "S":
                        k = key_of(f.value)
                        if k not in seen:
                            seen.add(k)
                            nxt.append(path + [k])
            out += nxt
            frontier = [p for p in nxt if self.corpus.has(p[-1])][:FANOUT]
        return out

    # ------------------------------------------------------------------ running a plan

    def run(self, plan: Plan | str, year: int = 0) -> Result:
        t0 = time.monotonic()
        if isinstance(plan, str):
            try:
                plan = parse(plan, set((self.corpus.meta or {}).get("relation_names", [])) or None)
            except PlanError as e:
                return Result("invalid", f"bad plan: {e}", steps=[str(e)])
        year = year or current_year()      # the caller should pass it; the envelope records it
        env: dict[str, Binding] = {}
        trace, budget = [], {"backtracks": BACKTRACKS}
        self.mapped = []
        self.rules_used = []
        self.errors, self.inferences, self.assumptions, self.fact_derived = [], [], [], {}
        self.choices = {}
        self.hints = {}            # a question's words about a name are that question's: nothing carries over (D4)
        self._use("navigator.execute")
        self._use("corpus.lookup")
        res = Result("answered", plan=plan, year=year, notes=list(getattr(plan, "notes", []) or []))
        try:
            for i, st in enumerate(plan.steps[:MAX_STEPS + 1]):
                if st.op == "ANSWER":
                    res.text = self._render(plan, env, st)
                    last = env[st.args[-1]]
                    res.final = self._final(last)
                    clash = next((b for b in env.values() if b.conflict), None)
                    if clash:
                        res.status, res.truth = "conflict", CONFLICT
                        res.text = self._say_conflict_get(clash)
                    elif last.step.op in ("TEST", "LOGIC", "REQUIRE", "SELECT"):
                        res.truth = last.verdict["truth"]
                        # TRUE and FALSE are answers; "I can't find that link" is neither; a conflict is said as one
                        res.status = {TRUE: "answered", FALSE: "answered", UNKNOWN: "unsupported",
                                      CONFLICT: "conflict"}[res.truth]
                    elif any(b.verdict and b.verdict.get("yes") is False for b in env.values()):
                        res.status = "unsupported"      # "I can't find that link" is not a yes or a no
                    break
                before = len(self.mapped)
                b = getattr(self, "_op_" + st.op.lower())(st, env, year, budget)
                for m in self.mapped[before:]:
                    b.note = (b.note + "; " if b.note else "") + f"took {m.short()}"
                if st.out:
                    env[st.out] = b
                trace.append(f"{st}  ->  {self._show(b)}" + (f"  ({b.note})" if b.note else ""))
        except Unsupported as e:
            trace.append(f"{st}  ->  stop: {e}")
            res.status, res.text = e.status, str(e)
            if e.error:
                self.errors.append(e.error)
        if res.notes and res.text:
            res.text = " ".join(res.notes) + " " + res.text
        res.steps = trace + [s for b in env.values() if b.step.op in ("MEASURE", "REQUIRE") and b.verdict
                             for s in b.verdict.get("steps", [])]
        res.errors = list(self.errors)
        res.assumptions = list(dict.fromkeys(self.assumptions))
        res.fact_derived = dict(self.fact_derived)
        res.mapped = list(self.mapped)
        res.rules = list(dict.fromkeys(self.rules_used))
        res.derived = [b.verdict["record"] for b in env.values() if b.step.op == "CALC" and b.verdict]
        res.subjects = [b.items[0].key for b in env.values() if b.step.op == "FIND" and b.items]
        # Said once, after the answer: which of a name's things it was about, and the others (D4)
        said = [c.note() for k, c in self.choices.items() if k in res.subjects and c.note()]
        if said and res.text and res.status == "answered":
            res.text = res.text + " " + " ".join(said)
        ev = self._evidence_via(env)
        res.evidence = [f for f, _ in ev]
        res.inherited = [via for _, via in ev]
        inf = list(self.inferences)
        inf += [f"inherited through {' > '.join(v)}" for v in res.inherited if v]
        inf += [f"word mapped by the lexicon: {m.short()}" for m in res.mapped]
        inf += [f"knowledge rule {r}" for r in res.rules if not r.startswith(("unit.", "functional."))]
        for b in env.values():
            v = b.verdict or {}
            if b.step.op == "TEST" and v.get("truth") == TRUE and (v.get("chain") or v.get("trail") or v.get("kind_of")):
                inf.append(f"followed a chain to {b.step.text}")
            if b.step.op == "CALC" and v.get("op") == "DIV":
                inf.append("a formula combining different relations (DIV)")
        res.inferences = list(dict.fromkeys(inf))
        confs = [f.conf for f in res.evidence]
        res.confidence = min(confs) if confs else None
        res.ms = int((time.monotonic() - t0) * 1000)
        return res

    @staticmethod
    def _final(b: Binding) -> dict:
        """The result in a form a checker can hold a model's sentence to."""
        v = b.verdict or {}
        if b.step.op == "COMPARE":
            (ka, *_), (kb, *_) = v["a"], v["b"]
            win = v["winner"]
            lose = None if win is None else (kb if win == ka else ka)
            return {"kind": "compare", "winner": win and name_of(win), "loser": lose and name_of(lose),
                    "label": v.get("label") or ""}
        if b.step.op == "COUNT":
            return {"kind": "count", "values": [num_text(v["count"])]}
        if b.step.op == "CALC":
            return {"kind": "calc", "values": [num_text(v["said"])], "op": v["op"], "derived": True}
        if b.step.op == "REQUIRE":
            q = v["measurement"].quantity
            return {"kind": "test", "yes": v.get("truth") == TRUE, "truth": v.get("truth", UNKNOWN),
                    "value": None if q is None else {"value": q.value, "unit": BASE_CODE[q.dim]}}
        if b.step.op == "SELECT":
            return {"kind": "select", "selected": [name_of(k) for k in v["selected"]], "verdicts": dict(v["verdicts"]),
                    "truth": v.get("truth", UNKNOWN)}
        if b.step.op == "MEASURE":
            q = v["measurement"].quantity
            return {"kind": "values", "values": [] if q is None else [num_text(fmt_plain(q.value))]}
        if b.step.op in ("TEST", "LOGIC"):
            return {"kind": "test", "yes": v.get("truth") == TRUE, "truth": v.get("truth", UNKNOWN)}
        vals = []
        for it in b.items:
            f = it.fact
            if b.step.op in ("FIND", "WHO", "FILTER") or f is None:
                vals.append(name_of(it.key))
            elif f.kind == "N":
                vals.append(num_text(f.value))
            elif f.kind == "D":
                vals.append(re.match(r"-?\d+", f.value.strip()).group(0).lstrip("-") if re.match(r"-?\d+", f.value.strip()) else f.value)
            elif f.relation == "DEFINITION":
                vals.append(clean_definition(f.value))
            else:
                vals.append(value_of(f))
        return {"kind": "values", "values": list(dict.fromkeys(vals))}

    @staticmethod
    def _show(b: Binding) -> str:
        if b.verdict:
            return str(b.verdict.get("show", ""))
        names = [i.key for i in b.items[:5]]
        return ", ".join(names) + (f" (+{len(b.items) - 5})" if len(b.items) > 5 else "") if names else "nothing"

    def _need(self, env, var) -> Binding:
        b = env[var]
        if not b.items and not b.verdict:
            raise Unsupported(f"nothing for {var}")
        return b

    def _op_find(self, st, env, year, budget):
        keys = self.resolve(st.text)
        if not keys:
            soft = self.soft_resolve(st.text)
            if soft:
                how, said = soft.note
                return Binding(st, [soft], note=f"not in the corpus as written; {how}: {soft.key}")
            # Not a subject, but other facts point at it ("krill": BLUE_WHALE DIET KRILL).  It's a real name in the
            # corpus's reverse index, so WHO can start from it; GET will find it has nothing of its own.
            k = "_".join(re.findall(r"[a-z0-9][a-z0-9'\-]*", strip_the(st.text))).upper()
            for cand in (k, k[:-1] if k.endswith("S") else ""):
                if cand and self.corpus.reverse(cand):
                    return Binding(st, [Item(cand)], note="known only as a value: what points at it")
            raise Unsupported(f"I couldn't find {st.text} in my corpus.", "no_entity")
        return Binding(st, [Item(k) for k in keys], note=f"{len(keys)} candidate(s)" if len(keys) > 1 else "")

    def _op_rank(self, st, env, year, budget):
        """Order values (word senses) by the words they share with st.text.  Ties keep the corpus
        order.  If nothing shares a word, the context is widened once through the corpus's own
        synonyms of those words; if that finds nothing either, every value is kept."""
        src = self._need(env, st.args[0])
        texts = [it.fact.value if it.fact else str(it.key) for it in src.items]
        order, scores = rank_by_overlap(texts, st.text)
        how = ""
        if not any(scores):
            wider = [value_of(f) for w in st.text.split() for f in self.facts(key_of(w), year)
                     if f.relation in ("SYNONYM", "WN_SIMILAR_TO", "HYPERNYM")][:24]
            order, scores = rank_by_overlap(texts, " ".join(wider))
            how = " (through related words)"
        if not any(scores):
            return Binding(st, src.items, note=f"nothing matched {st.text!r}: kept all {len(src.items)}")
        best = scores[order[0]]
        keep = [src.items[i] for i in order if scores[i] == best]
        return Binding(st, keep, note=f"kept {len(keep)} of {len(src.items)} matching {st.text!r}{how}")

    def _op_except(self, st, env, year, budget):
        src = self._need(env, st.args[0])
        drop = {key_of(w) for w in st.text.split(",") if w.strip()}
        keep = [it for it in src.items if key_of(str(it.key)) not in drop]
        if not keep:
            f = next((it.fact for it in src.items if it.fact), None)
            what = LEX_PLURAL.get(LEX_NAME.get(f.relation if f else "", ""), "")
            if f and what:
                raise Unsupported(f"That's all the {what} my corpus has for {name_of(f.subject).lower()}.")
            if f:
                raise Unsupported(f"My corpus doesn't list any other {rel_text(f.relation)} for {name_of(f.subject)}.")
            raise Unsupported("That's everything my corpus lists.")
        return Binding(st, keep, note=f"left out {len(src.items) - len(keep)} already said")

    def _get_one(self, key, rel, inherit, year) -> list[Item]:
        mk = lambda f, via=(): Item(key_of(f.value) if f.kind == "S" else f.value, f, list(via))
        facts = self.facts(key, year)
        rels = self._narrow(self.match_relations(facts, rel), rel)
        own = [mk(f) for f in facts if f.relation in rels]
        vals = [mk(f) for f in self.value_matches(facts, rel) if f.relation not in rels]
        if inherit and rel == "*":
            for path in self.ancestors(key, year):
                own += [mk(f, path) for f in self.facts(path[-1], year) if f.relation != "DEFINITION"]
        elif inherit and not own:
            for path in self.ancestors(key, year):      # the nearest ancestor that has it
                af = self.facts(path[-1], year)
                arels = self._narrow(self.match_relations(af, rel), rel)
                got = [mk(f, path) for f in af if f.relation in arels]
                if got:
                    own = got
                    break
        return own + vals

    @staticmethod
    def _narrow(rels: list[str], rel: str) -> list[str]:
        """A size word ("~big") means one measurement, the best one; other words keep every match."""
        if rel.startswith("~") and any(w in DIMENSIONS for w in rel[1:].split(",")):
            return rels[:1]
        return rels

    def _op_get(self, st, env, year, budget):
        src = self._need(env, st.args[0])
        rel, inherit = st.args[1], "INHERIT" in st.flags
        from_find = src.step.op == "FIND"
        out, note = [], ""
        for n, it in enumerate(src.items[:FANOUT]):
            got = self._get_one(it.key, rel, inherit, year)
            if from_find:
                if got:
                    if n:
                        note = f"backtracked: {src.items[0].key} had no {rel}, used {it.key}"
                    src.items = [it]          # commit: later steps mean this candidate
                    out = got
                    break
                if n + 1 >= len(src.items) or budget["backtracks"] <= 0:
                    break
                budget["backtracks"] -= 1
                continue
            out += got
        if not out:
            raise Unsupported(self._missing(src.items[0].key if src.items else "", rel))
        return Binding(st, out, note, conflict=self._too_many(out))

    def _too_many(self, items) -> dict | None:
        """A thing's own values for a relation a rule caps ("a body orbits one thing"), over the cap."""
        by = {}
        for it in items:
            f = it.fact
            rule = f and not it.via and self.knowledge.complete.get(f.relation)
            if rule and rule.max:
                by.setdefault((f.subject, f.relation), {})[key_of(f.value) if f.kind == "S" else f.value] = f
        for (subj, rel), vals in by.items():
            rule = self.knowledge.complete[rel]
            if len(vals) > rule.max:
                self.rules_used.append(rule.rule_id)
                return {"subject": subj, "relation": rel, "facts": list(vals.values()), "rule": rule}
        return None

    def _op_who(self, st, env, year, budget):
        src = self._need(env, st.args[0])
        rel = st.args[1]
        for it in src.items[:FANOUT]:
            # Match the relation on the v rows, then cite the stored rows themselves (real id and source).
            pseudo = [Fact(r["subject"], r["relation"], "S", it.key, conf=r["conf"]) for r in self.corpus.reverse(it.key)]
            rels = self.match_relations(pseudo, rel)
            hits = [Item(f.subject, f) for f in self.corpus.reverse_facts(it.key, year, self.min_conf, rels)] if rels else []
            # A relation that holds both ways: the thing's own rows answer too ("France SHARES_BORDER_WITH Monaco"
            # says Monaco borders France), each cited as stored; the rule is recorded with the answer.
            for r in rels or [rel]:
                sym = self.knowledge.symmetric.get(r)
                if sym:
                    own = [Item(key_of(f.value), f) for f in self.facts(it.key, year) if f.relation == r]
                    if own:
                        hits += own
                        self.rules_used.append(sym.rule_id)
            if hits:
                if src.step.op == "FIND":
                    src.items = [it]
                return Binding(st, list({h.key: h for h in hits}.values()))
        # Nobody points at it.  Maybe the question meant the thing's own relation ("who founded X").
        if budget["backtracks"] > 0 and src.items:
            budget["backtracks"] -= 1
            got = self._get_one(src.items[0].key, rel, False, year)
            if got:
                return Binding(st, got, note="nothing points here; used its own relation instead")
        raise Unsupported(f"My corpus doesn't say what {rel_phrase(rel)} {name_of(src.items[0].key)}.")

    def _op_filter(self, st, env, year, budget):
        src = self._need(env, st.args[0])
        target = self._target(st.text)
        keep = [it for it in src.items if self._is_a(it.key, target, year) is not None]
        if not keep:
            raise Unsupported(f"None of those are {st.text} in my corpus.")
        return Binding(st, keep, note=f"kept {len(keep)} of {len(src.items)}")

    def _op_count(self, st, env, year, budget):
        src = self._need(env, st.args[0])
        nums = [i for i in src.items if i.fact and i.fact.kind == "N"]
        if nums:
            f = nums[0].fact
            return Binding(st, [nums[0]], verdict={"count": f.value, "stated": True, "show": f.value})
        keys = list(dict.fromkeys(i.key for i in src.items))
        return Binding(st, src.items, verdict={"count": str(len(keys)), "stated": False, "show": len(keys)})

    def _op_test(self, st, env, year, budget):
        """TRUE if the corpus links them, FALSE if a knowledge rule rules it out, CONFLICT if both, else UNKNOWN."""
        src = self._need(env, st.args[0])
        target = self._target(st.text)
        yes = self._test_yes(st, src, target, year)
        no = self._test_no(src, target, year)
        truth = logic.both(yes is not None, no is not None)
        if no:
            self.rules_used.append(no["rule"].rule_id)
        if truth == TRUE:
            yes.verdict["truth"] = TRUE
            return yes
        if truth == FALSE:
            return Binding(st, no["items"], verdict={"yes": False, "truth": FALSE, "target": st.text, "no": no,
                                                     "show": f"no ({no['rule'].rule_id})"})
        if truth == CONFLICT:
            return Binding(st, yes.items + no["items"], verdict={"yes": False, "truth": CONFLICT, "target": st.text,
                                                                 "for": yes, "no": no,
                                                                 "show": f"conflict ({no['rule'].rule_id})"})
        return Binding(st, src.items, verdict={"yes": False, "truth": UNKNOWN, "target": st.text, "show": "not found"})

    def _test_no(self, src, target, year) -> dict | None:
        """A knowledge rule that rules the link out (computer/data/rules.json), with what it rests on."""
        k = self.knowledge
        isa = [it for it in src.items if it.fact and it.fact.relation == "IS_A" and not it.via]
        if isa and k.disjoint:
            # Only the curated IS_A: WordNet's senses ("dog" the frump) would clash with everything.
            subj = isa[0].fact.subject
            mine = {}
            for it in isa:
                mine.setdefault(it.key, [subj, it.key])
                for path in self.ancestors(it.key, year):
                    mine.setdefault(path[-1], [subj] + path)
            theirs = {}
            for t in sorted(target):
                if self.corpus.has(t) or any(t in d.classes for d in k.disjoint):
                    theirs.setdefault(t, [t])
                    for path in self.ancestors(t, year):
                        theirs.setdefault(path[-1], path)
            hit = k.disjoint_pair(set(mine), set(theirs))
            if hit:
                rule, a, b = hit
                return {"rule": rule, "kind": "disjoint", "mine": mine[a], "theirs": theirs[b], "a": a, "b": b,
                        "items": isa}
        facts = [it.fact for it in src.items if it.fact]
        rels = {f.relation for f in facts}
        own = [it for it in src.items if it.fact and not it.via and it.fact.kind == "S"]
        # Only when the step asked for that one relation: "*" (every relation) or a mix proves nothing.
        if (own and len(rels) == 1 and src.step.op == "GET" and src.step.args[1] != "*"
                and own[0].fact.relation in k.complete):
            rel = own[0].fact.relation
            if not any(self.corpus.has(t) for t in target):
                return None                     # "does titan orbit something" isn't a thing we can rule out
            onward = []
            for it in own:
                onward += [it.key] + [p[-1] for p in self.ancestors(it.key, year, rel)]
            if any(self._is_a(n, target, year) is not None for n in onward):
                return None                     # "does titan orbit a star": Saturn orbits the Sun, a star
            return {"rule": k.complete[rel], "kind": "complete", "items": own}
        return None

    def _test_yes(self, st, src, target, year):
        for it in src.items:
            if it.key in target:
                return Binding(st, [it], verdict={"yes": True, "path": [it], "show": "yes"})
        for it in src.items:
            if it.fact and it.fact.relation == "IS_A":
                path = self._is_a(it.key, target, year)
                if path is not None:
                    return Binding(st, [it], verdict={"yes": True, "path": [it], "chain": path, "show": "yes"})
        for it in src.items:
            f = it.fact
            if f and f.kind == "S" and f.relation != "IS_A" and (f.relation in TAXONOMY or f.relation not in LEXICAL_RELS):
                # Follow the same relation onward (Eiffel Tower -> Paris -> France).  Word links other than
                # "a kind of" aren't followed: a synonym of a synonym can be a different sense.
                for path in self.ancestors(it.key, year, TAXONOMY if f.relation in TAXONOMY else f.relation):
                    if path[-1] in target:
                        return Binding(st, [it], verdict={"yes": True, "path": [it], "trail": path, "show": "yes"})
                if f.relation not in TAXONOMY:
                    # "does titan orbit a star": Titan -> Saturn -> Sun, and the Sun is a star
                    for path in [[it.key]] + self.ancestors(it.key, year, f.relation):
                        chain = self._is_a(path[-1], target, year)
                        if chain is not None and len(chain) > 1:
                            return Binding(st, [it], verdict={"yes": True, "path": [it], "trail": path,
                                                              "kind_of": chain, "show": "yes"})
        return None

    def _op_logic(self, st, env, year, budget):
        op, parts = st.args[0], [env[v] for v in st.args[1:]]
        t = logic.combine(op, [p.verdict.get("truth", UNKNOWN) for p in parts])
        return Binding(st, [i for p in parts for i in p.items],
                       verdict={"yes": t == TRUE, "truth": t, "op": op, "parts": st.args[1:], "show": t.lower()})

    def _op_compare(self, st, env, year, budget):
        a, b = self._need(env, st.args[0]), self._need(env, st.args[1])
        more = st.args[2] == "MORE"

        na, nb = self._numeric(a.items, ("N", "D")), self._numeric(b.items, ("N", "D"))
        common = [r for r in na if r in nb]
        if common:
            order = self.match_relations([na[r][0][1] for r in common], env[st.args[0]].step.args[1])
            rel = (order or common)[0]
            (va, fa), (vb, fb) = self._one(na[rel]), self._one(nb[rel])
            if (fa.unit or "") != (fb.unit or ""):
                raise Unsupported(f"I have {rel_text(rel)} for both, but in different units, so I won't compare them.")
            ka, kb = fa.subject, fb.subject
            counted = False
        else:
            # No shared number: compare how many things the corpus lists ("which has more moons").
            ca = self._group(a.items)
            cb = self._group(b.items)
            shared = [r for r in ca if r in cb]
            if not shared:
                raise Unsupported("I don't have the same measurement for both, so I can't compare them.")
            rel = shared[0]
            va, vb = len(ca[rel]), len(cb[rel])
            fa, fb = ca[rel][0].fact, cb[rel][0].fact
            ka, kb = fa.subject, fb.subject
            counted = True
        if va == vb:
            win = None
        else:
            win = ka if (va > vb) == more else kb
        verdict = {"rel": rel, "a": (ka, va, fa), "b": (kb, vb, fb), "winner": win, "label": st.text,
                   "more": more, "counted": counted, "show": win or "equal"}
        return Binding(st, [Item(win or ka)], verdict=verdict)

    # ------------------------------------------------------------------ capabilities

    def _use(self, name: str) -> str:
        """Record that the plan uses `name` (or its fallback), via the registry.  Unavailable and required: the
        plan stops with a structured UNAVAILABLE error, and the computer says so instead of guessing."""
        from .capabilities import Unavailable
        reg = self.registry or shared_registry()
        try:
            return reg.choose(name)
        except Unavailable as e:
            err = reg.error("navigator.execute", "UNAVAILABLE", f"I can't do that right now: {e.name} isn't available.",
                            True, {"capability": e.name, "reason": e.reason})
            raise Unsupported(err["message"], "unavailable", err)

    def _error(self, cap: str, code: str, message: str, recoverable: bool, details: dict | None = None) -> dict:
        err = (self.registry or shared_registry()).error(cap, code, message, recoverable, details)
        self.errors.append(err)
        return err

    # ------------------------------------------------------------------ requirements (logic.requirement)

    def _op_measure(self, st, env, year, budget):
        """The thing's figure in one dimension (computer/analysis.measure): a fact in a unit of it, converted exactly, or
        charge x voltage.  Conflicts, several figures, wrong-dimension units and gaps are kept, not dropped."""
        src = self._need(env, st.args[0])
        dim = st.args[1]
        self._use("logic.units_gnu")             # prefers GNU Units; falls back to the built-in table, and says so
        m, chosen = None, None
        words = st.args[2] if len(st.args) > 2 else ""
        for it in src.items[:FANOUT]:
            facts = [f for f in self.corpus.facts_for(it.key) if f.source not in NOT_KNOWLEDGE]
            if words:
                facts = self._named_for(it.key, facts, dim, words)
            got = measure(it.key, facts, dim, self.knowledge.functional, year, self.min_conf)
            if m is None or (m.status == "missing" and got.status != "missing"):
                m, chosen = got, it
            if got.status != "missing":
                break
        src.items = [chosen]
        if m.formula:
            self._use("logic.formula")
            self.inferences.append(f"a formula combining different relations: {m.formula['expr']}")
            for f in m.facts:
                self.fact_derived[f.id] = [f"formula: {m.formula['expr']}"]
        self.rules_used += [r for r in m.rules]
        items = [Item(chosen.key, f) for f in m.facts] or [Item(chosen.key)]
        show = {"ok": lambda: say_quantity(m.quantity), "several": lambda: f"{len(m.candidates)} figures",
                "conflict": lambda: "conflict", "missing": lambda: "nothing", "incompatible": lambda: "wrong unit"}
        return Binding(st, items, note=m.reason if m.status != "ok" else "",
                       verdict={"measurement": m, "show": show[m.status](), "steps": list(m.steps)})

    def _named_for(self, key, facts, dim, words) -> list:
        """The question named the figure ("tall", "long"): keep this dimension's figures whose relation is named for
        it (HEIGHT, ELEVATION for "tall"), and say so.  If none is, every figure of the dimension stays, and that's said."""
        from .quantity import from_fact
        mine = [f for f in facts if (q := from_fact(f)) is not None and q.dim == dim]
        rels = set(self.match_relations(mine, words))
        if not rels:
            self.assumptions.append(f"no {dim} figure of {name_of(key)} is named for '{words[1:]}', so every one was considered")
            return facts
        self.assumptions.append(f"'{words[1:]}' is read as {name_of(key)}'s {listing(sorted(rel_text(r) for r in rels))}")
        return [f for f in facts if f not in mine or f.relation in rels]

    def _op_require(self, st, env, year, budget):
        """TRUE / FALSE against the stated value.  No figure: UNKNOWN (MISSING_FACT).  Wrong unit: UNKNOWN
        (UNIT_INCOMPATIBLE).  A functional rule's conflict: CONFLICT.  Several figures without a rule: the answer
        they all agree on, else UNKNOWN."""
        self._use("logic.requirement")
        mb = env[st.args[0]]
        m = mb.verdict["measurement"]
        cmp_, num, unit = st.args[1], float(st.args[2]), st.args[3]
        limit = Quantity(unit_dimension(unit), num * unit_factor(unit), f"{st.args[2]} {unit}", "stated")
        base = BASE_CODE[m.dim]
        name = name_of(m.subject)
        ok = (lambda v: v >= limit.value * (1 - 1e-12)) if cmp_ == "AT_LEAST" else (lambda v: v <= limit.value * (1 + 1e-12))
        sign = ">=" if cmp_ == "AT_LEAST" else "<="
        steps, reason = [], ""
        if m.facts and m.status in ("ok", "several"):
            # Which figure the stated requirement is held to is a reading, not a fact (rated vs usable capacity).
            how = (f"{DIM_NOUN[m.dim]} worked out from its {listing(sorted({rel_text(f.relation) for f in m.facts}))}"
                   if m.formula else listing(sorted({rel_text(f.relation) for f in m.facts})))
            self.assumptions.append(f"{say_quantity(limit)} is compared with {name}'s {how}")
        if m.status == "ok":
            v = m.quantity.value
            t = TRUE if ok(v) else FALSE
            steps.append(f"{fmt_plain(_r6(v))} {base} {sign} {fmt_plain(_r6(limit.value))} {base}: {t}")
        elif m.status == "several":
            ts = {TRUE if ok(q.value) else FALSE for q in m.candidates}
            for q in m.candidates:
                steps.append(f"{fmt_plain(_r6(q.value))} {base} {sign} {fmt_plain(_r6(limit.value))} {base}: "
                             f"{TRUE if ok(q.value) else FALSE}")
            if len(ts) == 1:
                t = ts.pop()
                self.inferences.append(f"several {DIM_NOUN[m.dim]}s, and every one gives {t}")
            else:
                t, reason = UNKNOWN, "several"
        elif m.status == "conflict":
            t = CONFLICT
            self.rules_used.append(m.rule.rule_id)
            self._error("logic.requirement", "CONFLICT", f"My corpus gives {name} more than one {DIM_NOUN[m.dim]}.",
                        False, {"values": [fmt_plain(_r6(q.value)) + " " + base for q in m.candidates],
                                "rule": m.rule.rule_id})
        elif m.status == "incompatible":
            t, reason = UNKNOWN, "incompatible"
            self._error("logic.requirement", "UNIT_INCOMPATIBLE", f"{name}'s {m.reason}.", False,
                        {"facts": [f.id for f in m.incompatible], "units": sorted({f.unit for f in m.incompatible}),
                         "expected": m.dim})
        else:
            t, reason = UNKNOWN, "missing"
            self._error("logic.requirement", "MISSING_FACT", f"Nothing in my corpus links {name} to {_a(DIM_NOUN[m.dim])}.",
                        True, {"subject": m.subject, "dimension": m.dim, "why": m.reason})
        return Binding(st, mb.items, verdict={"truth": t, "yes": t == TRUE, "measurement": m, "limit": limit,
                                              "cmp": cmp_, "reason": reason, "steps": steps, "show": t.lower()})

    def _op_select(self, st, env, year, budget):
        parts = [env[v] for v in st.args]
        verdicts = {p.verdict["measurement"].subject: p.verdict["truth"] for p in parts}
        truths = set(verdicts.values())
        overall = CONFLICT if CONFLICT in truths else UNKNOWN if UNKNOWN in truths else TRUE
        selected = [k for k, t in verdicts.items() if t == TRUE]
        return Binding(st, [i for p in parts for i in p.items],
                       verdict={"selected": selected, "verdicts": verdicts, "truth": overall,
                                "show": ", ".join(selected) or "none"})

    @staticmethod
    def _numeric(items, kinds) -> dict:
        """relation -> every (number, fact) the items hold for it, in corpus order."""
        out: dict = {}
        for i in items:
            f = i.fact
            if f and f.kind in kinds:
                v = to_number(f)
                if v is not None:
                    out.setdefault(f.relation, []).append((v, f))
        return out

    def _one(self, cands: list) -> tuple:
        """One figure for a comparison or a calculation, by a stated policy, never by corpus order:
          - figures that agree (after exact unit conversion) are one figure;
          - otherwise the figure from the best-ranked source (corpus.SOURCE_RANK: manual and document sources, then
            tables, then Wikidata ...), and the choice is recorded as an assumption;
          - two different figures from equally ranked sources: not picked.  The step stops with CONFLICT."""
        groups: list = []
        for v, f in cands:
            for g in groups:
                k = same_dimension(g[0][1].unit, f.unit)
                if k is not None and abs(g[0][0] * k[0] - v * k[1]) <= 1e-9 * max(1.0, abs(v * k[1])):
                    g.append((v, f))
                    break
            else:
                groups.append([(v, f)])
        if len(groups) == 1:
            return groups[0][0]
        rank = lambda g: min(SOURCE_RANK.get(f.source, 3) for _, f in g)
        best = min(rank(g) for g in groups)
        top = [g for g in groups if rank(g) == best]
        f0 = cands[0][1]
        shown = listing([show_value(g[0][1]) for g in groups[:4]])
        if len(top) > 1:
            err = self._error("navigator.execute", "CONFLICT",
                              f"My corpus gives {name_of(f0.subject)} more than one {rel_text(f0.relation)} ({shown}).",
                              False, {"subject": f0.subject, "relation": f0.relation,
                                      "values": [g[0][1].value for g in groups], "facts": [g[0][1].id for g in groups]})
            raise Unsupported(f"My corpus gives {name_of(f0.subject)} more than one {rel_text(f0.relation)} ({shown}), "
                              "from equally ranked sources, so I won't pick one for that.", "unsupported")
        g = top[0]
        others = sorted({f.source for o in groups if o is not g for _, f in o})
        self.assumptions.append(f"{name_of(f0.subject)}'s {rel_text(f0.relation)}: used the {g[0][1].source.lower()} "
                                f"figure ({show_value(g[0][1])}) over {listing([s.lower() for s in others])} "
                                f"figure{'s' if len(groups) > 2 else ''} ({listing([show_value(o[0][1]) for o in groups if o is not g])}), "
                                "by source rank")
        return g[0]

    def _op_calc(self, st, env, year, budget):
        """SUM / DIFF / RATIO of one measurement across things (units converted when they measure the same thing,
        refused when they don't), or DIV of any two numbers (population / area).  Only numbers the plan fetched."""
        op, parts = st.args[0], [self._need(env, v) for v in st.args[1:]]

        nums = [self._numeric(p.items, ("N",)) for p in parts]
        for p, n in zip(parts, nums):
            if not n:
                f = next((i.fact for i in p.items if i.fact), None)
                what = f"{name_of(f.subject)}'s {rel_phrase(p.step.args[1])}" if f else rel_phrase(p.step.args[1])
                raise Unsupported(f"My corpus doesn't give a number for {what}, so I can't work that out.")
        if op == "DIV":
            picks = []
            for p, n in zip(parts, nums):
                order = self.match_relations([x[0][1] for x in n.values()], p.step.args[1])
                picks.append(self._one(n[(order or list(n))[0]]))
            (va, fa), (vb, fb) = picks
            if vb == 0:
                raise Unsupported(f"{name_of(fb.subject)}'s {rel_text(fb.relation)} is zero, so I can't divide by it.")
            result, unit, rel = va / vb, fa.unit, fa.relation
            vals = [(fa.subject, va, fa), (fb.subject, vb, fb)]
        else:
            common = [r for r in nums[0] if all(r in n for n in nums[1:])]
            if not common:
                names = listing([name_of(next(i.fact.subject for i in p.items if i.fact)) for p in parts])
                raise Unsupported(f"I don't have the same measurement for {names}, so I can't work that out.")
            order = self.match_relations([nums[0][r][0][1] for r in common], parts[0].step.args[1])
            rel = (order or common)[0]
            chosen = [self._one(n[rel]) for n in nums]
            unit = chosen[0][1].unit
            vals = []
            for v, f in chosen:
                k = same_dimension(unit, f.unit)
                if k is None:
                    raise Unsupported(f"I have {rel_text(rel)} for each, but in units that don't measure the same thing, "
                                      "so I won't combine them.")
                vals.append((f.subject, v * k[1] / k[0], f))
            xs = [v for _, v, _ in vals]
            if op == "SUM":
                result = sum(xs)
            elif op == "DIFF":
                result = xs[0] - xs[1]
            else:
                if xs[1] == 0:
                    raise Unsupported(f"{name_of(vals[1][0])}'s {rel_text(rel)} is zero, so there's no ratio.")
                result = xs[0] / xs[1]
        shown = abs(result) if op == "DIFF" else result
        said = fmt_num(shown)
        sym = {"SUM": " + ", "DIFF": " - ", "RATIO": " / ", "DIV": " / "}[op]
        record = {"op": op, "relation": rel, "expr": sym.join(fmt_plain(v) for _, v, _ in vals) + f" = {fmt_num(result, 6)}",
                  "result": result, "unit": "" if op in ("RATIO",) else unit,
                  "facts": [f.id for _, _, f in vals]}
        verdict = {"op": op, "rel": rel, "values": vals, "result": result, "said": said, "unit": unit,
                   "label": st.text, "record": record, "show": said}
        return Binding(st, [Item(said)], verdict=verdict)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _group(items):
        out = {}
        for i in items:
            if i.fact and i.fact.kind in ("S", "T"):
                out.setdefault(i.fact.relation, []).append(i)
        return out

    def _target(self, words: str) -> set[str]:
        """The keys a name could mean, including ones that are only ever values ("animals" -> ANIMAL)."""
        k = key_of(re.sub(r"^(?:the|a|an)\s+", "", words.strip()))
        out = {k, k[:-1] if k.endswith("S") else k, k[:-2] if k.endswith("ES") else k,
               k[:-3] + "Y" if k.endswith("IES") else k}
        return out | set(self.resolve(words)[:1])

    def _is_a(self, key: str, target, year: int):
        targets = {target} if isinstance(target, str) else target
        if key in targets:
            return [key]
        for path in self.ancestors(key, year):
            if path[-1] in targets:
                return path
        return None

    def _missing(self, key: str, rel: str) -> str:
        if not key:
            return "My corpus doesn't have that."
        first = rel[1:].split(",")[0] if rel.startswith("~") else LEX_NAME.get(rel, "")
        if first in LEX_MISSING:
            w = name_of(key).lower()
            return f"I know the word {w}, but my corpus doesn't have {LEX_MISSING[first].format(w=w)}."
        return f"I know about {name_of(key)}, but my corpus doesn't say its {rel_phrase(rel)}."

    def _evidence(self, env) -> list[Fact]:
        """The facts the answer rests on.  What a RANK or EXCEPT left out isn't evidence."""
        return [f for f, _ in self._evidence_via(env)]

    def _evidence_via(self, env) -> list[tuple[Fact, list]]:
        """(fact, IS_A path it was inherited through, or [] if it's the thing's own fact)."""
        out, seen = [], set()
        narrowed = {b.step.args[0] for b in env.values() if b.step.op in ("RANK", "EXCEPT")}
        for var, b in env.items():
            if var in narrowed:
                continue
            for it in b.items:
                if it.fact and id(it.fact) not in seen and it.fact.source:
                    seen.add(id(it.fact))
                    out.append((it.fact, list(it.via)))
        return out

    # ------------------------------------------------------------------ saying it

    def _render(self, plan: Plan, env: dict, answer: Step) -> str:
        said: list[tuple[str, str]] = []          # (step variable, sentence)
        isa_said: set = set()

        def drop(var):
            nonlocal said
            said = [(v, s) for v, s in said if v != var]

        for st in plan.steps:
            b = env.get(st.out)
            if not b:
                continue
            add = lambda lines: said.extend((st.out, s) for s in lines if s)
            if st.op == "FIND" and b.items and b.items[0].note:
                add([self._say_soft(b.items[0])])
            elif st.op in ("RANK", "EXCEPT"):
                drop(st.args[0])
                origin = self._origin(env, st.args[0])
                if origin == "WHO" and not env[st.args[0]].note.startswith("nothing points"):
                    add([self._say_who(b.items)])
                elif origin in ("GET", "WHO"):
                    add(self._say_get(b, isa_said))
            elif st.op == "GET":
                add(self._say_get(b, isa_said))
            elif st.op == "WHO":
                if b.note.startswith("nothing points"):
                    add(self._say_get(b, isa_said))
                else:
                    add([self._say_who(b.items)])
            elif st.op == "FILTER":
                src = env[st.args[0]]
                if src.step.op == "WHO" and not src.note:
                    drop(st.args[0])
                    add([self._say_who(b.items)])
            elif st.op == "COUNT":
                v = b.verdict
                if not v["stated"]:
                    drop(st.args[0])
                    f = b.items[0].fact
                    lex = LEX_NAME.get(f.relation, "")
                    if f.relation == "DEFINITION":
                        defs = list(dict.fromkeys(clean_definition(i.fact.value) for i in b.items))
                        n = v["count"]
                        add([f"My corpus has {n} meaning{'' if n == '1' else 's'} for {name_of(f.subject).lower()}: "
                             f"{'; '.join(defs[:4])}."])
                    elif lex in LEX_PLURAL:
                        names = list(dict.fromkeys(value_of(i.fact) for i in b.items))
                        add([f"My corpus lists {v['count']} {LEX_PLURAL[lex]} for {name_of(f.subject).lower()}: "
                             f"{listing(names[:10])}."])
                    else:
                        names = list(dict.fromkeys(value_of(i.fact) for i in b.items))
                        add([f"My corpus lists {v['count']} for {name_of(f.subject)}: {listing(names[:10])}."])
            elif st.op == "TEST":
                drop(st.args[0])
                add(self._say_test(b))
            elif st.op == "LOGIC":
                for v in st.args[1:]:
                    drop(v)
                add(self._say_logic(b, env))
            elif st.op == "COMPARE":
                drop(st.args[0])
                drop(st.args[1])
                add([self._say_compare(b.verdict)])
            elif st.op == "CALC":
                for v in st.args[1:]:
                    drop(v)
                add([self._say_calc(b.verdict)])
            elif st.op == "MEASURE":
                add([self._say_measure(b.verdict["measurement"])])
            elif st.op == "REQUIRE":
                drop(st.args[0])
                add([self._say_require(b.verdict)])
            elif st.op == "SELECT":
                for v in st.args:
                    drop(env[v].step.args[0])
                    drop(v)
                add([self._say_select(b.verdict, env)])
        return " ".join(dict.fromkeys(s for _, s in said)).strip()

    @staticmethod
    def _origin(env, var) -> str:
        """The op that made a list, looking through RANK / EXCEPT / FILTER."""
        b = env[var]
        while b.step.op in ("RANK", "EXCEPT", "FILTER"):
            b = env[b.step.args[0]]
        return b.step.op

    @staticmethod
    def _say_soft(it: Item) -> str:
        how, said = it.note
        w = name_of(it.key).lower()
        if how == "listed":
            return f"I don't have {said} on its own, but my corpus lists it as {SOFT_LINKS[it.fact.relation]} {w}."
        if how == "spelling":
            return f"I think you mean {w}."
        return f"I don't have {said} on its own, but I have {w}."

    def _say_who(self, items) -> str:
        facts = [i.fact for i in items]
        if facts[0].relation in self.knowledge.symmetric:
            # Said from the side of the thing asked about, so it reads right whichever row each name came from
            about = {key_of(i.fact.value) if key_of(i.fact.subject) == i.key else key_of(i.fact.subject) for i in items}
            names = sorted({i.key for i in items})
            return (f"{listing([name_of(k) for k in sorted(about)])} {rel_text(facts[0].relation)} "
                    f"{listing([name_of(n) for n in names])}.")
        if re.match(r"^(LOCATED|PART_OF|MADE_OF|NAMED_AFTER|MEMBER_OF)", facts[0].relation):
            names = sorted({f.subject for f in facts})
            verb = "are" if len(names) > 1 else "is"
            return f"{listing([name_of(n) for n in names[:15]])} {verb} {rel_text(facts[0].relation)} {name_of(facts[0].value)}."
        from .navdata import shape
        if shape(facts[0].relation)[0] == "noun":
            # "what eats krill" through DIET: say it the way the fact reads ("Blue Whale's diet is krill.")
            by = {}
            for f in facts:
                by.setdefault(f.subject, []).append(f)
            said = " ".join(sentence(sub, fs[0].relation, fs) for sub, fs in sorted(by.items())[:5])
            rest = sorted(by)[5:]
            # Never cut a list short in silence: the ones not said in full are named
            return said + (f" Also: {listing([name_of(n) for n in rest])}." if rest else "")
        return phrase_block({"reverse": facts[0].relation, "subject": facts[0].value,
                             "names": sorted({f.subject for f in facts})})

    def _say_isa_chain(self, path, isa_said) -> list[str]:
        out = []
        for a, b in zip(path, path[1:]):
            if (a, b) not in isa_said:
                isa_said.add((a, b))
                out.append(f"{name_of(a) if a == path[0] else concept(a)} is {article_for(b)} {b.lower().replace('_', ' ')}.")
        return out

    def _say_get(self, b: Binding, isa_said) -> list[str]:
        out, groups = [], {}
        for it in b.items:
            f = it.fact
            groups.setdefault((tuple(it.via), f.subject, SAY_AS.get(f.relation, f.relation)), []).append(f)
        for (via, subj, rel), facts in list(groups.items())[:6]:
            if via:
                out += self._say_isa_chain(list(via), isa_said)
            if rel == "IS_A":
                for f in facts:
                    isa_said.add((subj, key_of(f.value)))
            s = sentence(subj, rel, facts)
            if s and s not in out:
                out.append(s)
        return out

    def _say_test(self, b: Binding) -> list[str]:
        word, body = self._test_parts(b)
        return ([f"{word}."] if word else []) + body

    def _test_parts(self, b: Binding) -> tuple[str, list[str]]:
        """("Yes" / "No" / "", the sentences that back it)."""
        v = b.verdict
        t = v.get("truth", TRUE if v.get("yes") else UNKNOWN)
        if t == FALSE:
            return "No", self._say_no(v["no"])
        if t == CONFLICT:
            _, why_yes = self._test_parts(v["for"])
            return "", ["My corpus disagrees with itself here."] + why_yes + \
                ["But it also says: " + " ".join(self._say_no(v["no"])), "So I won't pick one."]
        said = self._say_test_old(b)
        if said and said[0] == "Yes.":
            return "Yes", said[1:]
        return "", said

    def _say_no(self, no: dict) -> list[str]:
        if no["kind"] == "disjoint":
            out = self._say_isa_chain(no["mine"], set())
            theirs = no["theirs"]
            if len(theirs) > 1:
                out += [f"{concept(a)} is {article_for(b)} {b.lower().replace('_', ' ')}." for a, b in zip(theirs, theirs[1:])]
            a, b = no["a"].lower().replace("_", " "), no["b"].lower().replace("_", " ")
            return out + [f"Nothing is both {article_for(a)} {a} and {article_for(b)} {b}."]
        f = no["items"][0].fact
        return [sentence(f.subject, f.relation, [it.fact for it in no["items"]])]

    def _say_logic(self, b: Binding, env) -> list[str]:
        v = b.verdict
        t, op = v["truth"], v["op"]
        head = {("AND", TRUE): "Yes, both.", ("AND", FALSE): "No, not both.",
                ("OR", TRUE): "Yes, at least one.", ("OR", FALSE): "No, neither.",
                ("NOR", TRUE): "Right, neither.", ("NOR", FALSE): "No, it is one of them.",
                ("NOT", TRUE): "Right, it isn't.", ("NOT", FALSE): "No, it is."}.get((op, t))
        if head is None:
            head = ("My corpus disagrees with itself on part of that, so I won't say." if t == CONFLICT
                    else "I can't tell from my corpus.")
        out, said = [head], set()
        for var in v["parts"]:
            p = env[var]
            if p.step.op == "TEST":
                word, body = self._test_parts(p)
                label = p.step.text
            else:
                word, body = "", self._say_logic(p, env)
                label = "that"
            fresh = [x for x in body if x not in said]          # a chain already said for one part isn't repeated
            said.update(body)
            out.append(f"{label[:1].upper() + label[1:]}: " + (f"{word.lower()}. " if word else "") + " ".join(fresh))
        return [x.strip() for x in out]

    def _say_conflict_get(self, b: Binding) -> str:
        c = b.conflict
        said = sentence(c["subject"], c["relation"], c["facts"])
        return f"My corpus disagrees with itself here. {said} But {c['rule'].why}, so I won't pick one."

    def _say_test_old(self, b: Binding) -> list[str]:
        v = b.verdict
        it = b.items[0]
        if v["yes"]:
            if v.get("chain"):
                return ["Yes."] + self._say_isa_chain([it.fact.subject] + v["chain"], set())
            out = ["Yes."] + self._say_isa_chain(it.via, set()) + [sentence(it.fact.subject, it.fact.relation, [it.fact])]
            trail = v.get("trail") or []
            for a, b in zip(trail, trail[1:]):
                out.append(sentence(a, it.fact.relation, [Fact(a, it.fact.relation, "S", b)]))
            if v.get("kind_of"):
                out += self._say_isa_chain(v["kind_of"], set())
            return out
        known = [i for i in b.items if i.fact and i.fact.relation == "IS_A" and not i.via][:2]
        lex = [i for i in b.items if i.fact and i.fact.relation in LEXICAL_RELS]
        if lex and not known:
            f = lex[0].fact
            said = sentence(f.subject, SAY_AS.get(f.relation, f.relation),
                            [i.fact for i in lex if SAY_AS.get(i.fact.relation, i.fact.relation)
                             == SAY_AS.get(f.relation, f.relation)])
            return [f"I can't find that in my corpus. {said} Nothing there links it to {v['target']}."]
        if known:
            subj = name_of(known[0].fact.subject)
            kinds = listing([f"{article_for(i.key)} {value_of(i.fact)}" for i in known])
            return [f"I can't find that in my corpus. I know {subj} is {kinds}, but nothing there links it to {v['target']}."]
        return [f"I can't find that in my corpus: nothing there links it to {v['target']}."]

    @staticmethod
    def _say_measure(m) -> str:
        """The figure and where it came from: "Battery Alpha's energy capacity is 0.052 kilowatt-hours, which is
        52 watt-hours"; "Battery Beta's rated charge is 3.5 amp-hours and its nominal voltage is 12 volts, which
        makes 42 watt-hours"."""
        name = name_of(m.subject)
        if m.status != "ok":
            return ""
        q = m.quantity
        if m.formula:
            fa, fb = m.facts[0], m.facts[-1]
            return (f"{sentence(fa.subject, fa.relation, [fa])[:-1]} and its {rel_text(fb.relation)} is "
                    f"{show_value(fb)}, which makes {say_quantity(q)}")
        f = next((f for f in m.facts if f.id == (q.fact or {}).get("id")), m.facts[0])
        s = sentence(f.subject, f.relation, [f])[:-1]
        others = [g for g in m.facts if g is not f]
        if others:
            s += " (" + "; ".join(f"also {show_value(g)}" for g in others) + ", the same figure)"
        same = f"{fmt_num(float(f.value))} {unit_of(f.unit)}" == say_quantity(q) if re.fullmatch(r"-?[\d.]+(?:e[+-]?\d+)?", f.value) else False
        if (q.fact or {}).get("unit", "").upper() != BASE_CODE[q.dim] and not same:
            s += f", which is {say_quantity(q)}"       # 0.052 kWh, which is 52 Wh; not "6650.0 km, which is 6,650 km"
        return s or name

    def _say_require(self, v) -> str:
        m, t, lim = v["measurement"], v["truth"], say_quantity(v["limit"])
        name = name_of(m.subject)
        at_least = v["cmp"] == "AT_LEAST"
        meets = f"meets {lim}" if at_least else f"stays within {lim}"
        if m.status == "ok":
            body = self._say_measure(m)
            if t == TRUE:
                return f"Yes. {body}, so it {meets}."
            return f"No. {body}. That's {'less' if at_least else 'more'} than {lim}."
        figs = listing([say_quantity(q) for q in m.candidates])
        if m.status == "several":
            if t in (TRUE, FALSE):
                return (f"{'Yes' if t == TRUE else 'No'}. My corpus gives {name} more than one {DIM_NOUN[m.dim]}: {figs}. "
                        f"{'Every one' if t == TRUE else 'None'} of them {meets}.")
            return (f"I can't tell. My corpus gives {name} more than one {DIM_NOUN[m.dim]}: {figs}, and nothing says "
                    f"which one applies. Some meet {lim} and some don't.")
        if m.status == "conflict":
            return (f"My corpus disagrees with itself here: it gives {name} {DIM_NOUN[m.dim]}s of {figs}, and "
                    f"{m.rule.why}, so I won't say whether it {meets}.")
        if m.status == "incompatible":
            return f"I can't check that: {name}'s {m.reason}, so it can't be compared with {lim}."
        return f"I can't tell: nothing in my corpus links {name} to {_a(DIM_NOUN[m.dim])}. {m.reason}"

    def _say_select(self, v, env) -> str:
        parts = [b for b in env.values() if b.step.op == "REQUIRE"]
        lim = say_quantity(parts[0].verdict["limit"])
        meets = "meets" if parts[0].verdict["cmp"] == "AT_LEAST" else "stays within"
        yes = [name_of(k) for k, t in v["verdicts"].items() if t == TRUE]
        no = [name_of(k) for k, t in v["verdicts"].items() if t == FALSE]
        unsure = [name_of(k) for k, t in v["verdicts"].items() if t not in (TRUE, FALSE)]
        head = []
        if yes:
            head.append(f"{listing(yes)} {meets if len(yes) == 1 else meets.replace('meets', 'meet').replace('stays', 'stay')} {lim}")
        if no:
            head.append(f"{listing(no)} {DOESNT if len(no) == 1 else DONT}")
        if not yes and no and not unsure:
            head = [f"None of them {meets} {lim}"]
        if unsure:
            head.append(f"I can't tell for {listing(unsure)}")
        body = []
        for b in parts:
            said = self._say_require(b.verdict)
            body.append(re.sub(r"^(?:Yes|No)\. ", "", said))
        return "; ".join(head) + ". " + " ".join(body)

    def _say_calc(self, v) -> str:
        """Each number with whose it is, then the arithmetic's result.  The result is DERIVED: said as worked out."""
        op, vals, rel, unit = v["op"], v["values"], v["rel"], v["unit"]
        (ka, va, fa) = vals[0]
        if op == "DIV":
            kb, vb, fb = vals[1]
            other = (f"its {rel_text(fb.relation)} is {show_value(fb)}" if kb == ka
                     else f"{sentence(kb, fb.relation, [fb])[:-1]}")
            ua, ub = unit_of(fa.unit), unit_of(fb.unit)
            per = re.sub(r"(?<=[a-z])s$", "", ub) if ub else ""
            what = v["label"] or "that"
            amount = f"{v['said']}{' ' + ua if ua else ''}" + (f" per {per}" if per else "")
            return f"{sentence(ka, fa.relation, [fa])[:-1]}, and {other}, so {what} is about {amount}."
        rest = [f"{name_of(k)}'s is {show_value(f)}" for k, _, f in vals[1:]]
        line = f"{sentence(ka, fa.relation, [fa])[:-1]}, " + (f"and {rest[0]}" if len(rest) == 1
                                                               else f"{', '.join(rest[:-1])}, and {rest[-1]}")
        u = unit_of(unit)
        amount = f"{v['said']}{' ' + u if u else ''}"
        if op == "SUM":
            return f"{line}, so together that's {amount}."
        if op == "RATIO":
            return f"{line}, so {name_of(ka)}'s {rel_text(rel)} is {v['said']} times {name_of(vals[1][0])}'s."
        a_more = v["result"] > 0
        label = v["label"]
        if v["result"] == 0:
            return f"{line}, so they are the same."
        if label and not label.startswith(("less", "fewer")) and label not in LESS_LABELS:
            if a_more:
                return f"{line}, so {name_of(ka)} is {amount} {label}."
            return f"{line}, so {name_of(ka)} isn't {label}: {name_of(vals[1][0])}'s is greater by {amount}."
        bigger = ka if a_more else vals[1][0]
        return f"{line}, so the difference is {amount} ({name_of(bigger)}'s is greater)."

    def _say_compare(self, v) -> str:
        rel = v["rel"]
        (ka, va, fa), (kb, vb, fb) = v["a"], v["b"]
        if v["counted"]:
            line = f"For {rel_text(rel)}, my corpus lists {va} for {name_of(ka)} and {vb} for {name_of(kb)}"
        else:
            line = f"{sentence(ka, rel, [fa])[:-1]}, and {name_of(kb)}'s is {show_value(fb)}"
        if v["winner"] is None:
            return line + ", so they are the same."
        label = v["label"] or ("more" if v["more"] else "less")
        if label in ("more", "less", "fewer"):
            if v["counted"]:
                return line + f", so {name_of(v['winner'])} has {label}."
            label = f"{'greater' if v['more'] else 'smaller'} in {rel_text(rel)}"
        return line + f", so {name_of(v['winner'])} is {label}."


# ---------------------------------------------------------------------- small helpers

DOESNT, DONT = "doesn't", "don't"


def _r6(x: float) -> float:
    return float(f"{x:.6g}")


def _a(noun: str) -> str:
    return f"{article_for(noun)} {noun}"


def to_number(f: Fact):
    if f.kind == "D":
        m = re.match(r"(-?)(\d+)(?:-(\d{2}))?(?:-(\d{2}))?", f.value.strip())     # any number of digits: -4527000000
        if not m:
            return None
        y = int(m.group(2)) * (-1 if m.group(1) else 1)
        return y + (int(m.group(3) or 1) - 1) / 12 + (int(m.group(4) or 1) - 1) / 372
    try:
        return float(f.value.replace(",", ""))
    except ValueError:
        return None


def num_text(v: str) -> str:
    """A number as a checker compares it: no thousands commas, no trailing .0."""
    t = str(v).replace(",", "").strip()
    try:
        x = float(t)
    except ValueError:
        return t
    return str(int(x)) if x.is_integer() and "e" not in t.lower() else t


def show_value(f: Fact) -> str:
    s = sentence(f.subject, f.relation, [f])
    m = re.search(r" (?:is|was born(?: in| on)?|was founded(?: in| on)?|began(?: in| on)?|happened(?: in)?|died(?: in| on)?) (.+)\.$", s)
    return m.group(1) if m else f.value


def concept(key: str) -> str:
    """WHALE -> Whale, MARINE_MAMMAL -> Marine mammal (a kind of thing, not a proper name)."""
    s = key.lower().replace("_", " ")
    return s[:1].upper() + s[1:]


def article_for(key: str) -> str:
    return "an" if re.match(r"[aeiou]", key, re.I) else "a"


def plural(word: str, n: int) -> str:
    if n == 1:
        return f"{article_for(word)} {word}"
    if word.endswith("y") and not word.endswith(("ay", "ey", "oy")):
        return word[:-1] + "ies"
    return word if word.endswith("s") else word + ("es" if word.endswith(("sh", "ch", "x")) else "s")


NOUN_OF = {"big": "size", "large": "size", "small": "size", "heavy": "weight", "light": "weight",
           "tall": "height", "high": "height", "short": "height", "long": "length", "old": "age", "new": "age",
           "young": "age", "far": "distance", "near": "distance", "close": "distance", "hot": "temperature",
           "cold": "temperature", "fast": "speed", "slow": "speed", "deep": "depth", "wide": "width",
           "where": "location", "when": "date", "populous": "population", "people": "population"}


def rel_phrase(rel: str) -> str:
    if rel == "*":
        return "anything about that"
    if rel.startswith("~"):
        words = [NOUN_OF.get(w, w) for w in rel[1:].split(",")]
        return " or ".join(dict.fromkeys(words))
    return rel_text(rel)
