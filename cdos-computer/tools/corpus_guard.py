#!/usr/bin/env python3
"""The corpus guard, for a person to run (computer/corpus_guard.py; docs/CORPUS_GROWTH_AUDIT.md, phase 1).

    python3 tools/corpus_guard.py status  CORPUS.pf
    python3 tools/corpus_guard.py init    CORPUS.pf --by NAME --reason TEXT     # start guarding; only reads corpus.pf
    python3 tools/corpus_guard.py verify  CORPUS.pf --by NAME --reason TEXT     # build + validate + review; record it
    python3 tools/corpus_guard.py backup  CORPUS.pf --by NAME --reason TEXT
    python3 tools/corpus_guard.py restore CORPUS.pf BACKUP_ID --by NAME --reason TEXT
    python3 tools/corpus_guard.py adopt   CORPUS.pf --by NAME --reason TEXT     # accept an outside change, on record
    python3 tools/corpus_guard.py prune   CORPUS.pf --by NAME

verify builds the candidate into a temporary folder the way production does (build_shards --fix-wordnet
--fix-wikidata --fix-keys), runs validate_corpus on it, and asks the real-corpus review questions
(tests/real_corpus_review.jsonl).  It fails on any validator problem, and on any review case that passed on the last
verified build and doesn't now.  Cases that already failed are recorded, not held against the candidate.
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

from computer.corpus_guard import CorpusGuard, GuardError  # noqa: E402

REVIEW_CASES = ROOT / "tests" / "real_corpus_review.jsonl"
BUILD_FLAGS = {"fix_wordnet": True, "fix_wikidata": True, "fix_keys": True}


def verify(candidate: Path, baseline: dict | None, cases_path: Path = REVIEW_CASES, shards: int = 256,
           log=lambda *a: None) -> dict:
    """Build, validate and review a candidate corpus.pf.  ok is False with `why` on the first kind of problem."""
    import build_shards

    with tempfile.TemporaryDirectory(prefix="cdos-verify-") as tmp:
        data = Path(tmp) / "data"
        build_shards._build(candidate, data, shards, shards, log=log, **BUILD_FLAGS)
        return check_built(data, baseline, cases_path)


def check_built(data: Path, baseline: dict | None, cases_path: Path = REVIEW_CASES) -> dict:
    """Validate and review an already built data/ folder: verify() without the build."""
    import review_real_corpus
    import validate_corpus

    val = validate_corpus.validate(data, loads=2)
    out = {"version": val["integrity"]["version"], "facts": val["counts"]["rows"], "problems": val["problems"]}
    if val["problems"]:
        return {**out, "ok": False, "why": "the validator found problems: " + json.dumps(val["problems"])}
    if set(val["integrity"]["status"]) != {"verified"}:
        return {**out, "ok": False, "why": f"integrity {val['integrity']['status']}"}
    with open(cases_path, encoding="utf-8") as fh:
        cases = [json.loads(line) for line in fh if line.strip()]
    rep = review_real_corpus.run(str(data), cases) if cases else {"results": []}
    review = {x["id"]: x["verdict"] for x in rep["results"]}
    before = (baseline or {}).get("review") or {}
    regressed = sorted(k for k, v in before.items() if v == "pass" and review.get(k) != "pass")
    out["review"] = review
    if regressed:
        return {**out, "ok": False, "regressed": regressed,
                "why": f"{len(regressed)} review case(s) passed on the last verified build and don't now: "
                       + ", ".join(regressed)}
    return {**out, "ok": True}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["status", "init", "verify", "backup", "restore", "adopt", "prune"])
    ap.add_argument("corpus")
    ap.add_argument("backup_id", nargs="?")
    ap.add_argument("--by", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--keep", type=int, default=5)
    a = ap.parse_args(argv)
    g = CorpusGuard(a.corpus, keep=a.keep)
    try:
        if a.command == "status":
            print(json.dumps(g.status(), indent=1))
            return 0
        if a.command == "init":
            out = g.init(a.by, a.reason)
        elif a.command == "verify":
            m = g._read_manifest()
            with g.reading() as pf:
                result = verify(pf, (m or {}).get("verified"), log=print)
            print(json.dumps({k: v for k, v in result.items() if k != "review"}, indent=1))
            if not result["ok"]:
                return 1
            out = g.record_verified(result, a.by, a.reason)
        elif a.command == "backup":
            out = g.backup(a.by, a.reason)
        elif a.command == "restore":
            if not a.backup_id:
                ap.error("restore needs BACKUP_ID (see status)")
            out = g.restore(a.backup_id, a.by, a.reason)
        elif a.command == "adopt":
            out = g.adopt(a.by, a.reason)
        else:
            out = g.prune(a.by)
    except GuardError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
