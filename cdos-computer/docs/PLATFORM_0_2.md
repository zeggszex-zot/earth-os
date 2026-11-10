# CDOS Neural Platform 0.2: design note

This is the first milestone of the "self-growing CDOS" plan (section 7 of that document). It extends the Neural Core
0.1 tree without breaking it. All 423 earlier tests pass unchanged, and 36 acceptance tests are added (459 in all, 4
skipped because they need the production corpus).

**Built:**
- a shared, versioned interface for neural components
- a Growth Engine that finds what CDOS can't answer and prepares checked candidates
- capability plugins
- a simulated robot behind a deterministic safety envelope
- a learned robot policy on the same model lifecycle as the reasoning selector
- a local API and systemd services, with code, configuration and state kept apart

**Not built (later phases):** the LCARS desktop, the Linux distribution image, real robot hardware, vision or
language models, and Jetson acceleration. The robotics here is **simulation only**. No hardware driver exists in this
tree.

## 1. Acceptance criteria

| # | criterion | how | evidence (`tests/test_platform.py` unless noted) | status |
|---|---|---|---|---|
| 1 | existing tests still pass | additive changes; every edit to an existing file is listed in §8 | `python3 -m unittest discover -s tests`: 459 run, OK, 4 skipped (production corpus) | **met** |
| 2 | runs with no neural model | every neural, plugin and robotics part is optional and loads behind a never-raising loader | `TestNoModel` (no registry, an empty one, the platform with nothing optional) | **met** |
| 3 | a shared interface with versioned models and defined inputs and outputs | `computer/neural/components.py`: `IOSpec` (typed fields, units, ranges, choices), `ComponentSpec` (name, semver, kind, model id/hash), `ComponentRegistry` (validates both ways, major-version contracts, failures isolated); the selector, robot policies and plugins all sit behind it; `registry.py` runs the same lifecycle for any component | `TestInterface` | **met** |
| 4 | the Growth Engine identifies an unanswered question and creates a training task | `computer/growth/`: reads the experience log and the turn log, re-asks each failure, classifies it (`selector`, `missing_relation`, `corpus_gap`, `conflict`), ranks candidate answers, writes task files and a hash-chained journal | `TestGrowthDiscovers` | **met** |
| 5 | a training task produces a staged model without modifying the active model or corpus | `Growth.run`: a person's answers (append-only, named) become training examples, `train_to_staging` + `evaluate`, never `promote` | `TestGrowthRuns` (registry, `ACTIVE.json` and corpus byte-identical before and after) | **met** |
| 6 | evaluation detects regressions and rejects a deliberately bad candidate | per-question regression lists at the navigator and whole-computer level; 12 gates; promotion refuses a failed gate or a report made without the runtime's checks | `TestBadCandidate` (rejected with and without an active model; regressions listed; never promoted) | **met** (see §4 for what "bad" took) |
| 7 | a new capability is registered without changing the reasoning engine | `plugins_dir`: a folder with `plugin.json` + code, checked like a built-in capability (determinism scan before import, import review, self-test examples); example: a TMP117 thermometer | `TestPlugins` (engine source hashes unchanged; 12 kinds of bad plugin refused while the computer still starts) | **met** |
| 8 | a simulated robot submits state, receives a candidate action, and the action is rejected when it violates a deterministic constraint | `computer/robotics/`: observations → state → policy (component) → `Envelope.check` (13 rules, along the predicted path, fail closed) → STOP unless allowed; journaled; the sim robot is a separate process using the API | `TestRobot`, `TestApi`; `python3 -m computer.robotics simrobot` | **met** |
| 9 | a clean Linux installation starts CDOS services and keeps corpus, configuration and model registry across reboot | `tools/install_services.sh` (code, config and state apart); `cdos-api`, `cdos-growth` (+timer), `cdos-robot-sim` units, sandboxed | `TestInstallAndRestart`: install into empty folders, `systemd-analyze verify` every unit, start, use, **SIGKILL**, restart, compare | **partly verified**: unit files verified and a hard kill and restart survived. **No real reboot under systemd**: this sandbox has no systemd as PID 1 |

