"""The corpus guard (computer/corpus_guard.py): one writer, a backup before every change, and nothing but a verified
os.replace ever changes corpus.pf.  Every test works on a corpus.pf written into a temporary folder; the real corpus is
only ever copied, and only by the opt-in test at the end (CDOS_CORPUS_PF=/path/to/corpus.pf)."""

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_shards                                                                          # noqa: E402
from computer import corpus_guard                                                            # noqa: E402
from computer.corpus_guard import CorpusGuard, GuardBusy, GuardRefused, VerifyFailed          # noqa: E402
from computer.governor import sha256_file                                                    # noqa: E402

HEADER = "# test corpus\n# subject\trelation\tkind\tvalue\tunit\tvalid_from\tvalid_until\tsource_type\tsource_id\tconfidence\tqualifiers\n"


def pf_text(*extra):
    rows = [("LION", "IS_A", "MAMMAL"), ("TIGER", "IS_A", "MAMMAL"), ("MAMMAL", "IS_A", "ANIMAL"), *extra]
    return HEADER + "".join(f"{s}\t{r}\tS\t{v}\t\t0\t0\tDOCUMENT\tbio\t0.95\t\n" for s, r, v in rows)


ORIGINAL = pf_text().encode()
NEW = pf_text(("ZEBRA", "IS_A", "MAMMAL")).encode()
PLENTY = lambda p: 10 ** 12                                                                  # noqa: E731


def ok_verify(candidate, baseline):
    return {"ok": True, "version": "sha256:test", "facts": 4, "review": {"A1": "pass"}}


SCRIPT = """
import os, sys, time
sys.path.insert(0, {root!r})
from computer.corpus_guard import CorpusGuard
g = CorpusGuard({pf!r}, free_bytes=lambda p: 10 ** 12)
{body}
"""


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.pf = self.dir / "corpus.pf"
        self.pf.write_bytes(ORIGINAL)
        self.guard = CorpusGuard(self.pf, keep=3, free_bytes=PLENTY)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            p.stdout.close()
        self.tmp.cleanup()

    def init(self):
        self.guard.init("tester", "set up for a test")

    def other_process(self, body: str, ready: str | None = None) -> subprocess.Popen:
        """Run `body` in another Python process with `g` bound to a guard on the same corpus."""
        code = SCRIPT.format(root=str(ROOT), pf=str(self.pf), body=body)
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True)
        self.procs.append(p)
        if ready:
            line = p.stdout.readline().strip()
            self.assertEqual(line, ready, "the other process didn't get going")
        return p

    def replace(self, content=NEW, verify=ok_verify):
        with self.guard.writing("tester", "a test change") as w:
            with w.new() as fh:
                fh.write(content)
            return w.commit(verify)

    def events(self):
        return [r["event"] for r in self.guard.journal.records()]

    def temp_files(self):
        return sorted(p.name for p in self.dir.glob(".corpus.pf.new-*"))


class TestInit(GuardCase):
    def test_init_records_the_master_and_only_reads_it(self):
        before = self.pf.stat().st_mtime_ns
        m = self.guard.init("tester", "first")
        self.assertEqual(self.pf.read_bytes(), ORIGINAL)
        self.assertEqual(self.pf.stat().st_mtime_ns, before)
        self.assertEqual((m["generation"], m["sha256"], m["size"]), (1, sha256_file(self.pf), len(ORIGINAL)))
        self.assertEqual(len(m["backups"]), 1)
        self.assertEqual((self.dir / "backups" / m["backups"][0]["id"]).read_bytes(), ORIGINAL)
        self.assertTrue(self.guard.guarded)
        self.assertTrue(self.guard.status()["ok"], self.guard.status()["problems"])
        self.assertEqual(self.events(), ["init", "backup"])

    def test_init_twice_is_refused(self):
        self.init()
        with self.assertRaises(GuardRefused):
            self.guard.init("tester", "again")

    def test_an_unguarded_corpus_is_not_written(self):
        with self.assertRaisesRegex(GuardRefused, "not guarded"):
            with self.guard.writing("tester", "x"):
                pass
        self.assertFalse(self.guard.guarded)

    def test_a_write_says_who_and_why(self):
        self.init()
        with self.assertRaisesRegex(GuardRefused, "`by`"):
            with self.guard.writing("", "x"):
                pass


