# System Evolution Center

*First functional version. The plan it was built to is
[`SEC_ARCHITECTURE.md`](SEC_ARCHITECTURE.md), written before any file was
touched; this is what was built, how to use it, and what testing found.*

The System Evolution Center is where Earth OS looks at itself. It answers
five questions, each from readings rather than from guesses:

| Question | Where |
|---|---|
| What is Earth OS currently doing? | OVERVIEW |
| What is working well? | HEALTH — the findings that read *normal* |
| What needs improvement? | HEALTH, OPTIMIZATION |
| What capabilities are missing? | CAPABILITIES |
| What should we develop next? | ROADMAP |

**It proposes; it does not act.** It reads the machine and writes proposals.
Nothing changes until a person approves a proposal, and then the only thing
the center can change itself is one setting from a fixed list of thirteen in
`config.toml`, backed up first.

---

## 1. Opening it

| From | How |
|---|---|
| The home screen | **EVOLUTION** in the side column, under DIAGNOSTICS |
| By voice | *"system evolution center"*, *"evolution center"*, *"system evolution"* |
| A terminal, no window | `earth-os --evolution-report KIND [--json] [--checks]` |
| Straight into it | `earth-os --screen evolution` |

The bare word *evolution* is deliberately not a command: it would fire inside
ordinary conversation, the way the bare *home* does.

`KIND` is one of `health`, `cpu`, `ram`, `applications`, `hardware`,
`optimization`, `progress`. The command prints the report and exits; it opens
no window, writes no file, and waits about a second first, because processor
load is the difference between two readings. `--checks` runs the `--doctor`
checks first so their results are in the report.

## 2. The eight sections

**OVERVIEW** — processor, memory, storage, swap and temperature as meters;
findings counted by severity; and the facts beside them: operating system,
Earth OS version, kernel, uptime, available RAM, running processes, Earth OS's
own background jobs, what drawing a frame costs, and what the center itself
has cost this session.

**HEALTH** — every finding, most severe first, each with what was measured,
why it matters and what to do. Severities are INFORMATIONAL, LOW, MODERATE,
HIGH and CRITICAL. *RUN CHECKS* runs the `--doctor` checks; *EXPLAIN* asks a
model to put the findings into plain words (§6).

**OPTIMIZATION** — changes that could make Earth OS cheaper. Each answers the
same six questions: what it changes, why it could help, the expected benefit,
the disadvantages, whether a restart is needed, and how to reverse it. An
expected benefit is always marked `ESTIMATE:` and says what it is based on —
arithmetic, or a figure measured on another machine, labelled as such.
Entries are RECOMMENDED (a reading or a setting calls for it), AVAILABLE, or
IN EFFECT.

**CAPABILITIES** — what Earth OS cannot do, half does, or does experimentally:
MISSING, INCOMPLETE, NEEDS IMPROVEMENT, EXPERIMENTAL, FUTURE. Each says how it
is known (`detected live`, `source review`, or `policy`), what developing it
involves, what it requires, and **who may build it** — the rungs of
[`SELF_MODIFICATION.md`](SELF_MODIFICATION.md). New machine power is rung 3:
only a person may grant it, and the center never offers it to an agent.

**ROADMAP** — proposals, with priority, difficulty, dependencies, testing
requirements and a completion history. The statuses and who may move them:

```
DISCOVERED → PROPOSED → APPROVED → IN DEVELOPMENT → TESTING → COMPLETED
└─ the engine ─┘        └───────────── a person, by name ─────────────┘
```

REJECTED can follow any of the first three, ROLLED BACK any of the last three,
and both can be reconsidered. The engine may notice something and write it up;
every move from approval onward — and every rejection — takes a person's name.

*DISCOVER* puts what the center has noticed on the roadmap as DISCOVERED.
Approve, reject, postpone, resume and re-prioritise are buttons, and each is
recorded with the operator's name.

**DIAGNOSTICS** — the seven reports, built on screen and exportable as text or
JSON into the center's `reports/` folder.

**HISTORY** — every decision and every applied change, newest first. The file
is append-only.

**SETTINGS** — how often it samples, and whether a model may reword findings.
These apply for the session; SAVE on the SETTINGS screen writes them.

## 3. What it may change, and what it may never do

Once a proposal that carries a setting change is APPROVED, pressing *APPLY
CHANGE* copies `config.toml` into `backups/`, changes that one line, and
records what changed, from what, on whose press, and how to put it back.
*ROLL BACK* restores it — the whole file if nobody has edited it since, and
otherwise only that one key, so nobody else's edit is undone.

