"""Phase 2, computer/acquire/: registry, fetch (resume, checksum, limits), parse -> map -> normalise -> validate ->
dedupe -> conflicts -> staging.  A tiny synthetic source, served by a test HTTP server on 127.0.0.1; a tiny fixture
master.  No real downloads, and nothing writes the master (corpus.integrate is off, Q1)."""

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_shards                                                                          # noqa: E402
from computer import pfrow                                                                   # noqa: E402
from computer.acquire import AcquireError, registry                                          # noqa: E402
from computer.acquire.fetch import Bucket, FetchInterrupted, FetchRejected                   # noqa: E402
from computer.acquire.pipeline import Acquirer, same_value                                   # noqa: E402
from computer.corpus_guard import CorpusGuard                                                # noqa: E402

T = "\t"


def row(*c):
    c = list(c) + [""] * (11 - len(c))
    return T.join(c)


MASTER = "\n".join([
    "# fixture master", "# " + T.join(pfrow.COLUMNS),
    row("ATOMIC_MASS", "IS_FUNCTIONAL", "S", "TRUE", "", "0", "0", "MANUAL", "schema", "1.0"),
    row("CHEMICAL_SYMBOL", "IS_FUNCTIONAL", "S", "TRUE", "", "0", "0", "MANUAL", "schema", "1.0"),
    row("CAPITAL_CITY", "IS_FUNCTIONAL", "S", "TRUE", "", "0", "0", "MANUAL", "schema", "1.0"),
    row("HELIUM", "ATOMIC_MASS", "N", "4.0026", "", "0", "0", "TABLE", "ptable", "1.0"),
    row("HELIUM", "CHEMICAL_SYMBOL", "S", "He", "", "0", "0", "TABLE", "ptable", "1.0"),
    row("FRANCE", "CAPITAL_CITY", "S", "Paris", "", "0", "0", "TABLE", "geo", "1.0"),
    row("LION", "IS_A", "S", "MAMMAL", "", "0", "0", "DOCUMENT", "bio", "1.0"),
]) + "\n"

# The synthetic source, one planted case per line.  Its relation names are its own; sources.toml maps them.
SOURCE_LINES = [
    row("GERMANY", "capital", "S", "Berlin", "", "0", "0", "EX", "Q183/P36", "1.0"),           # staged
    row("GERMANY", "pop", "N", "83200000", "", "2023", "0", "EX", "Q183/P1082", "1.0"),        # staged (dated)
    row("Lion", "isa", "S", "Mammal", "", "0", "0", "EX", "Q140/P31", "1.0"),                  # duplicate: master
    row("GERMANY", "capital", "S", "Berlin", "", "0", "0", "EX", "Q183/P36", "1.0"),           # duplicate: this run
    row("HELIUM", "mass", "N", "4.002602", "", "0", "0", "EX", "Q560/P2067", "1.0"),          # staged, precision only
    row("HELIUM", "symbol", "S", "HE", "", "0", "0", "EX", "Q560/P246", "1.0"),                # duplicate: case only
    row("FRANCE", "capital", "S", "Lyon", "", "0", "0", "EX", "Q142/P36", "1.0"),              # staged + conflict
    row("GERMANY", "anthem", "S", "Das Lied", "", "0", "0", "EX", "Q183/P85", "1.0"),          # reject: map relation
    row("SPAIN", "capital", "S", "Madrid", "", "0", "0", "EX", "", "1.0"),                     # reject: map source-id
    row("ITALY", "capital", "S", "", "", "0", "0", "EX", "Q38/P36", "1.0"),                    # reject: normalise
    row("ITALY", "pop", "N", "lots", "", "0", "0", "EX", "Q38/P1082", "1.0"),                  # reject: validate N
    row("ITALY", "capital", "X", "Rome", "", "0", "0", "EX", "Q38/P36", "1.0"),                # reject: kind
    row("ITALY", "pop", "N", "59000000", "", "20x3", "0", "EX", "Q38/P1082", "1.0"),           # reject: year
    row("PORTUGAL", "capital", "S", "Lisbon", "", "0", "0", "EX", "Q45/P36", "1.5"),           # reject: confidence
    T.join(["MALTA", "capital", "S", "Valletta", "", "0", "0", "EX", "Q233/P36", "1.0"]),     # reject: 10 columns
    row("MERCURY", "isa", "S", "named thing", "", "0", "0", "EX", "Q308/P31", "1.0"),         # staged (the planet)
    row("MERCURY", "isa", "S", "named thing", "", "0", "0", "EX", "Q925/P31", "1.0"),         # staged (the element)
]
SOURCE = ("# synthetic test source\n" + "\n".join(SOURCE_LINES) + "\n").encode() + b"NOT\xffUTF8\n"  # reject: parse
SHA = "sha256:" + hashlib.sha256(SOURCE).hexdigest()


