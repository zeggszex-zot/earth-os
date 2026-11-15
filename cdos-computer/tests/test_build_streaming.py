"""The streaming build (tools/build_shards.py, phase 3): the same corpus, row for row, with memory that doesn't grow
with the number of rows.

GOLDEN holds the versions today's in-memory builder gave on the synthetic corpus below, recorded before the streaming
build replaced it (2026-11-15).  Every repair path is in that corpus: OEWN synsets, swapped labels, pointers, synsets
with no words, rows pointing at themselves; Wikidata ids as subjects and values, resolved and not, units, the same
name for different things; keys in mixed case; exact and repaired duplicates; dates, years, numbers, text relations,
SENSE= and ENTITY=.  The streaming build must reproduce every version exactly.
"""

import gzip
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_shards                                                                          # noqa: E402
from computer.corpus import Corpus                                                           # noqa: E402

T = "\t"


def synthetic_rows(n_subjects: int = 600, seed: int = 20261115) -> list[str]:
    rnd = random.Random(seed)
    rows = []

    def add(s, r, k, v, st, sid, unit="", vf="0", vu="0", conf="0.95", qual=""):
        rows.append(T.join([s, r, k, v, unit, vf, vu, st, sid, conf, qual]))

    # OEWN: synsets and their words, swapped labels, pointers, unresolvable synsets, self-references
    syn = [f"WN_n_{i:08d}" for i in range(1, n_subjects // 2)]
    words = {}
    for i, sid in enumerate(syn):
        w = [f"word{i}", f"Word{i}_alt"] if i % 7 else [f"word{i}"]
        words[sid] = w
        for x in w:
            add(x.upper(), "SYNSET", "S", sid, "OEWN", sid)
        add(w[0].upper(), "DEFINITION", "S", f"the meaning of word {i}", "OEWN", sid)
    for i in range(n_subjects):
        a, b = rnd.choice(syn), rnd.choice(syn)
        subj = words[a][0].upper()
        rel = rnd.choice(["ANTONYM", "HYPERNYM", "SYNONYM", "WORDNET_POINTER_!", "WORDNET_POINTER_&",
                          "WORDNET_POINTER_;C", "WORDNET_POINTER_-U", "WORDNET_POINTER_~x", "DERIVATION"])
        val = b if i % 5 else words[b][0]
        add(subj, rel, "S", val, "OEWN", a)
    add("LONELY", "HYPERNYM", "S", "WN_n_99999999", "OEWN", "WN_n_99999998")          # a synset with no words
    add("WORD3", "SYNONYM", "S", syn[3], "OEWN", syn[3])                                # resolves to itself

    # Wikidata: ids as subjects and values, names from source_id, units, the same name for two things
    qs = [f"Q{100 + i}" for i in range(n_subjects // 3)]
    names = {q: (f"Place {i}" if i % 9 else q) for i, q in enumerate(qs)}               # every 9th has no name
    for q in qs:
        add(names[q], "INSTANCE_OF", "S", rnd.choice(qs), "WIKIDATA", f"{q}/P31")
        add(names[q], "POPULATION", "N", str(rnd.randint(1, 10 ** 7)), "WIKIDATA", f"{q}/P1082",
            vf=str(rnd.randint(1900, 2025)))
        add(names[q], "AREA", "N", str(rnd.randint(1, 9000)), "WIKIDATA", f"{q}/P2046",
            unit=rnd.choice(["WD_Q232291", "WD_Q712226", ""]))
    add("Mars", "NAMED_AFTER", "S", "Q999", "WIKIDATA", "Q111/P138")
    add("Mars", "INSTANCE_OF", "S", "planet", "WIKIDATA", "Q111/P31")
    add("Mars", "INSTANCE_OF", "S", "god", "WIKIDATA", "Q999/P31")
    add("Place 1", "SHARES_BORDER_WITH", "S", "Q101", "WIKIDATA", "Q101/P47")             # points at itself
    add("Q5555", "INSTANCE_OF", "S", "Q100", "WIKIDATA", "Q5555/P31")                     # an id with no name

    # curated: mixed-case keys, exact and repaired duplicates, dates, deep time, text, ENTITY=, SENSE=
    for i in range(n_subjects):
        s = rnd.choice([f"Thing {i}", f"THING_{i}", f"thing-{i}"])
        add(s, "IS_A", "S", rnd.choice(["Mammal", "MAMMAL", "planet", "Planet"]), "DOCUMENT", "bio")
        add(s, "DISCOVERED", "D", rnd.choice(["1846-09-23", "1930", "-4527000000"]), "TABLE", "dates")
        add(s, "DESCRIPTION", "S", f"thing number {i}", "MANUAL", "notes")
        if i % 11 == 0:
            add(s, "IS_A", "S", "Mammal", "DOCUMENT", "bio")                                  # an exact duplicate
        if i % 13 == 0:
            add(s, "NAMED_AFTER", "S", "someone", "WIKIDATA", f"Q{i}/P138", qual=f"ENTITY=wd:Q{i}")
            add(s, "NAMED_AFTER", "S", "someone", "WIKIDATA", f"Q{i + 1}/P138", qual=f"ENTITY=wd:Q{i + 1}")
        if i % 17 == 0:
            add(s, "SYNONYM", "S", "same", "OEWN", "x", qual="SENSE=WN_n_00000001")
            add(s, "SYNONYM", "S", "same", "OEWN", "x", qual="SENSE=WN_n_00000002")
    rnd.shuffle(rows)
    return ["# synthetic corpus for the streaming build", "# columns"] + rows


def write_synthetic(path: Path, n_subjects: int = 600) -> Path:
    path.write_text("\n".join(synthetic_rows(n_subjects)) + "\n", encoding="utf-8")
    return path


FLAGS = [(w, d, k) for w in (False, True) for d in (False, True) for k in (False, True)]
GOLDEN = {             # (fix_wordnet, fix_wikidata, fix_keys, shards) -> content_sha256, from the in-memory build
    (False, False, False, 8): "c856bf384fe0c250ceebb6f32d50f4fb1c21eeaff9ea2cd975ac87f06bf7c531",
    (False, False, False, 256): "066a6064462d42ad84837c83a0354521376283693157c9e60161a42305ac443e",
    (False, False, True, 8): "76d3ddc1202d4310d5fec905276748c21956e944e8863f0d71e7f6c8bb6a8cf2",
    (False, False, True, 256): "68cf4f1c929f96d34bb726361f9aa4c0934e2e33d0f8836981c46feaa54b04c9",
    (False, True, False, 8): "375121982a4b589fc70fd1145e40d0b77c076a8e5e2a5e88386bee3a15a61a41",
    (False, True, False, 256): "a619a20ff75def6c2c270b2fc0236268641898a1f15bf8d823cafb3160276f2d",
    (False, True, True, 8): "045f1610b42580199e6b2e80dcba560a7bc239957165b54710acbea862a0fb8d",
    (False, True, True, 256): "630e7c12ad0d5ccb1bb2dc5d0025b937292cb4cb1d75a39320cd9f141676e792",
    (True, False, False, 8): "e5d82717222cc090ad343c6a5ef5575db164b6bd92b20ad23cad5d5e225e6e99",
    (True, False, False, 256): "5e36ad5e2f7508d9d8cbf5d6e09d25d71e07a5962db1f2c9fec3f9c23d28e45f",
    (True, False, True, 8): "ba74df36e92be59a90967c4b77e8ae529ecf62ec5f7b9eebbda05770c9ef98d1",
    (True, False, True, 256): "675b838ece31948474a75f7605878c66a070bd2551f6b09c56487306cac46895",
    (True, True, False, 8): "10d58e658eb64d5657a45fb98718d8cd9316f7403770f72dff30bfbaa9a986ec",
    (True, True, False, 256): "2a42f43b2f2b8b06e06c2a1956d88aafeb6b6885831d509066adc8cbf9c3d352",
    (True, True, True, 8): "827967fe554aff1a6bf336206bd4a297e9f7f1a01047c075b0b6e9324b7830d4",
    (True, True, True, 256): "6ce1a4cda541009a3ae135b440306075fef3908b83e187a812b166a138dcea2e",
}


def build_version(pf: Path, out: Path, flags, shards) -> str:
    w, d, k = flags
    meta, _ = build_shards.build(pf, out, shards, shards, fix_wordnet=w, fix_wikidata=d, fix_keys=k,
                                 log=lambda *a: None)
    return meta["content_sha256"]


EDGE = {               # name -> (corpus.pf bytes, content_sha256 from the in-memory build); 4 shards, fix_keys
    "empty": (b"# nothing\n", "5a96b87ee314daa49f73f7df5b60addf92e41db860d2351a6b24f7f00de70138"),
    "one row": (b"LION\tIS_A\tS\tMAMMAL\t\t0\t0\tDOCUMENT\tbio\t0.9\t\n",
                "ee54659a2a18a66a0dd747da56edc53f40afdd383114fcf1df34d721a0b6f738"),
    "CRLF": (b"LION\tIS_A\tS\tMAMMAL\t\t0\t0\tDOCUMENT\tbio\t0.9\t\r\nTIGER\tIS_A\tS\tCAT\t\t0\t0\tDOCUMENT\tbio\t\t\r\n",
             "5c94297644cbd8f697e81054c92f8fd270b332b983e98a10b655a20372863d7f"),
}
REAL_VERSION = "87ee23f5366d87d59385f089b92462035a92fd3d2bfa1dd792bfccb59c2211dd"


class StreamingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.pf = write_synthetic(self.dir / "corpus.pf")

    def tearDown(self):
        self.tmp.cleanup()

    def spills(self):
        return [p.name for p in self.dir.iterdir() if ".build-" in p.name]


class TestSameCorpus(StreamingCase):
    def test_every_golden_version(self):
        for (w, d, k, shards), version in GOLDEN.items():
            with self.subTest(fix_wordnet=w, fix_wikidata=d, fix_keys=k, shards=shards):
                self.assertEqual(build_version(self.pf, self.dir / f"d-{w}{d}{k}{shards}", (w, d, k), shards),
                                 version)
        self.assertEqual(self.spills(), [])

    def test_edge_cases(self):
        for name, (data, version) in EDGE.items():
            with self.subTest(name):
                pf = self.dir / "edge.pf"
                pf.write_bytes(data)
                self.assertEqual(build_version(pf, self.dir / name.replace(" ", "_"), (False, False, True), 4),
                                 version)

    def test_a_tiny_spill_buffer_changes_nothing(self):
        old, made = build_shards.SPILL_BUFFER, []
        real_spill = build_shards.Spill

        class Counted(real_spill):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                made.append(self)

        build_shards.SPILL_BUFFER, build_shards.Spill = 1, Counted          # flush after every row
        try:
            v = build_version(self.pf, self.dir / "tiny", (True, True, True), 8)
        finally:
            build_shards.SPILL_BUFFER, build_shards.Spill = old, real_spill
        self.assertEqual(v, GOLDEN[(True, True, True, 8)])
        self.assertGreater(made[0].flushes, 1000)                             # it really did flush every row

    def test_two_builds_are_the_same_bytes(self):
        build_version(self.pf, self.dir / "a", (True, True, True), 8)
        build_version(self.pf, self.dir / "b", (True, True, True), 8)
        for p in sorted((self.dir / "a").rglob("*.bin")):
            self.assertEqual(p.read_bytes(), (self.dir / "b" / p.relative_to(self.dir / "a")).read_bytes(), p.name)
        self.assertEqual(gzip.decompress((self.dir / "a" / "s" / "000.bin").read_bytes())[-1:], b"\n")

    def test_the_build_still_reads_like_before(self):
        build_version(self.pf, self.dir / "data", (True, True, True), 8)
        c = Corpus(self.dir / "data").load()
        self.assertEqual(c.integrity, "verified")
        self.assertIn(("INSTANCE_OF", "planet"), {(f.relation, f.value) for f in c.facts_for("MARS")})
        self.assertEqual(sum(1 for _ in c.subjects), c.meta["subjects"])


class TestSpill(StreamingCase):
    def test_the_spill_is_removed_when_the_build_fails(self):
        real = build_shards._write_gz
        calls = []

        def fail_on_third(path, lines):
            calls.append(path)
            if len(calls) == 3:
                raise OSError("disk full (simulated)")
            return real(path, lines)

        build_shards._write_gz = fail_on_third
        try:
            with self.assertRaisesRegex(OSError, "simulated"):
                build_version(self.pf, self.dir / "data", (True, True, True), 8)
        finally:
            build_shards._write_gz = real
        self.assertEqual(self.spills(), [])


class TestMemory(unittest.TestCase):
    """Peak memory with 50,000 and 400,000 rows over the same 2,000 subjects, each build in its own process.  The
    in-memory build grew by about 190 MB between the two; the streaming one must stay within a few MB."""

    CODE = r"""
import resource, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[1] + "/tools")
import build_shards
build_shards.build(sys.argv[2], sys.argv[3], fix_keys=True, log=lambda *a: None)
print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
"""

    def peak_kb(self, rows: int, tmp: Path) -> int:
        pf = tmp / f"rows-{rows}.pf"
        with open(pf, "w") as fh:
            for i in range(rows):
                fh.write(f"Thing {i % 2000}\tVALUE_{i % 50}\tS\tV {i}\t\t0\t0\tDOCUMENT\tbio\t0.9\t\n")
        out = subprocess.run([sys.executable, "-c", self.CODE, str(ROOT), str(pf), str(tmp / f"d{rows}")],
                             capture_output=True, text=True, check=True)
        return int(out.stdout.split()[-1])

    def test_memory_does_not_grow_with_the_rows(self):
        with tempfile.TemporaryDirectory() as t:
            small, large = self.peak_kb(50_000, Path(t)), self.peak_kb(400_000, Path(t))
        self.assertLess(large - small, 12 * 1024, f"peak grew from {small // 1024} MB to {large // 1024} MB")


REAL = os.environ.get("CDOS_CORPUS_PF", "")


@unittest.skipUnless(REAL and Path(REAL).is_file(), "set CDOS_CORPUS_PF to a real corpus.pf (it is only read)")
class TestTheRealCorpus(unittest.TestCase):
    def test_the_real_corpus_builds_to_the_same_version(self):
        with tempfile.TemporaryDirectory() as t:
            meta, _ = build_shards.build(REAL, Path(t) / "data", fix_wordnet=True, fix_wikidata=True, fix_keys=True,
                                         log=lambda *a: None)
        self.assertEqual(meta["content_sha256"], REAL_VERSION)


if __name__ == "__main__" and sys.argv[1:] == ["--golden"]:
    with tempfile.TemporaryDirectory() as tmp:
        pf = write_synthetic(Path(tmp) / "corpus.pf")
        for flags in FLAGS:
            for shards in (8, 256):
                v = build_version(pf, Path(tmp) / f"d{flags}{shards}", flags, shards)
                print(f"    ({flags[0]}, {flags[1]}, {flags[2]}, {shards}): \"{v}\",")
    sys.exit(0)
