# CDOS Distribution Architecture

*What is allowed to exist above and below the capability boundary.*

This is a contract, not a description. It says where things go and why, and
the parts of it that can be checked by a machine are checked by
`tests/test_boundary.py` — because the last set of architecture documents in
this repository drifted from the code within a week, and claimed a test suite
was passing when it was not running at all.

---

## 1. The goal, stated precisely

Not *"a Linux distribution with CDOS included"*. Not *"a new Linux"*.

> **A Linux distribution in which CDOS is the native computer interface.**

Linux stays underneath and keeps doing what it is good at: scheduling,
drivers, filesystems, the kernel. What changes is the **contract between the
human and the machine**. Today that contract is a shell and a set of command
names. Under CDOS it is a catalogue of capabilities, and the command names
become an implementation detail of one adapter.

The test of whether this has been achieved is not how the desktop looks. It
is this:

> **The computer changed. CDOS didn't.**

Move the same corpus from a desktop to a Raspberry Pi to a robot, and the
person's vocabulary keeps working while everything underneath is replaced.

---

## 2. The rule

One sentence decides where any piece of code belongs:

> **If the answer changes when you change the machine, it lives below the
> boundary. If it changes when you change your mind, it lives above it.**

`systemctl reboot` changes when you change the machine — below. That
*"start over"* means reboot changes when you change your mind — above. The
distinction is not about importance or complexity; it is about **what kind of
fact it is**.

Two corollaries, and both are enforced:

| Direction | Rule | Enforced by |
|---|---|---|
| Downward | Nothing above the boundary may name a mechanism | `test_no_module_exceeds_its_allowance` |
| Upward | Nothing below the boundary may know a word | `test_no_adapter_knows_any_vocabulary` |

The upward half is the one that is easy to lose, and it matters just as much.
An adapter that learned *"restart"* also means reboot would be a second,
hidden vocabulary — one the corpus cannot see, the semantic layer cannot
weigh, and nobody would think to look in when a phrase resolved strangely.
**Meaning belongs above. Mechanism belongs below.** An adapter that cannot
import the vocabulary cannot consult one, so that is what the test asserts.

---

## 3. The three zones

```
┌──────────────────────────────────────────────────────┐
│  ABOVE — presentations                               │
│  GUI (LCARS) · Voice · Terminal · future clients     │
│                                                      │
│  ABOVE — intent                                      │
│  corpus · vocabulary · synonyms · semantic layer     │
│  confidence · confirmation policy · ranking          │
│         knows words, knows no mechanisms             │
├══════════════════════════════════════════════════════┤
│  THE BOUNDARY — the capability catalogue             │
│  68 capabilities · 10 domains · hand-written · closed│
│         the only thing that touches both sides       │
├══════════════════════════════════════════════════════┤
│  BELOW — adapters                                    │
│  LinuxPlatform · (RobotPlatform) · (EmbeddedPlatform)│
│         knows mechanisms, knows no words             │
├──────────────────────────────────────────────────────┤
│  Linux kernel · hardware                             │
└──────────────────────────────────────────────────────┘
```

The GUI is **one presentation of the contract, not the contract itself.**
Voice, terminal and a future robot voice all enter the same world:

```
  "make the screen brighter"   (typed)
  "open my browser"            (spoken)
  "battery is getting low"     (robot, unprompted)
              │
              ▼
       one capability catalogue
              │
              ▼
        one adapter per machine
```

---

## 4. What is allowed above

* **Words.** Vocabulary, synonyms, learned aliases, the corpus.
* **Judgement.** Confidence thresholds, what counts as ambiguous, when to ask.
* **Policy.** Which capabilities need a human press. Ranking and recency.
* **Presentation.** Layout, colour, speech, the transcript.
* **Capability *names*.** `system.reboot` is not a Linux fact. It is the name
  of something this computer can be asked to do, and saying it is the whole
  point.

Forbidden above: any mechanism name, any path under `/proc` or `/sys`, any
`shell=True`, any direct process spawn.

## 5. What is allowed below

* **Mechanisms.** `systemctl`, `xrandr`, `pactl`, `nmcli`, `brightnessctl`,
  sysfs, `/proc`, IPC sockets, device files — all of it, freely.
* **Preference order.** Trying `brightnessctl`, then `light`, then sysfs is an
  adapter's business and nobody else's.
* **Honest refusal.** Saying *"this machine cannot do that"*.

Forbidden below: any synonym, any user-facing phrasing, any import from the
shell, any decision about whether the user *meant* it.

An adapter answers *how*. It never answers *whether*.

---

## 6. The boundary: the catalogue

