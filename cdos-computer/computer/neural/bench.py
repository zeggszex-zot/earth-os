"""Inference cost on this machine: model load time, per-question latency, memory.

    python3 -m computer.neural bench --registry models/neural --corpus data --questions tests/controller_heldout.jsonl

Per question it times the three parts of a neural prediction separately (reading the question against the corpus,
features + network, filling and validating plans) and the whole navigate() call with the neural planner and without
it, so the cost the model adds to a turn is the difference.  Peak RSS is this process's, so run it on the machine
that will answer (the NUC or the Pi), not the one that trained.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

from computer.corpus import Corpus
from computer.navigator import Navigator
from computer.neural.model import load_model_dir
from computer.neural.runtime import NeuralPlanner, load_active
from computer.neural.schema import encode
from computer.neural.train import peak_rss_mb, read_jsonl
from computer.planner import RulePlanner, navigate


def _pct(xs: list[float]) -> dict:
    if not xs:
        return {}
    s = sorted(xs)
    pick = lambda q: s[min(len(s) - 1, int(round(q * (len(s) - 1))))]
    return {"mean": round(statistics.fmean(s), 3), "p50": round(pick(0.5), 3), "p95": round(pick(0.95), 3),
            "max": round(s[-1], 3)}


def bench(registry, corpus_dir, questions: list[str], model_id: str | None = None, repeat: int = 3,
          precision: str = "float") -> dict:
    rss0 = peak_rss_mb()
    corpus = Corpus(corpus_dir).load()
    rss_corpus = peak_rss_mb()
    t = time.perf_counter()
    if model_id:
        d = Path(registry) / "models" / model_id
        if not d.is_dir():
            d = Path(registry) / "staging" / model_id
        net, manifest = load_model_dir(d)
    else:
        net, manifest, d = load_active(registry, corpus_base=corpus_dir)
    load_ms = (time.perf_counter() - t) * 1000
    if precision != "float":
        from computer.compute import QuantNetwork
        net = QuantNetwork.from_network(net, {"int8": 8, "int4": 4}[precision])
    rss_model = peak_rss_mb()
    neural = NeuralPlanner(corpus, net, manifest)
    rules = RulePlanner(corpus)
    nav = Navigator(corpus)
    read_ms, predict_ms, plan_ms, with_ms, without_ms = [], [], [], [], []
    for q in questions:                                   # warm the shard cache, as a running computer has it
        neural.plans(q)
        navigate(q, [rules], nav)
    for _ in range(repeat):
        for q in questions:
            neural.reader._last = None
            t0 = time.perf_counter()
            r = neural.reader.read(q)
            t1 = time.perf_counter()
            _, idx = encode(r, neural.reader)
            net.predict(idx)
            t2 = time.perf_counter()
            neural.plans(q)                                # reads (cached), predicts again, fills and validates
            t3 = time.perf_counter()
            read_ms.append((t1 - t0) * 1000)
            predict_ms.append((t2 - t1) * 1000)
            plan_ms.append(max(0.0, (t3 - t2) - (t2 - t1)) * 1000)
            corpus.last_subject = ""
            t4 = time.perf_counter()
            navigate(q, [rules], nav)
            t5 = time.perf_counter()
            corpus.last_subject = ""
            navigate(q, [rules, neural], nav)
            t6 = time.perf_counter()
            without_ms.append((t5 - t4) * 1000)
            with_ms.append((t6 - t5) * 1000)
    return {"model_id": manifest["model_id"], "precision": precision, "questions": len(questions), "repeat": repeat,
            "load_ms": round(load_ms, 2), "ms": {"read_question": _pct(read_ms), "features_and_network": _pct(predict_ms),
                                                  "fill_and_validate": _pct(plan_ms),
                                                  "navigate_rules_only": _pct(without_ms),
                                                  "navigate_with_neural": _pct(with_ms)},
            "peak_rss_mb": {"start": rss0, "after_corpus": rss_corpus, "after_model": rss_model, "end": peak_rss_mb()},
            "model_rows": len(getattr(net, "rows", None) or getattr(net, "index", {}) or {})}


def questions_from(paths) -> list[str]:
    out = []
    for p in paths:
        out += [e["question"] for e in read_jsonl(p)]
    return out
