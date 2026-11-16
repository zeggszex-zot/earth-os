"""Books -> corpus facts (tools/books.py), with a fake model behind a fake Ollama: which paragraphs are asked, the
fixed options, never a pull, resuming after a stop, staging (well-formed, not already known, with its source), the
review keys and their memory, the export as an approvable source, and integration through the guard with verify.
Every test works in a temporary folder on a temporary master; the real corpus is never touched."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "bench" / "book_facts"))

import bench                                                                                 # noqa: E402
import books                                                                                 # noqa: E402
import integrate                                                                             # noqa: E402
from computer.acquire import registry                                                        # noqa: E402
from computer.acquire.pipeline import Acquirer                                               # noqa: E402
from computer.corpus_guard import CorpusGuard, VerifyFailed                                  # noqa: E402

T = "\t"
TEX_SHA = {"calculus": "a" * 64, "gibbs": "b" * 64}


def block(i, text, typ="text", section="b-00001"):
    return {"id": f"b-{i:05d}", "type": typ, "section": section, "lines": [10 * i, 10 * i + 2], "pages": [str(i)],
            "text": text, "inline_math": []}


def edie(name):
    blocks = [block(0, "CALCULUS MADE EASY", section=None),                         # title page
              block(1, "Water boils at 100 degrees. It is a compound of hydrogen and oxygen."),
              block(2, "\\frac{dy}{dx}", typ="math"),                                # a maths block
              block(3, "Therefore,"),                                                # a fragment
              block(4, "Try $y = x^5$ and $y = x^6$."),                              # maths with two words
              block(5, "A sovereign is worth twenty shillings. A farthing is a small coin."),
              block(6, "The ensemble is canonical when the index is linear in the energy.")]
    return {"source": {"filename": f"{name}.tex", "sha256": TEX_SHA[name]},
            "metadata": {"title": {"value": name.title()}, "author": {"value": "An Author"}}, "blocks": blocks}


ANSWERS = {   # first words of a paragraph -> what the fake model says
    "Water": "water | BOILING_POINT | N | 100 | C\nwater | IS_A | S | compound |\nwater | IS_A | S | compound |\n"
             "water | contains | S | hydrogen |\nwater | BOILING_POINT | N | hot | C",
    "A sovereign": "sovereign | WORTH | N | 20 | shilling\nfarthing | IS_A | S | small coin |\nLion | IS_A | S | mammal |",
    "The ensemble": "NONE",
}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeOllama:
    """Answers by the paragraph's first words; records every request; knows only /api/version, /api/tags and
    /api/generate, so a pull (or anything else) fails the test."""

    def __init__(self, models=("llama3.1:8b",), digest="d8b", stop_after=None, warm_drift=False):
        self.models, self.digest, self.asked, self.stop_after = models, digest, [], stop_after
        self.warm_drift = warm_drift          # kept loaded, the second paragraph comes out differently

    def __call__(self, req):
        assert req.full_url.startswith("http://127.0.0.1:11434/"), req.full_url
        path = req.full_url.split("11434", 1)[1]
        body = json.loads(req.data) if req.data else None
        self.asked.append((path, body))
        if path == "/api/version":
            return Resp(b'{"version": "0.35.0"}')
        if path == "/api/tags":
            return Resp(json.dumps({"models": [{"name": m, "digest": self.digest} for m in self.models]}).encode())
        if path == "/api/generate":
            if self.stop_after is not None and len(self.generated()) > self.stop_after:
                raise KeyboardInterrupt
            para = body["prompt"].rsplit("Paragraph: ", 1)[1]
            out = next((v for k, v in ANSWERS.items() if para.startswith(k)), "NONE")
            if self.warm_drift and body["keep_alive"] != 0 and len(self.generated()) == 2:
                out = "water | IS_A | S | liquid |"
            return Resp(json.dumps({"response": out, "done_reason": "stop", "eval_count": 30,
                                    "eval_duration": 3_000_000_000, "prompt_eval_count": 300,
                                    "prompt_eval_duration": 10_000_000_000, "load_duration": 1_000_000_000}).encode())
        raise AssertionError(f"unexpected endpoint {path}")

    def generated(self):
        return [b for p, b in self.asked if p == "/api/generate"]


def row(s, r, v, st, sid, kind="S"):
    return T.join([s, r, kind, v, "", "0", "0", st, sid, "1.0", ""]) + "\n"


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.books = {}
        for name in ("calculus", "gibbs"):
            p = self.d / "edie" / name / f"{name}.edie.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(edie(name)))
            self.books[name] = p
        self.master = self.d / "corpus" / "corpus.pf"
        self.master.parent.mkdir()
        # LION IS_A MAMMAL is known (another source); FARTHING IS_A SMALL_COIN is an earlier books generation's row
        self.master.write_text(row("Lion", "IS_A", "MAMMAL", "MANUAL", "bio") +
                               row("farthing", "IS_A", "small coin", "DOCUMENT", "book/x@y/b-00005/L50-52"))
        self.root = self.d / "books"
        self.out = io.StringIO()

    def tearDown(self):
        self.tmp.cleanup()

    def extract(self, fake=None, model="llama3.1:8b", **kw):
        self.fake = fake or FakeOllama()
        return books.extract(model, bench.Ollama(opener=self.fake), self.root, self.books, log=lambda *a: None, **kw)

    @property
    def rundir(self):
        return books.run_dir(self.root, "llama3.1:8b")

    def stage(self):
        return books.stage(self.rundir, self.master, log=lambda *a: None)

    def review(self, keys):
        return books.review(self.rundir, io.StringIO("".join(k + "\n" for k in keys)), self.out)


class TestParagraphs(Case):
    def test_prose_paragraphs_only(self):
        paras, skipped = books.paragraphs(self.books)
        self.assertEqual([p["block"] for p in paras if p["book"] == "calculus"], ["b-00001", "b-00005", "b-00006"])
        self.assertEqual(len(paras), 6)
        why = skipped["calculus"]
        self.assertEqual(why["math block"], 1)
        self.assertEqual(why["text before the first section (title page)"], 1)
        self.assertEqual(why[f"maths or a fragment (under {books.MIN_WORDS} words of prose)"], 2)
        p = paras[0]
        self.assertEqual((p["book_sha256"], p["lines"]), ("sha256:" + "a" * 64, [10, 12]))

    def test_prose_words_leave_maths_out(self):
        self.assertEqual(books.prose_words("Try $y = x^5$ and $\\frac{a}{b}$."), ["Try", "and"])


class TestExtract(Case):
    def test_every_paragraph_asked_cold_at_temperature_0_with_a_fixed_seed(self):
        res = self.extract()
        self.assertEqual((res["asked"], res["done"], res["paragraphs"]), (6, 6, 6))
        gens = self.fake.generated()
        self.assertEqual(len(gens), 6)
        for g in gens:
            self.assertEqual(g["model"], "llama3.1:8b")
            self.assertEqual(g["options"]["temperature"], 0)
            self.assertEqual(g["options"]["seed"], 42)
            self.assertEqual((g["keep_alive"], g["stream"]), (0, False))
        self.assertEqual({p for p, _ in self.fake.asked}, {"/api/version", "/api/tags", "/api/generate"})
        outs = books.read_lines(self.rundir / "outputs.jsonl")
        self.assertEqual(outs[0]["book_sha256"], "sha256:" + "a" * 64)
        meta = json.loads((self.rundir / "run.json").read_text())
        self.assertEqual((meta["model_digest"], meta["prompt_sha256"]),
                         ("d8b", books.sha256_bytes(bench.PROMPT.read_bytes())))

    def test_a_model_that_isnt_installed_is_refused_not_pulled(self):
        fake = FakeOllama(models=("llama3.2:1b",))
        with self.assertRaisesRegex(bench.BenchError, "isn't installed"):
            self.extract(fake)
        self.assertFalse(fake.generated())

    def test_resumes_where_it_stopped(self):
        self.extract(limit=2)
        self.extract()
        self.assertEqual(len(self.fake.generated()), 4)                     # only the four not yet asked
        blocks = [(o["book"], o["block"]) for o in books.read_lines(self.rundir / "outputs.jsonl")]
        self.assertEqual(len(blocks), len(set(blocks)), 6)

    def test_a_stop_mid_run_keeps_every_answer_so_far(self):
        with self.assertRaises(KeyboardInterrupt):
            self.extract(FakeOllama(stop_after=3))
        self.assertEqual(len(books.read_lines(self.rundir / "outputs.jsonl")), 3)
        self.extract()
        self.assertEqual(len(self.fake.generated()), 3)

    def test_a_line_cut_short_by_a_stop_is_redone(self):
        self.extract(limit=2)
        with open(self.rundir / "outputs.jsonl", "a") as fh:
            fh.write('{"book": "calculus", "blo')
        self.extract()
        self.assertEqual(len(books.read_lines(self.rundir / "outputs.jsonl")), 6)

    def test_resuming_with_another_model_digest_is_refused(self):
        self.extract(limit=1)
        with self.assertRaisesRegex(books.BooksError, "model_digest"):
            self.extract(FakeOllama(digest="other"))


class TestStage(Case):
    def setUp(self):
        super().setUp()
        self.extract()
        self.before = self.master.read_bytes()
        self.report = self.stage()
        self.staged = books.load_staged(self.rundir)

    def test_well_formed_new_facts_with_their_source(self):
        c = self.report["counts"]
        self.assertTrue(self.report["reconciled"])
        self.assertEqual(c["malformed"], 2 * 2)             # per book: a lower-case relation, an N that isn't a number
        self.assertEqual(c["in_corpus"], 2)                 # Lion IS_A mammal, in each book
        self.assertEqual(c["paragraphs_without_facts"], 2)  # NONE
        facts = [(s["book"], s["subject"], s["relation"], s["value"]) for s in self.staged]
        self.assertIn(("calculus", "water", "BOILING_POINT", "100"), facts)
        self.assertEqual(sum(f[1:] == ("water", "IS_A", "compound") for f in facts), 1)   # once, not three times
        self.assertNotIn("gibbs", {s["book"] for s in self.staged if s["subject"] == "water"})
        s = next(s for s in self.staged if s["subject"] == "sovereign")
        self.assertEqual((s["book_sha256"], s["block"], s["lines"]), ("sha256:" + "a" * 64, "b-00005", [50, 52]))
        self.assertEqual(s["row"][7:10], ["DOCUMENT", "book/calculus@aaaaaaaaaaaa/b-00005/L50-52", "0.9"])
        self.assertEqual(s["sentence"], "A sovereign is worth twenty shillings.")
        self.assertTrue(any("relation isn't UPPER_CASE" in r["why"]
                            for r in books.read_lines(self.rundir / "rejects.jsonl")))

    def test_the_books_own_earlier_generation_isnt_a_duplicate(self):
        self.assertIn("farthing", {s["subject"] for s in self.staged})       # replaced on integration, so kept

    def test_corpus_pf_is_only_read(self):
        self.assertEqual(self.master.read_bytes(), self.before)

    def test_restaging_keeps_ids(self):
        self.stage()
        self.assertEqual([s["id"] for s in books.load_staged(self.rundir)], [s["id"] for s in self.staged])


class TestReview(Case):
    def setUp(self):
        super().setUp()
        self.extract()
        self.stage()
        self.staged = books.load_staged(self.rundir)

    def test_one_key_each_and_the_place_is_remembered(self):
        res = self.review(["a", "r", "q"])
        self.assertEqual((res["accept"], res["reject"]), (1, 1))
        self.assertIn("fact:      water | BOILING_POINT | N | 100 | C", self.out.getvalue())
        self.assertIn("sentence:  Water boils at 100 degrees.", self.out.getvalue())
        self.out = io.StringIO()
        self.review(["q"])
        self.assertIn(f"fact 3 of {len(self.staged)}", self.out.getvalue())      # carries on at the third

    def test_skipped_facts_come_back_last_and_undo_takes_a_decision_back(self):
        self.review(["s", "a", "u", "q"])
        dec = books.decisions(self.rundir)
        self.assertEqual(dec, {self.staged[0]["id"]: "skip"})
        self.out = io.StringIO()
        self.review(["a"] * (len(self.staged) - 1) + ["r"])
        self.assertIn("(skipped before)", self.out.getvalue())
        dec = books.decisions(self.rundir)
        self.assertEqual(dec[self.staged[0]["id"]], "reject")
        self.assertIn("Every staged fact is decided", self.out.getvalue())

    def test_p_shows_the_whole_paragraph(self):
        self.review(["p", "q"])
        self.assertIn("paragraph: Water boils at 100 degrees. It is a compound", self.out.getvalue())

    def test_end_of_input_quits_keeping_the_place(self):
        res = self.review([])
        self.assertEqual(res["undecided"], len(self.staged))


class TestExportAndIntegrate(Case):
    """Accepted facts become the `books` source and go through computer.acquire and tools/integrate.py like the
    others: approved in sources.toml by sha256, staged against this master, integrated through the guard with verify."""

    def setUp(self):
        super().setUp()
        self.extract()
        self.stage()
        self.staged = books.load_staged(self.rundir)
        self.acq_root = self.d / "acquire"
        self.acq_root.mkdir()
        (self.acq_root / "settings.toml").write_text("[corpus]\nintegrate = true\n")
        CorpusGuard(self.master).init("test", "a test master")

    def approve(self, exp):
        entry = exp["entry"].replace('approved_by = ""', 'approved_by = "test"')
        (self.acq_root / "sources.toml").write_text(entry)

    def verifier(self, ok=True):
        def v(candidate, baseline):
            self.candidate = Path(candidate).read_text()
            return {"ok": ok, "why": "a review case regressed", "version": "v", "facts": 3, "review": {}}
        return v

    def integrate(self, ok=True):
        acq = Acquirer(self.acq_root, self.master)
        acq.run("books")
        return integrate.integrate(acq, ["books"], "test", "book facts", self.verifier(ok))

    def test_nothing_accepted_nothing_exported(self):
        with self.assertRaisesRegex(books.BooksError, "no accepted"):
            books.export(self.rundir)

    def test_the_entry_is_refused_until_a_person_approves_it(self):
        self.review(["a", "q"])
        exp = books.export(self.rundir, today="2026-11-16")
        (self.acq_root / "sources.toml").write_text(exp["entry"])
        with self.assertRaisesRegex(Exception, "approved_by"):
            registry.get(self.acq_root / "sources.toml", "books")
        self.approve(exp)
        src = registry.get(self.acq_root / "sources.toml", "books")
        self.assertEqual((src.source_type, src.sha256), ("DOCUMENT", exp["sha256"]))

    def test_accepted_facts_integrate_and_a_later_rejection_leaves_the_corpus(self):
        keys = ["a"] * len(self.staged)
        keys[1] = "r"                                                        # water IS_A compound: rejected
        self.review(keys)
        exp = books.export(self.rundir)
        self.assertEqual(exp["facts"], len(self.staged) - 1)
        self.assertTrue(Path(exp["file"]).name.startswith("books-" + exp["sha256"][7:15]))
        self.approve(exp)
        res = self.integrate()
        self.assertTrue(res["changed"])
        text = self.master.read_text()
        self.assertIn("water\tBOILING_POINT\tN\t100\tC\t0\t0\tDOCUMENT\tbook/calculus@", text)
        self.assertNotIn("water\tIS_A", text)
        self.assertIn("Lion\tIS_A\tS\tMAMMAL", text)                            # the rest of the corpus stays
        self.assertNotIn("book/x@y/", text)                                  # the earlier books generation is replaced
        self.assertEqual(text.count("farthing\tIS_A"), 1)
        # later: the person rejects the boiling point; the next export replaces the generation without it
        books.append_line(self.rundir / "decisions.jsonl", {"id": self.staged[0]["id"], "decision": "reject"})
        self.approve(books.export(self.rundir))
        self.integrate()
        self.assertNotIn("BOILING_POINT", self.master.read_text())
        self.assertIn("sovereign\tWORTH", self.master.read_text())

    def test_a_candidate_that_fails_verify_leaves_the_master_as_it_was(self):
        self.review(["a", "q"])
        self.approve(books.export(self.rundir))
        before = self.master.read_bytes()
        with self.assertRaises(VerifyFailed):
            self.integrate(ok=False)
        self.assertEqual(self.master.read_bytes(), before)


class TestTrialAndSettings(Case):
    def setUp(self):
        super().setUp()
        self.sample = self.d / "sample.jsonl"
        self.sample.write_text("".join(json.dumps({"n": i + 1, "book": "calculus", "block": f"b-{i:05d}",
                                                   "text": t}) + "\n" for i, t in enumerate(
            ["Water boils at 100 degrees.", "A sovereign is worth twenty shillings.", "The ensemble is canonical."])))
        self.results = self.d / "results"

    def trial(self, model="llama3.1:8b", warm=False, **fake):
        fake = FakeOllama(models=(model,), **fake)
        return books.trial(model, bench.Ollama(opener=fake, keep_alive=books.WARM if warm else 0), self.results,
                           log=lambda *a: None, sample=self.sample)

    def test_trial_is_the_bench_on_its_fixed_sample_with_a_scorecard_and_a_comparison(self):
        self.trial("llama3.2:1b")
        res = self.trial()
        out = Path(res["run"])
        self.assertTrue((out / "scorecard.tsv").exists())
        self.assertEqual(len(books.fact_lines(out)), 3)                     # the given sample, not the real one
        self.assertIn("# Book-facts bench: llama3.1:8b", res["summary"])
        self.assertEqual(len(res["compared"]), 1)
        self.assertIn("| llama3.2:1b | llama3.1:8b |", next(iter(res["compared"].values())))
        self.assertIsNone(res["warm_check"])

    def warm_extract(self):
        return books.extract("llama3.1:8b", bench.Ollama(opener=FakeOllama(), keep_alive=books.WARM), self.root, self.books,
            log=lambda *a: None, results=self.results)

    def test_a_kept_loaded_trial_needs_a_fresh_start_one_first(self):
        with self.assertRaisesRegex(books.BooksError, "fresh-start"):
            self.trial(warm=True)
        self.assertFalse(self.results.exists())                             # refused before any paragraph

    def test_identical_facts_let_the_full_run_keep_the_model_loaded(self):
        with self.assertRaisesRegex(books.BooksError, "--warm needs"):
            self.warm_extract()                                             # no kept-loaded trial yet
        self.trial()
        res = self.trial(warm=True)
        self.assertTrue(res["warm_check"]["identical"])
        self.assertEqual(json.loads((Path(res["run"]) / "run.json").read_text())["keep_alive"], books.WARM)
        self.warm_extract()
        meta = json.loads((self.rundir / "run.json").read_text())
        self.assertEqual(meta["keep_alive"], books.WARM)
        with self.assertRaisesRegex(books.BooksError, "keep_alive"):
            self.extract()                                                  # one run, one mode

    def test_a_single_different_fact_keeps_the_full_run_fresh_start(self):
        self.trial()
        res = self.trial(warm=True, warm_drift=True)
        self.assertEqual((res["warm_check"]["identical"], res["warm_check"]["differing"]), (False, [2]))
        with self.assertRaisesRegex(books.BooksError, "--warm needs"):
            self.warm_extract()

    def test_another_model_digest_needs_its_own_check(self):
        self.trial()
        self.trial(warm=True)
        with self.assertRaisesRegex(books.BooksError, "--warm needs"):
            books.extract("llama3.1:8b", bench.Ollama(opener=FakeOllama(digest="new"), keep_alive=books.WARM),
                          self.root, self.books, log=lambda *a: None, results=self.results)

    def test_the_model_is_a_setting_defaulting_to_llama3_1_8b(self):
        self.assertEqual(books.DEFAULT_MODEL, "llama3.1:8b")
        self.root.mkdir()
        self.assertEqual(books.settings(self.root), {})
        (self.root / "settings.toml").write_text('[books]\nmodel = "qwen:7b"\n')
        self.assertEqual(books.settings(self.root)["model"], "qwen:7b")
        (self.root / "settings.toml").write_text('[books]\nmodle = "qwen:7b"\n')
        with self.assertRaisesRegex(books.BooksError, "may hold only"):
            books.settings(self.root)


if __name__ == "__main__":
    unittest.main()