The catalogue is the only thing that touches both sides, and therefore the
only thing that must be written by hand.

```python
_spec("system.reboot", "restart the machine", effectful=True)
```

68 capabilities across 10 domains:

| Domain | Capabilities |
|---|---|
| `system` | distro, hostname, kernel, lock, logout\*, reboot\*, shutdown\*, suspend\* |
| `filesystem` | create_directory, delete\*, exists, list, read, write\* |
| `hardware` | battery, cpu_count, cpu_load, cpu_load_per_core, cpu_model, disk, enumerate, load_average, memory, net_rate, processes, swap, temperature, uptime |
| `process` | inspect, launch, list, terminate\* |
| `audio` | get_volume, listen, set_volume, speak |
| `display` | get_brightness, get_resolution, screenshot, set_brightness, set_resolution\* |
| `network` | connect\*, disconnect\*, forget\*, interfaces, scan, status, wifi_off\*, wifi_on, wireless |
| `bluetooth` | connect, devices, disconnect, forget\*, off\*, on, scan, state |
| `user` | full_name, home, identity, sessions |
| `window` | can_place, close\*, focus, fullscreen\*, list, place\* |

`*` = effectful: always requires a deliberate human press, however confident
the reading was.

**The catalogue is closed.** A model asked to interpret an unfamiliar request
will produce a confident, plausible answer, and `system.format_disk` reads
like something that ought to exist. It does not exist, so it cannot be
invoked, whatever names a model produces. This is the architectural rule the
whole design exists to protect:

> The corpus and CDOS establish what the computer is capable of.
> A model may help interpret a request. **It may not extend the machine.**

Adding a capability is a deliberate act: an entry in `CATALOGUE`, a binding in
`_bind`, and nothing else can widen the surface.

---

## 7. Declared exceptions

Every abstraction layer needs an escape hatch, or people will make undeclared
ones. Two are declared here. Both are kept under test so they cannot quietly
grow.

### The terminal is the escape hatch

`screens/terminal.py` uses `shell=True`. That is forbidden everywhere else
and correct there: a terminal's entire job is to run a command line a person
typed and meant. CDOS is not trying to make Linux unreachable — it is trying
to make it *unnecessary*. Those are different goals, and the second one needs
a door.

`test_nothing_above_the_boundary_asks_a_shell_to_parse` permits `shell=True`
in exactly that one file and nowhere else.

### Diagnostics may name mechanisms

`diagnostics.py` exists to report which mechanisms this machine actually has.
It cannot do that without naming them. It is kept under a numeric allowance
rather than exempted outright, so it cannot quietly grow into a second
adapter.

---

## 7a. The system corpus

The catalogue says what the computer can do. It said nothing about how a
person asks for it, and the gap was larger than it looked: **31 of the then-38
capabilities had no words at all.** The machine could read its own
temperature, list its own networks and set its own brightness, and there was
no sentence that would make it. `run_capability` was reached only from tests.

`capability_corpus.py` is the missing half, and it lives above the boundary
because meaning always does — it names capabilities and never mechanisms.
The two halves are keyed to each other and checked:

* a phrasing for a capability that does not exist is a **test failure**;
* a capability with no phrasing is a **test failure**.

Add a capability and the suite tells you it cannot be spoken yet. That
guarantee is the point: a vocabulary maintained by hand *alongside* a
catalogue maintained by hand drifts silently, and the symptom is a machine
that can do something nobody can ask for — which is precisely the state this
codebase was in.

Now **68 of 68**, across 253 phrasings.

Two things belong up here that look like they might belong below:

**Relative adjustment.** *"Make it brighter"* is not a capability. It is a
reading, an arithmetic step, and a write. The sum is meaning, so it happens
above the line and the adapter is only ever handed an absolute figure. If the
reading cannot be taken, the request is **refused rather than started from an
invented value** — moving from a number nobody measured puts the screen
somewhere the person did not ask for.

**Saying the answer.** A question answered with `user.identity ok` has not
been answered. Turning a reading into a sentence is meaning too, so a robot
and a laptop report a temperature the same way.

### Precedence: most specific wins

The older vocabulary matches by containment, so its bare `home` fires inside
*"where is my home"* — a four-character reading of a sixteen-character
request. Whoever recognised more of the sentence understood more of it, so
the longer match wins; ties leave the vocabulary in front, as the older and
better-tested layer. `home` still navigates. *"Where is my home"* now answers.

---

## 8. Invariants

Five properties that must hold. Four are tested; the fifth is a review rule.

