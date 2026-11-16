#!/usr/bin/env python3
"""Check what staged runs would do to the answers, before anything is integrated.

    python3 tools/candidate_check.py SOURCE [SOURCE ...] [--root ~/cdos/acquire] [--master ~/cdos/corpus/corpus.pf]
                                     [--cases tests/entity_review.jsonl ...]

Builds the master as it is and a candidate (the master, without any generation a run replaces, plus each source's
latest staged delta), the way production builds them, then asks both the real-corpus review questions:

  - every review case must keep its verdict from the master's verified baseline;
  - every review answer must be word for word the same, unless it is about a name the candidate now holds
    several things under (ENTITY=, D4): those are listed, with both answers, for a person to read;
  - the extra cases (--cases, e.g. the entity review) must pass on the candidate.

Writes candidate_check.json next to the newest run.  corpus.pf is only read.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_shards  # noqa: E402
import review_real_corpus  # noqa: E402
from computer.acquire import DEFAULT_MASTER, DEFAULT_ROOT, AcquireError  # noqa: E402
from computer.acquire.index import replaced_by  # noqa: E402
from computer.acquire.pipeline import Acquirer  # noqa: E402
from computer.corpus import Corpus, entity_of  # noqa: E402
from computer.corpus_guard import CorpusGuard  # noqa: E402
from computer.governor import write_atomic  # noqa: E402
from corpus_guard import BUILD_FLAGS, REVIEW_CASES  # noqa: E402


def cases_of(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_candidate(master: Path, runs: list[tuple[Path, dict | None]], out: Path) -> dict:
    """The master without each replaced generation, then each run's delta; returns row counts."""
    skips = [replaced_by(rep, delta.parent / "held.pf") for delta, rep in runs if rep]
    kept = removed = added = 0
    with open(master, encoding="utf-8") as m, open(out, "w", encoding="utf-8") as fh:
        for line in m:
            c = (line.rstrip("\n").split("\t") + [""] * 11)[:11]
            if line.strip() and not line.startswith("#") and any(s(c) for s in skips):
                removed += 1
                continue
            fh.write(line if line.endswith("\n") else line + "\n")
            kept += 1
        for delta, _ in runs:
            with open(delta, encoding="utf-8") as d:
                for line in d:
                    if line.strip() and not line.startswith("#"):
                        fh.write(line if line.endswith("\n") else line + "\n")
                        added += 1
    return {"kept": kept, "removed": removed, "added": added}


def subjects_cited(result: dict) -> set[str]:
    return {c.split(" ", 1)[0] for c in result.get("cited", [])}


def check(acq: Acquirer, sources: list[str], extra: list[Path], baseline: dict | None, shards: int = 256) -> dict:
    runs, newest = [], None
    for sid in sources:
        rs = acq.runs(sid)
        if not rs:
            raise AcquireError(f"no staged run for {sid}")
        d = acq.root / "staging" / sid / rs[-1]["run"]
        runs.append((d / "delta.pf", rs[-1].get("replaces")))
        newest = d
    legacy = cases_of(REVIEW_CASES)
    extras = [c for p in extra for c in cases_of(p)]
    with tempfile.TemporaryDirectory(prefix="cdos-candidate-", dir=newest) as tmp:
        t = Path(tmp)
        rows = write_candidate(acq.master, runs, t / "candidate.pf")
        build_shards.build(acq.master, t / "master", shards, shards, log=lambda *a: None, **BUILD_FLAGS)
        build_shards.build(t / "candidate.pf", t / "cand", shards, shards, log=lambda *a: None, **BUILD_FLAGS)
        before = {x["id"]: x for x in review_real_corpus.run(str(t / "master"), legacy)["results"]}
        after = {x["id"]: x for x in review_real_corpus.run(str(t / "cand"), legacy)["results"]}
        ext = review_real_corpus.run(str(t / "cand"), extras)["results"] if extras else []
        cand = Corpus(t / "cand").load()
        several = lambda subj: len({entity_of(f) for f in cand.facts_for(subj) if entity_of(f)}) > 1   # noqa: E731
        base = (baseline or {}).get("review") or {k: v["verdict"] for k, v in before.items()}
        regressed = sorted(k for k, v in base.items() if v == "pass" and after.get(k, {}).get("verdict") != "pass")
        changed, unexplained = [], []
        for k in sorted(before):
            if before[k]["said"] == after[k]["said"]:
                continue
            subjects = subjects_cited(after[k]) | subjects_cited(before[k])
            entry = {"id": k, "q": before[k]["q"], "before": before[k]["said"], "after": after[k]["said"],
                     "several_things": sorted(s for s in subjects if several(s))}
            (changed if entry["several_things"] else unexplained).append(entry)
    out = {
        "format": "cdos-candidate-check/1", "sources": sources, "rows": rows,
        "review": {"cases": len(legacy), "regressed": regressed, "changed_for_names_with_several_things": changed,
                   "changed_otherwise": unexplained},
        "extra": [{"id": x["id"], "q": x["q"], "verdict": x["verdict"], "said": x["said"]} for x in ext],
    }
    out["ok"] = not regressed and not unexplained and all(x["verdict"] == "pass" for x in out["extra"])
    write_atomic(newest / "candidate_check.json", (json.dumps(out, indent=1, ensure_ascii=False) + "\n").encode())
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--master", default=str(DEFAULT_MASTER))
    ap.add_argument("--cases", action="append", default=[])
    a = ap.parse_args(argv)
    acq = Acquirer(Path(a.root), Path(a.master))
    guard = CorpusGuard(Path(a.master))
    baseline = ((guard._read_manifest() or {}).get("verified")) if guard.guarded else None
    try:
        out = check(acq, a.sources, [Path(p) for p in a.cases], baseline)
    except AcquireError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
