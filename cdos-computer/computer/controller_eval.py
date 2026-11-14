"""Is the learned controller worth having?  Same corpus, same questions:

    nav-rules        the rule planner alone (what the computer does now)
    +controller      rules first, then the trained network's top actions
    +try-everything  rules first, then *every* action, untrained: what exploring at run time would cost

"plans" is how many plans the executor ran per question, on average: what the network saves over trying
everything (each run reads shards from the SD card on the Pi)."""

from __future__ import annotations

import json
from collections import Counter

from .controller import ACTION_NAMES, ControllerPlanner, Uniform
from .navdata import score
from .navigator import Navigator
from .planner import RulePlanner, navigate


def run_system(corpus, examples, planners, limit=8):
    nav = Navigator(corpus)
    tot, plans, by = Counter(), 0, {}
    for ex in examples:
        corpus.last_subject = ""
        trace = []
        plan, r = navigate(ex["question"], planners, nav, trace=trace, limit=limit)
        plans += sum(t.startswith("planner ") and "no plan" not in t for t in trace)
        s = score(ex, r.text if r else "", bool(r and r.answered))
        tot[s] += 1
        by[ex["question"]] = (s, (r.text if r else "")[:110], plan.source if plan else "")
    return tot, plans / max(1, len(examples)), by


def compare_on(corpus, path, net, show: bool = True) -> str:
    ex = [json.loads(l) for l in open(path) if l.strip()]
    rules = RulePlanner(corpus)
    systems = {
        "nav-rules": [rules],
        "+controller": [rules, ControllerPlanner(corpus, net)],
        "+try-everything": [rules, ControllerPlanner(corpus, Uniform(), min_p=0, max_actions=len(ACTION_NAMES), max_plans=99)],
    }
    lines = [f"{len(ex)} questions from {path}", f"{'system':<18}{'right':>7}{'wrong':>7}{'abstained':>11}{'plans':>8}"]
    detail = {}
    for name, pls in systems.items():
        tot, avg, by = run_system(corpus, ex, pls, limit=99 if name == "+try-everything" else 8)
        detail[name] = by
        lines.append(f"{name:<18}{tot['right']:>7}{tot['wrong']:>7}{tot['abstained']:>11}{avg:>8.1f}")
    if show:
        lines.append("")
        for q in (e["question"] for e in ex):
            a, b = detail["nav-rules"][q], detail["+controller"][q]
            if a[0] != b[0] or b[0] == "wrong":
                lines.append(f"  {a[0]:>9} -> {b[0]:<9} {q}  |  {b[1]}  [{b[2]}]")
    return "\n".join(lines)
