"""Measure how the capability corpus reads paraphrases.

    python3 tools/esi_measure.py tests/esi_heldout.jsonl [--raw] [--show]

Each line of the set is {"text": ..., "expect": ...}; ``expect`` is a
capability name, an analysis name, or "-" for a sentence that must not
resolve to anything.  The corpus is built with no registry, so relative
adjustments ("make it brighter"), which need a live reading, are out of
scope — the sets contain none.

``--raw`` turns the lexical pass and answer validation off, giving the
phrasebook's figure before ESI.

A set whose lines also carry ``concepts``, ``attach`` and ``not_attach``
(tests/esi_context.jsonl) is scored on three outcomes, so that a vocabulary
problem is not mistaken for a grammar problem:

    recognition     every concept in ``concepts`` was understood
    attachment      the possessives ("my", "your") attached to exactly the nouns in
                    ``attach`` — none missing, none extra, none in
                    ``not_attach`` (lines with no possessive: n/a)
    interpretation  the sentence resolved to ``expect``
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


def known_labels() -> dict:
    """Every answer a sentence may be labelled with, and what it means."""
    from earth_os.capability_corpus import ANALYSES
    from earth_os.platform.capabilities import CATALOGUE
    out = {"-": "nothing: the sentence is not a request to this computer"}
    out.update({name: a.summary for name, a in sorted(ANALYSES.items())})
    out.update({name: spec.summary + (" [an act]" if spec.effectful else "")
                for name, spec in sorted(CATALOGUE.items())})
    return out


def read_rows(path: Path) -> list:
    """The set's lines; a label nobody could resolve to is an error, not a miss."""
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    labels = known_labels()
    bad = sorted({r["expect"] for r in rows if r.get("expect") not in labels})
    if bad:
        raise SystemExit(f"{path.name}: unknown label(s) {', '.join(map(repr, bad))}. "
                         f"See: {Path(__file__).name} --labels")
    return rows


def measure(path: Path, lexical: bool = True):
    corpus = CapabilityCorpus(registry=None, lexical=lexical, validate=lexical)
    rows = read_rows(path)
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


def measure_context(path: Path, lexical: bool = True):
    """Three outcomes per line: (row, recognised, attached or None, got)."""
    corpus = CapabilityCorpus(registry=None, lexical=lexical, validate=lexical)
    if corpus._lexicon is None:                       # the layer is off: read anyway
        from earth_os.lexical import Lexicon
        reader = Lexicon()
    else:
        reader = corpus._lexicon
    rows = read_rows(path)
    out = []
    for row in rows:
        reading = reader.read(row["text"])
        # This set labels possessives (phase 3A); question-word attachments
        # ("what>cpu", routing P1) are scored by tests/mind_routing.jsonl.
        attachments = {a for a in (getattr(reading, "attachments", ()) or ())
                       if a.startswith(("user>", "computer>"))}
        recognised = set(row.get("concepts", ())) <= set(reading.concepts)
        # Exact, not "the expected ones are there": an attachment nobody
        # labelled is as wrong as a missing one. A line with no attachment
        # labels is scored only if the reading made attachments anyway.
        attached = None
        if row.get("attach") or row.get("not_attach") or attachments:
            attached = (attachments == set(row.get("attach", ()))
                        and not attachments & set(row.get("not_attach", ())))
        out.append((row, recognised, attached, label(corpus.resolve(row["text"]))))
    return out


def context_summary(results) -> dict:
    def tally(rows):
        rec = sum(1 for _r, ok, _a, _g in rows if ok)
        att = [a for _r, _ok, a, _g in rows if a is not None]
        interp = sum(1 for r, _ok, _a, got in rows if got == r["expect"])
        # False positives, kept apart from silence: answering wrongly is
        # worse than not answering, and answering a non-request is worst.
        wrong = sum(1 for r, _ok, _a, got in rows
                    if r["expect"] != "-" and got not in ("-", r["expect"]))
        silent = sum(1 for r, _ok, _a, got in rows if r["expect"] != "-" and got == "-")
        requests_not = [got for r, _ok, _a, got in rows if r["expect"] == "-"]
        return {"lines": len(rows), "recognition": rec, "attachment": sum(att),
                "attachment_lines": len(att), "interpretation": interp,
                "wrong": wrong, "unanswered": silent,
                "non_requests": len(requests_not),
                "non_requests_answered": sum(1 for g in requests_not if g != "-")}
    by = {}
    for item in results:
        by.setdefault(item[0].get("category", "-"), []).append(item)
    return {"all": tally(results), **{k: tally(v) for k, v in by.items()}}


def _print_context(path: Path, results, raw: bool, show: bool) -> None:
    s = context_summary(results)
    print(f"{path.name}{' (raw)' if raw else ''}: {s['all']['lines']} lines")
    print(f"  {'':22} {'recognition':>12} {'attachment':>11} {'interpretation':>15}"
          f"  {'wrong':>5} {'silent':>6} {'non-requests answered':>22}")
    for name, t in s.items():
        att = (f"{t['attachment']}/{t['attachment_lines']}" if t["attachment_lines"]
               else "n/a")
        fp = (f"{t['non_requests_answered']}/{t['non_requests']}" if t["non_requests"]
              else "n/a")
        print(f"  {name:22} {t['recognition']:>8}/{t['lines']:<3} {att:>11} "
              f"{t['interpretation']:>11}/{t['lines']:<3}  {t['wrong']:>5} "
              f"{t['unanswered']:>6} {fp:>22}")
    if show:
        for row, recognised, attached, got in results:
            marks = []
            if not recognised:
                marks.append("recognition")
            if attached is False:
                marks.append("attachment")
            if got != row["expect"]:
                marks.append(f"interpretation: wanted {row['expect']}, got {got}")
            if marks:
                print(f"  {row['text']!r}: " + "; ".join(marks))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, nargs="?")
    parser.add_argument("--labels", action="store_true",
                        help="list every label a sentence may be given, and stop")
    parser.add_argument("--raw", action="store_true", help="lexical pass off")
    parser.add_argument("--show", action="store_true", help="list every miss")
    args = parser.parse_args(argv)
    if args.labels or args.path is None:
        for name, meaning in known_labels().items():
            print(f"{name:28} {meaning}")
        return 0
    first = next((l for l in args.path.read_text().splitlines() if l.strip()), "{}")
    if "concepts" in json.loads(first):
        _print_context(args.path, measure_context(args.path, lexical=not args.raw),
                       args.raw, args.show)
        return 0
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