The settings are these and no others (`earth_os/evolution/changes.py`):

```
[shell]     adaptive  frame_cache  phosphor  fps  idle_fps
            telemetry_interval  tick_interval
[windows]   poll_interval
[voice]     continuous
[companion] keep_alive
[evolution] poll_interval  process_interval  background_interval
```

Each is a boolean, a bounded number or a choice. None can name a program or a
path.

It cannot install a package, run a command, edit source code, delete
anything, change CDOS or the LCARS frame, or approve its own proposals. Those
are not promises; the suite reads the source to check them (§5.7).

Work that needs code is a person's. For rungs 1 and 2 an APPROVED proposal
also offers *HAND TO CLAUDE*, which passes the brief to the built-in agent on
that press; the agent's own gate decides what is kept.

Records live in `~/.local/share/earth-os/evolution/`: `roadmap.json`,
`history.jsonl`, `backups/`, `reports/`. The 2.0 uninstaller leaves that
directory alone.

## 4. Configuration

```toml
[evolution]
enabled             = true
poll_interval       = 2.0      # seconds between samples while it is on screen
process_interval    = 5.0      # seconds between reads of the full process list
background_interval = 60.0     # one trend sample this often while hidden; 0 = none
history             = 360      # samples kept in memory for trends
explain             = true     # a model may reword findings; it is never their source
```

`enabled = false` removes it: the screen says it is switched off and the
engine is never built.

---

## 5. Test results

**Machine.** A 2-core virtual machine (Intel Xeon @ 2.10 GHz), 7.8 GB RAM,
Ubuntu 24.04.5, kernel 6.18, Python 3.12.3, GTK 4.14. No GPU: the real
application was run under `Xvfb` at 1920×1080 with GTK drawing in software.
No thermal sensor, no battery, no swap and no language model on it — which is
itself part of what was tested (§5.10), and part of what was not (§7).

### 5.1 The suite

```
/usr/bin/python3.12 -m unittest discover -s tests -q
Ran 1188 tests — OK (skipped=27)            no display

xvfb-run -a /usr/bin/python3.12 -m unittest discover -s tests
Ran 1188 tests — OK (skipped=3)             with a display
```

Before this work the suite was 1046 tests, OK, 22 skipped. The 142 new tests
are `tests/test_evolution.py`. With no display, 27 skip: the earlier 22 plus
the five that drive a real window. With a display the only skips are the three
that need the cdos binaries, which are not built on this machine.

**Existing features continue working:** all 1046 earlier tests pass unedited.
No file under `earth_os/platform/` changed, `capability_corpus.py` did not
change, no capability was added and no allowance in `tests/test_boundary.py`
was raised. The human-write-only files are untouched.

### 5.2 The application launches

The real application was started under `Xvfb` with `--screen evolution` and
its window captured: the center drew, with live rendering figures and six
background jobs listed. `tools/bootcheck.py` — the boot contract — passed
three runs in three before the change and three in three after: one window
throughout, the frame up before the shell.

| | Before | After |
|---|---|---|
| Shell arrived, warm runs | 975 ms, 961 ms | 1003 ms, 973 ms |
| Importing the screen at boot | — | 1.4–1.7 ms of 81–93 ms for all screens |
| Engine modules imported at boot | — | none |

The difference between the two rows of arrival times is inside the spread of
either. The engine is first imported when the center is first opened or a
report is asked for.

### 5.3 The metrics are accurate

Each figure is compared with a second, independent reading of the kernel
(`TestTheMetricsAreAccurate`). All pass.

| Reading | Compared with | Tolerance |
|---|---|---|
| Memory total | `MemTotal` | exact |
| Memory used | `MemTotal − MemAvailable` | 5% of total (time passes between the two reads) |
| Storage total / free | `shutil.disk_usage("/")` | exact / 1% |
| Swap total | `SwapTotal` | exact |
| Processor load | the shell monitor's own figure | identical — it *is* that figure |
| Cores usable / listed | `sched_getaffinity` / `cpu_count` | exact |
| Uptime | `/proc/uptime` | 10 s |
| Process count, process list | entries in `/proc` | ±15 |
| Earth OS's own memory | `VmRSS` of the process | 10% |
| Kernel, version | `uname`, `earth_os.__version__` | exact |

Its verdicts are also checked against the machine's own: the same readings
are given to `performance_report` (*"why is my computer slow"*) and to the
Health Analyzer, and they must not disagree.

### 5.4 What it costs

`tools/evolution_bench.py` (`make evolutionbench`): the real process, phosphor
on, 30 fps ceiling, 8 s to settle, then sampled from outside through `/proc`
for 20 s. CPU is percent of one core.

