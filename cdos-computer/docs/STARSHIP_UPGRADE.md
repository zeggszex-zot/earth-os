# CDOS: the starship-computer upgrade, phase 1

> **Since then:** Milestone 1 added the capability registry, result envelopes, a content-hash corpus version and
> requirement checks (`MEASURE` / `REQUIRE`). See [`MILESTONE_1.md`](MILESTONE_1.md). This page describes phase 1 as it
> was; its test counts are from before Milestone 1.

This covers the assessment of the existing system, the plan, the first upgrade that was built (quantitative
reasoning: calculations, estimates, clarification, follow-ups, diagnostics, persistent working state), how it
was measured, and what is still open. Every number here was produced by the commands quoted beside it, on the
test corpus (`tests/mini_corpus.py`), on a cloud Linux container rather than a Pi. The real `data/` (Phase 1.5,
1.46M facts) wasn't available in this session, so **nothing here has been measured on the real corpus.**

## 1. Architecture assessment (what was already there)

CDOS Computer was already most of what the brief asks for. Verified by reading the code and running it:

| Brief asks for | Already present | Where |
|---|---|---|
| Deterministic intent parsing | Regex router with policy (care / health / money never reach the coins) | `computer/router.py` |
| Typed, validated plans | Plan language (FIND, GET, WHO, FILTER, COMPARE, COUNT, TEST, LOGIC, RANK, EXCEPT), validator, GBNF grammar | `computer/plan.py` |
| Bounded execution with evidence | Executor: 10 steps, fan-out 8, IS_A depth 4, 6 backtracks; every sentence rendered from fetched facts | `computer/navigator.py` |
| Multi-hop graph traversal | GET chains, inheritance through IS_A, reverse (WHO) lookups | `computer/navigator.py` |
| Truth semantics beyond yes/no | TRUE / FALSE / UNKNOWN / CONFLICT; FALSE needs a stated rule; conflicts are said, not resolved | `computer/logic.py`, `computer/rules.py` |
| Follow-ups | `planner.Context`: "another one", "what about sad", "and the opposite?" | `computer/planner.py` |
| Temporal facts | `start`/`until` on every fact; "in 1990" filters by year | `computer/corpus.py` |
| An optional model that isn't required | Rule planner is the default; LM / controller / proposer are optional and checked | `computer/planner.py`, `computer/controller.py`, `computer/proposer.py` |
| Low-power operation | Only the subject set in RAM; shards read on demand with an LRU of 24; compute profiles (eco when hot/busy) | `computer/corpus.py`, `computer/compute.py` |
| Reproducible evaluation | Five fixed held-out sets, `evaluate()`, the learning experiment with leak checks | `tests/*.jsonl`, `computer/navdata.py` |

**Storage and indexes, as they are.** The corpus is a set of gzip shards written by `tools/build_shards.py`:

| Structure | What it indexes | Memory |
|---|---|---|
| `subjects.bin` | every subject key (the entity directory) | held in RAM as a set (~15 MB on the real corpus) |
| `s/NNN.bin` (FNV-1a of the subject, mod `s_shards`) | subject → its facts (a hash-partitioned subject/predicate/object store) | read on demand, LRU of 24 shards |
| `v/NNN.bin` (FNV-1a of the value key) | value → subjects that point at it (the object index; WHO uses it) | same |
| `meta.json` | counts, shard counts, relation names | small |

There is no predicate index, no inverted text index over definitions or names, no range index, no positional
index, and no incremental update path: a change means rebuilding the shards. Facts had **no identifier**, so
evidence could only quote facts, not cite them.

**What was missing, measured.** Probing the turn loop (`Computer.think`) with the kinds of request in the brief
showed the main gap was quantitative. No plan step could do arithmetic on retrieved numbers, there was no way to
compute from stated quantities, no clarification for a missing input, no "at half the load" follow-up, and no
system-status reading. Worse, two of those questions got **misleading part-answers**: "the combined population of
France and Germany" was answered with France's population alone, and "population density" with the population.

Baseline, measured before any change (`python3 tools/bench_dialogue.py --root ../cdos-computer.orig`):

| set | navigate(), rules: right / wrong / abstained | Computer.think: right / wrong / abstained |
|---|---|---|
| navigator_questions (27) | 27 / 0 / 0 | 27 / 0 / 0 |
| lexical_questions (37) | 37 / 0 / 0 | 35 / 0 / 2 |
| lexical_paraphrases (30) | 30 / 0 / 0 | 28 / 0 / 2 |
| controller_heldout (42) | 11 / 0 / 31 | 21 / 8 / 13 |
| lexicon_heldout (28) | 12 / 0 / 16 | 13 / 3 / 12 |
| **analysis dialogues** (34 conversations, 53 turns) | | **6 / 10 / 37** |

