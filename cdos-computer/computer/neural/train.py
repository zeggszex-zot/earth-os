"""Training the neural core, reproducibly, into a staging directory.  The active model is never touched.

    python3 -m computer.neural train --registry models/neural --corpus data --seed 0 --epochs 40 --batch 32 --threads 1

What happens, in order:

  1. The corpus is opened read-only and its version recorded.  The registry may not be inside it.
  2. Teacher examples come from the corpus (computer/navdata.generate, every plan executed and checked), split by subject;
     navdata.leak_free drops training examples that mention a held-out subject anywhere.
  3. Held-out fixtures are materialised into the staging directory: A (held-out subjects, generator phrasings) and
     B (held-out subjects, re-worded with the held-out half of the templates), so every evaluation sees the same questions.
  4. Training questions: the training subjects' teacher examples, re-worded with the *other* half of the templates, and
     any --mine files.  A training question that is also a held-out or regression question is dropped (generated) or
     refused (yours); one of yours that names a held-out subject is dropped.
  5. Every question is explored (records.py) into a training record; the held-out questions too, for the selection metric.
  6. The network is trained (the existing controller's network and optimiser, with batch size, epochs, wall-time and
     CPU-time limits), and its initial weights are kept as the "untrained" baseline.
  7. model.json, untrained.json, manifest.json, records.jsonl.gz and fixtures/ are written to a temporary directory and
     renamed into <registry>/staging/<model id> when complete.  The model id is the weights' hash.

Same corpus, same arguments, same code: the same records hash and the same model file, byte for byte.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import random
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

from computer.controller import ACTION_NAMES, DIM, ControllerPlanner, Network
from computer.controller_train import reword
from computer.corpus import Corpus
from computer.navdata import generate, leak_free
from computer.neural import NEURAL_VERSION
from computer.neural.model import MANIFEST_FORMAT, sha256_bytes, sha256_file
from computer.neural.records import leak_report, make_record, names_in, norm_q, rows_of
from computer.neural.runtime import inside
from computer.neural.schema import SCHEMA_VERSION, describe, fingerprint

ROOT = Path(__file__).resolve().parent.parent.parent


class TrainingError(RuntimeError):
    pass


# ---------------------------------------------------------------------- limits

def limit_cpu(threads: int | None = None, nice: int | None = None) -> dict:
    """Thread caps for numpy's math libraries (only effective before numpy is first imported) and a lower priority."""
    out = {}
    if threads:
        already = "numpy" in sys.modules
        for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                  "VECLIB_MAXIMUM_THREADS"):
            os.environ[k] = str(threads)
        out["threads"] = threads
        if already:
            try:
                from threadpoolctl import threadpool_limits      # optional; not a dependency
                threadpool_limits(threads)
                out["threads_enforced"] = "threadpoolctl"
            except ImportError:
                out["threads_enforced"] = "no: numpy was already loaded in this process"
        else:
            out["threads_enforced"] = "environment"
    if nice:
        try:
            os.nice(nice)
            out["nice"] = nice
        except OSError as e:
            out["nice"] = f"not set: {e}"
    return out


def peak_rss_mb() -> float:
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except ImportError:
        return -1.0


# ---------------------------------------------------------------------- the network

def init_params(n_actions: int, hidden: int, dim: int, seed: int):
    import numpy as np
    rng = np.random.default_rng(seed)
    W1 = rng.normal(0, 0.1, (dim, hidden))
    b1 = np.zeros(hidden)
    W2 = rng.normal(0, 0.1, (n_actions, hidden))
    b2 = np.zeros(n_actions)
    return rng, [W1, b1, W2, b2]


def to_network(actions, dim, hidden, params, rows) -> Network:
    W1, b1, W2, b2 = params
    seen = sorted({i for r in rows for i in r["idx"]})
    return Network(list(actions), dim, hidden, {i: W1[i].tolist() for i in seen}, b1.tolist(), W2.tolist(), b2.tolist())


