# System Evolution Center

*First functional version, then audited (Phase 2, §10). The plan it was
built to is [`SEC_ARCHITECTURE.md`](SEC_ARCHITECTURE.md), written before any
file was touched; this is what was built, how to use it, and what testing
found. §5 is the first version's results; §10 is the audit's, with every
before-and-after measurement.*

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

Once a proposal is under way its detail shows two things, kept apart:

* **Expected — an ESTIMATE, not a measurement**: what the optimization said it
  would do, and what that estimate rests on.
* **MEASURED before → after**: Earth OS's own processor time, frames per
  second, time to draw one frame and memory, in the five minutes before the
  change against the time since it settled — with the whole machine's load as
  labelled context. Each figure is called a MEASURED DIFFERENCE, WITHIN THE
  VARIATION MEASURED, or TOO FEW SAMPLES, and a figure that was already moving
  before the change is flagged rather than credited to it. Live while IN
  DEVELOPMENT or TESTING; fixed when VERIFIED. How it is worked out: §10.4.

The result of the last press stays in its section as **Last action — DONE,
REFUSED or FAILED**: REFUSED when a rule said no, FAILED when the machine did,
and either way what was left as it was.

**DIAGNOSTICS** — the seven reports, built on screen and exportable as text or
JSON into the center's `reports/` folder.

**HISTORY** — every decision and every applied change, newest first. The file
is append-only.

**SETTINGS** — how often it samples, and whether a model may reword findings.
All four take effect on the press — the background timer is replaced, not
left running at its old rate. Each row says **IN EFFECT NOW** and whether the
value is **SAVED** in `config.toml`; SAVE on the SETTINGS screen keeps them.

## 3. What it may change, and what it may never do

Once a proposal that carries a setting change is APPROVED, pressing *APPLY
CHANGE* copies `config.toml` into `backups/`, changes that one line, puts the
new value into effect in the running Earth OS, and records what changed, from
what, on whose press, whether it is in effect now, and how to put it back.
*ROLL BACK* restores it — the whole file if nobody has edited it since, and
otherwise only that one key, so nobody else's edit is undone — and puts the
old value back into effect.

Every setting below takes effect at once except `[companion] keep_alive`,
which is read when the model client is built and needs a restart. "At once"
is a claim the suite checks in a real window for each of the twelve (§10.2).
The record says what actually happened: with no window running (a report from
a terminal) the change is made and recorded, and says it is not yet in effect.

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
                               # (applied at once: the running timer is replaced)
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
  (155.5–160.5 MB against 147.4), and nothing before that. *Corrected in the
  audit (§10.3): most of that is a one-time cost paid by whichever screen is
  drawn second — PROGRAMS pays it too. The center's own share was about
  4.5 MB, 3.5 of it OpenSSL, and is now about 1 MB.*
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
  *Correction:* the first version of this document said Claude Code was not
  installed here. A `claude` command is on this machine's PATH, and in the
  audit one scripted press of HAND TO CLAUDE reached the real agent (§10.6).
  The agent's own gate was not exercised end to end.
* **Trends last one session.** The history of samples is in memory, so a slow
  leak that takes days cannot be seen yet. It is listed under CAPABILITIES.
  For the same reason a before-and-after comparison across a restart has only
  the samples taken since the restart for its *after* — which is correct, but
  needs Earth OS left running for a few minutes before VERIFIED.
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

---

## 10. Phase 2 — audit and resource optimization

The first version was audited in place: every file under
`earth_os/evolution/`, the screen, the configuration and scheduler wiring, the
tests and this document. Nothing was rebuilt or replaced. The list of settings
the center may change is the same thirteen. CDOS, the LCARS frame and
everything below the platform boundary are untouched.

Every figure below was measured on the same machine as §5, against the first
version as committed (`d55a793`) — measured again at the start of the audit,
not copied from §5.

### 10.1 What the audit found

