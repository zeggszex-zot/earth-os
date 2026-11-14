"""Training the learned controller (computer/controller.py).  Used by tools/train_controller.py.

    teacher examples (computer/navdata.generate: the rule planner's plans, each one executed and checked)
      + re-wordings of them (reword(): new phrasings, *no* label, only the known answer)
      + your own questions with answers (any JSONL with question / gold / expect)
    --explore()--> for every question, try every action on the corpus; the ones that reach the known
                   answer and pass accept() are the target.  Nobody labels a re-worded question.
    --train()----> the network learns to predict those actions from the question's shape

The reward is deterministic: the executor's evidence has to contain the gold answer, and accept()
has to agree that the answer is about everything the question named.  "The plan answered" is not
enough on its own: a plan can answer the wrong question with true facts.
"""

from __future__ import annotations

import random
import re
from collections import Counter

from .controller import ACTION_NAMES, ControllerPlanner, Network, action_of, features, fills, hashed
from .navdata import HEAD_KINDS, YES_KINDS, plural_of, rw, score, shape
from .phrase import value_of
from .plan import parse

# ---------------------------------------------------------------------- re-wordings

FORMS = {
    "hop_noun": ["{s} {t}", "{t} of {s}", "the {t} for {s}", "{s}'s {t}?", "tell me the {t} for {s} please",
                 "do you know {s}'s {t}", "{t}, {s}", "i want the {t} of {s}", "quick question: {t} of {s}",
                 "what about {s}'s {t}"],
    "hop_verb": ["what is it that {s} {v}s", "{s} {v}s what", "things {s} {v}s", "tell me what {s} {v}s"],
    "chain": ["{t2} of {s}'s {t1}", "{s} {t1} {t2}", "the {t2} for the {t1} of {s}", "tell me the {t2} of {s}'s {t1}"],
    "compare": ["{a} or {b}: which is {adj}", "which one is {adj}, {a} or {b}", "{adj}, {a} or {b}?",
                "between {a} and {b} which is {adj}", "{a} compared with {b}, which is {adj}"],
    "count": ["number of {t} {s} has", "{s} {t} count", "count the {t} of {s}", "how many {t} for {s}",
              "{s}: how many {t}", "total {t} of {s}"],
    "test_isa": ["{s} is a {y}, correct?", "would you call {s} a {y}", "{s} a {y}?", "i think {s} is a {y}",
                 "can you confirm {s} is a {y}", "{s}: a kind of {y}?"],
    "reverse": ["what things {t} {o}", "name everything that {t} {o}", "list what {t} {o}", "tell me what {t} {o}"],
    "define": ["{w} meaning please", "explain {w} to me", "what does {w} even mean", "{w}: definition",
               "i need the meaning of {w}", "meaning of {w}?", "the word {w}, what does it mean"],
    "synonym": ["{w} in other words", "another way of saying {w}", "words like {w}", "similar word to {w}",
                "a word with the same meaning as {w}", "{w} alternative word"],
    "antonym": ["{w} opposite", "opposite word for {w}", "the contrary of {w}", "what's the opposite to {w}",
                "antonym for {w} please"],
    "hypernym": ["{w} is a type of what", "what category is {w} in", "{w}: what kind of thing",
                 "which class does {w} belong to"],
    "hyponym": ["types of {w}", "{w} kinds", "list some kinds of {w}", "{w} varieties"],
    "derivation": ["words that come from {w}", "related words for {w}"],
    "example": ["{w} in a sentence", "sentence with {w} please", "show {w} in a sentence"],
    "lex_test_synonym": ["{w} and {y}, same meaning?", "does {w} mean {y}", "{w} is a synonym for {y}, right"],
    "lex_test_antonym": ["{w} and {y} are opposites?", "{w} the opposite of {y}?"],
    "lex_chain": ["meaning of {w}'s opposite", "define {w}'s opposite"],
    "logic_and": ["is {s} both a {y} and a {z}", "{s}: {y} and {z}?", "would you say {s} is a {y} and a {z}",
                  "{s} is a {y} and also a {z}, right", "can {s} be a {y} and a {z} at once", "{s} - both {y} and {z}?"],
    "logic_or": ["{s}: {y} or {z}?", "would you call {s} a {y} or a {z}", "is {s} either a {y} or a {z}",
                 "{s} is a {y} or a {z}, right", "either {y} or {z}: is that {s}", "{s}, a {y} or a {z}?"],
    "logic_nor": ["{s} is neither {y} nor {z}, right", "neither a {y} nor a {z}: {s}?", "is {s} neither {y} nor {z}",
                  "would you say {s} is neither a {y} nor a {z}", "{s}: neither {y} nor {z}?",
                  "confirm {s} is neither a {y} nor a {z}"],
    "logic_not": ["{s} isn't a {y}, right", "{s} is not a {y}, correct?", "would you say {s} is not a {y}",
                  "{s}: not a {y}?", "can you confirm {s} is not a {y}", "i think {s} is not a {y}"],
    "test_rel_verb": ["{s} {v}s {o}, right?", "is it true that {s} {v}s {o}", "{s}: {v}s {o}?",
                      "would you say {s} {v}s {o}", "confirm {s} {v}s {o}", "i think {s} {v}s {o}"],
    "test_rel_noun": ["{o} is the {t} of {s}, right?", "is {o} the {t} of {s}", "{s}'s {t} is {o}?",
                      "would you say {o} is {s}'s {t}", "the {t} of {s}: {o}?", "i think {s}'s {t} is {o}"],
}
# Held-out phrasings: reword(..., which="test") uses only these forms (every other one), "train" the rest,
# so an experiment can ask whether the controller handles wordings it was never trained on.
def forms_for(key: str, which: str = "all") -> list[str]:
    fs = FORMS.get(key, [])
    return fs if which == "all" else fs[1::2] if which == "test" else fs[0::2]