def train_network(rows: list[dict], actions=ACTION_NAMES, hidden: int = 32, epochs: int = 40, lr: float = 0.01,
                  l2: float = 1e-4, seed: int = 0, dim: int = DIM, batch: int = 32, max_seconds: float | None = None,
                  max_cpu_seconds: float | None = None, progress=None) -> tuple[Network, Network, dict]:
    """(trained, untrained, info).  The existing controller's training (computer/controller_train.py train()), with the
    batch size and time limits as parameters: at batch 32 with no limit it produces the same weights.  `untrained` is the
    same network at its initial weights, before any step: the baseline that measures what training bought."""
    import numpy as np
    if batch < 1 or epochs < 0 or hidden < 1:
        raise TrainingError("batch and hidden must be at least 1, epochs at least 0")
    A = len(actions)
    aix = {a: i for i, a in enumerate(actions)}
    rng, params = init_params(A, hidden, dim, seed)
    untrained = to_network(actions, dim, hidden, [p.copy() for p in params], rows)
    W1, b1, W2, b2 = params
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    Y = np.zeros((len(rows), A))
    for i, r in enumerate(rows):
        for a in r["targets"]:
            Y[i, aix[a]] = 1.0 / len(r["targets"])
    t = 0
    order = np.arange(len(rows))
    w0, c0 = time.monotonic(), time.process_time()
    stopped, done_batches, losses = "", 0, []
    n_batches = max(1, -(-len(rows) // batch))
    for ep in range(epochs):
        rng.shuffle(order)
        total = 0.0
        for start in range(0, len(order), batch):
            idxs = order[start:start + batch]
            gW1 = np.zeros_like(W1)
            gb1, gW2, gb2 = np.zeros_like(b1), np.zeros_like(W2), np.zeros_like(b2)
            for i in idxs:
                idx = rows[i]["idx"]
                s = 1.0 / np.sqrt(max(1, len(idx)))
                pre = W1[idx].sum(0) * s + b1
                h = np.maximum(pre, 0)
                z = W2 @ h + b2
                z -= z.max()
                p = np.exp(z) / np.exp(z).sum()
                total -= float((Y[i] * np.log(p + 1e-12)).sum())
                dz = p - Y[i]
                gW2 += np.outer(dz, h)
                gb2 += dz
                dh = (W2.T @ dz) * (pre > 0)
                gW1[idx] += dh * s
                gb1 += dh
            grads = [gW1 / len(idxs) + l2 * W1, gb1 / len(idxs), gW2 / len(idxs) + l2 * W2, gb2 / len(idxs)]
            t += 1
            for k, (pm, g) in enumerate(zip(params, grads)):
                m[k] = 0.9 * m[k] + 0.1 * g
                v[k] = 0.999 * v[k] + 0.001 * g * g
                pm -= lr * (m[k] / (1 - 0.9 ** t)) / (np.sqrt(v[k] / (1 - 0.999 ** t)) + 1e-8)
            done_batches += 1
            if max_seconds is not None and time.monotonic() - w0 > max_seconds:
                stopped = f"wall-time limit ({max_seconds:g} s)"
            if max_cpu_seconds is not None and time.process_time() - c0 > max_cpu_seconds:
                stopped = f"CPU-time limit ({max_cpu_seconds:g} s)"
            if stopped:
                break
        losses.append(round(total / max(1, len(rows)), 5))
        if progress:
            progress(ep, losses[-1])
        if stopped:
            break
    info = {"epochs_requested": epochs, "epochs_completed": round(done_batches / n_batches, 3), "steps": t,
            "stopped_early": stopped, "loss_by_epoch": losses,
            "train_seconds": round(time.monotonic() - w0, 3), "train_cpu_seconds": round(time.process_time() - c0, 3)}
    return to_network(actions, dim, hidden, params, rows), untrained, info


def top1(model, rows: list[dict]) -> float:
    ok = 0
    for r in rows:
        sc = model.predict(r["idx"])
        ok += max(sc, key=sc.get) in r["targets"]
    return round(ok / max(1, len(rows)), 4)


def chance(rows: list[dict], n_actions: int) -> float:
    """The top-1 a uniformly random pick would get on these rows."""
    return round(sum(len(r["targets"]) / n_actions for r in rows) / max(1, len(rows)), 4)


# ---------------------------------------------------------------------- files

def canonical_json(obj, indent=None) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=indent, separators=(",", ":") if indent is None else (",", ": "),
                       ensure_ascii=False) + "\n").encode("utf-8")


