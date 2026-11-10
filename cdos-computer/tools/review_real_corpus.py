#!/usr/bin/env python3
"""Ask the review questions of a real corpus through the computer's public path, and say how each one went.

    python3 tools/review_real_corpus.py --data data
    python3 tools/review_real_corpus.py --data data --cases tests/real_corpus_review.jsonl --json out.json --md out.md

The corpus is loaded once, by Computer(cfg) as the device loads it (integrity checked); every case then gets a fresh computer
on that same loaded corpus, so one question's context can't leak into the next and no second copy is loaded.

Each case says what to expect, and the corpus rows the expectation rests on:
    answer   committed, every "must" string is said, no "must_not" string is
    admit    says it doesn't know / can't (the corpus lacks it), and no "must_not" string
    yes, no  the reply starts with that word
    any      observed, not scored (no single right answer from this corpus); "must_not" still applies
A case may carry "known": {"class": ..., "why": ...}: a classified failure, reported as known rather than new.
"""

from __future__ import annotations

import argparse
import json
import re
import resource
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from bench_dialogue import is_admission  # noqa: E402
from computer.config import Config  # noqa: E402
from computer.core import Computer  # noqa: E402


def verdict(case: dict, text: str) -> str:
    low = text.lower()
    if any(s.lower() in low for s in case.get("must_not", [])):
        return "fail"
    e = case["expect"]
    if e == "answer":
        return "pass" if not is_admission(text) and all(s.lower() in low for s in case.get("must", [])) else "fail"
    if e == "admit":
        return "pass" if is_admission(text) else "fail"
    if e in ("yes", "no"):
        return "pass" if re.match(rf"^\W*{e}\b", low) else "fail"
    return "observed"


def run(data: str, cases: list[dict]) -> dict:
    cfg = Config(corpus=data, llm_endpoint="", llm_mode="off", log_file="", state_file="")
    t = time.perf_counter()
    first = Computer(cfg)                                   # the public path: Computer loads and checks the corpus
    startup = time.perf_counter() - t
    rss_after_load = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    corpus = first.corpus
    out = []
    for c in cases:
        corpus.last_subject = ""
        computer = Computer(cfg, corpus)                         # fresh conversation, same loaded corpus
        reads0 = len(corpus.cache)
        t = time.perf_counter()
        r = computer.think(c["q"])
        ms = (time.perf_counter() - t) * 1000
        e = r.envelope
        v = verdict(c, r.text)
        out.append({**c, "said": r.text, "faculty": r.faculty, "intent": r.route.intent if r.route else "",
                    "status": e.get("status"), "truth": e.get("truth"), "certainty": e.get("certainty"),
                    "cited": [f"{x['subject']} {x['relation']} {x['value']} {x['unit']} ({x['source']}, {x['id']})".replace("  ", " ")
                              for x in e.get("evidence", [])[:4]],
                    "errors": [x["code"] for x in e.get("errors", [])], "ms": round(ms, 1),
                    "verdict": "known" if v == "fail" and c.get("known") else v})
    return {"data": data, "integrity": corpus.integrity, "version": corpus.version, "startup_s": round(startup, 2),
            "rss_after_load_mb": round(rss_after_load, 1),
            "rss_peak_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
            "facts": (corpus.meta or {}).get("facts"), "results": out}


def markdown(rep: dict) -> str:
    rows = ["| id | category | source | question | expected | said (abridged) | cited evidence | result | class |",
            "|---|---|---|---|---|---|---|---|---|"]
    for x in rep["results"]:
        exp = x["expect"] + (f": {', '.join(x.get('must', []))}" if x.get("must") else "")
        said = x["said"].replace("|", "/")
        said = said if len(said) <= 160 else said[:157] + "..."
        cited = "; ".join(x["cited"][:2]).replace("|", "/") or "—"
        known = x.get("known", {})
        rows.append(f"| {x['id']} | {x['cat']} | {x['src']} | {x['q']} | {exp} | {said} | {cited} | {x['verdict']} | "
                    f"{known.get('class', '')} |")
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True)
    ap.add_argument("--cases", default=str(ROOT / "tests" / "real_corpus_review.jsonl"))
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    cases = [json.loads(l) for l in open(a.cases) if l.strip()]
    rep = run(a.data, cases)
    tally = Counter(x["verdict"] for x in rep["results"])
    print(f"{rep['data']}: {rep['facts']:,} facts, integrity {rep['integrity']}, startup {rep['startup_s']} s, "
          f"RSS {rep['rss_after_load_mb']} MB after load, {rep['rss_peak_mb']} MB peak")
    for x in rep["results"]:
        print(f"[{x['verdict']:>8}] {x['id']:<4} {x['q']}\n           -> {x['said'][:300]}")
        if x["cited"]:
            print(f"           cites {x['cited'][:2]}")
    ms = sorted(x["ms"] for x in rep["results"])
    print(f"\n{dict(tally)};  ms per question: median {ms[len(ms)//2]}, p90 {ms[int(len(ms)*0.9)]}, max {ms[-1]}")
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    if a.md:
        Path(a.md).write_text(markdown(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
