"""The neural core (computer/neural): schema, serialization, invalid checkpoints, reproducibility, leakage, fallback,
promotion, the experience log, the evaluation, and that none of it touches the corpus or the deterministic engine.

Uses the tiny corpus (tests/mini_corpus.py) and the hand-written fixtures in tests/.  Training needs numpy; without it
the training-dependent tests are skipped (loading and fallback don't need it)."""

import copy
import hashlib
import io
import json
import random
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import mini_corpus                                                               # noqa: E402
from computer import capabilities as caps                                        # noqa: E402
from computer import envelope                                                    # noqa: E402
from computer.config import Config                                               # noqa: E402
from computer.controller import ACTION_NAMES, ControllerPlanner, Network, hashed  # noqa: E402
from computer.corpus import Corpus                                               # noqa: E402
from computer.core import Computer                                               # noqa: E402
from computer.neural import experience as X                                      # noqa: E402
from computer.neural import registry as R                                        # noqa: E402
from computer.neural.model import ModelError, check_network, load_model_dir, sha256_bytes  # noqa: E402
from computer.neural.records import explore_detailed, norm_q                     # noqa: E402
from computer.neural.runtime import NeuralPlanner, NeuralUnavailable, from_template, load_active  # noqa: E402
from computer.neural.schema import SCHEMA_VERSION, describe, encode, fingerprint  # noqa: E402
from computer.plan import parse                                                  # noqa: E402

try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

TMP = tempfile.TemporaryDirectory()
DATA = mini_corpus.build(Path(TMP.name) / "data")
HELD = [str(ROOT / "tests" / f) for f in ("controller_heldout.jsonl", "lexicon_heldout.jsonl")]
REGR = [str(ROOT / "tests" / f) for f in ("navigator_questions.jsonl", "lexical_questions.jsonl",
                                          "lexical_paraphrases.jsonl")]
GOLDEN = json.loads((ROOT / "tests" / "neural_features_golden.json").read_text())
QUIET = lambda *a: None                                                           # noqa: E731
_S = {}


def tree_digest(base) -> dict:
    """Every file under a folder: its bytes' hash and mtime."""
    out = {}
    for p in sorted(Path(base).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(base))] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    return out


CORPUS_BEFORE = tree_digest(DATA)
VERSION_BEFORE = Corpus(DATA).load().version


def cfg(**kw):
    base = dict(corpus=str(DATA), llm_mode="off", llm_endpoint="", log_file="", navigator_controller="")
    base.update(kw)
    return Config(**base)


def staged():
    """Train and evaluate once for the module (a few seconds on the tiny corpus)."""
    if "id" not in _S:
        from computer.neural.evaluate import evaluate
        from computer.neural.train import train_to_staging
        reg = Path(TMP.name) / "reg"
        d = train_to_staging(reg, DATA, heldout=HELD, regression=REGR, log=QUIET)
        rep = evaluate(reg, d.name, DATA, HELD, REGR, log=QUIET)
        _S.update(reg=reg, dir=d, id=d.name, report=rep)
    return _S


def fresh_copy(name: str) -> Path:
    """A copy of the trained registry (staging + eval, nothing active)."""
    s = staged()
    dest = Path(TMP.name) / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(s["reg"], dest)
    return dest


def active_registry() -> Path:
    if "areg" not in _S:
        areg = fresh_copy("areg")
        R.promote(areg, staged()["id"], approve=True)
        _S["areg"] = areg
    return _S["areg"]


def model_dict():
    return json.loads((staged()["dir"] / "model.json").read_text())


# ---------------------------------------------------------------------- schema

class TestSchema(unittest.TestCase):
    def test_the_fingerprint_is_the_one_the_golden_file_was_made_with(self):
        self.assertEqual(GOLDEN["schema_version"], SCHEMA_VERSION)
        self.assertEqual(GOLDEN["fingerprint"], fingerprint(),
                         "the schema changed: bump SCHEMA_VERSION, regenerate tests/neural_features_golden.json, retrain")

    def test_features_are_what_schema_v1_says(self):
        ctl = ControllerPlanner(Corpus(DATA).load())
        for g in GOLDEN["questions"]:
            f, idx = encode(ctl.reader.read(g["question"]), ctl.reader)
            self.assertEqual(f, g["features"], g["question"])
            self.assertEqual(idx, g["idx"], g["question"])

    def test_the_fingerprint_covers_the_actions_and_what_they_do(self):
        d = describe()
        self.assertNotEqual(fingerprint({**d, "actions": d["actions"] + ["add_fact"]}), fingerprint())
        t = copy.deepcopy(d)
        t["templates"]["hop"] = ["$1 = FIND {e1}", "ANSWER $1"]
        self.assertNotEqual(fingerprint(t), fingerprint())
        self.assertNotEqual(fingerprint({**d, "dim": d["dim"] * 2}), fingerprint())


# ---------------------------------------------------------------------- serialization

