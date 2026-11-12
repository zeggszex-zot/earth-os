"""Prove the tube still looks exactly like the tube.

Every optimisation in :mod:`earth_os.phosphor` is supposed to change what a
frame *costs*, never what it *shows*. This renders each screen through the
current code and compares it, pixel for pixel, against a reference PNG
rendered by the code before the change.

    python3 tools/phosphor_compare.py --save  /tmp/ref   # before
    python3 tools/phosphor_compare.py --check /tmp/ref   # after

A difference is reported as the number of pixels that differ and the largest
difference in any channel, so "identical" is a measurement rather than an
impression. Exit status is non-zero if anything differs.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cairo  # noqa: E402

from earth_os import chrome, phosphor  # noqa: E402

# The clock shows seconds, so it is the other thing that makes two runs
# differ for no interesting reason. Pinned before any screen is built.
chrome.clock_label = lambda when=None: "WED 11 NOV\n09:41:07"
try:
    from earth_os.screens import base as _screens_base
    _screens_base.clock_label = chrome.clock_label
except Exception:  # pragma: no cover
    pass

# The terminal's block caret blinks off ``time.time``, not off the render
# context, so it is the one animation the frozen ``now`` does not reach.
try:
    import time as _time_module

    from earth_os.screens import terminal as _terminal_module

    class _PinnedClock:
        def __getattr__(self, name):
            return getattr(_time_module, name)

        def time(self) -> float:
            return 1000.0

    _terminal_module.time = _PinnedClock()
except Exception:  # pragma: no cover
    pass
from earth_os.config import Config  # noqa: E402
from earth_os.core import Shell  # noqa: E402
from earth_os.screens import ALL_SCREENS  # noqa: E402

SCREENS = ("main", "engineering", "terminal", "programs", "tasks",
           "settings", "recent")


class FrozenMonitor:
    """Fixed hardware readings, so two runs draw the same picture.

    The gauges, the telemetry line and the clock are all live, and a frame
    rendered a second after another legitimately differs. Comparing the real
    thing against itself showed 25,075 pixels differing on ENGINEERING for
    that reason alone — which is noise, not a regression, and it would have
    hidden a real one. Everything that moves on its own is pinned here.
    """

    def __init__(self, real) -> None:
        self._real = real

    def cpu(self) -> float: return 0.3125
    def cores(self): return [0.25, 0.375, 0.3125, 0.4375]
    def memory(self): return (0.5, 4 * 1024 ** 3, 8 * 1024 ** 3)
    def swap(self): return (0.125, 1 * 1024 ** 3, 8 * 1024 ** 3)
    def disk(self, path: str = "/"): return (0.61, 61 * 1024 ** 3,
                                             100 * 1024 ** 3)
    def load(self): return (0.5, 0.4, 0.3)
    def uptime(self) -> float: return 7265.0
    def temperature(self): return 41.0
    def battery(self): return None
    def net_rate(self): return (1024.0 * 128, 1024.0 * 32)
    def processes(self) -> int: return 214
    def hostname(self) -> str: return "earth"
    def kernel(self) -> str: return "6.8.0"
    def distro(self) -> str: return "Linux"
    def poll(self, force: bool = False) -> None: pass
    def last_error(self) -> str: return ""

    def __getattr__(self, name):
        # Anything not pinned above comes from the real monitor, so a new
        # reading does not break this tool silently.
        return getattr(self._real, name)


def build() -> Shell:
    # A fixed path, not mkdtemp: the diagnostics tab prints the config's
    # location on screen, so a fresh temporary directory per run put a
    # different string in the picture and reported 2,203 pixels of
    # "difference" that were the path and nothing else.
    home = Path(tempfile.gettempdir()) / "earth-os-phosphor-compare"
    home.mkdir(parents=True, exist_ok=True)
    config = Config.load(home / "config.toml")
    config.values["voice"]["enabled"] = False
    shell = Shell(config)
    shell.monitor = FrozenMonitor(shell.monitor)
    for cls in ALL_SCREENS:
        screen = cls(shell)
        # Screens that cached the monitor at construction get the frozen one.
        if getattr(screen, "monitor", None) is not None:
            screen.monitor = shell.monitor
        shell.register_screen(screen)
    shell.say("Computer online. All systems nominal.", speak=False)
    return shell


def render(shell: Shell, name: str, w: int, h: int,
           frozen_now: float = 1000.0) -> cairo.ImageSurface:
    """One frame of *name*, with the clock held still so runs compare.

    ``RenderContext.now`` drives flashes and the caret, so it is pinned:
    without that, two runs a second apart differ legitimately and the
    comparison says nothing.
    """
    shell.current = name
    screen = shell.screens[name]
    screen.on_enter()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surface)
    ctx = shell.context(w, h)
    try:
        ctx.now = frozen_now
    except (AttributeError, TypeError):
        pass
    screen.draw(cr, ctx)          # build geometry first
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surface)
    phosphor.composite(cr, w, h, lambda c: screen.draw(c, ctx),
                       background=shell.palette.background, enabled=True)
    surface.flush()
    return surface


def compare(a: Path, b_surface: cairo.ImageSurface) -> tuple:
    ref = cairo.ImageSurface.create_from_png(str(a))
    if (ref.get_width(), ref.get_height()) != (b_surface.get_width(),
                                               b_surface.get_height()):
        return (-1, -1)
    x, y = bytes(ref.get_data()), bytes(b_surface.get_data())
    if x == y:
        return (0, 0)
    differing = 0
    worst = 0
    for i in range(0, min(len(x), len(y)), 4):
        px, py = x[i:i + 4], y[i:i + 4]
        if px != py:
            differing += 1
            worst = max(worst, max(abs(c - d) for c, d in zip(px, py)))
    return (differing, worst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", default="")
    ap.add_argument("--check", default="")
    ap.add_argument("--size", default="1280x800")
    ap.add_argument("--screens", default=",".join(SCREENS))
    args = ap.parse_args()
    w, h = (int(v) for v in args.size.lower().split("x"))
    names = args.screens.split(",")

    shell = build()
    if args.save:
        out = Path(args.save)
        out.mkdir(parents=True, exist_ok=True)
        for name in names:
            if name not in shell.screens:
                continue
            render(shell, name, w, h).write_to_png(str(out / f"{name}.png"))
        print(f"saved {len(names)} reference frames to {out}")
        return 0

    if not args.check:
        print("give --save or --check")
        return 2

    ref_dir = Path(args.check)
    bad = 0
    print(f"{'screen':<14}{'pixels differing':>18}{'worst channel':>16}")
    print("-" * 48)
    for name in names:
        if name not in shell.screens:
            continue
        ref = ref_dir / f"{name}.png"
        if not ref.exists():
            print(f"{name:<14}{'no reference':>18}")
            continue
        differing, worst = compare(ref, render(shell, name, w, h))
        if differing == -1:
            print(f"{name:<14}{'SIZE MISMATCH':>18}")
            bad += 1
            continue
        flag = "" if differing == 0 else "  <-- DIFFERS"
        print(f"{name:<14}{differing:>18}{worst:>16}{flag}")
        if differing:
            bad += 1
    print("-" * 48)
    print("identical" if not bad else f"{bad} screen(s) differ")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
