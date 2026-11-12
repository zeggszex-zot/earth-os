# The rendering engine

EARTH OS drew thirty frames a second whether or not anything had changed.
This document is what that cost, how it was measured, what replaced it, and
how to put it back if the replacement ever gets something wrong.

Every figure here was measured on the machine the work was done on, at
1920x1080, with the real process under a nested X server. Nothing in the
tables is an estimate; where a number *is* arithmetic on a measurement, it
says so.

---

## 1. The diagnosis

The frame clock was a plain repeating timer that ended in an unconditional
invalidation:

```python
self._frame_source = GLib.timeout_add(int(1000 / fps), self._on_frame)
...
self.area.queue_draw()        # every tick, unconditionally
```

Everything else ran off that same tick: screen ticks, window polling, GODCORE
refreshes, screenshot notices, and the frame rectangle the shell publishes for
program placement.

So the question was not "what is slow" but "how much of this work produced a
different picture". `tools/damage_audit.py` answers it by rendering a run of
frames at the configured rate, hashing each one and comparing it to its
predecessor:

| Screen | Frames identical to the one before | Grid cells that ever changed | CPU spent on identical frames |
|---|---:|---:|---:|
| PROGRAM INDEX | **89 of 89 (100.0%)** | 0 of 48 | 27.8% of a core |
| ENGINEERING | 86 of 89 (96.6%) | 5 of 48 | 37.3% of a core |
| TERMINAL | 84 of 89 (94.4%) | 1 of 48 | 26.7% of a core |
| HOME | 82 of 89 (92.1%) | 10 of 48 | 33.5% of a core |

*(1280x800, 30 fps, three seconds a screen.)*

That is the whole bug. The PROGRAM INDEX is a static list of buttons; it was
redrawn ninety times in three seconds and produced the same picture ninety
times, at a cost of a third of a CPU core. The terminal changed exactly once
every fifteen frames, in one cell of a 48-cell grid: the block caret, blinking
at 2 Hz.

The content on these screens changes **at most twice a second**. It was being
drawn thirty times a second.

Two things the investigation ruled *out*, since both were suspected:

* **System monitoring was not the problem.** `sysinfo.SystemMonitor` already
  caches for a second, and `screen.tick()` measures at 0.000–0.002 ms. The
  readings were cheap; *redrawing the whole screen because they existed* was
  not.
* **Phosphor was not the whole problem either.** It is about 40% of the cost
  of a frame — real, and addressed in §4 — but turning it off still left
  28.0% of a core on the home screen, because the other 60% of thirty
  unnecessary frames is still unnecessary.

### Where a frame goes

`tools/render_bench.py`, mean milliseconds over 30 frames at 1920x1080:

| Screen | background | screen draw | glow | frame + overlay | **total, tube on** | total, tube off |
|---|---:|---:|---:|---:|---:|---:|
| HOME | 0.34 | 4.54 | 6.83 | 1.63 | **14.26** | 4.67 |
| ENGINEERING | 0.38 | 6.08 | 6.88 | 1.67 | **15.68** | 5.86 |
| TERMINAL | 0.35 | 1.90 | 6.87 | 1.73 | **13.71** | 2.81 |
| PROGRAM INDEX | 0.36 | 2.44 | 6.34 | 1.60 | **12.18** | 2.35 |

14.26 ms a frame times 30 frames is 42.8% of a core before GTK is paid at all,
which is the right order for the 47.6% the process actually used.

### Where the glow goes

`tools/glow_bench.py` breaks the 7.3 ms down, because "the glow is expensive"
is not actionable and "the upscale is 54% of the glow" is:

| Stage | ms |
|---|---:|
| halve 1920x1080 → 960x540 | 1.026 |
| halve → 480x270 | 0.228 |
| halve → 240x135 | 0.056 |
| halve → 120x67 | 0.015 |
| additive mix of the two passes at 480x270 | 1.401 |
| **stretch 480x270 → 1920x1080** | **3.944** |
| surface allocations (4 a frame) | 0.065 |
| **whole glow** | **7.330** |
| *for comparison: blitting a kept full-size frame* | *0.636* |

The last line is the one that matters. A finished frame can be put back on
screen for 0.64 ms, against 14.26 ms to compose it again — twenty-two times
cheaper.

---

## 2. What replaced it

Frames are no longer produced on a clock. They are produced because something
asked for one, and three things ask.