@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestSerialization(unittest.TestCase):
    def test_a_stored_model_reads_back_the_same(self):
        from computer.neural.train import model_bytes
        d = staged()["dir"]
        a, m = load_model_dir(d)
        b, _ = load_model_dir(d)
        self.assertEqual(model_bytes(a), (d / "model.json").read_bytes())       # canonical: re-saving changes nothing
        for g in GOLDEN["questions"]:
            self.assertEqual(a.predict(g["idx"]), b.predict(g["idx"]))
        self.assertEqual(m["model_sha256"], sha256_bytes((d / "model.json").read_bytes()))

    def test_the_manifest_says_what_it_is_and_how_it_was_made(self):
        _, m = load_model_dir(staged()["dir"])
        self.assertEqual(m["schema"], {"name": "cdos-neural-features", "version": SCHEMA_VERSION, "fingerprint": fingerprint()})
        self.assertTrue(m["training_data"]["sha256"].startswith("sha256:"))
        self.assertEqual(m["training_data"]["corpus_version"], VERSION_BEFORE)
        for k in ("hidden", "epochs", "lr", "l2", "batch", "seed", "data_seed", "dim"):
            self.assertIn(k, m["hyperparameters"])
        for k in ("neural_version", "code_sha256", "python", "numpy", "platform"):
            self.assertIn(k, m["software"])
        for k in ("train_top1", "heldout_top1", "untrained_heldout_top1", "chance_heldout_top1"):
            self.assertIn(k, m["metrics"])
        for k in ("wall_seconds", "cpu_seconds", "peak_rss_mb"):
            self.assertIn(k, m["resources"])
        self.assertEqual(m["actions"], list(ACTION_NAMES))

    def test_records_say_what_was_tried_and_why_it_was_the_label(self):
        from computer.neural.train import read_records
        recs = read_records(staged()["dir"] / "records.jsonl.gz")
        with_target = [r for r in recs if r["split"] == "train" and r["targets"] and r["candidates"]]
        self.assertTrue(with_target)
        r = with_target[0]
        for k in ("inputs", "candidates", "targets", "label_rule", "provenance", "corpus_version"):
            self.assertIn(k, r)
        self.assertEqual(hashed(r["inputs"]["features"]), r["inputs"]["idx"])
        won = [c for c in r["candidates"] if c.get("outcome") == "reached the answer"]
        self.assertTrue(won and {c["action"] for c in won} >= set(r["targets"]))
        self.assertTrue(all(c["validator"] == "ok" for c in won))
        self.assertTrue(any(c.get("status") for c in r["candidates"]))           # the executor's outcome is kept
        self.assertIn("source", r)
        self.assertIn(r["source"], ("teacher", "reworded", "mine"))

    def test_the_data_hash_is_the_records_file(self):
        import gzip
        from computer.neural.records import dataset_hash
        from computer.neural.train import read_records
        d = staged()["dir"]
        _, m = load_model_dir(d)
        raw = gzip.decompress((d / "records.jsonl.gz").read_bytes())
        self.assertEqual(m["training_data"]["sha256"], sha256_bytes(raw))
        self.assertEqual(m["training_data"]["sha256"], dataset_hash(read_records(d / "records.jsonl.gz")))
        self.assertEqual(m["training_data"]["records_file_sha256"], sha256_bytes((d / "records.jsonl.gz").read_bytes()))

    def test_a_failed_run_leaves_nothing_half_made(self):
        from computer.neural.train import TrainingError, train_to_staging
        reg = Path(TMP.name) / "reg-fails"
        with mock.patch("computer.neural.train.train_network", side_effect=TrainingError("stop here")):
            with self.assertRaises(TrainingError):
                train_to_staging(reg, DATA, epochs=1, log=QUIET)
        self.assertEqual(list((reg / "staging").iterdir()), [])

    def test_int8_weights_choose_like_float(self):
        from computer.compute import QuantNetwork
        from computer.neural.model import probe
        from computer.neural.records import rows_of
        from computer.neural.train import read_records
        net, _ = load_model_dir(staged()["dir"])
        q = QuantNetwork.from_network(net, 8)
        probe(q, list(q.actions))
        rows = rows_of(read_records(staged()["dir"] / "records.jsonl.gz"), "heldout")
        top = lambda m, r: max(m.predict(r["idx"]).items(), key=lambda kv: kv[1])[0]   # noqa: E731
        same = sum(top(net, r) == top(q, r) for r in rows) / len(rows)
        self.assertGreater(same, 0.9)


# ---------------------------------------------------------------------- invalid checkpoints

