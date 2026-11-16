#!/usr/bin/env python3
"""Integrate approved, staged sources into corpus.pf, through the corpus guard (Q1: corpus.integrate).

    python3 tools/integrate.py SOURCE [SOURCE ...] --by NAME --reason TEXT [--root ~/cdos/acquire]
                                                   [--master ~/cdos/corpus/corpus.pf]

Refused unless every one of these holds:
  * corpus.integrate is on: `[corpus] integrate = true` in <root>/settings.toml, written by a person;
  * the master is guarded, and each source is approved in sources.toml as it was when its run was staged
    (the same `replaces`);
  * each source's latest staged run was staged against this master (its sha256), and its delta.pf is unchanged
    (its sha256);
  * no run is integrated already.

The candidate is the master without each replaced generation (except the rows of things a run held back), then each
run's delta, in the order given: computer/acquire/swap.candidate_lines, the same lines the candidate check built.  It is
written through CorpusGuard.writing; commit() builds it the way production does, validates it and asks the review
questions (tools/corpus_guard.verify): any case that passed on the last verified build and doesn't now refuses it,
and the master stays byte-identical.  Otherwise the master is backed up, replaced, and recorded as verified, and each
run's report.json says which generation it went into.  Held-back rows (held.pf) are not integrated.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from computer.acquire import DEFAULT_MASTER, DEFAULT_ROOT, AcquireError, INTEGRATE_OFF, integrate_on  # noqa: E402
from computer.acquire import registry  # noqa: E402
from computer.acquire.pipeline import Acquirer  # noqa: E402
from computer.acquire.swap import candidate_lines  # noqa: E402
from computer.corpus_guard import CorpusGuard, GuardError  # noqa: E402
from computer.governor import sha256_file, utc, write_atomic  # noqa: E402
from corpus_guard import verify  # noqa: E402


def plan(acq: Acquirer, sources: list[str]) -> list[dict]:
    if not integrate_on(acq.root):
        raise AcquireError(INTEGRATE_OFF)
    master_sha = sha256_file(acq.master)
    out = []
    for sid in sources:
        src = registry.get(acq.registry_path, sid)                 # refused if no longer approved
        runs = acq.runs(sid)
        if not runs:
            raise AcquireError(f"{sid}: nothing staged")
        r = runs[-1]
        d = acq.root / "staging" / sid / r["run"]
        if r.get("integrated"):
            raise AcquireError(f"{sid}: run {r['run']} is integrated already ({r['integrated']})")
        if (r.get("master") or {}).get("sha256") != master_sha:
            raise AcquireError(f"{sid}: run {r['run']} was staged against another master; run the source again")
        if sha256_file(d / "delta.pf") != r.get("delta_sha256"):
            raise AcquireError(f"{sid}: {d / 'delta.pf'} changed since it was staged")
        if (src.replaces or None) != (r.get("replaces") or None):
            raise AcquireError(f"{sid}: sources.toml's replaces differs from the run's; run the source again")
        out.append({"source": sid, "run": r["run"], "dir": d, "replaces": r.get("replaces"),
                    "delta_sha256": r["delta_sha256"]})
    return out


def integrate(acq: Acquirer, sources: list[str], by: str, reason: str, verifier=verify) -> dict:
    runs = plan(acq, sources)
    guard = CorpusGuard(acq.master)
    if not guard.guarded:
        raise AcquireError(f"{acq.master} isn't guarded (tools/corpus_guard.py init): nothing is written")
    counts: dict = {}
    details = {"sources": [{k: x[k] for k in ("source", "run", "delta_sha256")} for x in runs]}
    with guard.writing(by, reason) as w:
        with w.new() as fh:
            for line in candidate_lines(acq.master, [(x["dir"] / "delta.pf", x["replaces"]) for x in runs], counts):
                fh.write(line.encode("utf-8"))
        result = w.commit(verify=verifier, event="integrated", details={**details, "rows": counts})
    for x in runs:
        p = x["dir"] / "report.json"
        rep = json.loads(p.read_text(encoding="utf-8"))
        rep["integrated"] = {"generation": result.get("generation"), "sha256": result.get("sha256"), "at": utc(time.time())}
        write_atomic(p, (json.dumps(rep, indent=1, sort_keys=True) + "\n").encode())
    acq.journal.append({"event": "integrated", **details, "rows": counts, "generation": result.get("generation"),
                        "sha256": result.get("sha256")})
    return {**result, "rows": counts, **details}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--by", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--master", default=str(DEFAULT_MASTER))
    a = ap.parse_args(argv)
    acq = Acquirer(Path(a.root), Path(a.master))
    try:
        out = integrate(acq, a.sources, a.by, a.reason)
    except (AcquireError, GuardError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