The first five rows reproduce the README's published figures exactly. The `Computer.think` column scores what the computer
actually says, lookup included, and counts any reply that admits it can't as not committed. Unit tests at
baseline: 212 passed, 3 skipped.

## 2. Plan (prioritised)

Ordered by value to the brief's core loop ("receive a request, identify the operation, retrieve, reason,
calculate, ask when information is missing, answer concisely and verifiably"), and by how measurable each
step is on the data available here.

1. **Quantitative reasoning (built in this phase).** CALC over corpus numbers; an analysis engine for
   estimates from stated quantities; clarification; what-if follow-ups; explanations; evidence with fact
   ids; diagnostics; persistent working state. Biggest gap, and fully testable on the test corpus.
2. **Measure on the real corpus.** Run `tools/bench_dialogue.py` and the existing sets against `data/`. Check the
   CALC patterns against real relation names (AREA, POPULATION and so on), and check what unit codes the
   Wikidata rows use.
3. **An inverted term index**, *if* step 2 shows that subject-name and relation-word matching misses things a
   term index would find. The design is below (§4). Not built, because there's nothing here to measure it against.
4. **Incremental updates:** a small writable delta segment beside the shards, keyed by the new fact ids,
   merged at build time. Needed before any "store a verified conclusion" feature.
5. **Hypothesis testing and stored conclusions**, after 4: the evidence structure (§5) is already the record
   format a stored conclusion would need.
6. **Semantic (vector) index:** only if 2 and 3 leave a measured paraphrase gap that lexical matching can't close.

## 3. What was built: query planning and dialogue

### 3.1 The turn, now

```
 text ──► router.classify ──► care?  ── yes ─► a person
                 │
                 ▼ (not care, not health/money)
          Analyst.handle(text)  ── an analysis turn? ─► Answer (clarify | answer | explain | refuse)
                 │ no
                 ▼
          status intent ──► Diagnostics (read /sys, /proc, meta.json; "not available" if it can't)
          calc / clock / smalltalk ──► Logic (unchanged)
          instinct ──► the I Ching (unchanged)
          knowledge ──► pathway rules ──► navigate() (rule planner: CALC patterns first) ──► lookup ...
                 │
          analyst.idle() if another faculty answered; state saved only if it changed
```

The analyst runs before the other faculties, but it only claims a turn when one of these holds:

- **the turn names a formula:** a trigger phrase ("how long … run/last/power", "runtime", "battery life",
  "daily … energy", "how much energy", "power … of" with a voltage *and* a current stated) *plus*
  electrical context (a unit, an electrical word, or a corpus thing with electrical figures). So "how long did
  the Roman Empire last" and "how long can a cheetah run" go through to knowledge.
