"""Measure how the capability corpus reads paraphrases.

    python3 tools/esi_measure.py tests/esi_heldout.jsonl [--raw] [--show]

Each line of the set is {"text": ..., "expect": ...}; ``expect`` is a
capability name, an analysis name, or "-" for a sentence that must not
resolve to anything.  The corpus is built with no registry, so relative
adjustments ("make it brighter"), which need a live reading, are out of
scope — the sets contain none.

``--raw`` turns the lexical pass off, giving the figure before ESI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os.capability_corpus import CapabilityCorpus  # noqa: E402


def label(intent) -> str:
    if intent is None:
        return "-"
    return intent.analysis or intent.capability


def measure(path: Path, lexical: bool = True):
    corpus = CapabilityCorpus(registry=None, lexical=lexical)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    results = []
    for row in rows:
        got = label(corpus.resolve(row["text"]))
        results.append((row["text"], row["expect"], got))
    return results


def summary(results) -> dict:
    positive = [r for r in results if r[1] != "-"]
    negative = [r for r in results if r[1] == "-"]
    return {
        "total": len(results),
        "right": sum(1 for r in results if r[1] == r[2]),
        "positives_right": sum(1 for r in positive if r[1] == r[2]),
        "positives": len(positive),
        "positives_unanswered": sum(1 for r in positive if r[2] == "-"),
        "positives_wrong": sum(1 for r in positive if r[2] not in ("-", r[1])),
        "negatives_right": sum(1 for r in negative if r[2] == "-"),
        "negatives": len(negative),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--raw", action="store_true", help="lexical pass off")
    parser.add_argument("--show", action="store_true", help="list every miss")
    args = parser.parse_args(argv)
    results = measure(args.path, lexical=not args.raw)
    s = summary(results)
    print(f"{args.path.name}{' (raw)' if args.raw else ''}: "
          f"{s['right']}/{s['total']} right — "
          f"questions {s['positives_right']}/{s['positives']} "
          f"({s['positives_unanswered']} unanswered, {s['positives_wrong']} wrong), "
          f"non-questions {s['negatives_right']}/{s['negatives']} left alone")
    if args.show:
        for text, want, got in results:
            if want != got:
                print(f"  {text!r}: wanted {want}, got {got}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