1. **The catalogue is closed.** Nothing outside it can be invoked.
2. **Failure is data, never a fabricated answer.** A capability that could not
   be performed returns `ok=False` with a reason. This is the invariant the
   platform audit found broken in four places — a reboot that silently did
   nothing reported success, and `get_resolution()` returned a hardcoded
   `1024x768` that no caller could distinguish from a reading. *Inventing a
   plausible answer is the characteristic failure of this layer.* Watch for it.
3. **Effectful capabilities always require a human press.** No confidence
   score is ever high enough. *This was broken and is now enforced.*
   `_end_session` reached the registry through `invoke`, the ungated door, so
   saying **"shut down" powered the machine off on one phrase** — a speech
   engine mishearing something close to it was enough, with no press and no
   way back. Two existing tests passed *because* they asserted that
   behaviour. The catalogue marks these capabilities effectful precisely to
   prevent this, and the marking meant nothing on the path a person actually
   speaks through. Tested now at that door, for every effectful phrase.
4. **Adapters are selected by self-report and priority, never by name.**
   Linux sits at `GENERAL = 1000` because it is the *general* answer; anything
   more specific is tried first, or a robot running Linux could never get the
   robot adapter.
5. **One path per capability.** If the shell can do something two ways, the
   second way is the one that will still be Linux-only in a year.

---

## 9. Naming: decisions

The proposed scheme was `cdos.audio.*`, `cdos.window.*`, `system.power.reboot()`.
Three decisions, with reasons:

**No `cdos.` prefix.** Every capability in CDOS is a CDOS capability; the
prefix carries no information and costs four characters in every corpus fact
and every catalogue entry. Namespacing is for when two things could collide.
If CDOS ever federates with a second capability source — a robot exposing its
own catalogue over a link — add the prefix *then*, when it distinguishes
something.

**Stay two levels: `domain.action`.** `system.power.reboot` buys grouping that
38 capabilities do not need, and `CapabilitySpec` already derives `.domain`
and `.action` from a single dot. Revisit when a domain passes roughly a dozen
actions — `filesystem` will get there first.

**Keep power inside `system`.** A separate `power` domain is defensible, since
power is the most safety-critical group, but effectfulness already marks those
capabilities and moving them is churn for no behavioural change.

---

## 10. Domains: present, next, deferred

| Domain | Status | Note |
|---|---|---|
| `system` `process` `filesystem` `hardware` `audio` `display` `network` `user` | **present** | 38 capabilities, bound and tested |
| `window` | **mounted** | Was a parallel adapter system predating the boundary — a `WindowBackend` ABC with four implementations. The backends moved to `platform/linux_windows.py` unchanged; the policy stayed above. A mount, not a rewrite. **96 → 75.** |
| `robot` | **stub landed** | `platform/robot_stub.py` drives no hardware, and has already earned its place — see below. |
| `storage` | deferred | Disks, mounts, free space. `filesystem` covers files; `hardware.enumerate` half-covers devices. Split when something needs it. |
| `input` | deferred | Keyboard, pointer, touch. Needed for kiosk and robot, not for a desktop shell. |
| `sensor` | deferred | `hardware` already carries temperature and battery. Becomes real with the robot. |
| `robot` | Phase 3 | motors, arm, gripper, navigation, camera. |

The deferred ones are deferred deliberately. A domain invented before anything
needs it gets designed against imagined requirements, and the catalogue is the
one part of this system that is expensive to change.

---

## 11. Where the code actually stands

Measured, not claimed:

* **`core.py` contains zero direct process spawns.** Launching a program,
  powering down, and asking who is logged in each have exactly one path,
  through the capability layer. This was the Phase 2 goal and it was met.
* **66 mechanism names remain above the boundary, across 9 modules.** That is
  the honest size of what is left: 96 before the `window` mount, 75 before the
  `sysinfo` fold, and now concentrated almost entirely in the voice stack.

| Module | Names | What it needs |
|---|---:|---|
| `diagnostics.py` | 14 | *declared exception* |
| `config.py` | 13 | defaults belong to the adapter |
| `voice/backends.py` | 12 | `audio.speak` |
| `voice/capture.py` | 10 | `audio.listen` |
| `voice/engine.py` | 7 | `audio.speak` |
| `audio.py` | 5 | `audio.play` |
| `openers.py` | 2 | `filesystem.open` |
| `screens/settings.py` | 2 | label text |
| `core.py` | 1 | the `xdg-open` notepad default |

One of these is worth calling out.

**`config.py` ships mechanism names as defaults.** A default like *"use
espeak"* is an opinion about this machine, and the adapter is the component
that can actually check. Defaults should come up from the adapter, not down
from a config file written before the machine was known.

