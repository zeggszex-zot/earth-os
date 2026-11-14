# Platform layer: audit of the Phase 2 delivery

Written after Phase 2 landed, against the code as it actually runs rather
than as the delivery note described it.  Four defects are recorded here
because each one was invisible to the test suite at the time — they are the
shape of thing this layer will keep producing, so they are worth naming.

Every one of them is the same mistake wearing a different hat:

> **The adapter manufactured a confident answer instead of admitting failure.**

That is precisely the property `CapabilityResult` exists to provide — *"Never
raises at the caller; failure is data."*  The contract was right.  Two
adapters quietly broke it, and the tests could not see the difference.

---

## 1. Power commands reported success for work that never happened

`LinuxSystem._run_command` started the process and returned:

```python
try:
    subprocess.Popen(argv, ...)
except (OSError, subprocess.SubprocessError):
    ...fallback...
```

`Popen` only raises when the **binary is missing**.  `systemctl reboot` on a
machine not booted with systemd exists, runs, prints *"Failed to connect to
bus: Host is down"*, exits non-zero — and none of that reaches the caller.

Two consequences, the second worse than the first:

* The shell said *Acknowledged* to a reboot that did not occur.
* **The fallback was unreachable.**  `sudo shutdown -r now` only ran when
  `systemctl` was absent, never when it was present and failed — which is the
  one situation it was written for.

The shell already knew better about programs.  `Shell._watch_launch` carries
the comment *"Popen succeeding only means the binary ran"*, and watches for
programs that die immediately.  That reasoning was never carried across to
the power commands.

**Fixed** by waiting on each attempt and judging the outcome.  A power command
that works either returns zero and takes the machine down behind it, or never
returns; one that fails does so in milliseconds.  So a short grace separates
them, and *the timeout is the success case*:

```
system.reboot -> ok=False
  systemctl: Failed to connect to bus: Host is down;
  sudo: Failed to talk to init daemon: Host is down
```

Both attempts now run, and the error names the whole chain.  `Shell._end_session`
already had a `not result.ok` branch to fall back on quitting — it had simply
never been reachable.

## 2. `get_resolution()` returned the same invented answer on every machine

It ended in `return (1024, 768)`.  A caller could not tell a reading from a
placeholder.  Worse, the line above it shelled out to `xrandr -c` — a flag
xrandr does not accept — so the call failed, and **the placeholder was what
every real machine got**.

The test asserted only *"two ints greater than zero"*, so it passed on the
wrong answer, indefinitely.

**Fixed** by asking the kernel first (`/sys/class/graphics/fb0/virtual_size`,
then DRM modes), which is correct under X, under Wayland and on a bare console
alike, with `xrandr --current` behind it and `PlatformError` if none of them
can answer.  The capability layer turns that into an honest `ok=False`.

## 3. Brightness could only ever work as root

`set_brightness` wrote `/sys/class/backlight/*/brightness` directly, which
needs root, so it always failed in an ordinary session and reported `False`
with no reason.  `brightnessctl`, `light` and `xbacklight` exist precisely to
bridge that gap and were never tried.  `get_brightness` returned a bare `0.5`
when it could read nothing, and divided by `max_brightness` without guarding
zero.

**Fixed**: helpers first, raw write last, and a refusal rather than `0.5`.

## 4. The registry could never select a robot adapter

This one had not bitten yet because Phase 3 has not started.  It would have
bitten immediately.

```python
def register_adapter(adapter_class, priority=999):
    _ADAPTERS.append(adapter_class)
    # Sort by priority (would need to add priority tracking)
    pass
```

The documented `priority` was ignored.  Combined with
`LinuxPlatform.available()` returning **true on any Linux**, a robot adapter
registered on a Linux-based robot would be appended *after* `LinuxPlatform`,
which would match first and win — so the robot adapter could never be
selected, however it was registered.

The demonstration the whole architecture is aimed at — *the computer changed,
CDOS didn't* — would have failed on the first try, in a way that looks like
the robot adapter being broken rather than the registry.

**Fixed**: priority is honoured, the built-in Linux adapter sits at
`GENERAL = 1000` because Linux is the *general* answer rather than the
specific one, and anything registered afterwards is tried ahead of it by
default.  `get_platform()` now also reports what each adapter said when none
matches, instead of a bare *"no adapter available"* — that message lands in
front of whoever is porting CDOS to a new machine, which is the moment they
most need it.

---

## Where Phase 2 actually stands

The headline claim holds, and it is worth stating exactly:

**`earth_os/core.py` contains zero direct process spawns.**  Launching a
program, powering down, and asking who is logged in each have exactly one
path, through the capability layer.  That was the goal and it was met.

The broader claim — *"the shell no longer knows any Linux"* — is not yet true.
Twenty-two direct spawn sites remain outside the platform layer:

| Module | Sites | Needs |
|---|---|---|
| `voice/backends.py` | 7 | `audio` domain |
| `voice/capture.py` | 4 | `audio` domain |
| `cli.py` | 3 | tooling — arguably belongs outside |
| `screens/terminal.py` | 3 | `process` domain, with a pty |
| `audio.py` | 2 | `audio` domain |
| `voice/cdos_backend.py` | 2 | corpus bridge |
| `windows.py` | 1 | a `window` domain that does not exist yet |

`core.py` also keeps three small Linux assumptions: the `xdg-open
/etc/os-release` default bound to `notepad`, and two `shutil.which` probes in
`open_path` and `_deactivate_confirmed`.

None of this makes Phase 2 a failure — the core is clean, which is the part
that matters. But the shell as a whole is not yet platform-free, and the
distance is measurable: **22 call sites and one missing capability domain.**
That is the Phase 3 worklist, and it is better to carry it as a number than
as a claim.

---

## What the tests could not see

All four defects sat under a green suite.  The pattern is worth keeping:

* **A range assertion cannot catch a plausible constant.**  `1024x768` passes
  *"two ints greater than zero"* forever.  The resolution test now asserts the
  reading is *not* the old placeholder.
* **Mocking the layer under test hides the layer under test.**  Every power
  test mocked the capability registry, so `_run_command` — where the bug was —
  had no test at all.  It has seven now.
* **An unused extension point is untested by construction.**  Nothing
  registered an adapter, so nothing noticed that registering one did not work.
  There are six adapter-priority tests now, including one that registers a
  stub "Robot" on this Linux machine and asserts it wins.

Suite: **288 tests, all passing** (273 before; +15 here), under Python 3.12.

## A note on running the tests

The suite needs **Python 3.12**, not the default `python3` (3.11):

```bash
/usr/bin/python3.12 -m unittest discover -s tests -q
```

PyGObject's compiled extension is built for CPython 3.12, so under 3.11
`import gi` fails and the four GTK-touching test modules — including
`test_capabilities.py`, which covers the whole Phase 2 integration — error out
at import.  That is an interpreter mismatch, not a code fault, and it was
hiding the fact that the integration tests were never running.
