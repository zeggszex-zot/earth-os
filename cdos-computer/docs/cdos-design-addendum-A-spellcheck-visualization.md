# CDOS Computer: Design Addendum A — Spell Check and Spreadsheet Visualization

Status: draft for review. Milestone 2. Depends on Milestone 1 (capability interface and battery acceptance test, `cdos-design-capability-acceptance.md`). No code changes.

## 0. Scope and ordering

- Milestone 1 stays as written. Nothing here changes its acceptance criteria or the 215-test baseline.
- Spell check is independent of M1, but it is deferred until M1 is green, to keep M1 narrow.
- Visualization consumes the M1 result envelope, so it cannot start before M1's envelope exists.
- Order within M2: spell check → table model → text and CSV renderers → HTML page → charts.

## 1. What exists today (checked against the zip)

| Need | Present | Note |
|---|---|---|
| Spelling tolerance | Partly. `computer/navigator.py` `soft_resolve` and `edits1` accept one spelling slip, but only when exactly one known word is that close. It says "I think you mean …". | No general spell check. `computer/planner.py` `normalize()` only lowercases and strips filler. It does not correct spelling. |
| Corrections shown to the user | Partly. Soft matching already reports what it changed. | No general correction record. |
| Display or export | None. `computer/__main__.py` has text chat, `--plan`, `--check`. | No table, chart, or CSV code. |
| Output surface | Text in a terminal, speech, and LEDs. The Pi Zero 2 W has no screen in the hardware list. | This is the main decision for visualization (§5.1). |

## 2. Spell check — `language.spellcheck`

### 2.1 Declaration

| Field | Value |
|---|---|
| `name` / `version` | `language.spellcheck` / `1.0.0` |
| `inputs` | `text` (string); `protected` (set of strings, built from corpus keys, relation names, unit names, and numbers); `dictionary` (optional, path) |
| `output` | `corrected` (string), `original` (string, unchanged), `changes` (list of `{original, suggestion, start, end, reason, confidence}`), `unchanged_reasons` (list) |
| `deterministic` | true |
| `cost` | low memory with a bundled word list; the Hunspell backend is measured on the Pi before it is enabled |
| `writes_state` | false |
| `optional` | true |
| `fallback` | the existing navigator soft matching (`soft_resolve`), which is already present |
| `errors` | `DICTIONARY_UNAVAILABLE`, `INVALID_INPUT` |

### 2.2 Rules

1. **Never change protected tokens.** Corpus subjects (`BATTERY_ALPHA`, `Paris`), relation names, unit names (`KWH`, `Wh`), numbers, and quoted strings are never corrected. The protected set is built from the corpus at load time.
2. **Never guess across real words by default.** "meat" in "Does BATTERY_ALPHA meat 50 Wh?" is a real English word, so the default is to report nothing and leave it alone. A separate, reported suggestion is allowed, e.g. "Did you mean *meet*?", but the computer does not act on it.
3. **Corrections are separate from the input.** The envelope keeps `original`. The question the planner actually runs is `corrected`, and the answer names every change it made, e.g. "I read 'batery' as 'battery'."
4. **Only correct when the corrected form resolves.** A correction is applied only if the corrected text produces a known entity or relation. Otherwise it is reported as a suggestion, not applied.
5. **Offline.** The bundled word list ships with CDOS. A Hunspell or Aspell dictionary is optional, and its license is checked before packaging.
6. **Deterministic.** Same text and dictionary, same output.

### 2.3 Acceptance tests (fixture: the M1 battery corpus)

