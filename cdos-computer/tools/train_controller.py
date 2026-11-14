#!/usr/bin/env python3
"""Train the learned reasoning controller (computer/controller.py): a ~100k-weight network that picks the plan shape.

    python3 tools/train_controller.py --corpus data --out controller.json
    python3 tools/train_controller.py --corpus data --out controller.json --mine my_questions.jsonl

Then in config.toml:   navigator_controller = "controller.json"

What it does:
  1. teacher examples from the corpus (computer/navdata.generate: rule-planner plans, executed and checked)
  2. --rewordings N new phrasings of each (they keep the answer, not the plan)
  3. --mine: your own questions with answers (JSONL: question, gold, expect, kind) - "learning from examples you give it"
  4. exploration: every action is tried on every question; the ones that reach the answer and pass the
     check are the target - "learning from CDOS exploring its own corpus"
  5. training (needs numpy; the computer itself doesn't)

Split by subject, leak-free (computer/navdata.leak_free): dev rows are about things the network never saw, and no
training row so much as mentions one of them.  Re-wordings stay with their teacher's split.  (Before this, dev was
split by question text, so a re-wording of a training question could land in dev.)  --heldout scores a JSONL of
hand-written questions afterwards (never trained on), against the rules alone.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from computer.controller import ControllerPlanner                  # noqa: E402
from computer.controller_train import build_dataset, reword, top1, train  # noqa: E402
from computer.corpus import Corpus                                  # noqa: E402
from computer.navdata import generate                               # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", default="data")
    ap.add_argument("--out", default="controller.json")
    ap.add_argument("--subjects", type=int, default=3000, help="subjects to make teacher questions from")
    ap.add_argument("--rewordings", type=int, default=4, help="new phrasings per teacher question")
    ap.add_argument("--mine", action="append", default=[], help="JSONL of your own questions with answers")
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--lexicon", help="relation lexicon (tools/learn_lexicon.py) to use while exploring and evaluating")
    ap.add_argument("--heldout", help="JSONL to evaluate on afterwards (never trained on)")
    ap.add_argument("--rows-out", help="also write the explored training rows here (JSONL)")
    a = ap.parse_args(argv)

    corpus = Corpus(a.corpus).load()
    if not corpus.ready:
        sys.exit(f"no corpus: {corpus.error}")
    # Training is offline: keep every shard in memory instead of re-reading them for each of thousands of plans.
    corpus.cache_shards = max(corpus.cache_shards, sum((corpus.meta or {}).get(k, 0) for k in ("s_shards", "v_shards")))
    if a.lexicon:
        from computer.lexicon import Lexicon
        corpus.lexicon = Lexicon.load(a.lexicon)
    t0 = time.monotonic()
    rng = random.Random(a.seed)
    from computer.navdata import leak_free
    teacher, leaked = leak_free(generate(corpus, a.subjects, seed=a.seed))
    for ex in teacher:
        if ex["split"] == "test":
            ex["split"] = "dev"          # the controller has no separate test use here; both are held out
    print(f"leak-free split: dropped {leaked} training questions that mention a held-out thing", file=sys.stderr)
    exs = []
    for ex in teacher:
        exs.append(ex)
        exs += reword(ex, rng, a.rewordings)
    for path in a.mine:
        exs += [json.loads(l) for l in open(path) if l.strip()]
    print(f"{len(teacher)} teacher questions, {len(exs)} with re-wordings and your own", file=sys.stderr)

    ctl = ControllerPlanner(corpus)
    rows, stats = build_dataset(exs, ctl, progress=lambda i, n: print(f"  explored {i} ({n} rows)", file=sys.stderr))
    print(f"exploration: {dict(stats)}  ({time.monotonic() - t0:.0f}s)", file=sys.stderr)
    if a.rows_out:
        with open(a.rows_out, "w") as fh:
            for r in rows:
                fh.write(json.dumps({k: v for k, v in r.items() if k != "idx"}) + "\n")
    if not rows:
        sys.exit("nothing to learn from")
    tr = [r for r in rows if r.get("split", "train") == "train"]
    dev = [r for r in rows if r.get("split") == "dev"]
    net = train(tr, hidden=a.hidden, epochs=a.epochs, seed=a.seed,
                progress=lambda ep, loss: print(f"  epoch {ep + 1}: loss {loss:.3f}", file=sys.stderr)
                if ep % 10 == 9 else None)
    print(f"top-1 action accepted: train {top1(net, tr):.0%}, dev {top1(net, dev):.0%} ({len(tr)}/{len(dev)} rows)")
    net.save(a.out)
    print(f"wrote {a.out} ({Path(a.out).stat().st_size // 1024} KB, {len(net.rows)} feature rows)")
    if a.heldout:
        from computer.controller_eval import compare_on
        print(compare_on(corpus, a.heldout, net))


if __name__ == "__main__":
    main()
