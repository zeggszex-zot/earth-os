"""Corpus integrity: an answer's corpus_version names what was actually read, and a corpus that doesn't match the
hash recorded at build is said to, in every answer (review of Milestone 1, item 3)."""

import gzip
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import battery_corpus                                         # noqa: E402
from computer import corpus as corpus_mod                          # noqa: E402
from computer.config import Config                                 # noqa: E402
from computer.corpus import Corpus, content_hash                   # noqa: E402
from computer.core import Computer                                    # noqa: E402

Q = "Does BATTERY_ALPHA meet 50 Wh?"


def built(name: str) -> Path:
    """The battery fixture with its content hash recorded, as tools/build_shards.py records it."""
    d = battery_corpus.build(Path(tempfile.mkdtemp()) / name)
    meta = json.loads((d / "meta.json").read_text())
    meta["content_sha256"] = content_hash(d)
    (d / "meta.json").write_text(json.dumps(meta))
    return d


def shard_with(d: Path, text: str) -> Path:
    return next(p for p in sorted(d.glob("s/*.bin")) if text in gzip.decompress(p.read_bytes()).decode())


def edit(path: Path, old: str, new: str) -> None:
    """Change a shard in place, as someone editing data/ would, and make sure its mtime moves."""
    st = path.stat()
    path.write_bytes(gzip.compress(gzip.decompress(path.read_bytes()).decode().replace(old, new).encode()))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def snapshot(d: Path) -> dict:
    return {str(p.relative_to(d)): (p.read_bytes(), p.stat().st_mtime_ns) for p in d.rglob("*") if p.is_file()}


def ask(d: Path, q: str = Q, corpus=None, **kw):
    cfg = Config(corpus=str(d), llm_mode="off", llm_endpoint="", log_file="", navigator="always", **kw)
    computer = Computer(cfg, corpus) if corpus is not None else Computer(cfg)
    return computer, computer.think(q)


class TestLoadTimeCheck(unittest.TestCase):
    def test_an_intact_build_is_verified(self):
        d = built("intact")
        _, r = ask(d)
        e = r.envelope
        self.assertEqual(e["corpus_integrity"]["status"], "verified")
        self.assertEqual(e["corpus_version"], "sha256:" + json.loads((d / "meta.json").read_text())["content_sha256"])
        self.assertNotIn("CORPUS_INTEGRITY", [x["code"] for x in e["errors"]])

    def test_a_shard_edited_without_updating_meta_is_caught(self):
        """The reviewer's scenario: build (hash A), edit a shard, leave meta.json alone, load, ask."""
        d = built("edited")
        stored = json.loads((d / "meta.json").read_text())["content_sha256"]
        edit(shard_with(d, "0.052"), "0.052", "0.048")
        computer, r = ask(d)
        e = r.envelope
        self.assertEqual(computer.corpus.integrity, "mismatch")
        self.assertEqual(e["corpus_integrity"]["status"], "mismatch")
        self.assertNotEqual(e["corpus_version"], f"sha256:{stored}", "must not cite the hash of contents it didn't read")
        self.assertEqual(e["corpus_version"], f"sha256:{content_hash(d)}")
        self.assertEqual(e["corpus_integrity"]["stored"], stored)
        errs = [x for x in e["errors"] if x["code"] == "CORPUS_INTEGRITY"]
        self.assertEqual(len(errs), 1)
        self.assertFalse(errs[0]["recoverable"])
        self.assertEqual(errs[0]["capability"], "corpus.lookup@1.2.0")
        self.assertTrue(all(ev["corpus_version"] == e["corpus_version"] for ev in e["evidence"]))
        self.assertIn("48 watt-hours", r.text)            # the answer is from what was read, and says so

    def test_meta_json_edited_is_caught_too(self):
        d = built("meta")
        meta = json.loads((d / "meta.json").read_text())
        meta["facts"] += 1
        (d / "meta.json").write_text(json.dumps(meta))
        self.assertEqual(Corpus(d).load().integrity, "mismatch")

    def test_no_recorded_hash_is_computed_not_verified(self):
        d = battery_corpus.build(Path(tempfile.mkdtemp()) / "plain")
        _, r = ask(d)
        self.assertEqual(r.envelope["corpus_integrity"]["status"], "computed")
        self.assertEqual(r.envelope["corpus_version"], f"sha256:{content_hash(d)}")

    def test_nothing_is_written_to_the_corpus(self):
        d = built("readonly")
        edit(shard_with(d, "0.052"), "0.052", "0.048")
        before = snapshot(d)
        for mode in ("verify", "strict", "trust"):
            computer, _ = ask(d, corpus_integrity=mode)
            computer.think("Which of ALPHA and BETA meets 50 Wh?")
        self.assertEqual(snapshot(d), before)