- **the last turn was the analyst's**, and this one answers its open question ("3.7 volts", "under load", "both").
- **the last turn was the analyst's**, and this one is a change to it, marked by a cue word (what if / with / at /
  and / assume / instead …) and naming something the formula has: a scale word aimed at an input ("half the
  load"), a quantity of the right dimension, a corpus thing, or "no losses". It's not a change if it parses as
  maths ("and what is 15% of 80"), and a percentage only counts as an efficiency next to an efficiency word.

Otherwise it returns `None` and the turn proceeds exactly as before. Health and money questions never reach it.

### 3.2 The plan inside an analysis

A formula is typed data (`computer/analysis.py`, `FORMULAS`):

| formula | inputs (dimension) | result |
|---|---|---|
| `runtime` | battery (energy, or charge × voltage), load (power, or current × voltage), efficiency (fraction, default **assumed** 85%) | battery × efficiency ÷ load |
| `daily` | power, operating hours per day | power × hours |
| `energy` | power, duration | power × time |
| `power` | voltage, current (both must be stated) | voltage × current |

Execution for one question is an explicit sequence, shown in the `-v` trace:

```
analysis: runtime (runtime = battery energy x conversion efficiency / load power)
stated: 50 Wh (energy), 5 W (power)
corpus: RASPBERRY_PI_5: POWER_DRAW 12 W [3f0c…]          (when a named thing has figures)
analysis: 50 Wh x 0.85 / 5 W = 8.5 h (ESTIMATE)
```

Each input is resolved in this order, and its provenance is kept:

1. **stated** in this turn (quantities are parsed with units; "fifty watt hours" works, for voice). Names of
   corpus things are taken out first, so the "2 W" in "Raspberry Pi Zero 2 W" isn't read as 2 watts.
2. **composed** from two stated halves (10,000 mAh × 3.7 V), pairing each charge or current with the
   *nearest* voltage. If the load has a current but no voltage of its own, the battery's voltage is used,
   and the answer says so.
3. **corpus:** a named thing's figure of that dimension, found by unit rather than relation name, and cited by
   fact id. With **two or more different figures** (idle 3 W, load 6.4 W), the engine asks which one, listing
   both. It never picks.
4. **assumed:** only the efficiency has a default, and the answer always says it ("assuming 85% conversion
   efficiency"). It's configurable (`analysis_efficiency`).
5. **missing:** asked for, one input at a time, in the brief's style ("Specify the battery's usable energy
   capacity."), with any corpus figure already found said first.

Inputs are validated before anything is computed. Zero or negative loads, an efficiency above 100% and more
than 24 hours a day are all refused with the reason.

### 3.3 Arithmetic over corpus facts: `CALC`

A new step in the plan language, validated like the others and included in the llama.cpp grammar:

```
$5 = CALC RATIO $3 $4 heavier     SUM (2-3 parts) | DIFF | RATIO: one measurement across things
$4 = CALC DIV $2 $3 its population density      DIV: any two numbers (population / area)
```

- Its inputs must come from GET, so it can only do arithmetic on facts the plan fetched.
- SUM, DIFF and RATIO need the **same relation** for every part, and units that measure the same thing
  (KM and M are converted; KG and KM are refused).
- A missing number is a gap, and the gap is said. When a `calc` plan is unsupported, `navigate()` **stops**.
  Another reading isn't allowed to answer part of the question ("combined population" → one population).
- `Result.derived` records `{op, relation, expr, result, unit, facts: [ids]}`, and `Result.final` is
  `{"kind": "calc", …}`, so the proposer's checker holds a model's wording to the computed value.
- If a second attribute is named ("the ratio of the mass of Jupiter to the radius of Saturn"), it's kept, and
  the executor then refuses. The first version silently used mass for both. The probe caught it and a test
  covers it now.

Rule-planner patterns: "how many times X-er is A than B", "the ratio of …", "how much X-er is A than B",
"the difference in X between A and B", "the combined/total X of A, B and C", "A and B's X combined",
"the population density of A" / "how densely populated is A".

### 3.4 Response style

One sentence for a direct result: the result first, then its inputs, then any assumption. A what-if is said as one.

```
The estimated runtime is 8.5 hours: 50 watt-hours at 5 watts, assuming 85% conversion efficiency.
With the load at 2.5 watts, the estimated runtime is 17 hours: 50 watt-hours at 2.5 watts, assuming 85% conversion efficiency.
Specify the battery's usable energy capacity.
My corpus lists two power figures for Raspberry Pi 4: 3 watts (idle) and 6.4 watts (load). Specify which one.
Jupiter's mass is 1.898e27 kilograms, and Saturn's is 5.683e26 kilograms, so Jupiter's mass is 3.34 times Saturn's.
I know about France, but my corpus doesn't say its area.
The processor temperature is 54 degrees Celsius.
```

"How did you work that out?", "why?" and "what did you assume?" give the formula, every input with where it
came from (as you said / from my corpus (…) / assumed, because … / changed from the last answer), the
arithmetic, and whether the result is a calculation or an estimate.

## 4. Indexes

**Built: stable fact identifiers.** `Fact.id` is a 64-bit BLAKE2b hash of the fact's content (subject,
relation, kind, value, unit, start, until, source, qualifier), as 16 hex digits. The same fact keeps the same id
across rebuilds, a changed fact gets a new one, and no shard format changes. There are no collisions on the test
corpus (a test checks this). On 1.46M facts the expected number of 64-bit collisions is about 6 × 10⁻⁸. Evidence
and CALC records cite these ids. Any future secondary index would store them.

**Not built, and why:** the brief says to keep only the indexes that testing supports, and the test corpus
(175 facts and 59 subjects; 185 and 63 with the test devices) can't show that any index helps: every lookup there is already one shard read. Proposed
designs, ready to measure on the real corpus:

| index | format | justifies itself if… |
|---|---|---|
| inverted term index | `t/NNN.bin` (FNV-1a of the term): `term \t fact_id \t subject \t relation`, built from subject names, definitions and relation words by `build_shards.py`, stamped with the corpus version in `meta.json` | step 2 shows paraphrases that name no subject key but share rare words with a definition |
| predicate index | `p/<RELATION>.bin`: subjects that have the relation | "which countries have a population over 50 million": range questions across subjects |
| delta segment | `delta.jsonl` (append-only, fact rows plus ids and a tombstone flag), read after the shards and merged by the next build | approved corrections need to be live without a rebuild |
| range / positional / vector | — | not justified by anything measured yet |

**Stale-entry rule for any of them:** every derived index records the `meta.json` corpus version it was built
from. The reader refuses an index whose version doesn't match, and falls back to the shards rather than serve
stale ids.

## 5. Evidence and verification

Every analysis answer carries a `Finding` (`Reply.evidence`, also written to `computer.log.jsonl`):

| field | holds |
|---|---|
| `claim` | the sentence said |
| `status` | `DERIVED` (every input given or in the corpus), `ESTIMATE` (rests on an assumption), `UNKNOWN` (missing input, or a conflict to resolve) |
| `inputs` | each slot: value, dimension, words, `source` (stated / corpus / assumed / derived / previous / answer), the corpus fact record, notes, the parts it was composed from |
| `steps` | the arithmetic, e.g. `10 Ah x 3.7 V = 37 watt-hours`, `37 Wh x 0.85 / 12 W = 2.621 h` |
| `assumptions` | every assumption with its reason |
| `missing`, `conflicts` | inputs still needed; fact ids of figures that disagree |
| `facts` | corpus fact ids the answer rests on |
| `conditional` | the what-if it was computed under |

The navigator's existing structure (`evidence`, `inherited`, `rules`, `truth`) is unchanged, and `derived` is
added for CALC. Retrieved facts, derived results and estimates are kept apart in both. Nothing is converted
into a corpus fact: the corpus is never written.

## 6. Persistent working state

`computer/state.py`, switched on with `state_file = "computer.state.json"`:

- **Holds only transient task state** (the open calculation and whether a follow-up may refer to it), never
  knowledge. Deleting it forgets nothing the computer knows.
- **Writes are atomic:** a temporary file in the same folder, fsync, then `os.replace`. A crash leaves the old
  state.
- **Writes happen only when the state changed:** a turn that doesn't touch the calculation writes nothing,
  which spares an SD card.
- **Unusable files are ignored:** corrupt JSON, another version, an unknown formula, or older than
  `state_max_age_minutes` (60). The computer carries on and `--check` says why.
- **Effect:** "and at half the load?" and an open "Specify the battery's voltage." both survive a restart
  (tested with two separate `Computer` instances on one file).

## 7. Tests and benchmarks

**Unit tests:** `python3 -m unittest discover -s tests`: **267 run, 264 pass, 3 skipped** (the same 3 as before,
which need numpy or `data/`). That's the original 212 plus 52 new ones in `tests/test_analysis.py`. They cover
quantities and units, runtime (stated, composed, nearest voltage, configured efficiency, the device-name trap,
corpus citations, conflicting figures, two stated figures, refusals), clarification, follow-ups (and what must
*not* count as one), explanations, the questions the analyst must leave alone, CALC (validation, every op, unit
conversion and refusal, gaps, time, a second attribute, the proposer check), diagnostics on a fake `/sys` and
`/proc`, state, fact ids, the qualifier fix, and both dialogue sets end to end.

**Regression, by score** (`python3 tools/bench_dialogue.py`, both trees): all five existing sets are
**identical** to the baseline table above, on both measures.

**Regression, by text** (through `Computer.think` on both trees: the 214 distinct questions in the existing JSONL
sets, including `real_heldout` and `real_mine`, which mostly abstain on this corpus; about 200 questions
generated from the corpus by `navdata.generate`; and 12 small-talk, instinct and sensitive prompts):
**425 questions, 0 replies differ.**

**New behaviour:**

| set | baseline | upgraded |
|---|---|---|
| `analysis_heldout.jsonl`: 53 turns, written before the code | 6 / 10 / 37 | **53 / 0 / 0** |
| `analysis_paraphrases.jsonl`: 32 turns, written after the code, scored blind once | 4 / 5 / 23 | first run **28 / 2 / 2**; after fixes 32 / 0 / 0 |

How to read these:

- **Neither set is a fair measure of generalisation.** I wrote both, and the first was written knowing the design.
- **The paraphrase set's first run is the most honest number here: 28/2/2.** Both wrong answers were real: 4.25
  hours was said as "4.2" (Python rounds half to even), and "France and Germany's population combined?" got the
  old part-answer. Both abstentions were coverage gaps ("how many hours can…", "more massive"). All four were
  fixed. One gold value was changed after the run: "1,050 times" became "1,048 times", because the engine gives
  four figures for a ratio over 1,000 and my gold had assumed three.
- **The probe also found** and fixed: maths after an estimate being read as a what-if ("and what is 15% of 80"
  set the efficiency to 15%), a second stated figure being ignored ("a 5 W and a 3 W device"), and a
  pre-existing phrasing bug, where "(state idle)" was said after the load figure. Each has a test now.
- **Resources:** about 1 ms and 1 CPU-ms per turn, 0.5–0.7 shard reads per analysis turn, and peak RSS
  unchanged within noise (27.8 MB → 26.3 MB for the whole bench process). This is a cloud container, not a
  Zero 2 W.
- **What isn't measured:** SSD writes per hour in real use, and energy. RAPL isn't available here, and the Pi
  doesn't report energy.

## 8. Failure modes and limitations

- **It's tested on a 185-fact corpus.** Real Wikidata rows may store units as `WD_Q…` ids. Those aren't in
  `CORPUS_UNITS`, so their figures are invisible to the analyst and to CALC unit conversion, and same-unit
  comparisons still work. Check `--fix-wikidata`'s unit names on the real build.
- **Entity detection costs shard reads on a real corpus.** For a question that already triggered a formula,
  every word that is a subject (most English words, because of WordNet) costs a shard read until it's cached.
  Expect a few to a dozen reads, tens of milliseconds on a Pi. Not measured.
- **Only four formulas.** Charging time, solar sizing, cost and unit conversion aren't there. Adding one means a
  `Formula` entry, a branch in `_compute` and tests.
- **Follow-ups and new questions are told apart by surface cues.** A what-if needs a cue word and something the
  formula has, and anything that names a formula is new. Cases like "with a 200 Wh battery, should I go
  camping?" go to the analyst.
- **The efficiency default is a stated assumption, not a measured value.** 85% is typical for a
  battery-and-converter path, but it's wrong for a load running straight off the battery. Say "no losses" or
  give a figure.
- **CALC uses one fact per part:** the latest dated value. It doesn't average, and it doesn't pick between two
  undated values for the same relation; it takes the first in corpus order. The conflict-detection rules
  (`complete` relations) still apply to the GETs.
- **The density pattern assumes an area relation** is named with "area". It's untested on the real corpus.
- **Diagnostics read Linux paths**, so other systems get "not available". Battery figures need a fuel gauge in
  `/sys/class/power_supply`, and a USB power bank has none.
- **Pre-existing, not fixed:** "what is France and Germany's population" (not a sum) still answers for France
  only. "And what is 15% of 80" isn't parsed as maths, because the calculator doesn't strip a leading "and".

## 9. Completed, incomplete, next

**Completed (this phase)**
- Architecture assessment and baseline (§1); `tools/bench_dialogue.py` measures both trees on the same questions.
- `computer/quantity.py`: units, dimensions, number words, spoken formatting with round-half-up.
- `computer/analysis.py`: typed formulas, provenance-tracked slots, clarification, choices instead of silent picks,
  what-ifs, explanations, validation, `Finding` evidence.
- `CALC` in the plan language, validator, grammar, executor, renderer, rule planner and proposer check;
  `navigate()` stops on an unsupported calculation.
- `computer/diagnostics.py`, plus a `status` intent in the router.
- `computer/state.py`: atomic, versioned, age-limited working state, written only when it changes.
- `Fact.id`: stable content-hash fact identifiers.
- A fix for values with different qualifiers being said under the wrong one.
- 52 new tests and two dialogue sets. No existing test, score or reply changed.

**Incomplete (from the brief, not started)**
- New secondary indexes (inverted, predicate, range, positional, semantic) and incremental index maintenance.
  Designs are in §4, waiting on measurement against the real corpus.
- Active knowledge navigation beyond the existing bounded backtracking; hypothesis testing; storing verified
  conclusions.
- "What changed?" queries across versions; rule-based causal hypotheses.
- Long-running investigations that checkpoint mid-plan. Only the dialogue frame is persisted; plans finish in
  milliseconds, so there's been nothing yet to checkpoint.
- Hailo or any other accelerator: nothing here is a neural workload worth moving.

**Next justified steps**
1. Run `python3 tools/bench_dialogue.py` and `python3 -m unittest discover -s tests` with the real `data/` on the
   Pi. Record ms per turn, shard reads per turn and peak RSS.
2. Write 50–100 calculation and estimate questions against the real corpus (by someone other than whoever writes
   the next patterns) and score them blind.
3. Check unit codes on the real build and extend `CORPUS_UNITS`.
4. Only then decide on the inverted term index and the delta segment, using the measurements.