| # | Input | Expected `corrected` | Expected `changes` | Must not |
|---|---|---|---|---|
| S1 | "What is the batery capacity of BATTERY_ALPHA?" | "What is the battery capacity of BATTERY_ALPHA?" | one change, `batery` → `battery`, reason "unknown word, one edit from a known word" | change `BATTERY_ALPHA` |
| S2 | "Does BATTERY_ALPHA meat 50 Wh?" | unchanged | none; `unchanged_reasons` says "meat is a real word" | silently change `meat` |
| S3 | "Does BATTERY_ALPHA have 0.052 KWH?" | unchanged | none | change `KWH` or `0.052` |
| S4 | "Does BATTERY_ALPHA have 0.052 kwh?" (lowercase) | unchanged | none (case-insensitive unit match) | change the number |
| S5 | "batery capacity of xyzzy" | unchanged | none, because the correction would not resolve | apply a correction that resolves to nothing |
| S6 | S1 with the dictionary absent | same as S1 via the fallback | one change, reason from `soft_resolve` | error or crash |
| S7 | S1 run 5 times | identical | identical | any difference |

Assertion: the envelope's `original` always equals the input string, byte for byte.

## 3. Table model — `visualization.table` (the data layer)

The visualization layer never computes and never reads the corpus. It receives a `Table`, built from one or more M1 result envelopes.

```
Table
  id                  string, derived from the source envelopes' canonical_hash values
  title               string
  corpus_version      from the envelopes; all rows must agree, or the build fails
  columns[]           {name, type: text | number | verdict | certainty, unit, format}
  rows[]              {cells[]}
  cell                {value, display, certainty, evidence_ids[], envelope_hash}
```

Rules:
- A **number** cell has a stored numeric `value` and a unit. `display` is formatted only in the renderer ("52 Wh").
- A **verdict** cell holds TRUE, FALSE, UNKNOWN, or CONFLICT. A `UNKNOWN` cell renders as "Unknown", not as blank or 0.
- Every cell carries `evidence_ids` and `envelope_hash`. A cell with no evidence and no UNKNOWN status is an error (`BUILD_ERROR`), not a blank.
- A `Table` is immutable. Building it twice from the same envelopes gives byte-identical output.
- Sorting and filtering produce a new `Table` with the same provenance. They do not change any value.

### 3.1 The battery table (from the M1 fixture)

| Battery | Capacity | Meets 50 Wh? | Certainty (verdict cell) |
|---|---|---|---|
| Alpha | 52 Wh | Yes (TRUE) | ESTABLISHED |
| Beta | 42 Wh | No (FALSE) | INFERRED |
| Delta | Unknown | Unknown | UNKNOWN |

The capacity column for Beta is a derived value (3.5 Ah × 12 V). Its cell's evidence_ids point to both source facts and the formula step. The "Meets 50 Wh?" column comes from the M1 envelope's `truth`, not from the table code.

### 3.2 Table acceptance tests

| # | Test | Expected |
|---|---|---|
| T1 | Build from the three M1 envelopes | 3 rows; values as in §3.1 |
| T2 | Sort by capacity, ascending | Beta (42), Alpha (52), Delta last (Unknown always sorts last) |
| T3 | Filter "Meets = Yes" | Alpha only; Alpha's values unchanged |
| T4 | Build from envelopes with different `corpus_version` | `BUILD_ERROR` |
| T5 | Cell with a value and no evidence | `BUILD_ERROR` |
| T6 | Build twice | byte-identical serialised tables |

## 4. Renderers — `visualization.render.*`

Each renderer takes a `Table` and produces output. Renderers have no access to the corpus or the planner.

| Renderer | First release | Output |
|---|---|---|
| `text` | yes | aligned columns in the terminal, for `--table` and the chat |
| `csv` | yes | UTF-8 CSV of the values, with numbers unformatted (`52`, not `52 Wh`) and the unit in the header (`Capacity (Wh)`) |
| `csv` sidecar | yes | `<name>.provenance.json`: `corpus_version`, `envelope_hash` per row, evidence per cell. CSV has no reliable place for provenance, so it goes beside the file. |
| `html` | second | one static page, no external scripts or fonts, sortable columns, numeric formatting |
| `xlsx` | later | not in M2 |

### 4.1 CSV export rules
- Numbers unformatted; units in headers; `UNKNOWN` written as an empty cell plus a `certainty` column, so no spreadsheet reads it as zero.
- Certainty and truth are separate columns.
- Line endings and encoding fixed, so the file is byte-identical across runs.

### 4.2 Renderer acceptance tests

