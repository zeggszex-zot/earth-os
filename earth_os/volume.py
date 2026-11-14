"""The machine's output volume, as the chrome's volume spot sees it.

The spot in the upper-left box shows the level and opens a slider. Both go
through ``audio.get_volume`` and ``audio.set_volume`` — the same capabilities
"computer, louder" uses — so the slider and the voice cannot disagree about
what the volume is, and this module names no mechanism.

Dragging a slider asks for a new level many times a second. Each request
costs a process start below the boundary, so they are coalesced: the level
on screen follows the finger at once, and one worker thread applies only the
newest value whenever it is free. A drag of forty steps is a handful of
calls, and the last one is always the one the person let go on.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


class SystemVolume:
    """Read and set the output volume without blocking the interface."""

    #: How far the - and + buttons move it.
    STEP = 0.05

    def __init__(self, shell):
        self.shell = shell
        #: The level as last read or asked for, 0.0-1.0; None until known.
        self.level: Optional[float] = None
        #: Why the volume cannot be read or set, or "".
        self.error = ""
        #: Whether the slider is showing.
        self.panel_open = False
        self._want: Optional[float] = None
        self._busy = False
        self._read_once = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def _post(self) -> Callable[[Callable[[], None]], None]:
        post = getattr(self.shell, "call_on_main", None)
        return post if post is not None else (lambda fn: fn())

    def _registry(self):
        registry = getattr(self.shell, "capabilities", None)
        if registry is None:
            self.error = (f"no platform adapter: "
                          f"{getattr(self.shell, 'platform_error', '')}")
        return registry

    # ------------------------------------------------------------------
    def toggle_panel(self) -> None:
        self.panel_open = not self.panel_open
        if self.panel_open:
            self.refresh()
        self.shell.redraw()

    def close_panel(self) -> None:
        if self.panel_open:
            self.panel_open = False
            self.shell.redraw()

    def ensure_read(self) -> None:
        """Read the level the first time anything shows it."""
        if not self._read_once:
            self._read_once = True
            self.refresh()

    def refresh(self) -> None:
        """Read the level, off the interface thread when there is one."""
        registry = self._registry()
        if registry is None:
            self.shell.redraw()
            return

        def read() -> None:
            result = registry.invoke("audio.get_volume")

            def apply() -> None:
                # A read that lands mid-drag must not yank the slider back.
                if self._busy or self._want is not None:
                    return
                if result.ok:
                    self.level = float(result.value)
                    self.error = ""
                else:
                    self.error = result.error
                self.shell.redraw()
            self._post()(apply)

        if getattr(self.shell, "call_on_main", None) is None:
            read()
        else:
            threading.Thread(target=read, daemon=True,
                             name="cdos-volume-read").start()

    def set(self, level: float) -> None:
        """Ask for *level*. Shown at once; applied as soon as possible."""
        level = round(max(0.0, min(1.0, float(level))), 2)
        self.level = level
        registry = self._registry()
        if registry is None:
            self.shell.redraw()
            return
        with self._lock:
            self._want = level
            if self._busy:
                return              # the running worker will pick it up
            self._busy = True
        if getattr(self.shell, "call_on_main", None) is None:
            self._apply(registry)
        else:
            threading.Thread(target=self._apply, args=(registry,),
                             daemon=True, name="cdos-volume-set").start()
        self.shell.redraw()

    def nudge(self, direction: int) -> None:
        """One step up or down from where it is now."""
        if self.level is None:
            self.refresh()
            if self.level is None:
                return
        steps = round(self.level / self.STEP) + (1 if direction > 0 else -1)
        self.set(steps * self.STEP)

    def _apply(self, registry) -> None:
        while True:
            with self._lock:
                want = self._want
                self._want = None
                if want is None:
                    self._busy = False
                    return
            result = registry.invoke("audio.set_volume", level=want)

            def report(result=result) -> None:
                self.error = "" if result.ok else result.error
                if not result.ok:
                    self.shell.toast(f"VOLUME: {result.error}"[:90])
                self.shell.redraw()
            self._post()(report)

    # ------------------------------------------------------------------
    def label(self) -> str:
        """What the spot and the slider say the level is."""
        if self.level is None:
            return "--" if self.error else "..."
        return f"{self.level * 100:.0f}%"
