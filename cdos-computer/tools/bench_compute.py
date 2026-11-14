#!/usr/bin/env python3
"""Measure the compute engine on this machine before trusting it.

    # the controller: float vs int8 vs int4, on each backend: size, memory, time per question, same choice?
    python3 tools/bench_compute.py --controller controller.json --corpus data --questions my_questions.jsonl

    # and right/wrong/abstained on held-out questions at each precision (the number that matters)
    python3 tools/bench_compute.py --controller controller.json --corpus data --questions tests/real_heldout.jsonl --score

    # the mini LM at each profile's token budget (run llama-server with each profile's -t/-c to compare those too)
    python3 tools/bench_compute.py --lm http://127.0.0.1:8080 --n 5

    # write a quantized controller file (smaller on the SD card; loads without quantizing at boot)
    python3 tools/bench_compute.py --controller controller.json --write-int8 controller.q8.json

With no --corpus, the controller is timed on random feature sets shaped like real questions.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from computer.compute import PROFILES, Meter, NumpyBackend, PyBackend, QuantNetwork  # noqa: E402
from computer.controller import Network  # noqa: E402


def size_in_memory(build) -> int:
    tracemalloc.start()
    obj = build()
    cur, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del obj
    return cur


def feature_sets(net, corpus, questions, n=300):
    if corpus and questions:
        from computer.controller import ControllerPlanner, features, hashed
        from computer.corpus import Corpus
        ctl = ControllerPlanner(Corpus(corpus).load())
        qs = [json.loads(l)["question"] for l in open(questions) if l.strip()]
        return [hashed(features(ctl.reader.read(q), ctl.reader)) for q in qs]
    rng = random.Random(0)
    keys = list(net.rows)
    return [rng.sample(keys, min(len(keys), rng.randint(6, 18))) for _ in range(n)]


def time_predict(model, sets, reps=3) -> float:
    best = float("inf")
    for _ in range(reps):
        t = time.perf_counter()
        for s in sets:
            model.predict(s)
        best = min(best, (time.perf_counter() - t) / len(sets))
    return best * 1e6


def bench_controller(a):
    net = Network.load(a.controller)
    sets = feature_sets(net, a.corpus, a.questions)
    backends = [PyBackend()]
    try:
        backends.append(NumpyBackend())
    except ImportError:
        pass
    print(f"controller {a.controller}: {len(net.rows)} trained rows x {net.hidden} hidden, {len(net.actions)} actions;"
          f" {len(sets)} feature sets")
    print(f"{'precision':<10}{'backend':<9}{'file KB':>9}{'memory KB':>11}{'us/question':>13}{'same top-1':>12}")
    ref = [max(p, key=p.get) for p in (net.predict(s) for s in sets)]
    fsize = Path(a.controller).stat().st_size
    fmem = size_in_memory(lambda: Network.load(a.controller))
    print(f"{'float':<10}{'python':<9}{fsize / 1024:>9.0f}{fmem / 1024:>11.0f}{time_predict(net, sets):>13.0f}{'-':>12}")
    for bits in (8, 4):
        q = QuantNetwork.from_network(net, bits)
        blob = json.dumps(q.to_json(), separators=(",", ":"))
        qmem = size_in_memory(lambda: QuantNetwork.from_json(json.loads(blob)))
        for b in backends:
            q.backend = b
            same = sum(max(p, key=p.get) == r for p, r in zip((q.predict(s) for s in sets), ref)) / len(sets)
            print(f"{'int' + str(bits):<10}{b.name:<9}{len(blob) / 1024:>9.0f}{qmem / 1024:>11.0f}"
                  f"{time_predict(q, sets):>13.0f}{same:>12.1%}")
    if a.score:
        from computer.controller import ControllerPlanner
        from computer.controller_eval import run_system
        from computer.corpus import Corpus
        from computer.planner import RulePlanner
        c = Corpus(a.corpus).load()
        lexicon = Path(a.corpus).parent / "lexicon.json"
        if a.lexicon or lexicon.exists():
            from computer.lexicon import Lexicon
            c.lexicon = Lexicon.load(a.lexicon or lexicon)
        ex = [json.loads(l) for l in open(a.questions) if l.strip()]
        print(f"\n{len(ex)} questions, rules + controller")
        print(f"{'precision':<10}{'right':>7}{'wrong':>7}{'abstained':>11}")
        for name, model in (("float", net), ("int8", QuantNetwork.from_network(net, 8)),
                            ("int4", QuantNetwork.from_network(net, 4))):
            tot, _, _ = run_system(c, ex, [RulePlanner(c), ControllerPlanner(c, model)])
            print(f"{name:<10}{tot['right']:>7}{tot['wrong']:>7}{tot['abstained']:>11}")
    if a.write_int8:
        QuantNetwork.from_network(net, 8).save(a.write_int8)
        print(f"\nwrote {a.write_int8} ({Path(a.write_int8).stat().st_size / 1024:.0f} KB)")


def bench_lm(a):
    from computer.llm import MiniLM
    lm = MiniLM(a.lm, timeout=120)
    if not lm.available(force=True):
        print(f"no model at {a.lm}: {lm.error}")
        return
    meter = Meter()
    prompt = [{"role": "system", "content": "You are a CDOS reasoning computer. One to three short, precise sentences."},
              {"role": "user", "content": "friend asked: should I learn the violin?\n\nNOTES:\nThe coins show "
                                          "Waiting. Patience brings its own music.\n\nNow output the result."}]
    print(f"{lm.label} at {a.lm}, {a.n} replies per profile (llama-server's own -t/-c are whatever it was started with)")
    print(f"{'profile':<10}{'max_tokens':>11}{'median ms':>11}{'tokens':>8}{'tok/s':>8}{'cpu ms':>8}{'energy J':>10}")
    for name, p in PROFILES.items():
        if not p.lm_wording:
            print(f"{name:<10}{'-':>11}{'0':>11}{'-':>8}{'-':>8}{'-':>8}{'-':>10}   (eco doesn't call the model)")
            continue
        mt = int(70 * p.lm_tokens)
        rows = []
        for _ in range(a.n):
            tok = meter.start()
            lm.chat(prompt, max_tokens=mt, temperature=0.7)
            rows.append(meter.stop(tok, lm.last_timings))
        med = lambda k: statistics.median(r[k] for r in rows)
        toks = [r["lm"]["tokens"] for r in rows if r["lm"].get("tokens") is not None]
        tps = [r["lm"]["tok_s"] for r in rows if r["lm"].get("tok_s")]
        e = [r["energy_j"] for r in rows if "energy_j" in r]
        print(f"{name:<10}{mt:>11}{med('ms'):>11.0f}{statistics.median(toks) if toks else '-':>8}"
              f"{statistics.median(tps) if tps else '-':>8}{med('cpu_ms'):>8.0f}"
              f"{statistics.median(e) if e else '-':>10}")
    if not meter.energy_ok:
        print("energy: this machine doesn't report it here (no readable RAPL). Use a USB power meter on the Pi.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--controller")
    ap.add_argument("--corpus")
    ap.add_argument("--questions")
    ap.add_argument("--lexicon")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--write-int8")
    ap.add_argument("--lm")
    ap.add_argument("--n", type=int, default=5)
    a = ap.parse_args()
    if not (a.controller or a.lm):
        ap.error("give --controller and/or --lm")
    if a.score and not (a.corpus and a.questions):
        ap.error("--score needs --corpus and --questions")
    if a.controller:
        bench_controller(a)
    if a.lm:
        bench_lm(a)


if __name__ == "__main__":
    main()