| # | Found | Kind | Fixed by |
|---|---|---|---|
| 1 | `background_interval` was read once, when the window opened. Choosing NEVER left the job running; choosing a value after NEVER started none. The screen said changes applied "now". | defect | §10.2 |
| 2 | Seven more of the thirteen settings were also read only at start, and all but `keep_alive` were marked restart-required — including `phosphor`, which is read on every frame. | inconsistency | §10.2 |
| 3 | The optimizer kept its own hand-written copy of every restart flag, apart from the actuator's table. | duplication | §10.2 |
| 4 | *Before* and *after* were each "the last two minutes", so an *after* taken soon after the change held *before* samples. The frame cost was the session's running mean, diluted by every frame before the change. Nothing compared the two. | defect | §10.4 |
| 5 | Importing the engine cost 3.9–4.1 MB, nearly all of it OpenSSL, loaded by `hashlib` for a digest needed only on APPLY and ROLL BACK. | memory | §10.3 |
| 6 | Showing HISTORY read the whole append-only file — at 50,000 lines, 18 MB of transient memory and 35 ms on each visit. | memory, grows forever | §10.3 |
| 7 | After a visit, about 270 KB was kept that nothing used while hidden. | memory | §10.3 |
| 8 | A failed APPLY or export showed a raw OS error (`[Errno 17] File exists`); the agent declining over unverified changes was reported as "it is busy"; every result vanished after four seconds. | usability | §10.5 |
| 9 | Two exports of the same report within one second overwrote each other. | defect | §10.5 |
| 10 | §5.4 attributed 8–13 MB to the center; most of it is not the center's. §7 said Claude Code was not installed here; it is. | documentation | §10.3, §7 |

### 10.2 Settings take effect when they say they do

The window now has one entry point, `EarthWindow.apply_setting`, that puts a
changed setting into effect using paths that already existed for it: the
scheduler's own `reconfigure()` for the frame clock (it cancels the old timer
before arming the new), the cache flag, and a background-job setup that can
be run again and replaces only a job whose interval changed. `Shell.apply_setting`
is what the center's SETTINGS buttons and its actuator call; voice
`continuous` is set on the live engine.

| Setting | Before | Now |
|---|---|---|
| `shell.fps`, `idle_fps`, `adaptive` | read at start; marked restart | at once |
| `shell.frame_cache` | read at start; marked restart | at once |
| `shell.phosphor` | read every frame; *marked restart* | at once (and marked so) |
| `shell.telemetry_interval`, `tick_interval` | read at start; marked restart | at once |
| `windows.poll_interval` | partly — the gate was live, the timer was not | at once |
| `voice.continuous` | a button could; the actuator could not | at once, if voice is on |
| `companion.keep_alive` | restart | restart — read when the model client is built |
| `evolution.poll_interval`, `process_interval`, `explain` | at once | at once |
| `evolution.background_interval` | read at start; *the screen said "now"* | at once; 0 removes the job |

Each change record now holds `in_effect_now` from what happened rather than
from the table, and the screen reports that: "In effect now", "Restart Earth
OS for it to take effect", or — with no window — "Recorded, not yet in
effect". The SETTINGS rows say IN EFFECT NOW and SAVED or NOT SAVED.

**Tested in a real window:** each of the twelve no-restart settings is
changed through the actuator and the running value read back from the
window, the scheduler or the center; a rollback is read back the same way;
NEVER removes the timer and 30 s re-arms it at 30 s without touching the
other jobs; a timer that fires just after NEVER takes no sample.

### 10.3 Memory: each change measured first

| What | Before | After |
|---|---:|---:|
| Importing the engine (RSS, 3 runs each) | +3.9 to +4.1 MB | +0.4 MB |
| Process with the center on screen, by section | 157.1–161.4 MB | 153.4–157.6 MB |
| Process with the center hidden | 157.1 MB | 153.6 MB |
| HISTORY, last 200 of 3,000 lines | 3.6 ms, 1,086 KB peak | 2.7 ms, 218 KB peak |
| HISTORY, last 200 of 50,000 lines | 34.6 ms, 18,110 KB peak | 2.8 ms, 218 KB peak |
| HISTORY held while on screen | 184 KB | 50 KB |
| Held after leaving the center | ~270 KB | ~11 KB |

