"""CDOS Computer.

    python3 -m computer              device mode: hold the button and speak; a shake runs the sampler
                                     (falls back to the terminal when there is no hardware)
    python3 -m computer --text       terminal session   (add --speak for speech output)
    python3 -m computer --cast "should I learn to paint?"   one random I Ching sample, with the lines drawn
    python3 -m computer --wise       one stored aphorism
    python3 -m computer --check      system check: corpus, mini LM, voice, ears, GPIO, memory
    python3 -m computer --plan "what is the population of the capital of france"
                                     the navigator's plan, each step, the evidence and the answer
"""

from __future__ import annotations

import argparse
import sys
import time

from . import config as config_mod
from . import instinct
from .core import Computer


TAGS = {"knowledge": "KNOWLEDGE", "logic": "LOGIC", "instinct": "SAMPLER", "care": "PRIORITY"}
PROMPT = "CDOS> "


def show(r, verbose=False):
    tag = TAGS.get(r.faculty, r.faculty.upper())
    print(f"[{tag}{'' if r.voice == 'computer' else ' | ' + r.voice}] {r.text}")
    if verbose:
        for t in r.trace:
            print(f"     · {t}")


def text_chat(computer: Computer, speak=False, verbose=False):
    voice = None
    if speak:
        from .voice import Voice
        voice = Voice(computer.cfg)
    print(f"{computer.cfg.name} READY. Enter a query; empty line or Ctrl-D to exit. "
          f"(/shake runs the sampler, /v toggles the trace, /profile eco|balanced|priority|auto)")
    while True:
        try:
            q = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            break
        if q == "/v":
            verbose = not verbose
            continue
        if q.startswith("/profile"):
            mode = q.split()[1] if len(q.split()) > 1 else ""
            try:
                if mode:
                    computer.engine.set_profile(mode)
                print(f"compute: {computer.engine.status()}")
            except ValueError as e:
                print(e)
            continue
        r = computer.think("", shake=str(time.perf_counter_ns()).encode()) if q == "/shake" else computer.think(q)
        if r.perspective and r.route and r.route.intent != "more":
            print(r.perspective.reading.primary.glyph())
        show(r, verbose)
        if voice:
            voice.say(r.text)


def device_mode(computer: Computer):
    from .body import Body
    from .voice import Ears, Voice

    cfg = computer.cfg
    body, voice, ears = Body(cfg), Voice(cfg), Ears(cfg)
    if not body.has_hardware or not ears.ready:
        missing = ", ".join(x for x, ok in (("GPIO", body.has_hardware), ("speech recognition", ears.ready)) if not ok)
        print(f"[computer] no {missing}: using the terminal instead")
        return text_chat(computer, speak=voice.kind != "print")

    def answer(r):
        if r.perspective and r.route.intent != "more":
            body.status_leds("thinking")
            body.show_cast(r.perspective.reading.lines)
        body.status_leds(r.mood)
        voice.say(r.text)

    body.status_leds("happy")
    voice.say(f"{cfg.name} online. Hold the button to enter a query.")
    last_spoke = time.monotonic()
    while True:
        if body.button.wait_for_press(timeout=0.2):
            body.status_leds("curious")
            heard = ears.listen(lambda: body.button.is_pressed)
            if not heard:
                body.status_leds("curious")
                voice.say("Input not recognized. Repeat the query.")
                continue
            print(f"{PROMPT}{heard}")
            body.status_leds("thinking")
            r = computer.think(heard, shake=body.take_shake(window=15, minimum=4))   # a shake while asking counts
            answer(r)
            last_spoke = time.monotonic()
            continue
        shake = body.take_shake()
        if shake:
            answer(computer.think("", shake=shake))
            last_spoke = time.monotonic()
            continue
        if cfg.idle_saying_minutes and time.monotonic() - last_spoke > cfg.idle_saying_minutes * 60:
            line, _ = instinct.wise_saying()
            body.status_leds("wink")
            voice.say(line)
            last_spoke = time.monotonic()
            body.sleep()


def show_plan(computer: Computer, question: str) -> None:
    from .planner import navigate
    trace = []
    plan, res = navigate(question, computer.planners, computer.navigator, trace=trace, limit=8)
    for t in trace:
        print(t)
    if not res:
        print("no plan: the computer would use its ordinary lookup for this one")
        return
    print("\nevidence:")
    for f in res.evidence:
        print(f"  {f.subject}  {f.relation}  {f.value}{' ' + f.unit if f.unit else ''}   [{f.source}, {f.conf:.2f}]")
    print(f"\n[KNOWLEDGE] {res.text}   ({res.status}, {res.ms} ms)")