class Server:
    """A file server on 127.0.0.1 with Range, ETag, and faults on request."""

    def __init__(self):
        self.files, self.requests = {}, []
        server = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                f = server.files.get(self.path)
                server.requests.append({"path": self.path, **{k: v for k, v in self.headers.items()}})
                if f is None:
                    self.send_error(404)
                    return
                data, etag = f["data"], f.get("etag", '"v1"')
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.end_headers()
                    return
                start = 0
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes=") and f.get("range", True):
                    start = int(rng[6:].split("-")[0])
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", etag)
                self.end_headers()
                drop = f.pop("drop_after", None)
                if drop is not None:
                    self.wfile.write(body[:drop])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def opener(req):
    return NO_PROXY.open(req, timeout=10)


def registry_toml(url, sha=SHA, extra="", **over):
    d = {"source_type": '"WIKIDATA"', "licence": '"CC0-1.0"', "attribution": '"synthetic test data"',
         "url": f'"{url}"', "sha256": f'"{sha}"', "parser": '"pf"',
         "relations": '{ capital = "CAPITAL_CITY", pop = "POPULATION", isa = "IS_A", mass = "ATOMIC_MASS", '
                      'symbol = "CHEMICAL_SYMBOL" }',
         "entity": '"wd"', "max_bytes": "100000", "approved_by": '"tester"', "approved_at": '"2026-11-15"'}
    d.update(over)
    return "[source.example]\n" + "".join(f"{k} = {v}\n" for k, v in d.items() if v is not None) + extra


class AcquireCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.server = Server()
        self.server.files["/example.pf"] = {"data": SOURCE}
        self.master = self.dir / "corpus" / "corpus.pf"
        self.master.parent.mkdir()
        self.master.write_text(MASTER)
        self.root = self.dir / "acquire"
        self.root.mkdir()
        self.write_registry()
        self.sleeps = []

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()

    def write_registry(self, **over):
        (self.root / "sources.toml").write_text(registry_toml(self.server.url + "/example.pf", **over))

    def acq(self):
        return Acquirer(self.root, self.master, opener=opener, sleep=self.sleeps.append)

    def master_state(self):
        st = self.master.stat()
        return hashlib.sha256(self.master.read_bytes()).hexdigest(), st.st_ino, st.st_mtime_ns

    def run_dir(self, report):
        return self.root / "staging" / "example" / report["run"]

    def jsonl(self, path):
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


class TestRegistry(AcquireCase):
    def test_an_unapproved_source_is_refused(self):
        self.write_registry(approved_by=None)
        with self.assertRaisesRegex(AcquireError, "isn't approved"):
            self.acq().run("example")
        self.assertEqual(self.server.requests, [])

    def test_no_licence_no_checksum_or_an_unknown_parser_is_refused(self):
        for over, why in (({"licence": None}, "licence"), ({"sha256": None}, "can't be verified"),
                          ({"parser": '"xml"'}, "no parser")):
            with self.subTest(why=why):
                self.write_registry(**over)
                with self.assertRaisesRegex(AcquireError, why):
                    self.acq().run("example")

    def test_a_source_type_the_reader_doesnt_rank_is_refused(self):
        self.write_registry(source_type='"EXAMPLE"')
        with self.assertRaisesRegex(AcquireError, "doesn't rank source_type 'EXAMPLE'"):
            self.acq().run("example")

    def test_a_broken_registry_is_an_error_not_an_empty_one(self):
        (self.root / "sources.toml").write_text("[source.example\nurl = ")
        with self.assertRaisesRegex(AcquireError, "can't be read"):
            registry.load(self.root / "sources.toml")


