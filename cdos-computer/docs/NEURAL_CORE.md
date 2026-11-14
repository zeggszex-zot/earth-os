# CDOS Neural Core 0.1: design note

An optional learning subsystem, `computer/neural/`, around the existing learned controller. The deterministic
engine, corpus, plan language, validator, result envelopes and fallbacks are unchanged. The new code adds a versioned
feature schema, training records with provenance, staged training, evaluation gates, explicit activation, a safe
loader and an experience log.

**Result in one line.** On the test corpus, a model trained this way beats its own untrained initialisation on the
predefined held-out metric with every seed tried (net right−wrong 134.6 vs 67.2). It loses no answer the computer got right
without it, and the whole computer says fewer wrong things with it than without it. **The production corpus and the NUC
weren't available**, so none of this is shown at production scale. See *Limits*.

## 1. Baseline (before any change)

- **Tests:** `python3 -m unittest discover -s tests` gave 354 run, OK, 4 skipped (they need the production `data/`).
  19 s on the build machine (Xeon 2.1 GHz, 2 cores, Python 3.13, numpy 2.5).
- **Experiment:** `tools/experiment_learning.py` on the mini corpus is reproducible run to run (identical JSON).
  Its numbers differ slightly from the README's (B: 99/6/23, not 97/8/22), because the README's are older.
- **The existing controller** (`computer/controller.py`, `controller_train.py`, `controller_eval.py`):

| | |
|---|---|
| input features | `features()`: masked tokens, bigrams, first/last tokens, name and free-word counts, length, possessive, comparative, `less`, `-ing`, verb direction (`role:`), and whether the first name is a dictionary word or has `IS_A`. Hashed (FNV-1a, `"f:"` salt) mod 4096 into a **set** of indices |
| network | sparse input → 32 ReLU (1/√n scaling) → softmax. About 100k weights, plain-Python inference |
| action vocabulary | **29** plan shapes (`ACTION_NAMES`). The README says 24, which is stale |
| training data | `navdata.generate` teacher examples (executed, checked) + `reword()` paraphrases + optional `--mine`. Labels come from `explore()`: the simplest actions whose plan reaches the gold answer and passes `accept()` |
| optimiser | Adam, lr 0.01, L2 1e-4, batch 32 (hard-coded), cross-entropy against the uniform distribution over target actions |
| checkpoint | JSON `cdos-computer-controller/1` (`actions, dim, hidden, rows, b1, w2, b2`), or `-q/1` quantised. There is **no** manifest, schema version, data hash or integrity check |
| integration | `navigator_controller` → `Engine.load_controller` → `ControllerPlanner` after `RulePlanner`. `navigate()` runs rule plans first, and a learned answer is said only if `accept()` passes |

**Defects found in the existing path** (all reproduced on the original code before any edit):

1. `controller.json` with `"rows": []` → `AttributeError` in `Computer.__init__`: **CDOS doesn't start**.
2. A weight row longer than `hidden` loads, then raises `IndexError` inside `think()`: **the turn crashes**.
3. NaN weights load silently, and the controller then ranks actions arbitrarily.
4. Envelopes credit controller answers to **`planner.lm@1.0.0`**. `core._envelope` matched the whole plan source
   (`"learned:hop 0.97"`) against `"learned"`.
5. *(Not changed; reported.)* With the rules alone, "things that orbit ganymede" is answered "Ganymede orbits Jupiter."
   by the one-shot lookup. No rule pattern fires, so the lookup answers the reverse question with the forward fact.

## 2. Design

```
 train ──► <registry>/staging/<id>/ ──evaluate──► eval.json + gates ──promote --approve──► models/<id>/ + ACTIVE.json
 (never touches models/ or ACTIVE.json)                                (atomic; audited in promotions.jsonl)
                                                                                  │
 Computer start-up: load_active() → every check passes → NeuralPlanner after the rules; anything else → rules only
 each turn: rules' plans first → neural plans (fixed templates, re-validated) → executor → accept() → said only if answered
            └─► experience/experience.jsonl (append-only, hash-chained): prediction + checked outcome
```

**Reuse, not replace.** The brief asked to keep the existing approach unless experiments showed it inadequate. The
same network, features and labels clear every gate here, so nothing larger was added: no new dependency, inference
stays plain Python, and numpy is needed only to train. `train_network()` is the existing optimiser with batch size and
time limits as parameters. A test checks that at batch 32 it produces **the same weights** as `controller_train.train`.