def _words_of(rel: str) -> str:
    return rel[1:].split(",")[0] if rel.startswith("~") else rw(rel)


def reword(ex: dict, rng: random.Random, n: int = 3, which: str = "all") -> list[dict]:
    """New phrasings of a teacher example.  They keep its answer (gold, expect) and lose its plan:
    which action answers them is for explore() to find out.  which: "all", or "train" / "test" for
    disjoint sets of phrasings (forms_for)."""
    p = parse(ex["plan"])
    act = action_of(p)
    finds = [s.text for s in p.steps if s.op == "FIND"]
    rel = next((s.args[1] for s in p.steps if s.op in ("GET", "WHO")), "")
    forms, slots = [], {}
    if act in ("hop", "hop_inherit") and ex["kind"] in ("hop", "missing"):
        k, t, base = shape(rel) if not rel.startswith("~") else ("noun", _words_of(rel), "")
        if k == "noun":
            forms, slots = FORMS["hop_noun"], {"s": finds[0], "t": t}
        elif k == "verb":
            forms, slots = FORMS["hop_verb"], {"s": finds[0], "v": base}
    elif act == "chain":
        rels = [s.args[1] for s in p.steps if s.op == "GET"]
        forms, slots = FORMS["chain"], {"s": finds[0], "t1": _words_of(rels[0]), "t2": _words_of(rels[1])}
    elif act in ("compare_more", "compare_less"):
        adj = p.steps[4].text or ""
        if adj:
            forms, slots = FORMS["compare"], {"a": finds[0], "b": finds[1], "adj": adj}
    elif act == "count":
        forms, slots = FORMS["count"], {"s": finds[0], "t": plural_of(_words_of(rel))}
    elif act == "test_isa":
        forms, slots = FORMS["test_isa"], {"s": finds[0], "y": p.steps[2].text}
    elif act in ("logic_and", "logic_or", "logic_nor"):
        forms, slots = FORMS[act], {"s": finds[0], "y": p.steps[2].text, "z": p.steps[3].text}
    elif act == "logic_not":
        forms, slots = FORMS[act], {"s": finds[0], "y": p.steps[2].text}
    elif act == "test_rel":
        k, t, base = shape(rel) if not rel.startswith("~") else ("verb", "", _words_of(rel))
        o = p.steps[2].text
        if k == "verb":
            forms, slots = FORMS["test_rel_verb"], {"s": finds[0], "v": base, "o": o}
        elif k == "noun":
            forms, slots = FORMS["test_rel_noun"], {"s": finds[0], "t": t, "o": o}
    elif act == "reverse":
        k, t, base = shape(rel) if not rel.startswith("~") else ("verb", _words_of(rel), "")
        if k == "verb":
            forms, slots = FORMS["reverse"], {"t": t, "o": finds[0]}
    elif act in ("lex_test_synonym", "lex_test_antonym"):
        forms, slots = FORMS[act], {"w": finds[0], "y": p.steps[2].text}
    elif act in FORMS:
        forms, slots = FORMS[act], {"w": finds[0]}
    if not forms or ex["kind"] == "sense":      # a re-worded sense question loses the word that picks the sense
        return []
    if which != "all":
        forms = forms[1::2] if which == "test" else forms[0::2]
    out = []
    for f in rng.sample(forms, min(n, len(forms))):
        q = f.format(**slots)
        out.append({**{k: v for k, v in ex.items() if k not in ("plan", "answer", "id")},
                    "question": q, "reworded_from": ex.get("question"), "teacher": act})
    return out


