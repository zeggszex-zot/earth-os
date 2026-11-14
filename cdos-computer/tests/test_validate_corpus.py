"""tools/validate_corpus.py: finds what's wrong with a data/ folder, and never changes it."""

import gzip
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).parent))

import mini_corpus                      # noqa: E402
from validate_corpus import validate    # noqa: E402


def snapshot(d: Path) -> dict:
    return {str(p.relative_to(d)): (p.read_bytes(), p.stat().st_mtime_ns) for p in d.rglob("*") if p.is_file()}


class TestValidateCorpus(unittest.TestCase):
    def setUp(self):
        self.d = mini_corpus.build(Path(tempfile.mkdtemp()) / "data")

    def test_a_good_corpus_has_no_problems_and_is_left_alone(self):
        before = snapshot(self.d)
        rep = validate(self.d, loads=2)
        self.assertEqual(rep["problems"], {})
        self.assertEqual(rep["counts"]["rows"], len(mini_corpus.F))
        self.assertEqual(len(set(rep["integrity"]["status"])), 1)
        self.assertEqual(snapshot(self.d), before)

    def test_a_misplaced_row_is_found(self):
        a, b = sorted(self.d.glob("s/*.bin"))[:2]
        rows = gzip.decompress(a.read_bytes()).decode().splitlines()
        b.write_bytes(gzip.compress((gzip.decompress(b.read_bytes()).decode() + rows[0] + "\n").encode()))
        rep = validate(self.d, loads=1)
        self.assertIn("subject in the wrong s shard", rep["problems"])
        self.assertIn("row count differs from meta.facts", rep["problems"])

    def test_a_missing_reverse_row_is_found(self):
        for p in self.d.glob("v/*.bin"):
            p.write_bytes(gzip.compress(b""))
        self.assertIn("S fact without its reverse row", validate(self.d, loads=1)["problems"])

    def test_a_broken_number_and_a_bad_meta_are_found(self):
        p = sorted(self.d.glob("s/*.bin"))[0]
        p.write_bytes(gzip.compress((gzip.decompress(p.read_bytes()).decode() + "SUN\tMASS\tN\tlots\tKG\t\t\tX\t1.0\t\n").encode()))
        meta = json.loads((self.d / "meta.json").read_text())
        meta["subjects"] += 5
        (self.d / "meta.json").write_text(json.dumps(meta))
        probs = validate(self.d, loads=1)["problems"]
        self.assertIn("N value isn't a number", probs)
        self.assertIn("subject count differs from meta.subjects", probs)


if __name__ == "__main__":
    unittest.main()


class TestDeepTimeDates(unittest.TestCase):
    def test_geological_years_are_dates_not_problems(self):
        d = Path(tempfile.mkdtemp()) / "c"
        mini_corpus.build(d, [("MOON", "FOUNDED", "D", "-4527000000", "", None, None, "WIKIDATA", 1.0, ""),
                              ("MOON", "IS_A", "S", "SATELLITE", "", None, None, "DOCUMENT", 1.0, "")])
        rep = validate(d, loads=1)
        self.assertNotIn("D value isn't a date", rep["problems"])
        self.assertEqual(rep["counts"]["deep_time_dates"], 1)