| module | does |
|---|---|
| `schema.py` | schema v1 = the existing `features()` + `hashed()` + `ACTION_NAMES` + each action's plan template, fingerprinted (sha256 of the description). A golden file pins the features of 12 questions |
| `records.py` | one record per question: inputs (feature strings and indices), every candidate action with its plan, validator verdict, executor status, evidence ids, `accept()` verdict and outcome, the targets and the rule that chose them, and provenance. `explore_detailed()` picks exactly `explore()`'s labels (tested) |
| `train.py` | reproducible pipeline into staging: fixed seeds, streamed records (RAM stays flat on a big corpus), threads/nice/wall-time/CPU-time/epoch/batch limits, leak checks, manifest. The model id is the weights' hash |
| `model.py` | checkpoint and manifest validation: sizes, hashes, schema fingerprint, action list, shapes, finite bounded numbers, and a probe run on every row. Raises `ModelError` (missing/corrupt/incompatible) |
| `runtime.py` | `load_active()` raises only `NeuralUnavailable`. `NeuralPlanner` (a `ControllerPlanner`) re-parses each plan with the corpus's relation names and checks it is an instance of its action's template. If it ever throws, it goes silent for the session |
| `evaluate.py` | identical fixtures for every system, navigate-level and whole-computer comparisons, gates, `eval.json`/`eval.txt` |
| `registry.py` | promote (needs eval for these exact bytes, against the currently active model, all gates passed, **and** `approve`), rollback, deactivate, status; atomic `ACTIVE.json`; append-only audit |
| `experience.py` | hash-chained JSONL: model id, question, top-5 scores, each plan's run status and `accept()` verdict, what was said (envelope status/truth/certainty/evidence ids/corpus version), and the rules' action when they answered (a free agreement metric). `verify` detects edits, removals and damage |
| `bench.py` | load time, per-stage latency, navigate with/without the model, RSS |

### How the hard invariants are kept

| invariant | mechanism | tests (`tests/test_neural.py`) |
|---|---|---|
| 1 validator authoritative | the network outputs action scores only. Plans come from `ACTIONS` templates via `fills()`, are re-parsed by `plan.parse(…, relation_names)`, and must match their template's op sequence | `test_the_planner_only_proposes_template_plans`, `test_an_action_this_code_doesnt_have` |
| 2 no corpus writes | nothing in the package opens `data/` for writing; the registry may not be inside (or contain) the corpus; training and evaluation compare every corpus file's size/mtime and the content hash before and after, and abort otherwise | `test_zz_the_corpus_is_untouched_by_all_of_it`, `test_a_registry_inside_the_corpus`, gate `corpus_unchanged` |
| 3 evidence and envelopes | neural answers go through the same executor and `envelope.from_result`. Rule answers are byte-identical with the model on (canonical hash) | `test_rule_answers_and_their_envelopes_are_unchanged`, `test_neural_answers_keep_the_evidence_and_the_envelope` |
| 4 unsupported stays unknown | a neural result is used **only** if it answered and passed `accept()`. Otherwise the turn is re-navigated without the model and goes on exactly as it would have | `test_a_neural_non_answer_is_not_said`, `test_the_model_changes_nothing_but_adding_checked_answers` |
| 5 safety routing, calculations | untouched: the neural planner is reached only from `_navigate` for fact/explain/define intents | `test_safety_routing_and_calculations_are_unchanged` (calc, %, health, money, crisis, device control, analysis follow-ups) |
| 6 failure can't stop start-up | every load path is wrapped; more than 20 kinds of damage are refused with a reason and the computer answers with the rules | `TestInvalidCheckpoints` (missing, truncated, flipped byte, non-JSON, bad rows, NaN/inf/bool/huge, unknown action, reordered actions, wrong dim, wrong schema, path traversal, bad `ACTIVE.json`…), `TestFallback` |
| 7 no hardware, no network | `planner.neural` is declared deterministic: the capability scan forbids socket/http/random/clock in its modules, and the import review now follows sub-packages | `test_no_network_anywhere` (train + evaluate + answer with sockets blocked), `test_the_neural_answer_path_is_declared_deterministic_and_clean` |
| 8 no unexplained changes | every edit to an existing file is listed in §6 with its test | all 354 original tests pass unchanged |