## 2. Architecture

```
 LCARS / voice / tools ──► cdos-api.service  (Unix socket; AF_UNIX only, no IP, no devices)
                             Platform ─ Computer (rules, corpus, navigator, envelopes)       unchanged engine
                                     ├─ components: reasoning.selector, robot.policy[_expert], plugins
                                     └─ RobotController ─ Envelope (computer/data/robot_safety.json)
 cdos-robot-sim.service ──► observations / action requests ──► approved command or STOP
 cdos-growth.timer ──► cdos-growth.service: discover → tasks → (person answers) → train → evaluate → staging
 operator ──► python3 -m computer.neural|computer.robotics promote ... --approve   (the only way anything activates)

 /opt/cdos-computer/releases/<id>, current ->   code (replaceable)
 /etc/cdos-computer/config.toml, plugins/       configuration (written once; plugins are admin-only code)
 /var/lib/cdos/{data, models/neural, log, state, robotics}   corpus, registries, growth, experience, journals
```

Interfaces in 0.2:

| component | input | output | kind |
|---|---|---|---|
| `reasoning.selector` | `reasoning.question@1.0.0` | `reasoning.plan_scores@1.0.0` | neural |
| `robot.policy` | `robot.state@1.0.0` | `robot.action@1.0.0` | neural |
| `robot.policy_expert` | `robot.state@1.0.0` | `robot.action@1.0.0` | deterministic |
| `robot.policy_naive` | `robot.state@1.0.0` | `robot.action@1.0.0` | heuristic (baseline, not deployed) |
| `sensor.thermometer` (plugin) | `sensor.raw_register@1.0.0` | `sensor.temperature@1.0.0` | deterministic |

## 3. Growth Engine

Discover, Learn, Experiment, Evaluate, Grow. The first four run unattended (`growth auto`, daily); promotion stays
manual (`growth_approval = "manual"` is the only policy implemented, and anything else is refused).

- **What it learns from:** a person's answer to a question the computer couldn't answer. Each answer is checked
  against the corpus first: which plan shapes reach it? It is kept in `answers.jsonl` with the person's name.
- **What it doesn't learn from:** the computer's own answers (no independent check), or anything as a fact. Corpus
  gaps and missing relations are reported for whoever builds the corpus, and nothing in `computer/growth` writes `data/`.
- **Visible, not silent:** a run says which answers were used and which weren't, and why. An answer about a held-out
  subject is not trained on, because it would leak into the held-out test. A run where nothing usable remains is
  refused with the reasons.

Measured on the test corpus: two unanswered questions were found and classified `selector`. One answer
("which things orbit jupiter" → Ganymede) was usable; the other named SUN, a held-out subject, and was set aside. The
resulting candidate passed all 12 gates, beat the active model (net 135 vs 131), and answered both questions,
including the one it was not trained on. The active model was unchanged.

```sh
python3 -m computer.growth discover                    # findings + tasks (paths from config.toml)
python3 -m computer.growth show <task>                 # questions, candidate answers ranked by coverage
python3 -m computer.growth answer <task> --question "which things orbit jupiter" --gold ganymede --by "Your Name"
python3 -m computer.growth run <task>                  # staged + evaluated; prints the promote command
python3 -m computer.neural promote --model <id> --approve
```

## 4. What building this found (and what changed because of it)

1. **`accept()` lets true-but-irrelevant answers through.** A bad candidate got "Spain is a country." past it for "what
   is the population of the capital of spain", and "The opposite of happy is unhappy." for "explain happy to me".
   Discovery found the same gap ("Whale is a marine mammal." for "what does a whale eat"). The **neural path** now has
   two more deterministic checks (`runtime.unaddressed`, `lex_family_unasked`). Every content word must be addressed
   by the relations the answer used, and a dictionary relation must have been asked for. The legacy controller is
   unchanged.
   - Cost to the good model: navigator-level net 135 → 131; whole computer 203/29/24 → 202/30/24.
   - Effect on bad models: their regressions go to zero.