def check(computer: Computer):
    from .body import HAVE_GPIO, Body
    from .voice import Ears, Voice
    cfg = computer.cfg
    print(f"corpus      {computer.corpus.status()}  ({cfg.corpus})")
    print(f"navigator   {cfg.navigator}, planners: {', '.join(p.label for p in computer.planners)}")
    if cfg.relation_lexicon:
        lex = getattr(computer.corpus, "lexicon", None)
        print(f"lexicon     {f'{len(lex.by_word)} words' if lex else computer.controller_error}")
    if cfg.navigator_controller:
        print(f"controller  {computer.controller_error or 'loaded: ' + cfg.navigator_controller}")
    if cfg.neural_registry:
        n = computer.neural
        if n is None:
            print(f"neural      off: {computer.neural_error}")
        else:
            m = n.manifest
            trained_on = m.get("training_data", {}).get("corpus_version", "")
            same = trained_on == computer.corpus.version
            print(f"neural      {n.model_id} (schema v{m['schema']['version']}, held-out top-1 "
                  f"{m.get('metrics', {}).get('heldout_top1', 0):.0%}); trained on "
                  f"{'this corpus' if same else 'another corpus version: ' + trained_on[:19] + '…'}; experience log "
                  f"{'on' if computer.experience else 'off'}" + (f" ({computer.neural_error})" if computer.neural_error else ""))
    if cfg.plugins_dir:
        for p in computer.plugins:
            print(f"plugin      {p.name or p.folder.name}" + (f"@{p.version}" if p.version else "") +
                  (f": REFUSED: {p.error}" if p.error else ": ok"))
    if computer.components.names():
        print(f"components  {', '.join(computer.components.names())}")
    try:
        from .governor import Governor
        g = Governor.from_config(cfg)
        st = g.status()
        pol = (f"policy {st['policy']['path']} (operators: {', '.join(st['policy']['operators']) or 'none'})"
               if st["policy"]["loaded"] else st["policy"]["error"])
        aud = ("audit " + ("DEGRADED: " + st["audit"]["error"] if g.degraded else
                           f"ok, {st['audit']['lines']} lines" if g.audit.persistent else "in memory only"))
        print(f"governor    {pol}; {aud}; e-stop {'ENGAGED' if g.latch.engaged else 'clear'}: {g.latch.why}")
    except Exception as e:  # noqa: BLE001 - --check reports, it doesn't stop
        print(f"governor    not checked: {type(e).__name__}: {e}")
    if computer.llm:
        ok = computer.llm.available(force=True)
        print(f"mini LM     {'ok: ' + computer.llm.label if ok else 'not reachable at ' + cfg.llm_endpoint + ' (' + computer.llm.error + ')'}")
    else:
        print("mini LM     off")
    k = computer.knowledge
    print(f"rules       {computer.rules_error or (cfg.rules_file or 'built-in')}: "
          f"{len(k.disjoint)} disjoint groups, {len(k.complete)} complete relations, "
          f"{len(computer.pathways['knowledge'].rules)} knowledge-pathway rules")
    print(f"compute     {computer.compute_error or computer.engine.status()}")
    reg = computer.registry
    print(f"capabilities {len(reg.caps)} declared and checked; off: "
          + (", ".join(f"{n} ({why})" for n, why in sorted(reg.off.items())) or "none"))
    if computer.corpus.ready:
        say = {"verified": "verified: the shards match the hash recorded at build",
               "computed": "computed from the shards at start-up (no hash recorded: rebuild with tools/build_shards.py)",
               "trusted": "TRUSTED, NOT VERIFIED (corpus_integrity = \"trust\")",
               "mismatch": "MISMATCH: the shards don't match the hash recorded at build; rebuild or restore data/",
               "changed": "CHANGED: a shard has changed on disk since it was checked"}
        print(f"version     {computer.corpus.version[:23]}… {say.get(computer.corpus.integrity, computer.corpus.integrity)}"
              f" (mode {computer.cfg.corpus_integrity})")
    print(f"analysis    {'on: runtime, daily energy, energy, power; efficiency assumed ' + format(cfg.analysis_efficiency, '.0%') if computer.analyst else 'off'}")
    print(f"diagnostics {'on' if computer.diagnostics else 'off'}")
    print(f"state       {computer.state_error or (str(computer.state_path) if computer.state_path else 'in memory only (state_file not set)')}")
    if computer.llm_priority:
        ok = computer.llm_priority.available(force=True)
        print(f"priority LM {'ok: ' + computer.llm_priority.label if ok else 'not reachable at ' + cfg.llm_endpoint_priority}")
    print(f"voice       {Voice(cfg).kind}")
    print(f"ears        {'vosk ready' if Ears(cfg).ready else 'off (no vosk model / arecord)'}")
    print(f"gpio        {'ok' if HAVE_GPIO and Body(cfg).has_hardware else 'none'}")
    from .iching import Entropy
    print(f"chance      {Entropy().source}")
    try:
        import resource
        print(f"memory      {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024} MB (this process, peak)")
    except ImportError:
        pass
    r = computer.think("should I run a system check?")
    show(r, verbose=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m computer", description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--text", action="store_true", help="terminal session")
    ap.add_argument("--speak", action="store_true", help="with --text: also speak the output")
    ap.add_argument("--cast", metavar="QUESTION", nargs="?", const="", help="one random I Ching sample")
    ap.add_argument("--method", choices=["coins", "yarrow"])
    ap.add_argument("--wise", action="store_true", help="one stored aphorism")
    ap.add_argument("--check", action="store_true", help="run a system check")
    ap.add_argument("--plan", metavar="QUESTION", help="show the navigator's plan and evidence for a question")
    ap.add_argument("--envelope", metavar="QUESTION", help="answer one question and print its result envelope (JSON)")
    ap.add_argument("-v", "--verbose", action="store_true", help="show how each answer was reached")
    a = ap.parse_args(argv)

    cfg = config_mod.load(a.config)
    if a.method:
        cfg.method = a.method
    if a.wise:
        print(instinct.wise_saying()[0])
        return 0
    if a.cast is not None:
        p = instinct.perspective(a.cast, method=cfg.method)
        print(p.reading.primary.glyph())
        print(p.brief)
        print(p.more)
        return 0
    computer = Computer(cfg)
    if a.envelope:
        import json
        print(json.dumps(computer.think(a.envelope).envelope, indent=2, ensure_ascii=False))
    elif a.plan:
        show_plan(computer, a.plan)
    elif a.check:
        check(computer)
    elif a.text:
        text_chat(computer, speak=a.speak, verbose=a.verbose)
    else:
        device_mode(computer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