def model_bytes(net: Network) -> bytes:
    """The network as stored: the existing controller format, keys sorted, so the same weights give the same bytes."""
    return canonical_json(net.to_json())


def read_jsonl(path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def iter_records(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def read_records(path) -> list[dict]:
    return list(iter_records(path))


class RecordSink:
    """Training records to a gzip file as they're made: no timestamp in the header, so the same records give the same
    bytes, and the sha256 of the decompressed lines is records.dataset_hash() of the same records."""

    def __init__(self, path):
        self.fh = open(path, "wb")
        self.gz = gzip.GzipFile(fileobj=self.fh, mode="wb", mtime=0, filename="")
        self.h = hashlib.sha256()

    def write(self, rec: dict) -> None:
        line = json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
        self.gz.write(line)
        self.h.update(line)

    def close(self) -> str:
        self.gz.close()
        self.fh.close()
        return "sha256:" + self.h.hexdigest()

    def abort(self) -> None:
        for f in (self.gz, self.fh):
            try:
                f.close()
            except OSError:
                pass


def compact(rec: dict) -> dict:
    """The part of a record that training, the metrics and the leak checks use."""
    return {"id": rec["id"], "split": rec["split"], "source": rec["source"], "question": rec["question"],
            "targets": rec["targets"], "inputs": {"idx": rec["inputs"]["idx"], "names": rec["inputs"]["names"]},
            "provenance": {"concepts": rec["provenance"]["concepts"], "subjects": rec["provenance"]["subjects"]},
            "strict_refused": any(c.get("validator", "ok") != "ok" and c.get("outcome") == "reached the answer"
                                  for c in rec["candidates"])}


def code_sha256() -> str:
    """A hash of the computer package's source: which code produced (or evaluated) a model."""
    h = hashlib.sha256()
    for p in sorted((ROOT / "computer").rglob("*.py")):
        h.update(str(p.relative_to(ROOT)).encode() + b"\0" + p.read_bytes() + b"\0")
    return "sha256:" + h.hexdigest()


def software() -> dict:
    try:
        import numpy
        npv = numpy.__version__
    except ImportError:
        npv = None
    cpu = platform.processor() or ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {"neural_version": NEURAL_VERSION, "code_sha256": code_sha256(), "python": platform.python_version(),
            "numpy": npv, "platform": platform.platform(), "machine": platform.machine(), "cpu": cpu,
            "cpus": os.cpu_count()}


def corpus_snapshot(base) -> dict:
    """(size, mtime) of every file in the corpus folder: compared before and after, to show nothing was written."""
    out = {}
    for p in sorted(Path(base).rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(base))] = [st.st_size, st.st_mtime_ns]
    return out


def fixture_hash(items: list[dict]) -> str:
    return sha256_bytes(b"".join(canonical_json(x) for x in items))


FIXTURE_KEYS = ("question", "gold", "expect", "kind", "not", "subjects", "reworded_from", "teacher")


def fixture_item(e: dict) -> dict:
    return {k: e[k] for k in FIXTURE_KEYS if k in e}


# ---------------------------------------------------------------------- the whole run

def train_to_staging(registry, corpus_dir, *, seed: int = 0, data_seed: int | None = None, subjects: int = 3000,
                     rewordings: int = 3, epochs: int = 40, hidden: int = 32, lr: float = 0.01, l2: float = 1e-4,
                     batch: int = 32, mine=(), heldout=(), regression=(), lexicon: str | None = None,
                     things: bool = False, max_seconds: float | None = None, max_cpu_seconds: float | None = None,
                     limits: dict | None = None, detail: bool = True, replace: bool = False, log=None,
                     report: dict | None = None, require_mine: bool = False) -> Path:
    """`report`, if given, is filled with what became of each --mine question (used, or dropped and why), even when
    training is then refused.  require_mine: refuse unless at least one --mine question can be trained on."""
    log = log or (lambda *a: print(*a, file=sys.stderr))
    w0, c0 = time.monotonic(), time.process_time()
    data_seed = seed if data_seed is None else data_seed
    registry, corpus_dir = Path(registry), Path(corpus_dir)
    if inside(registry, corpus_dir) or inside(corpus_dir, registry):
        raise TrainingError("the neural registry must not be inside the corpus folder, or contain it")
    corpus = Corpus(corpus_dir).load()
    if not corpus.ready:
        raise TrainingError(f"no corpus: {corpus.error}")
    before = corpus_snapshot(corpus_dir)
    version = corpus.version
    corpus.cache_shards = max(corpus.cache_shards, sum((corpus.meta or {}).get(k, 0) for k in ("s_shards", "v_shards")))
    lex_sha = None
    if lexicon:
        from computer.lexicon import Lexicon
        corpus.lexicon = Lexicon.load(lexicon)
        lex_sha = sha256_bytes(Path(lexicon).read_bytes())
    from computer.controller import YEAR_NOW
    year = YEAR_NOW
    rels = set((corpus.meta or {}).get("relation_names", [])) or None

    # 2. teacher examples, held out by subject
    pool = None
    if things:
        pool = [k for k in sorted(corpus.subjects) if any(f.source != "OEWN" for f in corpus.facts_for(k))]
    teacher, dropped = leak_free(generate(corpus, subjects, seed=data_seed, subjects=pool))
    train_t = [e for e in teacher if e["split"] == "train"]
    held_t = [e for e in teacher if e["split"] != "train"]
    held_subjects = {s for e in held_t for s in e.get("subjects", [])}
    log(f"teacher: {len(train_t)} train, {len(held_t)} held out ({dropped} training questions dropped for "
        f"mentioning a held-out subject)")

    # 3. held-out fixtures, materialised
    rng_fix = random.Random(f"{data_seed}:fixtures")
    fixtures = {"A_heldout_generator_phrasings": [fixture_item(e) for e in held_t if e["expect"] == "answered"],
                "B_heldout_unseen_phrasings": [fixture_item(r) for e in held_t for r in reword(e, rng_fix, 2, which="test")]}
    outside = {}
    for kind, paths in (("D", heldout), ("R", regression)):
        for p in paths:
            outside[f"{kind}_{Path(p).stem}"] = [fixture_item(e) for e in read_jsonl(p)]
    every_fixture = {**fixtures, **outside}
    fixture_qs = {norm_q(e["question"]) for items in every_fixture.values() for e in items}

    # 4. training questions
    rng_train = random.Random(f"{data_seed}:train")
    exs = []
    for e in train_t:
        exs.append(("teacher", e))
        exs += [("reworded", r) for r in reword(e, rng_train, rewordings, which="train")]
    overlap_dropped = [e["question"] for _, e in exs if norm_q(e["question"]) in fixture_qs]
    exs = [(s, e) for s, e in exs if norm_q(e["question"]) not in fixture_qs]
    mine_n, mine_dropped = 0, 0
    for p in mine:
        for i, e in enumerate(read_jsonl(p)):
            if norm_q(e.get("question", "")) in fixture_qs:
                raise TrainingError(f"{p} line {i + 1} is a held-out or regression question: {e.get('question')!r}. "
                                    "A question can't be both trained on and used to judge the training.")
            if "expect" not in e or "question" not in e:
                raise TrainingError(f"{p} line {i + 1}: needs question, expect (and gold when answered)")
            exs.append(("mine", {**e, "origin": Path(p).name}))
            mine_n += 1

    # 5. explore.  Each record goes to disk as it's made; only what training and the checks need stays in memory, so a
    # large corpus costs file space, not RAM.  Everything is built in a work folder that becomes the staged model only
    # when it is complete.
    staging = registry / "staging"
    work = staging / f".work-{os.getpid()}-{time.monotonic_ns()}"
    work.mkdir(parents=True)
    try:
        return _explore_train_stage(**locals())        # everything prepared above, by name (see the signature)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _explore_train_stage(*, work, staging, corpus, corpus_dir, before, version, year, rels, exs, fixtures, every_fixture,
                         held_subjects, train_t, held_t, dropped, overlap_dropped, mine_n, mine_dropped, detail,
                         report, require_mine,
                         hidden, epochs, lr, l2, seed, batch, max_seconds, max_cpu_seconds, subjects, things, data_seed,
                         rewordings, lex_sha, limits, replace, log, w0, c0, **_) -> Path:
    ctl = ControllerPlanner(corpus)
    t_ex = time.monotonic()
    sink = RecordSink(work / "records.jsonl.gz")
    try:
        kept, stats, n = [], Counter(), 0

        def keep(rec):
            sink.write(rec)
            kept.append(compact(rec))
            stats[f"{rec['split']}:{rec['source']}"] += 1
            if not rec["targets"]:
                stats[f"{rec['split']}:unreachable"] += 1
            if kept[-1]["strict_refused"]:
                stats[f"{rec['split']}:label_refused_by_strict_validator"] += 1

        report = report if report is not None else {}
        report.update(mine_used=[], mine_dropped=[])
        for source, e in exs:
            rec = make_record(e, ctl, source=source, split="train", year=year, corpus_version=version, rels=rels,
                              detail=detail)
            if source == "mine":
                held = sorted(held_subjects & names_in(rec))
                if held:
                    mine_dropped += 1                  # one of yours names a held-out thing: not trained on
                    report["mine_dropped"].append({"question": e["question"], "why": f"names held-out subject(s) "
                                                   f"{', '.join(held)}: training on it would leak into the held-out test"})
                    continue
                if not rec["targets"]:
                    report["mine_dropped"].append({"question": e["question"], "why": "no plan shape reaches its answer "
                                                   "with this corpus, so there is nothing to learn"})
                else:
                    report["mine_used"].append({"question": e["question"], "targets": rec["targets"]})
            keep(rec)
            n += 1
            if n % 500 == 0:
                log(f"  explored {n} training questions")
        if require_mine and not report["mine_used"]:
            raise TrainingError("none of your questions can be trained on: " +
                                "; ".join(f"{d['question']!r}: {d['why']}" for d in report["mine_dropped"]))
        for name in ("A_heldout_generator_phrasings", "B_heldout_unseen_phrasings"):
            for e in fixtures[name]:
                keep(make_record({**e, "id": None}, ctl, source=name[0], split="heldout", year=year,
                                 corpus_version=version, rels=rels, detail=detail))
        data_sha = sink.close()
        explore_s = round(time.monotonic() - t_ex, 3)
        train_recs = [r for r in kept if r["split"] == "train"]
        leaks = leak_report(train_recs, held_subjects, {k: [e["question"] for e in v] for k, v in every_fixture.items()})
        if leaks["concept_leaks"] or any(leaks["question_overlap"].values()):
            raise TrainingError(f"leak check failed: {leaks}")
        rows, held_rows = rows_of(kept, "train"), rows_of(kept, "heldout")
        if not rows:
            raise TrainingError("nothing to learn from: no training question reached its answer")
        log(f"records: {dict(stats)} ({explore_s:.1f}s exploring)")

        # 6. train
        net, untrained, info = train_network(rows, ACTION_NAMES, hidden, epochs, lr, l2, seed, DIM, batch, max_seconds,
                                             max_cpu_seconds, progress=lambda ep, loss: log(f"  epoch {ep + 1}: loss {loss}")
                                             if (ep + 1) % 10 == 0 else None)
        mb, ub = model_bytes(net), model_bytes(untrained)
        msha, usha = sha256_bytes(mb), sha256_bytes(ub)
        model_id = f"nc{SCHEMA_VERSION}-{msha.split(':')[1][:12]}"
        from computer.neural.model import load_network_file   # the stored bytes, as the runtime will read them
        stored = Network.from_json(json.loads(mb))
        stored_u = Network.from_json(json.loads(ub))
        metrics = {"train_top1": top1(stored, rows), "heldout_top1": top1(stored, held_rows),
                   "untrained_train_top1": top1(stored_u, rows), "untrained_heldout_top1": top1(stored_u, held_rows),
                   "chance_heldout_top1": chance(held_rows, len(ACTION_NAMES)),
                   "train_rows": len(rows), "heldout_rows": len(held_rows)}
        log(f"top-1 accepted action: train {metrics['train_top1']:.1%}, held-out {metrics['heldout_top1']:.1%} "
            f"(untrained {metrics['untrained_heldout_top1']:.1%}, chance {metrics['chance_heldout_top1']:.1%})")

        after = corpus_snapshot(corpus_dir)
        if after != before or Corpus(corpus_dir).load().version != version:
            raise TrainingError("the corpus changed during training: nothing is staged")

        # 7. stage
        recs_sha = sha256_file(work / "records.jsonl.gz")
        fixture_files = {f"fixtures/{k}.jsonl": b"".join(canonical_json(x) for x in v) for k, v in every_fixture.items()}
        manifest = {
            "format": MANIFEST_FORMAT, "model_id": model_id, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model_file": "model.json", "model_sha256": msha, "untrained_file": "untrained.json", "untrained_sha256": usha,
            "model_format": json.loads(mb)["format"],
            "schema": {"name": describe()["name"], "version": SCHEMA_VERSION, "fingerprint": fingerprint()},
            "actions": list(ACTION_NAMES),
            "training_data": {"sha256": data_sha, "records_file": "records.jsonl.gz",
                              "records_file_sha256": recs_sha, "counts": dict(sorted(stats.items())),
                              "train_rows": len(rows), "heldout_rows": len(held_rows),
                              "teacher_train": len(train_t), "teacher_heldout": len(held_t),
                              "dropped_for_held_out_subjects": dropped,
                              "dropped_for_fixture_overlap": len(overlap_dropped),
                              "mine_questions": mine_n, "mine_dropped_for_held_out_subjects": mine_dropped,
                              "mine_used": report["mine_used"], "mine_dropped": report["mine_dropped"],
                              "leak_check": leaks, "corpus_version": version, "corpus_integrity": corpus.integrity,
                              "corpus_dir": str(corpus_dir), "year": year, "lexicon_sha256": lex_sha,
                              "generator": {"subjects": subjects, "things": things, "data_seed": data_seed,
                                            "rewordings": rewordings, "phrasings": "train half (controller_train.forms_for)"},
                              "fixtures": {k: {"file": f"fixtures/{k}.jsonl", "n": len(v), "sha256": fixture_hash(v)}
                                           for k, v in every_fixture.items()}},
            "hyperparameters": {"hidden": hidden, "epochs": epochs, "lr": lr, "l2": l2, "batch": batch, "seed": seed,
                                "data_seed": data_seed, "dim": DIM, "optimizer": "adam (0.9, 0.999, 1e-8)",
                                "max_seconds": max_seconds, "max_cpu_seconds": max_cpu_seconds, "cpu_limits": limits or {}},
            "training": info,
            "software": software(),
            "metrics": metrics,
            "resources": {"wall_seconds": round(time.monotonic() - w0, 3),
                          "cpu_seconds": round(time.process_time() - c0, 3), "explore_seconds": explore_s,
                          "train_seconds": info["train_seconds"], "peak_rss_mb": peak_rss_mb()},
            "corpus_unchanged": True,
        }
        dest = staging / model_id
        if dest.exists():
            if not replace:
                raise TrainingError(f"{dest} already exists (same weights: training is reproducible). --replace to redo it")
            shutil.rmtree(dest)
        (work / "fixtures").mkdir(parents=True)
        (work / "model.json").write_bytes(mb)
        (work / "untrained.json").write_bytes(ub)
        for rel, data in fixture_files.items():
            (work / rel).write_bytes(data)
        (work / "manifest.json").write_bytes(canonical_json(manifest, indent=1))
        load_network_file(work / "model.json", msha, list(ACTION_NAMES), DIM)       # what was written reads back, checked
        os.replace(work, dest)
        res = manifest["resources"]
        log(f"staged {model_id} in {dest} ({res['wall_seconds']:.1f}s, peak {res['peak_rss_mb']} MB)")
        return dest
    except BaseException:
        sink.abort()                       # the work folder is removed by the caller
        raise
