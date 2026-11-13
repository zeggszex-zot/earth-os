"""What the System Evolution Center costs, measured on the real process.

The center's promise is that it is cheap and that it is silent when nobody is
looking (docs/SEC_ARCHITECTURE.md §6). ``tests/test_evolution.py`` holds the
*structure* of that promise — no second sampler, no frame without a change —
but a structure is not a number. This is the number: the real application,
under a nested X server, sitting on each section in turn, sampled from
outside through ``/proc`` the way ``tools/cpu_bench.py`` samples a screen.

Three rows are there to be compared against, not to be admired:

* ``main``           the home screen, the center never opened — so the engine
                     is not even imported. This is what Earth OS cost before.
* ``engineering``    the existing live-gauge screen, for scale.
* ``main (hidden)``  the home screen after the center has been opened and
                     left, with the background job at its fastest setting
                     (one sample every 5 s; the default is 60 s). The worst
                     the center can cost while it is not on show.

Every other row is one section of the center.

Alongside the outside view, the process reports what it counted itself: the
samples the center took and what each cost, and the frames the window drew.
Those are the figures the center shows a person, so this is also where they
are checked against an independent measurement.

    python3 tools/evolution_bench.py
    python3 tools/evolution_bench.py --window 30 --json /tmp/evolution.json

Needs ``Xvfb``. Everything is measured; nothing here is estimated.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cpu_bench import Display, proc_cpu_seconds, proc_rss_kb  # noqa: E402

SECTIONS = ("overview", "health", "optimization", "capabilities", "roadmap",
            "diagnostics", "history", "settings")

CONFIG = """\
[shell]
phosphor      = {phosphor}
fullscreen    = false
cursor        = true
fps           = 30

[voice]
enabled       = false

