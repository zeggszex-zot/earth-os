# CDOS — rules for anything editing this repository

Read this before changing anything. It applies to every assistant, agent and
person working here.

Most of what follows is **checked by the test suite**, not merely requested.
That distinction is the point: a rules file is advice, and advice drifts. The
delivery note in this repository once claimed "36/36 tests passing" for a
suite that was not running at all. So where a rule can be machine-checked, it
is, and this file tells you which test does it.

## Before you start

```bash
/usr/bin/python3.12 -m unittest discover -s tests -q      # 559 tests, all pass
```

**Use Python 3.12 or newer.** PyGObject's compiled extension is built for
3.12, so under 3.11 the GTK-touching modules fail to import and four test
files silently do not run — including the ones covering the whole platform
integration. A green run on the wrong interpreter is worse than a red one.

If the suite is not green *before* your change, stop and say so. Do not start
work on a broken tree and inherit the blame for it.

---

## 1. The boundary rule

This is the architecture. Everything else is detail.

> **If the answer changes when you change the machine, it lives below the
> boundary. If it changes when you change your mind, it lives above it.**

`systemctl reboot` changes when you change the machine — below, in
`earth_os/platform/`. That *"start over"* means reboot changes when you
change your mind — above, in the corpus.

Both directions are enforced by `tests/test_boundary.py`:

| Direction | Rule |
|---|---|
| Downward | Nothing above the boundary may name a mechanism |
| Upward | No adapter may know a word — it cannot import the vocabulary |

The upward half is the one people lose. An adapter that learned *"restart"*
also means reboot would be a second, hidden vocabulary the corpus cannot see.
**Meaning belongs above. Mechanism belongs below.**

### The ratchet only goes down

There are 66 mechanism names above the boundary, recorded per module in
`tests/test_boundary.py`. A module with no allowance may name **nothing**; a
module with one may not exceed it. **Never raise an allowance.** If your
change needs to, you are putting a mechanism in the wrong place.

Two modules have already reached zero — `windows.py` and `sysinfo.py` — and
dropped out of the table entirely. That is what finishing looks like.

---

## 2. The catalogue is closed

`earth_os/platform/capabilities.py` lists the 68 things this computer can
be asked to do. Nothing outside it can be invoked, whatever name is produced.

> The corpus and CDOS establish what the computer is capable of.
> A model may help interpret a request. **It may not extend the machine.**

`system.format_disk` reads like something that ought to exist. It does not.
Adding a capability is a deliberate act: an entry in `CATALOGUE`, a binding in
`_bind`, **and phrasings in `capability_corpus.py`** — the suite fails until a
capability can be asked for in words, because a capability nobody can ask for
is a thing the machine does that nobody wanted.

**Do not add capabilities to be thorough.** 68 is not a score. Each one is a
promise the distribution has to keep on every machine it ships to.

### Compositions are a new name, not new power

`earth_os/compositions.py` lets a sequence of capabilities be given a name —
*"movie mode"*, *"status report"*. That is not a way around the catalogue, and
five structural rules keep it that way (the full argument is in
`docs/SELF_MODIFICATION.md`; all five are checked by
`tests/test_compositions.py`):

1. every step names a capability in `CATALOGUE`, checked at build time;
2. **effectfulness inherits** — one effectful step makes the whole thing
   effectful, so "tidy up" cannot contain `system.shutdown` and skip the press;
3. compositions are flat — a step never names another composition;
4. arguments are literals, never resolved at run time;
5. `compositions.py` holds no registry and has no `run` method, so
   `Shell.run_composition` is the only way to execute one — and it is the one
   that confirms.

Rule 5 is not theoretical. The effectful gate in this repository was bypassed
once already by a caller reaching for `registry.invoke` instead of the gated
path, and "shut down" powered the machine off with nothing to press. **Do not
add a convenience runner.**

---

## 3. Failure is data, never a fabricated answer

This is the defect this codebase keeps producing, so it is worth naming
precisely. Six real examples, all found and fixed:

* `systemctl reboot` failing on a machine with no systemd was reported as
  **success** — the shell said "Acknowledged" to a reboot that never happened.
* `get_resolution()` returned a hardcoded `1024x768` on failure, and the
  `xrandr` call above it passed a flag xrandr rejects, so **every machine got
  the placeholder**.
* `audio.listen()` returned `None` with a comment calling itself a
  placeholder; a conformance run duly recorded speech recognition as working
  on a machine where it is not implemented.
* `lock()` tried `loginctl`, and when it existed but failed, never reached
  `slock` — a fallback chain that only fired when a binary was *missing*.

If a reading cannot be taken, **raise `PlatformError`**. Do not return a
plausible number. The capability layer turns the exception into an honest
`ok=False` with a reason, which is what every caller above is written to
expect.

---

## 4. Effectful is not the same as disturbing

Two different questions, and conflating them is a mistake already made here:

* **`effectful`** (in the catalogue) — *should a person press something
  first?* For acts that are not trivially undone. Marking a reversible thing
  effectful trains people to confirm without reading, which is worse than
  confirming nothing. `system.lock` is correctly **not** effectful.
* **`DISTURBS`** (in `tools/conformance.py`) — *may a measuring instrument do
  this to its subject?* Locking someone's screen mid-run is not unsafe, it is
  rude. It happened, on a real desktop, before that list existed.

An effectful capability **never** runs without a deliberate press, however
confident the reading. No confidence score is ever high enough.

---

## 5. The GUI is one presentation, not the contract

