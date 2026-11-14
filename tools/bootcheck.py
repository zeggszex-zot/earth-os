#!/usr/bin/env python3
"""Measure the boot, the way ``conformance.py`` measures portability.

Boot is a thing this shell now makes a claim about — *the frame is up before
the machine is, and it never hands over* — and a claim in a docstring is an
assertion. This runs the real application under a real display server and
reports what actually happened.

Three numbers matter, and each one corresponds to a defect that was in this
tree:

``time to first frame``
    How long the machine shows a console, a grey root window, or nothing.
    Was 144 ms of module imports before a window could even be constructed,
    because ``app.py`` imported the shell, the screens and the voice engine at
    the top. They are imported inside the boot steps now.

``peak concurrent windows``
    Must be 1. Two means the splash has become a separate window again, and a
    separate window means a handover — present one, destroy the other, and the
    compositor decides what is on screen in between. It usually got away with
    it. "Usually" is the wrong guarantee for the one moment the machine is
    being judged on.

``frames drawn before the shell attached``
    Must be more than one. If it is one, the frame appeared and was replaced
    without ever animating, which means the person saw a still image — and a
    still image is indistinguishable from a hang.

Usage::

    python3 tools/bootcheck.py                 # on a machine with a display
    xvfb-run -a python3 tools/bootcheck.py     # headless

Exits non-zero if the boot contract is broken, so it can be wired into CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List


def _locate_package() -> Path:
    """Find the CDOS tree whether this is run from the repo or carried away.

    ``conformance.py`` learned this the hard way: a loose copy of the harness
    in ``~/Downloads`` died on ``ModuleNotFoundError`` before measuring
    anything. A tool that only works from one directory is a tool that will be
    run from another one.
    """
    here = Path(__file__).resolve()
    for candidate in (here.parent.parent, Path.cwd()):
        if (candidate / "earth_os" / "__init__.py").is_file():
            return candidate
    raise SystemExit(
        "cannot find the earth_os package: run this from the CDOS tree")


def measure(budget: float = 6.0, interval_ms: int = 20) -> Dict:
    """Boot the application, watch it, and shut it down."""
    sys.path.insert(0, str(_locate_package()))

    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib

    from earth_os.app import EarthApplication

    started = time.monotonic()
    first_frame: List[float] = []
    attached: List[float] = []
    windows_seen: List[int] = []
    frames_before_attach = [0]
    steps: List[Dict] = []
    last_message = [""]

    app = EarthApplication()

    def sample() -> bool:
        live = app.get_windows()
        windows_seen.append(len(live))
        window = app.window
        if window is None:
            return True
        if not first_frame and window.get_visible():
            first_frame.append(time.monotonic() - started)
        if window.shell is None:
            frames_before_attach[0] += 1
            if window.message != last_message[0]:
                last_message[0] = window.message
                steps.append({"at_ms": round((time.monotonic() - started) * 1000),
                              "message": window.message})
        elif not attached:
            attached.append(time.monotonic() - started)
        return True

    GLib.timeout_add(interval_ms, sample)
    GLib.timeout_add(int(budget * 1000), lambda: (app.quit(), False)[1])
    app.run([])

    return {
        "time_to_first_frame_ms": (round(first_frame[0] * 1000, 1)
                                   if first_frame else None),
        "time_to_shell_ms": (round(attached[0] * 1000, 1)
                             if attached else None),
        "peak_concurrent_windows": max(windows_seen) if windows_seen else 0,
        "samples_before_shell": frames_before_attach[0],
        "sample_interval_ms": interval_ms,
        "steps": steps,
    }


#: What the boot has to satisfy. Each one is a defect that was real.
def verdicts(result: Dict) -> List[Dict]:
    peak = result["peak_concurrent_windows"]
    before = result["samples_before_shell"]
    first = result["time_to_first_frame_ms"]
    shell = result["time_to_shell_ms"]
    return [
        {"check": "the window opened at all",
         "ok": first is not None,
         "detail": f"{first} ms" if first is not None else "never appeared"},
        {"check": "one window throughout",
         "ok": peak <= 1,
         "detail": f"peak {peak}"},
        {"check": "the frame was up before the shell",
         "ok": before > 0,
         "detail": f"{before} samples drawing the frame"},
        {"check": "the frame animated rather than sat still",
         "ok": before > 1,
         "detail": f"{before} samples at "
                   f"{result['sample_interval_ms']} ms"},
        {"check": "the shell arrived",
         "ok": shell is not None,
         "detail": f"{shell} ms" if shell is not None else "never attached"},
    ]


def report(result: Dict) -> bool:
    checks = verdicts(result)
    width = max(len(c["check"]) for c in checks)
    print("CDOS boot check")
    print()
    first = result["time_to_first_frame_ms"]
    shell = result["time_to_shell_ms"]
    print(f"  time to first frame   {first} ms")
    print(f"  time to shell         {shell} ms")
    if first is not None and shell is not None:
        print(f"  covered by the frame  {round(shell - first, 1)} ms")
    print()
    for step in result["steps"]:
        print(f"  {step['at_ms']:6d} ms  {step['message']}")
    print()
    for check in checks:
        mark = "ok  " if check["ok"] else "FAIL"
        print(f"  {mark}  {check['check']:<{width}}  {check['detail']}")
    print()
    passed = all(c["ok"] for c in checks)
    print("boot contract holds" if passed else "BOOT CONTRACT BROKEN")
    return passed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--budget", type=float, default=6.0,
                        help="seconds to let the application run")
    parser.add_argument("--interval", type=int, default=20,
                        help="sampling interval in milliseconds")
    parser.add_argument("--json", metavar="PATH",
                        help="also write the raw measurement here")
    args = parser.parse_args(argv)

    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("no display: run this under xvfb-run, or on a machine with one",
              file=sys.stderr)
        return 2

    result = measure(budget=args.budget, interval_ms=args.interval)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2))
    return 0 if report(result) else 1


if __name__ == "__main__":
    sys.exit(main())
