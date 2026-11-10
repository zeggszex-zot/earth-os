#!/usr/bin/env python3
"""How often is an accepted mapping right?  Precision from review sheets marked by hand.

    python3 tools/learn_lexicon.py --corpus data --out lexicon.json --review-out review.jsonl
    # mark each row: "correct": true / false   ("would someone asking about a thing with this word mean this relation?")
    python3 tools/calibrate_lexicon.py review.jsonl [more.jsonl ...]

Reports precision by kind of evidence and by confidence band, with a 95% interval (Wilson), so a confidence
of 0.8 can be compared with how often 0.8 was actually right.  Rows not marked yet are counted as unreviewed.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def report(rows: list[dict]) -> str:
    marked = [r for r in rows if r.get("correct") is not None]
    out = [f"{len(rows)} mappings, {len(marked)} reviewed"]
    for title, key in (("by evidence", lambda r: r.get("how", "?")),
                       ("by confidence", lambda r: f"{math.floor(r['conf'] * 10) / 10:.1f}-{math.floor(r['conf'] * 10) / 10 + 0.1:.1f}"),
                       ("by status", lambda r: r.get("status", "?"))):
        groups = defaultdict(list)
        for r in marked:
            groups[key(r)].append(bool(r["correct"]))
        out.append(f"\n{title}:")
        for g, v in sorted(groups.items()):
            lo, hi = wilson(sum(v), len(v))
            out.append(f"  {g:<34} {sum(v):>3}/{len(v):<3} = {sum(v) / len(v):>4.0%}   (95%: {lo:.0%}-{hi:.0%})")
    wrong = [r for r in marked if not r["correct"]]
    if wrong:
        out.append("\nmarked wrong: " + ", ".join(f"{r['word']}->{r['relation']}" for r in wrong))
    return "\n".join(out)


if __name__ == "__main__":
    rows = [json.loads(l) for p in sys.argv[1:] for l in open(p) if l.strip()]
    print(report(rows))