| Where | CPU % | RSS MB | Frames | Samples | ms each |
|---|---:|---:|---:|---:|---:|
| main — center never opened | 2.55 | 147.4 | 24 | – | – |
| engineering | 2.60 | 152.2 | 22 | – | – |
| evolution · overview | 1.60 | 160.5 | 10 | 10 | 2.96 |
| evolution · health | 0.55 | 156.6 | 0 | 10 | 4.48 |
| evolution · optimization | 0.45 | 156.5 | 0 | 10 | 2.97 |
| evolution · capabilities | 0.65 | 155.8 | 0 | 10 | 5.24 |
| evolution · roadmap | 0.40 | 155.5 | 0 | 10 | 2.87 |
| evolution · diagnostics | 0.50 | 159.4 | 0 | 10 | 4.45 |
| evolution · history | 0.45 | 155.5 | 0 | 10 | 2.87 |
| evolution · settings | 0.40 | 155.5 | 0 | 10 | 3.23 |
| main — center hidden, sampling every 5 s | 3.00 | 155.6 | 26 | 4 | 9.01 |

What that says:

* **On screen it costs less than the home screen does.** The home screen
  redraws its clock every second; the center draws when a sample changed what
  is shown. Seven of its eight sections drew no frame at all in twenty seconds
  while still sampling every two.
* **A sample costs 3–5 ms.** About 3 ms without the process list (2.9–3.2),
  4.5–5.2 ms with it, and the list is only read for the three sections that
  show it.
* **Memory: 8–13 MB** over the home screen once the engine has been loaded
  (155.5–160.5 MB against 147.4), and nothing before that.
* **Hidden, at its fastest setting**, the home screen measured 3.00, 2.95 and
  2.60% across three runs against 2.55, 2.65 and 2.45% with the center never
  opened — about 0.3 of a percentage point, at twelve times the default rate.
  By the center's own count that was four samples of 8–9 ms in twenty seconds.
  At the default of one sample a minute the cost was not separately measured;
  it is one twelfth as many samples.

**Soak.** 150 s on one section, 75 samples each:

| Section | CPU % | RSS growth over 150 s | ms each |
|---|---:|---:|---:|
| overview | 1.55 | +0.12 MB | 2.39 |
| health | 0.55 | +0.21 MB | 4.24 |

The trend buffer is still filling at that point (75 of 360 samples), so some
growth is expected; a soak past twelve minutes, when the buffer is full, was
not run.

It starts no thread, service or sampler of its own: hardware readings are the
shell's existing monitor, checked by `test_no_second_sampler_is_started`. In a
real window, at rest, the overview drew between two and five frames across
three sample intervals, a section with nothing moving drew at most one frame
in five seconds, and leaving the center stopped its sampling
(`TestInARealWindow`).

### 5.5 Diagnostic reports

All seven build as text and as JSON, on a working machine and on one where
every reading fails. From a terminal, in an empty home directory:

```
$ time earth-os --evolution-report health        → the report, exit 0, 1.3 s
$ find ~ -type f                                 → nothing
```

1.1 s of that is the deliberate wait between two processor readings. An
unknown kind exits 2 and lists the seven.

### 5.6 Proposals and approval, end to end

The calls the buttons make, on a real `config.toml`:

```
 1. start                       [shell] fps = 30; files: config.toml
 2. sample, analyze, discover   12 items DISCOVERED; config unchanged
 3. PROPOSE frame-rate          P-0013 PROPOSED; would set fps = 20; config unchanged
 4. APPLY before approval       refused: nothing is implemented without approval
 5. APPROVE with no name        refused: no decision is recorded without a name
 6. APPROVE by operator         APPROVED; config unchanged
 7. APPLY CHANGE                IN DEVELOPMENT; fps = 20; previous 30; backup taken
 8. the backup                  byte-identical to the original file
 9. TO TESTING, VERIFIED        COMPLETED
10. ROLL BACK                   ROLLED BACK; fps = 30
```

The history afterwards held nine lines for that proposal: noticed (engine),
proposed, approved, change applied, setting changed, testing, verified, change
rolled back, setting restored — every line after the first by *operator*.

### 5.7 Nothing changes without approval

* Sampling, analysing, advising, surveying, discovering, building every
  report, running the checks, explaining and the background job — twenty
  times over — leave `config.toml` the same bytes and the live configuration
  the same values, and create only the center's own `roadmap.json` and
  `history.jsonl`. Every proposal is still DISCOVERED.
* A proposal in any status but APPROVED cannot be applied; approval alone
  changes nothing.