**A change.** `FrameScheduler.request_frame()`. This is what `Shell.redraw()`
has always meant; it now reaches a scheduler instead of an unconditional
`queue_draw`. Safe from any thread, because workers call it.

**Interaction.** `FrameScheduler.note_interaction()`. While a finger is down
or a pointer is moving, the full configured rate runs, and keeps running for
`interaction_grace` seconds after the last event so the release and its flash
are smooth. Holding the rate is deliberately *not* the same as holding the
work: in the grace window with nothing changing, the timer stays alive so the
next press is served within one period, and nothing is drawn.

**Animation.** Every screen is asked how long until its picture changes by
itself:

```python
def next_frame_in(self) -> Optional[float]:
    """Seconds until this screen's picture changes by itself, or None."""
```

A terminal caret answers with the time to its next flip, a screen with a
running clock answers with what is left of this second, and a static screen
answers `None`. `None` from the screen on display is what lets the scheduler
take its timer down and leave the process asleep in `poll` until something
wakes it.

The base class answers for the two things any screen can have moving — the
shell's toast, which expires, and an element mid-flash after a press — so a
subclass takes the minimum of its own answer and `super().next_frame_in()`
rather than replacing it.

Three states fall out of that rather than being coded as a state machine:

| State | Meaning |
|---|---|
| `active` | the render timer at `[shell] fps`, because input or animation needs it |
| `idle` | no render timer; one armed wake-up for the next known deadline |
| `asleep` | no timers at all: the screen is static and untouched |

### Telemetry is sampled, not drawn

The live gauges were the reason the old engine looked justified. They are
still live, and they no longer cost frames:

```python
def readings() -> bool:
    before = shell.telemetry_signature()
    shell.monitor.poll()
    if shell.telemetry_signature() == before:
        return False                      # sampled; nothing to draw
    return bool(screen is not None and screen.live)
```

Sampling runs on its own one-second timer. A frame follows only when a
reading that a screen actually shows has moved. `Shell.telemetry_signature()`
is deliberately *finer* than what is displayed — the gauges quantise to
twentieths and the numbers to whole percents — so it occasionally asks for a
frame that draws identical pixels. That costs one frame; the opposite
rounding error would be a gauge that sticks.

### Background work is independent of the frame rate

Window polling, GODCORE, screenshot notices and telemetry each have their own
interval now. On the old engine all four were carried by the frame clock, so a
machine set to 10 fps polled for windows a third as eagerly as one set to 30 —
which was never a frame rate decision.

| Job | Interval | Asks for a frame when |
|---|---|---|
| `windows` | `[windows] poll_interval` (1.2 s) | the running-program list changed |
| `godcore` | ≥ 1 s, throttled to 6 s inside | never directly; the worker asks when its read lands |
| `screenshots` | 1 s | a screenshot appeared that the shell did not take |
| `telemetry` | `[shell] telemetry_interval` (1 s) | a displayed reading moved |
| `ticks` | `[shell] tick_interval` (0.1 s) | the screen found news and asked itself |

A background job that throws keeps its timer, because the alternative is a
shell that quietly stops noticing new windows.

---

## 3. Two paths that had to be fixed with it

Removing the clock exposed two places that changed what was on screen without
ever asking for a frame. Under the old engine they worked by accident: a
redraw was coming in 33 ms regardless.

**`Screen.invalidate()` now asks for a frame.** Invalidating *means* the
picture is out of date, so the two always belonged together. It matters most
for the call sites inside `draw_overlay`, which notice a changed option list
part-way through a frame whose geometry was already built: that frame shows
the old layout and the rebuilt one appears on the next. With nothing else
asking, a confirmation strip would have been a frame behind and stayed there.
The conditions guarding those sites go false once `build` records what it
drew, so this settles after one extra frame rather than looping.

**`Shell.status_message` is now a property that asks for a frame.** Forty
places set it, several from worker threads finishing a launch or a capability.
A redraw at each of them is forty chances to forget one, and a forgotten one
is an error message that never appears. Setting the same text twice costs
nothing.

`terminal.tick()` and `claude.tick()` also ask for a frame when they drain
output, which they previously left to the clock.

---

## 4. The tube

The requirement was that the CRT look survive exactly. It does:
`tools/phosphor_compare.py` renders every screen through both versions and
compares them pixel for pixel.

```
screen          pixels differing   worst channel
------------------------------------------------
main                           0               0
engineering                    0               0
terminal                       0               0
programs                       0               0
tasks                          0               0
settings                       0               0
recent                         0               0
------------------------------------------------
identical
```

