"""Evaluating a staged model: the same held-out questions, the same corpus, every system.

    python3 -m computer.neural evaluate --registry models/neural --model <id> --corpus data \
        --heldout tests/controller_heldout.jsonl --regression tests/navigator_questions.jsonl

Systems (each runs after the rule planner, the way the computer runs it):

    rules              the rule planner alone: what the computer does with no model
    rules+untrained    the same network at its initial weights, same budget as the trained one (min_p, 3 actions, 4 plans)
    rules+trained      the staged model, loaded and validated exactly as the computer loads it
    rules+try-every    every action, no probability floor, up to 99 plans (the repo's earlier "untrained" baseline:
                       what exploring at answer time would cost)
    rules+active       the currently active model, when there is one

Fixture sets: A and B were materialised at training time (held-out subjects; B also unseen phrasings), D are
hand-written held-out questions, R are regression sets the rules already answer.  Each answer is scored right /
wrong / abstained by navdata.score.

The predefined metric (PRIMARY, fixed before any result was seen; see docs/NEURAL_CORE.md):

    net = right - wrong, summed over the held-out sets B and D.  A wrong answer costs as much as a right one gains;
    abstaining costs nothing.  The trained model must beat the untrained one on it.

Secondary: top-1 selection accuracy on the held-out records (is the best-scored action one the checker accepts).

The whole computer, too: navigate() alone doesn't include the one-shot lookup, which answers simple questions when no
rule pattern does, and a learned plan that answers is said before the lookup is asked.  So every fixture is also asked
of core.Computer (no language model) without and with the model, and what it *says* is scored.  (This part was added
after the first run of the test suite showed a lookup answer replaced by a neural one; see docs/NEURAL_CORE.md.)

Gates (all must pass before `promote` will activate the model, and promotion still needs --approve):

    loads                  the model and its manifest pass every check the computer applies at start-up
    same_corpus            evaluated on the corpus version it was trained on
    selection_improves     held-out top-1: trained > untrained
    beats_majority         held-out top-1: trained > always picking the action most common in the training labels (a
                           model that learned only which actions are common hasn't learned to read questions; added in
                           0.2, when the neural path's stricter answer checks made such models harmless but promotable)
    primary_improves       PRIMARY: trained > untrained
    no_rule_regressions    no question answered right without the model is answered otherwise with it, by navigate()
                           or by the whole computer
    computer_not_worse     the whole computer's held-out net (A, B, D) with the model >= without it
    regression_sets_clean  on the R sets, no wrong answer the rules alone don't give
    fewer_wrong_than_try   held-out wrong answers: trained <= try-every
    not_worse_than_active  PRIMARY: trained >= the active model (when there is one)
    deterministic          the same predictions from a second, independent load
    corpus_unchanged       every corpus file has the same size and mtime afterwards, and the same content hash

Wrong answers the model adds over the rules alone are listed in the report (new_wrong) and are not a gate: the
controller's design trades some (each a true fact that answers a neighbouring question) for many more right answers.
Read them before approving.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

from computer.controller import ACTION_NAMES, ControllerPlanner, Uniform
from computer.controller_eval import run_system
from computer.corpus import Corpus
from computer.neural.model import load_model_dir, sha256_file
from computer.neural.records import rows_of
from computer.neural.runtime import NeuralPlanner, inside, read_active
from computer.neural.train import (canonical_json, chance, code_sha256, compact, corpus_snapshot, fixture_hash,
                                   iter_records, read_jsonl, top1)
from computer.planner import RulePlanner

EVAL_FORMAT = "cdos-neural-eval/1"
PRIMARY = "net = right - wrong, summed over held-out sets B and D; trained must beat untrained"
PRIMARY_SETS = ("B_", "D_")
HELDOUT_SETS = ("A_", "B_", "D_")
GATES = ("loads", "same_corpus", "selection_improves", "beats_majority", "primary_improves", "no_rule_regressions",
         "computer_not_worse",
         "regression_sets_clean", "fewer_wrong_than_try", "not_worse_than_active", "deterministic", "corpus_unchanged")


def model_dir_of(registry, model_id: str) -> Path:
    for sub in ("staging", "models"):
        d = Path(registry) / sub / model_id
        if d.is_dir():
            return d
    raise FileNotFoundError(f"no model {model_id} in {registry}/staging or {registry}/models")


def systems_for(corpus, trained, manifest, untrained, active=None, strict: bool = True) -> dict:
    rules = RulePlanner(corpus)
    out = {"rules": ([rules], 8),
           "rules+untrained": ([rules, NeuralPlanner(corpus, untrained, {"model_id": "untrained"}, strict=strict)], 8),
           "rules+trained": ([rules, NeuralPlanner(corpus, trained, manifest, strict=strict)], 8),
           "rules+try-every": ([rules, ControllerPlanner(corpus, Uniform(), min_p=0, max_actions=len(ACTION_NAMES),
                                                         max_plans=99)], 99)}
    if active is not None:
        out["rules+active"] = ([rules, NeuralPlanner(corpus, active[0], active[1], strict=strict)], 8)
    return out


def computers_for(corpus, trained, manifest, untrained, active=None, strict: bool = True) -> dict:
    """The whole computer (core.Computer: router, lookup, knowledge pathway, envelope), as deployed with no language
    model, with the model planner added exactly as _load_neural adds it."""
    from computer.config import Config
    from computer.core import Computer

    def make(model=None, man=None):
        c = Computer(Config(corpus=str(corpus.base), llm_mode="off", llm_endpoint="", log_file="", state_file="",
                            navigator_controller="", neural_registry=""), corpus)
        if model is not None:
            c.neural = NeuralPlanner(corpus, model, man, min_p=c.cfg.neural_min_p, strict=strict)
            c.planners.append(c.neural)
        return c
    out = {"computer": make(), "computer+untrained": make(untrained, {"model_id": "untrained"}),
           "computer+trained": make(trained, manifest)}
    if active is not None:
        out["computer+active"] = make(*active)
    return out


def run_computer(comp, examples: list[dict]) -> tuple[Counter, dict]:
    """Each question asked of a fresh conversation; scored on what was said ("answered" in the envelope = committed)."""
    from computer.navdata import score
    tot, by = Counter(), {}
    for ex in examples:
        comp.context.clear()
        comp.corpus.last_subject, comp.last_faculty, comp.last, comp.last_navigation = "", "", None, None
        if comp.analyst is not None:
            comp.analyst = type(comp.analyst)(comp.corpus, efficiency=comp.cfg.analysis_efficiency)
        r = comp.think(ex["question"])
        plan = comp.last_navigation[0] if comp.last_navigation else None
        s = score(ex, r.text, r.envelope.get("status") == "answered")
        tot[s] += 1
        by[ex["question"]] = (s, r.text[:110], plan.source if plan is not None and comp._said_by == "navigator"
                              else comp._said_by)
    return tot, by


def compare(sets: dict, by_q: dict, base: str, others: list[str]) -> dict:
    """Per question, against `base`: transition counts, answers `base` got right and the other didn't (regressions),
    and wrong answers `base` didn't give (new_wrong)."""
    out = {}
    for sys_name in others:
        trans, regress, new_wrong = Counter(), [], []
        for set_name, exs in sets.items():
            for e in exs:
                q = e["question"]
                a, b = by_q[set_name][base][q], by_q[set_name][sys_name][q]
                trans[f"{a[0]}->{b[0]}"] += 1
                if a[0] == "right" and b[0] != "right":
                    regress.append({"set": set_name, "question": q, "was": a[1], "now": b[1], "plan": b[2]})
                if b[0] == "wrong" and a[0] != "wrong":
                    new_wrong.append({"set": set_name, "question": q, "said": b[1], "plan": b[2]})
        out[sys_name] = {"transitions": dict(sorted(trans.items())), "regressions": regress, "new_wrong": new_wrong}
    return out


