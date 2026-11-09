"""Window policy: which program goes where, and which one is us.

The *mechanisms* — sway IPC, wlrctl, wmctrl, xdotool — moved below the
capability boundary to :mod:`earth_os.platform.linux_windows`, where
mechanisms belong.  What is left here is the half that is not a fact about
any compositor:

* which window is the shell's own, so it does not frame itself;
* whether a newly launched program should be framed or left alone;
* caching the list so a thirty-times-a-second frame clock can ask cheaply.

Those are decisions.  They read the same on sway, on X11 and on a machine
with no compositor cooperation at all, which is exactly the test for
belonging above the line.

The backend names are re-exported so existing callers keep working; new code
should reach the same functionality through the ``window`` capability domain
rather than through this module.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from . import brand
from .platform.linux_windows import (          # noqa: F401  (re-exported)
    NullWindows, SwayWindows, TIMEOUT, Window, WindowBackend, WlrctlWindows,
    WmctrlWindows, detect,
)

#: Application ids this shell is known by, so it can recognise its own window.
SELF_APP_IDS = frozenset({
    "earth-os", "org.earthos.shell", "earth_os", "earth.os",
})


def _is_self(window: "Window") -> bool:
    app = (window.app or "").strip().lower()
    if app in SELF_APP_IDS:
        return True
    # Fall back to an exact title match for backends that report no app id.
    return (window.title or "").strip().upper() in brand.TITLES


class WindowManager:
    """Caches the window list so the UI can poll it cheaply."""

    def __init__(self, preferred: str = "auto", fullscreen_launched: bool = True):
        self.backend = detect(preferred)
        self.fullscreen_launched = fullscreen_launched
        self.windows: List[Window] = []
        self.error = ""

    @property
    def available(self) -> bool:
        return self.backend.available

    def status(self) -> str:
        return f"{self.backend.name}: {self.backend.status()}"

    def refresh(self) -> List[Window]:
        if not self.backend.available:
            self.windows = []
            return self.windows
        try:
            found = self.backend.list_windows()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return self.windows
        # Drop our own window; switching to ourselves is not a feature.  Match
        # on the application id only — a substring match on the title would
        # also hide, say, an archive manager showing "cdos_v0_9_0.zip".
        self.windows = [w for w in found if not _is_self(w)]
        return self.windows

    @property
    def can_place(self) -> bool:
        return self.backend.available and self.backend.can_place

    def switch_to(self, window: Window,
                  frame: Optional[Tuple[int, int, int, int]] = None) -> Tuple[bool, str]:
        """Focus a window and either frame it or fullscreen it.

        With *frame*, the window is placed inside that rectangle so the LCARS
        chrome stays visible around it — the behaviour of the original, where
        programs lived inside the interface rather than replacing it.
        """
        if not self.backend.available:
            return (False, self.backend.status())
        if not self.backend.focus(window):
            return (False, f"could not focus {window.label}")

        if frame is not None and self.backend.can_place:
            x, y, w, h = frame
            if self.backend.place(window, x, y, w, h):
                window.framed = True
                return (True, f"{window.label} in frame")
            return (True, f"switched to {window.label} (could not frame it)")

        if self.fullscreen_launched:
            self.backend.fullscreen(window, True)
            window.framed = False
        return (True, f"switched to {window.label}")

    def claim_screen(self, app_ids) -> Optional[Tuple[int, int, int, int]]:
        """Give the shell the whole output, and stop new windows tiling it.

        Under a tiling compositor the shell is just another window, so sway
        halves it the moment anything else opens. The shell is meant to be the
        frame, so it takes the whole output and floats everything else.
        Returns the output rectangle, which is also the shell's own geometry.
        """
        backend = self.backend
        if not backend.available or not hasattr(backend, "output_geometry"):
            return None
        rect = backend.output_geometry()
        if rect is None:
            return None
        backend.float_all()
        own = backend.find_by_app(app_ids)
        if own is not None:
            x, y, w, h = rect
            backend.place(own, x, y, w, h)
        return rect

    def break_out(self, window: Window) -> Tuple[bool, str]:
        """Take a framed window to true fullscreen, over the whole shell."""
        if not self.backend.available:
            return (False, self.backend.status())
        self.backend.focus(window)
        if self.backend.fullscreen(window, True):
            window.framed = False
            return (True, f"{window.label} fullscreen")
        return (False, f"could not fullscreen {window.label}")

    def close(self, window: Window) -> Tuple[bool, str]:
        if not self.backend.available:
            return (False, self.backend.status())
        ok = self.backend.close(window)
        return (ok, f"closed {window.label}" if ok
                else f"could not close {window.label}")

    def find(self, text: str) -> Optional[Window]:
        """Best match by app name or title, for a spoken request."""
        needle = (text or "").strip().lower()
        if not needle:
            return None
        for window in self.windows:
            if needle == (window.app or "").lower():
                return window
        for window in self.windows:
            if needle in (window.app or "").lower() or needle in (window.title or "").lower():
                return window
        return None
