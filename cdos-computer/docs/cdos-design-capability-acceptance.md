# CDOS Computer: Capability Interface and Battery Acceptance Test

Status: draft for review. No code changes yet. Existing baseline: 215 tests, all passing (3 skipped).

## 1. Purpose

Define (a) how CDOS tools declare themselves, (b) the provenance every answer carries, and (c) one fixture-based acceptance test that shows the tools cooperating on a multi-step task. The test must separate engine defects from missing corpus knowledge.

## 2. Current state (checked against the zip)

| Needed for the milestone | Present now | Gap |
|---|---|---|
| Capability declarations | none; modules are wired directly in `computer/core.py` | registry and validation |
| Evidence records | `Fact` (subject, relation, kind, value, unit, start, until, source, conf, qual); `Result.evidence`, `Result.steps`, `Result.rules`, `Result.inherited` (IS_A path per fact) | no `derived` field on `Fact`; no certainty label; no assumptions list |
| Corpus version | `data/meta.json` holds `facts`, `subjects`, `s_shards`, `v_shards`, `relation_names` | no version or checksum field |
| Truth values | `computer/logic.py`: TRUE, FALSE, UNKNOWN, CONFLICT | certainty labels are a separate axis (see §4) |
| Unit conversion | none. `computer/calc.py` is plain arithmetic. | must be added |
| Requirement check (`>=` a constant) | none. `COMPARE` only does MORE/LESS between two GET results, and refuses different units (`navigator.py` line ~648). | new primitive |
| Formula with dimensions | none | new (on top of the calculator) |
| Deterministic runs | navigator and rule planner are deterministic; the I Ching is nondeterministic by design (chance) | declare it |
| Fixture writer | `tests/mini_corpus.py` `build(path, facts, ...)` writes the shard format | reuse with a new fact list |
| No-LLM operation | `llm_mode = "off"`, `navigator_planner = "rules"`, controller and lexicon off by default | already true; the test should assert it |

## 3. Capability interface

### 3.1 Declaration (required fields)

Each capability is registered with:

| Field | Type | Rule |
|---|---|---|
| `name` | string | unique, dotted, e.g. `corpus.lookup`, `logic.units` |
| `version` | semver | bumped on any change to output |
| `inputs` | schema | named, typed, with required/optional flags |
| `output` | schema | typed result envelope (§4), with error codes listed |
| `deterministic` | bool | `true` only if same inputs and corpus version give byte-identical output |
| `cost` | `{memory_mb, cpu}` | `cpu` is `low` / `medium` / `high`; measured on the Pi where possible |
| `writes_state` | bool | `false` for every capability in the acceptance test |
| `requires` | list | hardware, files, or other capabilities |
| `optional` | bool | if true, `fallback` is required |
| `fallback` | name or `none` | what runs instead when unavailable |
| `errors` | list of codes | every code the capability can return |

### 3.2 Registry validation (at startup)

The registry refuses to start, with a named message, if:
- a required field is missing or has the wrong type;
- `deterministic: true` and the code reads the clock, `random`, hardware RNG, or the network;
- `writes_state: true` without being on the explicit writer allowlist;
- `optional: true` with `fallback: none`;
- a fallback names an unknown capability, or a cycle of fallbacks;
- a returned error code is not in `errors`.

The existing `computer/rules.py` already refuses unknown actions at startup. Use the same pattern.

### 3.3 Availability and skipping

- The planner asks the registry for availability before calling a capability.
- If an **optional** capability is unavailable, the planner uses its `fallback` and records `skipped: <name> (<reason>)` in the trace. The answer's assumptions list gets an entry only when the fallback changes the answer's basis.
- If a **required** capability is unavailable, the step returns `UNAVAILABLE` with `recoverable: true`. The answer is UNKNOWN with that error. The computer never crashes and never guesses.

### 3.4 Structured errors