Getting that comparison to mean anything took work of its own. Run naively it
reported 25,075 differing pixels on ENGINEERING — from the live gauges, the
clock showing seconds, the caret blinking off the wall clock, and the
diagnostics tab printing the config's path, which was a fresh temporary
directory each run. The comparator pins all four. A test that cannot tell
noise from a regression would have hidden the regression.

Two changes, both measured:

**The blur pyramid is pooled.** Four surfaces of up to 2 MB were allocated and
discarded every frame — 120 allocations a second at 30 fps. They are now
reused, overwritten with `OPERATOR_SOURCE` so what lands in them is what a
fresh transparent surface would have held. The mix surface sits in its own
pool slot, because at the default passes it is the same size as the finest
pyramid level and must not be the same surface. Worth 0.065 ms a frame
directly, and the allocation churn is gone.

**The finished frame is kept.** `phosphor.FrameCache` holds the last
composited frame, so a repaint GTK asks for on its own — the window being
shown, uncovered, or moved between monitors — is a 0.64 ms blit instead of a
second trip through the glow.

The hard part of a cache is knowing the picture is the same, and `FrameCache`
does not try to work it out. The caller passes a token standing for "the
content that would be drawn", and passes `None` when it cannot promise that.
The window passes `None` whenever anything is animating, because then the
drawing depends on `ctx.now` as well as on the model and a token standing only
for the model would hand back a stale frame:

```python
token = None
if self._cache_enabled and self._animation_delay() is None:
    token = (self.frames.generation, id(screen), tube)
```

`generation` is bumped by every request for a frame, so a change can never be
served from the cache. The surface is allocated at device resolution with its
own device scale, so a HiDPI window is not quietly cached at half sharpness.

**What this did not do:** the cost of a frame that genuinely changed is
essentially unchanged — 13.63 ms against 14.26 ms on the home screen, about
4%, much of it within noise. That is the honest result. The glow's dominant
stage is a full-screen bilinear upscale, and there is no way to make it
cheaper in software Cairo without changing how the halo falls off, which is
the look. The saving is not in making a frame cheaper; it is in not drawing
frames that would have been identical.

---

## 5. Results

### The A–F matrix, measured on the real process

1920x1080, `tools/cpu_bench.py --matrix`, each configuration a fresh process
allowed to settle for 5 s and then sampled for 5 s. Percent of one core.

**Before**

| Test | FPS | Phosphor | HOME | ENGINEERING | PROGRAM INDEX |
|---|---:|---|---:|---:|---:|
| A | 30 | on | 47.6 | 50.2 | 34.6 |
| B | 20 | on | 33.2 | 36.6 | 27.2 |
| C | 10 | on | 16.0 | 19.6 | 13.2 |
| D | 30 | off | 28.0 | 32.0 | 22.4 |
| E | 20 | off | 18.8 | 22.4 | 14.6 |
| F | 10 | off | 9.6 | 11.8 | 8.4 |

**After**

| Test | FPS | Phosphor | HOME | ENGINEERING | PROGRAM INDEX |
|---|---:|---|---:|---:|---:|
| A | 30 | on | **2.6** | **3.0** | **0.4** |
| B | 20 | on | 2.2 | 3.0 | 0.4 |
| C | 10 | on | 3.6 | 2.0 | 0.4 |
| D | 30 | off | 1.8 | 2.0 | 0.4 |
| E | 20 | off | 1.2 | 2.2 | 0.4 |
| F | 10 | off | 1.6 | 1.8 | 0.4 |

The shape of the "after" table is the result worth noticing. CPU is now
almost independent of the configured frame rate, because the frame rate is a
ceiling on responsiveness rather than a bill paid every second. The remaining
spread — including HOME reading 3.6% at 10 fps and 2.6% at 30 — is noise at
±1–2%, which is the resolution of a per-second sampler at these magnitudes.
It should not be read as 10 fps costing more than 30.

### Under a finger

Idle CPU is the easy number to improve and the easy one to improve
dishonestly: a shell that is cheap at rest and sluggish under a hand has been
slowed down, not optimised. So the pointer is swept across the window at
about 50 moves a second — faster than a hand — for the whole sample:

| Screen | Before | After |
|---|---:|---:|
| HOME | 53.0% | **21.6%** |
| PROGRAM INDEX | 36.2% | **3.7%** |

The home screen under continuous interaction now costs less than half what it
cost sitting still. Every one of those frames is real work: the pointer
crossing buttons changes their hover state, and that is a change, so it is
drawn.