* **OpenSSL.** `hashlib` is imported where the digest is taken. The suite
  checks, in a fresh interpreter, that importing the engine does not load it.
* **History.** Only the end of the file is read, backwards in blocks; the
  result is identical to reading it all, torn last line included. The screen
  keeps the 200 lines it shows, not 200 decoded records it re-formatted on
  every frame.
* **Hidden.** Leaving releases the history, the report text, the last process
  list (69 KB) and the derived caches. A hidden sample keeps Earth OS's memory
  figure for the leak trend and not the list it came from. Opening the center
  takes a full sample first, so nothing shown is stale.

**The 8–13 MB in §5.4, corrected.** Stepping through a process: whichever
screen is drawn second costs about 8 MB the first time — PROGRAMS as much as
the center (8.0 MB vs 8.2 MB, either order). It is the toolkit and
allocator's first-use cost, and the home-screen row in the benchmark never
pays it. The center's own share is the engine import and about 0.5 MB of
drawing.

**Looked at, measured, and left alone:**

| Idea | Measured | Why left |
|---|---|---|
| Fewer distinct text sizes (the center adds 18) | 8.1 MB vs 8.2 MB drawing all sections | no difference |
| Release the phosphor scratch pool when phosphor is turned off | 3.1 MB pool; RSS moved 0.0 MB | no difference |
| Pack trend samples into tuples | 329 KB → 203 KB at a full buffer | under 0.1% of the process, and a wide change |
| The process benchmark's per-sample costs (e.g. 3.94 → 6.08 ms) | 60 timed samples per kind, 3 runs, both trees: medians 3.7–4.1 ms on each | noise in the benchmark, not a regression |
| Draw cost per section | 3 runs, both trees: the same section varies 2–4 ms between runs on either | no consistent difference |

Processor cost is unchanged within the noise: 0.35–1.50% of a core across
the sections now, 0.40–1.45% before; the home screen with the center hidden
2.75% against 2.65%.

### 10.4 Before and after an approved optimization

`earth_os/evolution/compare.py` is arithmetic on samples the center already
takes; it reads nothing and costs nothing until somebody looks.

* **Windows that cannot overlap.** *Before* is the five minutes up to the
  change. *After* starts 5 s after it. The change time is the APPLY press for
  a setting, or the start of TESTING for work done by hand.
* **Earth OS's own cost, from running totals.** Each sample now carries
  Earth OS's processor seconds (`time.process_time()` — this process's own
  clock, checked against `/proc/self/stat` in the suite), frames drawn and
  milliseconds spent drawing them. A window's figure is the change in the
  total over the change in time.
* **A difference must beat the noise.** Each figure is also worked out
  interval by interval. A difference is called MEASURED only when it exceeds
  twice the standard error of the two windows; otherwise it is WITHIN THE
  VARIATION MEASURED, or TOO FEW SAMPLES under four intervals a side.
* **A difference is not a cause.** A figure that was already moving the same
  way, by a comparable amount, within the *before* window is flagged *already
  moving before the change*. The first real-application trial needed this: it
  called Earth OS warming up after start a +5 MB measured effect.
* **Like for like.** If the center was on screen for very different shares of
  the two windows, the comparison says so: looking at it costs something.
* **The estimate stays separate**, stored on the proposal as `expected` and
  `basis` and shown as *Expected — an ESTIMATE, not a measurement*.

**Checked against an independent measurement.** `tools/evolution_bench.py
--before-after phosphor-off` runs the real application on the home screen,
lets start-up settle for 20 s, then carries the optimization through the
center's own approval line and measures the same two windows from outside
through `/proc`. 90 s each side, center hidden:

