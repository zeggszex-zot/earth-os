#!/usr/bin/env python3
"""Check a built data/ folder without changing it: metadata, every shard, the indexes, integrity and version.

    python3 tools/validate_corpus.py data
    python3 tools/validate_corpus.py data --json report.json

Read-only.  It opens files for reading and nothing else (tests/test_validate_corpus.py checks that).

  metadata     meta.json has its fields; s_shards / v_shards match the files on disk
  shards       every shard decompresses; s rows have 10 columns, v rows 6; kinds are S/N/T/D; numbers, dates,
               confidences and years parse
  placement    every subject is in the shard the reader looks in (fnv1a(key) % s_shards), and every value key in
               its v shard: a misplaced row could never be found
  totals       rows = meta.facts; subjects in the shards = subjects.bin = meta.subjects; relations = meta's list
  reverse      every S fact has its reverse row and every reverse row has its fact (WHO depends on it)
  integrity    Corpus(base).load(), the computer's own path: integrity status, and the version is the same on every load
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from computer.corpus import Corpus, fnv1a, key_of  # noqa: E402
from computer.pfrow import DATE, DEEP_TIME, KINDS, YEAR, field_problems  # noqa: E402,F401


def lines(path: Path):
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    for line in data.decode("utf-8").split("\n"):
        if line:
            yield line


def validate(base: Path, loads: int = 3, sample: int = 8) -> dict:
    t0 = time.perf_counter()
    base = Path(base)
    rep = {"base": str(base), "problems": [], "counts": {}, "examples": {}}
    bad = Counter()

    def problem(kind, example):
        bad[kind] += 1
        rep["examples"].setdefault(kind, [])
        if len(rep["examples"][kind]) < sample:
            rep["examples"][kind].append(example)

    meta = json.loads((base / "meta.json").read_text())
    for k in ("facts", "subjects", "s_shards", "v_shards", "relation_names"):
        if k not in meta:
            problem("meta field missing", k)
    s_files, v_files = sorted(base.glob("s/*.bin")), sorted(base.glob("v/*.bin"))
    if len(s_files) != meta.get("s_shards") or len(v_files) != meta.get("v_shards"):
        problem("shard count differs from meta", f"s {len(s_files)}/{meta.get('s_shards')}, v {len(v_files)}/{meta.get('v_shards')}")

    subjects_bin = set(lines(base / "subjects.bin"))
    forward, reverse = Counter(), Counter()
    seen_subjects, rels, rows, kinds, sources = set(), set(), 0, Counter(), Counter()
    dup, deep_time = Counter(), 0
    for p in s_files:
        n = int(p.stem)
        for line in lines(p):
            c = line.split("\t")
            rows += 1
            if len(c) != 10:
                problem("s row without 10 columns", f"{p.name}: {line[:120]}")
                continue
            subj, rel, kind, value, unit, start, until, source, conf, qual = c
            seen_subjects.add(subj)
            rels.add(rel)
            kinds[kind] += 1
            sources[source] += 1
            dup[line] += 1
            if fnv1a(subj) % meta["s_shards"] != n:
                problem("subject in the wrong s shard", f"{subj} in {p.name}")
            for name in field_problems(kind, value, start, until, conf, qual):   # shared with acquire (pfrow)
                problem(name, line[:120])
            if kind == "D" and DATE.match(value.strip()) and DEEP_TIME.match(value.strip()):
                deep_time += 1                     # valid (a geological year), counted so a reader knows they exist
            if kind == "S":
                forward[(key_of(value), subj, rel, start, until)] += 1
    for p in v_files:
        n = int(p.stem)
        for line in lines(p):
            c = line.split("\t")
            if len(c) != 6:
                problem("v row without 6 columns", f"{p.name}: {line[:120]}")
                continue
            vk, subj, rel, start, until, _ = c
            if fnv1a(vk) % meta["v_shards"] != n:
                problem("value key in the wrong v shard", f"{vk} in {p.name}")
            reverse[(vk, subj, rel, start, until)] += 1
    missing_rev = forward - reverse
    orphan_rev = reverse - forward
    for k in list(missing_rev)[:sample]:
        problem("S fact without its reverse row", str(k))
    for k in list(orphan_rev)[:sample]:
        problem("reverse row without its fact", str(k))
    bad["S fact without its reverse row"] = sum(missing_rev.values())
    bad["reverse row without its fact"] = sum(orphan_rev.values())
    if rows != meta.get("facts"):
        problem("row count differs from meta.facts", f"{rows} rows, meta says {meta.get('facts')}")
    if seen_subjects != subjects_bin:
        problem("subjects.bin differs from the shards' subjects",
                f"{len(seen_subjects - subjects_bin)} only in shards, {len(subjects_bin - seen_subjects)} only in subjects.bin")
    if len(subjects_bin) != meta.get("subjects"):
        problem("subject count differs from meta.subjects", f"{len(subjects_bin)} vs {meta.get('subjects')}")
    if rels != set(meta.get("relation_names", [])):
        problem("relations differ from meta.relation_names", f"{sorted(rels ^ set(meta.get('relation_names', [])))[:10]}")
    dups = sum(v - 1 for v in dup.values() if v > 1)
    if dups:
        problem("identical rows", f"{dups} rows repeat another exactly")

    t1 = time.perf_counter()
    versions, statuses, load_s = [], [], []
    for _ in range(loads):
        t = time.perf_counter()
        c = Corpus(base).load()
        load_s.append(round(time.perf_counter() - t, 3))
        versions.append(c.version)
        statuses.append(c.integrity)
    if len(set(versions)) != 1:
        problem("version differs between loads", str(versions))
    rep["counts"] = {"rows": rows, "subjects": len(subjects_bin), "relations": len(rels), "kinds": dict(kinds), "deep_time_dates": deep_time,
                     "sources": dict(sources.most_common()), "s_shards": len(s_files), "v_shards": len(v_files),
                     "reverse_rows": sum(reverse.values())}
    rep["integrity"] = {"status": statuses, "version": versions[0], "stored": meta.get("content_sha256", ""),
                        "load_seconds": load_s}
    rep["problems"] = {k: v for k, v in bad.items() if v}
    rep["seconds"] = {"scan": round(t1 - t0, 1), "loads": round(time.perf_counter() - t1, 1)}
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data")
    ap.add_argument("--loads", type=int, default=3)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    rep = validate(Path(a.data), a.loads)
    c = rep["counts"]
    print(f"{rep['base']}: {c['rows']:,} rows, {c['subjects']:,} subjects, {c['relations']} relations, "
          f"{c['s_shards']}+{c['v_shards']} shards ({rep['seconds']['scan']} s)")
    print(f"  kinds {c['kinds']}")
    print(f"  sources {c['sources']}")
    i = rep["integrity"]
    print(f"  integrity {i['status']}, version {i['version'][:23]}…, stored {i['stored'][:16] or '(none)'}…, "
          f"load {i['load_seconds']} s")
    if rep["problems"]:
        for k, v in rep["problems"].items():
            print(f"  PROBLEM {k}: {v:,}")
            for e in rep["examples"].get(k, [])[:3]:
                print(f"      e.g. {e}")
    else:
        print("  no problems")
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=2, default=str))
    return 1 if rep["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