```
{"code": "MISSING_FACT" | "UNIT_INCOMPATIBLE" | "DIMENSION_ERROR" | "CONFLICT"
         | "UNKNOWN_ENTITY" | "UNAVAILABLE" | "INVALID_INPUT" | "BOUND_EXCEEDED",
 "capability": "<name>@<version>",
 "message": "<short sentence the computer can say>",
 "recoverable": true | false,
 "details": { ... }}
```

Errors are data. A capability never raises past the planner.

### 3.5 Initial inventory (proposed declarations)

| Capability | Wraps | Deterministic | Writes state | Optional | Fallback |
|---|---|---|---|---|---|
| `corpus.lookup` | `corpus.py` | yes | no | no | none |
| `navigator.execute` | `navigator.py` | yes | no | no | none |
| `planner.rules` | `planner.py` (rules) | yes | no | no | none |
| `logic.calc` | `calc.py` | yes | no | no | none |
| `logic.units` (new) | built-in unit table | yes | no | no | none (required for battery test) |
| `logic.units_gnu` (future) | GNU Units | yes | no | yes | `logic.units` |
| `logic.formula` (new) | restricted parser + units | yes | no | no | none |
| `knowledge.lexicon` | `lexicon.py` (runtime read) | yes | no | yes | no lexicon |
| `planner.controller` | `controller.py` | yes (fixed weights) | no | yes | rules only |
| `planner.lm` / `proposer` | llama-server | no | no | yes | rules |
| `instinct.iching` | `iching.py` | **no** (chance, by design) | no | no | none; excluded from acceptance tests |
| `voice.*` | Vosk, Piper, eSpeak | no (audio input) | no | yes | print |

## 4. Provenance and result envelope

### 4.1 Reuse the existing evidence model

Keep the existing `Fact` fields unchanged: `subject`, `relation`, `kind`, `value`, `unit`, `start`, `until`, `source`, `conf`, `qual`. Keep `steps`, `rules`, and `inherited` as they are. Add the following to each evidence record, without changing the shard format:

- `derived`: `[]` for a fact stored as written; otherwise the path it was reached by (IS_A chain, or the formula that produced it).
- `corpus_version`: the version of the corpus it came from.

### 4.2 Certainty labels (support axis)

These describe **how the evidence supports the answer**. They are separate from the truth value.

| Label | Meaning |
|---|---|
| ESTABLISHED | The answer rests only on non-derived corpus facts, plus exact definitional conversions listed in `rules`. |
| INFERRED | At least one step is a derivation: an IS_A or knowledge-rule path, or a formula combining different relations. |
| UNKNOWN | The evidence does not establish an answer. |
| CONFLICT | The evidence supports incompatible claims. |

**Combining labels:** a result takes the weakest label of its supporting steps, in this order: CONFLICT > UNKNOWN > INFERRED > ESTABLISHED.

### 4.3 Truth value (answer axis)

The existing values, unchanged: TRUE, FALSE, UNKNOWN, CONFLICT. A requirement check can return FALSE ("Beta does not meet 50 Wh"). That is a truth value, not a certainty label. The envelope carries both.

### 4.4 Assumptions (separate list)

