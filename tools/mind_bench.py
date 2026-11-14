#!/usr/bin/env python3
"""Measure Earth Mind on this machine (docs/EARTH_MIND.md, "Measured").

    python3 tools/mind_bench.py                 # a table
    python3 tools/mind_bench.py --json out.json # and the numbers, for comparing machines

What is measured, and what is not:

* everything Earth Mind adds around a model — building it, routing, system
  readings, CDOS, the memory store, a character's turn — is measured for real,
  on this machine's real capability layer, with a stub model that answers at
  once so that the overhead is what shows;
* the language model itself is measured only if one is reachable at the
  configured address. If none is, the table says so. It never substitutes a
  figure for one.

Nothing here changes the machine: readings only, and all memory goes to a
scratch directory that is removed afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def rss_mb() -> float:
    """This process's resident memory now, from the kernel."""
    with open("/proc/self/status", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def timed(fn, repeat: int = 1):
    """(median ms, worst ms, last result) over *repeat* calls."""
    times, out = [], None
    for _ in range(repeat):
        start = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(times), max(times), out


class Stub:
    """A model that answers at once: what remains is Earth Mind's own cost."""

    name = "stub"
    available = True

    def __init__(self):
        self.calls = 0
        self.chars = []

    def status(self):
        return "stub"

    def chat(self, messages, mode="CASUAL", length_factor=1.0, temperature_shift=0.0):
        self.calls += 1
        self.chars.append(sum(len(m["content"]) for m in messages))
        return "Understood."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", metavar="FILE", help="also write the numbers here")
    parser.add_argument("--characters", type=int, default=200)
    args = parser.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="earth-mind-bench-"))
    os.environ["HOME"] = str(scratch)
    os.environ["XDG_CONFIG_HOME"] = str(scratch / ".config")
    os.environ["XDG_DATA_HOME"] = str(scratch / ".local" / "share")
    rows, data = [], {"machine": {"cpus": os.cpu_count(), "python": sys.version.split()[0]}}

    def row(label, value, note=""):
        rows.append((label, value, note))

    try:
        base = rss_mb()
        t0 = time.perf_counter()
        from earth_os.config import Config
        from earth_os.core import Shell
        config = Config.load()
        config.values["voice"]["enabled"] = False
        shell = Shell(config)
        shell_ms = (time.perf_counter() - t0) * 1000.0
        with_shell = rss_mb()
        row("shell (1.0 parts) built", f"{shell_ms:.0f} ms", f"{with_shell:.1f} MB resident")

        ms, _, mind = timed(shell.earth_mind)
        with_mind = rss_mb()
        row("Earth Mind built", f"{ms:.1f} ms", f"+{with_mind - with_shell:.1f} MB; no model call, no reading, no database")
        data["build"] = {"shell_ms": round(shell_ms, 1), "mind_ms": round(ms, 2),
                         "mind_mb": round(with_mind - with_shell, 2)}

        # -- routing -----------------------------------------------------------
        sentences = ["What is using the most RAM?", "Explain how neural networks work.", "Open Firefox.",
                     "Why is my computer running slowly?", "Talk to the shopkeeper.",
                     "I had a long day and I am tired", "what is 12 times 9", "ls -la | grep py"]
        mind.route(sentences[0])                       # the corpus's first-use indexing, once
        times = []
        for sentence in sentences:
            ms, _, _ = timed(lambda s=sentence: mind.route(s, "bench"), repeat=200)
            times.append(ms)
        row("routing, per request", f"{statistics.median(times):.2f} ms", f"worst sentence {max(times):.2f} ms; no model")
        data["route_ms"] = {"median": round(statistics.median(times), 3), "worst": round(max(times), 3)}

        # -- the machine --------------------------------------------------------
        for label, text in (("system reading (cpu load)", "what is the cpu load"),
                            ("system analysis (top memory)", "what is using the most memory"),
                            ("diagnosis, findings only", "why is my computer running slowly")):
            ms, worst, response = timed(lambda t=text: mind.handle(t, "bench"), repeat=5)
            row(label, f"{ms:.1f} ms", f"worst {worst:.1f} ms; {'ok' if response.ok else 'UNAVAILABLE: ' + response.text[:50]}")
            data[label] = {"median_ms": round(ms, 2), "worst_ms": round(worst, 2), "ok": bool(response.ok)}

        # -- CDOS -------------------------------------------------------------
        if mind.link is not None and mind.link.available:
            before = rss_mb()
            first, _, answer = timed(lambda: mind.handle("what is 12 times 9", "bench"))
            later, worst, _ = timed(lambda: mind.handle("what is 144 divided by 12", "bench"), repeat=20)
            row("CDOS first question", f"{first:.0f} ms", f"starts the embedded computer; +{rss_mb() - before:.1f} MB")
            row("CDOS sum, afterwards", f"{later:.1f} ms", f"worst {worst:.1f} ms; no model; said {answer.text[:30]!r}")
            data["cdos"] = {"first_ms": round(first, 1), "later_ms": round(later, 2)}
        else:
            row("CDOS", "not measured", mind.link.status() if mind.link is not None else "not linked")

        # -- the language model ---------------------------------------------------
        model = mind.models.model()
        if model is not None and model.available:
            first, _, _ = timed(lambda: model.chat([{"role": "user", "content": "Say hello in five words."}]))
            later, worst, _ = timed(lambda: model.chat([{"role": "user", "content": "Say hello in five words."}]),
                                    repeat=3)
            row(f"model {model.name}, first call", f"{first:.0f} ms", "includes loading the model")
            row(f"model {model.name}, afterwards", f"{later:.0f} ms", f"worst {worst:.0f} ms")
            data["model"] = {"name": model.name, "first_ms": round(first), "later_ms": round(later)}
        else:
            why = model.status() if model is not None else "no model configured"
            row("language model", "NOT MEASURED", f"none reachable here ({why})")
            data["model"] = {"measured": False, "why": why}

        # -- memory ---------------------------------------------------------------
        from earth_os.mind.memory import Principal, character_namespace
        game = mind.memory.view(Principal("game", world="bench"))
        ns = character_namespace("bench", "npc")
        write, _, _ = timed(lambda: game.add(ns, f"the traveller said something about lamp oil and rope {time.perf_counter()}",
                                              kind="heard"), repeat=1000)
        recall_1k, _, _ = timed(lambda: game.recall(ns, "did I buy lamp oil?", limit=5), repeat=30)
        for i in range(9000):
            game.add(ns, f"event number {i} at the quay", kind="event")
        recall_10k, worst, _ = timed(lambda: game.recall(ns, "did I buy lamp oil?", limit=5), repeat=30)
        size = mind.memory.path.stat().st_size / 1024.0 if mind.memory.path and mind.memory.path.exists() else 0
        row("memory write", f"{write:.2f} ms", "one record, committed to disk")
        row("memory recall, 1,000 records", f"{recall_1k:.1f} ms", "one namespace")
        row("memory recall, 10,000 records", f"{recall_10k:.1f} ms", f"worst {worst:.1f} ms; bounded scan; file {size:.0f} kB")
        data["memory"] = {"write_ms": round(write, 3), "recall_1k_ms": round(recall_1k, 2),
                          "recall_10k_ms": round(recall_10k, 2), "file_kb": round(size)}

        # -- characters -------------------------------------------------------------
        stub = Stub()
        mind.models.use(stub, environment="game", spec="custom:stub")
        mind.games.install({"id": "crowd", "name": "Crowd", "facts": ["The market is open."], "characters": [
            {"id": f"npc{i}", "name": f"Villager {i}", "role": "villager", "background": f"You live at number {i}.",
             "knowledge": [f"Your stall is number {i}."], "goals": ["sell everything by dusk"]}
            for i in range(args.characters)]})
        crowd = mind.games.open("crowd")
        before = rss_mb()
        start = time.perf_counter()
        for i in range(args.characters):
            crowd.talk(f"npc{i}", "good morning, what are you selling today?")
        first_pass = (time.perf_counter() - start) * 1000.0 / args.characters
        start = time.perf_counter()
        for i in range(args.characters):
            crowd.talk(f"npc{i}", "do you remember what I asked you this morning?")
        second_pass = (time.perf_counter() - start) * 1000.0 / args.characters
        grew = rss_mb() - before
        row(f"{args.characters} characters, one turn each", f"{first_pass:.1f} ms/turn",
            "Earth Mind's share: prompt, memory read and write; model time excluded")
        row("…a second turn each, with memories", f"{second_pass:.1f} ms/turn",
            f"prompt {statistics.median(stub.chars):.0f} chars median, {max(stub.chars)} max")
        row("…memory for all of them", f"+{grew:.1f} MB",
            f"{len(mind.models.clients)} model client(s) in total; {len(crowd.voice.active())} working memories kept warm")
        data["characters"] = {"count": args.characters, "ms_per_turn": round(first_pass, 2),
                              "ms_per_turn_with_memories": round(second_pass, 2), "rss_mb": round(grew, 2),
                              "model_clients": len(mind.models.clients),
                              "prompt_chars_median": round(statistics.median(stub.chars)),
                              "prompt_chars_max": max(stub.chars)}

        row("process, at the end", f"{rss_mb():.1f} MB", f"started at {base:.1f} MB")
        data["rss_mb"] = {"start": round(base, 1), "end": round(rss_mb(), 1)}
        mind.close()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    width = max(len(r[0]) for r in rows)
    print(f"Earth Mind on this machine: {os.cpu_count()} CPUs, Python {sys.version.split()[0]}\n")
    for label, value, note in rows:
        print(f"  {label:<{width}}  {value:>14}  {note}")
    if args.json:
        Path(args.json).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
