"""The book-facts bench (bench/book_facts/bench.py), against a fake Ollama: a fixed sample, a fixed prompt and options,
installed models only (never a pull), loopback only, every output recorded, and nothing written near corpus.pf."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "book_facts"))

import bench                                                                                 # noqa: E402

PARA = "Gibbs wrote this. " * 20


def doc(n):
    return {"blocks": [{"id": f"b-{i:05d}", "type": "text", "section": "b-00001", "lines": [i, i + 1], "pages": ["1"],
                        "text": f"Paragraph {i}. " + PARA, "inline_math": []} for i in range(n)]
            + [{"id": "b-99999", "type": "text", "section": None, "lines": [1, 1], "text": PARA, "inline_math": []},
               {"id": "b-99998", "type": "text", "section": "b-1", "lines": [1, 1], "text": "short", "inline_math": []}]}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeOllama:
    def __init__(self, models=("llama3.2:1b",), output="gibbs | IS_A | S | physicist |\nNONE"):
        self.models, self.output, self.asked = models, output, []

    def __call__(self, req):
        path = req.full_url.split("11434", 1)[1]
        body = json.loads(req.data) if req.data else None
        self.asked.append((path, body))
        if path == "/api/version":
            return Resp(b'{"version": "0.35.0"}')
        if path == "/api/tags":
            return Resp(json.dumps({"models": [{"name": m, "digest": "abc123", "details": {"parameter_size": "1.2B"}}
                                               for m in self.models]}).encode())
        if path == "/api/generate":
            return Resp(json.dumps({"response": self.output, "done_reason": "stop", "eval_count": 40,
                                    "eval_duration": 2_000_000_000, "prompt_eval_count": 300,
                                    "prompt_eval_duration": 500_000_000, "total_duration": 2_600_000_000,
                                    "load_duration": 100_000_000}).encode())
        raise AssertionError(f"unexpected {path}")


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.books = {}
        for name, n in (("calculus", 40), ("gibbs", 90)):
            p = self.d / f"{name}.edie.json"
            p.write_text(json.dumps(doc(n)))
            self.books[name] = p
        self.sample = self.d / "sample.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def make(self):
        return bench.make_sample(self.books, self.sample)

    def run_bench(self, fake=None, model="llama3.2:1b"):
        fake = fake or FakeOllama()
        return bench.run(model, bench.Ollama(opener=fake), self.sample, self.d / "results", log=lambda *a: None), fake


class TestTheSample(Case):
    def test_25_and_25_evenly_spaced_and_fixed(self):
        rows = self.make()
        self.assertEqual([r["book"] for r in rows].count("calculus"), 25)
        self.assertEqual([r["book"] for r in rows].count("gibbs"), 25)
        cal = [r["block"] for r in rows if r["book"] == "calculus"]
        self.assertEqual((cal[0], cal[-1]), ("b-00000", "b-00039"))             # first to last, evenly
        self.assertNotIn("b-99999", [r["block"] for r in rows])                 # outside a section
        self.assertNotIn("b-99998", [r["block"] for r in rows])                 # too short
        self.assertTrue(all(r["document_sha256"].startswith("sha256:") for r in rows))
        again = self.d / "again.jsonl"
        bench.make_sample(self.books, again)
        self.assertEqual(again.read_bytes(), self.sample.read_bytes())          # no randomness
        with self.assertRaisesRegex(bench.BenchError, "fixed"):
            self.make()


class TestARun(Case):
    def test_everything_is_recorded(self):
        self.make()
        out, fake = self.run_bench()
        meta = json.loads((out / "run.json").read_text())
        self.assertEqual(meta["options"]["temperature"], 0)
        self.assertEqual(meta["options"]["seed"], 42)
        self.assertEqual((meta["model_digest"], meta["ollama"]), ("abc123", "0.35.0"))
        self.assertTrue(meta["reproducible"]["same_output"])
        outs = [json.loads(x) for x in (out / "outputs.jsonl").read_text().splitlines()]
        self.assertEqual(len(outs), 50)
        self.assertEqual(outs[0]["tokens_per_s"], 20.0)
        gens = [b for p, b in fake.asked if p == "/api/generate"]
        self.assertEqual(len(gens), 51)                                         # 50 + the repeat
        self.assertTrue(all(g["options"] == bench.OPTIONS and not g["stream"] for g in gens))
        self.assertTrue(all(g["keep_alive"] == 0 for g in gens))                # cold: no cached prompt between
        self.assertIn("Paragraph 0.", gens[0]["prompt"])
        self.assertNotIn("{paragraph}", gens[0]["prompt"])
        review = (out / "review.pf").read_text().splitlines()
        self.assertTrue(review[0].startswith("#"))
        self.assertEqual(review[1].split("\t")[7:10], ["LLM", "calculus:b-00000", "0.0"])

    def test_a_model_that_isnt_installed_is_refused_and_never_pulled(self):
        self.make()
        fake = FakeOllama(models=("llama3.2:1b",))
        with self.assertRaisesRegex(bench.BenchError, "isn't installed"):
            self.run_bench(fake, model="llama3.1:8b")
        self.assertNotIn("/api/generate", [p for p, _ in fake.asked])
        self.assertNotIn("/api/pull", Path(bench.__file__).read_text())
        with self.assertRaises(bench.BenchError):
            bench.Ollama(opener=fake)._call("/api/pull", {"model": "x"})

    def test_only_a_local_ollama(self):
        for url in ("http://example.org:11434", "http://10.0.0.5:11434"):
            with self.assertRaisesRegex(bench.BenchError, "local"):
                bench.Ollama(url)


class TestFacts(unittest.TestCase):
    def test_lines_become_checked_facts(self):
        out = bench.facts_of("Here are the facts:\nx | IS_A | S | thing |\ny | MASS | N | heavy | KG\n"
                             "z | is a | S | thing |\nw | A | S\nNONE\nv | HAS_PART | S | leg")
        self.assertEqual([f["auto_check"].split(":")[0] for f in out],
                         ["ok", "malformed", "malformed", "malformed", "ok"])
        self.assertIn("N value isn't a number", out[1]["auto_check"])
        self.assertIn("relation isn't UPPER_CASE", out[2]["auto_check"])
        self.assertEqual(out[4]["unit"], "")                                    # unit left off: four fields


class TestScoring(Case):
    def test_a_repeated_line_is_one_row(self):
        self.make()
        out, _ = self.run_bench(FakeOllama(output="a | IS_A | S | b |\na | IS_A | S | b |\na | IS_A | S | b |\n"
                                                  "c | IS_A | S | d |"))
        rows = bench.make_scorecard(out).read_text().splitlines()
        self.assertEqual(len(rows), 1 + 50 * 2)
        self.assertEqual(rows[1].split("\t")[4], "3")                          # repeats
        s = bench.stats(out)
        self.assertEqual((s["facts"], s["distinct_facts"]), (200, 100))
        self.assertIn("Nothing marked yet: 100 facts", bench.summary(out).read_text())

    def test_scorecard_marks_summary_and_compare(self):
        self.make()
        out, _ = self.run_bench()
        card = bench.make_scorecard(out)
        with self.assertRaisesRegex(bench.BenchError, "your marks"):
            bench.make_scorecard(out)                                           # never over a marked card
        rows = card.read_text().splitlines()
        self.assertEqual(rows[0].split("\t"), bench.SCORE_COLUMNS)
        self.assertEqual(len(rows), 51)                                         # one fact line per paragraph
        marked = [rows[0]] + [r.replace("\t\t", "\tcorrect\t", 1) if False else r for r in rows[1:]]
        lines = [r.split("\t") for r in marked]
        for i, l in enumerate(lines[1:], 1):
            l[11] = "correct" if i <= 30 else ("wrong" if i <= 40 else "")
        card.write_text("\n".join("\t".join(l) for l in lines) + "\n")
        s = bench.stats(out)
        self.assertEqual((s["marked"], s["unmarked"], s["marks"]["correct"], s["precision"]), (40, 10, 30, 0.75))
        self.assertIn("Precision (correct / marked): 0.75", bench.summary(out).read_text())
        lines[1][11] = "maybe"
        card.write_text("\n".join("\t".join(l) for l in lines) + "\n")
        with self.assertRaisesRegex(bench.BenchError, "marks must be"):
            bench.stats(out)

    def test_compare_needs_the_same_sample_and_prompt(self):
        self.make()
        a, _ = self.run_bench()
        b = self.d / "other"
        b.mkdir()
        for f in ("outputs.jsonl", "run.json"):
            (b / f).write_text((a / f).read_text())
        self.assertIn("| precision |", bench.compare(a, b))
        meta = json.loads((b / "run.json").read_text())
        meta["prompt_sha256"] = "sha256:other"
        (b / "run.json").write_text(json.dumps(meta))
        with self.assertRaisesRegex(bench.BenchError, "not a direct comparison"):
            bench.compare(a, b)


class TestNeverTheCorpus(unittest.TestCase):
    def test_the_bench_doesnt_touch_the_corpus(self):
        src = Path(bench.__file__).read_text()
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        for word in ("CorpusGuard", "corpus_guard", "integrate(", "acquire", ".writing("):
            self.assertNotIn(word, code)
        self.assertNotIn('"corpus.pf"', code)


if __name__ == "__main__":
    unittest.main()
