"""The capability registry, the result envelope, and the corpus version."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import mini_corpus                                                    # noqa: E402
from computer import capabilities as caps                                  # noqa: E402
from computer import envelope                                              # noqa: E402
from computer.capabilities import (DECLARATIONS, CapabilityError, Registry, Unavailable, default_registry,  # noqa: E402
                              scan)
from computer.config import Config                                         # noqa: E402
from computer.corpus import Corpus, content_hash                           # noqa: E402
from computer.core import Computer                                            # noqa: E402

TMP = tempfile.TemporaryDirectory()


def decl(**over):
    d = {"name": "test.thing", "version": "1.0.0", "inputs": {}, "output": {"type": "x"}, "deterministic": True,
         "cost": {"memory_mb": 1, "cpu": "low"}, "writes_state": False, "requires": [], "optional": False,
         "fallback": "none", "errors": []}
    d.update(over)
    return d


def module(src: str) -> str:
    p = Path(tempfile.mkdtemp()) / "m.py"
    p.write_text(src)
    return str(p)


class TestRegistryRefuses(unittest.TestCase):
    def refuses(self, *ds, why=""):
        with self.assertRaises(CapabilityError) as cm:
            Registry(list(ds)).validate()
        if why:
            self.assertIn(why, str(cm.exception))

    def test_the_shipped_inventory_validates(self):
        r = default_registry()
        self.assertGreaterEqual(len(r.caps), 20)
        self.assertEqual(r.available("logic.units_gnu"), (False, "not implemented in this release"))

    def test_fields(self):
        d = decl()
        del d["errors"]
        self.refuses(d, why="missing field 'errors'")
        self.refuses(decl(deterministic="yes"), why="must be bool")
        self.refuses(decl(version="1.0"), why="semver")
        self.refuses(decl(name="Thing"), why="dotted lower case")
        self.refuses(decl(cost={"memory_mb": 1}), why="cost")
        self.refuses(decl(cost={"memory_mb": True, "cpu": "low"}), why="cost")
        self.refuses(decl(errors=["OOPS"]), why="vocabulary")
        self.refuses(decl(inputs={"x": {"type": "int"}}), why="input 'x'")
        self.refuses(decl(colour="blue"), why="unknown field")
        self.refuses(decl(), decl(), why="declared twice")

    def test_nested_fields(self):
        self.refuses(decl(requires=[7]), why="requires entries are strings")
        self.refuses(decl(requires=["corpus.lookup"]), why="requires entries are strings")
        self.refuses(decl(requires=["magic:wand"]), why="requires entries are strings")
        self.refuses(decl(requires=["capability:"]), why="requires entries are strings")
        self.refuses(decl(output={}), why="output is")
        self.refuses(decl(output={"type": 3}), why="output is")
        self.refuses(decl(output={"type": "x", "spec": ["a"]}), why="output is")
        self.refuses(decl(inputs={"x": {"type": "Not A Type", "required": True}}), why="lower-case name")
        self.refuses(decl(inputs={"x": {"type": "int", "required": True, "default_from": 3}}), why="default_from")
        self.refuses(decl(errors=["CONFLICT", "CONFLICT"]), why="distinct")
        self.refuses(decl(modules=[3]), why="modules are strings")
        self.refuses(decl(requires=["capability:test.ghost"]), why="isn't declared")

    def test_writers_need_the_allowlist(self):
        self.refuses(decl(writes_state=True, deterministic=False), why="allowlist")

    def test_fallbacks(self):
        self.refuses(decl(optional=True), why="need a fallback")
        self.refuses(decl(optional=True, fallback="test.nowhere"), why="isn't declared")
        self.refuses(decl(name="test.a", optional=True, fallback="test.b"),
                     decl(name="test.b", optional=True, fallback="test.a"), why="cycle")

    def test_determinism_is_checked_in_the_source(self):
        self.refuses(decl(modules=[module("import time\ndef f():\n    return time.time()\n")]), why="calls time.time()")
        self.refuses(decl(modules=[module("import random\n")]), why="imports random")
        self.refuses(decl(modules=[module("from datetime import datetime\n")]), why="datetime")
        self.refuses(decl(modules=[module("import socket\n")]), why="imports socket")
        self.refuses(decl(modules=[module("x = open('/dev/hwrng')\n")]), why="/dev/hwrng")
        self.refuses(decl(modules=[module("import os\nfd = os.open('/dev/hwrng', os.O_RDONLY)\n")]), why="/dev/hwrng")
        self.refuses(decl(modules=[module("from pathlib import Path\nb = Path('/dev/urandom').read_bytes()\n")]),
                     why="/dev/urandom")
        Registry([decl(modules=[module("NEVER = ('/dev/hwrng', '/dev/random')\n")])]).validate()   # naming isn't reading
        self.refuses(decl(modules=[module("from .clock import current_year\n")]), why="reads the clock")
        Registry([decl(modules=[module('"""Never calls time.time() itself."""\nimport time\nt = time.monotonic()\n')])]).validate()

    def test_the_clock_only_as_a_declared_default(self):
        clock = decl(name="system.clock", deterministic=False)
        uses = decl(modules=[module("from .clock import current_year\n")],
                    inputs={"year": {"type": "int", "required": False, "default_from": "system.clock"}},
                    requires=["capability:system.clock"])
        Registry([clock, uses]).validate()
        self.refuses(clock, decl(requires=["capability:system.clock"]), why="non-deterministic system.clock")
        self.refuses(decl(inputs={"year": {"type": "int", "required": False, "default_from": "system.nope"}}),
                     why="isn't declared")

    def test_the_answer_path_modules_are_clean(self):
        for m in ("corpus", "navigator", "planner", "plan", "logic", "quantity", "analysis", "rules", "calc", "phrase"):
            self.assertEqual(scan((ROOT / "computer" / f"{m}.py").read_text(), clock_ok=True), "", m)
        self.assertEqual(scan((ROOT / "computer" / "navigator.py").read_text(), clock_ok=False),
                         "reads the clock (computer.clock) without declaring a year input with default_from system.clock")

    def test_every_import_of_the_answer_path_is_checked_or_reviewed(self):
        self.assertEqual(caps.unreviewed_imports(default_registry()), [])
        self.assertEqual(scan((ROOT / "computer" / "capabilities.py").read_text()), "")   # reviewed as "checked in the tests"
        r = Registry([decl(modules=[module("from .navdata import generate\n")])])
        self.assertEqual(caps.unreviewed_imports(r), ["test.thing: " + r.caps["test.thing"].modules[0]
                                                      + " imports ['generate'] from computer.navdata"])
        from computer.navdata import shape                                    # the one reviewed navdata import: pure
        self.assertEqual([shape("ORBITS") for _ in range(3)], [shape("ORBITS")] * 3)

    def test_the_computer_refuses_to_start_on_a_bad_declaration(self):
        bad = copy.deepcopy(DECLARATIONS)
        next(d for d in bad if d["name"] == "navigator.execute")["writes_state"] = True
        d = mini_corpus.build(Path(TMP.name) / "c1")
        with mock.patch.object(caps, "DECLARATIONS", bad):
            with self.assertRaises(CapabilityError) as cm:
                Computer(Config(corpus=str(d), llm_mode="off", llm_endpoint="", log_file=""), Corpus(d).load())
        self.assertIn("navigator.execute", str(cm.exception))


class TestRegistryAtRunTime(unittest.TestCase):
    def test_undeclared_error_codes_are_refused(self):
        r = default_registry()
        self.assertEqual(r.error("logic.requirement", "CONFLICT", "x", False)["capability"], "logic.requirement@1.0.0")
        with self.assertRaises(CapabilityError):
            r.error("logic.calc", "CONFLICT", "x", False)

    def test_fallbacks_are_followed_and_recorded(self):
        r = default_registry()
        r.begin_turn()
        self.assertEqual(r.choose("logic.units_gnu"), "logic.units")
        self.assertEqual(r.turn.skipped, [{"name": "logic.units_gnu", "reason": "not implemented in this release"}])
        self.assertIn({"name": "logic.units", "version": "1.0.0"}, r.turn.used)

    def test_a_required_capability_that_is_off_raises(self):
        r = default_registry()
        r.disable("logic.units", "test")
        with self.assertRaises(Unavailable):
            r.choose("logic.units_gnu")

    def test_configuration_switches_things_off(self):
        r = default_registry(Config(llm_mode="off", state_file=""))
        for name in ("planner.lm", "state.working", "knowledge.lexicon", "planner.controller"):
            self.assertFalse(r.available(name)[0], name)
        r.begin_turn()
        self.assertEqual(r.choose("state.working"), "state.memory")


class TestEnvelope(unittest.TestCase):
    def test_numbers_are_normalised(self):
        a = {"status": "answered", "certainty": "ESTABLISHED", "value": 52}
        self.assertEqual(envelope.canonical_hash(a), envelope.canonical_hash({**a, "value": 52.0}))
        self.assertEqual(envelope.canonical_hash(a), envelope.canonical_hash({**a, "value": 5.2e1}))
        self.assertNotEqual(envelope.canonical_hash(a), envelope.canonical_hash({**a, "value": 52.0000001}))
        self.assertEqual(envelope.canonical_hash({"v": 0.0}), envelope.canonical_hash({"v": -0.0}))

    def test_volatile_fields_dont_count(self):
        a = {"status": "answered", "certainty": "ESTABLISHED", "ms": 3, "compute": {"cpu_ms": 1}, "trace": ["x"]}
        b = {"status": "answered", "certainty": "ESTABLISHED", "ms": 900, "compute": {"cpu_ms": 7}, "trace": ["y"]}
        self.assertEqual(envelope.canonical_hash(a), envelope.canonical_hash(b))
        self.assertEqual(envelope.canonical_hash({"x": {"ms": 1, "k": 1}}), envelope.canonical_hash({"x": {"ms": 2, "k": 1}}))

    def test_key_order_doesnt_count(self):
        self.assertEqual(envelope.canonical_hash({"a": 1, "b": [1, 2]}), envelope.canonical_hash({"b": [1, 2], "a": 1}))

    def test_weakest_label(self):
        self.assertEqual(envelope.weakest("ESTABLISHED", "INFERRED"), "INFERRED")
        self.assertEqual(envelope.weakest("INFERRED", "UNKNOWN", "ESTABLISHED"), "UNKNOWN")
        self.assertEqual(envelope.weakest("UNKNOWN", "CONFLICT"), "CONFLICT")

    def test_every_reply_carries_one(self):
        d = mini_corpus.build(Path(TMP.name) / "c2")
        computer = Computer(Config(corpus=str(d), llm_mode="off", llm_endpoint="", log_file=""), Corpus(d).load())
        for q, kind, certainty in (("what is 12 times 7", "calc", "ESTABLISHED"),
                                   ("What's the population of the capital of France?", "knowledge", "ESTABLISHED"),
                                   ("is a whale a mammal", "knowledge", "INFERRED"),
                                   ("How many times heavier is Jupiter than Saturn?", "knowledge", "ESTABLISHED"),
                                   ("What is the population density of France?", "knowledge", "UNKNOWN"),
                                   ("How long can a 50 Wh battery run a 5 W device?", "analysis", "INFERRED"),
                                   ("hello", "logic", "UNKNOWN"),
                                   ("what is the capital of narnia", "knowledge", "UNKNOWN")):
            e = computer.think(q).envelope
            self.assertEqual((e["kind"], e["certainty"]), (kind, certainty), q)
            self.assertTrue(e["canonical_hash"].startswith("sha256:"), q)
            self.assertIn(e["status"], envelope.STATUSES)
        e = computer.think("is a whale a mammal").envelope                       # INFERRED because of the IS_A chain
        self.assertEqual(e["truth"], "TRUE")
        self.assertEqual(e["inferences"], ["followed a chain to mammal"])

    def test_an_estimate_lists_what_was_said_and_assumed(self):
        d = mini_corpus.build(Path(TMP.name) / "c3")
        computer = Computer(Config(corpus=str(d), llm_mode="off", llm_endpoint="", log_file=""), Corpus(d).load())
        e = computer.think("How long can a 50 Wh battery run a 5 W device?").envelope
        self.assertEqual(e["value"], {"result": 8.5, "unit": "h"})
        self.assertIn("battery as stated: 50 Wh", e["assumptions"])
        self.assertTrue(any("efficiency" in a for a in e["assumptions"]))
        e = computer.think("Calculate the runtime.").envelope
        self.assertEqual((e["status"], e["certainty"], e["errors"][0]["code"]), ("unsupported", "UNKNOWN", "MISSING_FACT"))


class TestCorpusVersion(unittest.TestCase):
    def test_content_not_metadata(self):
        a = mini_corpus.build(Path(TMP.name) / "v_a")
        b = mini_corpus.build(Path(TMP.name) / "v_b")
        self.assertEqual(content_hash(a), content_hash(b))           # gzip bytes differ, content doesn't
        facts = list(mini_corpus.F)
        facts[0] = facts[0][:3] + ("GIANT_STAR",) + facts[0][4:]
        c = mini_corpus.build(Path(TMP.name) / "v_c", facts)
        self.assertEqual((a / "meta.json").read_text(), (c / "meta.json").read_text())
        self.assertNotEqual(content_hash(a), content_hash(c))

    def test_stored_hash_is_used_and_can_be_verified(self):
        a = mini_corpus.build(Path(TMP.name) / "v_d")
        meta = json.loads((a / "meta.json").read_text())
        meta["content_sha256"] = content_hash(a)
        (a / "meta.json").write_text(json.dumps(meta))
        c = Corpus(a).load()
        self.assertEqual(c.version, "sha256:" + meta["content_sha256"])
        self.assertTrue(c.verify_version()[0])
        mini_corpus.build(Path(TMP.name) / "v_d", mini_corpus.F[1:])   # the shards change, meta's hash is stale
        (a / "meta.json").write_text(json.dumps({**json.loads((a / "meta.json").read_text()),
                                                 "content_sha256": meta["content_sha256"]}))
        self.assertFalse(Corpus(a).load().verify_version()[0])

    def test_build_shards_writes_it(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import build_shards
        src = Path(TMP.name) / "corpus.pf"
        src.write_text("BATTERY_ALPHA\tENERGY_CAPACITY\tN\t0.052\tKWH\t0\t0\tMANUAL\t\t1.0\t\n")
        meta, _ = build_shards.build(src, Path(TMP.name) / "built", s_shards=2, v_shards=2, log=lambda *a: None)
        self.assertEqual(meta["content_sha256"], content_hash(Path(TMP.name) / "built"))
        src2 = Path(TMP.name) / "elsewhere.pf"
        src2.write_text(src.read_text())
        meta2, _ = build_shards.build(src2, Path(TMP.name) / "built2", s_shards=2, v_shards=2, log=lambda *a: None)
        self.assertEqual(meta["content_sha256"], meta2["content_sha256"])   # built_from (a path) isn't content


if __name__ == "__main__":
    unittest.main()