def evaluate(registry, model_id: str, corpus_dir, heldout=(), regression=(), lexicon=None, log=None,
             strict: bool = True) -> dict:
    """strict=False evaluates without the neural path's two extra answer checks (runtime.unaddressed and
    lex_family_unasked), as the legacy controller runs: for analysis only, and promote() refuses such a report."""
    log = log or (lambda *a: print(*a, file=sys.stderr))
    t0 = time.monotonic()
    registry, corpus_dir = Path(registry), Path(corpus_dir)
    d = model_dir_of(registry, model_id)
    gates, notes = {}, []
    try:
        trained, manifest = load_model_dir(d)
        untrained, _ = load_model_dir(d, "untrained")
        gates["loads"] = (True, "model, untrained baseline and manifest pass every check")
    except ValueError as e:
        raise SystemExit(f"{model_id} can't be evaluated: {e}")
    if inside(registry, corpus_dir) or inside(corpus_dir, registry):
        raise SystemExit("the neural registry must not be inside the corpus folder, or contain it")
    before = corpus_snapshot(corpus_dir)
    corpus = Corpus(corpus_dir).load()
    if not corpus.ready:
        raise SystemExit(f"no corpus: {corpus.error}")
    corpus.cache_shards = max(corpus.cache_shards, sum((corpus.meta or {}).get(k, 0) for k in ("s_shards", "v_shards")))
    if lexicon:
        from computer.lexicon import Lexicon
        corpus.lexicon = Lexicon.load(lexicon)
    trained_on = manifest["training_data"]["corpus_version"]
    gates["same_corpus"] = (corpus.version == trained_on,
                            "same corpus version" if corpus.version == trained_on else
                            f"trained on {trained_on[:19]}…, evaluated on {corpus.version[:19]}…")

    # the fixtures: materialised at training time, plus the hand-written and regression files given now
    sets = {}
    for p in sorted((d / "fixtures").glob("[AB]_*.jsonl")):
        sets[p.stem] = read_jsonl(p)
    for kind, paths in (("D", heldout), ("R", regression)):
        for p in paths:
            sets[f"{kind}_{Path(p).stem}"] = read_jsonl(p)
    staged_fx = manifest["training_data"].get("fixtures", {})
    for name, items in sets.items():
        if name in staged_fx and staged_fx[name]["sha256"] != fixture_hash(items):
            notes.append(f"{name} differs from the copy made at training time")
        if name[0] in "DR" and name not in staged_fx:
            notes.append(f"{name} wasn't given at training time, so its questions weren't checked against the training set")

    try:
        active_id = read_active(registry)
    except ValueError as e:
        raise SystemExit(f"ACTIVE.json is damaged ({e}); fix or remove it before evaluating against it")
    active = None
    if active_id and active_id != model_id:
        try:
            active = load_model_dir(registry / "models" / active_id)
        except ValueError as e:
            notes.append(f"active model {active_id} doesn't load ({e}); compared without it")
    systems = systems_for(corpus, trained, manifest, untrained, active, strict)

    results, by_q = {}, {}
    for set_name, exs in sets.items():
        results[set_name] = {"n": len(exs)}
        by_q[set_name] = {}
        for sys_name, (planners, limit) in systems.items():
            s0 = time.monotonic()
            tot, plans, by = run_system(corpus, exs, planners, limit=limit)
            ms = (time.monotonic() - s0) * 1000 / max(1, len(exs))
            results[set_name][sys_name] = {"right": tot["right"], "wrong": tot["wrong"], "abstained": tot["abstained"],
                                           "net": tot["right"] - tot["wrong"], "plans": round(plans, 2),
                                           "ms_per_question": round(ms, 2)}
            by_q[set_name][sys_name] = by
        log(f"  {set_name}: " + ", ".join(f"{k} {v['right']}/{v['wrong']}/{v['abstained']}"
                                          for k, v in results[set_name].items() if k != "n"))

    comparisons = compare(sets, by_q, "rules", [s for s in systems if s != "rules"])

    # the whole computer: what is said, with the one-shot lookup and the knowledge pathway (core.Computer), with and
    # without the model.  navigate() alone can't show a lookup answer the model's plan pre-empts.
    computers = computers_for(corpus, trained, manifest, untrained, active, strict)
    results_c, by_c = {}, {}
    for set_name, exs in sets.items():
        results_c[set_name] = {"n": len(exs)}
        by_c[set_name] = {}
        for sys_name, comp in computers.items():
            tot, by = run_computer(comp, exs)
            results_c[set_name][sys_name] = {**{k: tot[k] for k in ("right", "wrong", "abstained")},
                                             "net": tot["right"] - tot["wrong"]}
            by_c[set_name][sys_name] = by
    comparisons_c = compare(sets, by_c, "computer", [s for s in computers if s != "computer"])

    def total(sys_name, prefixes, key, res=None):
        res = results if res is None else res
        return sum(res[s][sys_name][key] for s in res if s.startswith(prefixes))

    primary = {s: total(s, PRIMARY_SETS, "net") for s in systems}
    # the selection task: top-1 on the held-out records explored at training time
    kept = [compact(r) for r in iter_records(d / manifest["training_data"]["records_file"])]
    held_rows, train_rows = rows_of(kept, "heldout"), rows_of(kept, "train")
    freq = Counter()
    for r in train_rows:
        for a in r["targets"]:
            freq[a] += 1 / len(r["targets"])
    majority = min(freq, key=lambda a: (-freq[a], a)) if freq else ""
    selection = {"rows": len(held_rows), "trained": top1(trained, held_rows), "untrained": top1(untrained, held_rows),
                 "chance": chance(held_rows, len(ACTION_NAMES)), "majority_action": majority,
                 "majority": round(sum(majority in r["targets"] for r in held_rows) / max(1, len(held_rows)), 4)}
    if active is not None:
        selection["active"] = top1(active[0], held_rows)

    gates["selection_improves"] = (selection["trained"] > selection["untrained"],
                                   f"held-out top-1 {selection['trained']:.1%} vs untrained {selection['untrained']:.1%}")
    gates["beats_majority"] = (selection["trained"] > selection["majority"],
                               f"held-out top-1 {selection['trained']:.1%} vs always {majority!r} {selection['majority']:.1%}")
    gates["primary_improves"] = (primary["rules+trained"] > primary["rules+untrained"],
                                 f"net {primary['rules+trained']} vs untrained {primary['rules+untrained']}")
    reg, reg_c = comparisons["rules+trained"]["regressions"], comparisons_c["computer+trained"]["regressions"]
    gates["no_rule_regressions"] = (not reg and not reg_c, f"{len(reg)} rule-right answers lost by navigate(), "
                                                          f"{len(reg_c)} by the whole computer")
    cn, cr = total("computer+trained", HELDOUT_SETS, "net", results_c), total("computer", HELDOUT_SETS, "net", results_c)
    gates["computer_not_worse"] = (cn >= cr, f"whole computer, held-out net {cn} vs {cr} without the model")
    rs = [s for s in results if s.startswith("R_")]
    extra_wrong = sum(max(0, results[s]["rules+trained"]["wrong"] - results[s]["rules"]["wrong"]) +
                      max(0, results_c[s]["computer+trained"]["wrong"] - results_c[s]["computer"]["wrong"]) for s in rs)
    gates["regression_sets_clean"] = (extra_wrong == 0, f"{extra_wrong} wrong answers beyond those without the model "
                                                        f"on {len(rs)} regression sets" if rs else "no regression sets given")
    wt, wu = total("rules+trained", HELDOUT_SETS, "wrong"), total("rules+try-every", HELDOUT_SETS, "wrong")
    gates["fewer_wrong_than_try"] = (wt <= wu, f"held-out wrong answers {wt} vs {wu} trying every action")
    if active is not None:
        gates["not_worse_than_active"] = (primary["rules+trained"] >= primary["rules+active"],
                                          f"net {primary['rules+trained']} vs active {active_id} {primary['rules+active']}")
    else:
        gates["not_worse_than_active"] = (True, "no other active model to compare with")

    # determinism: a second, independent load gives the same scores on the same questions
    again, _ = load_model_dir(d)
    p1, p2 = NeuralPlanner(corpus, trained, manifest), NeuralPlanner(corpus, again, manifest)
    sample = [e["question"] for items in sets.values() for e in items][:80]
    same = all(p1.scores(q)[1] == p2.scores(q)[1] and [str(x) for x in p1.plans(q)] == [str(x) for x in p2.plans(q)]
               for q in sample)
    gates["deterministic"] = (same, f"{len(sample)} questions, identical scores and plans" if same else "scores differ")

    after = corpus_snapshot(corpus_dir)
    unchanged = after == before and Corpus(corpus_dir).load().version == corpus.version
    gates["corpus_unchanged"] = (unchanged, "no corpus file changed" if unchanged else "THE CORPUS CHANGED")

    report = {"format": EVAL_FORMAT, "model_id": model_id, "model_sha256": manifest["model_sha256"],
              "manifest_sha256": sha256_file(d / "manifest.json"), "active_at_eval": active_id,
              "evaluated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "corpus_version": corpus.version,
              "code_sha256": code_sha256(), "strict": strict, "primary_metric": PRIMARY, "primary": primary, "selection": selection,
              "results": results, "comparisons": comparisons,
              "computer": {"results": results_c, "comparisons": comparisons_c},
              "fixtures": {k: {"n": len(v), "sha256": fixture_hash(v)} for k, v in sets.items()},
              "gates": {g: {"pass": bool(gates[g][0]), "why": gates[g][1]} for g in GATES},
              "passed": all(gates[g][0] for g in GATES), "notes": notes, "seconds": round(time.monotonic() - t0, 2)}
    if d.parent.name == "staging":
        (d / "eval.json").write_bytes(canonical_json(report, indent=1))
        (d / "eval.txt").write_text(table(report), encoding="utf-8")
    else:                       # a promoted model's folder is never written: its eval.json is the one it was promoted on
        out = registry / "evaluations"
        out.mkdir(parents=True, exist_ok=True)
        stem = f"{model_id}-{report['evaluated'].replace(':', '')}"
        (out / f"{stem}.json").write_bytes(canonical_json(report, indent=1))
        (out / f"{stem}.txt").write_text(table(report), encoding="utf-8")
    return report


