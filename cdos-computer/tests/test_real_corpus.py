"""The real-corpus review (tests/real_corpus_review.jsonl) against a production data/ folder.

Skipped unless CDOS_DATA names a built data/ folder (or ./data exists):
    CDOS_DATA=/path/to/data python3 -m unittest tests/test_real_corpus.py
A case fails the test only if it fails and isn't classified ("known") in the case file: a new failure has to be
looked at and classified (engine defect, corpus data, unsupported query) before the review passes again.
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


@unittest.skipUnless(HAS_DATA, "set CDOS_DATA to a production data/ folder")
class TestRealCorpusReview(unittest.TestCase):
    def test_every_failure_is_classified(self):
        from review_real_corpus import run
        cases = [json.loads(l) for l in open(ROOT / "tests" / "real_corpus_review.jsonl") if l.strip()]
        rep = run(DATA, cases)
        self.assertIn(rep["integrity"], ("verified", "computed"))
        new = [(x["id"], x["q"], x["said"][:200]) for x in rep["results"] if x["verdict"] == "fail"]
        self.assertEqual(new, [])


if __name__ == "__main__":
    unittest.main()
