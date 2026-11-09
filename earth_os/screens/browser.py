"""File browser — the port of ``LCARSexplorer.exe``.

Directories and files in one selectable list, with an LCARS path header, paging
arrows and open-with-the-desktop-handler on activation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Optional

from .. import sysinfo
from ..widgets import (
    ArrowButton, Label, ListPanel, RenderContext, StandardButton, TextButton,
)
from .base import Frame, Screen

SKIP_HIDDEN = True


class BrowserScreen(Screen):
    name = "browser"
    title = "COMPUTER CORE"

    def __init__(self, app):
        super().__init__(app)
        self.path = Path.home()
        self.entries: List[Path] = []
        self.error = ""
        self.message = ""
        self.message_ok = True
        self.list: Optional[ListPanel] = None

    # ------------------------------------------------------------------
    def open(self, path: str | Path) -> None:
        target = Path(os.path.expanduser(str(path)))
        if target.is_file():
            target = target.parent
        self.path = target
        self.refresh()

    def refresh(self) -> None:
        self.error = ""
        try:
            items = sorted(
                (p for p in self.path.iterdir()
                 if not (SKIP_HIDDEN and p.name.startswith("."))),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError as exc:
            items = []
            self.error = f"access denied: {exc.strerror or exc}"
        self.entries = items
        if self.list is not None:
            self.list.items = [self._label(p) for p in items]
            self.list.selected = -1
            self.list.offset = 0
        self.app.redraw()

    def _label(self, path: Path) -> str:
        if path.is_dir():
            return f"[ {path.name} ]"
        try:
            size = sysinfo.human_bytes(path.stat().st_size)
        except OSError:
            size = "-"
        return f"{path.name}    ·  {size}"

    # ------------------------------------------------------------------
    def build(self, ctx: RenderContext, f: Frame) -> None:
        u = ctx.u
        self.add_chrome(ctx, f, title=ctx.strings["myComp"])
        cx, cy, cw, ch = f.content

        self.g.add(TextButton(
            "path_hdr", (cx, cy, cw, u(1.3)),
            label=str(self.path), color="accent3", label_color="accent4",
        ))

        nav_w = u(5.2)
        self.g.add(StandardButton(
            "up", (cx, cy + u(1.9), nav_w, u(1.6)), label="UP",
            label_align="center", color="accent4", on_click=self.go_up,
        ))
        self.g.add(StandardButton(
            "home", (cx + nav_w + u(0.4), cy + u(1.9), nav_w, u(1.6)),
            label="HOME", label_align="center", color="accent2",
            on_click=lambda: self.open(Path.home()),
        ))
        self.g.add(StandardButton(
            "root", (cx + (nav_w + u(0.4)) * 2, cy + u(1.9), nav_w, u(1.6)),
            label="ROOT", label_align="center", color="accent3",
            on_click=lambda: self.open("/"),
        ))
        self.g.add(StandardButton(
            "openit", (cx + cw - nav_w, cy + u(1.9), nav_w, u(1.6)),
            label="OPEN", label_align="center", color="accent5",
            on_click=self.activate_selected,
        ))

        list_y = cy + u(4.0)
        list_h = ch - u(4.4)
        arrow_w = u(2.4)
        self.list = ListPanel(
            "entries", (cx, list_y, cw - arrow_w - u(0.4), list_h),
            items=[self._label(p) for p in self.entries],
            row_units=1.45,
        )
        self.g.add(self.list)
        self.g.add(ArrowButton(
            "page_up", (cx + cw - arrow_w, list_y, arrow_w, u(3.0)),
            direction="up", color="accent4",
            on_click=lambda: self._page(-1, ctx),
        ))
        self.g.add(ArrowButton(
            "page_down", (cx + cw - arrow_w, list_y + list_h - u(3.0), arrow_w, u(3.0)),
            direction="down", color="accent4",
            on_click=lambda: self._page(1, ctx),
        ))
        if not self.entries:
            self.refresh()

    # ------------------------------------------------------------------
    def _page(self, delta: int, ctx: RenderContext) -> None:
        if self.list is not None:
            self.list.page(delta, ctx)
            self.app.redraw()

    def go_up(self) -> None:
        if self.path.parent != self.path:
            self.open(self.path.parent)

    def activate_selected(self) -> None:
        if self.list is None or not (0 <= self.list.selected < len(self.entries)):
            self.message = "select a file first"
            self.message_ok = False
            self.app.redraw()
            return
        target = self.entries[self.list.selected]
        if target.is_dir():
            self.open(target)
            return
        # open_path passes the path as its own argv element, so spaces and
        # quotes in a filename survive; launch() would have split them.
        #
        # It answers twice: at once that it is trying, and again — here, on
        # this screen — with what actually happened. The first answer alone
        # used to be reported as the result, so a file nothing could open
        # still said "opening" and then simply did not.
        before = ({w.handle for w in self.app.windows.windows}
                  if self.app.windows.available else set())

        def done(ok: bool, message: str) -> None:
            self.message = message
            self.message_ok = ok
            self.app.play("ack" if ok else "beep")
            if ok and self.app.windows.available:
                # Give the newly opened program the screen.
                self.app.hand_over_soon(target.name, before=before)
            self.app.redraw()

        self.message = f"opening {target.name}..."
        self.message_ok = True
        self.app.open_path(str(target), on_done=done)
        self.app.redraw()

    # ------------------------------------------------------------------
    def on_release(self, x: float, y: float, pressed) -> None:
        if pressed is self.list and self.list is not None and self.frame is not None:
            row = self.list.row_at(x, y, self.frame.ctx)
            if row >= 0:
                self.app.play("beep")
                if self.list.selected == row:
                    self.activate_selected()
                else:
                    self.list.selected = row
                self.app.redraw()
            return
        super().on_release(x, y, pressed)

    def on_scroll(self, x: float, y: float, dy: float) -> bool:
        if self.list is not None and self.list.contains(x, y) and self.frame is not None:
            step = 3 if dy > 0 else -3
            self.list.offset = max(
                0, min(self.list.offset + step,
                       max(0, len(self.list.items) - self.list.visible_rows(self.frame.ctx))))
            return True
        return False

    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        from ..widgets import draw_text

        header = self.g.get("path_hdr")
        if header is not None:
            header.label = str(self.path)  # type: ignore[attr-defined]
        if self.frame is None:
            return
        cx, cy, cw, ch = self.frame.content

        if self.error:
            draw_text(cr, ctx, self.error.upper(), cx + cw / 2, cy + ch / 2,
                      ctx.u(1.1), ctx.color("alert"), align="center", valign="middle")

        # Say what happened, here, where the press happened — not in the main
        # screen's transcript where it cannot be seen from this screen.
        if self.message:
            draw_text(cr, ctx, self.message.upper(), cx, cy + ch - ctx.u(0.2),
                      ctx.u(0.72),
                      ctx.color("accent5" if self.message_ok else "alert"),
                      align="left", valign="bottom", max_width=cw)
