"""Talking to a compositor about other programs' windows.

Below the capability boundary, because every line of it is a mechanism.  A
Wayland client cannot see or touch another client's windows — that is the
compositor's job, by design — so this asks whatever *can*, and says plainly
when nothing can:

``sway`` / ``i3``
    A real IPC.  ``swaymsg -t get_tree`` enumerates every window and
    ``swaymsg [con_id=N] focus`` switches to one.  The best case.

``wlrctl`` / ``lswt`` (wlroots)
    The ``wlr-foreign-toplevel-management`` protocol, which labwc, river and
    Wayfire implement.  Enough to list and focus.

``wmctrl`` / ``xdotool`` (X11)
    Works on any X session, including GNOME-on-Xorg and KDE-on-Xorg.

``none``
    GNOME and KDE on Wayland deliberately expose no such protocol to
    unprivileged clients.  There is no workaround, and the shell says so
    rather than pretending the feature is broken.

This was written before the boundary existed and arrived as a parallel
adapter system of its own — an ABC and four implementations, already the
right shape, simply on the wrong side of the line.  Moving it was mounting
rather than rewriting: the backends below are unchanged.  What stayed above
in :mod:`earth_os.windows` is the part that is *policy* — when to frame a
program, which window is the shell's own — because that is a decision rather
than a mechanism, and decisions belong above the line.

Everything runs through ``subprocess`` with an argv list; there is no shell
anywhere in this file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

TIMEOUT = 4.0


@dataclass
class Window:
    """One window belonging to some other program."""

    handle: str                 # backend-specific identifier
    title: str
    app: str = ""
    workspace: str = ""
    focused: bool = False
    fullscreen: bool = False
    framed: bool = False

    @property
    def label(self) -> str:
        name = self.app or self.title or "window"
        if self.title and self.app and self.title.lower() != self.app.lower():
            trimmed = self.title if len(self.title) <= 48 else self.title[:45] + "..."
            return f"{name} — {trimmed}"
        return name


def _run(argv: List[str]) -> Tuple[int, str]:
    try:
        done = subprocess.run(argv, timeout=TIMEOUT, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True,
                              errors="replace", shell=False)
        return (done.returncode, done.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return (-1, "")


class WindowBackend(ABC):
    name = "none"

    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def list_windows(self) -> List[Window]: ...

    def focus(self, window: Window) -> bool:
        return False

    def fullscreen(self, window: Window, on: bool = True) -> bool:
        return False

    def close(self, window: Window) -> bool:
        return False

    def place(self, window: Window, x: int, y: int, w: int, h: int) -> bool:
        """Put a window at an exact rectangle, in screen pixels.

        This is what makes frame mode possible: the shell keeps its chrome and
        hands the middle of the screen to the program, instead of the program
        covering everything.
        """
        return False

    @property
    def can_place(self) -> bool:
        return False

    def status(self) -> str:
        return "unavailable"


class NullWindows(WindowBackend):
    """No compositor cooperation available."""

    name = "none"

    def __init__(self, reason: str = ""):
        self.reason = reason or "no supported window control found"

    @property
    def available(self) -> bool:
        return False

    def list_windows(self) -> List[Window]:
        return []

    def status(self) -> str:
        return self.reason


class SwayWindows(WindowBackend):
    """sway and i3, through their IPC. The most capable backend."""

    name = "sway"

    def __init__(self, binary: str = ""):
        self.binary = binary or shutil.which("swaymsg") or shutil.which("i3-msg") or ""

    @property
    def available(self) -> bool:
        if not self.binary:
            return False
        code, _ = _run([self.binary, "-t", "get_version"])
        return code == 0

    def status(self) -> str:
        if not self.binary:
            return "swaymsg/i3-msg not on PATH"
        return f"{self.binary}" if self.available else f"{self.binary} present but not responding"

    def list_windows(self) -> List[Window]:
        code, out = _run([self.binary, "-t", "get_tree"])
        if code != 0 or not out:
            return []
        try:
            tree = json.loads(out)
        except json.JSONDecodeError:
            return []

        found: List[Window] = []

        def walk(node: dict, workspace: str = "") -> None:
            if node.get("type") == "workspace":
                workspace = str(node.get("name", ""))
            # A leaf with an app_id or window_class is a real window.
            app = node.get("app_id") or ""
            if not app:
                props = node.get("window_properties") or {}
                app = props.get("class") or ""
            children = (node.get("nodes") or []) + (node.get("floating_nodes") or [])
            if not children and (app or node.get("window")):
                found.append(Window(
                    handle=str(node.get("id")),
                    title=str(node.get("name") or ""),
                    app=str(app),
                    workspace=workspace,
                    focused=bool(node.get("focused")),
                    fullscreen=bool(node.get("fullscreen_mode")),
                ))
            for child in children:
                walk(child, workspace)

        walk(tree)
        return found

    def _command(self, window: Window, command: str) -> bool:
        code, _ = _run([self.binary, f"[con_id={window.handle}]", command])
        return code == 0

    def focus(self, window: Window) -> bool:
        return self._command(window, "focus")

    def fullscreen(self, window: Window, on: bool = True) -> bool:
        return self._command(window, f"fullscreen {'enable' if on else 'disable'}")

    def close(self, window: Window) -> bool:
        return self._command(window, "kill")

    # -- output geometry and our own window --------------------------------
    def output_geometry(self) -> Optional[Tuple[int, int, int, int]]:
        """The focused output's rectangle, in screen pixels.

        The shell cannot ask GTK how big the screen is when sway has tiled it
        into half of one, so the size has to come from the compositor.
        """
        code, out = _run([self.binary, "-t", "get_outputs"])
        if code != 0 or not out:
            return None
        try:
            outputs = json.loads(out)
        except json.JSONDecodeError:
            return None
        chosen = None
        for output in outputs:
            if not output.get("active", True):
                continue
            if output.get("focused"):
                chosen = output
                break
            chosen = chosen or output
        if not chosen:
            return None
        rect = chosen.get("rect") or {}
        try:
            return (int(rect["x"]), int(rect["y"]),
                    int(rect["width"]), int(rect["height"]))
        except (KeyError, TypeError, ValueError):
            return None

    def find_by_app(self, app_ids) -> Optional[Window]:
        wanted = {a.lower() for a in app_ids}
        for window in self.list_windows():
            if (window.app or "").lower() in wanted:
                return window
        return None

    def float_all(self) -> None:
        """Ask sway to float new windows, so the shell can position them.

        Without this sway tiles each new window beside the shell, halving it
        and leaving nothing for the frame to sit in.
        """
        _run([self.binary, "for_window", "[app_id=.*]", "floating", "enable"])
        _run([self.binary, "for_window", "[class=.*]", "floating", "enable"])

    @property
    def can_place(self) -> bool:
        return True

    def place(self, window: Window, x: int, y: int, w: int, h: int) -> bool:
        """Float the window and pin it to an exact rectangle.

        Tiling would fight the shell for the screen, so the window is floated
        first; sway then honours an absolute position and size.
        """
        ok = self._command(window, "fullscreen disable")
        ok = self._command(window, "floating enable") and ok
        code, _ = _run([self.binary, f"[con_id={window.handle}]",
                        f"resize set {int(w)} {int(h)}"])
        ok = (code == 0) and ok
        code, _ = _run([self.binary, f"[con_id={window.handle}]",
                        f"move absolute position {int(x)} {int(y)}"])
        return (code == 0) and ok


class WlrctlWindows(WindowBackend):
    """wlroots compositors through wlrctl (labwc, river, Wayfire)."""

    name = "wlrctl"

    def __init__(self):
        self.binary = shutil.which("wlrctl") or ""

    @property
    def available(self) -> bool:
        if not self.binary:
            return False
        code, _ = _run([self.binary, "toplevel", "list"])
        return code == 0

    def status(self) -> str:
        return self.binary or "wlrctl not on PATH"

    def list_windows(self) -> List[Window]:
        code, out = _run([self.binary, "toplevel", "list"])
        if code != 0:
            return []
        windows: List[Window] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            app, _, title = line.partition(":")
            windows.append(Window(handle=line, title=title.strip(),
                                  app=app.strip()))
        return windows

    def focus(self, window: Window) -> bool:
        code, _ = _run([self.binary, "toplevel", "focus",
                        f"app_id:{window.app}"])
        return code == 0

    def fullscreen(self, window: Window, on: bool = True) -> bool:
        code, _ = _run([self.binary, "toplevel", "fullscreen",
                        f"app_id:{window.app}"])
        return code == 0


class WmctrlWindows(WindowBackend):
    """Any X11 session, including GNOME-on-Xorg and KDE-on-Xorg."""

    name = "wmctrl"

    def __init__(self):
        self.binary = shutil.which("wmctrl") or ""
        self.xdotool = shutil.which("xdotool") or ""

    @staticmethod
    def _under_wayland() -> bool:
        """True when this is a Wayland session, whatever DISPLAY says.

        XWayland sets ``DISPLAY`` inside a Wayland session, so its presence
        proves nothing.
        """
        return bool(os.environ.get("WAYLAND_DISPLAY")) or \
            os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"

    @property
    def available(self) -> bool:
        if not self.binary:
            return False
        # Under Wayland, wmctrl is talking to XWayland. It will answer, and it
        # will even list windows — but only X11 ones. Native Wayland clients
        # (Firefox, most GTK4 apps) are invisible to it. Reporting this as
        # working produces an empty program list and windows that never move,
        # which looks like a broken feature rather than an absent one.
        if self._under_wayland():
            return False
        if not os.environ.get("DISPLAY"):
            return False
        code, _ = _run([self.binary, "-m"])
        return code == 0

    def status(self) -> str:
        if not self.binary:
            return "wmctrl not on PATH (sudo apt install wmctrl)"
        if self._under_wayland():
            return ("wmctrl is installed but this is a Wayland session; it can "
                    "only see XWayland windows, not native ones. Log into an "
                    "X11 session, or run the shell under sway.")
        if not os.environ.get("DISPLAY"):
            return "wmctrl present but this is not an X session"
        return self.binary

    def list_windows(self) -> List[Window]:
        code, out = _run([self.binary, "-lx"])
        if code != 0:
            return []
        windows: List[Window] = []
        for line in out.splitlines():
            # 0x03000007  0 gedit.Gedit   hostname   Document
            parts = re.split(r"\s+", line.strip(), maxsplit=4)
            if len(parts) < 5:
                continue
            handle, desktop, wm_class, _host, title = parts
            if desktop == "-1":
                continue                     # sticky/panel windows
            app = wm_class.split(".")[-1] if wm_class else ""
            windows.append(Window(handle=handle, title=title, app=app,
                                  workspace=desktop))
        return windows

    def focus(self, window: Window) -> bool:
        code, _ = _run([self.binary, "-i", "-a", window.handle])
        return code == 0

    def fullscreen(self, window: Window, on: bool = True) -> bool:
        code, _ = _run([self.binary, "-i", "-r", window.handle,
                        "-b", f"{'add' if on else 'remove'},fullscreen"])
        return code == 0

    def close(self, window: Window) -> bool:
        code, _ = _run([self.binary, "-i", "-c", window.handle])
        return code == 0

    @property
    def can_place(self) -> bool:
        return True

    def place(self, window: Window, x: int, y: int, w: int, h: int) -> bool:
        """Move and resize through wmctrl's -e gravity,X,Y,W,H form.

        Maximised windows ignore geometry changes, so those states come off
        first.
        """
        _run([self.binary, "-i", "-r", window.handle,
              "-b", "remove,maximized_vert,maximized_horz"])
        _run([self.binary, "-i", "-r", window.handle,
              "-b", "remove,fullscreen"])
        code, _ = _run([self.binary, "-i", "-r", window.handle,
                        "-e", f"0,{int(x)},{int(y)},{int(w)},{int(h)}"])
        return code == 0


def detect(preferred: str = "auto") -> WindowBackend:
    """Pick the best available backend, or explain why there is none."""
    candidates = {
        "sway": SwayWindows, "wlrctl": WlrctlWindows, "wmctrl": WmctrlWindows,
    }
    if preferred in candidates:
        backend = candidates[preferred]()
        return backend if backend.available else NullWindows(
            f"{preferred}: {backend.status()}")
    if preferred == "none":
        return NullWindows("disabled in config")

    for factory in (SwayWindows, WlrctlWindows, WmctrlWindows):
        backend = factory()
        if backend.available:
            return backend

    # Say something useful about *why*, since the answer differs by desktop.
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or \
        os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"
    have_wmctrl = bool(shutil.which("wmctrl"))

    if wayland and ("gnome" in desktop or "kde" in desktop or "plasma" in desktop):
        note = (f"{desktop or 'this desktop'} on Wayland exposes no "
                f"window-control protocol to applications, so programs cannot "
                f"be listed or placed.")
        if have_wmctrl:
            # Worth saying plainly: they installed wmctrl and it is not the
            # missing piece.
            note += (" wmctrl is installed but only reaches XWayland windows, "
                     "not native Wayland ones, so it does not help here.")
        note += (" Log into the Xorg session (wmctrl then works), or install "
                 "sway for programs framed inside the interface.")
        return NullWindows(note)

    if wayland:
        return NullWindows(
            "no window control found. Install sway for full support including "
            "framing, or wlrctl for switching only on a wlroots compositor.")

    return NullWindows(
        "no window control found. On X11: sudo apt install wmctrl")


