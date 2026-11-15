#!/usr/bin/env python3
"""Preview a generation swap (Q2) in staging, never in the master.

    python3 tools/swap_preview.py SOURCE [--root ~/cdos/acquire] [--master ~/cdos/corpus/corpus.pf]

For the source's last staged run: write candidate.pf (the master without the generation the source replaces, plus
the run's delta), build the master and the candidate the way production does, compare their served rows of that
source relation by relation, validate the candidate and ask it the real-corpus review questions against the master's
verified baseline.  Writes candidate.pf and swap.json in the run's folder.  corpus.pf is only read.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_shards  # noqa: E402
from computer.acquire import DEFAULT_MASTER, DEFAULT_ROOT, AcquireError  # noqa: E402
from computer.acquire.pipeline import Acquirer  # noqa: E402
from computer.acquire.swap import write_candidate  # noqa: E402
from computer.corpus_guard import CorpusGuard  # noqa: E402
from computer.governor import sha256_file, write_atomic  # noqa: E402
from corpus_guard import BUILD_FLAGS, REVIEW_CASES, check_built  # noqa: E402

FLAG_CHANGE = 0.10          # a relation whose served count moves by more than this is listed with examples


def served(data: Path, source_type: str, examples: int = 5) -> tuple[Counter, dict]:
    """(rows of `source_type` by relation, a few example rows of each) in a built data/ folder."""
    counts, samples = Counter(), defaultdict(list)
    for p in sorted(data.glob("s/*.bin")):
        for line in gzip.decompress(p.read_bytes()).decode("utf-8").split("\n"):
            c = line.split("\t")
            if len(c) == 10 and c[7] == source_type:
                counts[c[1]] += 1
                if len(samples[c[1]]) < examples:
                    samples[c[1]].append(f"{c[0]} {c[1]} {c[3]}"[:160])
    return counts, samples


def preview(acq: Acquirer, sid: str, baseline: dict | None, cases_path: Path = REVIEW_CASES,
            shards: int = 256) -> dict:
    runs = acq.runs(sid)
    if not runs:
        raise AcquireError(f"no staged run for {sid}; run it first")
    run = runs[-1]
    rep = run.get("replaces")
    if not rep:
        raise AcquireError(f"{sid}'s run replaces no generation; there is no swap to preview")
    run_dir = acq.root / "staging" / sid / run["run"]
    if sha256_file(acq.master) != run["master"]["sha256"]:
        raise AcquireError("the master changed since this run was staged; run the source again first")
    cand = run_dir / "candidate.pf"
    rows = write_candidate(acq.master, run_dir / "delta.pf", rep, cand)
    st = rep["source_type"]
    with tempfile.TemporaryDirectory(prefix="cdos-swap-", dir=run_dir) as tmp:
        before_meta, _ = build_shards.build(acq.master, Path(tmp) / "master", shards, shards, log=lambda *a: None,
                                            **BUILD_FLAGS)
        after_meta, _ = build_shards.build(cand, Path(tmp) / "candidate", shards, shards, log=lambda *a: None,
                                           **BUILD_FLAGS)
        before, _ = served(Path(tmp) / "master", st)
        after, samples = served(Path(tmp) / "candidate", st)
        check = check_built(Path(tmp) / "candidate", baseline, cases_path)
    relations = {}
    for r in sorted(set(before) | set(after)):
        b, a = before.get(r, 0), after.get(r, 0)
        change = (a - b) / b if b else None
        entry = {"before": b, "after": a, "change": None if change is None else round(change, 4)}
        if change is None or abs(change) > FLAG_CHANGE:
            entry["examples_after"] = samples.get(r, [])
        relations[r] = entry
    base_review = (baseline or {}).get("review") or {}
    out = {
        "format": "cdos-swap-preview/1", "source": sid, "run": run["run"], "source_type": st, "replaces": rep,
        "master": {"sha256": rows["master_sha256"], "version": "sha256:" + before_meta["content_sha256"],
                   "facts": before_meta["facts"]},
        "candidate": {"sha256": rows["candidate_sha256"], "version": "sha256:" + after_meta["content_sha256"],
                      "facts": after_meta["facts"], "path": str(cand)},
        "rows": {k: rows[k] for k in ("kept", "removed_total", "added_total", "removed", "added")},
        "served": {"before_total": sum(before.values()), "after_total": sum(after.values()),
                   "relations": relations,
                   "flagged": sorted(r for r, e in relations.items() if "examples_after" in e)},
        "check": {k: check.get(k) for k in ("ok", "why", "problems", "regressed")},
        "review_changes": {k: {"before": base_review.get(k), "after": v} for k, v in sorted(check.get("review", {}).items())
                           if base_review and base_review.get(k) != v},
        "integrated": False,
    }
    write_atomic(run_dir / "swap.json", (json.dumps(out, indent=1, sort_keys=True) + "\n").encode())
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--master", default=str(DEFAULT_MASTER))
    a = ap.parse_args(argv)
    acq = Acquirer(Path(a.root), Path(a.master))
    guard = CorpusGuard(Path(a.master))
    baseline = ((guard._read_manifest() or {}).get("verified")) if guard.guarded else None
    try:
        out = preview(acq, a.source, baseline)
    except AcquireError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: out[k] for k in ("rows", "served", "check", "review_changes")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
