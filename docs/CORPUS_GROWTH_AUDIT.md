# Corpus Growth: architecture audit and phased plan

*Phase 1 of the autonomous knowledge-acquisition project. Written before any
code was changed. Every number below was measured on the uploaded
`corpus.pf` and the `earth-os-routing-p4` source tree (HEAD `1a99a5b`) on
2026-11-14. Commands to reproduce them are in Appendix A.*

**Nothing was modified.** `corpus.pf` still hashes to
`f17a638f…a792f425`, the same value recorded in
`cdos-computer/docs/REAL_CORPUS_REVIEW.md` and in the `v1.0.0` tag of
`zeggszex-zot/earth-os-corpus`.

### Status on 2026-11-15

The audit below is kept as written on 2026-11-14. What has changed since:

- **Decisions.** Q1–Q7 are settled; §9 records each decision next to the
  recommendation. Q7 went the other way from the recommendation: GitHub
  `main` is authoritative.
- **G1, the importers.** Searched for on the development machine and not
  found: `~/cdos` holds only `corpus/`, and the candidates in `~/Downloads`
  (`tools/build_corpus.py`, `tools/learned_to_pf.py`,
  `INSTALL-CDOS-COMPLETE.sh` and its two payloads) all read finished `.pf`
  files or are unrelated. The Wikidata, DBpedia and OEWN importers are
  treated as lost: new ones are written in phase 4.
- **G12, repository drift.** Resolved. `esi/phase-1` (`1a99a5b`) was merged
  into `main` by tree comparison in `e533596` and archived as the tag
  `archive/esi-phase-1`. `PLATFORM_DELIVERY.md` was left out,
  `reviews/esi_bridge.json` was taken, and `BUILD` was removed.
- **D7.** The global git email is now set to the GitHub noreply address.
  Commits made before that keep the placeholder; history was not rewritten.
- **The test baseline (§1.3)** on the development machine (`earthos`:
  4 cores, 15 GB RAM, 83 GB free, `/usr/bin/python3` 3.13.5; there is no
  python3.12): Earth OS 1338 run, OK (4 skipped, 1 expected failure);
  `cdos-computer` 512 run, OK (97 skipped, 92 of them for numpy).
- `corpus.pf` still hashes to `f17a638f…a792f425`.
- **D1 is fixed** (`592983c`): the `--fix-wordnet` repair applies to OEWN
  rows only. Built with `--fix-wordnet --fix-wikidata --fix-keys`, the
  served corpus changes in exactly 425 rows (Wikidata `HYPERNYM` →
  `ANTONYM`); its version is now `sha256:87ee23f5…`. The `cdos-computer`
  suite is 516 run, OK (97 skipped).
- **Phase 1, the corpus guard, is built** (`cdos-computer/computer/corpus_guard.py`,
  command line `cdos-computer/tools/corpus_guard.py`). One writer at a time
  (flock, refused at once when held), a full backup checked by sha256
  before every change, a manifest with a pending record so a crash between
  the rename and the manifest is recovered, a hash-chained journal, and
  `verify` = build + `validate_corpus` + the 73 review cases, failing only
  when a case that passed on the last verified build no longer does.
  `build_shards.py` reads a guarded corpus under the shared lock. The real
  `corpus.pf` was guarded on 2026-11-15 (`init`, generation 1, one backup)
  and verified the same day: the 54 review cases that pass are the baseline.
- **Phase 2, the pipeline core, is built** (`cdos-computer/computer/acquire/`,
  `python3 -m computer.acquire {status,plan,run,show,integrate}`, root
  `~/cdos/acquire`). Approved sources only (Q1: `approved_by`/`approved_at`
  in `sources.toml`); downloads resume with HTTP Range, are size- and
  bandwidth-limited, and are verified by sha256 before use (failures are
  quarantined); runs go parse → map → normalise → validate → dedupe →
  conflicts → `staging/<source>/<run>/` with every rejected record logged
  and counts reconciled. `integrate` refuses: nothing writes `corpus.pf`.
  The fact key and row checks are shared by the build, the validator and
  the pipeline (`computer/pfrow.py`), with `ENTITY=` in the key (Q3); the
  real corpus still builds to `sha256:87ee23f5…`. A new row with `ENTITY=`
  is also a duplicate of the same fact in the master without one (a legacy
  row, until Q2 re-imports its source).
- **Phase 3, the streaming build, is built.** `build_shards` spills rows to
  per-shard files and deduplicates one shard at a time; the real corpus
  still builds to `sha256:87ee23f5…`, all 514 files identical decompressed,
  with peak memory 124 MB instead of 737 MB. Memory no longer grows with
  rows (50,000 → 400,000 rows over the same subjects: within 12 MB, where the
  old build grew by about 180 MB); it still grows with the number of
  distinct subjects, because `content_hash` reads `subjects.bin` whole.