### 11a. One reality per reading

`sysinfo.py` read `/proc` and `/sys` to drive the gauges while `LinuxHardware`
read the same files for the catalogue. That is not a tidiness problem. It is
**two realities**:

```
CDOS says the CPU is at 52 degrees.
The gauge beside it says 57.
```

Both are "right", both were measured, and nothing in the system can say which
to believe. Worse, the one that drifts is the one nobody is testing, because
the tests were written against the other.

The readings now come from one place. What stayed above the boundary is the
part that was never a mechanism: **rate limiting** (the gauges are drawn on a
thirty-times-a-second frame clock and the numbers change once a second —
deciding how often to ask is a display concern), **the shapes the interface
wants** (`memory()` returns a fraction alongside the bytes, because a bar
needs a fraction), and **formatting**.

The fold was bigger than the nine names suggested. Four readings were truly
duplicated — memory, temperature, battery, CPU model — and **seven more had no
capability at all**: CPU load, per-core load, load average, swap, uptime,
process count, network rate, plus kernel and distro. A gauge could show them
and no sentence could ask for them. Those are now capabilities too, which is
why the catalogue grew from 44 to 54.

The sampling moved down with them. `/proc/stat` holds monotonic totals, not
rates, so a load figure only exists as the difference between two readings —
and jiffies are a Linux detail. The adapter keeps that state now and returns
a *fraction*; a robot's controller reports load some other way and the shell
never learns the difference.

`distro()` deserves a note. It is the one place a distribution name is read,
and it sits below the boundary where it can never become a branch. CDOS never
asks *are we on Debian* in order to decide what to do — the adapter tries
mechanisms in order. It exists so the machine can answer a person who asks,
and for nothing else.

### The ratchet

`tests/test_boundary.py` records a per-module allowance. `windows.py` has
no allowance any more, which is what a finished migration looks like in this
table — and a single `swaymsg` added back to it fails the build.

`tests/test_boundary.py` records a per-module allowance. A module with no
allowance may name **nothing**; a module with one may not exceed it; an
allowance that has been beaten is reported so it can be lowered. New
violations fail immediately while existing debt comes down at whatever pace
the work allows.

The Phase 3 worklist is therefore not a paragraph in a document. It is a
number in a test, and the number only goes down.

---

## 12. What makes it a *distribution*

A distribution is a promise about what is present. Debian promises apt and
systemd; Ubuntu promises a release cadence and GNOME.

> **A CDOS distribution promises a capability catalogue.**

That is the whole value proposition, and it is what makes this a distro rather
than an application you install. On an arbitrary Linux box, CDOS must probe:
is there a backlight? a lock command? a compositor that can place windows? On
a CDOS distribution, those questions are answered at **build** time, and the
corpus can be written against capabilities that are *guaranteed* rather than
hoped for.

Concretely, three things:

1. **A capability manifest.** The image declares which capabilities it
   guarantees. `Platform.capabilities()` already reports this at runtime; the
   distribution pins it at build time and ships it. A capability in the
   manifest that fails at runtime is a **build defect**, not a user problem.
2. **A fixed adapter set.** Known at build time, so selection is deterministic
   rather than discovered.
3. **CDOS as the session.** Already supported — `Shell.is_the_session` reads
   `XDG_SESSION_DESKTOP=earth-os`.

Everything else — which kernel, which init, which packaging format — is below
the boundary and therefore **explicitly not an architectural decision**. It is
a packaging choice that can be revisited without touching a line above the
line. That is the payoff for drawing the boundary in the first place: the
question *"should this be based on Debian or Arch?"* stops being interesting.

---

## 12a. Proving it: the conformance harness

`tools/conformance.py` exercises every non-effectful capability and reports
what happened, plus **which mechanism served each area**. Run it on two
machines and compare:

```bash
./tools/multi-distro.sh debian12      # on one machine
./tools/multi-distro.sh kali          # on another
python3 tools/conformance.py --compare reports/debian12.json reports/kali.json
```

Both halves of the report matter, and this is the part that is easy to get
wrong:

> **Matching answers alone prove nothing** if both machines ran the same
> `systemctl` — that is one computer measured twice.
> **Differing mechanisms alone prove nothing** either.

So the harness refuses to call an all-identical comparison a success, and
says so in as many words. A pass looks like *N mechanisms differed; no
capability disagreed.*

Three results so far, all on this repository:

