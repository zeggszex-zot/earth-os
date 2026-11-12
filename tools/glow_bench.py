"""Break the glow down into its stages, so the right one gets optimised.

``_glow`` is one function but five different costs: four successive halvings,
an additive mix of two of them, and one stretch back to the screen. Timing
the whole thing says only that it is expensive. This says which part is.

    python3 tools/glow_bench.py --size 1920x1080
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cairo  # noqa: E402

from earth_os import phosphor  # noqa: E402
from earth_os.config import Config  # noqa: E402
from earth_os.core import Shell  # noqa: E402
from earth_os.screens import ALL_SCREENS  # noqa: E402


def ms(fn, n=25, warmup=4) -> float:
    for _ in range(warmup):
        fn()
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000.0)
    return round(statistics.fmean(out), 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--frames", type=int, default=25)
    args = ap.parse_args()
    W, H = (int(v) for v in args.size.lower().split("x"))

    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    shell = Shell(config)
    for cls in ALL_SCREENS:
        shell.register_screen(cls(shell))
    screen = shell.screens["main"]
    screen.on_enter()

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(surface)
    ctx = shell.context(W, H)
    screen.draw(cr, ctx)
    cr.push_group_with_content(cairo.CONTENT_COLOR_ALPHA)
    screen.draw(cr, ctx)
    frame = cr.pop_group()

    print(f"glow stage breakdown at {W}x{H}, mean ms over {args.frames} runs")
    print("=" * 62)

    # Surface allocation alone, at each level the pyramid builds.
    sizes = [(W // 2, H // 2), (W // 4, H // 4), (W // 8, H // 8),
             (W // 16, H // 16)]
    total_alloc = 0.0
    for i, (w, h) in enumerate(sizes, start=1):
        t = ms(lambda w=w, h=h: cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h),
               args.frames)
        total_alloc += t
        print(f"  allocate level {i} surface {w}x{h:<6}        {t:>8.3f}")
    print(f"  {'allocation subtotal':<38}{total_alloc:>8.3f}")
    print("-" * 62)

    # Each halving, timed on its own input.
    src, w, h = frame, W, H
    levels = {}
    for step in (1, 2, 3, 4):
        t = ms(lambda s=src, w=w, h=h: phosphor._half(s, w, h), args.frames)
        out, w, h = phosphor._half(src, w, h)
        levels[step] = (out, w, h)
        src = cairo.SurfacePattern(out)
        print(f"  halve to level {step} ({w}x{h})".ljust(40) + f"{t:>8.3f}")
    print("-" * 62)

    # The additive mix of the two passes, at the finer pass's size.
    finest = min(n for n, _ in phosphor.GLOW_PASSES)
    _, gw, gh = levels[finest]

    def mix() -> None:
        glow = cairo.ImageSurface(cairo.FORMAT_ARGB32, gw, gh)
        gc = cairo.Context(glow)
        gc.set_operator(cairo.OPERATOR_ADD)
        for halvings, strength in phosphor.GLOW_PASSES:
            s, w2, h2 = levels[halvings]
            gc.save()
            gc.scale(gw / w2, gh / h2)
            gc.set_source_surface(s, 0, 0)
            p = gc.get_source()
            p.set_filter(cairo.FILTER_BILINEAR)
            p.set_extend(cairo.EXTEND_PAD)
            gc.paint_with_alpha(strength)
            gc.restore()
        glow.flush()

    print(f"  additive mix at {gw}x{gh}".ljust(40) + f"{ms(mix, args.frames):>8.3f}")

    glow = cairo.ImageSurface(cairo.FORMAT_ARGB32, gw, gh)
    gc = cairo.Context(glow)
    gc.set_operator(cairo.OPERATOR_ADD)
    for halvings, strength in phosphor.GLOW_PASSES:
        s, w2, h2 = levels[halvings]
        gc.save()
        gc.scale(gw / w2, gh / h2)
        gc.set_source_surface(s, 0, 0)
        p = gc.get_source()
        p.set_filter(cairo.FILTER_BILINEAR)
        p.set_extend(cairo.EXTEND_PAD)
        gc.paint_with_alpha(strength)
        gc.restore()
    glow.flush()

    def stretch() -> None:
        cr.save()
        cr.scale(W / gw, H / gh)
        cr.set_source_surface(glow, 0, 0)
        p = cr.get_source()
        p.set_filter(cairo.FILTER_BILINEAR)
        p.set_extend(cairo.EXTEND_PAD)
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.paint()
        cr.restore()

    print(f"  stretch {gw}x{gh} -> {W}x{H}".ljust(40)
          + f"{ms(stretch, args.frames):>8.3f}")
    print("-" * 62)
    whole = ms(lambda: phosphor._glow(cr, frame, W, H), args.frames)
    print(f"  {'WHOLE GLOW':<38}{whole:>8.3f}")

    # And what it costs to simply blit a cached full-size result instead.
    cached = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cc = cairo.Context(cached)
    cc.set_source(frame)
    cc.paint()
    cached.flush()

    def blit() -> None:
        cr.save()
        cr.set_source_surface(cached, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.restore()

    print(f"  {'(for comparison) blit a cached frame':<38}"
          f"{ms(blit, args.frames):>8.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
