#!/usr/bin/env python3
"""Does the relation lexicon help on questions it never saw, and does the computer still admit what it doesn't know?

    python3 tools/eval_lexicon.py --corpus data --questions held_out.jsonl --lexicon lexicon.json [--lexicon other.json]

For each system (rules alone, then rules + each lexicon):
    answerable    right / wrong / abstained    (the corpus has the answer)
    unanswerable  admitted / wrong             (it doesn't: "I don't know" is the right answer)
Leak check: a held-out question about a thing an example-learned mapping rests on isn't a fair test of it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from computer.corpus import Corpus                     # noqa: E402
from computer.lexicon import Lexicon, subjects_of      # noqa: E402
from computer.navdata import score                     # noqa: E402
from computer.navigator import Navigator               # noqa: E402
from computer.planner import RulePlanner, navigate     # noqa: E402


def run(corpus, questions, lexicon):
    corpus.lexicon = lexicon
    nav, rules = Navigator(corpus), RulePlanner(corpus)
    tot, rows = Counter(), []
    for ex in questions:
        corpus.last_subject = ""
        _, r = navigate(ex["question"], [rules], nav)
        s = score(ex, r.text if r else "", bool(r and r.answered))
        part = "answerable" if ex["expect"] == "answered" else "unanswerable"
        tot[(part, s)] += 1
        rows.append((ex, s, (r.text if r else "")[:100], [m.word for m in (r.mapped if r else [])]))
    return tot, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", default="data")
    ap.add_argument("--questions", required=True)
    ap.add_argument("--lexicon", action="append", default=[])
    ap.add_argument("--show", action="store_true", help="every question that any system got wrong or changed")
    a = ap.parse_args(argv)
    corpus = Corpus(a.corpus).load()
    qs = [json.loads(l) for l in open(a.questions) if l.strip()]
    systems = [("rules", None)] + [(f"+ {Path(p).name}", Lexicon.load(p)) for p in a.lexicon]
    n_a = sum(q["expect"] == "answered" for q in qs)
    print(f"{len(qs)} questions: {n_a} answerable, {len(qs) - n_a} the corpus can't answer\n")
    print(f"{'system':<26}{'answerable: right':>18}{'wrong':>7}{'abstained':>11}   {'unanswerable: admitted':>22}{'wrong':>7}")
    results = []
    for name, lex in systems:
        tot, rows = run(corpus, qs, lex)
        results.append((name, rows))
        print(f"{name:<26}{tot[('answerable', 'right')]:>18}{tot[('answerable', 'wrong')]:>7}"
              f"{tot[('answerable', 'abstained')]:>11}   {tot[('unanswerable', 'right')]:>22}{tot[('unanswerable', 'wrong')]:>7}")
    for name, lex in systems[1:]:
        seen = lex.example_subjects()
        leaks = [q["question"] for q in qs if subjects_of(corpus, q["question"]) & seen]
        print(f"\nleak check {name}: {len(seen)} subjects behind example-learned mappings; "
              f"{len(leaks)} held-out questions about them" + (f": {leaks}" if leaks else ""))
    if a.show:
        print()
        base = {r[0]["question"]: r for r in results[0][1]}
        for name, rows in results[1:]:
            for ex, s, text, mapped in rows:
                b = base[ex["question"]][1]
                if s != "right" or s != b:
                    print(f"  [{name}] {b:>9} -> {s:<9} {ex['question']}  |  {text}" + (f"  (via {mapped})" if mapped else ""))


if __name__ == "__main__":
    main()