def table(rep: dict) -> str:
    lines = [f"model {rep['model_id']}  corpus {rep['corpus_version'][:23]}…", f"primary metric: {rep['primary_metric']}", ""]
    for set_name, res in rep["results"].items():
        lines.append(f"{set_name}  ({res['n']} questions)")
        lines.append(f"  {'system':<18}{'right':>7}{'wrong':>7}{'abstained':>11}{'net':>6}{'plans/q':>9}{'ms/q':>8}")
        for s, r in res.items():
            if s != "n":
                lines.append(f"  {s:<18}{r['right']:>7}{r['wrong']:>7}{r['abstained']:>11}{r['net']:>6}{r['plans']:>9}"
                             f"{r['ms_per_question']:>8}")
        lines.append("")
    lines.append("primary (net over B + D): " + ", ".join(f"{k} {v}" for k, v in rep["primary"].items()))
    s = rep["selection"]
    lines.append(f"selection, held-out top-1 ({s['rows']} rows): trained {s['trained']:.1%}, untrained {s['untrained']:.1%}, "
                 f"always {s.get('majority_action', '?')!r} {s.get('majority', 0):.1%}, chance {s['chance']:.1%}"
                 + (f", active {s['active']:.1%}" if "active" in s else ""))
    c = rep["comparisons"]["rules+trained"]
    lines.append(f"against the rules alone (navigate): {c['transitions']}")
    lines.append(f"  rule-right answers lost: {len(c['regressions'])}; wrong answers added: {len(c['new_wrong'])}")
    for w in c["new_wrong"]:
        lines.append(f"    [{w['set']}] {w['question']!r} -> {w['said']!r}  ({w['plan']})")
    comp = rep.get("computer")
    if comp:
        lines += ["", "the whole computer (router, lookup, knowledge pathway): what is said"]
        lines.append(f"  {'set':<34}" + "".join(f"{s:>22}" for s in next(iter(comp["results"].values())) if s != "n"))
        for set_name, res in comp["results"].items():
            lines.append(f"  {set_name:<34}" + "".join(f"{r['right']:>9} /{r['wrong']:>3} /{r['abstained']:>3} n{r['net']:>3}"
                                                      for s, r in res.items() if s != "n"))
        cc = comp["comparisons"]["computer+trained"]
        lines.append(f"  against the computer without the model: {cc['transitions']}")
        lines.append(f"  right answers lost: {len(cc['regressions'])}; wrong answers added: {len(cc['new_wrong'])}")
        for w in cc["regressions"]:
            lines.append(f"    LOST [{w['set']}] {w['question']!r}: {w['was']!r} -> {w['now']!r}")
        for w in cc["new_wrong"]:
            lines.append(f"    [{w['set']}] {w['question']!r} -> {w['said']!r}  ({w['plan']})")
    lines.append("")
    for g, v in rep["gates"].items():
        lines.append(f"  {'PASS' if v['pass'] else 'FAIL'}  {g:<24} {v['why']}")
    lines.append(f"\n{'ALL GATES PASS' if rep['passed'] else 'NOT ELIGIBLE FOR PROMOTION'}")
    for n in rep["notes"]:
        lines.append(f"note: {n}")
    return "\n".join(lines) + "\n"