# ---------------------------------------------------------------------- exploration

def reached(ex: dict, res) -> bool:
    """Did this run reach the known answer, *from the evidence*?  ("France is a country" contains "franc",
    but the currency question wasn't answered.)"""
    if ex["expect"] == "unsupported":
        return not res.answered
    if not res.answered or score(ex, res.text, True) != "right":
        return False
    gold = [g.lower().replace(",", "") for g in ex.get("gold", [])]
    if ex["kind"] == "compare" or ex["kind"] in YES_KINDS or ex["kind"] in HEAD_KINDS or gold == ["yes"]:
        return True
    final = [str(v) for v in (res.final or {}).get("values", [])]
    vals = " | ".join([value_of(f) for f in res.evidence] + [f.value for f in res.evidence] + final)
    vals = vals.lower().replace(",", "").replace("_", " ")
    return any(g in vals for g in gold)


def explore(ex: dict, ctl: ControllerPlanner, actions=ACTION_NAMES, year: int = 0) -> tuple[list[str], int]:
    """Try every action on this question.  (actions that reached the answer and passed accept(), plans run)."""
    r = ctl.reader.read(ex["question"])
    good, runs = {}, 0
    numeric = all(re.fullmatch(r"[0-9][0-9,.]*", g) for g in ex.get("gold", [])) and ex.get("gold")
    for a in actions:
        for text in fills(a, r):
            p = parse(text)
            if any(st.op == "COUNT" for st in p.steps) and not numeric:
                continue        # "My corpus lists 1 for Owl: mice" has the answer in it, but it's a count
            res = ctl.nav.run(p, year=year)
            runs += 1
            if res.answered and ctl.accept(ex["question"], p, res):
                continue
            if any("used its own relation instead" in t for t in res.steps):
                continue        # a reverse lookup that fell back to the forward one: the forward action is the label
            if ex["expect"] == "answered" and reached(ex, res):
                good[a] = len(p.steps)
                break
    # The simplest plans that work: a longer plan that also gets there (a chain through the answer,
    # a reverse lookup that fell back to the forward one) is not what the question asked for.
    best = min(good.values(), default=0)
    out = [a for a, n in good.items() if n == best]
    if "hop" in out and "hop_inherit" in out:
        out.remove("hop_inherit")       # inheriting is for when the thing itself has nothing
    return out, runs