## 3. Training records, manifest, schema

Record (`cdos-neural-record/1`): `question, split, source, inputs{features, idx, names, relation_words, free},
candidates[{action, plan, validator, status, evidence, accept, outcome}], targets, label_rule, runs, provenance{kind,
gold, expect, subjects, concepts, teacher_action, teacher_plan, reworded_from, example_id, origin}, corpus_version, year`.
Feature strings are stored as well as indices, so a later schema can re-hash old records.

Manifest (`cdos-neural-manifest/1`): `model_id, created, model_sha256, untrained_sha256, schema{version, fingerprint},
actions, training_data{sha256 (= sha256 of the decompressed records), counts, leak_check, corpus_version, generator,
fixtures{sha256}}, hyperparameters, training{epochs_completed, stopped_early, loss_by_epoch}, software{neural_version,
code_sha256, python, numpy, platform, cpu}, metrics, resources{wall, cpu, explore, train seconds, peak_rss_mb}`.

**Leakage controls.** Splits are by subject. `navdata.leak_free` drops training examples that touch a held-out subject.
Training paraphrases use only the training half of the templates. Any training question whose normalised text equals a
held-out or regression fixture question is dropped (generated) or refused (`--mine`). A `--mine` question that names a
held-out subject is dropped. All of this is counted in the manifest.

## 4. Evaluation