### Against the targets

| Metric | Goal | Measured |
|---|---|---|
| Smooth 30 fps when needed | yes | full rate while animating or interacting |
| Idle CPU | below 5–10% of a core | 0.4% static, 2.6–3.0% with live gauges |
| Interactive CPU | as low as practical | 3.7% static, 21.6% on the busiest screen |
| RAM | fewer allocations | 149.7 MB against 157.7 MB; 120 surface allocations a second removed |
| Phosphor | appearance preserved | pixel-identical on all 7 screens |
| Responsiveness | immediate | drawn on the event, not up to a period later |
| Compatibility | preserved | 1027 tests pass; `adaptive = false` restores the old engine |

### Stability

Sixty seconds on the home screen, sampled every second: CPU flat at a median
of 2.0% with no drift (first ten seconds 2,1,2,2,2,2,3,3,1,2; last ten
2,2,2,3,2,2,4,5,1,2), RSS 149.7 MB and not climbing. No runaway timer, no
duplicate callback, no idle loop.

---

## 6. Configuration

All under `[shell]`, all backward-compatible. An existing `config.toml` that
sets only `fps` and `phosphor` keeps working and gets the new engine.

```toml
[shell]
fps                = 30     # the rate when something is actually moving
adaptive           = true   # draw on change, not on a clock; false = old engine
idle_fps           = 0      # floor under the idle rate; 0 = do not redraw at rest
interaction_grace  = 0.45   # seconds of full rate after the last input
telemetry_interval = 1.0    # how often the gauges are sampled, in seconds
tick_interval      = 0.1    # how often screens notice their own news
frame_cache        = true   # reuse the composited frame when nothing changed
phosphor           = true   # unchanged
```

`fps` has changed meaning in a way worth stating plainly: it used to be the
rate the machine always drew at, and it is now the maximum rate it will draw
at when there is a reason. There is no longer a reason to lower it to save
CPU, and lowering it now costs responsiveness for almost no saving.

**`idle_fps` is the escape hatch.** If something on a screen ever updates
without asking for a frame, it will look frozen. Setting `idle_fps = 2`
guarantees a couple of frames a second at rest — enough to unstick anything —
at about a tenth of the old cost. `adaptive = false` is the full way back.

---

## 7. Installing and rolling back

The work was done in the Git workspace, not in `/usr/lib/earth-os`, and the
installed copy was never overwritten with an untested build.

```bash
# from the workspace
make test                       # 1027 tests
python3 tools/phosphor_compare.py --save  /tmp/ref     # on the old code
python3 tools/phosphor_compare.py --check /tmp/ref     # on the new
python3 tools/cpu_bench.py --matrix --screens main,programs
sudo ./install.sh
```

Rolling back, in increasing order of severity:

```bash
# 1. keep the new code, restore the old behaviour — no reinstall, no restart
#    beyond the shell itself
printf '[shell]\nadaptive = false\n' >> ~/.config/earth-os/config.toml

# 2. keep adaptive rendering, but guarantee frames at rest
printf '[shell]\nidle_fps = 2\n' >> ~/.config/earth-os/config.toml

# 3. turn off only the kept-frame cache
printf '[shell]\nframe_cache = false\n' >> ~/.config/earth-os/config.toml

# 4. go back to the code as it was
git checkout perf-baseline     # the tag taken before any change
sudo ./install.sh
```

The `perf-baseline` tag is a commit of the working tree exactly as it was
received, including the uncommitted modifications that were in it. See
`docs/ROLLBACK.md` for the installed-copy side of this.

---

## 8. What is still true

* **A changed frame still costs what it cost.** 13.6 ms at 1920x1080 on the
  home screen with the tube on. If a future screen does animate continuously,
  it will cost roughly what the old engine cost, because that is the price of
  the look. The engine's job is to make sure nothing pays it needlessly.
* **The glow is spatially non-local**, so partial-region redrawing is not
  available without visible seams. Everything here invalidates whole frames.
* **A screen that lies in `next_frame_in()` will appear frozen.** That is the
  one failure mode the architecture introduces, which is why it is a single
  method with a default that covers toasts and flashes, why every screen's
  answer is asserted in `tests/test_rendering.py`, and why `idle_fps` exists.
* **These figures are from one machine under Xvfb with the Cairo GSK
  renderer.** The absolute numbers will differ on other hardware and under a
  real compositor; the ratios should not.
