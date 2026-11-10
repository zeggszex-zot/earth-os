#!/usr/bin/env python3
"""Evaluate: the plain lookup vs the navigator, same corpus, same questions.

    python3 tools/eval_navigator.py --corpus data --examples navdata/test.jsonl
    python3 tools/eval_navigator.py --corpus data --examples navdata/test.jsonl --lm http://127.0.0.1:8081

Systems:
    lookup          Corpus.ask, as the computer answered before the navigator
    nav-rules       the navigator with the rule planner
    nav-lm          the navigator with a (fine-tuned) planner model, rules as fallback   (--lm)
    nav-lm-only     the planner model alone, no rules fallback: how good the model itself is   (--lm)
    propose         the model writes plan + answer, the executor checks it   (--lm --propose)
    direct          the model answers from its weights, the rules' run checks it   (--lm --direct)
                    (both also report how often the model's own words passed the check)

"right" means the gold answer is in what was said (for comparisons: "so <winner> ...").  "wrong" means
something was said that isn't the answer: for questions the corpus can't answer, any answer is wrong.
Template-made questions favour the rule planner, which was written for the same templates: the
fair test of a model is paraphrased or hand-written questions (--examples accepts any JSONL with
question / kind / gold / expect).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from computer.corpus import Corpus                                          # noqa: E402
from computer.navdata import LookupSystem, NavigatorSystem, evaluate, format_report  # noqa: E402
from computer.planner import LMPlanner, RulePlanner                         # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", default="data")
    ap.add_argument("--examples", required=True)
    ap.add_argument("--lm", metavar="ENDPOINT", help="planner model server")
    ap.add_argument("--propose", action="store_true", help="--lm model proposes plan + words (navigator_planner = propose)")
    ap.add_argument("--direct", action="store_true", help="--lm model answers from its weights (navigator_planner = direct)")
    ap.add_argument("--fewshot", action="store_true", help="give the model the few-shot prompt (for un-tuned models)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", help="also write the report as JSON here")
    a = ap.parse_args(argv)

    corpus = Corpus(a.corpus).load()
    if not corpus.ready:
        sys.exit(f"no corpus: {corpus.error}")
    ex = [json.loads(l) for l in open(a.examples) if l.strip()]
    if a.limit:
        ex = ex[: a.limit]
    systems = [LookupSystem(corpus), NavigatorSystem(corpus, [RulePlanner(corpus)], "nav-rules")]
    if a.lm:
        from computer.llm import MiniLM
        lm = MiniLM(a.lm, timeout=60)
        if not lm.available(force=True):
            sys.exit(f"planner model not reachable at {a.lm}: {lm.error}")
        if a.propose or a.direct:
            from computer.navdata import ProposerSystem
            from computer.proposer import Proposer
            prop = Proposer(lm, corpus, fewshot=a.fewshot, direct=a.direct)
            systems.append(ProposerSystem(corpus, prop, [RulePlanner(corpus)], "direct" if a.direct else "propose"))
        else:
            planner = LMPlanner(lm, corpus, fewshot=a.fewshot)
            systems += [NavigatorSystem(corpus, [planner, RulePlanner(corpus)], "nav-lm"),
                        NavigatorSystem(corpus, [planner], "nav-lm-only")]
    report = evaluate(ex, systems)
    print(format_report(report, len(ex)))
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
