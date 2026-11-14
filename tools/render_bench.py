"""Measure what a frame actually costs, without a display server.

The UI layer is toolkit-agnostic — ``tools/screenshot.py`` already draws
screens straight onto an image surface — so the per-frame cost of the real
pipeline can be measured here exactly as the window pays it:

    background fill  ->  screen.draw()  ->  glow  ->  frame  ->  overlay

That is the whole of ``EarthWindow._on_draw``, and the sum of those stages
times the frame rate is the shell's floor on CPU. Nothing is estimated: each
stage is timed separately against the same surface GTK would hand us.

    python3 tools/render_bench.py --size 1920x1080 --frames 60
    python3 tools/render_bench.py --matrix          # the A-F configurations
    python3 tools/render_bench.py --profile         # cProfile the draw path

Run it with the interpreter that has pycairo, which on a system with more
than one Python is not always ``python3``.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cairo  # noqa: E402

from earth_os import phosphor  # noqa: E402
from earth_os.config import Config  # noqa: E402
from earth_os.core import Shell  # noqa: E402
from earth_os.screens import ALL_SCREENS  # noqa: E402

#: Screens worth timing: the home screen, the two with live gauges, the
#: terminal, and the program list.
SCREENS = ("main", "engineering", "terminal", "programs", "tasks")


def build_shell(phosphor_on: bool = True, fps: int = 30) -> Shell:
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    config.values["shell"]["phosphor"] = phosphor_on
    config.values["shell"]["fps"] = fps
    shell = Shell(config)
    for screen_class in ALL_SCREENS:
        shell.register_screen(screen_class(shell))
    return shell


def seed(shell: Shell) -> None:
    """Enough state that panels are not timed empty."""
    shell.say("Computer online. All systems nominal.", speak=False)
    shell.user_said("Computer, status report.")
    shell.say("All primary systems are operating within normal parameters.",
              speak=False)


def _timed(fn: Callable[[], None], frames: int, warmup: int = 5) -> Dict:
    """*fn* run *frames* times; milliseconds per call, warmup discarded."""
    for _ in range(warmup):
        fn()
    samples: List[float] = []
    for _ in range(frames):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    samples.sort()
    return {
        "mean_ms": round(statistics.fmean(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(samples[min(int(len(samples) * 0.95), len(samples) - 1)], 3),
        "min_ms": round(samples[0], 3),
        "max_ms": round(samples[-1], 3),
        "frames": frames,
    }


def stages(shell: Shell, name: str, width: int, height: int,
           frames: int) -> Dict:
    """Time each stage of one frame separately, on a real image surface."""
    shell.current = name
    screen = shell.screens[name]
    screen.on_enter()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)

    def ctx():
        return shell.context(width, height)

    # Build once so geometry is not charged to the first timed frame.
    screen.draw(cr, ctx())

    out: Dict = {"screen": name, "size": f"{width}x{height}"}

    # 1. The background fill the window does before anything else.
    def fill() -> None:
        cr.save()
        cr.set_source_rgb(*shell.palette.background)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        cr.restore()

    out["background"] = _timed(fill, frames)

    # 2. The screen's own drawing, into a group as the window does it.
    def draw_only() -> None:
        c = ctx()
        cr.push_group_with_content(cairo.CONTENT_COLOR_ALPHA)
        screen.draw(cr, c)
        cr.pop_group()

    out["screen_draw"] = _timed(draw_only, frames)

    # 3. The glow, over a frame that has already been drawn.
    cr.push_group_with_content(cairo.CONTENT_COLOR_ALPHA)
    screen.draw(cr, ctx())
    captured = cr.pop_group()

    def glow_only() -> None:
        cr.save()
        phosphor._glow(cr, captured, width, height)
        cr.restore()

    out["glow"] = _timed(glow_only, frames)

    # 4. Painting the crisp frame and the cached scanline overlay.
    def overlay_only() -> None:
        cr.save()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source(captured)
        cr.paint()
        cr.set_source_surface(phosphor.overlay(width, height), 0, 0)
        cr.paint()
        cr.restore()

    out["frame_and_overlay"] = _timed(overlay_only, frames)

    # 5. The whole thing, phosphor on and off, as _on_draw pays it.
    def whole(enabled: bool) -> Callable[[], None]:
        def run() -> None:
            c = ctx()
            phosphor.composite(cr, width, height,
                               lambda cc: screen.draw(cc, c),
                               background=shell.palette.background,
                               enabled=enabled)
        return run

    out["composite_on"] = _timed(whole(True), frames)
    out["composite_off"] = _timed(whole(False), frames)

    # 6. What the frame clock does besides drawing.
    def tick_only() -> None:
        screen.tick()

    out["tick"] = _timed(tick_only, frames)

    def ensure_only() -> None:
        screen.ensure_built(ctx())

    out["ensure_built"] = _timed(ensure_only, frames)
    return out


def cpu_estimate(frame_ms: float, fps: int) -> float:
    """Percent of one core a frame of *frame_ms* costs at *fps*.

    Arithmetic on a measured number, not a measurement: it is what the draw
    path alone would use with the process doing nothing else.
    """
    return round(frame_ms * fps / 10.0, 1)


def report(results: List[Dict], fps_list=(30, 20, 10)) -> str:
    lines = ["", "PER-STAGE FRAME COST (measured, mean ms)", "=" * 78]
    head = f"{'screen':<13}{'bg':>7}{'draw':>8}{'glow':>8}{'frame+ov':>10}{'ON':>9}{'OFF':>9}{'tick':>8}"
    lines.append(head)
    lines.append("-" * 78)
    for r in results:
        lines.append(
            f"{r['screen']:<13}"
            f"{r['background']['mean_ms']:>7.2f}"
            f"{r['screen_draw']['mean_ms']:>8.2f}"
            f"{r['glow']['mean_ms']:>8.2f}"
            f"{r['frame_and_overlay']['mean_ms']:>10.2f}"
            f"{r['composite_on']['mean_ms']:>9.2f}"
            f"{r['composite_off']['mean_ms']:>9.2f}"
            f"{r['tick']['mean_ms']:>8.3f}")
    lines += ["", "IMPLIED CPU, ONE CORE, DRAW PATH ONLY (%)", "=" * 78]
    lines.append(f"{'screen':<13}" + "".join(
        f"{f'{f}on':>9}{f'{f}off':>9}" for f in fps_list))
    lines.append("-" * 78)
    for r in results:
        row = f"{r['screen']:<13}"
        for f in fps_list:
            row += f"{cpu_estimate(r['composite_on']['mean_ms'], f):>9.1f}"
            row += f"{cpu_estimate(r['composite_off']['mean_ms'], f):>9.1f}"
        lines.append(row)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--screens", default=",".join(SCREENS))
    ap.add_argument("--json", default="")
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    width, height = (int(v) for v in args.size.lower().split("x"))
    shell = build_shell()
    seed(shell)
    names = [n for n in args.screens.split(",") if n in shell.screens]

    if args.profile:
        screen = shell.screens[names[0]]
        shell.current = names[0]
        screen.on_enter()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(surface)
        screen.draw(cr, shell.context(width, height))

        def run() -> None:
            for _ in range(args.frames):
                c = shell.context(width, height)
                phosphor.composite(cr, width, height,
                                   lambda cc: screen.draw(cc, c),
                                   background=shell.palette.background,
                                   enabled=True)

        profiler = cProfile.Profile()
        profiler.enable()
        run()
        profiler.disable()
        stats = pstats.Stats(profiler)
        stats.sort_stats("tottime").print_stats(22)
        return 0

    results = [stages(shell, n, width, height, args.frames) for n in names]
    print(f"EARTH OS render benchmark  ({args.label or 'unlabelled'})")
    print(f"size {width}x{height}  frames {args.frames}  python {sys.version.split()[0]}")
    print(report(results))
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"label": args.label, "size": args.size, "results": results},
            indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
