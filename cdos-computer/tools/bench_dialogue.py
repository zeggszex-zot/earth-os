#!/usr/bin/env python3
"""Benchmark the whole turn (Computer.think), not just the navigator, on fixed question sets.

    python3 tools/bench_dialogue.py                          # this tree
    python3 tools/bench_dialogue.py --root ../cdos-computer.orig  # another tree (e.g. the baseline), same questions
    python3 tools/bench_dialogue.py --json out.json

Two kinds of set, both on the test corpus (tests/mini_corpus.py; DEVICES added for the analysis set):

  regression   the five existing JSONL sets, one question at a time with a fresh computer, scored with
               computer.navdata.score().  Run twice: through navigate() with the rule planner (the README's
               numbers) and through Computer.think() (what the computer actually says, lookup included).
  dialogues    tests/analysis_heldout.jsonl: multi-turn conversations (calculations over corpus facts,
               estimates with stated inputs, clarification, follow-ups, diagnostics).  One computer per dialogue.

Scoring a turn (dialogues):
  answered     right: every gold string is in the reply and no "not" string is; abstained: the reply asks a
               question or admits it can't; otherwise wrong.
  clarify      right: the reply asks (see ASKS) and names every gold string; wrong: it gave a figure instead.
  unsupported  right: it admits it can't (ADMITS); wrong: anything else.
  passthrough  an existing behaviour the new code must not break: right if every gold string is there.
  status       right: it reports a reading or says the sensor isn't available, and names the gold strings.

Resource figures: ms and CPU ms per turn (this process), peak RSS of the process (ru_maxrss), and shard
files read from disk (corpus LRU misses).  These are from whatever machine runs this, not from a Pi.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import resource
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SETS = ["navigator_questions", "lexical_questions", "lexical_paraphrases", "controller_heldout", "lexicon_heldout"]

ASKS = re.compile(r"\b(specify|which one|which (?:figure|value)|tell me (?:the|its|your)|what is the battery's|in what unit)\b",
                  re.I)
ADMITS = re.compile(r"(can't|cannot|can not|couldn't|doesn't (?:say|have|list)|don't (?:know|have)|won't|isn't in|"
                    r"not available|no (?:\w+ )?(?:sensor|reading)|i'm not able|unable)", re.I)


def is_admission(text: str) -> bool:
    return bool(ADMITS.search(text)) or text.startswith("Hoo. I don't know")


def score_turn(turn: dict, text: str) -> str:
    t = text.lower()
    gold = [g.lower() for g in turn.get("gold", [])]
    has_gold = all(g in t for g in gold)
    bad = any(n.lower() in t for n in turn.get("not", []))
    e = turn["expect"]
    if e == "answered":
        if has_gold and not bad:
            return "right"
        return "abstained" if (ASKS.search(text) or is_admission(text)) else "wrong"
    if e == "clarify":
        if ASKS.search(text) and has_gold:
            return "right"
        return "abstained" if is_admission(text) else "wrong"
    if e == "unsupported":
        return "right" if is_admission(text) else "wrong"
    if e == "passthrough":
        return "right" if has_gold and not bad else "wrong"
    if e == "status":
        ok = bool(re.search(r"\d", text)) or "not available" in t
        return "right" if ok and has_gold and "corpus doesn't" not in t and "don't know that one" not in t else "wrong"
    raise ValueError(e)


def load_tree(root: Path):
    for m in [m for m in sys.modules if m == "computer" or m.startswith("computer.")]:
        del sys.modules[m]
    sys.path.insert(0, str(root))
    mods = {n: importlib.import_module(f"computer.{n}") for n in ("config", "corpus", "core", "navdata", "planner",
                                                             "navigator")}
    sys.path.pop(0)
    return mods


def counting(corpus):
    """Count shard files read from disk (LRU misses)."""
    orig = corpus._shard
    corpus.reads = 0

    def shard(kind, n):
        if f"{kind}/{n:03d}" not in corpus.cache:
            corpus.reads += 1
        return orig(kind, n)
    corpus._shard = shard
    return corpus


def make_computer(m, data):
    cfg = m["config"].Config(corpus=str(data), llm_endpoint="", llm_mode="off", log_file="")
    if hasattr(cfg, "state_file"):
        cfg.state_file = ""
    corpus = counting(m["corpus"].Corpus(str(data)).load())
    return m["core"].Computer(cfg, corpus), corpus


def run_regression(m, data, path: Path) -> dict:
    ex = [json.loads(l) for l in path.open() if l.strip()]
    nav_c, computer_c = Counter(), Counter()
    corpus = m["corpus"].Corpus(str(data)).load()
    sysm = m["navdata"].NavigatorSystem(corpus, [m["planner"].RulePlanner(corpus)], "nav-rules")
    for e in ex:
        text, committed = sysm.answer(e["question"])
        nav_c[m["navdata"].score(e, text, committed)] += 1
    t0, c0, reads = time.monotonic(), time.process_time(), 0
    for e in ex:
        computer, corp = make_computer(m, data)
        r = computer.think(e["question"])
        reads += corp.reads
        computer_c[m["navdata"].score(e, r.text, not is_admission(r.text))] += 1
    n = len(ex)
    return {"n": n, "nav_rules": dict(nav_c), "computer": dict(computer_c), "ms_per_q": (time.monotonic() - t0) * 1000 / n,
            "cpu_ms_per_q": (time.process_time() - c0) * 1000 / n, "shard_reads_per_q": reads / n}


def run_dialogues(m, data, path: Path, show: bool) -> dict:
    dialogues = [json.loads(l) for l in path.open() if l.strip()]
    by_expect, total, wrong = {}, Counter(), []
    turns, t0, c0, reads = 0, time.monotonic(), time.process_time(), 0
    for d in dialogues:
        computer, corp = make_computer(m, data)
        for i, turn in enumerate(d["turns"]):
            r = computer.think(turn["q"])
            s = score_turn(turn, r.text)
            turns += 1
            total[s] += 1
            by_expect.setdefault(turn["expect"], Counter())[s] += 1
            if s != "right":
                wrong.append({"id": d["id"], "turn": i + 1, "q": turn["q"], "score": s, "said": r.text})
            if show:
                print(f"  [{s:>9}] {d['id']} #{i + 1}: {turn['q']}\n              -> {r.text}")
        reads += corp.reads
    return {"dialogues": len(dialogues), "turns": turns, "total": dict(total),
            "by_expect": {k: dict(v) for k, v in by_expect.items()}, "not_right": wrong,
            "ms_per_turn": (time.monotonic() - t0) * 1000 / max(1, turns),
            "cpu_ms_per_turn": (time.process_time() - c0) * 1000 / max(1, turns),
            "shard_reads_per_turn": reads / max(1, turns)}


def rwa(c: dict) -> str:
    return f"{c.get('right', 0)}/{c.get('wrong', 0)}/{c.get('abstained', 0)}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(HERE), help="the cdos-computer tree to benchmark")
    ap.add_argument("--dialogues", default=str(HERE / "tests" / "analysis_heldout.jsonl"))
    ap.add_argument("--show", action="store_true", help="print every dialogue turn")
    ap.add_argument("--json", help="write the report here")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()

    sys.path.insert(0, str(HERE / "tests"))
    import mini_corpus                                       # the test corpus from *this* tree, for both runs
    tmp = Path(tempfile.mkdtemp())
    plain = mini_corpus.build(tmp / "plain", mini_corpus.F)
    devices = mini_corpus.build(tmp / "devices", mini_corpus.F + getattr(mini_corpus, "DEVICES", []))

    m = load_tree(root)
    report = {"root": str(root), "regression": {}, "dialogues": None}
    print(f"tree: {root}\n")
    print(f"{'set':<22}{'n':>4}  {'navigate() rules r/w/a':>24}  {'Computer.think r/w/a':>17}  {'ms/q':>6}  {'cpu ms':>7}  {'reads/q':>7}")
    for s in SETS:
        r = run_regression(m, plain, HERE / "tests" / f"{s}.jsonl")
        report["regression"][s] = r
        print(f"{s:<22}{r['n']:>4}  {rwa(r['nav_rules']):>24}  {rwa(r['computer']):>17}  {r['ms_per_q']:>6.1f}  "
              f"{r['cpu_ms_per_q']:>7.1f}  {r['shard_reads_per_q']:>7.1f}")
    print()
    d = run_dialogues(m, devices, Path(a.dialogues), a.show)
    report["dialogues"] = d
    print(f"\ndialogues ({d['dialogues']} conversations, {d['turns']} turns): right/wrong/abstained {rwa(d['total'])}")
    for k, v in sorted(d["by_expect"].items()):
        print(f"  {k:<12} {rwa(v)}")
    print(f"  {d['ms_per_turn']:.1f} ms/turn, {d['cpu_ms_per_turn']:.1f} CPU ms/turn, {d['shard_reads_per_turn']:.2f} shard reads/turn")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    report["peak_rss_mb"] = peak
    print(f"\npeak RSS of this process: {peak:.1f} MB")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
