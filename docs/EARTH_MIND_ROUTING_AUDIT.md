# Earth Mind — intent and routing audit

*Written before any code was changed. Branch `esi/phase-1` at `02405f3`.
Every failure below was reproduced headlessly through the real terminal
screen (`TerminalScreen.submit`, as `tests/test_mind_terminal.py` drives it),
the real capability layer on this build machine, and CDOS Computer embedded
and reading the Phase 1.5 corpus (1,229,268 facts). No language model was
running — the case that matters most, and the one where routing alone
decides the answer. Each was also run on the code from before ESI
(`3012e53`), so that what ESI caused is separated from what it inherited.*

---

## 1. Summary

All five failures reproduce. They come from **six** causes, not five, and
two of them are not language problems at all:

| | Question | Today | Before ESI | Main cause |
|---|---|---|---|---|
| A | Which applications are using the most resources? | CDOS: "nothing on it" | same | No analysis ranks by CPU; *and* the CPU figure it would need is never measured (§4.4) |
| B | Which processor does this computer have? | CDOS: dictionary senses of *processor* | same | A question about *this computer* falls through to word knowledge (§4.3) |
| C | What processes are using the most CPU? | **processor model** | CDOS: "nothing on it" | **ESI regression**: word order dropped (§4.2); plus §4.4 |
| D | What is the difference between RAM and storage? | **memory reading** | CDOS: "RAM is a kind of volatile storage" | **ESI regression** on an older defect: topics matched, not questions (§4.1) |
| E | How many cores does this processor have? | "2 cores" | same | The reading counts *logical* processors and is labelled "cores" (§4.5) |

**C and D are regressions introduced by ESI phase 1.** Both used to fail
honestly; ESI made them answer confidently and wrongly. The paraphrase sets
did not catch it because none of them contained a knowledge question or a
comparison. That is recorded here plainly because it is the most useful
lesson in this audit: ESI's evaluation measured whether it *found* the
machine's questions, never whether it *left alone* the world's.

The routing table in the brief does not need a new intent subsystem. Its
categories already exist in Earth Mind's router and the corpus's analyses
(§5). What is missing is one decision — **is this asking what something
*is*, or how *this machine's* one is?** — made before the corpus may claim a
sentence, and one honest fallback when nothing is recognised.

---

## 2. The pipeline, as it is

### Terminal (where the failures were seen)

```
TerminalScreen.run_line                         screens/terminal.py:552
 ├─ "!cmd"  → shell, untouched
 ├─ "?text" → Earth Mind, forced
 ├─ _answer_question → _ask_mind(priority_only) :787 / :683
 │     └─ only a *priority* route is taken here: analysis, diagnosis,
 │        installed program, game character
 ├─ CommandTranslator.translate                 translator.py:273
 │     "how much memory is free" → `free -h`, run as a command
 └─ _ask_mind (any route)                       screens/terminal.py:683
       └─ EarthMind.route → Router.route        mind/mind.py:210, mind/router.py:98
       └─ EarthMind.handle → _dispatch          mind/mind.py:214, :241
```

### Earth Mind's routing decision (`mind/router.py:98`)

```
classify(text)                                  companion/intent.py:187
  sensitive? → conversation (never the sampler)
  remember/forget? → World Model
  "talk to …"? → a character
  ┌──────────────────────────────────────────────────────────────┐
  │ _system(text)                               router.py:143    │
  │   SystemSubsystem.resolve                   mind/system.py:94│
  │     CapabilityCorpus.resolve                capability_corpus.py:577
  │       phrase as written: adjustment, %, analysis, phrasebook │
  │       read by meaning (ESI)  _as_meaning :644 → lexical.py:422
  │   conversation guard (talk that names a word or two)         │
  │   → SYSTEM_QUERY (reading/analysis) · MIXED (diagnosis)      │
  │     · SYSTEM_ACTION (proposal to the gated shell path)       │
  └──────────────────────────────────────────────────────────────┘
  if nothing claimed it:
    kind knowledge/calculation → KNOWLEDGE: CDOS first, Companion after
    kind task                  → handoff to the shell's command path
    otherwise                  → CONVERSATION: the Companion
```

**The routing rule, in one line: if the capability corpus recognises a
topic word, it is a system question; if not, and it is shaped like a
question, it is a knowledge question.** Every failure is one side of that
line or the other.

### Answering

