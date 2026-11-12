# System Evolution Center — architecture assessment and implementation plan

*Written before any file was modified. Baseline: branch
`perf/adaptive-rendering` at `cd427e1`, suite green —
`/usr/bin/python3.12 -m unittest discover -s tests -q`: 1046 tests, OK,
22 skipped (no sway session, no cdos binaries, no audio on the build machine).*

The System Evolution Center (SEC) is where Earth OS looks at itself: what it
is doing, what is working, what needs improvement, what is missing, and what
to build next. This document says what already exists, where the SEC attaches
to it, and what the SEC will deliberately **not** build because Earth OS
already has it.

---

## 1. What Earth OS is, as found

| Asked about | What exists | Where |
|---|---|---|
| OS architecture | A Linux desktop shell in Python 3.12: one GTK 4 window, Cairo-drawn, run as a sway kiosk session. A **capability boundary** splits it: mechanism below (`earth_os/platform/`), meaning above. 68 capabilities in a closed, hand-written catalogue; 17 are effectful and always wait for a press. | `platform/capabilities.py`, `AGENTS.md` §1–2, `session/` |
| Desktop environment / rendering | Retained scene graph of LCARS widgets; ten screens in one window; adaptive frame scheduler (frames on change, interaction or animation — not on a clock); phosphor compositor with a frame cache. | `screens/`, `widgets/`, `chrome.py`, `scheduler.py`, `phosphor.py`, `docs/RENDERING.md` |
| Configuration | `~/.config/earth-os/config.toml` over in-code `DEFAULTS`; `EARTH_OS_<SECTION>_<KEY>` overrides; SETTINGS screen writes it back. | `config.py`, `screens/settings.py` |
| System monitoring | **One** sampler: `sysinfo.SystemMonitor`, a rate-limited view over the `hardware.*` capabilities, polled by the `telemetry` background job once a second. CPU, per-core, memory, swap, disk, load, uptime, temperature, battery, network rate, process count. | `sysinfo.py`, `app.py::_start_background` |
| Process management | `process.list` / `process.inspect` (readings), `process.terminate` (effectful). Analyses already rank memory and processor use by program. | `capability_corpus.py::rank_memory`, `rank_processor` |
| Storage monitoring | `hardware.disk` for one path. | catalogue |
| Logging / diagnostics | `earth-os --doctor` — 28 prerequisite checks, OK/WARN/FAIL; `~/.cache/earth-os/last-error.log` for a failed start; the `performance_report` analysis ("why is my computer slow"). **No system-log or service-state reading exists in the catalogue.** | `diagnostics.py`, `cli.py` |
| Terminal / command execution | TERMINAL screen: the one declared `shell=True` escape hatch, with an English→command translator and Earth Mind in front. | `screens/terminal.py`, `translator.py` |
| AI / LLM | Earth Mind coordinates; Companion Engine talks; models via Ollama or cloud. A model's words are never parsed for commands, and an explanation containing a figure the readings do not contain is discarded. | `mind/`, `companion/`, `docs/EARTH_MIND.md` |
| CDOS | CDOS Computer service/embedded link; GODCORE snapshot; `cdos-computer/` imported unchanged. | `voice/computer_link.py`, `voice/cdos_backend.py` |
| LCARS interface | `chrome.pieces()` is the only description of the frame; `Screen` base class; `.lng` string tables; `.lxcp` palettes. | `chrome.py`, `screens/base.py`, `theme.py` |
| Update / install / change | `install.sh`, `docs/ROLLBACK.md`; the built-in Claude Code agent whose every change is judged by a mechanical gate (suite, boundary ratchet, protected files) and ends in a git commit or a named stash. | `agent/gate.py`, `agent/policy.py`, `docs/SELF_MODIFICATION.md` |

### The rules the SEC inherits

These are enforced by the suite, not merely requested, and the SEC is built
inside them rather than beside them.

1. **The boundary.** Nothing above `platform/` may name a mechanism. The SEC
   lives above it, so it reads the machine only through capabilities.
2. **The catalogue is closed.** The SEC adds **no** capability. Where the
   directive asks for something the catalogue cannot read (failed services,
   system logs), the SEC says *not measurable here* and files it under
   Capability Development as power a person would have to grant.
3. **Failure is data, never a fabricated answer.** A reading that cannot be
   taken is shown as unavailable with its reason — never as zero.
4. **Effectful means a deliberate press.** No confidence is high enough.
5. **The GUI is one presentation.** Logic does not go in `screens/`.
6. **Human-write-only files** (`platform/capabilities.py`,
   `tests/test_boundary.py`, `tests/test_capabilities.py`, `AGENTS.md`,
   `agent/policy.py`, `agent/gate.py`, `tests/test_agent.py`) are not touched.
7. **Meaning can be learned. Mechanism can be verified. Power must be
   granted.** (`docs/SELF_MODIFICATION.md`.) The directive's safety section
   and this ladder say the same thing; the SEC's approval workflow is that
   ladder given an interface, not a second policy.