class TestChangedWhileRunning(unittest.TestCase):
    def test_a_shard_changed_after_the_check_is_flagged(self):
        d = built("running")
        c = Corpus(d).load()
        self.assertEqual(c.integrity, "verified")
        edit(shard_with(d, "RATED_CHARGE"), "3.5", "4.5")          # not read yet: it's only hashed at load
        computer, r = ask(d, "Does BATTERY_BETA meet 50 Wh?", corpus=c)
        self.assertEqual(c.integrity, "changed")
        self.assertEqual(r.envelope["corpus_integrity"]["status"], "changed")
        self.assertTrue(r.envelope["corpus_integrity"]["changed_files"])
        self.assertIn("CORPUS_INTEGRITY", [x["code"] for x in r.envelope["errors"]])

    def test_reading_unchanged_shards_keeps_it_verified(self):
        d = built("steady")
        c = Corpus(d).load()
        for q in ("Does BATTERY_BETA meet 50 Wh?", Q, "Does BATTERY_EPSILON meet 50 Wh?"):
            ask(d, q, corpus=c)
        self.assertEqual(c.integrity, "verified")


class TestModes(unittest.TestCase):
    def test_strict_refuses_a_failed_corpus(self):
        d = built("strict")
        edit(shard_with(d, "0.052"), "0.052", "0.048")
        _, r = ask(d, corpus_integrity="strict")
        e = r.envelope
        self.assertEqual((e["status"], e["truth"], e["certainty"], e["evidence"]), ("unavailable", None, "UNKNOWN", []))
        self.assertEqual([x["code"] for x in e["errors"]], ["CORPUS_INTEGRITY"])
        self.assertNotIn("48", r.text)
        _, r = ask(d, "what is 12 times 7", corpus_integrity="strict")   # no corpus needed: still answered
        self.assertIn("84", r.text)

    def test_strict_answers_from_an_intact_corpus(self):
        _, r = ask(built("strict_ok"), corpus_integrity="strict")
        self.assertEqual((r.envelope["truth"], r.envelope["corpus_integrity"]["status"]), ("TRUE", "verified"))

    def test_trust_skips_the_check_and_says_so(self):
        d = built("trust")
        with mock.patch.object(corpus_mod, "content_hash", side_effect=AssertionError("trust must not hash")):
            computer, r = ask(d, corpus_integrity="trust")
        e = r.envelope
        self.assertEqual(e["corpus_integrity"]["status"], "trusted")
        self.assertNotEqual(e["corpus_integrity"]["status"], "verified")
        self.assertEqual(e["corpus_integrity"]["actual"], "")
        self.assertEqual(e["corpus_version"], "sha256:" + json.loads((d / "meta.json").read_text())["content_sha256"])

    def test_trust_without_a_recorded_hash_still_computes(self):
        d = battery_corpus.build(Path(tempfile.mkdtemp()) / "trust_plain")
        self.assertEqual(Corpus(d, integrity="trust").load().integrity, "computed")

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            Corpus("/nowhere", integrity="hope")


if __name__ == "__main__":
    unittest.main()
