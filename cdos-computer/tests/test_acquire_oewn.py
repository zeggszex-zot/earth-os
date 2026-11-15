"""Phase 4: OEWN through the pipeline as a new generation (Q2).  The gwa_xml parser on a small hand-written GWA LMF
file, the relation map, the legacy generation left out of dedupe, the staged swap preview, and the build repairing
only legacy OEWN rows.  No downloads: the source is a file:// URL to the fixture, and nothing writes the master."""

import gzip
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_shards                                                                          # noqa: E402
import swap_preview                                                                          # noqa: E402
from computer.acquire import AcquireError                                                    # noqa: E402
from computer.acquire.parsers import parse_gwa_xml                                           # noqa: E402
from computer.acquire.pipeline import Acquirer                                               # noqa: E402
from computer.corpus import Corpus                                                           # noqa: E402
from computer.corpus_guard import CorpusGuard                                                # noqa: E402

T = "\t"

GWA = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE LexicalResource SYSTEM "http://globalwordnet.github.io/schemas/WN-LMF-1.1.dtd">
<LexicalResource xmlns:dc="https://globalwordnet.github.io/schemas/dc/">
<Lexicon id="oewn" label="Open English WordNet" language="en" email="x@example.org"
         license="https://creativecommons.org/licenses/by/4.0/" version="2025">
  <LexicalEntry id="oewn-dog-n"><Lemma writtenForm="dog" partOfSpeech="n"/>
    <Sense id="oewn-dog__1" synset="oewn-02086723-n"/></LexicalEntry>
  <LexicalEntry id="oewn-domestic_dog-n"><Lemma writtenForm="domestic dog" partOfSpeech="n"/>
    <Sense id="oewn-domestic_dog__1" synset="oewn-02086723-n"/></LexicalEntry>
  <LexicalEntry id="oewn-canine-n"><Lemma writtenForm="canine" partOfSpeech="n"/>
    <Sense id="oewn-canine__1" synset="oewn-02085998-n"/></LexicalEntry>
  <LexicalEntry id="oewn-puppy-n"><Lemma writtenForm="puppy" partOfSpeech="n"/>
    <Sense id="oewn-puppy__1" synset="oewn-01325095-n"/></LexicalEntry>
  <LexicalEntry id="oewn-happy-a"><Lemma writtenForm="happy" partOfSpeech="a"/>
    <Sense id="oewn-happy__1" synset="oewn-01151786-a">
      <SenseRelation relType="antonym" target="oewn-unhappy__1"/>
      <SenseRelation relType="feminine" target="oewn-unhappy__1"/>
    </Sense></LexicalEntry>
  <LexicalEntry id="oewn-unhappy-a"><Lemma writtenForm="unhappy" partOfSpeech="a"/>
    <Sense id="oewn-unhappy__1" synset="oewn-01152856-a">
      <SenseRelation relType="antonym" target="oewn-happy__1"/>
      <SenseRelation relType="derivation" target="oewn-nowhere__1"/>
    </Sense></LexicalEntry>
  <LexicalEntry id="oewn-bank-n"><Lemma writtenForm="bank" partOfSpeech="n"/>
    <Sense id="oewn-bank__1" synset="oewn-08420278-n"/>
    <Sense id="oewn-bank__2" synset="oewn-09213565-n"/></LexicalEntry>
  <LexicalEntry id="oewn-ghost-n"><Lemma writtenForm="ghost" partOfSpeech="n"/>
    <Sense id="oewn-ghost__1" synset="oewn-99999999-n"/></LexicalEntry>
  <Synset id="oewn-02086723-n" members="oewn-dog-n oewn-domestic_dog-n" partOfSpeech="n">
    <Definition>a member of the genus Canis</Definition>
    <Example>the dog barked all night</Example>
    <SynsetRelation relType="hypernym" target="oewn-02085998-n"/>
    <SynsetRelation relType="hyponym" target="oewn-01325095-n"/>
    <SynsetRelation relType="other" target="oewn-02085998-n"/>
    <SynsetRelation relType="hypernym" target="oewn-00000000-n"/>
  </Synset>
  <Synset id="oewn-02085998-n" members="oewn-canine-n" partOfSpeech="n">
    <Definition>any of various fissiped mammals</Definition></Synset>
  <Synset id="oewn-01325095-n" members="oewn-puppy-n" partOfSpeech="n">
    <Definition>a young dog</Definition>
    <SynsetRelation relType="hypernym" target="oewn-02086723-n"/></Synset>
  <Synset id="oewn-01151786-a" members="oewn-happy-a" partOfSpeech="a">
    <Definition>enjoying or showing joy</Definition></Synset>
  <Synset id="oewn-01152856-a" members="oewn-unhappy-a" partOfSpeech="a">
    <Definition>experiencing or marked by sadness</Definition></Synset>
  <Synset id="oewn-08420278-n" members="oewn-bank-n" partOfSpeech="n">
    <Definition>a financial institution</Definition></Synset>
  <Synset id="oewn-09213565-n" members="oewn-bank-n" partOfSpeech="n">
    <Definition>sloping land beside a body of water</Definition></Synset>
  <Synset id="oewn-12345678-n" partOfSpeech="n"><Definition>a synset nobody uses</Definition></Synset>