def build_dataset(examples: list[dict], ctl: ControllerPlanner, progress=None) -> tuple[list[dict], Counter]:
    """examples -> rows {question, features, targets}.  Questions the corpus can't answer keep the teacher's
    action (any action "abstains" on them, so exploring them says nothing)."""
    rows, stats = [], Counter()
    for i, ex in enumerate(examples):
        if ex["expect"] == "answered":
            good, runs = explore(ex, ctl)
            stats["runs"] += runs
            if not good:
                stats["unreachable"] += 1
                continue
            stats["explored"] += 1
            if ex.get("teacher") and ex["teacher"] not in good:
                stats["found_other_than_teacher"] += 1
        else:
            t = ex.get("teacher") or (action_of(ex["plan"]) if ex.get("plan") else None)
            if not t:
                stats["skipped_unsupported"] += 1
                continue
            good = [t]
            stats["teacher_unsupported"] += 1
        r = ctl.reader.read(ex["question"])
        rows.append({"question": ex["question"], "idx": hashed(features(r, ctl.reader)), "targets": good,
                     "kind": ex.get("kind", ""), "split": ex.get("split", "train")})
        if progress and i % 200 == 0:
            progress(i, len(rows))
    return rows, stats


# ---------------------------------------------------------------------- training (numpy)

def train(rows: list[dict], actions=ACTION_NAMES, hidden: int = 32, epochs: int = 60, lr: float = 0.01,
          l2: float = 1e-4, seed: int = 0, dim: int = 4096, progress=None) -> Network:
    import numpy as np
    rng = np.random.default_rng(seed)
    A = len(actions)
    aix = {a: i for i, a in enumerate(actions)}
    W1 = rng.normal(0, 0.1, (dim, hidden))
    b1 = np.zeros(hidden)
    W2 = rng.normal(0, 0.1, (A, hidden))
    b2 = np.zeros(A)
    params = [W1, b1, W2, b2]
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    Y = np.zeros((len(rows), A))
    for i, r in enumerate(rows):
        for a in r["targets"]:
            Y[i, aix[a]] = 1.0 / len(r["targets"])
    t = 0
    order = np.arange(len(rows))
    for ep in range(epochs):
        rng.shuffle(order)
        total = 0.0
        for start in range(0, len(order), 32):
            batch = order[start:start + 32]
            gW1 = np.zeros_like(W1)
            gb1, gW2, gb2 = np.zeros_like(b1), np.zeros_like(W2), np.zeros_like(b2)
            for i in batch:
                idx = rows[i]["idx"]
                s = 1.0 / np.sqrt(max(1, len(idx)))
                pre = W1[idx].sum(0) * s + b1
                h = np.maximum(pre, 0)
                z = W2 @ h + b2
                z -= z.max()
                p = np.exp(z) / np.exp(z).sum()
                total -= float((Y[i] * np.log(p + 1e-12)).sum())
                dz = p - Y[i]
                gW2 += np.outer(dz, h)
                gb2 += dz
                dh = (W2.T @ dz) * (pre > 0)
                gW1[idx] += dh * s
                gb1 += dh
            grads = [gW1 / len(batch) + l2 * W1, gb1 / len(batch), gW2 / len(batch) + l2 * W2, gb2 / len(batch)]
            t += 1
            for k, (pm, g) in enumerate(zip(params, grads)):
                m[k] = 0.9 * m[k] + 0.1 * g
                v[k] = 0.999 * v[k] + 0.001 * g * g
                pm -= lr * (m[k] / (1 - 0.9 ** t)) / (np.sqrt(v[k] / (1 - 0.999 ** t)) + 1e-8)
        if progress:
            progress(ep, total / max(1, len(rows)))
    seen = sorted({i for r in rows for i in r["idx"]})
    return Network(list(actions), dim, hidden, {i: W1[i].tolist() for i in seen}, b1.tolist(), W2.tolist(), b2.tolist())


def top1(net, rows) -> float:
    """Share of rows whose best-scored action is one the checker accepted."""
    ok = 0
    for r in rows:
        sc = net.predict(r["idx"])
        ok += max(sc, key=sc.get) in r["targets"]
    return ok / max(1, len(rows))
