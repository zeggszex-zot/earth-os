#!/usr/bin/env python3
"""Does training on CDOS's own knowledge make it better at answering new questions?

    python3 tools/experiment_learning.py --corpus data --subjects 3000 --heldout tests/real_heldout.jsonl --out exp.json

The model here is the learned controller (computer/controller.py: question shape -> plan shape).  It is trained only on
examples the corpus generated about itself (computer/navdata.py: every plan executed, every answer checked), and scored
on questions it never saw.  The executor still checks every answer, so "better" means more right answers without
more wrong ones.

The split holds out *things*, not just questions (computer/navdata.leak_free): the subjects of held-out questions never
appear in a training example, not as a subject, not as a class tested against, not in evidence.  Phrasings are held
out too: the re-word templates are split in two (controller_train.forms_for), and the second half is only ever used
for testing.

Test sets
    A  unseen things, template phrasings     the generator's own questions about held-out subjects
    B  unseen things, unseen phrasings       held-out subjects, re-worded with the held-out templates
    C  seen things, unseen phrasings         training subjects, re-worded with the held-out templates
    D  written by a person                   --heldout files (never generated, never trained on)

Systems
    rules                the rule planner alone (no learning)
    rules+untrained      rules, then every action tried in turn: what exploring at run time would cost
    rules+trained@f      rules, then a controller trained on a fraction f of the training subjects

Read the result honestly: A, B and C come from templates, so they're kinder than real questions.  D is the fair test.
On the tiny test corpus (tests/mini_corpus.py) the numbers only show that the experiment runs.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from computer.controller import ACTION_NAMES, ControllerPlanner, Uniform  # noqa: E402
from computer.controller_eval import run_system                            # noqa: E402
from computer.controller_train import build_dataset, reword, top1, train   # noqa: E402
from computer.corpus import Corpus                                         # noqa: E402
from computer.navdata import generate, leak_free                           # noqa: E402
from computer.planner import RulePlanner                                   # noqa: E402


def check_no_leaks(train_exs, held_exs) -> int:
    held = {s for e in held_exs for s in e.get("subjects", [])}
    return sum(bool(held & set(e.get("concepts", e.get("subjects", [])))) for e in train_exs)


def run(a) -> dict:
    t0 = time.monotonic()
    corpus = Corpus(a.corpus).load()
    if not corpus.ready:
        sys.exit(f"no corpus: {corpus.error}")
    corpus.cache_shards = max(corpus.cache_shards, sum((corpus.meta or {}).get(k, 0) for k in ("s_shards", "v_shards")))
    if a.lexicon:
        from computer.lexicon import Lexicon
        corpus.lexicon = Lexicon.load(a.lexicon)
    rng = random.Random(a.seed)
    pool = None
    if a.things:
        # Subjects with facts from somewhere other than the dictionary: on Phase 1.5, 1.33M of 1.46M rows are WordNet, so
        # sampling every subject makes ~80% of the questions dictionary questions.
        pool = [k for k in sorted(corpus.subjects) if any(f.source != "OEWN" for f in corpus.facts_for(k))]
        print(f"--things: {len(pool)} of {len(corpus.subjects)} subjects have non-dictionary facts", file=sys.stderr)
    teacher, dropped = leak_free(generate(corpus, a.subjects, seed=a.seed, subjects=pool))
    train_t = [e for e in teacher if e["split"] == "train"]
    held_t = [e for e in teacher if e["split"] != "train"]
    leaks = check_no_leaks(train_t, held_t)
    log = lambda *x: print(*x, file=sys.stderr)
    log(f"teacher questions: {len(train_t)} train, {len(held_t)} held out ({dropped} dropped for mentioning a "
        f"held-out thing); leaks left: {leaks}")

    sets = {
        "A unseen things, template phrasings": [e for e in held_t if e["expect"] == "answered"],
        "B unseen things, unseen phrasings": [r for e in held_t for r in reword(e, rng, 2, which="test")],
        "C seen things, unseen phrasings": [r for e in train_t for r in reword(e, rng, 1, which="test")],
    }
    for path in a.heldout:
        sets[f"D {Path(path).name}"] = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]

    # Training rows: the training subjects' teacher questions and their training-template re-wordings.
    by_subject = {}
    for e in train_t:
        by_subject.setdefault(e["subjects"][0], []).append(e)
    subjects = sorted(by_subject)
    random.Random(a.seed).shuffle(subjects)
    ctl0 = ControllerPlanner(corpus)
    rows_by_subject = {}
    for s in subjects:
        exs = []
        for e in by_subject[s]:
            exs += [e] + reword(e, rng, a.rewordings, which="train")
        rows_by_subject[s], _ = build_dataset(exs, ctl0)
    log(f"explored {sum(len(r) for r in rows_by_subject.values())} training rows ({time.monotonic() - t0:.0f}s)")

    rules = RulePlanner(corpus)
    systems = {"rules": [rules],
               "rules+untrained": [rules, ControllerPlanner(corpus, Uniform(), min_p=0, max_actions=len(ACTION_NAMES),
                                                            max_plans=99)]}
    fits = {}
    for f in a.fractions:
        take = subjects[: max(1, round(len(subjects) * f))]
        rows = [r for s in take for r in rows_by_subject[s]]
        if not rows:
            continue
        net = train(rows, epochs=a.epochs, seed=a.seed)
        fits[f] = {"subjects": len(take), "rows": len(rows), "train_top1": round(top1(net, rows), 3)}
        systems[f"rules+trained@{f:g}"] = [rules, ControllerPlanner(corpus, net)]

    report = {"corpus": corpus.status(), "subjects_asked": a.subjects, "seed": a.seed, "things": a.things,
              "split": {"train_questions": len(train_t), "held_out_questions": len(held_t),
                        "dropped_for_leaks": dropped, "leaks_left": leaks},
              "training": fits, "results": {}}
    for set_name, exs in sets.items():
        if not exs:
            continue
        report["results"][set_name] = {"n": len(exs)}
        for sys_name, planners in systems.items():
            limit = 99 if sys_name == "rules+untrained" else 8
            tot, plans, by = run_system(corpus, exs, planners, limit=limit)
            kinds = Counter()
            for e in exs:
                kinds[(e.get("kind", "?"), by[e["question"]][0])] += 1
            report["results"][set_name][sys_name] = {"right": tot["right"], "wrong": tot["wrong"],
                                                     "abstained": tot["abstained"], "plans": round(plans, 2),
                                                     "by_kind": {f"{k}:{v}": n for (k, v), n in sorted(kinds.items())},
                                                     # read these: a "wrong" can be a true fact that answers a
                                                     # neighbouring question, or a false one
                                                     "wrong_answers": [[e["question"], by[e["question"]][1],
                                                                        by[e["question"]][2]] for e in exs
                                                                       if by[e["question"]][0] == "wrong"]}
    report["seconds"] = round(time.monotonic() - t0, 1)
    return report


def table(report: dict) -> str:
    lines = [f"corpus: {report['corpus']}",
             f"split: {report['split']}", "training: " + ", ".join(
                 f"@{f:g}: {v['subjects']} subjects, {v['rows']} rows, fits {v['train_top1']:.0%}"
                 for f, v in report["training"].items()), ""]
    for set_name, res in report["results"].items():
        lines.append(f"{set_name}  ({res['n']} questions)")
        lines.append(f"  {'system':<22}{'right':>7}{'wrong':>7}{'abstained':>11}{'plans/q':>9}")
        for sys_name, r in res.items():
            if sys_name == "n":
                continue
            lines.append(f"  {sys_name:<22}{r['right']:>7}{r['wrong']:>7}{r['abstained']:>11}{r['plans']:>9}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", default="data")
    ap.add_argument("--subjects", type=int, default=3000)
    ap.add_argument("--rewordings", type=int, default=3)
    ap.add_argument("--fractions", default="0.25,0.5,1", help="shares of the training subjects to train on")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--heldout", action="append", default=[], help="JSONL of hand-written questions (repeatable)")
    ap.add_argument("--lexicon")
    ap.add_argument("--things", action="store_true",
                    help="only subjects with non-dictionary facts (countries, planets, animals...), not every word")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", help="write the full report (with per-kind counts) as JSON")
    a = ap.parse_args(argv)
    a.fractions = [float(x) for x in a.fractions.split(",") if x]
    report = run(a)
    print(table(report))
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1))
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
