# Milestone 1: capability registry, result envelope, requirement checking

Implements `docs/cdos-design-capability-acceptance.md` on top of the upgraded repository (`docs/STARSHIP_UPGRADE.md`),
with the review decisions applied. Nothing in the earlier code was rebuilt: units come from `computer/quantity.py`,
formulas from `computer/analysis.py`, and arithmetic over facts from the navigator's plan language. Spell check and
visualization stay in Milestone 2.

Every figure below comes from this container (x86, the test corpus). Nothing has been measured on a Pi or on
the real corpus.

## Result

| | before M1 | after M1 |
|---|---|---|
| unit tests (`python3 -m unittest discover -s tests`) | 267 run: 264 pass, 3 skipped | **323 run: 320 pass, 3 skipped** (the same 3) |
| battery cases 1–6 through `Computer.think` | all 6 "I don't know that one yet" | **all 6 right on every property** |
| battery cases 7–10 | not applicable | **pass** |
| replies changed on 425 existing questions | — | **0** (against both the pre-M1 tree and the original zip) |
| existing benchmark rows, 2 dialogue sets | — | identical scores |
| deliberate engine breaks caught by the new tests | — | **12 of 12** (§6) |
| start-up cost of checking declarations | — | ~100 ms, +0.8 MB peak (§7) |
| time per turn | 0.50–0.58 ms | 0.58–0.68 ms (within run-to-run noise) |

## 1. Capability registry (`computer/capabilities.py`)

20 capabilities are declared, each with the draft's fields: name, version, inputs, output, deterministic, cost,
writes_state, requires, optional, fallback and errors. Each also lists the modules its determinism is checked
in. `Computer()` builds the registry and validates it, and **a declaration that fails stops the computer from starting**,
with the capability named (tested).

Validation refuses:
- missing or mistyped fields, a version that isn't semver, a name that isn't dotted lower case, unknown fields,
  and an error code outside the shared vocabulary;
- `writes_state: true` unless the capability is on `WRITERS`. Only `state.working` is: the analysis engine
  returns its state, and the computer writes it through `state.working`, which falls back to `state.memory` when
  `state_file` isn't set;
- `optional: true` with fallback `none`, a fallback that isn't declared, and fallback cycles;
- `deterministic: true` when a declared module calls `time.time / localtime / gmtime / strftime / ctime`,
  `datetime.now / today`, `os.urandom`, imports `random / secrets / socket / urllib / http / ssl / asyncio`, or
  names `/dev/hwrng`. The check reads Python's token stream, so docstrings and comments don't count.
  `time.monotonic` is allowed: it only times things, and timings are kept out of every hash;
- `deterministic: true` with a non-deterministic requirement, unless that requirement is only the default
  for a declared input (below).

**The clock, which was the blocker.** The navigator, the lookup, the analysis engine and the lexicon used to
read the year from the clock themselves. Now `computer/clock.py` is the only place on the answer path that reads it,
and it's declared as `system.clock` (non-deterministic). Each capability that can default to the current year
declares a `year` input with `default_from: "system.clock"`. The year actually used is recorded in
`Result.year` and the envelope, and the hash covers it. So "same inputs, same output" holds, with the year
counted as an input. The validator caught three of my own declarations that left the year input out, before
any test ran.

At run time, `choose()` follows fallbacks and records each one skipped (`logic.units_gnu → logic.units`),
`need()` raises `Unavailable` for a required capability that's off, and `error()` refuses a code the capability
didn't declare. `logic.units_gnu` is declared but not implemented. It's always unavailable, with the reason
"not implemented in this release", and it isn't a stub that pretends to work.

## 2. Result envelope (`computer/envelope.py`, `Reply.envelope`)

Every reply carries one, built from whatever produced it: the navigator, the lookup, the analysis engine, the
calculator, the clock, diagnostics, small talk or instinct. The shape is the draft's §4.5, plus `kind`, `year`,
`value` and `inferences`.