| Route | Where the answer comes from |
|---|---|
| SYSTEM_QUERY | `SystemSubsystem.answer` (`mind/system.py:123`) → capability readings → `CapabilityCorpus.say_result` / `explain` |
| MIXED (diagnosis) | the same findings; the Companion may reword them, checked figure by figure |
| KNOWLEDGE | `Companion.respond` (`companion/engine.py:148`) → `ComputerLink` (`voice/computer_link.py`) → CDOS Computer's corpus and navigator |
| SYSTEM_ACTION | a `Proposal`, carried out by `Shell.carry_out` on the gated path |

### Voice

`Shell.handle_utterance` (`core.py:567`): vocabulary, compositions and the
capability corpus compete on how much of the sentence they recognised; then
the learned layer; then `run_capability_intent` (`core.py:2367`); then Earth
Mind. The corpus is the same object, so every corpus defect below is a voice
defect too.

### Files responsible

| Concern | File |
|---|---|
| Language understanding | `earth_os/lexical.py` (ESI), `earth_os/semantics.py` (learned aliases), `earth_os/companion/intent.py` (question shape) |
| Recognising machine questions | `earth_os/capability_corpus.py` (phrasebook, analyses, ESI matching) |
| Routing | `earth_os/mind/router.py`, `earth_os/mind/system.py`; `earth_os/screens/terminal.py` (terminal precedence) |
| Dispatch | `earth_os/mind/mind.py` (`_dispatch`), `earth_os/core.py` (voice tiers, gated actions) |
| Readings | `earth_os/platform/linux_adapter.py` (below the boundary) |
| Knowledge | `earth_os/voice/computer_link.py` → `cdos-computer/computer/` |
| Response text | `capability_corpus.say_result` / `explain`; `companion/engine.py`; `screens/terminal.py:_show` |

---

## 3. Reproduced

Through the terminal, no model, real corpus. "Before ESI" is `3012e53`.

| Question | Before ESI | Now |
|---|---|---|
| A. Which applications are using the most resources? | CDOS: nothing on it | same |
| B. Which processor does this computer have? | CDOS: *"Processor can mean: … a business engaged in processing…"* | same |
| C. What processes are using the most CPU? | CDOS: nothing on it | **"Intel(R) Xeon(R) Processor @ 2.30GHz"** |
| D. What is the difference between RAM and storage? | CDOS: *"Ram is a kind of volatile storage."* | **"430.9 MB of 7.8 GB in use, 7.4 GB free"** |
| E. How many cores does this processor have? | "2 cores" | same |
| what is a cpu | CDOS: the definition ✓ | **the processor model** |
| what is ram | CDOS: sheep, battering ram, computer memory | **the memory reading** |
| what is the difference between *memory* and storage | **the memory reading** | same |
| what is the temperature of the sun | **"How hot the machine is: Nothing to report."** | same |
| how much memory is free → *and the cpu?* | — | conversation: "My language model isn't running" |
| how much memory is free → *what about the disk* | — | CDOS: *"Disk can mean: a flat circular plate…"* |

---

## 4. Root causes

### 4.1 The corpus matches topics, not questions — D, and more

A phrase is matched by containment, and seven reading phrases are a single
topic word: `memory`, `battery`, `temperature`, `volume`, `uptime`,
`hostname`, `screenshot`. Any sentence that names one is read as asking for
this machine's reading of it — "what is the difference between memory and
storage" reads the memory; "what is the temperature of the sun" reads the
machine's thermometer. Nothing asks *what* is being asked about the topic:
what it **is** (knowledge), which one this machine **has** (identity), how
it is **doing** (measurement), or what is **using** it (ranking).

This predates ESI. ESI widened it twice: its six one-concept meanings carry
the same flaw *with synonyms* ("ram" is memory, so "what is ram" now reads
the memory), and §4.2.

### 4.2 ESI dropped word order — C (regression)

