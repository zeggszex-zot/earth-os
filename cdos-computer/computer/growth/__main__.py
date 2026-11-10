"""python3 -m computer.growth <command>: the Growth Engine (computer/growth).  Never promotes a model.

    discover            re-ask what the logs say failed; print the findings; make tasks for them
    tasks               every task and its status
    show <task>         one task: its questions, the candidate answers the corpus can reach, its answers, its candidate
    answer <task> --question Q --gold A [--gold B] --by NAME [--expect unsupported] [--kind hop]
    run <task>          train a candidate with the task's answers into staging and evaluate it
    auto                discover + make tasks + run the answered ones (what cdos-growth.timer runs)

The Governor (computer/governor.py) decides who may: `answer` needs the teacher or operator role (the answer becomes
training data, so the user who gave it is recorded), `run` and `auto` need service or operator.  discover, tasks and
show only read and propose.

Paths come from config.toml (neural_registry, corpus, log_file) unless given: --registry, --corpus, --turn-log.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from computer.config import HERE, load


def resolve(p: str) -> str:
    return str(p if Path(p).is_absolute() else HERE / p) if p else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m computer.growth", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config.toml (default: the computer's own)")
    ap.add_argument("--registry", help="the neural registry (default: neural_registry from the config)")
    ap.add_argument("--corpus", help="the corpus data/ folder (default: corpus from the config)")
    ap.add_argument("--turn-log", help="the computer's turn log (default: log_file from the config)")
    ap.add_argument("--heldout", action="append", default=[], help="hand-written held-out questions for evaluation")
    ap.add_argument("--regression", action="append", default=[], help="questions the rules must keep getting right")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--no-replay", action="store_true", help="don't re-ask (no corpus needed; nothing is classified)")
    sub.add_parser("tasks")
    s = sub.add_parser("show")
    s.add_argument("task")
    a = sub.add_parser("answer")
    a.add_argument("task")
    a.add_argument("--question", required=True)
    a.add_argument("--gold", action="append", default=[])
    a.add_argument("--by", required=True, help="who is answering")
    a.add_argument("--expect", default="answered", choices=["answered", "unsupported"])
    a.add_argument("--kind", default="hop", help="how to score it: hop (any gold word), test_yes, compare ...")
    r = sub.add_parser("run")
    r.add_argument("task")
    r.add_argument("--epochs", type=int, default=40)
    r.add_argument("--subjects", type=int, default=3000)
    r.add_argument("--seed", type=int, default=0)
    sub.add_parser("auto")
    args = ap.parse_args(argv)

    cfg = load(args.config)
    registry = args.registry or resolve(cfg.neural_registry)
    if not registry:
        print("no neural registry: set neural_registry in config.toml or pass --registry", file=sys.stderr)
        return 2
    heldout = args.heldout or [resolve(p) for p in cfg.growth_heldout]
    regression = args.regression or [resolve(p) for p in cfg.growth_regression]
    from computer.growth.engine import Growth, GrowthError
    perm = {"answer": "growth.answer", "run": "growth.train", "auto": "growth.train"}.get(args.cmd)
    decision = gov = None
    if perm:
        from computer.governor import Forbidden, cli_decision
        target = f"growth:{getattr(args, 'task', None) or args.cmd}"
        why = getattr(args, "reason", "") or {"answer": f"answer from {getattr(args, 'by', '')}",
                                               "run": "train a candidate into staging",
                                               "auto": "scheduled growth run"}[args.cmd]
        try:
            gov, decision = cli_decision(args.config, perm, target, why, {"registry": str(registry)})
        except Forbidden as e:
            print(f"refused by the Governor: {e.why}", file=sys.stderr)
            return 4
    try:
        g = Growth(registry, args.corpus or cfg.corpus, args.turn_log or resolve(cfg.log_file),
                   resolve(cfg.relation_lexicon) or None, getattr(cfg, "growth_approval", "manual"))
        if args.cmd == "discover":
            found = g.discover(replay=not args.no_replay)
            made = g.create_tasks(found)
            print(json.dumps({"findings": [asdict(f) for f in found], "tasks_created": [t["id"] for t in made]}, indent=1))
        elif args.cmd == "tasks":
            for t in g.tasks():
                print(f"{t['id']}  {t['kind']:<17} {t['status']:<14} {len(t['questions'])} questions, "
                      f"{len(t['answers'])} answered")
        elif args.cmd == "show":
            print(json.dumps(g.task(args.task), indent=1))
        elif args.cmd == "answer":
            t = g.answer(args.task, args.question, args.gold, args.by, args.expect, args.kind,
                         authorized=decision.ref if decision else None)
            print(json.dumps({"task": t["id"], "status": t["status"], "last": t["history"][-1]}, indent=1))
        elif args.cmd == "run":
            t = g.run(args.task, heldout, regression, epochs=args.epochs, subjects=args.subjects, seed=args.seed)
            print(json.dumps({"task": t["id"], "status": t["status"], "candidate": t["candidate"]}, indent=1))
        elif args.cmd == "auto":
            if not getattr(cfg, "growth_auto_run", True):
                found = g.discover()
                print(json.dumps({"findings": len(found), "tasks_created": [t["id"] for t in g.create_tasks(found)],
                                  "tasks_run": [], "note": "growth_auto_run = false"}, indent=1))
            else:
                print(json.dumps(g.auto(heldout, regression), indent=1))
    except GrowthError as e:
        if gov:
            gov.result(decision, f"refused: {e}")
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if gov:
        gov.result(decision, "done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