---

## 2. What the SEC will not duplicate

| Need | Reused, not rebuilt |
|---|---|
| CPU, RAM, swap, disk, temperature, load, uptime, network, process count | `shell.monitor` — the existing single sampler. The SEC starts **no** sampler, thread or service of its own for these. |
| Process list, core count, hardware list, sessions | The capability registry, through one new checked accessor (`Shell.take_reading`, §4). |
| "What is using the memory / processor" | `rank_memory`, `rank_processor` from the corpus. |
| Configuration and component checks | The doctor's `CHECKS`, run on demand on a worker. FAIL and WARN rows become findings. |
| Thresholds for busy / saturated / under pressure | The same cut points `performance_report` already uses, so the SEC and the spoken answer to *"why is my computer slow"* cannot disagree. A test feeds both the same readings. |
| Plain-language explanation by a model | `CompanionEngine.explain` via Earth Mind: one pass, every figure checked against the findings, findings said as they stand when no model is available or the check fails. |
| Making an approved code change | The built-in agent and its gate. The SEC hands over a brief on a press; it does not edit code. |
| Rendering statistics | `FrameScheduler.stats()` and `Background.ran/changed`, which were written "for the diagnostics screen" and never shown. |

The existing ENGINEERING / DIAGNOSTICS screen stays exactly as it is. It is
the place to glance at gauges; the SEC is the place to reason about them.

---

## 3. Where the SEC integrates

```
            screens/evolution.py         one more Screen in the one window
                     │  (presentation only: tabs, lists, buttons)
                     ▼
            earth_os/evolution/          the engine — above the boundary
   ┌────────────┬────────────┬─────────────┬──────────────┐
   │ inspector  │ health     │ optimizer   │ registry     │
   │ (snapshot) │ (findings) │ (advice)    │ (capability  │
   │            │            │             │  gaps)       │
   ├────────────┴────────────┴─────────────┴──────────────┤
   │ planner (roadmap, statuses, approval)  history       │
   │ changes (the one actuator)             reporter      │
   └───────────────────────┬───────────────────────────────┘
                           │ a port: the Shell
                           ▼
   shell.monitor · shell.take_reading() · shell.capabilities.supported()
   shell.config · diagnostics.CHECKS · shell.earth_mind() · shell.agent
                           │
                     ══ boundary ══
                           │
                  earth_os/platform/      (unchanged)
```

Logical components from the directive, and the module each becomes:

| Component | Module | Responsibility |
|---|---|---|
| SEC Core | `evolution/center.py` | Owns the others; sampling cadence; lazy per-panel refresh; settings. |
| System Inspector | `evolution/inspector.py` | One `Snapshot` of readings, each either a value or *unavailable + why*. |
| Health Analyzer | `evolution/health.py` | Deterministic rules → findings with severity, explanation, suggestion, evidence. |
| Performance Analyzer | `evolution/trends.py` | Ring buffer of samples; averages, peaks, growth over time. |
| Capability Registry | `evolution/registry.py` | What the machine cannot do, half does, or does experimentally — each entry **detected**, not asserted. |
| Optimization Advisor | `evolution/optimizer.py` | The optimization catalogue, six mandatory fields each, matched against live config and readings. |
| Evolution Planner | `evolution/planner.py` | Proposals, the eight statuses, legal transitions, priority, dependencies. |
| Approval Manager | `evolution/planner.py` + `evolution/changes.py` | The only code that moves a proposal past PROPOSED, and the only code that changes anything outside the SEC's own data. |
| Diagnostic Reporter | `evolution/reporter.py` | Seven reports, as text or JSON. |
| Evolution History | `evolution/store.py` | Append-only JSONL of every decision and change. |

Data lives in `~/.local/share/earth-os/evolution/` (`roadmap.json`,
`history.jsonl`, `backups/`, `reports/`), beside the other things the
computer remembers, so the 2.0 uninstaller already leaves it alone.

---

## 4. Changes to existing files (all additive)

| File | Change | Why it is needed |
|---|---|---|
| `earth_os/core.py` | `Shell.take_reading(capability)`: the check `answer_reading` already makes (in the catalogue, parameterless, not effectful, in `READINGS`), returning the value instead of a sentence; `answer_reading` now calls it. `Shell.evolution()`: lazy accessor, built on first use like `earth_mind()`. `Shell.explain_findings()`. Three action names. | The SEC needs values, not sentences. One checked path rather than a second way to the registry. |
| `earth_os/sysinfo.py` | `SystemMonitor.problem(key)`: why a reading fell back to its default, per reading. | The monitor already swallows a failure into a default; the SEC must be able to tell *0 %* from *could not be read*. |
| `earth_os/scheduler.py` | `FrameScheduler.note_draw(seconds)` and three extra fields in `stats()`. | Frame cost as a measurement, so rendering findings are not guesses. |
| `earth_os/app.py` | Time the draw; publish `shell.render_stats`; one slow `evolution` background job. No new top-level import. | Rendering performance and background-job activity belong to the window; the shell cannot otherwise see them. |
| `earth_os/config.py` | `[evolution]` defaults and commented block. | Polling intervals are configurable. |
| `earth_os/screens/__init__.py`, `screens/main.py`, `theme.py`, `.lng`, `vCommands.ini` | Register the screen; one entry in the home column; its label; three spoken phrases. | So it is reachable the way every other screen is. |
| `earth_os/cli.py` | `earth-os --evolution-report KIND [--json]`. | A robot with no screen runs the same CDOS; the reports must not need the GUI. |
| `pyproject.toml` | Package `earth_os.evolution`. | Packaging. |

