"""Render every screen to a PNG without a display server.

Because the UI layer knows nothing about GTK windows, each screen can be drawn
straight onto an image surface.  Used for visual checks and for the screenshots
in the README.

    python3 tools/screenshot.py --out /tmp/shots --size 1920x1080
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cairo  # noqa: E402

from earth_os import phosphor  # noqa: E402
from earth_os.config import Config  # noqa: E402
from earth_os.core import Shell  # noqa: E402
from earth_os.screens import ALL_SCREENS  # noqa: E402


def build_shell(palette: str | None = None) -> Shell:
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    if palette:
        config.values["shell"]["palette"] = palette
    shell = Shell(config)
    for screen_class in ALL_SCREENS:
        shell.register_screen(screen_class(shell))
    return shell


def seed(shell: Shell) -> None:
    """Populate demo state so panels are not rendered empty."""
    shell.say("Computer online. All systems nominal.", speak=False)
    shell.user_said("Computer, status report.")
    shell.say(
        "All primary systems are operating within normal parameters. "
        "Processor load is nominal, storage at sixty-one percent of capacity, "
        "no thermal anomalies detected.", speak=False)
    shell.user_said("Computer, how much memory is free?")
    shell.say("Four point two gigabytes of eight are currently available.",
              speak=False)
    browser = shell.screens.get("browser")
    if browser is not None:
        browser.open("/usr/share/applications" if Path("/usr/share/applications").is_dir() else "/etc")


def render(shell: Shell, name: str, width: int, height: int, out: Path) -> Path:
    shell.current = name
    screen = shell.screens[name]
    screen.on_enter()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    ctx = shell.context(width, height)
    screen.invalidate()

    screen.draw(cr, ctx)          # first pass so live labels are populated
    phosphor.composite(cr, width, height, lambda c: screen.draw(c, ctx),
                       background=shell.palette.background,
                       enabled=phosphor.enabled(shell.config))
    path = out / f"{name}-{width}x{height}.png"
    surface.write_to_png(str(path))
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/cdos-shots")
    parser.add_argument("--size", default="1600x1000")
    parser.add_argument("--palette", default=None)
    parser.add_argument("--only", default=None, help="comma-separated screens")
    parser.add_argument("--flat", action="store_true",
                        help="no phosphor glow or scanlines")
    args = parser.parse_args()

    width, _, height = args.size.partition("x")
    width, height = int(width), int(height)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    shell = build_shell(args.palette)
    if args.flat:
        shell.config.values["shell"]["phosphor"] = False
    seed(shell)
    names = ([n.strip() for n in args.only.split(",")] if args.only
             else list(shell.screens))
    for name in names:
        if name not in shell.screens:
            print(f"unknown screen: {name}")
            continue
        path = render(shell, name, width, height, out)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
