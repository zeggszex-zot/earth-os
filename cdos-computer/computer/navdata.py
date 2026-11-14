"""Phase 3 and 6: training examples for the navigator, and the navigator-vs-lookup evaluation.

generate()  walks the corpus and writes question -> plan -> result examples.  Every example is
            *executed* before it is kept: if the plan doesn't reach the gold answer in the
            corpus, the example is thrown away.  So the model is only ever taught plans that work.
evaluate()  asks the same questions of the plain lookup (Corpus.ask, what the computer did before)
            and of the navigator, on the same corpus, and scores them.

Splits are by subject (hash), so test questions are about things the model never saw in training, and
leak_free() then drops any training example that so much as mentions a held-out thing (as the subject, a
class it's tested against, or a fact in its evidence).  Used by tools/make_navigator_data.py,
tools/eval_navigator.py and tools/experiment_learning.py.

Each kept example records what a model could learn from, and what a checker needs:

    question, plan            what was asked, and the plan that answers it (the training target)
    evidence                  the facts used: subject, relation, value, unit, source, confidence, and
                              "derived": "" for the thing's own fact, or the IS_A path it was inherited through
    rules                     the knowledge rules a "no" or a conflict rests on (computer/data/rules.json)
    steps                     every plan step and what it found (the inference)
    final, truth              the answer itself (values, winner, count, or TRUE/FALSE/...)
    answer                    the computer's sentence, which says why the answer follows
    concepts                  every corpus thing it touches (for the leak check)
"""

from __future__ import annotations

import random
import re
import time
from collections import Counter, defaultdict

from .corpus import Corpus, fnv1a, key_of, rel_words
from .navigator import Navigator
from .phrase import article, clean_definition, name_of, rel_text, value_of
from .plan import PlanError, parse
from .planner import LEX, LM_SYSTEM

# Dictionary relations are left out of the encyclopedic templates below and get their own (Generator.lexical).
SKIP_RELS = {"DEFINITION", "SYNONYM", "HYPERNYM", "HYPONYM", "CATEGORY", "WN_SIMILAR_TO", "DERIVATION",
             "WN_ANTONYM", "ANTONYM", "EXAMPLE"}
ADJECTIVE = {"MASS": ("heavier", "MORE"), "RADIUS": ("bigger", "MORE"), "AREA": ("bigger", "MORE"),
             "HEIGHT": ("taller", "MORE"), "LENGTH": ("longer", "MORE"), "POPULATION": ("more populous", "MORE"),
             "INCEPTION": ("older", "LESS"), "FOUNDED": ("older", "LESS"), "BORN": ("older", "LESS"),
             "ELEVATION": ("higher", "MORE"), "DEPTH": ("deeper", "MORE"), "SPEED": ("faster", "MORE")}


YES_KINDS = {"test_yes", "lex_yes"}
NO_KINDS = {"test_no", "lex_no"}     # "I can't find a link" is right; any "yes" is wrong
# Yes/no answers with a definite head ("No.", "Yes, at least one.", "Right, neither."): right only if the answer
# starts with gold[0].  The heads are all different, so a plain test can't pass for an and/or question.
HEAD_KINDS = {"test_false", "logic", "rel_test"}
HEADS = {("TEST", "TRUE"): "yes.", ("TEST", "FALSE"): "no.",
         ("AND", "TRUE"): "yes, both.", ("AND", "FALSE"): "no, not both.",
         ("OR", "TRUE"): "yes, at least one.", ("OR", "FALSE"): "no, neither.",
         ("NOR", "TRUE"): "right, neither.", ("NOR", "FALSE"): "no, it is one of them.",
         ("NOT", "TRUE"): "right, it isn't.", ("NOT", "FALSE"): "no, it is."}


LEX_RELS_OF = {"define": ("DEFINITION",), "synonym": ("SYNONYM", "WN_SIMILAR_TO"), "antonym": ("ANTONYM", "WN_ANTONYM"),
               "hypernym": ("HYPERNYM", "IS_A"), "hyponym": ("HYPONYM",), "derivation": ("DERIVATION",),
               "example": ("EXAMPLE",)}