`assumptions` lists conditions the answer depends on but the evidence does not establish. Examples:
- "usable energy equals the rated value" (which figure counts as the requirement's measure);
- a conditional knowledge rule (e.g. `disjoint.animal_classes`);
- a user-supplied input, quoted exactly.

An empty list means no conditions beyond the cited evidence.

### 4.5 Result envelope

```json
{
  "status": "answered | unsupported | conflict | invalid | unavailable",
  "truth": "TRUE | FALSE | UNKNOWN | CONFLICT | null",
  "value": null,
  "certainty": "ESTABLISHED | INFERRED | UNKNOWN | CONFLICT",
  "text": "concise answer",
  "evidence": [{"subject": "...", "relation": "...", "value": "...", "unit": "...",
                "source": "...", "conf": 1.0, "derived": [], "corpus_version": "..."}],
  "steps": ["..."],
  "rules": ["unit.kwh_to_wh"],
  "assumptions": ["..."],
  "errors": [],
  "capabilities": [{"name": "...", "version": "..."}],
  "skipped": [{"name": "...", "reason": "..."}],
  "corpus_version": "sha256:…",
  "canonical_hash": "sha256:…"
}
```

`canonical_hash` covers everything except timings (`Result.ms`) and any field that names a file path. Two runs that should agree must produce equal hashes.

## 5. Corpus version

- Read `data/meta.json` at load time and compute `sha256` over its bytes. Every result records it.
- Add an optional `version` field, written by `tools/build_shards.py`. When present, it takes precedence over the hash for display. The loader must still work without it (the existing shards have no `version`).
- The corpus is read-only in dialogue. Nothing in the answer path writes to `data/` or `corpus.pf`.

## 6. Decision needing review

**D1: are exact unit conversions ESTABLISHED or INFERRED?**
Proposal: exact definitional conversions (kWh → Wh, ×1000) are listed in `rules` and do **not** lower the label. A formula that combines different relations (Ah × V → Wh) **does** lower it to INFERRED. Without this, almost every unit-based answer would be INFERRED and the label would carry no information.

**D2: is "does not meet" a FALSE, or a CONFLICT-free UNKNOWN?**
Proposal: FALSE, when the requirement is a stated number and the evidence gives a number. UNKNOWN when the number is missing.

## 7. Acceptance test: battery selection

### 7.1 Fixture

Built with a new builder in `tests/battery_corpus.py`, reusing `mini_corpus.build()`. All names are fictional. Facts are written in the existing 10-column format.

| Subject | Relation | Value | Unit | Notes |
|---|---|---|---|---|
| BATTERY_ALPHA | IS_A | BATTERY | | |
| BATTERY_ALPHA | ENERGY_CAPACITY | 0.052 | KWH | 52 Wh |
| BATTERY_BETA | IS_A | BATTERY | | |
| BATTERY_BETA | RATED_CHARGE | 3.5 | AH | |
| BATTERY_BETA | NOMINAL_VOLTAGE | 12 | V | 3.5 Ah × 12 V = 42 Wh |
| BATTERY_DELTA | IS_A | BATTERY | | no capacity or charge fact (missing) |
| BATTERY_GAMMA | IS_A | BATTERY | | |
| BATTERY_GAMMA | ENERGY_CAPACITY | 2.0 | KG | incompatible unit |
| BATTERY_EPSILON | IS_A | BATTERY | | |
| BATTERY_EPSILON | ENERGY_CAPACITY | 40 | WH | |
| BATTERY_EPSILON | ENERGY_CAPACITY | 60 | WH | conflicting, no dates |
| KWH, WH, AH, V, KG | | | | unit names registered in the built-in table |

**Requirement (stated by the question):** at least 50 Wh.

### 7.2 Inputs

Question, run through the public path with this configuration:

```
llm_mode = "off"
navigator = "always"
navigator_planner = "rules"
navigator_controller = ""
relation_lexicon = ""
corpus = <fixture data dir>
```

The test also points the LLM endpoints at a closed port, so any network call fails the test.

### 7.3 Cases

Expected values are what the design requires. Predicted baseline status on the current code is marked, to be confirmed by running.

| # | Question | Expected truth | Expected certainty | Must say | Must not | Predicted baseline |
|---|---|---|---|---|---|---|
| 1 | "Does BATTERY_ALPHA meet 50 Wh?" | TRUE | ESTABLISHED (D1) | 52 Wh; the kWh→Wh conversion | any answer that ignores the unit | fails: no conversion, no threshold |
| 2 | "Does BATTERY_BETA meet 50 Wh?" | FALSE | INFERRED | 42 Wh, from 3.5 Ah and 12 V | a value without its derivation | fails: no formula |
| 3 | "Which of ALPHA and BETA meets 50 Wh?" | ALPHA | INFERRED (weakest: BETA's derivation) | both verdicts, with evidence | picking BETA | fails |
| 4 | "Does BATTERY_DELTA meet 50 Wh?" | UNKNOWN | UNKNOWN | nothing links it to capacity | FALSE or TRUE | likely passes the answer; fails the label |
| 5 | "Does BATTERY_GAMMA meet 50 Wh?" | none (error) | n/a | DIMENSION or UNIT_INCOMPATIBLE, with the units | any number | fails: no dimension check |
| 6 | "Does BATTERY_EPSILON meet 50 Wh?" | CONFLICT | CONFLICT | both values, 40 and 60 Wh | picking one | likely fails: no conflict on capacity |
| 7 | Case 1 with `logic.units_gnu` absent | TRUE | ESTABLISHED | same answer; `skipped` lists GNU Units | a different answer | not yet applicable |
| 8 | Case 1 with `logic.units` absent (required) | UNKNOWN | UNKNOWN | `UNAVAILABLE` error | a guessed number, or a crash | not yet applicable |
| 9 | Case 3 run 5 times | identical | identical | — | any differing `canonical_hash` | must hold |
| 10 | Any case with the network blocked | same as case | same | — | any connection attempt | must hold |

### 7.4 Assertions

For each case the test checks:
1. `truth`, `certainty`, and `status` equal the table.
2. `text` contains each "must say" item and none of the "must not" items.
3. Every number in `text` appears in `evidence` or `steps`.
4. `evidence` cites `corpus_version` equal to the sha256 of the fixture's `meta.json`.
5. `capabilities` lists every capability used, with versions.
6. `assumptions` is non-empty for case 2 (which figure is the requirement's measure) and empty for case 1, except for D1 conversions, which appear in `rules`.
7. `canonical_hash` is equal across runs (case 9).
8. No socket is opened (case 10).

### 7.5 Engine defect or knowledge gap?

The fixture is complete by construction, so for cases 1–3 and 5–6 any wrong or missing answer is an **engine defect**. Case 4 is the designed **knowledge gap**: the correct output is UNKNOWN. Answering FALSE or TRUE there is a defect too, because it asserts something the corpus does not say. The test reports the two kinds separately, so a failing run says which one it is.

### 7.6 Unit table in the test

The test uses the built-in table only. The table is a JSON file in `computer/data/`, with exact factors:
- Energy: Wh = 1; kWh = 1000 Wh.
- Charge: Ah = 1 (charge, not energy).
- Power and voltage are present only so the formula check can reject them where they don't apply (e.g. Ah × V is energy; Ah alone is not).
- Mass: kg, g. Used only for case 5.

## 8. Scope limits (not in this milestone)

- No SQLite, Hunspell/Aspell, new graph libraries, SymPy, Graphviz, or speech changes.
- No changes to the existing 215 tests' expectations.
- The Jupiter-versus-Saturn scenario is a later integration test against real corpus facts, and only once its facts and units are confirmed present in `data/`.

## 9. Implementation order (after review)

1. Registry and declaration validation, with the existing modules declared. No behaviour change. The 215 tests must still pass.
2. Result envelope and provenance fields, filled in from the existing `Result`. Labels computed but not yet shown.
3. Built-in unit table and `logic.units`, with the exact-conversion rule (D1).
4. Requirement comparison primitive (`AT_LEAST` against a stated value).
5. `logic.formula` for Ah × V (dimension-checked).
6. Battery fixture and test, run in CI; expected to go from red to green across steps 3–6.
7. Optional-capability skipping (cases 7–8).

## 10. Open questions for review

1. Confirm D1 (exact conversions do not lower the label) and D2 (FALSE vs UNKNOWN for a missing requirement figure).
2. Is "usable energy equals rated value" the right assumption to record for case 2, or should the requirement name its measure?
3. Should the registry live in `computer/capabilities.py` (new) or be folded into `computer/rules.py`?
4. Is the 4.5 envelope the right public shape, or should the computer's own `Reply` carry it as well?
