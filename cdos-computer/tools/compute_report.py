#!/usr/bin/env python3
"""What did each profile cost?  Reads the computer's log (computer.log.jsonl): every turn records its profile, wall time,
CPU time, energy (where the machine reports it) and the mini LM's own timings.

    python3 tools/compute_report.py computer.log.jsonl
    python3 tools/compute_report.py computer.log.jsonl --since "2026-10-01"

Run the computer on one profile for a while, then another (or set compute_profile = "auto"), and compare.
Different questions cost different amounts, so compare by faculty, not just overall."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict


def median(xs):
    xs = [x for x in xs if x is not None]
    return f"{statistics.median(xs):.0f}" if xs else "-"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log")
    ap.add_argument("--since", default="")
    a = ap.parse_args()
    groups = defaultdict(list)
    for line in open(a.log, encoding="utf-8"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        c = e.get("compute")
        if not c or e.get("t", "") < a.since:
            continue
        groups[(c.get("profile", "?"), e.get("faculty", "?"))].append((e, c))
    if not groups:
        print("no turns with compute records yet (they start with the compute engine)")
        return
    print(f"{'profile':<10}{'faculty':<11}{'turns':>6}{'ms':>7}{'cpu ms':>8}{'energy mJ':>10}{'LM calls':>9}"
          f"{'LM ms':>7}{'tok/s':>7}{'LM voiced':>10}")
    for (prof, fac), rows in sorted(groups.items()):
        lm = [x for _, c in rows for x in c.get("lm") or []]
        voiced = sum(e.get("voice", "computer") not in ("computer", "owl") for e, _ in rows)
        energy = [c["energy_j"] * 1000 for _, c in rows if "energy_j" in c]
        print(f"{prof:<10}{fac:<11}{len(rows):>6}{median(c['ms'] for _, c in rows):>7}"
              f"{median(c.get('cpu_ms') for _, c in rows):>8}{median(energy):>10}{len(lm):>9}"
              f"{median(x.get('ms') for x in lm):>7}{median(x.get('tok_s') for x in lm):>7}"
              f"{voiced / len(rows):>10.0%}")


if __name__ == "__main__":
    main()