LCARS is **not** the primary interface. It is one client of the capability
layer, alongside voice, the terminal, and whatever comes next. A robot with
no screen runs the same CDOS.

Logic that belongs to the machine does not go in `screens/`. If you find
yourself putting a decision in a screen, it belongs in `core.py` or in the
corpus.

### There is one window, and it opens before the shell exists

`EarthWindow.shell` is `Optional` and starts as `None`. In that state the
window draws `splash.draw` — the LCARS frame with nothing in it — and once the
shell has been built, `attach()` makes it start drawing screens into the same
surface. There is no second window and no handover.

There used to be both. Present the real window, destroy the splash, in that
order, and hope the compositor never showed a frame in between. It usually did
not; "usually" is the wrong guarantee for the one moment the machine is being
judged on.

Two consequences, both easy to undo by accident:

* **`app.py` must not import `core`, `screens` or `voice` at the top.** They
  are imported inside the boot steps. At module level they are paid before
  `do_activate` can run, which is before any window can exist — 71 ms during
  which nothing can be on screen, because the splash cannot appear until this
  module has finished loading. Time to first frame was 144 ms and is now 135
  with 202 ms of shell construction behind the frame.
* **Nothing in `app.py` calls `.destroy()`.** A destroy during boot is a
  handover by another name.

```bash
make bootcheck       # boots the real application and measures it
```

A pass is *"boot contract holds"*: one window throughout, the frame up before
the shell, and animating rather than sitting still — a still image is
indistinguishable from a hang. It exits non-zero otherwise, and the structural
half is also in `tests/test_splash.py` so the four-second suite catches it too.

### There is one frame, in `earth_os/chrome.py`

`chrome.pieces()` is the only description of the LCARS frame. `Screen.add_chrome`
calls it and so does the boot splash, which draws the frame before any screen
exists — that is what makes boot look like one machine waking up rather than
two programs starting in sequence.

Two rules follow, both checked by `tests/test_splash.py`:

* **Do not rebuild the chrome anywhere else.** The splash did, for one day, and
  the copy had already lost the bottom bar's right cap and the clock pill
  beside it. Invisible alone, obvious at handover.
* **Do not move `Frame` back under `screens/`.** That is where it used to live,
  and importing it ran `screens/__init__`, which imports every screen, the
  window manager and the platform adapters — 72 ms and twenty-seven modules to
  draw an empty rectangle, during the startup the splash exists to cover. It
  is now 51 ms and eight, and the test fails if the splash reaches further.

### The session files are shipped code too

`session/` holds the sway config, the desktop entries and the session script.
They are installed and executed on every boot, and until `tests/test_session.py`
existed **nothing ever read them**. That is how `titlebar_padding 0` survived:
sway rejects any value below 1, so every session start printed *"Invalid size
specified"* and the only thing that noticed was a person reading the error.

Two rules, both checked:

* **Changing `session/sway.conf` means bumping `earth-os-config-version`.**
  The session script only replaces a user's `~/.config/earth-os/sway.conf`
  when the template's version is higher. Fix the template without bumping it
  and you have fixed `/usr/share` while leaving every existing machine exactly
  as broken — which is how a defect gets fixed twice and still reported.
* **Validate with sway itself, not by eye.** `sway -C` builds a backend before
  it parses, so on a machine with no GPU it dies before reading a line and
  reports success by never getting far enough to fail. `WLR_BACKENDS=headless`
  is what makes it actually parse. The test does this, and proves it works by
  handing sway the old broken line and requiring a rejection.

### The terminal is the declared escape hatch

`screens/terminal.py` uses `shell=True`. That is forbidden everywhere else and
correct there — a terminal's job is to run a command line a person typed and
meant. CDOS makes Linux *unnecessary*, not *unreachable*. An escape hatch that
is declared is a feature; an undeclared one is a leak. Do not add a second.

---

## 6. Portability is measured, not asserted

```bash
make conformance           # measure this machine
make bundle                # one file to carry to another machine
python3 tools/conformance.py --compare a.json b.json
```

A pass is *"N mechanisms differed; no capability disagreed."* If **nothing**
differed, that is one computer measured twice and the harness says so. Run the
conformance suite after any change below the boundary.

---

## 7. Working alongside another agent

More than one assistant may edit this tree. That is workable **because the
tests are the arbiter** — but only under these rules:

1. **Commit before you start and after you finish.** Small commits. The next
   agent needs to be able to see and undo what you did.
2. **Run the full suite before and after.** "It passed when I wrote it" is not
   a claim anyone can check later.
3. **Never edit `earth_os/platform/` and `earth_os/capability_corpus.py`
   in the same unreviewed change** unless you are adding a capability, which
   requires both. Those two files are the boundary; changing both at once is
   how the boundary moves without anyone noticing.
4. **Do not "fix" a failing test by changing the test** until you have
   understood what it was protecting. One test in this repository stopped a
   wrong change during this session by encoding real reasoning in its
   docstring. Read the docstring before touching the assertion.
5. **Say what you changed and why it is still true** — in the commit message,
   not only in conversation.

---

## Architecture documents

| File | What it is |
|---|---|
| `docs/CDOS_DISTRIBUTION.md` | The contract. What may exist above and below the boundary. |
| `docs/PLATFORM_AUDIT.md` | Four defects found after the platform layer shipped, and why the tests missed them. |
| `docs/PLATFORM_ABSTRACTION.md` | The capability API. |
| `docs/PORTING.md` | Writing a new adapter. |

If you change behaviour these describe, update them **in the same change**.
Numbers in those documents are checked against the code; leaving them stale is
how the last set of documents came to be wrong.