* Read from the source: nothing under `earth_os/evolution/` imports
  `subprocess`, a socket, a platform adapter, the agent, the shell or the
  composition runner; only `store.py` and `changes.py` write a file, and
  `changes.py` writes only the configuration file; no engine module calls a
  decision; a decision without a person's name is refused.
* `Shell.take_reading` refuses `system.shutdown`, `process.terminate`,
  `filesystem.delete` and anything else that is not a parameterless,
  non-effectful reading.
* Opening the center in the real application wrote nothing at all.

### 5.8 With a model, and without one

Without: every section works; *EXPLAIN* returns the findings as they stand and
says so. With Earth Mind and the Companion both switched off: the same. With
the real Earth Mind and no model running: the same.

With: exercised against a stand-in for the Companion's reply, in three cases —
wording that checked is used and marked `model`; wording containing a figure
the findings lack is discarded and the findings shown; a model that raises
costs the explanation and nothing else. In none of them does a finding, an
optimization or a report change. **No real language model was run** (§7).

### 5.9 Missing readings

A shell whose every hardware reading fails produces a snapshot in which each
is *unavailable* with its reason — never zero. No rule concludes anything from
a reading it does not have, no finding says the machine is fine, and nothing
rises above LOW except the missing adapter itself. Reports list what was
unavailable. A reading that recovers stops being reported as a problem.

On this machine three readings are really absent — temperature, battery and
rendering statistics when headless — and each appears as unavailable with its
reason.

### 5.10 Found while testing, and changed

Three things came from looking at the rendered screens and the transcript
rather than from a failing test. Each has a test now.

* **Component checks crowded out the machine.** A dozen optional components
  can be unavailable at once, all LOW; listed alphabetically they stood in
  front of *storage is filling up*. Findings of equal severity are now listed
  machine first, component checks last.
* **Something already in effect could be proposed**, producing a proposal
  with nothing to carry out and a "how to reverse" for a change nobody made.
  The engine now declines and the screen does not offer it.
* **The record of an applied change did not say who pressed.** The decision
  carried a name; the change it led to did not. Both do now.

Also: the checks include one that builds a window to see whether one can be
built. Run from the center's worker thread inside a running toolkit that
would be unsafe, and unnecessary — so when a window is already publishing its
statistics, that row is answered from that fact.

---

## 6. What a model may do here

Reword. The findings are fixed before a model sees them; it is given the
sentences and may return other sentences. The Companion checks every figure in
the reply against the findings and discards a reply that adds one. Nothing a
model says is parsed for a command, becomes a finding, or reaches a report.

## 7. Not tested, and known limits

* **No real language model.** None is installed here and none could be. The
  model path was tested against a stand-in only.
* **No real thermal sensor, battery or swap.** Those rules were exercised
  with constructed readings, not live ones.
* **No GPU and no sway session.** Costs were measured with software rendering
  under Xvfb; a real display will differ, in either direction.
* **Handing work to the built-in agent** was tested against a stand-in agent.
  Claude Code is not installed on this machine.
* **Trends last one session.** The history of samples is in memory, so a slow
  leak that takes days cannot be seen yet. It is listed under CAPABILITIES.
* **Not measurable at all, and said so on screen:** the state of system
  services, system logs, processor time per program, and storage beyond the
  system volume. The catalogue has no reading for them. They are listed under
  CAPABILITIES as power a person would have to grant.
* **`make conformance` was not run**: nothing below the boundary changed.

## 8. Where the build differs from the plan

* `SEC_ARCHITECTURE.md` §4 expected three action names; there is one,
  `evolution`, reached by three spoken phrases.
* It expected three extra fields in `FrameScheduler.stats()`; there are four
  (`draws_timed`, `draw_ms_mean`, `draw_ms_peak`, `draw_ms_last`).
* Two shell methods were not in the plan: `doctor_rows()` and
  `hand_to_agent()`. The engine may not import the diagnostics module or the
  agent, so the shell offers each as a port.
* `tools/evolution_bench.py` is new.

`AGENTS.md` is human-write-only and was not edited. The section proposed for
it is at the end of `SEC_ARCHITECTURE.md`.

## 9. What to build next

In the order the directive's own strategy suggests — small, and each one
measurable before the next:

1. Keep trend samples between sessions (FUTURE on the capability list), so
   leaks and slow drift become visible.
2. A read-only service-state reading in the catalogue — a person's act — which
   turns *"system services cannot be checked"* into findings.
3. Processor time per program in `process.list`, a rung-2 change the agent's
   gate can verify.
4. Run the center against a real model and a real display, and record those
   measurements here beside these.