| field | how it's filled |
|---|---|
| `status` | answered / unsupported / conflict / **invalid** (an error asking again won't fix: wrong unit or dimension, bad input) / **unavailable** (a required capability is off) |
| `truth` | TRUE / FALSE / UNKNOWN / CONFLICT; `null` for invalid answers and for "which of" questions, whose answer is in `value` |
| `certainty` | ESTABLISHED / INFERRED / UNKNOWN / CONFLICT, the weakest of the steps (below) |
| `value` | the requirement's measured figure in base units, the selection with each candidate's verdict, a CALC result, or an estimate's result and unit |
| `evidence` | each fact with its id, source, confidence, `derived` (its IS_A path or formula) and `corpus_version` |
| `steps`, `rules` | the plan trace, conversions (`0.052 KWH = 52 WH (unit.kwh_to_wh)`), comparisons (`42 WH >= 50 WH: FALSE`); conversion rule ids, functional and knowledge rule ids |
| `assumptions` | how a name was read ("I took Alpha to mean Battery Alpha."), which figure a requirement was held to, conditional knowledge rules, inputs the user stated, an estimate's assumptions, a what-if |
| `errors` | structured, with `capability@version`, `recoverable` and `details` |
| `capabilities`, `skipped` | what this turn used, with versions; what it skipped, with the reason |
| `corpus_version`, `canonical_hash` | §3 below |

**Certainty.** ESTABLISHED means the answer rests only on facts stored as written, plus exact conversions,
which are listed in `rules` and don't lower the label (D1). It becomes INFERRED when any step derives
something: an IS_A chain, a knowledge rule, a lexicon mapping, a formula over different relations (charge ×
voltage, population ÷ area), several figures that all agree, or an estimate. CALC over one relation (a sum of
populations) stays ESTABLISHED: exact arithmetic on stored facts. `Result.inferences` lists the reasons.

**Canonical hash.** sha256 over sorted keys, with numbers as normalised decimals (52, 52.0 and 5.2e1 hash the
same; -0.0 is 0). It excludes `canonical_hash`, `ms`, `compute` and `trace` at any depth. It includes no file
paths: the same corpus copied to another folder gives the same hash (tested).

## 3. Corpus version and integrity (`Corpus.version`, `Corpus.integrity`, `content_hash`)

The version is sha256 over the corpus content: `subjects.bin` and every `s/` and `v/` shard, decompressed and
in path order, plus `meta.json` without its hash, version and `built_from` fields.

**Checked at load (fix from review).** The first version used the hash stored in `meta.json` whenever there was
one, and recomputed only when there wasn't. So a shard edited after the build, with `meta.json` left alone,
was answered from while citing the old hash: provenance for contents that weren't the ones read. I reproduced
this before fixing it. Now `Corpus.load()` recomputes the hash and compares, and `corpus_version` is always the
hash of what was actually read. The outcome goes in every envelope as `corpus_integrity`:

| `corpus_integrity.status` | meaning | in the answer |
|---|---|---|
| verified | the stored hash matched the shards at load | — |
| computed | no stored hash; the version was computed from the shards | — |
| trusted | `corpus_integrity = "trust"`: the stored hash was used without reading the shards | never called verified |
| mismatch | the shards (or `meta.json`) don't match the stored hash | a CORPUS_INTEGRITY error, with the stored and actual hashes |
| changed | verified at load, but a shard read later has a different size or mtime | a CORPUS_INTEGRITY error, listing the files |

`corpus_integrity` in `config.toml` sets the mode. `verify` (the default) answers and flags it. `strict` refuses
to answer from a corpus that fails, with status `unavailable`; questions that don't need the corpus, like
sums, are still answered. `trust` is the explicit fast path for a build you know is intact. Nothing writes a
hash, or anything else, into `data/` (tested in all three modes).

**The cost of checking:** every start now reads and hashes every shard whenever a hash is stored (before, only
when none was). That's 0.35 s for 89 MB of rows here, and an estimated few seconds on a Zero 2 W. `trust`
avoids it and says so in every answer. A cheaper verified start (for example a signed stamp of file sizes
and mtimes in the computer's own state folder) isn't built.

- **Rebuilding the same facts** gives the same version, even though gzip's bytes differ.
- **Changing one fact's value** gives a new version, even though `meta.json` is byte-identical. A metadata
  hash, as the draft first proposed, would have missed this (tested).
- `tools/build_shards.py` now stores `content_sha256` in `meta.json`. `Corpus.verify_version()` and `--check`
  report a stored hash that no longer matches the shards.
- A corpus built before this change has no stored hash, so the version is worked out once, on first use.
  That's 0.35 s for 89 MB of rows here; a Zero 2 W is likely 10–20 times slower (estimated, not measured).
  Rebuilding with `build_shards.py` removes the cost. `data/` is never written.

## 4. Requirement checking (`MEASURE`, `REQUIRE`, `SELECT`)

New plan steps, validated and in the GBNF grammar:

```
$1 = FIND battery beta
$2 = MEASURE $1 energy                    a fact in a unit of energy (converted exactly), or charge x voltage
$3 = REQUIRE $2 AT_LEAST 50 Wh            the unit's dimension must match MEASURE's, or the plan doesn't validate
ANSWER $3
```

`MEASURE` uses `analysis.measure()`, which is the same unit table and the same `energy = charge × voltage`
rule the runtime estimates use, so there's one engine. It never drops evidence. It returns one of five outcomes:

| outcome | when | `REQUIRE` gives |
|---|---|---|
| ok | one applicable value, after exact conversion (0.05 kWh and 50 Wh are one value) | TRUE / FALSE |
| several | different values, and no functional rule covers them (e.g. two relations: rated and usable) | the verdict if every value gives the same one, else UNKNOWN |
| conflict | a functional rule says one value at a time, and two different values both apply | CONFLICT, citing the rule |
| incompatible | a fact named for the dimension, in a unit of another dimension (ENERGY_CAPACITY in kg) | UNKNOWN with UNIT_INCOMPATIBLE; status invalid |
| missing | no figure and no way to compose one | UNKNOWN with MISSING_FACT, saying what was absent |

**The functional rule** is a new `functional` list in `computer/data/rules.json`, validated at load like the other
knowledge rules, with ENERGY_CAPACITY, BATTERY_CAPACITY, RATED_CHARGE and NOMINAL_VOLTAGE. Two values
conflict only if **both hold in the year asked about** (`Fact.holds`) and still differ after exact conversion.
A value whose period ended, or hasn't begun, doesn't apply. Its behaviour is tested on its own:

- a replaced value (40 Wh until 2020, 60 Wh from 2020): no conflict; TRUE in 2025, FALSE in 2015;
- one value in two units: no conflict;
- two relations (rated 52 Wh, usable 48 Wh): several, not a conflict. "Meets 50 Wh" is UNKNOWN; "meets 40 Wh" is TRUE;
- two dated values that are both still open (from 2018 and from 2021): a conflict. Here the requirement check is
  stricter than the plain lookup, which by its older convention says the newer figure;
- without the rule, Epsilon's 40 and 60 Wh are only "several": UNKNOWN at 50 Wh, TRUE at 30 Wh. With the rule,
  it's CONFLICT at both.

Rule-planner patterns cover "does X meet / satisfy / reach N unit", "does X have at least / at most N unit",
"is X at least N unit", and "which of A, B (and C) meets N unit". A short name ("Alpha") is read as the one
subject whose name ends with it, and the answer says so. If none matches, or more than one, it isn't guessed.
When a requirement plan can't be done, `navigate()` stops there, the same as for CALC, so no other reading can
give a part-answer. The analysis engine never treats a requirement question as a what-if follow-up.

## 5. Battery acceptance test (`tests/test_battery_acceptance.py`, fixture `tests/battery_corpus.py`)

The configuration is the draft's §7.2, with the model endpoints on a closed port. Sockets are patched to raise,
so **any** connection attempt fails the test.

| # | question | truth | certainty | status | also checked |
|---|---|---|---|---|---|
| 1 | Does BATTERY_ALPHA meet 50 Wh? | TRUE | ESTABLISHED | answered | `rules == ["unit.kwh_to_wh"]`; the conversion step; evidence underived |
| 2 | Does BATTERY_BETA meet 50 Wh? | FALSE | INFERRED | answered | the `3.5 AH x 12 V = 42 WH` step; both facts marked as formula inputs; `logic.formula` listed |
| 3 | Which of ALPHA and BETA meets 50 Wh? | null | INFERRED | answered | value `{selected: [ALPHA], verdicts: {ALPHA: TRUE, BETA: FALSE}}`; the name reading in assumptions |
| 4 | Does BATTERY_DELTA meet 50 Wh? | UNKNOWN | UNKNOWN | unsupported | **the reason:** the plan reached `MEASURE`, the error is MISSING_FACT for BATTERY_DELTA / energy, saying there's no charge and voltage, and there's no evidence |
| 5 | Does BATTERY_GAMMA meet 50 Wh? | null | UNKNOWN | invalid | UNIT_INCOMPATIBLE, units `["KG"]`, expected energy, not recoverable; no number said but the requirement's |
| 6 | Does BATTERY_EPSILON meet 50 Wh? | CONFLICT | CONFLICT | conflict | `rules == ["functional.energy_capacity"]`; CONFLICT error with both values |
| 7 | case 1 without GNU Units | TRUE | ESTABLISHED | answered | same words and assumptions; `skipped` lists it, with the reason |
| 8 | case 1 without `logic.units` | null | UNKNOWN | unavailable | UNAVAILABLE, recoverable, names `logic.units`; no figure said; no crash |
| 9 | case 3, five times | — | — | — | one `canonical_hash`; the same from a copy elsewhere; a different one when a fact changes |
| 10 | every case | — | — | — | no socket opened |

For every case 1–6, these are checked separately: truth, certainty and status; the words that must and
mustn't be said; that every number said appears in the evidence, the steps, the question or the year; that
`corpus_version` and each evidence record equal the fixture's content hash; and that each capability is listed
at its declared version. The report puts a failure under "engine defect" or "knowledge gap" (§7.5). Another test
checks that the dialogue path and a direct `navigate()` call agree.

## 6. Do the tests catch a broken engine?

Each of these was applied to a copy of the tree, and the new tests were run against it:

| break | caught by |
|---|---|
| no functional rule in rules.json | case 6 |
| exact conversion not recorded | case 1 (rules) |
| every requirement passes | case 2 |
| the formula doesn't count as an inference | case 2 (certainty) |
| a wrong-unit fact is ignored | case 5 |
| missing figure treated as "no" | case 4 |
| timings included in the hash | envelope tests |
| the navigator reads the clock directly again | registry validation (the computer won't start) |

| `corpus_version` trusts the stored hash again (the review's bug) | `test_a_shard_edited_without_updating_meta_is_caught` |
| the integrity error isn't added to envelopes | integrity tests |
| `strict` answers anyway | `test_strict_refuses_a_failed_corpus` |
| shards changed after the check go unnoticed | `test_a_shard_changed_after_the_check_is_flagged` |

All 12 were caught. The timings break was missed at first, because envelopes didn't contain timings, so the
exclusion was never exercised. A unit test now checks it directly.

## 7. Cost

| | before | after |
|---|---|---|
| `Computer()` start-up | ~1 ms | ~100 ms (checking 20 declarations, reading ~15 modules' tokens; cached by file and mtime within a process) |
| peak memory at start-up | 23.6–25.1 MB | 24.5 MB |
| per turn, one computer, 220 questions | 0.50–0.58 ms | 0.58–0.68 ms |

The first version of the source check parsed syntax trees. `navigator.py`'s tree alone takes about 9 MB to
build, which pushed peak memory to 33 MB. Reading tokens instead gives the same verdicts (the same tests and
the same mutations) for +0.8 MB.

## 8. Decisions and deviations

- **Case 1's assumptions aren't empty (deviates from draft §7.4 assertion 6).** "50 Wh is compared with
  Battery Alpha's energy capacity" is recorded for Alpha, just as "…worked out from its nominal voltage and rated
  charge" is for Beta. Which stored figure a stated requirement is held to (rated, usable, nominal) is a
  reading in both cases. The Ah × V step is a derivation, which lowers Beta's label, not an assumption.
  `test_assumptions_name_the_measure_for_both_figures` records this choice, so it's easy to reverse if you
  decide otherwise.
- **Case 3's truth is null.** "Which of" has no truth value; the selection and each verdict are in `value`.
- **Case 5's status is invalid,** and its truth is null.
- **D1 and D2 as agreed:** exact conversions are ESTABLISHED and recorded in `rules`; a missing figure is
  UNKNOWN; a figure below the requirement is FALSE; conflicting figures are CONFLICT, but only under a stated
  functional rule.
- **The unit table stays in `computer/quantity.py`** (Python). It wasn't moved to JSON, so there's still one table
  and no loader to keep in step.
- **Version numbers:** existing capabilities start at 1.x; `navigator.execute` is 1.2.0, because CALC,
  MEASURE, REQUIRE and SELECT were added.

## 9. Limits

- **The determinism check reads each declared module, not everything it imports.** It's backed by a test that
  fails on any import from those modules that isn't itself checked, `computer.clock` through a declared year, or
  listed in `REVIEWED_IMPORTS` with the names used and why (fix from review). Three are listed today:
  `navdata.shape` (a pure function, from a module whose `random` is for training data), the registry itself, and
  one constant from `wisdom` used only by `python3 -m computer.rules`. A new dependency fails the test until it's
  reviewed. The runtime checks (case 9, the hash tests) remain the backstop.
- **Declarations are checked inside, too (fix from review):** `requires` entries must be strings of the form
  `capability:` / `file:` / `binary:` / `network:`, `output` needs a string `type`, input types and
  `default_from` are checked, error codes must be distinct, and a device path counts only when it's opened
  (`open`, `os.open`, `Path`), not merely named.
- **The check finds clock, random and network calls; it doesn't find sensor reads.** The diagnostics module
  reads `/proc` and is declared non-deterministic by hand.
- **Short names** ("Alpha") work only when exactly one subject ends with that name. On the real corpus that's
  rarer, and the question gets "I couldn't find…" instead. The check scans every subject key once, and only
  in that fallback.
- **Units:** only the codes in `CORPUS_UNITS` are recognised. Wikidata unit ids (`WD_Q…`) aren't. On the real
  build, a capacity stored that way is "missing", not "incompatible".
- **`memory_mb` in the declarations is a rough estimate,** not a measurement on a Pi.
- **The real corpus has now been reviewed** (`docs/REAL_CORPUS_REVIEW.md`); a Pi still hasn't been tested.
  Originally: **none of this had run on the real corpus or a Pi.** The first thing to run there:
  `python3 -m unittest discover -s tests`, then `python3 -m computer --check` (it reports the corpus version and
  whether it's stored), then `python3 tools/build_shards.py … --out data` to store the hash.

## 10. Next: Milestone 2

Unchanged from the addendum, and now unblocked: the table model can be built from these envelopes
(`value`, `truth`, `certainty`, `evidence`, `canonical_hash`), with spell-check reporting, the terminal and CSV
renderers, and later the static HTML page. Two points from the review still need your decision: S6's fallback
(`soft_resolve` only corrects names) and the definition of "resolves" in rule 4.