LEX_FORMS = {
    "define": ["what does {w} mean", "what is the meaning of {w}", "define {w}", "what is the definition of {w}",
               "what does the word {w} mean", "{w} meaning", "tell me what {w} means"],
    "synonym": ["what is another word for {w}", "give me a synonym for {w}", "synonyms for {w}",
                "what are other words for {w}", "what is a word that means {w}"],
    "antonym": ["what is the opposite of {w}", "antonym of {w}", "what is the antonym of {w}",
                "what word means the opposite of {w}"],
    "hypernym": ["what kind of thing is {w}", "what is {w} a kind of", "what type of thing is {w}"],
    "hyponym": ["what are some kinds of {w}", "name some types of {w}", "what kinds of {w} are there"],
    "derivation": ["what words are related to {w}", "what words come from {w}"],
    "example": ["use {w} in a sentence", "give me an example sentence with {w}"],
}
LEX_CHAIN_FORMS = ["what does the opposite of {w} mean", "define the opposite of {w}",
                   "what is the meaning of the opposite of {w}"]
LEX_TEST_FORMS = {"synonym": ["is {w} a synonym of {y}", "does {w} mean the same as {y}", "is {w} another word for {y}"],
                  "antonym": ["is {w} the opposite of {y}", "is {w} an antonym of {y}"]}


def split_of(key: str) -> str:
    h = fnv1a("split:" + key) % 10
    return "test" if h == 0 else "dev" if h == 1 else "train"


def speakable(key: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z'\-]*(?:_[A-Z0-9'\-]+){0,4}", key)) and len(key) <= 40


def nm(key: str) -> str:
    return name_of(key).lower()


def rw(rel: str) -> str:
    """The words a person would use for a relation: HAS_SATELLITE -> satellite."""
    return re.sub(r"^(has |is |wn )", "", rel_text(rel))


PARTICIPLE = {"part", "used", "made", "member", "named", "founded", "built", "owned", "located", "capital", "composed"}
NOUNISH_S = {"lungs", "parts", "series", "species", "news", "mass", "class", "status", "genus", "address"}


def shape(rel: str) -> tuple[str, str, str]:
    """(shape, words, verb base): how people ask about a relation.
    noun  CAPITAL -> "capital", "the capital of X";  verb  ORBITS -> "orbit", "what does X orbit";
    part  PART_OF -> "part of", "what is X part of";  where LOCATED_IN -> "where is X"."""
    t = rel_text(rel)
    w = t.split()
    if not w:
        return "noun", t, ""
    if w[0] == "has" and len(w) > 1:
        return "noun", " ".join(w[1:]), ""
    if w[0] == "located":
        return "where", t, ""
    if w[0] in PARTICIPLE or w[0].endswith("ed"):
        return ("part", t, "") if len(w) > 1 else ("noun", t, "")
    if (w[0].endswith("s") and not w[0].endswith(("ss", "us", "is", "ics", "ys")) and w[0] not in NOUNISH_S
            and len(w) <= 2 and "of" not in w):
        base = w[0][:-2] if w[0].endswith(("shes", "ches", "xes")) else w[0][:-1]
        return "verb", t, " ".join([base] + w[1:])
    return "noun", t, ""


def plural_of(w: str) -> str:
    if w.endswith("s"):
        return w
    if w.endswith("y") and not w.endswith(("ay", "ey", "oy")):
        return w[:-1] + "ies"
    return w + ("es" if w.endswith(("sh", "ch", "x")) else "s")


def hop_forms(rel: str, s: str) -> list[str]:
    k, t, base = shape(rel)
    if k == "noun":
        return [f"what is the {t} of {s}", f"what is {s}'s {t}", f"tell me the {t} of {s}"]
    if k == "verb":
        return [f"what does {s} {base}"]
    if k == "part":
        return [f"what is {s} {t}"]
    return [f"where is {s}", f"where is {s} located"]


