"""Run the real shell under a virtual display and measure what it costs.

``render_bench.py`` times the draw path; this times the *process*, which is
the number a person actually sees in ``top``. It starts a nested X server,
launches EARTH OS against a configuration it writes itself, lets it settle,
then samples ``/proc/<pid>/stat`` and ``/proc/<pid>/status`` over a fixed
window. One configuration per run, restarted each time, which is what makes
the A-F matrix comparable rather than suggestive.

    python3 tools/cpu_bench.py --matrix
    python3 tools/cpu_bench.py --fps 30 --phosphor on --screen main
    python3 tools/cpu_bench.py --matrix --screens main,engineering,programs

Needs ``Xvfb``. Everything is measured; nothing here is estimated.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent

#: The A-F matrix from the brief, plus the screen to sit on.
MATRIX = (("A", 30, True), ("B", 20, True), ("C", 10, True),
          ("D", 30, False), ("E", 20, False), ("F", 10, False))

CONFIG = """\
[shell]
phosphor      = {phosphor}
fullscreen    = false
cursor        = true
fps           = {fps}
start_screen  = "{screen}"

[voice]
enabled       = false

[agent]
enabled       = false
"""


def clock_ticks() -> float:
    try:
        return float(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError):
        return 100.0


def proc_cpu_seconds(pid: int) -> Optional[float]:
    """utime+stime of *pid* and its reaped children, in seconds."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    # After the comm field: state is [0], so utime is [11] and stime [12].
    utime, stime = float(fields[11]), float(fields[12])
    return (utime + stime) / clock_ticks()


def proc_rss_kb(pid: int) -> Optional[int]:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def system_idle() -> Optional[float]:
    """Cumulative idle+iowait jiffies across all cores."""
    try:
        parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    except (OSError, IndexError):
        return None
    values = [float(v) for v in parts]
    return values[3] + (values[4] if len(values) > 4 else 0.0)


def system_total() -> Optional[float]:
    try:
        parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    except (OSError, IndexError):
        return None
    return sum(float(v) for v in parts)