| # | Test | Expected |
|---|---|---|
| R1 | CSV of the battery table | header `Battery,Capacity (Wh),Meets 50 Wh,Certainty`; Delta's capacity empty, certainty UNKNOWN |
| R2 | Sidecar for R1 | contains `corpus_version` and one `envelope_hash` per row |
| R3 | Re-import the CSV values | values equal the table's stored values, to the stored precision |
| R4 | Text render | "Unknown" appears for Delta, never blank or 0 |
| R5 | Import boundary | the `visualization` package imports nothing from `computer.corpus`, `computer.navigator`, `computer.planner`, or `computer.calc`; a test enforces this |
| R6 | Renderer is pure | same `Table` gives identical output on repeated runs |

## 5. Charts

### 5.1 Output surface (decision needed)

The Pi Zero 2 W in the hardware list has no screen. Options:
- **A. Terminal and CSV only.** No charts. Simplest, and fits the hardware.
- **B. Local web page** served by the Pi on the home network (static files, no external dependencies). Charts drawn in plain SVG. Works on a phone or laptop.
- **C. NUC serves the page**, the Pi sends the table over the LAN. Keeps the Pi light.
- **D. Add a small display** to the hardware list. This is a new hardware decision.

Recommendation: A for M2's first release, and B for charts, since it needs no new hardware.

### 5.2 Chart types

| Chart | Use | Note |
|---|---|---|
| Bar | compare values across candidates | the default; supports a reference line at the requirement (50 Wh) |
| Line | a value over time (e.g. POPULATION by year, from dated corpus facts) | needs `start` dates; undated facts are not plotted |
| Pie | shares of a whole | **not recommended** for this use: the battery comparison is against a threshold, and pie slices hide thresholds and tiny differences. Include only if asked, with a warning in the output. |

Charts read the same `Table` as the renderers, and each bar or point links to its evidence_ids.

### 5.3 Chart acceptance tests

| # | Test | Expected |
|---|---|---|
| C1 | Bar chart data from the battery table | three bars: 52, 42, and no bar for Delta (shown as "Unknown" label, not a zero-height bar) |
| C2 | Reference line | at 50, labelled with the requirement |
| C3 | Chart values equal table values | sum/values match exactly |
| C4 | Line chart from dated facts (France POPULATION, M1-style fixture) | two points, with years; undated fact excluded and listed as excluded |
| C5 | SVG output | valid SVG, no external references, identical on repeated runs |

## 6. Explaining a cell

`explain(table, row, column)` returns the M1 envelope fields for that cell: value, truth, certainty, evidence (with corpus_version), steps, rules, assumptions. The explanation is built from the envelope, not generated freely, so it can't say anything the evidence doesn't support.

| # | Test | Expected |
|---|---|---|
| E1 | explain Beta's capacity | 3.5 Ah and 12 V facts, the formula step, INFERRED, the corpus_version |
| E2 | explain Delta's capacity | UNKNOWN; "nothing links BATTERY_DELTA to a capacity" |
| E3 | explain a cell whose envelope is missing | error, not a generated guess |

## 7. Dependencies and risks

- Spell check depends on the corpus protected-term list. A bad protected list either changes technical names (bad) or blocks valid corrections (safe). Test S1 and S3 guard it.
- Visualization depends on M1's envelope. If the envelope changes, the table model changes too; both are versioned.
- Pie charts and unsourced numbers in a visual are the biggest risk of misleading users. The import-boundary test (R5) and the cell-evidence rule (§3) exist for this reason.
- Hunspell/Aspell licensing must be checked before any dictionary is shipped (see the previous design's decision on dictionary licensing).

## 8. Decisions needed

1. **Output surface** (§5.1): A now and B for charts, or C, or D?
2. **Pie charts**: exclude from the first release, or keep as an option?
3. **Spell-check word list**: bundled, or Hunspell from the start (after the licence check)?
4. **Spell-check behaviour for real-word errors** (§2.2 rule 2): report only, as proposed, or act on them when the corrected sentence resolves and the original does not?
