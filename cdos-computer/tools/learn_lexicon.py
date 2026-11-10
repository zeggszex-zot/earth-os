#!/usr/bin/env python3
"""Learn the relation lexicon (computer/lexicon.py): which words mean which relation, from the corpus's own dictionary
and from questions with known answers.  Every mapping is checked against the knowledge graph and kept with its evidence.

    python3 tools/learn_lexicon.py --corpus data --out lexicon.json
    python3 tools/learn_lexicon.py --corpus data --out lexicon.json --mine my_questions.jsonl --show

Then in config.toml:   relation_lexicon = "lexicon.json"
The corpus is not changed.  Delete lexicon.json to forget everything it learned.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from computer.corpus import Corpus          # noqa: E402
from computer.lexicon import learn          # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", default="data")
    ap.add_argument("--out", default="lexicon.json")
    ap.add_argument("--mine", action="append", default=[], help="JSONL questions with answers (question, gold, expect)")
    ap.add_argument("--heldout", action="append", default=[],
                    help="JSONL of test questions: examples that repeat one, or ask about the same thing, are not learned from")
    ap.add_argument("--subjects", type=int, default=0, help="subjects to scan for how relations are used (0 = all)")
    ap.add_argument("--review-out", help="write the accepted and ambiguous mappings to a JSONL sheet to mark correct/wrong by hand "
                                         "(then: tools/calibrate_lexicon.py sheet.jsonl)")
    ap.add_argument("--show", action="store_true", help="print every proposal and what CDOS decided")
    a = ap.parse_args(argv)
    corpus = Corpus(a.corpus).load()
    if not corpus.ready:
        sys.exit(f"no corpus: {corpus.error}")
    ex = [json.loads(l) for p in a.mine for l in open(p) if l.strip()]
    held = [json.loads(l) for p in a.heldout for l in open(p) if l.strip()]
    lex = learn(corpus, ex, held, max_subjects=a.subjects)
    for d in lex.meta.get("examples_dropped", []):
        print(f"  not learned from: {d['question']!r}  ({d['dropped']})")
    lex.save(a.out)
    st = {}
    for m in lex.mappings:
        st[m.status] = st.get(m.status, 0) + 1
    print(f"wrote {a.out}: {st}")
    if a.review_out:
        with open(a.review_out, "w") as fh:
            for m in lex.mappings:
                if m.status in ("accepted", "ambiguous"):
                    fh.write(json.dumps({"word": m.word, "relation": m.relation, "conf": m.conf, "status": m.status,
                                         "how": m.via[0]["how"] if m.via else "", "evidence": m.via[0] if m.via else {},
                                         "correct": None, "reviewer": ""}) + "\n")
        print(f"review sheet: {a.review_out}  (set \"correct\" to true/false, \"reviewer\" to your name)")
    for m in lex.mappings:
        if a.show or m.status != "rejected":
            how = "; ".join(f"{v['how']}: {v.get('fact') or v.get('subjects')}" for v in m.via)
            print(f"  {m.status:<9} {m.word:<14} -> {m.relation:<18} {m.conf:.2f}  {m.why}  [{how[:110]}]")


if __name__ == "__main__":
    main()