2. **That made bad models harmless but promotable,** so a gate was added: **`beats_majority`**. Held-out top-1 must
   beat always picking the most common training label (18.3% here).
   - Every bad candidate tried falls below it: shuffled labels 13.9%, always-hop 15.0%, negated weights 0.6%. The
     real model scores 65.6%.
   - All three bad candidates are rejected with and without an active model.
   - Regression detection is still exercised: evaluated without the extra checks (`strict=False`, analysis only; such
     a report can't promote), the shuffled model loses 4 navigator-level and 7 whole-computer answers, and each is
     listed.
3. **The safety file didn't match the world.** Its workspace (±5 m) was larger than the simulated room (±4.5 m), and
   the evaluation caught an untrained policy leaving the room in 6 of 40 rooms. The workspace is now ±4.4 m. An
   envelope enforces only the limits it is given, so its file needs the same review as code.
4. **The existing one-shot lookup says wrong things** that a learned plan sometimes pre-empts: "what orbits jupiter
   please" gets "Jupiter orbits Sun.", and "owl food" gets the dictionary's definition of food. This is the
   deterministic engine, so it is left as found (see also defect 5 in `docs/NEURAL_CORE.md`).

## 5. Robotics (simulation)

The envelope (`computer/data/robot_safety.json`) has 13 rules:
- speed, turn rate, acceleration and duration
- the workspace and obstacle clearance, checked along the whole predicted path
- motor temperature and battery voltage
- state age and confidence, complete state
- the emergency stop
- the action being well formed

Missing or old data is a violation. STOP is always allowed. A rejected candidate becomes STOP, and the next policy in
line is asked (learned, then expert). Every decision, with every candidate tried, goes to a hash-chained journal.
After a restart the controller trusts nothing it saw before: its first request is refused (`state.complete`).

Held-out rooms (40, seeds 10000+):

| policy | rooms reached | collisions / out of bounds | candidates rejected |
|---|---|---|---|
| naive (no limits) | 0 | 0 / 0 | 100% (it never moves) |
| learned, untrained weights | 0 | 0 / 0 | 96% |
| expert | 30 | 0 / 0 | 0% |
| **learned** (imitates the expert; 2,631 samples, under 2 s on CPU) | 22 | 0 / 0 | 81% (mostly re-proposals while stuck) |
| **deployed: learned, then expert, envelope over both** | **31** | 0 / 0 | — (the expert stepped in for 140 of 2,291 commands) |

**Hardware boundary:** the envelope is a higher-level check, not real-time safety. For a physical robot, the plan's
advice stands: time-critical motor control and the emergency stop belong in a microcontroller that enforces its own
limits whatever CDOS sends. That needs its own safety design before any actuator is connected.

## 6. Services, storage, persistence

```sh
sudo tools/install_services.sh [--robot-sim]   # user cdos; /opt, /etc, /var/lib layout; enables cdos-api + growth timer
python3 -m computer.services.api --config /etc/cdos-computer/config.toml       # what cdos-api.service runs
python3 -m computer.robotics simrobot --config /etc/cdos-computer/config.toml  # the simulated robot
python3 -m computer --check                                                     # plugins, components, neural, ...
```

- **Units:** sandboxed (`ProtectSystem=strict`, `ReadWritePaths=/var/lib/cdos`, `PrivateDevices`, empty capability
  set).
- **No network:** `cdos-api` and `cdos-growth` are limited to `RestrictAddressFamilies=AF_UNIX` and
  `IPAddressDeny=any`. Growth runs at low priority (`Nice=15`, `CPUQuota=50%`, idle I/O).
- **Upgrades:** installing a new release only moves `current`. Configuration and state are never overwritten, and the
  old release stays for rollback.
- **Restart test:** the restart test kills the API with SIGKILL and starts it again from the installed code. Corpus,
  configuration and both activation records come back byte-identical, the same selector and robot policy load, growth
  tasks are retained, and the experience log, growth journal and robot journal all verify and continue.

## 7. Commands

| | |
|---|---|
| reasoning selector | `python3 -m computer.neural train / evaluate / promote --approve / status` (as in 0.1) |
| robot policy | `python3 -m computer.robotics train-policy / evaluate-policy / promote-policy --approve / status` |
| growth | `python3 -m computer.growth discover / tasks / show / answer / run / auto` |
| safety file | `python3 -m computer.robotics envelope [file]` |
| tests | `python3 -m unittest tests.test_platform -v` (about 35 s); the full suite about 90 s |

## 8. Changes to existing files

| file | change | why |
|---|---|---|
| `computer/core.py` | plugins loaded before the capability registry (a refused plugin never stops start-up); `self.components`; the selector registered as a component; turn log records the envelope's `status` and `certainty` | criteria 3, 4, 7 (discovery needs to know what wasn't answered) |
| `computer/capabilities.py` | `default_registry(cfg, extra)`; 8 new declarations (growth, robot.*, api.local); `WRITERS` += `growth.engine`, `robot.journal`; `planner.neural` covers `components.py` | every new part is declared and checked at start-up like the old ones |
| `computer/config.py`, `config.example.toml` | `plugins_dir`, `growth_*`, `robotics`, `robot_safety_file`, `robotics_journal`, `api_socket`, `api_tcp_port` (all off or empty by default) | |
| `computer/neural/registry.py` | `component_root()`; `promote`/`rollback`/`status` take a component's `load`; a `strict=False` report can't promote | one lifecycle for every component |
| `computer/neural/runtime.py` | the neural path's two extra answer checks (§4.1) | |
| `computer/neural/evaluate.py` | `beats_majority` gate; `strict` option (analysis only) | §4.2 |
| `computer/neural/train.py` | reports which `--mine` questions were used or dropped and why; `require_mine`; records file closed on refusal | Growth runs say what happened |
| `computer/neural/experience.py` | `ChainLog` (format name as a parameter), `read_chain` | the same chain for growth and robot journals |
| `computer/__main__.py` | `--check` lists plugins and components | |

