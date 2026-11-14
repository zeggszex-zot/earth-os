"""How many of the frames we draw are the same picture as the one before?

The frame clock redraws unconditionally. This renders a run of frames at the
configured rate, hashes each one, and reports how many were identical to
their predecessor — the share of the shell's CPU that produced no change on
screen. It also reports which screen regions changed, so the answer is not
just "something moved" but "the clock moved".

    python3 tools/damage_audit.py --screen main --seconds 3 --fps 30
"""

from __future__ import annotations

import argparse
import hashlib
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


def build(width: int, height: int):
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    shell = Shell(config)
    for cls in ALL_SCREENS:
        shell.register_screen(cls(shell))
    return shell


def tiles(data: bytes, width: int, height: int, stride: int,
          cols: int = 8, rows: int = 6):
    """Hash the frame in a grid, so a change can be located."""
    out = []
    tw, th = width // cols, height // rows
    for r in range(rows):
        for c in range(cols):
            h = hashlib.blake2b(digest_size=8)
            for y in range(r * th, min((r + 1) * th, height), 3):
                row = y * stride
                h.update(data[row + c * tw * 4: row + (c + 1) * tw * 4])
            out.append(h.digest())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen", default="main")
    ap.add_argument("--size", default="1280x800")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--phosphor", default="on")
    args = ap.parse_args()

    W, H = (int(v) for v in args.size.lower().split("x"))
    shell = build(W, H)
    name = args.screen
    shell.current = name
    screen = shell.screens[name]
    screen.on_enter()

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(surface)
    stride = surface.get_stride()
    enabled = args.phosphor != "off"

    total = int(args.seconds * args.fps)
    period = 1.0 / args.fps
    same = 0
    changed_frames = []
    prev_hash = None
    prev_tiles = None
    region_hits = {}
    draw_ms = 0.0

    start = time.monotonic()
    for i in range(total):
        target = start + i * period
        now = time.monotonic()
        if now < target:
            time.sleep(target - now)
        t0 = time.perf_counter()
        ctx = shell.context(W, H)
        screen.tick()
        phosphor.composite(cr, W, H, lambda c: screen.draw(c, ctx),
                           background=shell.palette.background,
                           enabled=enabled)
        surface.flush()
        draw_ms += (time.perf_counter() - t0) * 1000.0
        data = bytes(surface.get_data())
        digest = hashlib.blake2b(data, digest_size=16).digest()
        if prev_hash is not None:
            if digest == prev_hash:
                same += 1
            else:
                changed_frames.append(i)
                now_tiles = tiles(data, W, H, stride)
                if prev_tiles is not None:
                    for idx, (a, b) in enumerate(zip(prev_tiles, now_tiles)):
                        if a != b:
                            region_hits[idx] = region_hits.get(idx, 0) + 1
                prev_tiles = now_tiles
        else:
            prev_tiles = tiles(data, W, H, stride)
        prev_hash = digest

    compared = total - 1
    print(f"screen {name}  {W}x{H}  phosphor {args.phosphor}  "
          f"{args.fps} fps  {args.seconds}s")
    print("=" * 66)
    print(f"  frames drawn                         {total}")
    print(f"  frames identical to the one before   {same} of {compared}"
          f"  ({100.0 * same / max(compared, 1):.1f}%)")
    print(f"  frames that changed anything         {len(changed_frames)}")
    print(f"  mean draw cost                       {draw_ms / total:.2f} ms")
    print(f"  CPU spent on identical frames        "
          f"{draw_ms / total * same * args.fps / 10.0 / max(compared, 1):.1f}%"
          " of one core")
    if changed_frames:
        gaps = [b - a for a, b in zip(changed_frames, changed_frames[1:])]
        if gaps:
            print(f"  frames between changes               "
                  f"min {min(gaps)}  max {max(gaps)}")
    if region_hits:
        cols = 8
        print(f"  grid cells that ever changed         "
              f"{len(region_hits)} of {cols * 6}")
        top = sorted(region_hits.items(), key=lambda kv: -kv[1])[:6]
        for idx, n in top:
            print(f"      cell row {idx // cols} col {idx % cols}: "
                  f"changed in {n} frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