class TestReplace(GuardCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_a_verified_replace(self):
        out = self.replace()
        self.assertTrue(out["changed"])
        self.assertEqual(self.pf.read_bytes(), NEW)
        m = self.guard._read_manifest()
        self.assertEqual((m["generation"], m["sha256"], m["pending"]), (2, sha256_file(self.pf), None))
        self.assertEqual(m["verified"]["sha256"], m["sha256"])
        self.assertEqual(m["verified"]["review"], {"A1": "pass"})
        self.assertIn(ORIGINAL, [(self.dir / "backups" / b["id"]).read_bytes() for b in m["backups"]])
        self.assertEqual(self.events()[-1], "replaced")
        self.assertEqual(self.temp_files(), [])
        self.assertTrue(self.guard.status()["ok"])

    def test_a_failed_verify_leaves_the_master_byte_identical(self):
        bad = lambda c, b: {"ok": False, "why": "the validator found problems"}                # noqa: E731
        with self.assertRaises(VerifyFailed):
            self.replace(verify=bad)
        self.assertEqual(self.pf.read_bytes(), ORIGINAL)
        self.assertEqual(self.guard._read_manifest()["generation"], 1)
        self.assertEqual(self.temp_files(), [])
        self.assertIn("rejected", self.events())

    def test_an_error_inside_the_block_discards_the_candidate(self):
        with self.assertRaises(ValueError):
            with self.guard.writing("tester", "x") as w:
                with w.new() as fh:
                    fh.write(NEW)
                raise ValueError("the importer broke")
        self.assertEqual(self.pf.read_bytes(), ORIGINAL)
        self.assertEqual(self.temp_files(), [])
        self.assertEqual(self.events()[-1], "discarded")

    def test_no_space_is_refused_before_anything_is_written(self):
        tight = CorpusGuard(self.pf, free_bytes=lambda p: 10)
        backups = sorted(os.listdir(self.dir / "backups"))
        with self.assertRaisesRegex(GuardRefused, "not enough free space"):
            with tight.writing("tester", "x"):
                pass
        self.assertEqual(self.pf.read_bytes(), ORIGINAL)
        self.assertEqual(sorted(os.listdir(self.dir / "backups")), backups)
        self.assertEqual(self.temp_files(), [])

    def test_the_same_content_is_not_a_change(self):
        out = self.replace(content=ORIGINAL)
        self.assertFalse(out["changed"])
        self.assertEqual(self.guard._read_manifest()["generation"], 1)

    def test_without_fcntl_nothing_is_written(self):
        with mock.patch.object(corpus_guard, "fcntl", None):
            with self.assertRaisesRegex(GuardRefused, "no fcntl"):
                with self.guard.writing("tester", "x"):
                    pass
            with self.assertRaisesRegex(GuardRefused, "no fcntl"):
                with self.guard.reading():
                    pass
        self.assertEqual(self.pf.read_bytes(), ORIGINAL)


class TestLocks(GuardCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_a_second_writer_is_refused_at_once(self):
        self.other_process('with g.writing("other", "holding the lock"):\n'
                           '    print("held", flush=True); time.sleep(60)', ready="held")
        t = time.monotonic()
        with self.assertRaisesRegex(GuardBusy, '"by": "other"'):
            with self.guard.writing("tester", "x"):
                pass
        self.assertLess(time.monotonic() - t, 1.0, "the second writer waited instead of being refused")
        self.assertEqual(self.pf.read_bytes(), ORIGINAL)

    def test_a_reader_keeps_the_writer_out(self):
        self.other_process('with g.reading():\n    print("held", flush=True); time.sleep(60)', ready="held")
        with self.assertRaisesRegex(GuardBusy, "reader"):
            with self.guard.writing("tester", "x"):
                pass
        with self.guard.reading() as path:                       # readers share
            self.assertEqual(path, self.pf.resolve())

    def test_a_writer_keeps_readers_out(self):
        self.other_process('with g.writing("other", "holding"):\n    print("held", flush=True); time.sleep(60)',
                           ready="held")
        with self.assertRaises(GuardBusy):
            with self.guard.reading():
                pass

    def test_the_lock_is_free_again_after_the_writer(self):
        self.replace()
        with self.guard.writing("tester", "again"):
            pass
        self.assertEqual(self.guard.lock_path.read_text(), "")


class TestInterruptions(GuardCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_a_writer_killed_mid_write_leaves_the_master_untouched(self):
        p = self.other_process('with g.writing("other", "killed") as w:\n'
                               '    with w.new() as fh:\n'
                               '        fh.write(b"half a corpus"); fh.flush()\n'
                               '        print("writing", flush=True); time.sleep(60)', ready="writing")
        p.kill()
        p.wait()
        self.assertEqual(self.pf.read_bytes(), ORIGINAL)
        self.assertEqual(len(self.temp_files()), 1)
        self.assertFalse(self.guard.status()["ok"])
        with self.guard.writing("tester", "after the kill"):
            pass
        self.assertEqual(self.temp_files(), [])
        self.assertIn("removed", self.events())
        self.assertTrue(self.guard.status()["ok"], self.guard.status()["problems"])

    def crash_at(self, stage):
        p = self.other_process(f'g._hook = lambda s: os._exit(9) if s == {stage!r} else None\n'
                               'with g.writing("other", "crashing") as w:\n'
                               '    with w.new() as fh:\n'
                               f'        fh.write({NEW!r})\n'
                               '    w.commit()')
        self.assertEqual(p.wait(timeout=60), 9)

    def test_a_crash_before_the_rename_is_dropped(self):
        self.crash_at("after_pending")
        self.assertEqual(self.pf.read_bytes(), ORIGINAL)
        self.assertIsNotNone(self.guard._read_manifest()["pending"])
        with self.guard.writing("tester", "next"):
            pass
        m = self.guard._read_manifest()
        self.assertEqual((m["pending"], m["generation"], m["sha256"]), (None, 1, sha256_file(self.pf)))
        self.assertIn("recovered", self.events())

    def test_a_crash_after_the_rename_is_completed(self):
        self.crash_at("after_replace")
        self.assertEqual(self.pf.read_bytes(), NEW)
        self.assertFalse(self.guard.status()["ok"])               # master != manifest, with a pending record
        with self.guard.writing("tester", "next"):
            pass
        m = self.guard._read_manifest()
        self.assertEqual((m["pending"], m["generation"], m["sha256"]), (None, 2, sha256_file(self.pf)))
        self.assertTrue(self.guard.status()["ok"], self.guard.status()["problems"])


class TestBackups(GuardCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_restore_brings_back_the_exact_bytes(self):
        first = self.guard._read_manifest()["backups"][0]["id"]
        self.replace()
        self.guard.restore(first, "tester", "undo the test change")
        self.assertEqual(self.pf.read_bytes(), ORIGINAL)
        m = self.guard._read_manifest()
        self.assertEqual(m["generation"], 3)
        self.assertIn(NEW, [(self.dir / "backups" / b["id"]).read_bytes() for b in m["backups"]])
        self.assertEqual(self.events()[-1], "restored")

    def test_a_damaged_backup_is_not_restored(self):
        first = self.guard._read_manifest()["backups"][0]["id"]
        self.replace()
        with open(self.dir / "backups" / first, "ab") as fh:
            fh.write(b"x")
        with self.assertRaisesRegex(GuardRefused, "doesn't match its recorded sha256"):
            self.guard.restore(first, "tester", "x")
        self.assertEqual(self.pf.read_bytes(), NEW)
        self.assertIn("backup", " ".join(self.guard.status()["problems"]))

    def test_prune_keeps_the_newest_and_the_last_verified(self):
        self.replace(pf_text(("V", "IS_A", "VERIFIED")).encode())         # generation 2: verified
        verified_sha = sha256_file(self.pf)
        for i in range(5):
            self.replace(pf_text(("N", "IS_A", f"N{i}")).encode(), verify=None)
        m = self.guard._read_manifest()
        shas = [b["sha256"] for b in m["backups"]]
        self.assertIn(verified_sha, shas)
        self.assertEqual(len(m["backups"]), 3 + 1)
        self.assertEqual(sorted(os.listdir(self.dir / "backups")), sorted(b["id"] for b in m["backups"]))
        self.assertIn("pruned", self.events())


class TestOutsideChanges(GuardCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_an_outside_edit_is_refused_until_a_person_adopts_it(self):
        self.pf.write_bytes(NEW)
        with self.assertRaisesRegex(GuardRefused, "changed outside the guard"):
            with self.guard.writing("tester", "x"):
                pass
        self.assertFalse(self.guard.status()["ok"])
        m = self.guard.adopt("tester", "checked the edit by hand")
        self.assertEqual((m["generation"], m["sha256"]), (2, sha256_file(self.pf)))
        self.assertIn("adopted", self.events())
        self.replace(pf_text(("AFTER", "IS_A", "ADOPT")).encode())
        self.assertTrue(self.guard.status()["ok"])

    def test_a_damaged_manifest_is_refused_and_left_alone(self):
        m = json.loads(self.guard.manifest_path.read_text())
        m["size"] += 1
        self.guard.manifest_path.write_text(json.dumps(m))
        before = self.guard.manifest_path.read_bytes()
        with self.assertRaisesRegex(GuardRefused, "manifest is damaged"):
            with self.guard.writing("tester", "x"):
                pass
        self.assertEqual(self.guard.manifest_path.read_bytes(), before)
        self.assertEqual(self.pf.read_bytes(), ORIGINAL)

    def test_a_missing_manifest_is_refused(self):
        self.guard.manifest_path.unlink()
        with self.assertRaisesRegex(GuardRefused, "no manifest"):
            with self.guard.writing("tester", "x"):
                pass

    def test_an_edited_journal_stops_writes(self):
        path = self.guard.journal.path
        lines = path.read_text().splitlines()
        lines[0] = lines[0].replace('"tester"', '"someone else"')
        path.write_text("\n".join(lines) + "\n")
        with self.assertRaisesRegex(GuardRefused, "journal doesn't verify"):
            with self.guard.writing("tester", "x"):
                pass
        self.assertEqual(self.pf.read_bytes(), ORIGINAL)


class TestBuildUnderTheLock(GuardCase):
    def test_an_unguarded_corpus_builds_as_before(self):
        meta, _ = build_shards.build(self.pf, self.dir / "data", 4, 4, log=lambda *a: None)
        self.assertEqual(meta["facts"], 3)
        self.assertFalse(self.guard.lock_path.exists())

    def test_a_guarded_corpus_builds_and_a_writer_stops_the_build(self):
        self.init()
        meta, _ = build_shards.build(self.pf, self.dir / "data", 4, 4, log=lambda *a: None)
        self.assertEqual(meta["facts"], 3)
        self.other_process('with g.writing("other", "holding"):\n    print("held", flush=True); time.sleep(60)',
                           ready="held")
        with self.assertRaises(GuardBusy):
            build_shards.build(self.pf, self.dir / "data2", 4, 4, log=lambda *a: None)
        self.assertFalse((self.dir / "data2").exists())


class TestIsolation(unittest.TestCase):
    def test_the_guard_needs_only_the_standard_library_and_the_governor(self):
        tree = ast.parse((ROOT / "computer" / "corpus_guard.py").read_text())
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        allowed = {"__future__", "contextlib", "json", "os", "shutil", "time", "pathlib", "fcntl", "computer.governor"}
        self.assertLessEqual(mods, allowed)


REAL = os.environ.get("CDOS_CORPUS_PF", "")


@unittest.skipUnless(REAL and Path(REAL).is_file(), "set CDOS_CORPUS_PF to a real corpus.pf (it is only copied)")
class TestOnACopyOfTheRealCorpus(unittest.TestCase):
    def test_init_and_verify_on_a_copy(self):
        import corpus_guard as cli
        real = Path(REAL)
        sha, mtime = sha256_file(real), real.stat().st_mtime_ns
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "corpus.pf"
            shutil.copyfile(real, copy)
            g = CorpusGuard(copy)
            g.init("tester", "a copy of the real corpus")
            result = cli.verify(copy, None)
            self.assertTrue(result["ok"], result.get("why"))
            m = g.record_verified(result, "tester", "baseline on the copy")
            self.assertEqual(m["verified"]["version"], result["version"])
            self.assertTrue(g.status()["ok"], g.status()["problems"])
        self.assertEqual((sha256_file(real), real.stat().st_mtime_ns), (sha, mtime))


if __name__ == "__main__":
    unittest.main()
