"""Training records: everything the neural core learns from, and how each label was decided.

One record per question (cdos-neural-record/1):

    question, split ("train" | "heldout"), source ("teacher" | "reworded" | "mine")
    inputs        features (strings, so a later schema can re-hash them), idx (hashed), names, free words
    candidates    every action tried: its plan, the plan validator's verdict (with the corpus's relation names),
                  the executor's status and evidence fact ids, accept()'s verdict, and the outcome
    targets       the actions the label rule chose ([] = nothing reached the answer: kept, never trained on)
    label_rule    how: the simplest actions whose plan reached the known answer and passed accept()
    provenance    kind, gold, expect, subjects, concepts, teacher action and plan, reworded_from, example id, origin file
    corpus_version, year

The label rule is the existing one (computer/controller_train.py explore()): tests/test_neural.py checks that
explore_detailed() picks exactly the same targets.  What's added is the record of what was tried.
"""

from __future__ import annotations

import hashlib
import json
import re

from computer.controller import ACTION_NAMES, action_of, fills, singular
from computer.controller_train import reached
from computer.corpus import key_of
from computer.neural.schema import encode
from computer.plan import PlanError, parse

RECORD_FORMAT = "cdos-neural-record/1"
LABEL_EXPLORED = "explored: the simplest actions whose plan reached the known answer and passed accept()"
LABEL_TEACHER = "teacher: the rule planner's action (the corpus can't answer it, so exploring says nothing)"


def norm_q(q: str) -> str:
    """A question as the leak check compares them: lower case, words only."""
    return " ".join(re.findall(r"[a-z0-9]+", (q or "").lower().replace("'s", "")))


def explore_detailed(ex: dict, ctl, actions=ACTION_NAMES, year: int = 0, rels=None) -> tuple[list[str], int, list[dict]]:
    """controller_train.explore(), step for step, keeping each candidate: (targets, plans run, candidates)."""
    q = ex["question"]
    r = ctl.reader.read(q)
    good, runs, cands = {}, 0, []
    numeric = all(re.fullmatch(r"[0-9][0-9,.]*", g) for g in ex.get("gold", [])) and ex.get("gold")
    for a in actions:
        texts = fills(a, r)
        if not texts:
            cands.append({"action": a, "plan": None, "outcome": "no_fill"})
            continue
        for text in texts:
            p = parse(text)
            c = {"action": a, "plan": text, "validator": "ok"}
            try:
                parse(text, rels)
            except PlanError as e:
                c["validator"] = f"refused: {e}"
            cands.append(c)
            if any(st.op == "COUNT" for st in p.steps) and not numeric:
                c["outcome"] = "skipped: a count can't answer a non-numeric question"
                continue
            res = ctl.nav.run(p, year=year)
            runs += 1
            c["status"] = res.status
            c["evidence"] = [f.id for f in res.evidence][:24]
            if res.answered:
                why = ctl.accept(q, p, res)
                c["accept"] = why
                if why:
                    c["outcome"] = "rejected by accept()"
                    continue
            if any("used its own relation instead" in t for t in res.steps):
                c["outcome"] = "fell back to the forward relation"
                continue
            if ex["expect"] == "answered" and reached(ex, res):
                c["outcome"] = "reached the answer"
                good[a] = len(p.steps)
                break
            c["outcome"] = "answered, not the known answer" if res.answered else "no answer"
    best = min(good.values(), default=0)
    out = [a for a, n in good.items() if n == best]
    if "hop" in out and "hop_inherit" in out:
        out.remove("hop_inherit")
    return out, runs, cands


def record_id(source: str, split: str, ex: dict) -> str:
    body = json.dumps([source, split, ex["question"], ex.get("plan") or "", ex.get("reworded_from") or ""])
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def make_record(ex: dict, ctl, *, source: str, split: str, year: int, corpus_version: str, rels=None,
                detail: bool = True) -> dict:
    r = ctl.reader.read(ex["question"])
    feats, idx = encode(r, ctl.reader)
    teacher = ex.get("teacher") or (action_of(ex["plan"]) if ex.get("plan") else None)
    if ex["expect"] == "answered":
        targets, runs, cands = explore_detailed(ex, ctl, year=year, rels=rels)
        rule = LABEL_EXPLORED
    else:
        targets, runs, cands, rule = ([teacher] if teacher else []), 0, [], LABEL_TEACHER
    return {"format": RECORD_FORMAT, "id": record_id(source, split, ex), "question": ex["question"], "split": split,
            "source": source,
            "inputs": {"features": feats, "idx": idx, "names": [s.phrase for s in r.spans if not s.relish],
                       "relation_words": [s.phrase for s in r.spans if s.relish], "free": [r.toks[i] for i in r.free]},
            "candidates": cands if detail else [c for c in cands if c.get("outcome") == "reached the answer"],
            "targets": targets, "label_rule": rule, "runs": runs,
            "provenance": {"kind": ex.get("kind", ""), "gold": list(ex.get("gold", [])), "expect": ex["expect"],
                           "subjects": list(ex.get("subjects", [])), "concepts": list(ex.get("concepts", [])),
                           "teacher_action": teacher, "teacher_plan": ex.get("plan"),
                           "reworded_from": ex.get("reworded_from"), "example_id": ex.get("id"),
                           "origin": ex.get("origin")},
            "corpus_version": corpus_version, "year": year}


def rows_of(records: list[dict], split: str = "train") -> list[dict]:
    """What the trainer sees: {idx, targets} for the records of one split that have a target."""
    return [{"idx": r["inputs"]["idx"], "targets": r["targets"], "question": r["question"]}
            for r in records if r["split"] == split and r["targets"]]


def dataset_hash(records: list[dict]) -> str:
    h = hashlib.sha256()
    for r in records:
        h.update(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        h.update(b"\n")
    return "sha256:" + h.hexdigest()


def names_in(record: dict) -> set[str]:
    """The corpus keys a record's question names (as the reader found them).  A word read as a relation ("capital" in
    "france capital city") isn't a name here, as navdata.leak_free doesn't count it: the thing asked about is France."""
    out = set()
    for n in record["inputs"]["names"]:
        k = key_of(n.replace(" ", "_"))
        out |= {k, key_of(singular(n.lower()).replace(" ", "_"))}
    return out


def leak_report(train: list[dict], held_subjects: set[str], fixtures: dict[str, list[str]]) -> dict:
    """What a training record shares with the held-out data: subjects (by provenance and by name) and question text."""
    concept = [r["id"] for r in train if held_subjects & set(r["provenance"]["concepts"] or r["provenance"]["subjects"])]
    named = [r["id"] for r in train if held_subjects & names_in(r)]
    seen = {norm_q(r["question"]) for r in train}
    text = {name: sorted({q for q in qs if norm_q(q) in seen}) for name, qs in fixtures.items()}
    return {"held_out_subjects": len(held_subjects), "train_records": len(train),
            "concept_leaks": len(concept), "name_mentions": len(named),
            "question_overlap": {k: len(v) for k, v in text.items()},
            "question_overlap_examples": {k: v[:5] for k, v in text.items() if v}}
