"""Integration (tools/integrate.py): staged runs into corpus.pf through the corpus guard, only when corpus.integrate is
on, only from runs staged against this exact master, and only if the candidate verifies.  Every test works on a
temporary master; the real corpus is never touched."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import integrate                                                                             # noqa: E402
from computer.acquire import AcquireError                                                    # noqa: E402
from computer.acquire.pipeline import Acquirer                                               # noqa: E402
from computer.corpus_guard import CorpusGuard, VerifyFailed                                  # noqa: E402

T = "\t"


def row(s, r, v, st, sid, q=""):
    return T.join([s, r, "S", v, "", "0", "0", st, sid, "1.0", q]) + "\n"


MASTER = (row("Paris", "CAPITAL_OF", "Q142", "WIKIDATA", "Q90/P1376") + row("France", "CAPITAL", "Q90", "WIKIDATA",
          "Q142/P36") + row("Lion", "IS_A", "MAMMAL", "DOCUMENT", "bio"))
REGISTRY = ('[source.wikidata]\nsource_type = "WIKIDATA"\nlicence = "CC0-1.0"\nattribution = "x"\nparser = "pf"\n'
            'url = "file:///x.pf"\nsha256 = "sha256:' + "0" * 64 + '"\nrelations = { IS_A = "IS_A" }\n'
            'replaces = { source_type = "WIKIDATA", source_id = "^Q[0-9]+/" }\nmax_bytes = 1000\n'
            'approved_by = "t"\napproved_at = "2026-11-15"\n')


def sha(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.master, self.root = d / "corpus" / "corpus.pf", d / "acquire"
        self.master.parent.mkdir()
        self.master.write_text(MASTER)
        CorpusGuard(self.master).init("test", "a test master")
        self.root.mkdir()
        (self.root / "sources.toml").write_text(REGISTRY)
        self.run = self.root / "staging" / "wikidata" / "20261115T000000Z-0001"
        self.run.mkdir(parents=True)
        (self.run / "delta.pf").write_text(row("France", "CAPITAL", "Paris", "WIKIDATA", "Q142/P36", "ENTITY=wd:Q142"))
        (self.run / "held.pf").write_text("# held\n" + row("Paris", "IS_A", "city", "WIKIDATA", "Q90/P31",
                                                              "ENTITY=gn:2988507"))
        self.write_report()
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def write_report(self, **change):
        rep = {"run": self.run.name, "master": {"path": str(self.master), "sha256": sha(self.master)},
               "delta_sha256": sha(self.run / "delta.pf"), "integrated": False,
               "replaces": {"source_type": "WIKIDATA", "source_id": "^Q[0-9]+/"}, **change}
        (self.run / "report.json").write_text(json.dumps(rep))

    def switch(self, on: bool):
        (self.root / "settings.toml").write_text(f"[corpus]\nintegrate = {'true' if on else 'false'}\n")

    def verifier(self, ok=True):
        def v(candidate, baseline):
            self.calls.append(Path(candidate).read_text())
            return {"ok": ok, "why": "a review case regressed", "version": "v", "facts": 2, "review": {}}
        return v

    def go(self, ok=True):
        return integrate.integrate(Acquirer(self.root, self.master), ["wikidata"], "test", "a test integration",
                                   self.verifier(ok))


class TestRefused(Case):
    def test_off_unless_a_person_switched_it_on(self):
        before = self.master.read_bytes()
        with self.assertRaisesRegex(AcquireError, "corpus.integrate is off"):
            self.go()
        self.switch(False)
        with self.assertRaisesRegex(AcquireError, "corpus.integrate is off"):
            self.go()
        (self.root / "settings.toml").write_text("[corpus]\nintegrate = 1\n")
        with self.assertRaisesRegex(AcquireError, "true or false"):
            self.go()
        self.assertEqual(self.master.read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_a_candidate_that_fails_verify_leaves_the_master_byte_identical(self):
        self.switch(True)
        before = self.master.read_bytes()
        with self.assertRaises(VerifyFailed):
            self.go(ok=False)
        self.assertEqual(self.master.read_bytes(), before)
        self.assertFalse(json.loads((self.run / "report.json").read_text())["integrated"])

    def test_a_run_staged_against_another_master_or_changed_since(self):
        self.switch(True)
        self.write_report(master={"sha256": "sha256:" + "1" * 64})
        with self.assertRaisesRegex(AcquireError, "another master"):
            self.go()
        self.write_report()
        with open(self.run / "delta.pf", "a") as fh:
            fh.write(row("Rome", "IS_A", "city", "WIKIDATA", "Q220/P31"))
        with self.assertRaisesRegex(AcquireError, "changed since it was staged"):
            self.go()


class TestIntegrated(Case):
    def test_the_candidate_replaces_the_master_through_the_guard(self):
        self.switch(True)
        old = sha(self.master)
        out = self.go()
        text = self.master.read_text()
        self.assertEqual(self.calls, [text])                                 # what was verified is what went in
        self.assertIn("Q142/P36\t1.0\tENTITY=wd:Q142", text)                 # the new row
        self.assertNotIn("France\tCAPITAL\tS\tQ90", text)                     # the replaced one
        self.assertIn("Q90/P1376", text)                                     # Paris was held back: its old row stays
        self.assertIn("Lion\tIS_A", text)                                    # other sources untouched
        self.assertNotIn("gn:2988507", text)                                 # held rows aren't integrated
        g = CorpusGuard(self.master).status()
        self.assertEqual((g["generation"], g["sha256"], g["ok"]), (2, sha(self.master), True))
        self.assertIn(old, [b["sha256"] for b in g["backups"] if b["state"] == "ok"])   # the old master, backed up
        self.assertEqual(json.loads((self.run / "report.json").read_text())["integrated"]["generation"], 2)
        self.assertEqual(out["rows"], {"kept": 2, "removed": 1, "added": 1})
        with self.assertRaisesRegex(AcquireError, "integrated already"):
            self.go()


class TestThePackageStillNeverWrites(Case):
    def test_its_integrate_points_at_the_tool(self):
        from computer.acquire.__main__ import main
        self.switch(True)
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(main(["integrate", "--root", str(self.root), "--master", str(self.master)]), 2)
        self.assertIn("tools/integrate.py", err.getvalue())


if __name__ == "__main__":
    unittest.main()