class Generator:
    def __init__(self, corpus: Corpus, seed: int = 0, year: int = 0):
        self.corpus = corpus
        self.nav = Navigator(corpus)
        self.rng = random.Random(seed)
        self.year = year or time.localtime().tm_year
        self.numeric = defaultdict(list)    # (relation, unit) -> [(subject, fact)]
        self.seen_q = set()
        self.lex_pool = []                  # (word, kind) with that kind of link, for "no" examples

    def facts(self, key):
        # WN_* are the dictionary's own pointers (usage domain, also see, pertainym): nobody asks "how many also sees"
        return [f for f in self.nav.facts(key, self.year) if f.relation not in SKIP_RELS and not f.relation.startswith("WN_")]

    def _keep(self, ex, out):
        """Run the plan.  Keep the example only if it reaches what it should."""
        if ex["question"] in self.seen_q:
            return
        try:
            p = parse(ex["plan"])
        except PlanError:
            return          # a relation the plan language can't name (e.g. WORDNET_POINTER_;C in an unrepaired build)
        r = self.nav.run(p, year=self.year)
        text = r.text.lower()
        if ex["expect"] == "answered":
            if not r.answered or not any(g.lower() in text for g in ex["gold"]):
                return
            if any(n.lower() in text for n in ex.get("not", [])):
                return
            if ex["kind"] == "compare" and f"so {ex['gold'][0].lower()} " not in text:
                return
            if ex["kind"] in YES_KINDS and not text.startswith("yes"):
                return
        elif r.answered and ex["kind"] not in NO_KINDS:
            return
        if ex["kind"] in NO_KINDS and text.startswith("yes"):
            return
        if ex["kind"] in HEAD_KINDS and not (r.answered and text.startswith(ex["gold"][0])):
            return
        ex["answer"] = r.text
        ex["split"] = split_of(ex["subjects"][0])
        record(ex, r, p)
        self.seen_q.add(ex["question"])
        out.append(ex)

    def lexical(self, s: str, out: list):
        """Dictionary questions about a word: meaning (and which sense), other words, opposite, what kind
        of thing, kinds of it, related words, a sentence; one two-step chain; a yes; a missing opposite."""
        S, rng = nm(s), self.rng
        by = defaultdict(list)
        for f in self.nav.facts(s, self.year):
            by[f.relation].append(f)
        facts_of = lambda kind: [f for r in LEX_RELS_OF[kind] for f in by.get(r, [])]
        gold_of = lambda f: clean_definition(f.value) if f.relation == "DEFINITION" else value_of(f)
        kinds = [k for k in LEX_RELS_OF if any(f.relation != "IS_A" for f in facts_of(k))]
        if not kinds:
            return
        rng.shuffle(kinds)
        for kind in kinds[:2]:
            fs = facts_of(kind)[:3] if kind == "define" else facts_of(kind)
            q = rng.choice(LEX_FORMS[kind]).format(w=S, a=f"{article(S)} {S}")
            self._keep({"kind": kind, "question": q, "plan": f"$1 = FIND {S}\n$2 = GET $1 {LEX[kind]}\nANSWER $2",
                        "gold": [gold_of(f) for f in fs], "expect": "answered", "subjects": [s]}, out)
        defs = facts_of("define")
        if len(defs) >= 2 and rng.random() < 0.4:        # which sense: a word only that sense's text has
            i = rng.randrange(len(defs))
            others = set().union(*(set(re.findall(r"[a-z]{4,}", d.value.lower())) for j, d in enumerate(defs) if j != i))
            cue = [w for w in re.findall(r"[a-z]{4,}", defs[i].value.lower()) if w not in others and w not in S
                   and w not in ("that", "with", "from", "which", "especially", "used", "having", "something")]
            if cue:
                w = rng.choice(cue)
                self._keep({"kind": "sense", "question": f"what does {S} mean, as in {w}",
                            "plan": f"$1 = FIND {S}\n$2 = GET $1 {LEX['define']}\n$3 = RANK $2 {w}\nANSWER $3",
                            "gold": [clean_definition(defs[i].value)],
                            "not": [clean_definition(d.value) for j, d in enumerate(defs) if j != i],
                            "expect": "answered", "subjects": [s]}, out)
        ants = facts_of("antonym")
        if ants and rng.random() < 0.5:
            v = key_of(ants[0].value)
            vdefs = [clean_definition(f.value) for f in self.nav.facts(v, self.year) if f.relation == "DEFINITION"][:3]
            if vdefs and speakable(v):
                self._keep({"kind": "lex_chain", "question": rng.choice(LEX_CHAIN_FORMS).format(w=S),
                            "plan": f"$1 = FIND {S}\n$2 = GET $1 {LEX['antonym']}\n$3 = GET $2 {LEX['define']}\nANSWER $3",
                            "gold": vdefs, "expect": "answered", "subjects": [s, v]}, out)
        for kind in ("synonym", "antonym"):
            fs = [f for f in facts_of(kind) if speakable(key_of(f.value))]
            if fs:
                self.lex_pool.append((s, kind))
                if rng.random() < 0.4:
                    y = value_of(rng.choice(fs))
                    self._keep({"kind": "lex_yes", "question": rng.choice(LEX_TEST_FORMS[kind]).format(w=S, y=y),
                                "plan": f"$1 = FIND {S}\n$2 = GET $1 {LEX[kind]}\n$3 = TEST $2 {y}\nANSWER $3",
                                "gold": ["yes"], "expect": "answered", "subjects": [s]}, out)
        if defs and not ants and rng.random() < 0.15:
            self._keep({"kind": "missing", "question": f"what is the opposite of {S}",
                        "plan": f"$1 = FIND {S}\n$2 = GET $1 {LEX['antonym']}\nANSWER $2",
                        "gold": [], "expect": "unsupported", "subjects": [s]}, out)

    def lex_no(self, out: list, n: int):
        """'is sad a synonym of happy' when the corpus links them no such way: the answer is 'I can't find that'."""
        words = [w for w, _ in self.lex_pool]
        for _ in range(n):
            if len(self.lex_pool) < 2:
                return
            (s, kind), other = self.rng.choice(self.lex_pool), self.rng.choice(words)
            linked = {key_of(f.value) for f in self.nav.facts(s, self.year) if f.relation in LEX_RELS_OF[kind]}
            if other == s or other in linked:
                continue
            self._keep({"kind": "lex_no", "question": self.rng.choice(LEX_TEST_FORMS[kind]).format(w=nm(s), y=nm(other)),
                        "plan": f"$1 = FIND {nm(s)}\n$2 = GET $1 {LEX[kind]}\n$3 = TEST $2 {nm(other)}\nANSWER $3",
                        "gold": [], "expect": "unsupported", "subjects": [s]}, out)

    def from_subject(self, s: str, out: list, lexical: float = 1.0):
        if not speakable(s):
            return
        if lexical and self.rng.random() < lexical:
            self.lexical(s, out)
        facts = self.facts(s)
        if not facts:
            return
        by = defaultdict(list)
        for f in facts:
            by[f.relation].append(f)
        S = nm(s)
        rng = self.rng
        for f in facts:
            if f.kind == "N" and f.relation in ADJECTIVE:
                self.numeric[(f.relation, f.unit)].append((s, f))
        # one hop
        rels = [r for r, fs in by.items() if len(fs) <= 3 and r != "IS_A"]
        if rels:
            r = rng.choice(rels)
            self._keep({"kind": "hop", "question": rng.choice(hop_forms(r, S)),
                        "plan": f"$1 = FIND {S}\n$2 = GET $1 {r}\nANSWER $2",
                        "gold": [value_of(f) for f in by[r]], "expect": "answered", "subjects": [s]}, out)
        # two hops
        hops = [(r, f) for r, fs in by.items() for f in fs if f.kind == "S" and r != "IS_A" and len(fs) == 1]
        rng.shuffle(hops)
        for r1, f1 in hops[:3]:
            v = key_of(f1.value)
            if not speakable(v) or shape(r1)[0] != "noun":
                continue
            vf = defaultdict(list)
            for f in self.facts(v):
                if key_of(f.value) != s and f.relation != "IS_A":
                    vf[f.relation].append(f)
            r2s = [r for r, fs in vf.items() if len(fs) <= 3]
            if not r2s:
                continue
            r2 = rng.choice(r2s)
            k2, t2, base2 = shape(r2)
            t1 = shape(r1)[1]
            if k2 == "noun":
                q = rng.choice([f"what is the {t2} of the {t1} of {S}", f"what is the {t2} of {S}'s {t1}"])
            elif k2 == "verb":
                q = f"what does the {t1} of {S} {base2}"
            else:
                continue
            self._keep({"kind": "chain", "question": q,
                        "plan": f"$1 = FIND {S}\n$2 = GET $1 {r1}\n$3 = GET $2 {r2}\nANSWER $3",
                        "gold": [value_of(f) for f in vf[r2]], "expect": "answered", "subjects": [s, v]}, out)
            break
        # count
        many = [r for r, fs in by.items() if len(fs) >= 2 and all(f.kind == "S" for f in fs) and r != "IS_A"]
        if many:
            r = rng.choice(many)
            k, t, base = shape(r)
            if k == "noun":
                q = f"how many {plural_of(t)} does {S} have"
            elif k == "verb":
                q = f"how many things does {S} {base}"
            else:
                q = ""
            if q:
                self._keep({"kind": "count", "question": q,
                        "plan": f"$1 = FIND {S}\n$2 = GET $1 {r}\n$3 = COUNT $2\nANSWER $3",
                            "gold": [str(len({f.value for f in by[r]}))], "expect": "answered", "subjects": [s]}, out)
        # is-a, yes and no
        isa = [f for f in by.get("IS_A", []) if speakable(key_of(f.value))]
        if isa:
            f = rng.choice(isa)
            anc = self.nav.ancestors(key_of(f.value), self.year)
            target = key_of(f.value) if not anc or rng.random() < 0.5 else rng.choice(anc)[-1]
            if speakable(target):
                self._keep({"kind": "test_yes", "question": f"is {S} a {nm(target)}",
                            "plan": f"$1 = FIND {S}\n$2 = GET $1 IS_A\n$3 = TEST $2 {nm(target)}\nANSWER $3",
                            "gold": ["yes"], "expect": "answered", "subjects": [s]}, out)
        # reverse
        for f in facts:
            if f.kind == "S" and f.relation != "IS_A" and rng.random() < 0.5:
                v = key_of(f.value)
                k, t, base = shape(f.relation)
                if not speakable(v) or k == "noun":
                    continue
                q = f"what {t} {nm(v)}" if k == "verb" else f"what is {t} {nm(v)}"
                self._keep({"kind": "reverse", "question": q, "plan": f"$1 = FIND {nm(v)}\n$2 = WHO $1 {f.relation}\nANSWER $2",
                            "gold": [name_of(s)], "expect": "answered", "subjects": [v, s]}, out)
                break
        # missing: a relation it doesn't have (the right plan, and the right answer is "I don't know")
        vocab = (self.corpus.meta or {}).get("relation_names", [])
        have_words = set().union(*(rel_words(r) for r in by)) if by else set()
        lacks = [r for r in vocab if r not in by and r not in SKIP_RELS and r != "IS_A" and shape(r)[0] == "noun"
                 and " of " not in shape(r)[1] and not (rel_words(r) & have_words)]   # "part" vs PART_OF: not missing
        if lacks and rng.random() < 0.3:
            r = rng.choice(lacks)
            self._keep({"kind": "missing", "question": f"what is the {shape(r)[1]} of {S}",
                        "plan": f"$1 = FIND {S}\n$2 = GET $1 {r}\nANSWER $2",
                        "gold": [], "expect": "unsupported", "subjects": [s]}, out)

    def comparisons(self, out: list, per_rel: int = 40):
        for (rel, unit), rows in self.numeric.items():
            rows = [r for r in rows if speakable(r[0])]
            self.rng.shuffle(rows)
            for (a, fa), (b, fb) in list(zip(rows[0::2], rows[1::2]))[:per_rel]:
                try:
                    va, vb = float(fa.value), float(fb.value)
                except ValueError:
                    continue
                if va == vb:
                    continue
                adj, d = ADJECTIVE[rel]
                win = a if (va > vb) == (d == "MORE") else b
                self._keep({"kind": "compare", "question": f"which is {adj}, {nm(a)} or {nm(b)}",
                            "plan": f"$1 = FIND {nm(a)}\n$2 = FIND {nm(b)}\n$3 = GET $1 {rel}\n$4 = GET $2 {rel}\n"
                                    f"$5 = COMPARE $3 $4 {d} {adj}\nANSWER $5",
                            "gold": [name_of(win)], "expect": "answered", "subjects": [a, b]}, out)

    def test_no(self, out: list, n: int):
        pool = [(e["subjects"][0], e["question"].split(" a ", 1)[-1]) for e in out if e["kind"] == "test_yes"]
        for _ in range(n):
            if len(pool) < 2:
                return
            (s, _), (_, other) = self.rng.sample(pool, 2)
            target = key_of(other)
            if self.nav._is_a(s, target, self.year) is not None:
                continue
            self._keep({"kind": "test_no", "question": f"is {nm(s)} a {other}",
                        "plan": f"$1 = FIND {nm(s)}\n$2 = GET $1 IS_A\n$3 = TEST $2 {other}\nANSWER $3",
                        "gold": [], "expect": "unsupported", "subjects": [s]}, out)


    # ------------------------------------------------------------------ yes/no with a definite answer

    def truths(self, out: list, n: int):
        """FALSE through a knowledge rule, and/or/neither-nor/not, and "does titan orbit jupiter".
        Built from the test_yes examples (a thing and a class it really is), each one executed and kept only
        if the computer's answer starts with the right head."""
        k = self.nav.knowledge
        pool = [(e["subjects"][0], key_of(e["question"].split(" a ", 1)[-1])) for e in out if e["kind"] == "test_yes"]
        self.rng.shuffle(pool)
        made = 0
        for s, yes in pool:
            if made >= n:
                break
            mine = {yes} | {p[-1] for p in self.nav.ancestors(s, self.year)}
            nos = sorted({c for d in k.disjoint for c in d.classes if mine & d.classes and c not in mine
                          and (self.corpus.has(c) or self.nav.resolve(nm(c)))})
            if not nos:
                continue
            S, Y = nm(s), nm(yes)
            no, no2 = self.rng.choice(nos), self.rng.choice(nos)
            N, N2 = nm(no), nm(no2)
            a = lambda w: article(w)
            body = f"$1 = FIND {S}\n$2 = GET $1 IS_A\n"
            two = lambda x, y, op: body + f"$3 = TEST $2 {x}\n$4 = TEST $2 {y}\n$5 = LOGIC {op} $3 $4\nANSWER $5"
            one = lambda x: body + f"$3 = TEST $2 {x}\n$4 = LOGIC NOT $3\nANSWER $4"
            cases = [
                ("test_false", f"is {S} {a(N)} {N}", body + f"$3 = TEST $2 {N}\nANSWER $3", HEADS["TEST", "FALSE"]),
                ("logic", f"is {S} {a(Y)} {Y} and {a(N)} {N}", two(Y, N, "AND"), HEADS["AND", "FALSE"]),
                ("logic", f"is {S} {a(N)} {N} or {a(Y)} {Y}", two(N, Y, "OR"), HEADS["OR", "TRUE"]),
                ("logic", f"is {S} not {a(N)} {N}", one(N), HEADS["NOT", "TRUE"]),
                ("logic", f"is {S} not {a(Y)} {Y}", one(Y), HEADS["NOT", "FALSE"]),
            ]
            if no2 != no:
                cases += [("logic", f"is {S} neither {a(N)} {N} nor {a(N2)} {N2}", two(N, N2, "NOR"), HEADS["NOR", "TRUE"]),
                          ("logic", f"is {S} {a(N)} {N} or {a(N2)} {N2}", two(N, N2, "OR"), HEADS["OR", "FALSE"])]
            for kind, q, plan, head in cases:
                before = len(out)
                self._keep({"kind": kind, "question": q, "plan": plan, "gold": [head], "expect": "answered",
                            "subjects": [s]}, out)
                made += len(out) - before
        # relations a rule says are complete: "does titan orbit saturn" (yes) and "... jupiter" (no)
        by_rel = defaultdict(list)
        for s in sorted(self.corpus.subjects):
            for f in self.nav.facts(s, self.year):
                if f.relation in k.complete and f.kind == "S" and speakable(s) and speakable(key_of(f.value)):
                    by_rel[f.relation].append((s, key_of(f.value)))
        for rel, pairs in by_rel.items():
            kshape, t, base = shape(rel)
            say = (lambda s, v: f"does {nm(s)} {base} {nm(v)}") if kshape == "verb" else \
                  (lambda s, v: f"is the {t} of {nm(s)} {nm(v)}")
            values = sorted({v for _, v in pairs})
            for s, v in pairs[: max(2, n // 4)]:
                plan = lambda v: f"$1 = FIND {nm(s)}\n$2 = GET $1 {rel}\n$3 = TEST $2 {nm(v)}\nANSWER $3"
                self._keep({"kind": "rel_test", "question": say(s, v), "plan": plan(v), "gold": [HEADS["TEST", "TRUE"]],
                            "expect": "answered", "subjects": [s]}, out)
                other = [w for w in values if w not in (v, s) and self.corpus.has(w)]
                if other:
                    w = self.rng.choice(other)
                    self._keep({"kind": "rel_test", "question": say(s, w), "plan": plan(w),
                                "gold": [HEADS["TEST", "FALSE"]], "expect": "answered", "subjects": [s]}, out)


def record(ex: dict, r, plan) -> None:
    """What the run used and found, kept with the example (see the top of this file)."""
    ex["evidence"] = [{"s": f.subject, "r": f.relation, "v": f.value, "unit": f.unit or "", "source": f.source,
                       "conf": f.conf, "derived": ">".join(via)} for f, via in zip(r.evidence, r.inherited or
                                                                             [[] for _ in r.evidence])]
    ex["rules"] = list(r.rules)
    ex["steps"] = list(r.steps)
    ex["final"] = r.final
    if r.truth:
        ex["truth"] = r.truth
    concepts = set(ex.get("subjects", [])) | set(r.subjects)
    for f in r.evidence:
        concepts.add(f.subject)
        if f.kind == "S":
            concepts.add(key_of(f.value))
    for st in plan.steps:
        if st.op in ("TEST", "FILTER", "FIND") and st.text:
            concepts.add(key_of(st.text))
    ex["concepts"] = sorted(c for c in concepts if c)


def leak_free(examples: list[dict]) -> tuple[list[dict], int]:
    """Hold out *things*, not just questions.  The subjects of dev/test examples are held out; a training example
    that touches any of them anywhere (subject, tested class, evidence) is dropped.  Returns (kept, dropped)."""
    held = {s for e in examples if e.get("split") in ("dev", "test") for s in e.get("subjects", [])}
    kept, dropped = [], 0
    for e in examples:
        if e.get("split", "train") == "train" and held & set(e.get("concepts", e.get("subjects", []))):
            dropped += 1
            continue
        kept.append(e)
    return kept, dropped


def generate(corpus: Corpus, n_subjects: int = 5000, seed: int = 0, year: int = 0, progress=None,
             lexical: float = 1.0, truths: bool = True, subjects: list | None = None) -> list[dict]:
    """`lexical`: the share of subjects that also get dictionary questions (0 = none, as before).
    `truths`: also FALSE / and / or / not / "does titan orbit jupiter" examples (Generator.truths)."""
    g = Generator(corpus, seed, year)
    keys = sorted(subjects if subjects is not None else corpus.subjects)
    random.Random(seed).shuffle(keys)
    out: list[dict] = []
    for i, s in enumerate(keys[:n_subjects]):
        g.from_subject(s, out, lexical)
        if progress and i % 500 == 0:
            progress(i, len(out))
    g.comparisons(out)
    g.test_no(out, n=max(1, sum(e["kind"] == "test_yes" for e in out) // 2))
    if truths:
        # about as many as the plain yes questions: enough to learn the shapes, not so many they crowd out the rest
        g.truths(out, n=max(1, sum(e["kind"] == "test_yes" for e in out)))
    g.lex_no(out, n=max(1, sum(e["kind"] == "lex_yes" for e in out) // 2) if g.lex_pool else 0)
    for i, e in enumerate(out):
        e["id"] = f"{e['kind']}-{i}"
    return out


def as_chat(ex: dict, fmt: str = "plan") -> dict:
    """The training row, in the exact chat shape the run-time caller sends (no few-shot):
        plan     LMPlanner            -> the plan
        propose  Proposer             -> the plan, then "SAY: <words>"
        direct   Proposer(direct)     -> "SAY: <words>" only: knowledge distilled into the weights
    <words> is a checked paraphrase when there is one (make_navigator_data --paraphrase), else the
    executor's own sentence; for questions the corpus can't answer it is the computer's "I don't know"."""
    from .proposer import SYSTEM, SYSTEM_DIRECT
    q = {"role": "user", "content": f"Q: {ex['question']}\n"}
    say = "SAY: " + (ex.get("say") or ex["answer"]) + "\n"
    if fmt == "plan":
        return {"messages": [{"role": "system", "content": LM_SYSTEM}, q, {"role": "assistant", "content": ex["plan"] + "\n"}]}
    if fmt == "propose":
        return {"messages": [{"role": "system", "content": SYSTEM}, q, {"role": "assistant", "content": ex["plan"] + "\n" + say}]}
    if fmt == "direct":
        return {"messages": [{"role": "system", "content": SYSTEM_DIRECT}, q, {"role": "assistant", "content": say}]}
    raise ValueError(fmt)


# ---------------------------------------------------------------------- evaluation

class LookupSystem:
    """What the computer did before the navigator: one Corpus.ask, the first block if it matched."""
    label = "lookup"

    def __init__(self, corpus: Corpus):
        self.corpus = corpus

    def answer(self, q: str) -> tuple[str, bool]:
        from .phrase import phrase_block
        self.corpus.last_subject = ""
        a = self.corpus.ask(q)
        if not a["found"] or not a["blocks"][0]["matched"]:
            return "", False
        return phrase_block(a["blocks"][0]), True


class NavigatorSystem:
    def __init__(self, corpus: Corpus, planners: list, label: str):
        self.corpus = corpus
        self.nav = Navigator(corpus)
        self.planners = planners
        self.label = label
        self.invalid = 0

    def answer(self, q: str) -> tuple[str, bool]:
        from .planner import navigate
        self.corpus.last_subject = ""
        plan, r = navigate(q, self.planners, self.nav)
        if r is None:
            return "", False
        return r.text, r.answered


class ProposerSystem:
    """The model proposes plan + words (or words only); the executor checks.  Counts how often
    the model's own words survived the check."""

    def __init__(self, corpus: Corpus, proposer, fallback: list, label: str):
        self.corpus, self.nav = corpus, Navigator(corpus)
        self.proposer, self.fallback, self.label = proposer, fallback, label
        self.accepted = self.asked = 0
        self.reasons: Counter = Counter()

    def answer(self, q: str) -> tuple[str, bool]:
        from .proposer import propose_and_verify
        self.corpus.last_subject = ""
        trace: list = []
        plan, r, words = propose_and_verify(q, self.proposer, self.nav, self.fallback, trace=trace)
        self.asked += 1
        if words:
            self.accepted += 1
        else:
            why = next((t.split("rejected: ", 1)[1] for t in trace if "rejected: " in t), "")
            self.reasons[re.sub(r"[:(\[].*", "", why).strip() or "no usable proposal / no answer"] += 1
        if r is None:
            return "", False
        return (words or r.text), r.answered


def score(ex: dict, text: str, committed: bool) -> str:
    """'right', 'wrong' (said something that isn't the answer) or 'abstained'."""
    t = text.lower()
    if ex["expect"] == "unsupported":
        if ex["kind"] in NO_KINDS:
            return "wrong" if t.startswith("yes") else "right"
        return "wrong" if committed else "right"
    if not committed:
        return "abstained"
    if any(n.lower() in t for n in ex.get("not", [])):
        return "wrong"            # said the answer, but also a sense it shouldn't have
    if ex["kind"] == "compare":
        return "right" if f"so {ex['gold'][0].lower()} " in t else "wrong"
    if ex["kind"] in YES_KINDS:
        return "right" if t.startswith("yes") else "wrong"
    if ex["kind"] in HEAD_KINDS:
        return "right" if t.startswith(ex["gold"][0]) else "wrong"
    return "right" if any(g.lower() in t for g in ex["gold"]) else "wrong"


def evaluate(examples: list[dict], systems: list) -> dict:
    report = {}
    for sysm in systems:
        by_kind = defaultdict(Counter)
        ms = 0.0
        for ex in examples:
            t0 = time.monotonic()
            text, committed = sysm.answer(ex["question"])
            ms += (time.monotonic() - t0) * 1000
            by_kind[ex["kind"]][score(ex, text, committed)] += 1
        total = sum(by_kind.values(), Counter())
        report[sysm.label] = {"by_kind": {k: dict(v) for k, v in sorted(by_kind.items())}, "total": dict(total),
                              "accuracy": total["right"] / max(1, sum(total.values())),
                              "ms_per_question": ms / max(1, len(examples))}
        if hasattr(sysm, "accepted"):
            report[sysm.label]["model_words_used"] = sysm.accepted / max(1, sysm.asked)
            report[sysm.label]["rejections"] = dict(sysm.reasons.most_common(6))
    return report


def format_report(report: dict, n: int) -> str:
    systems = list(report)
    kinds = sorted({k for r in report.values() for k in r["by_kind"]})
    head = f"{'kind':<10}" + "".join(f"{s:>22}" for s in systems)
    lines = [f"{n} questions.  right / wrong / abstained (wrong = said something that isn't the answer)", "", head,
             "-" * len(head)]
    for k in kinds + ["ALL"]:
        row = f"{k:<10}"
        for s in systems:
            c = report[s]["total"] if k == "ALL" else report[s]["by_kind"].get(k, {})
            tot = sum(c.values()) or 1
            row += f"{c.get('right', 0) / tot:>9.0%} {c.get('right', 0):>3}/{c.get('wrong', 0)}/{c.get('abstained', 0):<5}"
        lines.append(row)
    lines.append("")
    lines += [f"{s}: {report[s]['ms_per_question']:.1f} ms per question" for s in systems]
    for s in systems:
        if "model_words_used" in report[s]:
            lines.append(f"{s}: the model's own words were used for {report[s]['model_words_used']:.0%} of questions; "
                         f"otherwise: {report[s]['rejections']}")
    return "\n".join(lines)
