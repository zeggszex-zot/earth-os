"""Draw the shell into a PNG, for when the screen itself cannot be captured.

Capturing the whole screen is a platform capability (``display.screenshot``)
and depends on a tool the session can use. When there is none — a bare X
server, a compositor that refuses — the shell can still draw *itself*,
because every screen paints onto a Cairo context and does not care whether
that is a window or an image. Other programs are not in the picture, and the
shell says so; but the button still produces a file instead of an error.

Same technique as ``tools/screenshot.py`` and the agent bridge's
``op_screenshot``, kept here so the running shell does not import a tool.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

#: Under the person's Pictures folder, so a file manager finds them where it
#: would look, and in a folder of their own so they are easy to pick out.
FOLDER_NAME = "EARTH OS Screenshots"


def pictures_dir() -> Path:
    """The XDG pictures directory, or ~/Pictures."""
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    try:
        for line in (config / "user-dirs.dirs").read_text(
                encoding="utf-8").splitlines():
            if line.startswith("XDG_PICTURES_DIR="):
                value = line.split("=", 1)[1].strip().strip('"')
                return Path(os.path.expandvars(
                    value.replace("$HOME", str(Path.home()))))
    except OSError:
        pass
    return Path.home() / "Pictures"


def screenshot_folder() -> Path:
    return pictures_dir() / FOLDER_NAME


def new_screenshot_path() -> Path:
    return screenshot_folder() / f"cdos-{time.strftime('%Y%m%d-%H%M%S')}.png"


def capture_from_command_line() -> int:
    """``earth-os --screenshot``: the whole screen, into the folder.

    For the session's Print key. The compositor grabs that key before any
    program sees it, so the running shell cannot be the one to answer it;
    this runs instead, through the same capability, into the same folder,
    and the shell notices the new file and says so on screen. Imports no
    toolkit, so it is quick enough to bind to a key.
    """
    from .platform import CapabilityRegistry, PlatformError, get_platform
    try:
        registry = CapabilityRegistry(get_platform())
    except PlatformError as exc:
        print(f"earth-os: {exc}", file=sys.stderr)
        return 1
    result = registry.invoke("display.screenshot", path=str(new_screenshot_path()))
    if not result.ok:
        print(f"earth-os: screenshot failed: {result.error}", file=sys.stderr)
        return 1
    print(result.value)
    return 0


def render_shell(shell, path: str, width: int, height: int,
                 screen_name: str = "") -> str:
    """Paint a screen — *screen_name*, or the one up now — into *path*."""
    import cairo

    width = max(320, min(int(width), 7680))
    height = max(240, min(int(height), 4320))
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)

    screen = shell.screens.get(screen_name) if screen_name else shell.screen()
    if screen is None:
        raise RuntimeError("no screen is up")
    from . import phosphor

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    ctx = shell.context(width, height)

    # Through the tube, like the window: a screenshot should look like the
    # screen it was taken of.
    phosphor.composite(cr, width, height, lambda c: screen.draw(c, ctx),
                       background=shell.palette.background,
                       enabled=phosphor.enabled(shell.config))
    # The window is a different size, or showing another screen; make the
    # one drawn here rebuild for it rather than keep this canvas's layout.
    screen.invalidate()
    surface.write_to_png(str(target))
    return str(target)
