"""The real-corpus review (tests/real_corpus_review.jsonl) against a production data/ folder.

Skipped unless CDOS_DATA names a built data/ folder (or ./data exists):
    CDOS_DATA=/path/to/data python3 -m unittest tests/test_real_corpus.py
A case fails the test only if it fails and isn't classified ("known") in the case file: a new failure has to be
looked at and classified (engine defect, corpus data, unsupported query) before the review passes again.

KNOWN_FAILURES are the cases that failed on every verified build so far (generation 1, f17a638f..., and generation 2,
9e791281..., recorded by the corpus guard).  They must still fail: one that starts passing fails the test too, so
the list is updated on purpose, never left stale.
"""

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

DATA = os.environ.get("CDOS_DATA") or str(ROOT / "data")
HAS_DATA = (Path(DATA) / "meta.json").exists()
# id -> what the case asks; all five expect an admission that the corpus lacks the fact
KNOWN_FAILURES = {
    "W8": "what does flurbishness mean (no such word)",
    "X1": "the capital of Jupiter",
    "X2": "the population of the Moon",
    "X3": "the mass of France",
    "X6": "the boiling point of Jupiter",
}


@unittest.skipUnless(HAS_DATA, "set CDOS_DATA to a production data/ folder")
class TestRealCorpusReview(unittest.TestCase):
    def test_every_failure_is_classified(self):
        from review_real_corpus import run
        cases = [json.loads(l) for l in open(ROOT / "tests" / "real_corpus_review.jsonl") if l.strip()]
        rep = run(DATA, cases)
        self.assertIn(rep["integrity"], ("verified", "computed"))
        verdict = {x["id"]: x for x in rep["results"]}
        new = [(x["id"], x["q"], x["said"][:200]) for x in rep["results"]
               if x["verdict"] == "fail" and x["id"] not in KNOWN_FAILURES]
        self.assertEqual(new, [], "new failures: classify each, or fix it")
        fixed = [(k, verdict[k]["verdict"], verdict[k]["said"][:200]) for k in KNOWN_FAILURES
                 if verdict.get(k, {}).get("verdict") != "fail"]
        self.assertEqual(fixed, [], "known failures that no longer fail: take them off KNOWN_FAILURES")


if __name__ == "__main__":
    unittest.main()