**New:** `computer/neural/components.py`, `computer/growth/` (`__init__`, `__main__`, `engine`), `computer/plugins.py`,
`computer/robotics/` (`__init__`, `__main__`, `state`, `world`, `safety`, `policy`, `controller`, `sim`, `learn`),
`computer/services/` (`__init__`, `platform`, `api`, `client`), `computer/data/robot_safety.json`,
`plugins/tmp117_temperature/`, `systemd/cdos-{api,growth,robot-sim}.service`, `systemd/cdos-growth.timer`,
`tools/install_services.sh`, `tests/test_platform.py`, `docs/PLATFORM_0_2.md`.

## 9. Limits and next steps

- **Scale is unproven.** Everything was measured on the 59-subject test corpus, on a cloud Xeon. Neither the
  production corpus nor the NUC was available. Run `train`/`evaluate` and the Growth Engine on `data/` with questions
  you wrote before promoting anything.
- **Reboot:** criterion 9 is verified at the process level (hard kill and restart) and by `systemd-analyze verify`,
  not by booting a fresh install. Do that once on the NUC: `sudo tools/install_services.sh`, reboot,
  `curl --unix-socket /run/cdos/api.sock http://x/v1/health`.
- **The held-out guard costs growth answers.** On the real corpus about 20% of subjects are held out, so about one
  answer in five will be set aside. That keeps the evaluation honest; making it cheaper needs a second evaluation set
  built from people's answers.
- **The learned robot policy imitates;** it doesn't plan. It reaches fewer rooms than the expert and gets stuck where
  the expert does. Worth trying next: DAgger-style data collection, and goals from the corpus ("go to the charger").
- **Plugins are trusted code.** The checks catch accidents, not malice, so `plugins_dir` must be writable only by an
  administrator. The installer puts it under `/etc` for that reason.
- **Next milestone suggestions:**
  - a feedback endpoint so LCARS can collect answers
  - per-component evaluation suites in the Growth Engine (the robot policy as a growth target)
  - vision as a component (camera → `robot.observation`)
  - an LCARS console reading `/v1/health` and the robot journal