- **The Pi (Q5, revised).** A Pi 4 with 2–4 GB and several GB free, running
  an always-on Claude Code search for new facts. There is no `pi` build
  profile yet: what the Pi holds is decided with the searcher's design
  (phases 5/9). Its findings go to a review queue as extracted facts, each
  with its page and quote; a person reviews them before anything is
  integrated (Q1 unchanged). The searcher's findings are not one source a
  person can approve once: each comes from a different page. So they stay
  under per-fact review even after `corpus.integrate` is switched on;
  source approval never covers them. The searcher runs sandboxed, with no corpus
  writer, git access or tokens, a budget and an e-stop, and treats web
  pages as untrusted input.
- **Phase 4, OEWN 2025 as a generation swap: the code is built, nothing is
  downloaded yet.** The 2025 edition (common words only, like the legacy
  rows, which have essentially no proper nouns) is read by the `gwa_xml`
  parser into rows of the legacy shape with words as values, never synset
  ids (D2); a registry `replaces` names the legacy generation (`OEWN` rows
  with `WN_x_nnnnnnnn` ids), which the dedupe index leaves out;
  `tools/swap_preview.py` writes the candidate (the master without that
  generation, plus the run's delta) in staging, builds the master and the
  candidate, compares their served rows relation by relation and checks
  the candidate against the review baseline. `build_shards --fix-wordnet`
  now repairs only legacy-id rows; the real corpus still builds to
  `sha256:87ee23f5…`.
- **Phase 4 run on real data (2026-11-15), staged only.** The approved
  source (`~/cdos/acquire/sources.toml`, approved by richie) was fetched
  and verified (`sha256:9ca6d1dc…`, 11,363,503 bytes): 1,039,726 records,
  999,223 staged, 21,998 duplicates within the file, 18,505 rejected (the
  unmapped relation types `other`, `is_entailed_by`, `is_caused_by`), no
  parse rejections; 107,519 synsets, the same number as the legacy rows.
  The swap preview (master without the 1,335,135 legacy OEWN rows, plus the
  999,223 new ones) builds to `sha256:ff38e69a…`, 1,106,278 facts, and the
  validator finds no problems. SYNONYM, DEFINITION, HYPERNYM and HYPONYM
  match today's served counts within 0.1 %. The large changes are
  corrections: the legacy import gave every word of a synset the
  word-to-word links of each of its words (DERIVATION −51 %, WN_ANTONYM
  −34 %, usage links), WordNet's verb groups are now WN_SIMILAR_TO, and the
  part and member relations were swapped (D8). One review case regresses,
  S1, which pins the exact fact count and fails for any new corpus; it has
  to be updated when a new corpus is accepted. `corpus.integrate` stays off.

- **GeoNames (phase 4, second source): the code is built, nothing is
  downloaded yet.** GeoNames publishes no checksums and regenerates its
  files daily, so a source can now be *pinned*: `python3 -m computer.acquire
  pin SOURCE` downloads its files without parsing them and prints their
  sha256s, a person approves by copying them into `sources.toml`, and the
  run uses exactly those files. A source can have several input files
  (`inputs`). The `geonames` parser reads `countryInfo.txt`,
  `admin1CodesASCII.txt` and `cities15000.zip`, resolves codes to names
  (a code that resolves to nothing is rejected), writes the corpus's own
  relation names and units, leaves populations undated (GeoNames doesn't
  say when they were counted) and marks every row `ENTITY=gn:<id>`. Runs
  report how many names stand for more than one place (D4). `GEONAMES` is
  ranked with Wikidata, and the registry refuses a source type the reader
  doesn't rank.
- **GeoNames run on real data (2026-11-15), staged only.** Pinned and
  approved: `countryInfo.txt` (`79f8f59e…`), `admin1CodesASCII.txt`
  (`2e4867f5…`), `cities15000.zip` (`ce7a4055…`). 211,859 records:
  190,091 staged, 20,204 held back, 1,541 duplicates (1,440 of them facts
  the corpus already has as legacy Wikidata rows), 23 rejected (region codes
  missing from the admin1 file). The first run showed two flaws, now fixed:
  the dedupe index read legacy Wikidata values as bare ids (D3), so names
  "conflicted" with ids; it now reads the master through the build's own
  row pass (`computer/served.py`, shared by the build, which is unchanged:
  same version, all 514 files identical). And places that share a name
  were compared with each other; comparisons are now by name, relation and
  `ENTITY`. 1,338 names stand for more than one place (Richmond ×9,
  Springfield ×8, …): their rows are **held back** (`held.pf`) until the
  reader can tell places apart (D4); a test build had answered "Springfield's
  population is 114,394, or 170,188, or 59,680". Remaining: 399 conflicts
  (CURRENCY 104, mostly GeoNames' short names such as "Dollar" against
  Wikidata's "Eastern Caribbean dollar"; AREA 129 and POPULATION 137, real
  differences between sources; CONTINENT 17) and 74 comparisons marked
  ambiguous. Not yet answered from these rows: "what time zone is X in"
  (no phrasing for `TIMEZONE`) and "what country is Lyon in" (the reader's
  country question doesn't use the new `LOCATED_IN` rows).

**Also found on 2026-11-15:**

- **The reader lists a word's meanings in row order** and reads out the
  first few. The 2025 file orders synsets by id, which put verbs before
  nouns ("what does colour mean" lost "a visual attribute…"); the parser
  now emits nouns, verbs, adjectives, adverbs, as the legacy rows did. The
  reader choosing meanings without depending on row order is a separate
  change.

**Open items found on 2026-11-15:**

- **Closed: qualifiers.** The column holds `KEY=VALUE` separated by `;`
  (`computer/qualifiers.py`); every existing row is one qualifier under it,
  and the validator now checks the form. `SENSE=` and `ENTITY=` say which
  fact a row is and are never said: a row with them answers exactly as the
  same row without them (`tests/test_qualifiers.py`), while content
  qualifiers (`REFERENCE=`, `MODE=`, …) still shape the answer.

- **"Is X a kind of Y" when only an opposite is known.** After D1, "is
  midnight a kind of noon" is answered "The opposite of noon is midnight."
  It no longer says a false "yes", but it does not answer the yes/no
  question either.
- **Five impossible-question cases in the real-corpus review.**
  `test_real_corpus.test_every_failure_is_classified` (run with `CDOS_DATA`
  set to a built `data/`) fails on W8, X1, X2, X3 and X6 ("what is the
  capital of jupiter" and the like): each is answered with a plain
  "NOT FOUND: no matching fact in the corpus…", which the review counts as
  a fail. It fails identically before and after D1, so it predates that
  change. The normal suite skips it because no `data/` folder exists.

---

## 0. Summary

**The read side is in good shape. The write side is missing.** CDOS can
build, verify, version and answer from a 1.2-million-fact corpus, and it
does that well. But no code in this repository adds a fact to `corpus.pf`.
The Wikidata, DBpedia and OEWN importers that produced it are not in this
tree. They belong to the separate CDOS project (`/home/richie/cdos`, the
`cd_chatbot` / `cdos` / `cdos-bridge` build), which was not uploaded.

Six findings shape the plan:

1. **The importers to reuse are not here.** I can tell from the data what
   they produced, but not how. Section 3 lists what I need.
2. **A live defect will corrupt new data.** `build_shards.py --fix-wordnet`
   relabels *every* `ANTONYM` row as `HYPERNYM`, from any source, not only
   OEWN. 425 Wikidata "opposite of" facts are already served wrongly
   (`MIDNIGHT HYPERNYM noon`). Every future importer that writes `ANTONYM`
   or `HYPERNYM` would be hit the same way (§4, D1).
3. **Subjects are stored by label, so homonyms merge.** 226 subject keys
   already hold more than one Wikidata entity (`MERCURY` holds the planet,
   the element and the god). At millions of entities this becomes the
   dominant data-quality problem. It can be fixed without changing the PF
   schema (§6.4).
4. **The shard build will not scale.** It holds the whole corpus in memory:
   716 MB peak for 1.46 M rows, about 490 bytes per row. A filtered Wikidata
   import of 20–30 M rows would need 10–15 GB. Streaming the build has to
   come before any large import (§5.1).
5. **The project's rules currently forbid what you're asking for.** The
   Growth Engine "never promotes", `growth_approval = "manual"` is "the only
   policy there is", and `cdos-growth.service` runs with `IPAddressDeny=any`.
   Autonomous growth is still possible, but approval has to move from each
   fact to each source. A person approves a source once (licence, mapping,
   limits), and from then on the engine may integrate validated batches from
   it. You need to agree to that change (§6.1).
6. **Licences differ by source, and the corpus is published.** Wikidata and
   OpenAlex are CC0. OEWN and GeoNames are CC BY 4.0. DBpedia is CC BY-SA.
   A public combined snapshot needs attribution, and if DBpedia rows are
   included, share-alike obligations probably carry over. (I'm not a lawyer;
   this needs your decision, §9.)

---

## 1. What already exists

### 1.1 Corpus storage and format

| Item | Finding |
|---|---|
| Master | `corpus.pf`: 1,459,260 lines = 2 header comments + 1,459,258 facts; 106 MB; UTF-8, tab-separated, 11 columns on every row |
| Columns | `subject relation kind value unit valid_from valid_until source_type source_id confidence qualifiers` |
| Kinds | S 1,430,671 · N 24,769 · D 3,814 · T 4 |
| Subjects | 143,777 as written; 141,383 after case-folding |
| Relations | 131 |
| Exact duplicate rows | 0 |
| Served form | `data/`: `meta.json`, `subjects.bin`, 256 `s/` and 256 `v/` gzip shards, built by `cdos-computer/tools/build_shards.py` |
| Built with fixes | 1,210,591 facts, 140,428 subjects, 129 relations; version `sha256:935d4547…` (the same as the M1.1 review, so the build is reproducible) |

**Facts by source.** The corpus is mostly a dictionary:

| source_type | rows | share | contents |
|---|---:|---:|---|
| OEWN | 1,335,135 | 91.5 % | 107,519 synsets; values are synset IDs (864,566 rows) |
| WIKIDATA | 109,818 | 7.5 % | 13,321 entities, 57 properties; `source_id` = `Q90/P36`; 69,125 values are bare QIDs |
| DBPEDIA | 5,824 | 0.4 % | 1,131 resources, all `CATEGORY` |
| MANUAL | 5,589 | | schema, number theory, multiplication table, SI, calendar |
| TABLE | 1,474 | | periodic table, geography, chemistry, materials |
| DOCUMENT | 811 | | reference notes (astronomy, biology, …) |
| INFERENCE | 606 | | `periodic_order`, `geo_inverse`, `category_rule`, `compound_inverse`, `polygon_rule` |
| LLM | 1 | | `speculative_model_output` (kept, and never stated as fact) |

### 1.2 Components, and where they are

| Concern | Component | State |
|---|---|---|
| Wikidata / DBpedia / OEWN importers | not in this repository (CDOS C project) | **not available to audit** |
| Corpus growers | none write `corpus.pf`. `computer/growth/` finds unanswered questions and trains *models* into staging; it never writes `data/` | reusable for finding gaps |
| PF → served shards | `tools/build_shards.py` (`--fix-wordnet --fix-wikidata --fix-keys`) | works; holds everything in memory |
| Validation | `tools/validate_corpus.py`: columns, kinds, numbers, dates, shard placement, totals, reverse index, duplicates, load-twice version check | works; read-only; tested |
| Integrity / versioning | `computer/corpus.py`: content sha256 over decompressed shards; `verify` / `strict` / `trust` modes; detects shards changed after load | works |
| Reading and reasoning | `computer/corpus.py`, `navigator.py`, `planner.py`, `plan.py`, `rules.py`; Earth OS reaches them via `docs/CDOS.md` and `earth_os/dictionary_bridge` | works; must stay working |
| Dedup | only inside `build_shards` (an in-memory set over the repaired key) | not persistent; nothing at import time |
| Provenance | per row: `source_type` + `source_id` | good; no record of dump version, retrieval date or licence |
| Fact classes | `SOURCE_RANK`; `NOT_KNOWLEDGE = {"LLM"}` | works for today's 8 source types |
| Conflicts | the Growth Engine classifies questions as `conflict`; `applicable()` handles time series | no detector at import |
| Locking, atomic writes | `governor.py` (`flock`, `write_atomic`, hash-chained audit, persistent e-stop); `state.py`; `neural/registry.py` | reusable. **Nothing locks `corpus.pf`** |
| Append-only journals | `neural/experience.ChainLog` (hash-chained) | reusable |
| Roles and approval | `governor.py`: `service` vs `operator`; `model.promote` is operator-only | reusable for source approval |
| Services | `systemd/` units, `tools/install_services.sh` (code, config and state kept apart; releases switched by symlink; sandboxed units) | reusable pattern |
| Local API | `computer/services/api.py` (Unix socket or loopback) | where the dashboard controls should go |
| Monitoring UI | Earth OS System Evolution Center (`earth_os/evolution/`, `screens/evolution.py`): reads and proposes, never acts | the natural home for a CORPUS section |
| Publishing | none. `earth-os-corpus` has `README.md` + `SHA256SUMS` (v1.0.0) only | to build |

### 1.3 The test baseline

| Suite | Here (this sandbox, Python 3.12) | Note |
|---|---|---|
| `cdos-computer` | **512 run, OK, 95 skipped** | the suite that covers the corpus |
| Earth OS `tests/` | 553 run, 7 failures, 32 errors, 25 skipped | 37 of the 39 come from what this sandbox lacks: Pango, GTK, cairo, sway, and the install's required packages. The other two (`'Robot' != 'Linux'`) are platform detection choosing the robot adapter, very likely for the same reason, but not proven. AGENTS.md expects 892 tests on your machine; that baseline has to be confirmed green there before any change |

---

## 2. What works

- **Determinism and integrity.** The same `corpus.pf` built with the same
  flags gives the same `sha256:935d4547…` every time. The validator finds no
  problems. Answers cite the corpus version.
- **The "never invents a fact" chain.** The executor is the only reader,
  missing data is reported as UNKNOWN rather than "no", and LLM rows are
  never presented as facts. New sources inherit all of this automatically,
  provided they're registered in `SOURCE_RANK` (§6.3).
- **Provenance per row.** Every Wikidata row says which entity and property
  it came from (`Q233/P38`), and every OEWN row says which synset.
- **Time.** `valid_from` / `valid_until` and `applicable()` already handle
  dated series (population since 2023) and periods that ended (Malta's
  currency before 2008).
- **Operational building blocks.** File locking, atomic replace, hash-chained
  logs, role-based approval, an e-stop, sandboxed systemd units and a
  release/rollback installer all exist and are tested. The acquisition
  engine should be assembled from them, not rewritten.

---

## 3. What's incomplete or missing

| # | Gap | Effect |
|---|---|---|
| G1 | **Importer source code** (Wikidata, DBpedia, OEWN) not available | Can't reuse or fix them; Phase 2 would have to reimplement them. **Please upload `/home/richie/cdos`** (or say where the importers live). |
| G2 | Import-time pipeline: download, verify, map, normalise, dedupe, stage | Doesn't exist |
| G3 | Persistent dedup index | Dedup happens only at build time, in memory |
| G4 | Source registry and licence registry | None; licence information isn't recorded anywhere |
| G5 | A writer lock and backups for `corpus.pf` | Two writers could interleave; no automated backup |
| G6 | Source versioning (replace OEWN 2023 with 2025; a new GeoNames day) | Can only append. Re-importing a newer release would duplicate rather than supersede |
| G7 | Streaming shard build | Memory grows linearly (§5.1) |
| G8 | A conflict detector that normalises first | 1,649 functional (subject, relation) pairs have more than one undated value. Many aren't real disagreements: `He` vs `HE`, `4.0026` vs `4.002602` |
| G9 | Publication tooling | None |
| G10 | A network-capable background service | `cdos-growth` is offline by design (`IPAddressDeny=any`) |
| G11 | Corpus monitoring in the UI | None |
| G12 | Repository drift | GitHub `earth-os` main is a single squashed commit (`bd36161`) that differs from the uploaded tree (`BUILD` vs `PLATFORM_DELIVERY.md`, `reviews/esi_bridge.json`). Which one is authoritative? |

---

## 4. Defects found

| ID | Defect | Evidence | Severity |
|---|---|---|---|
| **D1** | `build_shards --fix-wordnet` relabels `ANTONYM→HYPERNYM` and `HYPERNYM→HYPONYM` **for every source**, not only OEWN | 425 Wikidata P461 ("opposite of") rows are served as HYPERNYM: `MIDNIGHT HYPERNYM noon`, `SOIL HYPERNYM rock`, `ANTIMATTER HYPERNYM common matter`. The navigator follows HYPERNYM for "is X a kind of Y". | **High.** Wrong answers today, and it would silently corrupt every future source that uses those relation names. One-line fix: limit `RELABEL` to `stype == "OEWN"`. |
| D2 | OEWN import has swapped labels and leaves synset IDs as values | Documented in `build_shards.py`. 864,566 values are `WN_x_nnnnnnnn`. The repair happens at build time, so `corpus.pf` stays wrong | Medium. Correct new OEWN rows **can't be mixed** with legacy rows while the build-time relabel is on, or the new rows would be "repaired" into wrong ones |
| D3 | Wikidata values are bare QIDs | 69,125 rows; 14,905 can't be resolved and are dropped at build | Medium; a new importer should resolve labels at import |
| D4 | Label-keyed subjects merge homonyms | 226 of 13,081 Wikidata keys hold 2–5 entities (`MERCURY`: Q308, Q925, Q1150) | Medium now, high at scale |
| D5 | 16,980 Wikidata rows point at their own subject; 909 subjects are QIDs with no label | dropped at build | Low; fix at import |
| D6 | Conflicts that are really case or precision differences | §3 G8 | Low; normalise before comparing |
| D7 | The `earth-os-corpus` commit author email is a placeholder (`YOUR-GITHUB-EMAIL`) | `git show v1.0.0` | Cosmetic; set `git config user.email` before automated publishing |
| **D8** | *(found 2026-11-15)* The legacy OEWN import swapped holonym and meronym for part and member relations | Legacy rows say `CAR PART_HOLONYM accelerator` and `FLEET MEMBER_HOLONYM aircraft`; OEWN 2025 says `car PART_MERONYM accelerator`, `fleet MEMBER_MERONYM aircraft`. `--fix-wordnet` doesn't repair it | Medium; served wrongly today; the OEWN 2025 generation swap corrects it |

---

## 5. Constraints that shape the design

### 5.1 Scale

| Size | `corpus.pf` | Build RAM today | Served RAM (`subjects.bin` held in memory) |
|---|---|---|---|
| 1.46 M rows (today) | 106 MB | 716 MB, 16 s | ~15 MB keys (Pi-sized) |
| 10 M rows | ~0.7 GB | ~5 GB | ~100+ MB |
| 30 M rows | ~2 GB | ~15 GB | ~300+ MB (not Pi-sized) |

The README targets a Raspberry Pi Zero 2 W (512 MB). A fully grown corpus
won't fit on it. Suggestion: one master corpus, and **profiles** built
from it, e.g. `full` for the NUC/desktop and `pi` with a source and
relation allowlist. That's a build-time filter; the PF format doesn't
change.

### 5.2 Licences (to be recorded per source in the registry)

| Source | Licence | Verified |
|---|---|---|
| Wikidata | CC0 | yes |
| OpenAlex | CC0 | yes |
| Crossref metadata | CC0 waiver on the public data file; abstracts have limits | yes |
| Library of Congress (id.loc.gov) | public domain | yes |
| Open English WordNet | CC BY 4.0 | yes |
| GeoNames | CC BY 4.0 (credit GeoNames) | yes |
| DBpedia | CC BY-SA (core snapshots); some facts individually licensed | yes |
| Government open data | varies per dataset; record each one | per dataset |

Every published snapshot will carry a `NOTICE` with per-source attribution.
Whether to include CC BY-SA rows in the public snapshot, or publish them as
a separate asset, is decision **Q4**.

### 5.3 GitHub Releases

Each asset must be under 2 GiB, and a release can hold up to 1,000 assets.
There's no limit on total release size or bandwidth. Today's corpus
compresses to a few tens of MB, so this won't matter for a long time. The
publisher will still split anything over 1.9 GiB.

### 5.4 The project's own rules (AGENTS.md)

- *Failure is data*: an importer that can't parse a row rejects it with a
  reason. It never guesses a value.
- *A model's words are words*: LLM suggestions may go into a **proposal
  queue**, never into `corpus.pf` as facts.
- *Earth Mind coordinates, it does not act* and *the capability catalogue is
  closed*: dashboard controls must not become a back door. Reading status is
  fine. Start/pause/publish go through the CDOS API under governor roles.
  Exposing them as Earth OS capabilities is a deliberate catalogue change
  (entry + binding + phrasings), done once and reviewed.

---

## 6. How autonomous growth should fit in

### 6.1 Approval moves from facts to sources

```
 operator (person)                           service (cdos-acquire)
 ─────────────────                           ───────────────────────
 approve source  ─── registry entry ───►     may download, stage, validate, and
   licence, mapping,   (signed into the       INTEGRATE batches from approved sources,
   relation allowlist,  governor audit)        and PUBLISH verified snapshots
   rate/size limits
 approve discovered  ◄── proposal ──────     Mode B may only PROPOSE new sources
   source                                    estop.json halts everything at once
```

New governor actions: `corpus.source.approve` (operator only),
`corpus.integrate` and `corpus.publish` (service, only for approved sources,
and turned off until §8 phase 8 passes). The "never promotes" rule then
holds where it matters: no new source enters without a person.

### 6.2 Data flow

```
 registry ─► download/ (resume, verify size+sha, skip unchanged by ETag)
          ─► parse ─► map to CDOS relations ─► normalise (keys, labels, units, dates)
          ─► validate (kinds, numbers, dates, ranges; reject with reason)
          ─► dedupe (persistent index) ─► conflict check (normalised, recorded, never deleted)
          ─► staging/<cycle>/delta.pf + report.json      ← nothing touches the master before here
          ─► [corpus.pf.lock] backup ─► write corpus.pf.new = master + delta (or a source generation swap)
             ─► fsync ─► build shards to a temp dir ─► validate_corpus ─► regression questions
             ─► os.replace(corpus.pf.new, corpus.pf) ─► build the served data/ ─► journal
          ─► publish queue ─► snapshot (zstd) + SHA256SUMS + manifest.json + NOTICE ─► gh release
```

`corpus.pf` stays a single file in the same format, so the C binaries and
`build_shards.py` keep working unchanged. Above about 2 GB, splitting into
`corpus.d/` parts can be reconsidered as a documented change.

### 6.3 Keeping fact classes distinct, without a schema change

The class comes from `source_type`, kept in one registry:

| Class | source_type | In `corpus.pf`? |
|---|---|---|
| sourced | WIKIDATA, OEWN, GEONAMES, OPENALEX, LOC, CROSSREF, DBPEDIA, TABLE, MANUAL | yes |
| inferred | INFERENCE (rule name in `source_id`) | yes, ranked below sourced |
| extracted | EXTRACTED (document + offset in `source_id`) | yes, after review |
| generated | LLM | **no**: proposals queue only (the one existing LLM row stays) |

`corpus.py`'s `SOURCE_RANK` has to learn every new `source_type`.
Otherwise an unknown source ranks 3, the same as `LLM`/`?`. A test should
fail when a registry source is missing from `SOURCE_RANK`.

### 6.4 Entity identity

New rows carry the source entity in the existing **qualifiers** column, e.g.
`ENTITY=wd:Q1150` or `ENTITY=gn:2988507`. The build's dedupe key already
includes `SENSE=` and would include `ENTITY=` the same way, and
`Fact.sentence()` would skip it as it already skips `SENSE=`. Homonyms then
stay separate facts under one lookup key. The navigator can tell them apart
later; that's a separate, later change.

### 6.5 Superseding a source version

Integration works on **source generations**. Importing OEWN 2025 replaces
the OEWN 2023-era rows in one atomic rewrite rather than appending next to
them. Removed rows are written to a retraction log, not lost, and the
previous master stays in `backups/`. This is how D2 gets fixed properly:
OEWN is re-imported correctly, and `--fix-wordnet` is retired for it.

---

## 7. Source assessment (Phase 2 order)

| # | Source | What to take | Size | Value for CDOS | Complexity | Recommendation |
|---|---|---|---|---|---|---|
| 1 | **OEWN 2025** (GWA XML) | full; replaces legacy OEWN | tens of MB gz | High: fixes D2 for 91 % of the corpus | Low | **First.** Proves the generation swap on a small source |
| 2 | **Wikidata** truthy N-Triples (CC0) | filtered: English label, sitelink threshold, property allowlist (the existing 57 + curated additions), labels resolved at import | ~40 GB bz2 per weekly dump, ~8 B triples; a filtered output of a few M entities | Very high | High (one streaming pass, CPU-bound; parallel bzip2) | **Second**, after the streaming build (§8 phase 3) |
| 3 | **GeoNames** (CC BY 4.0) | `cities500`/`cities15000` + `countryInfo` + `admin1`, then `allCountries` | small subsets; `allCountries` is several hundred MB zipped, 11 M+ names | High for "where/how many people" | Low (TSV) | Third; subset first |
| 4 | **LoC LCSH** (public domain) | subjects: broader/narrower/variants | moderate N-Triples | Medium (topic hierarchy) | Medium | Fourth |
| 5 | **OpenAlex** (CC0) | institutions, publishers, funders, topics. **Not works** | small entity sets; works alone ~330–750 GB | Medium | Low (JSONL + manifests) | Fifth, entities only |
| 6 | **Crossref** (CC0) | none for now | 208 GB / 180 M records, yearly | Low for questions people ask CDOS | High | **Defer**; OpenAlex covers institutions |
| 7 | **DBpedia** (CC BY-SA) | mapping-based objects/literals, if Q4 allows | GBs | Overlaps Wikidata | Medium | **Defer** pending Q4 |
| 8 | Government open data | specific, high-value tables (e.g. NIST constants, USGS GNIS, Natural Earth), each evaluated alone | small | High per row | Low each | Mode B candidates |
| — | Others to evaluate in Mode B | Wikidata Lexemes (CC0), ConceptNet (CC BY-SA, dated), Open Food Facts (ODbL) | — | — | — | evaluate; licences to confirm |

Every importer meets the nine requirements in your brief (resume, verify,
incremental, dedupe, original IDs, attribution, licence record, counts of
imported/rejected/duplicate, repeatable), and its tests run on small
fixtures checked into `tests/fixtures/`. That's also necessary here: this
sandbox can't reach any of these hosts, so real downloads happen on your
machine.

---

## 8. Phased implementation plan

Each phase ends with its tests green, a commit, and a short doc update, per
AGENTS.md §7.

| Phase | Work | Depends on | Resources (your machine) | Passes when | Main risk |
|---|---|---|---|---|---|
| **0. Ground truth** | Upload the CDOS importer source; confirm 892/892 on your machine; settle Q1–Q6; **fix D1** (scope RELABEL to OEWN, plus a regression test that a Wikidata ANTONYM survives the build) | — | none | the suite is green; the D1 test fails before and passes after; the served corpus changes only in those 425 rows | the fix changes 425 answers; that's intended |
| **1. Guard rails** | `corpus_guard`: a writer lock (`flock` on `corpus.pf.lock`), backups (keep N, hardlink and zstd), a sha256 manifest, `verify` = build to temp + `validate_corpus` + a regression question set | 0 | disk ≈ 3× corpus | a second writer is refused; a killed write leaves the master byte-identical; restore from backup is tested | — |
| **2. Pipeline core** | `computer/acquire/`: registry (TOML + licence fields), downloader (HTTP Range resume, size/sha/ETag, token-bucket bandwidth), stage → validate → dedupe (SQLite) → conflicts → `delta.pf` + report → integrate via phase 1 → ChainLog journal. CLI `python3 -m computer.acquire {status,plan,run-source,integrate,verify}` | 1 | dedupe index ≈ 40 B/fact | runs end to end on fixtures; re-running is a no-op (0 new, N duplicate); a corrupt download is rejected | dedupe semantics drifting from the build's key; one shared key function avoids it |
| **3. Scale** | Streaming `build_shards` (external sort, bounded RAM, byte-identical output on today's corpus); `ENTITY=` in the dedupe key; build profiles (`full`, `pi`) | 1 | — | same `sha256:935d4547…` on today's corpus; peak RAM flat as rows grow | output drift; the identical-hash test guards it |
| **4. Seed importers** | OEWN 2025 (generation swap) → GeoNames subset → Wikidata filtered → LCSH → OpenAlex entities. One source per commit | 2, 3 | Wikidata: ~45 GB download, ~10–20 h CPU per pass, a few GB of output | per source: counts reconciled (read = imported + rejected + duplicate); spot-checked answers; the full CDOS suite and the 73-case review still pass | Wikidata noise and vandalism; property allowlist, sitelink threshold, conflict log |
| **5. Service** | `cdos-acquire.service` (network allowed, sandboxed, `Nice`, `CPUQuota`, `MemoryMax`, `IOWeight`, idle I/O); persistent SQLite task queue; retry with backoff; disk-floor pause; SIGTERM = safe stop at a batch boundary; e-stop | 2 | — | the kill/restart/reboot tests below | — |
| **6. Publication** | Snapshot + `SHA256SUMS` + `manifest.json` (version, fact count, per-source counts, licences) + `NOTICE`; `gh release create` with unique tags; `latest.json` committed to the repo; retry queue; tokens never logged | 1, 5 | GitHub: one compressed snapshot per release | GitHub auth and upload failures leave the master untouched and the snapshot queued; a re-run never overwrites a tag | release storage creeping up; default to at most one publication per day and only when the corpus changed |
| **7. Dashboard** | `status.json` from the engine → a read-only CORPUS section in the Evolution Center; controls via the CDOS API (governor-gated) | 5, 6 | — | the Evolution Center tests are unchanged and green; controls are refused without the role | boundary rules (§5.4) |
| **8. Testing and soak** | Your 12 failure tests, automated where possible (fault injection: truncated download, `kill -9` mid-integrate, `ENOSPC` via a small loop filesystem, network down, bad `gh` token, two writers); then a **72-hour** supervised soak with publication on | 4–7 | — | the corpus is verified before and after each test; CDOS retrieval questions answer the same or better | — |
| **9. Unattended** | Mode C (re-check approved sources for new versions; Wikidata weekly); then Mode B (discovery proposals only); operations guide | 8 | — | two weeks unattended with no intervention | — |

**Where the work starts.** Phases 0–2 are the "established database
acquisition and reliable import infrastructure" you asked to begin with.
Nothing in them needs the network, and nothing writes the master except
through the phase 1 guard.

---

## 9. Decisions needed from you

| | Question | My recommendation | Decision |
|---|---|---|---|
| **Q1** | Move approval from facts to sources (§6.1), changing the "never promotes" policy for corpus data only? | Yes, with `corpus.integrate` off until phase 8 passes | **Yes.** Sources are approved, not individual facts. `corpus.integrate` stays off until phase 8 passes |
| **Q2** | Repair legacy OEWN/Wikidata rows by re-importing them (generation swap) rather than keeping the build-time fixes forever? | Yes | **Yes.** Re-import the legacy rows as a generation swap; the build-time fixes are not kept permanently |
| **Q3** | Add `ENTITY=` qualifiers to new rows (no schema change)? | Yes | **Yes.** New rows carry `ENTITY=wd:Q…` / `ENTITY=gn:…`; no schema change |
| **Q4** | Include CC BY-SA (DBpedia) rows in the public snapshot? | Publish them as a separate asset, or leave DBpedia out | **Not in the main snapshot.** DBpedia goes out as a separate release asset, or not at all |
| **Q5** | Which machine runs it (CPU cores, RAM, free disk), and is the Pi still a target for the full corpus? | Run on the NUC/desktop; a `pi` build profile for the Pi | **This desktop** (`earthos`: 4 cores, 15 GB RAM, 83 GB free) guards, builds, verifies and integrates. **Revised 2026-11-15:** the Pi (a Pi 4, 2–4 GB) runs an always-on web searcher whose findings go to a review queue, and stay under per-fact review even after `corpus.integrate` is on (they are not one approvable source); no `pi` build profile yet (first decided: "a smaller build, not the full corpus") |
| **Q6** | Release tag scheme | `v1.0.0` stays the baseline; then `v2026.11.14-1` (UTC date + cycle) | **As recommended:** `v1.0.0` is the baseline; later tags are `vYYYY.MM.DD-N` |
| **Q7** | Which repo is authoritative: GitHub `earth-os` (`bd36161`) or your local tree (`1a99a5b`)? | Your local tree; GitHub looks like a squashed export | **GitHub `main`.** The local tree was merged into it (`e533596`, 2026-11-15) and archived as `archive/esi-phase-1` |

---

## Appendix A. Reproducing the measurements

```bash
sha256sum corpus.pf                                   # f17a638f…a792f425
python3 cdos-computer/tools/build_shards.py corpus.pf --out /tmp/data \
        --fix-wordnet --fix-wikidata --fix-keys       # 1,210,591 facts, 16 s, 716 MB peak
python3 cdos-computer/tools/validate_corpus.py /tmp/data   # no problems; sha256:935d4547…
for f in /tmp/data/s/*.bin; do zcat $f; done \
  | awk -F'\t' '$8=="WIKIDATA" && $2=="HYPERNYM"' | wc -l  # 425  (D1)
(cd cdos-computer && /usr/bin/python3 -m unittest discover -s tests -q)   # 512 OK
```

## Appendix B. Sources consulted

- Wikidata dumps (CC0; truthy ≈ 37–40 GB bz2): https://dumps.wikimedia.org/wikidatawiki/entities/
- Open English WordNet, CC BY 4.0: https://en-word.net/
- GeoNames export, CC BY 4.0: https://www.geonames.org/export/
- OpenAlex snapshot, CC0: https://help.openalex.org/access/snapshot
- Crossref public data file 2026: https://www.crossref.org/blog/2026-public-data-file-now-available/
- Library of Congress Linked Data Service, public domain: https://id.loc.gov/about/
- DBpedia licensing: https://www.dbpedia.org/dbpedia-enterprise/
- GitHub release limits: https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