@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestInvalidCheckpoints(unittest.TestCase):
    """Every way a model can be wrong: refused at load with a reason, and the computer starts with the rules."""

    def broken(self, mutate, name):
        """A registry whose active model has been damaged by mutate(model_dir, manifest dict, model dict)."""
        s = staged()
        reg = Path(TMP.name) / f"broken-{name}"
        if reg.exists():
            shutil.rmtree(reg)
        d = reg / "models" / s["id"]
        shutil.copytree(s["dir"], d)
        R.set_active(reg, s["id"], None)
        man = json.loads((d / "manifest.json").read_text())
        mod = json.loads((d / "model.json").read_text())
        mutate(d, man, mod)
        return reg

    def write_model(self, d, man, mod):
        data = json.dumps(mod).encode()
        (d / "model.json").write_bytes(data)
        man["model_sha256"] = sha256_bytes(data)          # the hash is right: the content itself is what's wrong
        (d / "manifest.json").write_text(json.dumps(man))

    def refused(self, reg, *why):
        with self.assertRaises(NeuralUnavailable) as cm:
            load_active(reg, corpus_base=DATA)
        for w in why:
            self.assertIn(w, cm.exception.reason)
        c = Computer(cfg(neural_registry=str(reg)))
        self.assertEqual([p.label for p in c.planners], ["rules"])
        self.assertIn("neural core not loaded", c.neural_error)
        self.assertFalse(c.registry.available("planner.neural")[0])
        r = c.think("What's the population of the capital of France?")
        self.assertIn("2,100,000", r.text)
        return cm.exception.reason

    def test_no_registry(self):
        self.refused(Path(TMP.name) / "nowhere", "no neural registry")

    def test_no_active_model(self):
        reg = fresh_copy("inactive")
        self.refused(reg, "no active model")

    def test_damaged_activation_record(self):
        self.refused(self.broken(lambda d, man, mod: (d.parent.parent / "ACTIVE.json").write_text("{nope"), "active"),
                     "ACTIVE.json")

    def test_activation_record_pointing_outside(self):
        def m(d, man, mod):
            (d.parent.parent / "ACTIVE.json").write_text(json.dumps({"format": "cdos-neural-active/1",
                                                                     "model_id": "../staging/x"}))
        self.refused(self.broken(m, "outside"), "invalid model id")

    def test_activation_record_naming_a_missing_model(self):
        def m(d, man, mod):
            (d.parent.parent / "ACTIVE.json").write_text(json.dumps({"format": "cdos-neural-active/1",
                                                                     "model_id": "nc1-000000000000"}))
        self.refused(self.broken(m, "missing"), "missing")

    def test_truncated_model_file(self):
        def m(d, man, mod):
            b = (d / "model.json").read_bytes()
            (d / "model.json").write_bytes(b[: len(b) // 2])
        self.refused(self.broken(m, "truncated"), "doesn't match the hash")

    def test_one_flipped_byte(self):
        def m(d, man, mod):
            b = bytearray((d / "model.json").read_bytes())
            b[len(b) // 2] ^= 0x01
            (d / "model.json").write_bytes(bytes(b))
        self.refused(self.broken(m, "flipped"), "doesn't match the hash")

    def test_not_json(self):
        def m(d, man, mod):
            data = b"\x00\x01 not json"
            (d / "model.json").write_bytes(data)
            man["model_sha256"] = sha256_bytes(data)
            (d / "manifest.json").write_text(json.dumps(man))
        self.refused(self.broken(m, "notjson"), "isn't valid JSON")

    def test_a_row_of_the_wrong_length(self):
        def m(d, man, mod):
            k = next(iter(mod["rows"]))
            mod["rows"][k] = mod["rows"][k] + [0.1]
            self.write_model(d, man, mod)
        self.refused(self.broken(m, "rowlen"), "corrupt", f"row")

    def test_a_row_outside_the_input(self):
        def m(d, man, mod):
            mod["rows"]["99999"] = [0.0] * mod["hidden"]
            self.write_model(d, man, mod)
        self.refused(self.broken(m, "rowidx"), "row index")

    def test_numbers_that_arent(self):
        for i, bad in enumerate((float("nan"), float("inf"), True, "1", 1e9)):
            def m(d, man, mod, bad=bad):
                mod["b2"][0] = bad
                self.write_model(d, man, mod)
            self.refused(self.broken(m, f"num{i}"), "b2")

    def test_an_action_this_code_doesnt_have(self):
        def m(d, man, mod):
            mod["actions"] = mod["actions"][:-1] + ["add_fact"]
            self.write_model(d, man, mod)
        self.assertIn("add_fact", self.refused(self.broken(m, "action"), "incompatible"))

    def test_actions_in_another_order(self):
        def m(d, man, mod):
            mod["actions"] = list(reversed(mod["actions"]))
            self.write_model(d, man, mod)
        self.refused(self.broken(m, "order"), "incompatible")

    def test_another_input_size(self):
        def m(d, man, mod):
            mod["dim"] = 8192
            self.write_model(d, man, mod)
        self.refused(self.broken(m, "dim"), "incompatible", "8192")

    def test_another_schema(self):
        def m(d, man, mod):
            man["schema"]["fingerprint"] = "sha256:" + "1" * 64
            (d / "manifest.json").write_text(json.dumps(man))
        self.refused(self.broken(m, "schema"), "incompatible", "feature schema")

    def test_a_file_name_that_leaves_the_folder(self):
        def m(d, man, mod):
            man["model_file"] = "../../../model.json"
            (d / "manifest.json").write_text(json.dumps(man))
        self.refused(self.broken(m, "traversal"), "plain file name")

    def test_no_manifest(self):
        self.refused(self.broken(lambda d, man, mod: (d / "manifest.json").unlink(), "nomanifest"), "missing", "manifest")

    def test_a_registry_inside_the_corpus(self):
        with self.assertRaises(NeuralUnavailable) as cm:
            load_active(DATA / "neural", corpus_base=DATA)
        self.assertIn("corpus", cm.exception.reason)
        from computer.neural.train import TrainingError, train_to_staging
        with self.assertRaises(TrainingError):
            train_to_staging(DATA / "neural", DATA, log=QUIET)
        self.assertFalse((DATA / "neural").exists())

    def test_check_network_refuses_what_it_should(self):
        good = model_dict()
        check_network(copy.deepcopy(good), list(ACTION_NAMES), 4096)
        for mutate in (lambda d: d.update(format="cdos-owl-controller/1"), lambda d: d.update(rows=[]),
                       lambda d: d.update(hidden=0), lambda d: d.update(hidden=5000), lambda d: d.update(w2=d["w2"][:-1]),
                       lambda d: d.update(b1=d["b1"] + [0.0]), lambda d: d.update(actions=[1, 2])):
            bad = copy.deepcopy(good)
            mutate(bad)
            with self.assertRaises(ModelError):
                check_network(bad, list(ACTION_NAMES), 4096)


# ---------------------------------------------------------------------- reproducibility

@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestReproducibility(unittest.TestCase):
    def train(self, name, **kw):
        from computer.neural.train import train_to_staging
        reg = Path(TMP.name) / f"repro-{name}"
        if reg.exists():
            shutil.rmtree(reg)
        d = train_to_staging(reg, DATA, epochs=kw.pop("epochs", 6), heldout=HELD, regression=REGR, log=QUIET, **kw)
        return d, json.loads((d / "manifest.json").read_text())

    def test_same_seed_same_bytes(self):
        d1, m1 = self.train("a")
        d2, m2 = self.train("b")
        self.assertEqual(d1.name, d2.name)
        self.assertEqual(m1["model_sha256"], m2["model_sha256"])
        self.assertEqual(m1["untrained_sha256"], m2["untrained_sha256"])
        self.assertEqual(m1["training_data"]["sha256"], m2["training_data"]["sha256"])
        self.assertEqual((d1 / "records.jsonl.gz").read_bytes(), (d2 / "records.jsonl.gz").read_bytes())
        for f in (d1 / "fixtures").iterdir():
            self.assertEqual(f.read_bytes(), (d2 / "fixtures" / f.name).read_bytes())

    def test_the_training_seed_changes_the_weights_not_the_data(self):
        _, m0 = self.train("seed0", seed=0, data_seed=0)
        _, m1 = self.train("seed1", seed=1, data_seed=0)
        self.assertNotEqual(m0["model_sha256"], m1["model_sha256"])
        self.assertEqual(m0["training_data"]["sha256"], m1["training_data"]["sha256"])

    def test_it_is_the_existing_trainer(self):
        from computer.controller_train import train as old_train
        from computer.neural.records import rows_of
        from computer.neural.train import read_records, train_network
        rows = rows_of(read_records(staged()["dir"] / "records.jsonl.gz"), "train")
        old = old_train(rows, epochs=3, seed=5)
        new, untrained, info = train_network(rows, epochs=3, seed=5, batch=32)
        self.assertEqual(old.to_json(), new.to_json())
        self.assertNotEqual(untrained.to_json(), new.to_json())
        self.assertEqual(info["epochs_completed"], 3)

    def test_labels_are_the_existing_labels(self):
        from computer.controller_train import explore, reword
        from computer.navdata import generate
        c = Corpus(DATA).load()
        ctl = ControllerPlanner(c)
        rng = random.Random(0)
        exs = []
        for e in generate(c, 5000)[:60]:
            exs += [e] + reword(e, rng, 2)
        for e in exs:
            if e["expect"] != "answered":
                continue
            old, runs_old = explore(e, ctl)
            new, runs_new, cands = explore_detailed(e, ctl)
            self.assertEqual((old, runs_old), (new, runs_new), e["question"])
            self.assertTrue(cands)

    def test_limits_stop_training_and_say_so(self):
        from computer.neural.records import rows_of
        from computer.neural.train import read_records, train_network
        rows = rows_of(read_records(staged()["dir"] / "records.jsonl.gz"), "train")
        _, _, info = train_network(rows, epochs=500, max_seconds=0.0)
        self.assertIn("wall-time", info["stopped_early"])
        self.assertLess(info["epochs_completed"], 1)
        _, _, info = train_network(rows, epochs=500, max_cpu_seconds=0.0, batch=8)
        self.assertIn("CPU-time", info["stopped_early"])
        _, _, info = train_network(rows, epochs=2, batch=7)
        self.assertEqual((info["epochs_completed"], info["stopped_early"]), (2, ""))


# ---------------------------------------------------------------------- leakage

@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestLeakage(unittest.TestCase):
    def records(self):
        from computer.neural.train import read_records
        return read_records(staged()["dir"] / "records.jsonl.gz")

    def test_no_held_out_subject_in_training(self):
        recs = self.records()
        _, m = load_model_dir(staged()["dir"])
        held = {s for r in recs if r["split"] == "heldout" for s in r["provenance"]["subjects"]}
        self.assertTrue(held)
        for r in recs:
            if r["split"] == "train":
                self.assertFalse(held & set(r["provenance"]["concepts"]), r["question"])
        self.assertEqual(m["training_data"]["leak_check"]["concept_leaks"], 0)

    def test_no_held_out_or_regression_question_is_trained_on(self):
        recs = self.records()
        train_q = {norm_q(r["question"]) for r in recs if r["split"] == "train"}
        for f in list((staged()["dir"] / "fixtures").iterdir()) + [Path(p) for p in HELD + REGR]:
            for line in f.read_text().splitlines():
                if line.strip():
                    self.assertNotIn(norm_q(json.loads(line)["question"]), train_q, f.name)

    def test_held_out_phrasings_are_never_trained_on(self):
        """Every question the held-out half of the templates can make about a held-out subject: none is a training
        question, and every B question is one of them.  (About *training* subjects the two halves do meet once
        normalised: "{s} {t}" and "{s}'s {t}?" both give "moon radius".  That's the existing template split, and it
        doesn't reach B, whose subjects are never trained on; docs/NEURAL_CORE.md lists it.)"""
        from computer.controller_train import reword
        from computer.navdata import generate, leak_free
        teacher, _ = leak_free(generate(Corpus(DATA).load(), 3000, seed=0))
        by_split = {"train": set(), "held": set()}
        for e in teacher:
            qs = {norm_q(r["question"]) for r in reword(e, random.Random(0), 99, which="test")}
            by_split["train" if e["split"] == "train" else "held"] |= qs
        recs = self.records()
        train_q = {norm_q(r["question"]) for r in recs if r["split"] == "train"}
        self.assertTrue(by_split["train"] and by_split["held"])
        self.assertEqual(train_q & by_split["held"], set())
        b = [r for r in recs if r["source"] == "B"]
        self.assertTrue(b)
        self.assertTrue(all(norm_q(r["question"]) in by_split["held"] for r in b))
        self.assertTrue(any(r["source"] == "reworded" for r in recs if r["split"] == "train"))

    def test_your_own_question_that_is_a_test_question_is_refused(self):
        from computer.neural.train import TrainingError, train_to_staging
        mine = Path(TMP.name) / "mine_leak.jsonl"
        q = json.loads(Path(HELD[0]).read_text().splitlines()[0])
        mine.write_text(json.dumps({**q, "question": q["question"].upper() + "?"}) + "\n")
        with self.assertRaises(TrainingError) as cm:
            train_to_staging(Path(TMP.name) / "reg-mine-leak", DATA, epochs=1, mine=[mine], heldout=HELD,
                             regression=REGR, log=QUIET)
        self.assertIn("held-out", str(cm.exception))

    def test_your_own_question_about_a_held_out_thing_is_dropped(self):
        from computer.neural.train import train_to_staging
        held = sorted({s for r in self.records() if r["split"] == "heldout" for s in r["provenance"]["subjects"]})
        ctl = ControllerPlanner(Corpus(DATA).load())
        name = next(k.lower().replace("_", " ") for k in held       # one the reader takes as a name, not a relation word
                    if [s.phrase for s in ctl.reader.read(f"tell me about {k.lower().replace('_', ' ')}").spans
                        if not s.relish] == [k.lower().replace("_", " ")])
        mine = Path(TMP.name) / "mine_held.jsonl"
        mine.write_text(json.dumps({"question": f"tell me something about {name} please", "gold": ["x"],
                                    "expect": "answered", "kind": "hop"}) + "\n" +
                        json.dumps({"question": "france capital city please", "gold": ["paris"], "expect": "answered",
                                    "kind": "hop"}) + "\n")
        d = train_to_staging(Path(TMP.name) / "reg-mine-held", DATA, epochs=1, mine=[mine], heldout=HELD,
                             regression=REGR, log=QUIET)
        td = json.loads((d / "manifest.json").read_text())["training_data"]
        if "FRANCE" in held:
            self.assertEqual(td["mine_dropped_for_held_out_subjects"], 2)
        else:
            self.assertEqual((td["mine_questions"], td["mine_dropped_for_held_out_subjects"]), (2, 1))


# ---------------------------------------------------------------------- fallback

@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestFallback(unittest.TestCase):
    def test_a_model_that_fails_at_answer_time_is_switched_off(self):
        c = Computer(cfg(neural_registry=str(active_registry())))
        self.assertEqual([p.label for p in c.planners], ["rules", "neural"])
        with mock.patch.object(c.neural.model, "predict", side_effect=RuntimeError("boom")):
            r = c.think("What's the population of the capital of France?")
            self.assertIn("2,100,000", r.text)
            self.assertIn("boom", c.neural.failed)
            r = c.think("jupiter vs saturn, which is heavier")          # a turn later: no exception, no model
            self.assertEqual(r.faculty, "knowledge")
        self.assertEqual(c.neural.plans("owl diet"), [])

    def test_an_unexpected_error_while_loading_doesnt_stop_start_up(self):
        with mock.patch("computer.neural.runtime.load_active", side_effect=MemoryError("no room")):
            c = Computer(cfg(neural_registry=str(active_registry())))
        self.assertIsNone(c.neural)
        self.assertIn("no room", c.neural_error)
        self.assertIn("84", c.think("what is 12 times 7").text)

    def test_an_unwritable_experience_log_costs_the_log_not_the_answer(self):
        reg = fresh_copy("unwritable")
        R.promote(reg, staged()["id"], approve=True)
        (reg / "experience").mkdir()
        (reg / "experience" / "experience.jsonl").mkdir()            # a folder where the file should be
        c = Computer(cfg(neural_registry=str(reg)))
        self.assertIsNotNone(c.neural)
        r = c.think("What's the population of the capital of France?")
        self.assertIn("2,100,000", r.text)

    def test_off_by_default(self):
        c = Computer(cfg())
        self.assertIsNone(c.neural)
        self.assertEqual(c.neural_error, "")
        self.assertFalse(c.registry.available("planner.neural")[0])
        self.assertFalse(c.registry.available("neural.experience")[0])


# ---------------------------------------------------------------------- non-interference

@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestNonInterference(unittest.TestCase):
    def test_rule_answers_and_their_envelopes_are_unchanged(self):
        off = Computer(cfg())
        on = Computer(cfg(neural_registry=str(active_registry()), neural_experience=False))
        n = 0
        for path in REGR:
            for line in Path(path).read_text().splitlines():
                if not line.strip():
                    continue
                q = json.loads(line)["question"]
                a, b = off.think(q), on.think(q)
                self.assertEqual(a.text, b.text, q)
                self.assertEqual(a.envelope["canonical_hash"], b.envelope["canonical_hash"], q)
                n += 1
        self.assertGreater(n, 90)

    def test_safety_routing_and_calculations_are_unchanged(self):
        off = Computer(cfg())
        on = Computer(cfg(neural_registry=str(active_registry()), neural_experience=False))
        for q in ("what is 12 times 7", "what's 15% of 80", "should I stop taking my medicine?",
                  "should I put my savings into bitcoin?", "I want to hurt myself", "turn on the lights",
                  "search the internet for news", "hello", "How long can a 50 Wh battery run a 5 W device?",
                  "And at half the load?", "how many times heavier is jupiter than saturn",
                  "What is the population density of France?"):
            a, b = off.think(q), on.think(q)
            self.assertEqual((a.faculty, a.route.intent, a.text), (b.faculty, b.route.intent, b.text), q)
            self.assertEqual(a.envelope["canonical_hash"], b.envelope["canonical_hash"], q)
        for q in ("should I learn the violin?", "say something wise"):     # the sampler: random, but still the sampler
            self.assertEqual(off.think(q).faculty, on.think(q).faculty)

    def test_neural_answers_keep_the_evidence_and_the_envelope(self):
        c = Computer(cfg(neural_registry=str(active_registry()), neural_experience=False))
        r = c.think("jupiter vs saturn, which is heavier")
        plan, res = c.last_navigation
        self.assertTrue(plan.source.startswith("neural:"))
        e = r.envelope
        self.assertEqual(e["status"], "answered")
        self.assertIn({"name": "planner.neural", "version": "0.1.0"}, e["capabilities"])
        self.assertTrue(e["evidence"])
        self.assertEqual({f["corpus_version"] for f in e["evidence"]}, {VERSION_BEFORE})
        corpus = Corpus(DATA).load()
        for f in e["evidence"]:                       # every fact cited is one the corpus holds, as stored
            held = {x.id for x in corpus.facts_for(f["subject"])}
            self.assertIn(f["id"], held)
        self.assertEqual(e["canonical_hash"], envelope.canonical_hash(e))

    def test_the_model_changes_nothing_but_adding_checked_answers(self):
        """With the model, each reply is either exactly what the computer says without it (text and envelope), or an
        answer from a neural plan that ran, answered and passed accept()."""
        off = Computer(cfg())
        on = Computer(cfg(neural_registry=str(active_registry()), neural_experience=False))
        qs = ["what is the capital of narnia", "things that orbit ganymede", "food of france", "zibble glorb meaning",
              "seat of government of germany", "nourishment of an owl"]
        qs += [json.loads(l)["question"] for f in HELD for l in Path(f).read_text().splitlines() if l.strip()]
        same = added = 0
        for q in qs:
            off.context.clear(), on.context.clear()
            off.corpus.last_subject = on.corpus.last_subject = ""
            a, b = off.think(q), on.think(q)
            plan, res = on.last_navigation or (None, None)
            if not (plan is not None and plan.source.startswith("neural:") and on._said_by == "navigator"):
                self.assertEqual(a.text, b.text, q)
                self.assert_same_but_the_executor_ran(a.envelope, b.envelope, q)
                same += 1
                continue
            self.assertTrue(res.answered, q)
            self.assertEqual(b.envelope["status"], "answered", q)
            self.assertEqual(on.neural.last["accept"].get(str(plan)), "", q)
            self.assertIn({"name": "planner.neural", "version": "0.1.0"}, b.envelope["capabilities"])
            added += 1
        self.assertGreater(same, 10)
        self.assertGreater(added, 10)

    def assert_same_but_the_executor_ran(self, a: dict, b: dict, q: str):
        """Two envelopes for the same reply: equal, except that with the model the executor may have run (the neural
        plans that found no checked answer), and the envelope says so."""
        strip = lambda e: {k: v for k, v in e.items() if k not in ("capabilities", "canonical_hash")}   # noqa: E731
        self.assertEqual(strip(a), strip(b), q)
        extra = [c for c in b["capabilities"] if c not in a["capabilities"]]
        self.assertEqual([c for c in a["capabilities"] if c not in b["capabilities"]], [], q)
        self.assertTrue(set(c["name"] for c in extra) <= {"navigator.execute"}, (q, extra))

    def test_a_neural_non_answer_is_not_said(self):
        off = Computer(cfg())
        on = Computer(cfg(neural_registry=str(active_registry()), neural_experience=False))
        q = "what is the capital of narnia"
        a, b = off.think(q), on.think(q)
        self.assertTrue(b.text.startswith("NOT FOUND"), b.text)
        self.assert_same_but_the_executor_ran(a.envelope, b.envelope, q)
        self.assertIn("not said", " ".join(b.trace))
        self.assertEqual((b.envelope["status"], b.envelope["certainty"]), ("unsupported", "UNKNOWN"))

    def test_the_planner_only_proposes_template_plans(self):
        c = Corpus(DATA).load()
        net, man = load_model_dir(active_registry() / "models" / staged()["id"])
        p = NeuralPlanner(c, net, man, min_p=0.0, max_actions=len(ACTION_NAMES), max_plans=99)
        rels = set(c.meta["relation_names"])
        qs = [json.loads(l)["question"] for f in HELD for l in Path(f).read_text().splitlines() if l.strip()]
        n = 0
        for q in qs:
            for pl in p.plans(q):
                parse(str(pl), rels)
                self.assertTrue(from_template(pl, pl.source), (q, str(pl)))
                n += 1
        self.assertGreater(n, 50)
        bad = parse("$1 = FIND france\n$2 = GET $1 ~capital\n$3 = GET $2 ~population\nANSWER $3")
        self.assertFalse(from_template(bad, "neural:hop 0.9"))
        self.assertFalse(from_template(bad, "neural:add_fact 0.9"))
        self.assertTrue(from_template(bad, "neural:chain 0.9"))

    def test_training_never_touches_the_active_model(self):
        from computer.neural.evaluate import evaluate
        from computer.neural.train import train_to_staging
        reg = fresh_copy("busy")
        R.promote(reg, staged()["id"], approve=True)
        before = (tree_digest(reg / "models"), (reg / "ACTIVE.json").read_bytes())
        d = train_to_staging(reg, DATA, seed=3, epochs=4, heldout=HELD, regression=REGR, log=QUIET)
        rep = evaluate(reg, d.name, DATA, HELD, REGR, log=QUIET)
        self.assertEqual(before, (tree_digest(reg / "models"), (reg / "ACTIVE.json").read_bytes()))
        self.assertEqual(rep["active_at_eval"], staged()["id"])
        self.assertIn("rules+active", rep["results"]["B_heldout_unseen_phrasings"])
        # the active model changed since that evaluation: it has to be evaluated again before promotion
        R.deactivate(reg, approve=True)
        with self.assertRaises(R.PromotionRefused) as cm:
            R.promote(reg, d.name, approve=True)
        self.assertIn("evaluate again", str(cm.exception))

    def test_no_network_anywhere(self):
        """Training, evaluating and answering with the model, with every socket connection refused."""
        import socket
        from computer.neural.evaluate import evaluate
        from computer.neural.train import train_to_staging

        def blocked(*a, **kw):
            raise AssertionError(f"network call attempted: {a[1:] if len(a) > 1 else a}")
        with mock.patch.object(socket.socket, "connect", blocked), mock.patch.object(socket, "create_connection", blocked):
            reg = Path(TMP.name) / "reg-offline"
            d = train_to_staging(reg, DATA, epochs=3, heldout=HELD, regression=REGR, log=QUIET)
            evaluate(reg, d.name, DATA, HELD, REGR, log=QUIET)
            c = Computer(cfg(neural_registry=str(active_registry()), neural_experience=False))
            self.assertIn("jupiter is heavier", c.think("jupiter vs saturn, which is heavier").text.lower())

    def test_a_promoted_model_is_never_rewritten_by_evaluation(self):
        from computer.neural.evaluate import evaluate
        reg = fresh_copy("reeval-promoted")
        mid = staged()["id"]
        R.promote(reg, mid, approve=True)
        shutil.rmtree(reg / "staging" / mid)
        before = tree_digest(reg / "models")
        evaluate(reg, mid, DATA, HELD, REGR, log=QUIET)
        self.assertEqual(tree_digest(reg / "models"), before)
        self.assertEqual(len(list((reg / "evaluations").glob(f"{mid}-*.json"))), 1)

    def test_zz_the_corpus_is_untouched_by_all_of_it(self):
        staged()
        active_registry()
        c = Computer(cfg(neural_registry=str(active_registry())))
        for q in ("owl diet", "jupiter vs saturn, which is heavier", "meaning: cheerful"):
            c.think(q)
        self.assertEqual(tree_digest(DATA), CORPUS_BEFORE)
        self.assertEqual(Corpus(DATA).load().version, VERSION_BEFORE)


# ---------------------------------------------------------------------- promotion

@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestPromotion(unittest.TestCase):
    def test_nothing_is_activated_without_approval(self):
        reg = fresh_copy("noapprove")
        out = R.promote(reg, staged()["id"], approve=False)
        self.assertIn("approval required", out["status"])
        self.assertFalse((reg / "ACTIVE.json").exists())
        self.assertFalse((reg / "models").exists())
        buf = io.StringIO()
        from computer.neural.__main__ import main
        with redirect_stdout(buf):
            self.assertEqual(main(["promote", "--registry", str(reg), "--model", staged()["id"]]), 3)
        self.assertFalse((reg / "ACTIVE.json").exists())

    def test_an_unevaluated_model_is_refused(self):
        reg = fresh_copy("noeval")
        (reg / "staging" / staged()["id"] / "eval.json").unlink()
        with self.assertRaises(R.PromotionRefused) as cm:
            R.promote(reg, staged()["id"], approve=True)
        self.assertIn("evaluated", str(cm.exception))

    def test_a_failed_gate_is_refused(self):
        reg = fresh_copy("failgate")
        p = reg / "staging" / staged()["id"] / "eval.json"
        ev = json.loads(p.read_text())
        ev["gates"]["no_rule_regressions"] = {"pass": False, "why": "1 rule-right answer lost"}
        p.write_text(json.dumps(ev))
        with self.assertRaises(R.PromotionRefused) as cm:
            R.promote(reg, staged()["id"], approve=True)
        self.assertIn("no_rule_regressions", str(cm.exception))

    def test_an_evaluation_of_other_bytes_is_refused(self):
        reg = fresh_copy("otherbytes")
        p = reg / "staging" / staged()["id"] / "eval.json"
        ev = json.loads(p.read_text())
        ev["model_sha256"] = "sha256:" + "2" * 64
        p.write_text(json.dumps(ev))
        with self.assertRaises(R.PromotionRefused) as cm:
            R.promote(reg, staged()["id"], approve=True)
        self.assertIn("different files", str(cm.exception))

    def test_promotion_activates_audits_and_can_be_undone(self):
        reg = fresh_copy("lifecycle")
        mid = staged()["id"]
        out = R.promote(reg, mid, approve=True)
        self.assertEqual(out["status"], "activated")
        self.assertEqual(json.loads((reg / "ACTIVE.json").read_text())["model_id"], mid)
        for f in (reg / "models" / mid).rglob("*"):
            if f.is_file():
                self.assertEqual(f.stat().st_mode & 0o222, 0, f)             # promoted files are read-only
        with self.assertRaises(R.PromotionRefused):
            R.promote(reg, mid, approve=True)                                # already promoted
        self.assertIn("approval required", R.deactivate(reg)["status"])
        R.deactivate(reg, approve=True)
        self.assertIsNone(Computer(cfg(neural_registry=str(reg))).neural)
        self.assertIn("approval required", R.rollback(reg, mid)["status"])
        R.rollback(reg, mid, approve=True)
        self.assertIsNotNone(Computer(cfg(neural_registry=str(reg))).neural)
        audit = [json.loads(l) for l in (reg / "promotions.jsonl").read_text().splitlines()]
        self.assertEqual([a["action"] for a in audit], ["promote", "deactivate", "rollback"])
        st = R.status(reg)
        self.assertEqual(st["active"], mid)
        self.assertEqual([m["model_id"] for m in st["promoted"]], [mid])


# ---------------------------------------------------------------------- experience log

class TestExperienceLog(unittest.TestCase):
    def test_the_chain_catches_edits_removals_and_damage(self):
        p = Path(TMP.name) / "xp" / "a.jsonl"
        if p.exists():
            p.unlink()
        log = X.ExperienceLog(p)
        for i in range(4):
            self.assertTrue(log.append({"model_id": "m", "question": f"q{i}"}))
        self.assertTrue(X.verify(p)["ok"])
        self.assertEqual(X.verify(p)["lines"], 4)
        lines = p.read_text().splitlines()
        edited = lines[:1] + [lines[1].replace("q1", "qX")] + lines[2:]
        p.write_text("\n".join(edited) + "\n")
        v = X.verify(p)
        self.assertFalse(v["ok"])
        self.assertEqual(v["breaks"][0]["line"], 2)
        p.write_text("\n".join(lines[:1] + lines[2:]) + "\n")                    # a line removed
        self.assertIn("doesn't follow", X.verify(p)["breaks"][0]["why"])
        p.write_text("\n".join(lines) + "\n" + '{"format": "cdos-neural-exp')    # a write cut short
        log2 = X.ExperienceLog(p)
        self.assertTrue(log2.append({"model_id": "m", "question": "after"}))
        self.assertEqual(json.loads(p.read_text().splitlines()[-1])["question"], "after")
        v = X.verify(p)
        self.assertEqual(v["lines"], 6)                                     # the new record is on a line of its own
        self.assertEqual([b["line"] for b in v["breaks"]], [5])               # only the damaged line is reported
        self.assertTrue(v["breaks"][0]["why"].startswith("not JSON"))
        self.assertEqual(json.loads(p.read_text().splitlines()[5])["prev"], "sha256:broken")

    def test_a_full_log_stops_rather_than_rotating(self):
        p = Path(TMP.name) / "xp" / "full.jsonl"
        log = X.ExperienceLog(p, max_bytes=10)
        self.assertTrue(log.append({"q": "x" * 20}))
        size = p.stat().st_size
        self.assertFalse(log.append({"q": "y"}))
        self.assertIn("archive", log.error)
        self.assertEqual(p.stat().st_size, size)

    def test_neural_runs_from_the_trace(self):
        trace = ["route: fact", "planner rules [hop]: $1 = FIND a | ANSWER $1", "  $1 = FIND a  ->  A", "  => unsupported",
                 "planner neural [learned]: $1 = FIND a | $2 = GET $1 ~x | ANSWER $2", "  $1 = FIND a  ->  A",
                 "  => answered, confidence 1.00", "  => rejected: the answer ignores b",
                 "planner neural [learned]: $1 = FIND a | $2 = GET $1 ~y | ANSWER $2", "  => answered"]
        runs = X.neural_runs(trace)
        self.assertEqual([(r["plan"].count("\n"), r["status"], r["rejected"]) for r in runs],
                         [(2, "answered", "the answer ignores b"), (2, "answered", "")])

    @unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
    def test_the_computer_logs_predictions_and_checked_outcomes(self):
        reg = fresh_copy("xplog")
        R.promote(reg, staged()["id"], approve=True)
        c = Computer(cfg(neural_registry=str(reg)))
        self.assertEqual(c.think("what is 12 times 7").faculty, "logic")
        r1 = c.think("jupiter vs saturn, which is heavier")
        r2 = c.think("What's the population of the capital of France?")
        path = reg / "experience" / "experience.jsonl"
        recs = [json.loads(l) for l in path.read_text().splitlines()]
        self.assertEqual(len(recs), 2)                                       # the sum never asked the model
        a, b = recs
        self.assertEqual(a["model_id"], staged()["id"])
        self.assertTrue(a["top"] and a["plans"])
        self.assertEqual(a["outcome"]["planner"], "neural")
        self.assertEqual(a["outcome"]["evidence"], [e["id"] for e in r1.envelope["evidence"]])
        self.assertTrue(any(p["ran"] and p["accept"] == "" for p in a["plans"]))
        self.assertEqual(b["outcome"]["planner"], "rules")
        self.assertEqual(b["rules_action"], "chain")
        self.assertEqual(b["outcome"]["truth"], r2.envelope["truth"])
        c2 = Computer(cfg(neural_registry=str(reg)))                         # a restart continues the chain
        c2.think("owl diet")
        v = X.verify(path)
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["lines"], 3)
        st = X.stats(path)
        self.assertEqual(st["counts"]["predictions"], 3)
        self.assertEqual(st["agreement_with_rules"]["questions"], 1)


# ---------------------------------------------------------------------- the evaluation

@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestEvaluation(unittest.TestCase):
    def test_training_improves_the_predefined_held_out_metric(self):
        rep = staged()["report"]
        p = rep["primary"]
        self.assertGreater(p["rules+trained"], p["rules+untrained"])
        s = rep["selection"]
        self.assertGreater(s["trained"], s["untrained"])
        self.assertGreater(s["trained"], s["chance"])
        self.assertTrue(rep["passed"], {g: v for g, v in rep["gates"].items() if not v["pass"]})

    def test_the_comparison_with_the_rules_lists_every_change(self):
        rep = staged()["report"]
        c = rep["comparisons"]["rules+trained"]
        self.assertEqual(c["regressions"], [])
        n = sum(rep["results"][s]["n"] for s in rep["results"])
        self.assertEqual(sum(c["transitions"].values()), n)
        added = sum(v for k, v in c["transitions"].items() if k.endswith("->wrong") and not k.startswith("wrong"))
        self.assertEqual(added, len(c["new_wrong"]))
        text = (staged()["dir"] / "eval.txt").read_text()
        self.assertIn("wrong answers added", text)
        for w in c["new_wrong"]:
            self.assertIn(w["question"], text)
        cc = rep["computer"]["comparisons"]["computer+trained"]                # what the whole computer says
        self.assertEqual(cc["regressions"], [])
        self.assertEqual(sum(cc["transitions"].values()), n)
        res = rep["computer"]["results"]
        for s in res:
            self.assertEqual(sum(res[s]["computer"][k] for k in ("right", "wrong", "abstained")), res[s]["n"])
        self.assertTrue(rep["gates"]["computer_not_worse"]["pass"])

    def test_the_same_fixtures_for_every_system(self):
        rep = staged()["report"]
        for set_name, res in rep["results"].items():
            totals = {s: r["right"] + r["wrong"] + r["abstained"] for s, r in res.items() if s != "n"}
            self.assertEqual(set(totals.values()), {res["n"]}, set_name)
        _, m = load_model_dir(staged()["dir"])
        for k, v in m["training_data"]["fixtures"].items():
            self.assertEqual(rep["fixtures"][k]["sha256"], v["sha256"], k)

    def test_evaluation_is_reproducible(self):
        from computer.neural.evaluate import evaluate
        reg = fresh_copy("reeval")
        rep = evaluate(reg, staged()["id"], DATA, HELD, REGR, log=QUIET)
        strip = lambda r: {s: {k: {kk: vv for kk, vv in v.items() if kk != "ms_per_question"} if isinstance(v, dict) else v
                               for k, v in res.items()} for s, res in r["results"].items()}   # noqa: E731
        self.assertEqual(strip(rep), strip(staged()["report"]))
        self.assertEqual(rep["primary"], staged()["report"]["primary"])
        self.assertEqual(rep["comparisons"], staged()["report"]["comparisons"])


# ---------------------------------------------------------------------- the existing controller path

class TestControllerFileHardening(unittest.TestCase):
    """A damaged controller.json (navigator_controller) used to stop start-up or crash a later turn."""

    def run_with(self, d: dict, name: str):
        p = Path(TMP.name) / f"ctl-{name}.json"
        p.write_text(json.dumps(d))
        c = Computer(cfg(navigator_controller=str(p)))
        self.assertEqual([x.label for x in c.planners], ["rules"], name)
        self.assertIn("not loaded", c.controller_error)
        c.think("jupiter vs saturn, which is heavier")                       # no exception
        return c.controller_error

    def test_damaged_files_are_refused_at_start_up(self):
        A, n = list(ACTION_NAMES), len(ACTION_NAMES)
        base = {"format": "cdos-computer-controller/1", "actions": A, "dim": 4096, "hidden": 2, "rows": {},
                "b1": [0, 0], "w2": [[0, 0]] * n, "b2": [0] * n}
        self.run_with({**base, "rows": []}, "rowslist")
        self.assertIn("fails on a test input", self.run_with({**base, "rows": {"5": [0.1] * 8}}, "rowlong"))
        self.assertIn("probability", self.run_with({**base, "b2": [float("nan")] * n}, "nan"))

    @unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
    def test_a_staged_model_is_also_a_valid_controller_file(self):
        c = Computer(cfg(navigator_controller=str(staged()["dir"] / "model.json")))
        self.assertEqual([x.label for x in c.planners], ["rules", "learned"])
        r = c.think("jupiter vs saturn, which is heavier")
        self.assertIn("so jupiter is heavier", r.text.lower())
        names = [x["name"] for x in r.envelope["capabilities"]]
        self.assertIn("planner.controller", names)                         # was credited to planner.lm
        self.assertNotIn("planner.lm", names)


# ---------------------------------------------------------------------- capabilities

class TestCapabilities(unittest.TestCase):
    def test_sub_package_imports_are_resolved(self):
        src = "from .model import x\nfrom ..plan import parse\nfrom computer.neural.schema import y\nfrom . import logic\n"
        self.assertEqual(caps.internal_imports(src, "computer.neural.runtime"),
                         [("computer.neural.model", {"x"}), ("computer.plan", {"parse"}),
                          ("computer.neural.schema", {"y"}), ("computer.neural.logic", {"*"})])
        self.assertEqual(caps.internal_imports("from .navdata import shape\n"), [("computer.navdata", {"shape"})])

    def test_the_neural_answer_path_is_declared_deterministic_and_clean(self):
        reg = caps.default_registry()
        c = reg.caps["planner.neural"]
        self.assertTrue(c.deterministic)
        self.assertEqual(c.fallback, "planner.rules")
        for m in c.modules:
            self.assertEqual(caps.scan(Path(reg._source(m)).read_text()), "", m)
        self.assertEqual(caps.unreviewed_imports(reg), [])
        self.assertFalse(reg.caps["neural.experience"].deterministic)
        self.assertNotIn("computer.neural.experience", c.modules)
        self.assertIn("neural.experience", caps.WRITERS)


if __name__ == "__main__":
    unittest.main()