class TestFetch(AcquireCase):
    def test_a_download_is_verified_and_renamed_into_place(self):
        got = self.acq().fetcher.fetch(registry.get(self.root / "sources.toml", "example"))
        self.assertTrue(got.changed)
        self.assertEqual(got.path.read_bytes(), SOURCE)
        self.assertEqual(got.path.parent.name, SHA[7:15])
        self.assertEqual(list((self.root / "downloads" / "example").glob("*.part")), [])

    def test_an_interrupted_download_resumes_with_range(self):
        self.server.files["/example.pf"]["drop_after"] = 100
        src = registry.get(self.root / "sources.toml", "example")
        with self.assertRaises(FetchInterrupted):
            self.acq().fetcher.fetch(src)
        part = self.root / "downloads" / "example" / "example.pf.part"
        self.assertEqual(part.stat().st_size, 100)
        got = self.acq().fetcher.fetch(src)
        self.assertEqual(got.path.read_bytes(), SOURCE)
        self.assertEqual(self.server.requests[-1].get("Range"), "bytes=100-")

    def test_a_server_that_ignores_range_restarts_from_zero(self):
        self.server.files["/example.pf"].update(drop_after=100, range=False)
        src = registry.get(self.root / "sources.toml", "example")
        with self.assertRaises(FetchInterrupted):
            self.acq().fetcher.fetch(src)
        got = self.acq().fetcher.fetch(src)                    # asked for bytes=100-, got a 200 with everything
        self.assertEqual(got.path.read_bytes(), SOURCE)        # not 100 bytes + the whole file

    def test_a_wrong_checksum_is_quarantined_and_not_staged(self):
        self.server.files["/example.pf"]["data"] = SOURCE.replace(b"Berlin", b"Bonn!!")
        before = self.master_state()
        with self.assertRaisesRegex(FetchRejected, "quarantined"):
            self.acq().run("example")
        q = list((self.root / "quarantine" / "example").glob("example.pf.*"))
        self.assertEqual(len([p for p in q if not p.name.endswith(".why")]), 1)
        self.assertFalse((self.root / "staging").exists())
        self.assertEqual(self.master_state(), before)

    def test_a_checksum_file_is_used_when_the_registry_has_none(self):
        self.server.files["/SHA256SUMS"] = {"data": f"{SHA[7:]}  example.pf\n{'0' * 64}  other.pf\n".encode()}
        self.write_registry(sha256=None, checksum_url=f'"{self.server.url}/SHA256SUMS"')
        got = self.acq().fetcher.fetch(registry.get(self.root / "sources.toml", "example"))
        self.assertEqual(got.sha256, SHA)

    def test_over_max_bytes_is_refused(self):
        self.write_registry(max_bytes="100")
        with self.assertRaisesRegex(FetchRejected, "max_bytes"):
            self.acq().run("example")

    def test_an_unchanged_upstream_is_not_downloaded_again(self):
        self.write_registry(sha256=None, checksum_url=f'"{self.server.url}/SHA256SUMS"')
        self.server.files["/SHA256SUMS"] = {"data": f"{SHA[7:]}  example.pf\n".encode()}
        src = registry.get(self.root / "sources.toml", "example")
        self.acq().fetcher.fetch(src)
        got = self.acq().fetcher.fetch(src)
        self.assertFalse(got.changed)
        self.assertEqual([r["path"] for r in self.server.requests].count("/example.pf"), 1)

    def test_the_bandwidth_limit_holds(self):
        clock = [0.0]
        b = Bucket(1000, clock=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        for _ in range(10):
            b.take(500)
        self.assertAlmostEqual(clock[0], 5.0, places=6)          # 5000 bytes at 1000 bytes/s
        self.write_registry(bandwidth="100")
        self.acq().fetcher.fetch(registry.get(self.root / "sources.toml", "example"))
        self.assertGreater(sum(self.sleeps), 0)


class TestRun(AcquireCase):
    def test_every_planted_case_ends_where_it_should(self):
        before = self.master_state()
        r = self.acq().run("example")
        self.assertEqual(r["counts"], {"read": 18, "staged": 6, "rejected": 9, "duplicate": 3, "conflicts": 1})
        self.assertTrue(r["reconciled"])
        self.assertEqual(r["duplicates_of_rows_without_entity"], 2)        # Lion/Mammal and HE: legacy rows
        self.assertEqual(r["rejected_by"], {
            "map: relation": 1, "map: source-id": 1, "normalise: empty": 1, "parse: columns": 1, "parse: not-utf8": 1,
            "validate: N value isn't a number": 1, "validate: confidence outside 0..1": 1,
            "validate: start/until isn't a year": 1, "validate: unknown kind": 1})
        d = self.run_dir(r)
        rejects = self.jsonl(d / "rejects.jsonl")
        self.assertEqual(len(rejects), 9)
        self.assertTrue(all(x["why"] and x["raw"] for x in rejects))
        self.assertEqual({x["line"] for x in rejects if x["stage"] == "parse"}, {16, 19})   # 10 columns; not UTF-8
        staged = [x.split(T) for x in (d / "delta.pf").read_text().splitlines() if not x.startswith("#")]
        self.assertEqual([(s[0], s[1], s[3]) for s in staged], [
            ("GERMANY", "CAPITAL_CITY", "Berlin"), ("GERMANY", "POPULATION", "83200000"),
            ("HELIUM", "ATOMIC_MASS", "4.002602"), ("FRANCE", "CAPITAL_CITY", "Lyon"),
            ("MERCURY", "IS_A", "named thing"), ("MERCURY", "IS_A", "named thing")])
        self.assertTrue(all(s[7] == "WIKIDATA" and s[8] for s in staged))         # the source's type, the original id
        self.assertEqual([s[10] for s in staged if s[0] == "MERCURY"], ["ENTITY=wd:Q308", "ENTITY=wd:Q925"])
        conflicts = self.jsonl(d / "conflicts.jsonl")
        self.assertEqual([(c["subject"], c["value"], c["existing"][0]["value"]) for c in conflicts],
                         [("FRANCE", "Lyon", "Paris")])
        self.assertEqual(r["licence"], "CC0-1.0")
        self.assertEqual(self.master_state(), before)

    def test_a_master_row_for_another_entity_is_not_a_duplicate(self):
        # The master knows MERCURY the planet (ENTITY=wd:Q308); the element (Q925) with the same words is new.
        self.master.write_text(MASTER + row("MERCURY", "IS_A", "S", "named thing", "", "0", "0", "EX", "Q308/P31",
                                            "1.0", "ENTITY=wd:Q308") + "\n")
        r = self.acq().run("example")
        staged = [x.split(T) for x in (self.run_dir(r) / "delta.pf").read_text().splitlines() if x[:1] != "#"]
        self.assertEqual([s[10] for s in staged if s[0] == "MERCURY"], ["ENTITY=wd:Q925"])
        self.assertEqual(r["duplicates_of_rows_without_entity"], 2)

    def test_the_staged_delta_builds_and_the_entities_stay_two_facts(self):
        r = self.acq().run("example")
        meta, _ = build_shards.build(self.run_dir(r) / "delta.pf", self.dir / "data", 4, 4, fix_keys=True,
                                     log=lambda *a: None)
        self.assertEqual(meta["facts"], 6)                      # the two MERCURY rows aren't merged (Q3)

    def test_a_rerun_with_nothing_changed_is_a_no_op(self):
        a = self.acq()
        first = a.run("example")
        again = a.run("example")
        self.assertTrue(again["unchanged"])
        self.assertEqual(len(a.runs("example")), 1)
        forced = a.run("example", force=True)
        self.assertEqual(forced["delta_sha256"], first["delta_sha256"])
        self.assertEqual(len(a.runs("example")), 2)

    def test_a_changed_mapping_is_a_new_run(self):
        a = self.acq()
        a.run("example")
        self.write_registry(relations='{ capital = "CAPITAL_CITY" }')
        r = a.run("example")
        self.assertNotIn("unchanged", r)
        self.assertEqual(r["counts"]["staged"], 2)

    def test_a_crash_while_staging_leaves_no_run(self):
        a = self.acq()
        a._hook = lambda stage: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            a.run("example")
        self.assertEqual(a.runs("example"), [])
        self.assertEqual(list((self.root / "staging" / "example").glob(".*.part")), [])

    def test_a_part_run_left_by_a_kill_is_removed_by_the_next(self):
        stale = self.root / "staging" / "example" / ".20260101T000000Z-0001.part"
        stale.mkdir(parents=True)
        (stale / "delta.pf").write_text("half")
        r = self.acq().run("example")
        self.assertFalse(stale.exists())
        self.assertEqual(len(self.acq().runs("example")), 1)
        self.assertIn("removed", [x.get("event") for x in self.acq().journal.records()])
        self.assertTrue(r["reconciled"])

    def test_the_journal_records_every_step_and_verifies(self):
        self.acq().run("example")
        events = [x["event"] for x in self.acq().journal.records()]
        self.assertEqual(events, ["fetched", "indexed", "staged"])
        self.assertTrue(self.acq().journal.verify()["ok"])


class TestTheMasterIsNotWritten(AcquireCase):
    def test_integrate_is_off(self):
        with self.assertRaisesRegex(AcquireError, "corpus.integrate is off"):
            self.acq().integrate("example")
        out = subprocess.run([sys.executable, "-m", "computer.acquire", "integrate", "example",
                              "--root", str(self.root), "--master", str(self.master)],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn("corpus.integrate is off", out.stderr)

    def test_a_guarded_master_is_read_under_the_shared_lock_and_left_alone(self):
        g = CorpusGuard(self.master, free_bytes=lambda p: 10 ** 12)
        g.init("tester", "fixture")
        before = self.master_state()
        manifest = g.manifest_path.read_bytes()
        self.acq().run("example")
        self.assertEqual(self.master_state(), before)
        self.assertEqual(g.manifest_path.read_bytes(), manifest)
        self.assertTrue(g.status()["ok"], g.status()["problems"])

    def test_a_held_writer_lock_refuses_the_index_build(self):
        g = CorpusGuard(self.master, free_bytes=lambda p: 10 ** 12)
        g.init("tester", "fixture")
        with g.writing("tester", "holding"):
            with self.assertRaisesRegex(AcquireError, "can't be read for the dedupe index"):
                self.acq().run("example")

    def test_no_code_in_acquire_can_write_the_master(self):
        for p in (ROOT / "computer" / "acquire").glob("*.py"):
            src = p.read_text()
            self.assertNotIn(".writing(", src, p.name)
            self.assertNotIn("Writer", src, p.name)
            tree = ast.parse(src)
            names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
            self.assertFalse(names & {"Writer", "write_atomic"} - ({"write_atomic"} if p.name == "fetch.py" else set()),
                             p.name)


class TestSharedRows(unittest.TestCase):
    def test_numbers_agree_to_the_less_precise_and_names_ignore_case(self):
        self.assertTrue(same_value("N", "4.0026", "4.002602"))
        self.assertFalse(same_value("N", "4.0026", "4.0031"))
        self.assertTrue(same_value("S", "He", "HE"))
        self.assertFalse(same_value("S", "Paris", "Lyon"))

    def test_the_build_uses_the_shared_key(self):
        src = (ROOT / "tools" / "build_shards.py").read_text()
        self.assertIn("pfrow.fact_key(", src)
        self.assertIn("pfrow.finish(", src)
        self.assertIn("field_problems(", (ROOT / "tools" / "validate_corpus.py").read_text())

    def test_entity_and_sense_keep_facts_apart(self):
        k = lambda q: pfrow.fact_key("MERCURY", "IS_A", "S", "x", "", None, None, q, True)  # noqa: E731
        self.assertNotEqual(k("ENTITY=wd:Q308"), k("ENTITY=wd:Q925"))
        self.assertNotEqual(k("SENSE=WN_n_00000001"), k("SENSE=WN_n_00000002"))
        self.assertEqual(k(""), k(""))


if __name__ == "__main__":
    unittest.main()