| Figure | The center reports | `/proc`, same windows |
|---|---|---|
| Earth OS processor time | 2.78% → 1.99% of a core, −28% — MEASURED | 2.72% → 1.97% |
| Time to draw one frame | 7.33 → 4.68 ms, −36% — MEASURED | not visible from outside |
| Frames drawn | 1.24 → 1.33 /s — WITHIN THE VARIATION | — |
| Earth OS memory | 153.6 → 157.4 MB — MEASURED | 153.8 → 157.4 MB |
| Whole machine, memory | 6.43% → 6.37% — flagged *already moving* | — |

The rollback that followed restored `config.toml` byte for byte and took
effect at once.

The memory rise after switching phosphor off is real by both measurements,
with a flat *before* window. It was not reproduced headless (switching off,
then dropping the effect's 3.1 MB scratch pool, moved RSS by 0.0 MB), so its
cause is on the toolkit side and was not identified. It is reported as
measured, which is what the comparison is for: the estimate for this
optimization spoke of processor time, and the measurement found a memory
cost the estimate did not mention.

### 10.5 Controls and failures

Every section, every row's detail and every roadmap button was driven by a
script, including the failure paths: a configuration that cannot be written,
a reports folder blocked by a file, a proposal applied before approval, and
an agent that declines.

| Case | Before | Now |
|---|---|---|
| APPLY, config cannot be written | `cannot write /…: [Errno 2] No such file or directory: '/…'` | *Not applied: Could not write the configuration: the folder does not exist (/…). config.toml and the running setting are unchanged; P-0002 is still APPROVED.* |
| EXPORT, a file in the way | `Unable to export: [Errno 17] File exists: '/…/reports'` | *Not saved: Something that is not a folder is in the way (/…/reports). Nothing was written.* |
| Agent declines, unverified changes in its workspace | *The agent did not take the request: it is busy.* | the agent's own words, then *P-0003 is still APPROVED; nothing was handed over.* |
| Any result | a toast for four seconds | also kept in the section as *Last action — DONE / REFUSED / FAILED* for five minutes |
| ROLL BACK | *P-0001: ROLLED BACK* | what was restored, whether the whole file or only the key, and whether it is in effect |
| Two exports in one second | the second overwrote the first | two files |

Checked by the same script: approve, reject, postpone, resume, priority up
and down, apply, testing, verified, roll back and reconsider each move the
proposal as they should; each optimization's state and current value match
the configuration; every expected benefit is marked ESTIMATE; backups are
taken before every change and the rolled-back file matches the original.

### 10.6 One thing that happened while testing

The first version of the button sweep ran with the real built-in agent in
place, and pressed HAND TO CLAUDE. A `claude` command exists on this machine,
so the agent accepted the brief and wrote its settings into the test
machine's own home folder. The script ended before a session could run;
nothing in the repository changed, no agent process remained, and the files
were removed. The sweep now stubs the agent and isolates its home folder, as
the suite already did. It is recorded here because it is what the button is
for: a press does reach the agent.

### 10.7 Results

```
/usr/bin/python3.12 -m unittest discover -s tests -q
Ran 1217 tests — OK (skipped=31)            no display
xvfb-run -a /usr/bin/python3.12 -m unittest discover -s tests
Ran 1217 tests — OK (skipped=3)             with a display; the 3 need cdos binaries
```

1188 tests before the audit; the 29 new ones are all in
`tests/test_evolution.py` (now 171), and none of the earlier ones were weakened.
One was changed: it asserted a verified proposal records exactly `before` and
`after`, and it now also records `changed_at` and the `comparison`.

`tools/bootcheck.py`, three runs on each tree: one window throughout, the
frame before the shell, the shell arriving at 1010, 1015 and 1126 ms before
and 1071, 1059 and 1097 ms after. `earth-os --evolution-report` still prints
every kind, as text and JSON, and writes nothing.

**Not tested in the audit:** a real language model, a real thermal sensor or
battery, a GPU or a sway session (as §7), and the agent's gate end to end. The
before-and-after comparison was checked against `/proc` for one optimization,
`phosphor-off`; the others use the same arithmetic.