Systems (all run after the rule planner): `rules`, `rules+untrained` (the same network at initialisation, same budget),
`rules+trained`, `rules+try-every` (the repo's earlier "untrained" baseline: every action, no floor, ≤99 plans), and
`rules+active` when a model is already active. Fixtures: **A** held-out subjects with generator phrasings, **B**
held-out subjects with held-out phrasings (both materialised at training time), **D** hand-written held-out
(`controller_heldout`, `lexicon_heldout`), **R** regression sets the rules already answer.

**Predefined primary metric** (fixed in `evaluate.PRIMARY` before the first evaluation ran): *net = right − wrong
summed over B and D; trained must beat untrained.* Secondary: held-out top-1 selection accuracy.

**Added after the first run, disclosed.** The navigate-level comparison (what `controller_eval` and the experiment
measure) leaves out the one-shot lookup. A learned plan that answers is said before the lookup is asked. The test suite
caught this ("france capital?": the lookup's answer was replaced by a neural one with the same text), so the evaluator
now also asks every fixture of a whole `Computer` without and with the model and scores what it says. Two gates were
added: no right answer lost at either level, and whole-computer held-out net not worse. Both are stricter, not looser.

Gates (all required before `promote`): `loads, same_corpus, selection_improves, primary_improves,
no_rule_regressions, computer_not_worse, regression_sets_clean, fewer_wrong_than_try, not_worse_than_active,
deterministic, corpus_unchanged`.

## 5. Results (mini corpus, 59 subjects; 5 training seeds, same data)

The data hash is identical across seeds. Seed 0 retrained in a separate process gives the same model id
(`nc1-d8c0f1aa741b`). Where trained and untrained vary by seed, the tables show the mean and [min–max].

**Navigate level** (right / wrong / abstained, net):

| set (n) | rules | untrained (init) | **trained** | try-every |
|---|---|---|---|---|
| A unseen subjects (58) | 57/0/1, 57 | 57/0/1, 57 | 57/0/1, 57 | 58/0/0, 58 |
| B unseen subjects + phrasings (128) | 46/1/81, 45 | 51.4/6.0/70.6, 45.4 [35–56] | **98.2/6.2/23.6, 92.0 [91–93]** | 87/36/5, 51 |
| D controller_heldout (42) | 11/0/31, 11 | 12.4/1.8/27.8, 10.6 | **31.6/2.0/8.4, 29.6 [29–30]** | 33/6/3, 27 |
| D lexicon_heldout (28) | 12/0/16, 12 | 11.8/0.6/15.6, 11.2 | **13/0/15, 13** | 15/6/7, 9 |
| R regression (94) | 94/0/0 | 94/0/0 | 94/0/0 | 92/2/0 |
| **primary (net B+D)** | 68 | 67.2 [55–76] | **134.6 [134–135]** | 87 |
| plans per question (B) | 0.34 | 1.16 | 1.77 | 13.53 |

**Selection** (180 held-out rows, top action is one the checker accepts): trained **65.9%** (63.9–67.8), untrained
5.8% (0–16.1), chance 4.9%.

**Whole computer** (what is said; seed 0 shown, seeds 1–4 within ±2 on every count, held-out net 173–175):

| set | without the model | with the model |
|---|---|---|
| A (58) | 56/1/1 | 56/1/1 |
| B (128) | 68/40/20, net 28 | **100/22/6, net 78** |
| D controller_heldout (42) | 21/8/13, net 13 | **33/4/5, net 29** |
| D lexicon_heldout (28) | 14/2/12 | 14/2/12 |
| R regression (94) | 94/0/0 | 94/0/0 |
| held-out total | 159/51/46, net 108 | **203/29/24, net 174** |

**Against the rule-only baseline, including regressions:**

- **Lost right answers: 0** at both levels, for every seed.
- Navigate level: the model adds **7–8 wrong answers** where the rules abstained. Each is a true fact answering a
  neighbouring question: yes/no questions answered without the "yes" ("i think ganymede orbits jupiter" → "Ganymede
  orbits Jupiter."), "jupiter satellites, how many" → the list rather than 4, "different dogs" → the definition of dog,
  "sad the opposite of glad?" → "The opposite of sad is glad.", "owl: a kind of animal?" → "Animal is a kind of organism."
- Whole computer: the model adds **1–2** wrong answers ("owl: a kind of animal?" at score 0.16, "different dogs" at 0.21)
  and replaces **24** wrong lookup answers with right ones (transitions, seed 0: wrong→right 24, abstained→right 20,
  abstained→wrong 2). Of the 27 answers wrong both ways, 21 still come from the deterministic engine. In the other 6,
  a neural plan now says the same wrong sentence the lookup said ("i think ganymede orbits jupiter" → "Ganymede orbits
  Jupiter."), so the navigate-level "added" wrong answers are mostly ones the computer already gave.
- `neural_min_p` is a weak lever. Raising it from 0.08 to 0.25 only moves the whole computer from 203/29/24 to
  200/27/29, because the existing controller always tries its top-scored action even below the floor.

## 6. Changes to existing files (each with a test)

| file | change | why |
|---|---|---|
| `computer/core.py` | `_load_neural()`, never fatal; profile limits apply to `neural` too; a neural result is used only if answered; `_experience()` after each turn; `--check` data | the integration |
| `computer/core.py` | legacy `navigator_controller` load: catch every exception, then `probe()` the model | defects 1–3: start-up crash, turn crash, NaN model. **Behaviour change:** such files are now refused with a reason. `TestControllerFileHardening` |
| `computer/core.py` | envelope credits the planner by source prefix (`learned:` → `planner.controller`, `neural:` → `planner.neural`) | defect 4. **Behaviour change:** controller answers' `capabilities` now name the planner actually used. `test_a_staged_model_is_also_a_valid_controller_file` |
| `computer/capabilities.py` | declares `planner.neural` (deterministic, optional, falls back to rules) and `neural.experience` (writes; added to `WRITERS`); disabled unless configured | every capability is declared and checked at start-up |
| `computer/capabilities.py` | `internal_imports()` resolves sub-package and `from . import x` imports | the old regex silently skipped both, so `computer/neural/*` would have escaped the import review (as did `navigator.py`'s `from . import logic`, which resolves to a checked module: no new findings) |
| `computer/config.py`, `config.example.toml` | `neural_registry` (default empty = off), `neural_min_p`, `neural_experience` | |
| `computer/__main__.py` | `--check` prints the neural model, its schema, held-out top-1, whether it was trained on this corpus, and why it's off | |
| `README.md` | a short section, the new files, the new tests | |

With the model on, one envelope field can differ on a turn the model *didn't* answer: `capabilities` gains
`navigator.execute`, because the executor really did run the neural plans. Every other field, the text and the
evidence are identical (`assert_same_but_the_executor_ran`).

## 7. Performance (this machine; **not the NUC**)

Measured on the build machine (Xeon 2.1 GHz, 2 cores, Python 3.13.17, numpy 2.5.3), mini corpus:

| | |
|---|---|
| training, end to end | 2.6–3.3 s wall (explore 1.5–1.8 s, train 0.8–1.0 s, 40 epochs, 224 rows), peak RSS 58 MB |
| model load + validation | 9.6 ms; RSS +0.7 MB; `model.json` 130 KB, 441 feature rows |
| features + network | mean 0.19 ms, p95 0.27 ms (int8: 0.15 ms) |
| fill + validate plans | mean 0.25 ms, p95 0.49 ms |
| `navigate()` | rules only 0.21 ms; with the model 0.92 ms mean, p95 1.59 ms (it runs plans the rules didn't have) |
| whole `think()` | 0.52 ms without → 1.20 ms with → 1.43 ms with the experience log |
| cold start | 165–240 ms either way (difference within noise); RSS 23 → 24 MB |

On the production corpus the existing experiment took 4–16 min and ~1.1 GB. Training here is the same exploration with
records streamed to disk, so expect similar time plus the records file (`--summary-records` shrinks it). To measure on
the NUC:

```sh
/usr/bin/time -v python3 -m computer.neural train --corpus data --registry models/neural --subjects 3000 --threads 4
python3 -m computer.neural bench --corpus data --registry models/neural --model <id> \
    --questions tests/real_heldout.jsonl --repeat 5 [--precision int8]
```

The manifest records wall time, CPU time and peak RSS for every training run, and `bench` prints latency percentiles
and RSS.

## 8. Commands

```sh
# reproducible development: the test corpus and the hand-written fixtures in tests/
python3 -m computer.neural train    --preset mini --registry /tmp/reg            # prints the model id
python3 -m computer.neural evaluate --preset mini --registry /tmp/reg --model <id>
python3 -m computer.neural promote  --registry /tmp/reg --model <id>             # dry run: what would happen
python3 -m computer.neural promote  --registry /tmp/reg --model <id> --approve

# your corpus
python3 -m computer.neural train    --corpus data --registry models/neural --seed 0 --epochs 40 --batch 32 \
    --threads 2 --nice 10 --max-seconds 3600 [--mine my_questions.jsonl] [--lexicon lexicon.json] \
    --heldout my_heldout.jsonl --regression tests/real_heldout.jsonl
python3 -m computer.neural evaluate --corpus data --registry models/neural --model <id> \
    --heldout my_heldout.jsonl --regression tests/real_heldout.jsonl
python3 -m computer.neural promote  --registry models/neural --model <id> --approve
# config.toml:  neural_registry = "models/neural"
python3 -m computer --check                                  # neural  nc1-… (schema v1 …) or why it's off

python3 -m computer.neural status | rollback --to <id> --approve | deactivate --approve
python3 -m computer.neural log verify | log stats            # the experience log's chain; agreement with the rules
python3 -m computer.neural schema                            # the schema and its fingerprint
python3 -m unittest tests.test_neural -v
```

Pass the same `--heldout`/`--regression` files to `train` and `evaluate`, so `train` can check that none of those
questions is trained on. Relative `--registry` paths are taken from the current folder, while `neural_registry` in
`config.toml` is taken from the repo root.

## 9. Limits and next steps

- **Scale.** 59 subjects, 224 training rows, about 250 held-out questions. On the production corpus the README's own
  experiment found the controller added one right answer on 40 real held-out questions. Run `train` and `evaluate` on
  `data/` with a few hundred questions you wrote before promoting anything there.
- **B is optimistic.** Its phrasings are templates. The two template halves also meet once normalised ("{s} {t}" and
  "{s}'s {t}?" both give "moon radius"). That can't leak into B, whose subjects are never trained on, but "unseen
  phrasing" is partly cosmetic. D is the fair test.
- **"Verified" outcomes in the experience log are machine-verified** (validator, executor, `accept()`, envelope), not
  human-verified, so the log isn't training data. A next step would be an append-only `feedback` record per prediction
  id, with a person's gold answer, exported in `--mine` format.
- **The log stores questions**, as `computer.log.jsonl` already does. Its size is capped at 256 MB, after which
  logging stops (it never rotates or truncates). The chain detects edits, but it is not tamper-proof against someone
  who rewrites the whole file. Promoted files are read-only by mode, which guards against accidents, not against root.
- **Feature drift:** `e:dict`/`e:isa` read facts in the year `controller.py` was imported. The manifest records the year.
- Defect 5 (the lookup answering reverse questions forward) is in the deterministic engine and is left for a separate change.