`lexical.py` reads a phrase as an unordered set. "what processor" became
`{what, cpu}`, which "**what** processes are using the most **cpu**" and
"**what** is a **cpu**" both contain, so both answer with the processor
model. The phrase meant *what [kind of] processor*; the order carried that.
The ESI context set had already logged this exact miss ("what is using the
most cpu" → cpu model) as its one *word order* case; it was not yet seen to
be a regression against the terminal.

### 4.3 The fallback for an unrecognised question is word knowledge — A, B

When the corpus claims nothing, `classify` (`companion/intent.py:109`) calls
any sentence starting *what/which/how many…* a knowledge question, and the
router sends it to CDOS. CDOS answers about the **word**: "Which processor
does this computer have?" gets the senses of *processor*. Nothing in routing
notices that the question is about **this computer** — "this computer", "do
you have", "my", "is using", "right now" — and that word knowledge cannot
answer it. B fails only because the phrase is "*what* processor" and the
person said "*which*".

### 4.4 Per-process CPU is never measured — A, C (not language)

`linux_adapter.py:353`, present since EARTH OS 1.0.0:

```python
# Estimate CPU percent (simplified; real implementation needs history)
cpu_percent = 0.0
```

Every process reports 0.0 % on Linux. So no ranking by CPU is possible from
any wording, however well understood — and `performance_report`'s "*X is
using the most processor time*" finding has **never** fired on Linux;
`rank_processor` sees all zeros and correctly says nothing, so the gap was
silent. This is the defect `AGENTS.md` §3 names — a placeholder number
where a reading was not taken — and would be its seventh recorded instance.

There is also no analysis that ranks by CPU, or by "resources", even if the
figure existed: `ANALYSES` has `top_memory`, `memory_report` and
`performance_report` only.

### 4.5 The core count is logical processors, labelled cores — E (not language)

`hardware.cpu_count` is `len(os.sched_getaffinity(0))`
(`linux_adapter.py:569`): the logical processors *this process may run on*.
The catalogue calls it "how many processor cores", and `say_result` says
"N cores" (`capability_corpus.py:831`). On a machine with hyper-threading
"4 cores" may be two cores; under a CPU affinity limit it may be fewer than
the machine has. No reading of physical cores exists in the catalogue.

### 4.6 System questions have no follow-up context

"and the cpu?" and "what about the disk" after a memory question carry no
topic of their own. Earth Mind keeps conversation history for the Companion,
but a system answer leaves nothing a follow-up can use, so the second
sentence is routed alone.

---

## 5. Does it need an intent-classification layer?

No new subsystem. The brief's categories already exist; what is missing is
the decision between them:

| Brief's intent | Already in Earth OS as |
|---|---|
| KNOWLEDGE_QUERY, CONCEPT_COMPARISON | router `KNOWLEDGE` → CDOS |
| HARDWARE_IDENTITY | a `READING` of an identity capability (`cpu_model`, `hostname`, `distro`, `kernel`) |
| SYSTEM_STATUS, RESOURCE_MONITORING, STORAGE_MONITORING | a `READING` (`memory`, `disk`, `cpu_load`, `battery` …) |
| PROCESS_MONITORING | an `ANALYSIS` (`top_memory`; none for CPU yet) |
| PERFORMANCE_DIAGNOSIS | a `DIAGNOSIS` (`performance_report`) |
| CONVERSATIONAL_QUERY | router `CONVERSATION` |
| UNKNOWN_INTENT | today: falls to KNOWLEDGE. Should be: "recognised the topic, not the question" |

Adding a parallel classifier would give the same sentence two places to be
misunderstood. The decision belongs inside the reading ESI already makes,
where the corpus and the router both see it.

---

## 6. Minimal proposal

Six steps, each its own commit, each measured before and after, in this
order. None calls a model. P1–P3 and P5 are above the boundary; P4 is below
it and is kept in a separate commit from any corpus change (`AGENTS.md` §7.3).

**P1 — Question words attach to their noun (ESI).** The 3A mechanism,
extended: *what/which* bind to the next noun as possessives do, so "what
processor" requires `what>cpu`, and "what processes … cpu" carries
`what>process`. *Which* reads as *what*. Fixes C's wrong answer and "what is
a cpu" → model; makes B resolve. About 30 lines in `lexical.py`; the
phrasebook-unchanged test still applies.

**P2 — World or machine: a question's frame (ESI → corpus).** A reading
records two things, from a handful of function words rather than phrases:

* *asks what something is* — "what is **a**/**an** X", "what is X" (bare,
  ending the sentence), "what does X mean", "define/explain X", "how does X
  work"; *compares* — "difference between", "X vs Y", "X or Y"; *belongs to
  something else* — "temperature **of the sun**";
* *points at this machine* — the 3A attachments (`user>…`, `computer>…`),
  "this computer/machine", "do I/you have", "installed", "right now",
  "is using".

A sentence that asks what something is, or compares, or names another
owner — and does not point at this machine — is **not claimed** by the
corpus's readings and analyses (acts are unaffected), so the router sends it
to CDOS. Fixes D, "what is a cpu", "what is ram", and the two inherited
cases (memory vs storage; the sun).

**P3 — An honest fallback (router).** When the corpus claims nothing but
the sentence points at this machine and names a machine concept, route to
the system subsystem with an explicit *"I recognised X but cannot read
that"*, instead of CDOS's word senses. This is the brief's response
validation by construction: a machine question cannot reach a dictionary
answer. Fixes A's and B's *source* even before P4/P5 exist.

**P4 — Measure per-process CPU (adapter, below the boundary).** Two reads
of `/proc/<pid>/stat` (utime + stime) against `/proc/stat`'s total, a short
interval apart — or the difference from the previous listing, cached — and
**`None` when it cannot be measured, never 0.0**. `rank_processor` already
treats a missing figure as no data. Measure the cost of the second sample.
Fixes the silent `performance_report` gap as a side effect.

**P5 — Rank by CPU and by resources (analyses).** `top_processor` and
`top_resources` in `ANALYSES`: readings only (`process.list`, plus the
memory reading), so they add no capability (`AGENTS.md` §2). Phrases, and a
`resource` concept in ESI. Fixes A and C. Says plainly when P4's figure is
unavailable rather than ranking zeros.

**P6 — Say what the core count is.** `say_result` reports "N logical
processors" (above the boundary, now). Physical cores need a capability —
a reading of the processor topology (physical cores, logical processors,
sockets) from `/proc/cpuinfo` — and the catalogue summary "how many processor
cores" is wrong too. `platform/capabilities.py` is human-write-only, so both
are **yours to approve**; I would prepare the change for review, not make it.

**Deferred: follow-ups (§4.6).** Measured failing; it needs a design (the
last system topic held per interface, a follow-up substituting its concept),
and it should come after P1–P5 are measured.

**Not proposed:** a new classifier, more model calls, and a longer
phrasebook. P1 and P2 are compositional — a few function words and the
attachment mechanism — not one phrase per question.

---

## 7. Tests, against the brief's ten

A new developer-labelled set, `tests/mind_routing.jsonl`, scored on the
**route** (knowledge / reading / analysis / diagnosis / conversation) as
well as the answer, kept apart from the ESI sets and from the independent
set. Each line records which of the ten it covers.

| Brief | Covered by |
|---|---|
| 1 Knowledge vs hardware identity | "what is a cpu" / "which cpu do I have" |
| 2 Identity vs live monitoring | "which cpu" / "how busy is the cpu" |
| 3 CPU utilisation vs model | C; "what is using my cpu" |
| 4 RAM definition vs measurement | D; "what is ram" / "how much ram is free" |
| 5 Storage definition vs measurement | "what is an ssd" / "how full is my disk" |
| 6 Process ranking by CPU | C, A — after P4/P5 |
| 7 Process ranking by memory | existing `top_memory` phrasings |
| 8 Many ways to ask | three phrasings per intent at least |
| 9 Unknown and ambiguous | "what is the temperature of the sun", "my memory is terrible" |
| 10 Multi-turn | "and the cpu?" — recorded failing until the deferred step |

The five terminal failures become tests through `TerminalScreen`, as
`tests/test_mind_terminal.py` does, so they are checked where they were seen.
The ESI sets are preserved and re-measured after every step; the
phrasebook-unchanged, no-new-ambiguity and false-positive ceilings stay.

---

## 8. Cost, before any change

| | |
|---|---|
| `EarthMind.route` | 1.34 ms per question (no model, no reading) |
| A reading answered in the terminal | 50–70 ms |
| A CDOS answer | ~120 ms; the first after start ~1.1 s (corpus load) |
| ESI's index | built in 2.8 ms, 99 KB |

P1–P3 should add well under a millisecond and kilobytes; P4 adds a second
`/proc` sample, which will be measured, and must not run on the frame clock.

---

## 9. In the System Evolution Center

Earth Mind already returns, for every request, the route, its reasons
(`Response.why`), the source, whether it was checked, and `elapsed_ms`.
Nothing new needs computing; it needs keeping. A small ring buffer of recent
routing decisions in memory (counts and timings always; sentence text only
when a setting allows, redacted by `mind/redact.py`) would give the
**Diagnostics and Reports** section:

* route counts by kind, and the share answered vs unanswered;
* corpus *declined* vs *claimed*, with P2's reason (asks what it is /
  compares / another owner);
* fallbacks taken by P3 — *machine questions the machine could not read*,
  which is the most useful list in it, because each is a missing reading or
  analysis;
* response-validation refusals;
* `route` latency p50/p95 and reading/CDOS latency;
* ESI's index size and the bridge's approval counts, already available.

A routing *mistake* cannot be counted automatically — if it could, it would
not be made. That is what the labelled sets measure; the Center should show
their last scores beside the live counts, and never present the counts as
accuracy.