Nothing below the boundary changes. `capability_corpus.py` does not change.
No capability is added. No allowance in `tests/test_boundary.py` is raised;
every new module names zero mechanisms.

---

## 5. Evolution safety

```
DISCOVERY → ANALYSIS → PROPOSAL → USER APPROVAL → IMPLEMENTATION → TESTING → VERIFICATION
 DISCOVERED            PROPOSED     APPROVED       IN DEVELOPMENT   TESTING    COMPLETED
                           └──────── REJECTED                └──────── ROLLED BACK
```

* **The engine may only discover and propose.** Creating a `DISCOVERED`
  record and promoting it to `PROPOSED` are the only transitions it can make.
  Every other transition is a method that takes the name of the person who
  pressed, and is reachable only from a button.
* **One actuator, and it is small.** `evolution/changes.py` can do exactly one
  kind of thing: set a value from a fixed, bounded list of tunables in
  `config.toml` (frame rate, sampling intervals, the phosphor effect and the
  like). It refuses unless the proposal is `APPROVED`, copies the file to
  `backups/` first, records the change and its rollback in the history, and
  can put the previous value back. It cannot install a package, run a
  command, edit code, or write any other file.
* **Everything else is a hand-off.** A proposal that needs code is carried
  out by a person, or — for rungs 1 and 2 of the self-modification ladder —
  handed to the existing Claude Code agent on a press, whose gate decides
  what is kept. A proposal that needs new machine power (rung 3) says so:
  the catalogue is human-write-only, and the SEC does not offer to hand it
  to an agent.
* **Structural, not promised.** As with `earth_os/mind/`, a test reads the
  imports: nothing under `earth_os/evolution/` may import `subprocess`, a
  platform adapter, the agent or the composition runner; file writes are
  confined to `store.py` and `changes.py`.
* **The LLM is never the source of a measurement.** It is given findings
  already fixed and may only reword them.

---

## 6. Performance budget

* No thread, no service, no second sampler. Hardware readings come from the
  monitor the shell already polls.
* **Visible:** sampling rides the existing `ticks` job through
  `Screen.tick()`, at `[evolution] poll_interval` (default 2 s). The
  expensive reading — the full process list — is taken only for the panels
  that show it, at `process_interval` (default 5 s).
* **Not visible:** `tick()` is not called for a screen that is not up. One
  background job at `background_interval` (default 60 s; 0 switches it off)
  appends a trend sample from the monitor's cached readings.
* Panels are built lazily: only the selected tab's data is computed. Doctor
  checks and model explanations run only on a press, on a worker.
* History is a fixed-size ring buffer of small tuples (default 360 samples).
* The engine is not imported until the screen is first opened or a report is
  asked for; boot pays nothing.

Actual consumption is measured in the test phase and recorded in
`docs/EVOLUTION_CENTER.md`, not asserted here.

---

## 7. Implementation plan — first functional version

1. Engine: inspector, trends, health, optimizer, registry, planner, store,
   changes, reporter, center.
2. Shell wiring (§4) and the `[evolution]` configuration block.
3. The SYSTEM EVOLUTION CENTER screen: OVERVIEW · HEALTH · OPTIMIZATION ·
   CAPABILITIES · ROADMAP · DIAGNOSTICS · HISTORY · SETTINGS.
4. Tests (`tests/test_evolution.py`): every item in the directive's §8, plus
   the structural safety checks above. Full suite before and after.
5. Measure the real process; write up results.

### Deliberately not in this version

* Automatic maintenance of any kind.
* New capabilities (service state, system logs, package management). They are
  *listed* by the Capability Registry as missing, with what granting them
  would take.
* A learned or model-driven diagnosis. The rules are deterministic.
* Changing the ENGINEERING screen, the terminal, CDOS or the LCARS chrome.

### A proposed addition to `AGENTS.md` (for a person to make)

`AGENTS.md` is human-write-only, so it is not edited here. If the SEC is
kept, a section in the style of §8 would hold future agents to its rules:

> **The Evolution Center proposes; it does not act.** Nothing under
> `earth_os/evolution/` may import what can change the machine
> (`tests/test_evolution.py` reads the imports). The engine may create and
> propose; only a press moves a proposal further. `changes.py` is the one
> actuator: a bounded list of `config.toml` tunables, backed up first. Do
> not widen that list to be thorough, and do not give the center a runner.