</Lexicon>
</LexicalResource>
"""

RELATIONS = ('{ definition = "DEFINITION", synonym = "SYNONYM", hypernym = "HYPERNYM", '
             'instance_hypernym = "HYPERNYM", hyponym = "HYPONYM", instance_hyponym = "HYPONYM", '
             'antonym = "WN_ANTONYM", derivation = "DERIVATION" }')


def row(*c):
    return T.join((list(c) + [""] * 11)[:11])


# The legacy generation: OEWN rows with WN_x_nnnnnnnn ids, swapped labels and synset-id values, as corpus.pf has them.
LEGACY = [
    row("DOG", "SYNSET", "S", "WN_n_02084071", "", "0", "0", "OEWN", "WN_n_02084071", "1.0"),
    row("DOG", "DEFINITION", "S", "a member of the genus Canis", "", "0", "0", "OEWN", "WN_n_02084071", "1.0"),
    row("DOG", "ANTONYM", "S", "WN_n_02083346", "", "0", "0", "OEWN", "WN_n_02084071", "1.0"),   # really hypernym
    row("CANINE", "SYNSET", "S", "WN_n_02083346", "", "0", "0", "OEWN", "WN_n_02083346", "1.0"),
    row("CANINE", "DEFINITION", "S", "any of various fissiped mammals", "", "0", "0", "OEWN", "WN_n_02083346", "1.0"),
    row("HAPPY", "SYNSET", "S", "WN_a_01148283", "", "0", "0", "OEWN", "WN_a_01148283", "1.0"),
    row("UNHAPPY", "SYNSET", "S", "WN_a_01149494", "", "0", "0", "OEWN", "WN_a_01149494", "1.0"),
    row("HAPPY", "WORDNET_POINTER_!", "S", "WN_a_01149494", "", "0", "0", "OEWN", "WN_a_01148283", "1.0"),
]
OTHER = [
    row("LION", "IS_A", "S", "MAMMAL", "", "0", "0", "DOCUMENT", "bio", "1.0"),
    row("FRANCE", "CAPITAL", "S", "Paris", "", "0", "0", "WIKIDATA", "Q142/P36", "1.0"),
    row("ODDITY", "SYNONYM", "S", "curiosity", "", "0", "0", "OEWN", "manual-1", "1.0"),     # OEWN, not legacy id
]
MASTER = "# fixture master\n" + "\n".join(LEGACY + OTHER) + "\n"


class OewnCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.xml = self.dir / "english-wordnet-2025.xml.gz"
        self.xml.write_bytes(gzip.compress(GWA.encode()))
        self.master = self.dir / "corpus" / "corpus.pf"
        self.master.parent.mkdir()
        self.master.write_text(MASTER)
        self.root = self.dir / "acquire"
        self.root.mkdir()
        self.write_registry()
        self.cases = self.dir / "no-cases.jsonl"
        self.cases.write_text("")

    def tearDown(self):
        self.tmp.cleanup()

    def write_registry(self, xml: Path | None = None):
        xml = xml or self.xml
        sha = "sha256:" + hashlib.sha256(xml.read_bytes()).hexdigest()
        (self.root / "sources.toml").write_text(
            "[source.oewn]\n"
            'source_type = "OEWN"\nlicence = "CC-BY-4.0 + WordNet License"\n'
            'attribution = "Open English WordNet (test fixture)"\n'
            f'url = "{xml.as_uri()}"\nsha256 = "{sha}"\nparser = "gwa_xml"\nmax_bytes = 1000000\n'
            f"relations = {RELATIONS}\n"
            'replaces = { source_type = "OEWN", source_id = "^WN_[nvar]_[0-9]{8}$" }\n'
            'approved_by = "tester"\napproved_at = "2026-11-15"\n')

    def acq(self):
        return Acquirer(self.root, self.master)

    def staged(self, report):
        d = self.root / "staging" / "oewn" / report["run"]
        return [x.split(T) for x in (d / "delta.pf").read_text().splitlines() if not x.startswith("#")]

    def master_state(self):
        st = self.master.stat()
        return hashlib.sha256(self.master.read_bytes()).hexdigest(), st.st_ino, st.st_mtime_ns


class TestParser(OewnCase):
    def test_rows_and_rejections(self):
        out = list(parse_gwa_xml(self.xml))
        good = [(c[0], c[1], c[3]) for _, c, _, _ in out if c]
        bad = sorted(why.split(":")[0] for _, c, why, _ in out if c is None)
        self.assertIn(("dog", "definition", 'a member of the genus Canis; "the dog barked all night"'), good)
        self.assertIn(("dog", "synonym", "domestic dog"), good)
        self.assertIn(("domestic dog", "hypernym", "canine"), good)
        self.assertIn(("dog", "hyponym", "puppy"), good)
        self.assertIn(("puppy", "hypernym", "dog"), good)              # the first lemma of the target synset
        self.assertIn(("happy", "antonym", "unhappy"), good)
        self.assertIn(("unhappy", "antonym", "happy"), good)
        self.assertEqual(sum(1 for g in good if g[:2] == ("bank", "definition")), 2)   # two senses, two rows
        self.assertEqual(bad, ["dangling-sense-relation", "dangling-synset-relation", "missing-synset", "no-lemmas"])
        for _, c, _, _ in out:
            if c:
                self.assertEqual((c[7], c[10]), ("OEWN", f"SENSE={c[8]}"))
                self.assertTrue(c[8].startswith("oewn-"))
                self.assertFalse(c[3].startswith(("WN_", "oewn-")), c)        # values are words, never ids

    def test_meanings_come_nouns_first_whatever_the_file_order(self):
        xml = ("<LexicalResource><Lexicon id='x'>"
               "<LexicalEntry id='c-v'><Lemma writtenForm='colour'/><Sense id='s1' synset='oewn-00281861-v'/></LexicalEntry>"
               "<LexicalEntry id='c-a'><Lemma writtenForm='colour'/><Sense id='s2' synset='oewn-00395196-a'/></LexicalEntry>"
               "<LexicalEntry id='c-r'><Lemma writtenForm='colourfully'/><Sense id='s3' synset='oewn-00012345-r'/></LexicalEntry>"
               "<LexicalEntry id='c-n'><Lemma writtenForm='colour'/><Sense id='s4' synset='oewn-04963771-n'/></LexicalEntry>"
               "<Synset id='oewn-00012345-r'><Definition>in a colourful way</Definition></Synset>"
               "<Synset id='oewn-00281861-v'><Definition>change color</Definition></Synset>"
               "<Synset id='oewn-00395196-a'><Definition>having colors</Definition></Synset>"
               "<Synset id='oewn-04963771-n'><Definition>a visual attribute of things</Definition></Synset>"
               "</Lexicon></LexicalResource>")
        p = self.dir / "order.xml"
        p.write_text(xml)
        order = [c[8].rsplit("-", 1)[-1] for _, c, _, _ in parse_gwa_xml(p) if c and c[1] == "definition"]
        self.assertEqual(order, ["n", "v", "a", "r"])

    def test_malformed_xml_fails_the_run_and_stages_nothing(self):
        bad = self.dir / "broken.xml.gz"
        bad.write_bytes(gzip.compress(GWA.replace("</Lexicon>", "").encode()))
        self.write_registry(bad)
        with self.assertRaisesRegex(AcquireError, "isn't well-formed XML"):
            self.acq().run("oewn")
        self.assertEqual(self.acq().runs("oewn"), [])

    def test_an_entity_expansion_bomb_is_refused(self):
        lol = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
               + "".join(f'<!ENTITY {chr(98 + i)} "{("&" + chr(97 + i) + ";") * 10}">' for i in range(8))
               + ']><LexicalResource><Lexicon><LexicalEntry id="x"><Lemma writtenForm="&i;"/></LexicalEntry>'
               '</Lexicon></LexicalResource>')
        p = self.dir / "lol.xml"
        p.write_text(lol)
        with self.assertRaisesRegex(AcquireError, "amplification|entity|recursive"):
            list(parse_gwa_xml(p))

    def test_a_decompression_bomb_is_refused(self):
        p = self.dir / "bomb.xml.gz"
        p.write_bytes(gzip.compress(b"<LexicalResource>" + b" " * 2_000_000 + b"</LexicalResource>"))
        with self.assertRaisesRegex(AcquireError, "decompression bomb"):
            list(parse_gwa_xml(p, cap=100_000))


class TestParserScales(unittest.TestCase):
    def test_time_grows_with_the_entries_not_their_square(self):
        def gwa(n):
            return ("<LexicalResource><Lexicon id='x'>"
                    + "".join(f"<LexicalEntry id='e{i}'><Lemma writtenForm='w{i}'/><Sense id='s{i}' synset='y{i}'/>"
                              "</LexicalEntry>" for i in range(n))
                    + "".join(f"<Synset id='y{i}'><Definition>d{i}</Definition></Synset>" for i in range(n))
                    + "</Lexicon></LexicalResource>").encode()

        import time
        with tempfile.TemporaryDirectory() as t:
            took = {}
            for n in (5_000, 40_000):
                p = Path(t) / f"x{n}.xml"
                p.write_bytes(gwa(n))
                start = time.perf_counter()
                self.assertEqual(sum(1 for _ in parse_gwa_xml(p)), n)
                took[n] = time.perf_counter() - start
        # 8x the entries: about 8x the time when linear, 64x when quadratic
        self.assertLess(took[40_000] / took[5_000], 20, took)


class TestRun(OewnCase):
    def test_the_run_maps_and_counts_everything(self):
        before = self.master_state()
        r = self.acq().run("oewn")
        self.assertEqual(r["counts"], {"read": 24, "staged": 17, "rejected": 7, "duplicate": 0, "conflicts": 0})
        self.assertEqual(r["rejected_by"], {"map: relation": 3, "parse: dangling-sense-relation": 1,
                                            "parse: dangling-synset-relation": 1, "parse: missing-synset": 1,
                                            "parse: no-lemmas": 1})
        rels = {(s[0], s[1], s[3]) for s in self.staged(r)}
        self.assertIn(("happy", "WN_ANTONYM", "unhappy"), rels)
        self.assertIn(("dog", "HYPERNYM", "canine"), rels)
        self.assertIn(("dog", "HYPONYM", "puppy"), rels)
        self.assertEqual(r["replaces"], {"source_type": "OEWN", "source_id": "^WN_[nvar]_[0-9]{8}$"})
        self.assertEqual(self.master_state(), before)

    def test_the_replaced_generation_is_left_out_of_the_index(self):
        self.acq().run("oewn")
        meta = self.acq().index.meta()
        self.assertEqual(int(meta["rows"]), len(OTHER))                 # legacy SYNSET and fact rows all left out
        self.assertIn("WN_", meta["exclude"])


class TestSwapPreview(OewnCase):
    def test_the_preview_builds_the_candidate_and_leaves_the_master(self):
        g = CorpusGuard(self.master, free_bytes=lambda p: 10 ** 12)
        g.init("tester", "fixture")
        before, manifest = self.master_state(), g.manifest_path.read_bytes()
        acq = self.acq()
        acq.run("oewn")
        out = swap_preview.preview(acq, "oewn", None, self.cases, shards=8)
        self.assertEqual(self.master_state(), before)
        self.assertEqual(g.manifest_path.read_bytes(), manifest)
        self.assertEqual(out["rows"]["removed_total"], len(LEGACY))
        self.assertEqual(out["rows"]["added_total"], 17)
        self.assertEqual(out["rows"]["kept"], len(OTHER))
        cand = Path(out["candidate"]["path"]).read_text()
        self.assertNotIn("WN_n_02084071", cand)
        self.assertIn("ODDITY", cand)                                   # OEWN, but not the replaced generation
        rel = out["served"]["relations"]
        self.assertEqual(rel["HYPERNYM"], {"before": 1, "after": 3, "change": 2.0,
                                           "examples_after": rel["HYPERNYM"]["examples_after"]})
        self.assertEqual((rel["WN_ANTONYM"]["before"], rel["WN_ANTONYM"]["after"]), (1, 2))
        self.assertTrue(out["check"]["ok"], out["check"])
        self.assertFalse(out["integrated"])
        self.assertTrue((Path(out["candidate"]["path"]).parent / "swap.json").exists())
        build_shards.build(out["candidate"]["path"], self.dir / "c-data", 8, 8, fix_wordnet=True,
                                     fix_wikidata=True, fix_keys=True, log=lambda *a: None)
        c = Corpus(self.dir / "c-data").load()
        self.assertIn(("WN_ANTONYM", "unhappy"), {(f.relation, f.value) for f in c.facts_for("HAPPY")})
        self.assertIn(("HYPERNYM", "canine"), {(f.relation, f.value) for f in c.facts_for("DOG")})

    def test_no_preview_without_a_run_or_after_the_master_changed(self):
        with self.assertRaisesRegex(AcquireError, "no staged run"):
            swap_preview.preview(self.acq(), "oewn", None, self.cases, shards=8)
        self.acq().run("oewn")
        self.master.write_text(MASTER + row("NEW", "IS_A", "S", "THING", "", "0", "0", "DOCUMENT", "x") + "\n")
        with self.assertRaisesRegex(AcquireError, "master changed"):
            swap_preview.preview(self.acq(), "oewn", None, self.cases, shards=8)

    def test_integrate_is_still_off(self):
        with self.assertRaisesRegex(AcquireError, "corpus.integrate is off"):
            self.acq().integrate("oewn")


class TestBuildRepairsOnlyLegacyRows(unittest.TestCase):
    def test_a_new_oewn_row_is_not_relabelled(self):
        with tempfile.TemporaryDirectory() as t:
            pf = Path(t) / "c.pf"
            pf.write_text("\n".join([
                row("HAPPY", "ANTONYM", "S", "unhappy", "", "0", "0", "OEWN", "oewn-01151786-a", "1.0",
                    "SENSE=oewn-01151786-a"),
                row("DOG", "HYPERNYM", "S", "canine", "", "0", "0", "OEWN", "oewn-02086723-n", "1.0",
                    "SENSE=oewn-02086723-n"),
                row("CAT", "ANTONYM", "S", "WN_n_02083346", "", "0", "0", "OEWN", "WN_n_02121620", "1.0"),
                row("CANINE", "SYNSET", "S", "WN_n_02083346", "", "0", "0", "OEWN", "WN_n_02083346", "1.0"),
            ]) + "\n")
            build_shards.build(pf, Path(t) / "d", 4, 4, fix_wordnet=True, fix_keys=True, log=lambda *a: None)
            c = Corpus(Path(t) / "d").load()
            facts = lambda k: {(f.relation, f.value) for f in c.facts_for(k)}            # noqa: E731
            self.assertIn(("ANTONYM", "unhappy"), facts("HAPPY"))         # new row: kept as written
            self.assertIn(("HYPERNYM", "canine"), facts("DOG"))
            self.assertIn(("HYPERNYM", "canine"), facts("CAT"))           # legacy row: still repaired


if __name__ == "__main__":
    unittest.main()