| A | B | Mechanisms differing | Capabilities disagreeing |
|---|---|---:|---:|
| Ubuntu 24.04 | Robot stub | 9 of 14 | **0** |
| Ubuntu 24.04 | Same box, no systemd on `PATH` | 2 | **0** |
| Ubuntu 24.04 | Robot with one answer corrupted | 9 | **2** — correctly caught |

The second row is the most useful for a distribution, because systemd versus
not-systemd is the largest real split between Linux distributions, and the
capability answers were **identical across all 54**.

The third row exists because a harness that always passes is worse than no
harness — it launders a claim it never checked. A deliberately corrupted
adapter is detected, named, and fails the exit code so CI can gate on it.

### The harness found a bug on its first run

`system.lock` reported **broken**. `lock()` built its fallback chain with a
`for` loop *outside* `_run_command`:

```python
for cmd in candidates:
    if shutil.which(cmd[0]):
        self._run_command(cmd)     # raises -> slock is never tried
        return
```

It moved on only when a binary was **missing**. When `loginctl` exists but
fails — a container, a session with no bus, a user without permission — the
error propagated out of the loop and `slock` was never reached, though it
would have worked. That is the same defect the platform audit found inside
`_run_command` itself, in a second place, which is a decent argument for
there being exactly one implementation of *"try these in order"*. There is
now: `_try_each`.

### And the harness had two bugs of its own

Worth recording, because both were the tool testing its own assumptions:

* It hard-coded `network.status(name="lo")` and this process's pid, then
  reported that the robot "disagreed" — because a robot has no loopback
  interface. **An argument that names a thing must come from the machine
  being measured.** Those arguments are derived now.
* It treated `none` as a disagreement. But `battery()` is declared
  `Optional`, so a machine with no battery answering `None` is the *hardware*
  differing — which is what the experiment measures. Calling that a failure
  would mean the claim could only hold between two identical machines, which
  is no claim at all.

Six of the first run's "failures" were these two mistakes. None were CDOS.

---

## 13. The claim, demonstrated

> **The computer changed. CDOS didn't.**

`platform/robot_stub.py` drives no hardware. That is the point of it: the
expensive question is not whether we can talk to a servo, it is whether a
second platform can take over from Linux at all — and a stub answers that in
an afternoon. It is enabled by `CDOS_ROBOT` in the environment, so it never
claims a desktop by accident.

Same eight sentences, two machines, one vocabulary:

| Asked | Desktop (Linux) | Robot (stub) |
|---|---|---|
| *who am i* | `root` | `operator` |
| *what is your name* | `vm` | `stub-01` |
| *how is the battery* | Nothing to report | Battery 82%, discharging |
| *what hardware* | CPU, Memory | Left drive, Right drive, Arm, Gripper, Front camera, Range finder |
| *how hot is it* | Nothing to report | 41 degrees |
| *what networks* | eth0, lo, ifb0, ifb1 | radio0 |
| *make the screen brighter* | *no backlight device* | *display is not available on this machine* |
| *shut down* | asks first → `systemctl poweroff` | asks first → `motors.stop`, `power.off` |

The last two rows carry the weight.

A robot has no screen, so the honest answer to *"make the screen brighter"*
is to say so — not to crash, and not to pretend. And powering down a robot is
not the same act as powering down a desktop: the motors stop first, or the
machine falls over on the way out. That ordering is a fact about robots and
lives in the adapter. The intent above the boundary is unchanged.

**It has already paid for itself.** The registry ignored the `priority`
argument it documented, and `LinuxPlatform.available()` returns true on *any*
Linux — including a Linux robot. A real robot adapter would have lost every
selection to the general one, and the failure would have looked like a broken
robot rather than a broken registry. A stub finds that in minutes. Hardware
finds it after weeks, in a lab, with motors attached.

## 14. What to do next

1. ~~A stub robot adapter.~~ **Done.** It caught the registry bug, as
   predicted.
2. ~~Mount `windows.py` as the `window` domain.~~ **Done.** 21 names removed,
   96 → 75, and the `window` domain is now in the catalogue and speakable.
3. ~~Fold `sysinfo.py` into `hardware`.~~ **Done.** The duplicate reader is
   gone — see §11a. 75 → 66.
4. **The multi-distribution run.** The harness is built (§12a) and has been
   exercised against the robot and against a systemd-less machine. What is
   left is running it on real Debian, Kali and Fedora boxes — one command
   each, `./tools/multi-distro.sh`.
5. **Then** the first bootable image. Not a new kernel — a Linux base that
   arrives at CDOS instead of a desktop, shipping the capability manifest
   from §12.

Steps 2 and 3 are worth doing before the image, because an image built on a
boundary with 96 holes in it will be an image that has to be rebuilt.