[evolution]
background_interval = {background}
"""

#: Runs inside the measured process. It is the real application — the same
#: ``EarthApplication`` ``earth-os`` starts — with one timer added to put it
#: on the screen and section being measured, and one to write down what the
#: process counted about itself when the sample window closes.
DRIVER = r'''
import json, sys, time
from earth_os.config import Config
from earth_os.app import EarthApplication
from gi.repository import GLib

config_path, plan, report_path, settle, window = sys.argv[1:6]
settle, window = float(settle), float(window)
app = EarthApplication(Config.load(config_path))
marks = {}

def counted():
    shell = app.shell
    center = shell._evolution
    out = {"imported": any(m.startswith("earth_os.evolution") for m in sys.modules),
           "render": shell.render_stats() if callable(shell.render_stats) else None,
           "center": center.overhead() if center is not None else None,
           "trend_samples": len(center.trends) if center is not None else 0}
    return out

def drive():
    shell = app.shell
    if shell is None or app.window is None or app.window.shell is None:
        return True                              # still booting
    screen, _, rest = plan.partition(":")
    shell.go(screen)
    if screen == "evolution" and rest:
        section, _, then = rest.partition(">")
        shell.screens["evolution"]._go_section(section)
        if then:
            shell.go(then)
    return False

def begin():
    marks["start"] = counted()
    return False

def finish():
    with open(report_path, "w") as fh:
        json.dump({"start": marks.get("start"), "end": counted()}, fh)
    return False

GLib.timeout_add(250, drive)
GLib.timeout_add(int(settle * 1000), begin)
GLib.timeout_add(int((settle + window) * 1000), finish)
sys.exit(app.run([sys.argv[0]]))
'''


def run_one(display: Display, plan: str, label: str, *, settle: float, window: float,
            python: str, phosphor: bool, background: float) -> Dict:
    workdir = Path(tempfile.mkdtemp(prefix="earthos-sec-bench-"))
    config = workdir / "config.toml"
    config.write_text(CONFIG.format(phosphor="true" if phosphor else "false",
                                    background=background))
    report = workdir / "counted.json"
    env = display.env
    env.update({"XDG_CACHE_HOME": str(workdir / "cache"),
                "XDG_CONFIG_HOME": str(workdir / "config"),
                "XDG_DATA_HOME": str(workdir / "data"),
                "HOME": str(workdir), "PYTHONPATH": str(ROOT)})

    proc = subprocess.Popen(
        [python, "-c", DRIVER, str(config), plan, str(report), str(settle), str(window)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    row: Dict = {"label": label, "plan": plan}
    try:
        time.sleep(settle)
        if proc.poll() is not None:
            err = (proc.stderr.read() or b"").decode("utf-8", "replace")
            row["error"] = err.strip()[-400:] or "exited during settle"
            return row
        slices: List[float] = []
        rss: List[int] = []
        first = last = proc_cpu_seconds(proc.pid)
        began = then = time.monotonic()
        for _ in range(max(int(window), 3)):
            time.sleep(1.0)
            now, cpu = time.monotonic(), proc_cpu_seconds(proc.pid)
            if cpu is None or last is None:
                break
            slices.append(100.0 * (cpu - last) / (now - then))
            last, then = cpu, now
            kb = proc_rss_kb(proc.pid)
            if kb:
                rss.append(kb)
        elapsed = time.monotonic() - began
        if first is not None and last is not None and elapsed > 0:
            row["cpu_percent"] = round(100.0 * (last - first) / elapsed, 2)
        if slices:
            row["cpu_median"] = round(statistics.median(slices), 2)
            row["cpu_max"] = round(max(slices), 1)
        if rss:
            row["rss_mb"] = round(max(rss) / 1024.0, 1)
            row["rss_growth_mb"] = round((rss[-1] - rss[0]) / 1024.0, 2)
        row["seconds"] = round(elapsed, 1)

        # What the process counted about itself over the same window.
        time.sleep(1.5)
        try:
            counted = json.loads(report.read_text())
        except (OSError, ValueError):
            counted = None
        if counted and counted.get("start") and counted.get("end"):
            a, b = counted["start"], counted["end"]
            row["engine_imported"] = bool(b.get("imported"))
            if a.get("render") and b.get("render"):
                row["frames"] = b["render"]["frames_drawn"] - a["render"]["frames_drawn"]
                row["draw_ms_mean"] = round(b["render"]["draw_ms_mean"], 1)
            if b.get("center"):
                before = (a.get("center") or {}).get("samples", 0)
                row["samples"] = int(b["center"]["samples"] - before)
                row["sample_ms_mean"] = round(b["center"]["mean_ms"], 2)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        shutil.rmtree(workdir, ignore_errors=True)
    return row


#: The before-and-after check: the real application on the home screen with
#: the center hidden and sampling every 5 s; at the change point it carries
#: one optimization through the center's own approval line — propose,
#: approve, apply — and at the end through testing and VERIFIED. The center's
#: comparison is written out with the window edges, so the bench can measure
#: the same windows from outside.
BEFORE_AFTER = r'''
import json, sys, time
from earth_os.config import Config
from earth_os.app import EarthApplication
from gi.repository import GLib

config_path, report_path, optimization, before_s, after_s, warm = sys.argv[1:7]
before_s, after_s, warm = float(before_s), float(after_s), float(warm)
app = EarthApplication(Config.load(config_path))
out = {"optimization": optimization}

def ready():
    return app.shell is not None and app.window is not None and app.window.shell is not None

def open_once():
    if not ready():
        return True
    if "booted" not in out:
        out["booted"] = time.time()
    if time.time() - out["booted"] < warm:
        return True                  # let start-up settle: it is not the change
    app.shell.go("evolution")        # builds the center, as a person opening it would
    app.shell.go("main")
    out["opened"] = time.time()
    GLib.timeout_add(int(before_s * 1000), change)
    return False

def change():
    shell = app.shell
    center = shell._evolution
    planner = center.planner
    p = center.propose_from("optimization", optimization, by="bench")
    planner.approve(p.id, by="bench")
    planner.implement(p.id, by="bench", before=center.vitals())
    out["proposal"] = p.id
    out["changed_at"] = p.measured["changed_at"]
    out["applied"] = {k: p.applied.get(k) for k in ("setting", "value", "previous",
                                                    "in_effect_now", "backup")}
    GLib.timeout_add(int(after_s * 1000), verify)
    return False

def verify():
    center = app.shell._evolution
    p = center.planner.get(out["proposal"])
    center.planner.begin_testing(p.id, by="bench")
    center.planner.complete(p.id, by="bench", after=center.after_change(p))
    out["before"] = p.measured["before"]
    out["after"] = p.measured["after"]
    out["comparison"] = p.measured["comparison"]
    out["config_after"] = open(config_path).read()
    center.planner.roll_back(p.id, by="bench")
    out["rolled_back"] = p.applied.get("reverted")
    out["config_rolled_back"] = open(config_path).read()
    with open(report_path, "w") as fh:
        json.dump(out, fh, default=str)
    app.quit()
    return False

GLib.timeout_add(500, open_once)
sys.exit(app.run([sys.argv[0]]))
'''


def before_after(display: Display, optimization: str, *, before: float, after: float,
                 python: str, screen_size: str, warm: float = 20.0) -> Dict:
    """Carry one optimization through the center in the real application,
    and measure the same two windows from outside through /proc."""
    from earth_os.evolution.compare import SETTLE_SECONDS
    workdir = Path(tempfile.mkdtemp(prefix="earthos-sec-ba-"))
    config = workdir / "config.toml"
    config.write_text(CONFIG.format(phosphor="true", background=5.0))
    original = config.read_text()
    report = workdir / "result.json"
    env = display.env
    env.update({"XDG_CACHE_HOME": str(workdir / "cache"), "XDG_CONFIG_HOME": str(workdir / "config"),
                "XDG_DATA_HOME": str(workdir / "data"), "HOME": str(workdir),
                "PYTHONPATH": str(ROOT)})
    proc = subprocess.Popen([python, "-c", BEFORE_AFTER, str(config), str(report), optimization,
                             str(before), str(after), str(warm)],
                            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    outside: List[tuple] = []           # (wall, cpu seconds, rss kb)
    try:
        while proc.poll() is None:
            cpu, kb = proc_cpu_seconds(proc.pid), proc_rss_kb(proc.pid)
            if cpu is not None:
                outside.append((time.time(), cpu, kb or 0))
            time.sleep(0.5)
        result = json.loads(report.read_text())
    except (OSError, ValueError):
        err = (proc.stderr.read() or b"").decode("utf-8", "replace") if proc.stderr else ""
        return {"error": err.strip()[-600:] or "no result"}
    finally:
        if proc.poll() is None:
            proc.kill()
        shutil.rmtree(workdir, ignore_errors=True)

    def window(start: float, end: float) -> Dict:
        inside = [o for o in outside if start <= o[0] <= end]
        if len(inside) < 2:
            return {}
        (w0, c0, _), (w1, c1, _) = inside[0], inside[-1]
        return {"cpu_percent": 100.0 * (c1 - c0) / (w1 - w0),
                "rss_mb": statistics.mean(o[2] for o in inside) / 1024.0,
                "seconds": w1 - w0}

    b, a = result["before"], result["after"]
    result["outside"] = {"before": window(float(b["from"]), float(b["to"])),
                         "after": window(float(a["from"]), float(a["to"]))}
    result["restored_exactly"] = result.get("config_rolled_back") == original
    result["settle"] = SETTLE_SECONDS
    return result


def print_before_after(result: Dict) -> None:
    if "error" in result:
        print("FAILED:", result["error"])
        return
    from earth_os.evolution.compare import describe
    print(f"Optimization {result['optimization']} as {result['proposal']}: "
          f"{result['applied']['setting']} {result['applied']['previous']} -> "
          f"{result['applied']['value']}, in effect now: {result['applied']['in_effect_now']}")
    for label in ("before", "after"):
        w = result[label]
        print(f"  {label:<6} window: {w['samples']} samples over {w['span_seconds']} s, "
              f"center on screen {w.get('center_on_screen', 0):.0%}")
    print()
    print("What the center reports:")
    for line in describe(result["comparison"]).splitlines():
        print("  " + line)
    print()
    print("Measured from outside (/proc), the same two windows:")
    ob, oa = result["outside"]["before"], result["outside"]["after"]
    if ob and oa:
        print(f"  Earth OS processor time  {ob['cpu_percent']:.2f}% -> {oa['cpu_percent']:.2f}% of one core")
        print(f"  Earth OS memory (RSS)    {ob['rss_mb']:.1f} MB -> {oa['rss_mb']:.1f} MB")
    rb = result.get("rolled_back") or {}
    print()
    print(f"Rolled back: restored to {rb.get('restored')}, whole file "
          f"{'restored byte for byte' if rb.get('whole_file_restored') else 'NOT restored'}, "
          f"in effect now: {rb.get('in_effect_now')}; config identical to the original: "
          f"{result['restored_exactly']}")


def table(rows: List[Dict]) -> str:
    head = (f"{'where':<26}{'CPU %':>7}{'median':>8}{'max':>7}{'RSS MB':>9}{'grew':>7}"
            f"{'frames':>8}{'samples':>9}{'ms each':>9}{'engine':>8}")
    out = [head, "-" * len(head)]
    for r in rows:
        if "error" in r:
            out.append(f"{r['label']:<26} FAILED: {r['error'][:60]}")
            continue

        def num(key: str, spec: str) -> str:
            return format(r[key], spec) if key in r else "-"
        out.append(f"{r['label']:<26}{num('cpu_percent', '.2f'):>7}{num('cpu_median', '.2f'):>8}"
                   f"{num('cpu_max', '.1f'):>7}{num('rss_mb', '.1f'):>9}"
                   f"{num('rss_growth_mb', '+.2f'):>7}{num('frames', 'd'):>8}"
                   f"{num('samples', 'd'):>9}{num('sample_ms_mean', '.2f'):>9}"
                   f"{('yes' if r.get('engine_imported') else 'no'):>8}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--settle", type=float, default=8.0)
    ap.add_argument("--window", type=float, default=20.0)
    ap.add_argument("--phosphor", default="on", choices=("on", "off"))
    ap.add_argument("--sections", default=",".join(SECTIONS))
    ap.add_argument("--only-sections", action="store_true",
                    help="leave out the three comparison rows (for a long soak "
                         "of one or two sections)")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--json", default="")
    ap.add_argument("--before-after", metavar="OPTIMIZATION", default="",
                    help="instead of the cost table: carry one optimization (e.g. "
                         "phosphor-off) through the center in the real application and "
                         "check its before/after comparison against /proc")
    ap.add_argument("--phase", type=float, default=90.0,
                    help="with --before-after: seconds before, and after, the change")
    args = ap.parse_args()

    if args.before_after:
        sys.path.insert(0, str(ROOT))
        with Display(args.size, number=74) as display:
            result = before_after(display, args.before_after, before=args.phase,
                                  after=args.phase + 6.0, python=args.python,
                                  screen_size=args.size)
        print_before_after(result)
        if args.json:
            Path(args.json).write_text(json.dumps(result, indent=2, default=str))
            print(f"\nwrote {args.json}")
        return 0 if "error" not in result else 1

    plans = [] if args.only_sections else [("main", "main (center never opened)", 60.0),
                                            ("engineering", "engineering", 60.0)]
    plans += [(f"evolution:{s}", f"evolution · {s}", 60.0)
              for s in args.sections.split(",") if s]
    if not args.only_sections:
        plans.append(("evolution:health>main", "main (hidden, every 5 s)", 5.0))

    rows: List[Dict] = []
    with Display(args.size, number=73) as display:
        for plan, label, background in plans:
            rows.append(run_one(display, plan, label, settle=args.settle, window=args.window,
                                python=args.python, phosphor=args.phosphor == "on",
                                background=background))
            print(table(rows[-1:]).splitlines()[-1], flush=True)

    print()
    print("System Evolution Center — process cost")
    print(f"display {args.size}  phosphor {args.phosphor}  settle {args.settle:g}s  "
          f"sample {args.window:g}s  python {args.python}")
    print("=" * 98)
    print(table(rows))
    print()
    print("CPU % is of one core, from /proc/<pid>/stat over the sample window. 'grew' is the "
          "change in\nRSS across the window. frames, samples and ms each are what the process "
          "counted itself.")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"size": args.size, "phosphor": args.phosphor, "settle": args.settle,
             "window": args.window, "rows": rows}, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