class Display:
    """A nested X server, so the real GTK window really gets drawn."""

    def __init__(self, size: str = "1920x1080", number: int = 71):
        self.size = size
        self.number = number
        self.proc: Optional[subprocess.Popen] = None

    def __enter__(self) -> "Display":
        if not shutil.which("Xvfb"):
            raise RuntimeError("Xvfb is not installed; cannot measure the "
                               "process without a display")
        self.proc = subprocess.Popen(
            ["Xvfb", f":{self.number}", "-screen", "0", f"{self.size}x24",
             "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        if self.proc.poll() is not None:
            raise RuntimeError("Xvfb exited immediately")
        return self

    def __exit__(self, *exc) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    @property
    def env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env["DISPLAY"] = f":{self.number}"
        env["GDK_BACKEND"] = "x11"
        env.pop("WAYLAND_DISPLAY", None)
        # Keep the measurement about rendering, not about a theme lookup.
        env["GSK_RENDERER"] = env.get("GSK_RENDERER", "cairo")
        return env


class Pointer:
    """Moves the pointer, so the interactive state can be measured.

    Idle CPU is the easy number to improve and the easy one to improve
    dishonestly: a shell that is cheap at rest and sluggish under a finger
    has not been optimised, it has been slowed down. This drives real motion
    events into the window through the nested X server for the whole sample
    window, so the interactive figure is measured under load rather than
    assumed.
    """

    def __init__(self, env: Dict[str, str], size: str) -> None:
        self.env = env
        self.width, self.height = (int(v) for v in size.lower().split("x"))
        self.proc: Optional[subprocess.Popen] = None

    def available(self) -> bool:
        return shutil.which("xdotool") is not None

    def start(self) -> None:
        if not self.available():
            return
        # A sweep across the middle of the window, back and forth, at about
        # 50 moves a second — faster than a hand, which is the point.
        script = (
            "while true; do "
            f"for x in $(seq 80 24 {max(self.width - 80, 200)}); do "
            f"xdotool mousemove $x $(( {self.height} / 2 )); "
            "done; "
            f"for x in $(seq {max(self.width - 80, 200)} -24 80); do "
            f"xdotool mousemove $x $(( {self.height} / 3 )); "
            "done; done")
        self.proc = subprocess.Popen(
            ["sh", "-c", script], env=self.env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def run_one(display: Display, *, fps: int, phosphor: bool, screen: str,
            settle: float, window: float, python: str,
            label: str = "", interact: bool = False) -> Dict:
    """Start the shell once, let it settle, then sample it.

    With *interact* the pointer is swept across the window throughout the
    sample, which is how the interactive figure is measured rather than
    guessed.
    """
    workdir = Path(tempfile.mkdtemp(prefix="earthos-bench-"))
    config = workdir / "config.toml"
    config.write_text(CONFIG.format(
        phosphor="true" if phosphor else "false", fps=fps, screen=screen))

    env = display.env
    env["XDG_CACHE_HOME"] = str(workdir / "cache")
    env["XDG_CONFIG_HOME"] = str(workdir / "config")
    env["XDG_DATA_HOME"] = str(workdir / "data")
    env["HOME"] = str(workdir)
    env["PYTHONPATH"] = str(ROOT)

    proc = subprocess.Popen(
        [python, "-m", "earth_os", "--windowed", "--no-voice",
         "-c", str(config), "-s", screen],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    result: Dict = {"label": label, "fps": fps,
                    "phosphor": "on" if phosphor else "off",
                    "screen": screen,
                    "pointer": "moving" if interact else "still"}
    pointer = Pointer(env, display.size)
    try:
        time.sleep(settle)
        if interact:
            if not pointer.available():
                result["note"] = "xdotool missing; pointer not moved"
            else:
                pointer.start()
                time.sleep(0.5)
        if proc.poll() is not None:
            err = (proc.stderr.read() or b"").decode("utf-8", "replace")
            result["error"] = err.strip()[-400:] or "exited during settle"
            return result

        # Sample in slices so a spike is visible rather than averaged away.
        slices = max(int(window), 3)
        cpu0 = proc_cpu_seconds(proc.pid)
        idle0, tot0 = system_idle(), system_total()
        t0 = time.monotonic()
        per_slice: List[float] = []
        rss: List[int] = []
        last_cpu, last_t = cpu0, t0
        for _ in range(slices):
            time.sleep(1.0)
            now = time.monotonic()
            cpu = proc_cpu_seconds(proc.pid)
            if cpu is None or last_cpu is None:
                break
            per_slice.append(100.0 * (cpu - last_cpu) / (now - last_t))
            last_cpu, last_t = cpu, now
            kb = proc_rss_kb(proc.pid)
            if kb:
                rss.append(kb)
        cpu1 = proc_cpu_seconds(proc.pid)
        idle1, tot1 = system_idle(), system_total()
        elapsed = time.monotonic() - t0

        if cpu0 is not None and cpu1 is not None and elapsed > 0:
            result["cpu_percent"] = round(100.0 * (cpu1 - cpu0) / elapsed, 1)
        if per_slice:
            result["cpu_per_second"] = [round(v, 1) for v in per_slice]
            result["cpu_median"] = round(statistics.median(per_slice), 1)
            result["cpu_max"] = round(max(per_slice), 1)
        if rss:
            result["rss_mb"] = round(max(rss) / 1024.0, 1)
        if None not in (idle0, idle1, tot0, tot1) and tot1 > tot0:
            result["system_idle_percent"] = round(
                100.0 * (idle1 - idle0) / (tot1 - tot0), 1)
        result["seconds_sampled"] = round(elapsed, 1)
    finally:
        pointer.stop()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        shutil.rmtree(workdir, ignore_errors=True)
    return result


def table(rows: List[Dict]) -> str:
    out = [f"{'test':<6}{'screen':<13}{'fps':>5}{'phos':>6}{'pointer':>9}"
           f"{'CPU %':>8}{'median':>8}{'max':>7}{'RSS MB':>9}{'sys idle':>10}"]
    out.append("-" * 81)
    for r in rows:
        if "error" in r:
            out.append(f"{r.get('label',''):<6}{r['screen']:<13}"
                       f"{r['fps']:>5}{r['phosphor']:>6}   FAILED: "
                       f"{r['error'][:28]}")
            continue
        out.append(
            f"{r.get('label',''):<6}{r['screen']:<13}{r['fps']:>5}"
            f"{r['phosphor']:>6}{r.get('pointer','still'):>9}"
            f"{r.get('cpu_percent', 0):>8.1f}"
            f"{r.get('cpu_median', 0):>8.1f}{r.get('cpu_max', 0):>7.1f}"
            f"{r.get('rss_mb', 0):>9.1f}{r.get('system_idle_percent', 0):>10.1f}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", action="store_true",
                    help="run the A-F configurations")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--phosphor", default="on", choices=("on", "off"))
    ap.add_argument("--screen", default="main")
    ap.add_argument("--screens", default="",
                    help="comma separated; with --matrix, every screen")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--settle", type=float, default=6.0)
    ap.add_argument("--window", type=float, default=8.0)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--json", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--interact", action="store_true",
                    help="sweep the pointer across the window while sampling")
    args = ap.parse_args()

    screens = [s for s in args.screens.split(",") if s] or [args.screen]
    rows: List[Dict] = []
    with Display(args.size) as display:
        if args.matrix:
            for screen in screens:
                for label, fps, phos in MATRIX:
                    rows.append(run_one(
                        display, fps=fps, phosphor=phos, screen=screen,
                        settle=args.settle, window=args.window,
                        python=args.python, label=label,
                        interact=args.interact))
                    print(table(rows[-1:]).splitlines()[-1], flush=True)
        else:
            for screen in screens:
                rows.append(run_one(
                    display, fps=args.fps, phosphor=args.phosphor == "on",
                    screen=screen, settle=args.settle, window=args.window,
                    python=args.python, label="-",
                    interact=args.interact))
                print(table(rows[-1:]).splitlines()[-1], flush=True)

    print()
    print(f"EARTH OS process benchmark  ({args.note or 'unlabelled'})")
    print(f"display {args.size}  settle {args.settle}s  "
          f"sample {args.window}s  python {args.python}")
    print("=" * 72)
    print(table(rows))
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"note": args.note, "size": args.size, "rows": rows}, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
