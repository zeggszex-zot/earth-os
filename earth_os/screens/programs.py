"""Program index — the port of the start menu and PERSONAL PROGRAMS panel.

Reads freedesktop ``.desktop`` entries from the standard XDG application
directories, which is the Linux equivalent of the original's Start Menu walk,
and merges in any ``[[programs]]`` blocks from ``config.toml``.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..widgets import (
    ArrowButton, HalfPillButton, Label, RenderContext, StandardButton, TextButton,
)
from .base import Frame, Screen

FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")


@dataclass
class Program:
    name: str
    command: str
    categories: str = ""
    comment: str = ""
    source: str = "desktop"


def _parse_desktop(path: Path) -> Optional[Program]:
    """Minimal .desktop reader: the Desktop Entry group only."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    in_entry = False
    data: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
            continue
        if not in_entry or "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        data.setdefault(key.strip(), value.strip())
    if data.get("Type") != "Application":
        return None
    if data.get("NoDisplay", "").lower() == "true":
        return None
    if data.get("Hidden", "").lower() == "true":
        return None
    name = data.get("Name") or path.stem
    command = FIELD_CODES.sub("", data.get("Exec", "")).strip()
    if not command:
        return None
    if data.get("Terminal", "").lower() == "true":
        term = os.environ.get("TERMINAL", "xterm")
        command = f"{term} -e {command}"
    return Program(name=name, command=command,
                   categories=data.get("Categories", ""),
                   comment=data.get("Comment", ""))


def discover_programs(extra: Optional[List[Dict[str, object]]] = None) -> List[Program]:
    dirs: List[Path] = []
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    for base in [data_home, *data_dirs.split(":")]:
        candidate = Path(base) / "applications"
        if candidate.is_dir():
            dirs.append(candidate)

    seen: Dict[str, Program] = {}
    for directory in dirs:
        for entry in sorted(directory.glob("*.desktop")):
            program = _parse_desktop(entry)
            if program and program.name.lower() not in seen:
                seen[program.name.lower()] = program

    for item in (extra or []):
        label = str(item.get("label") or item.get("name") or "")
        command = str(item.get("exec") or item.get("command") or "")
        if label and command:
            seen[label.lower()] = Program(name=label, command=command,
                                          source="config")
    return sorted(seen.values(), key=lambda p: p.name.lower())


class ProgramsScreen(Screen):
    name = "programs"
    title = "PROGRAM INDEX"

    COLORS = ("accent2", "accent3", "accent5", "accent4", "accent6")

    def __init__(self, app):
        super().__init__(app)
        self.programs: List[Program] = []
        self.page = 0
        self.per_page = 1
        self.loaded = False

    def on_enter(self) -> None:
        if not self.loaded:
            self.reload()

    def reload(self) -> None:
        self.programs = discover_programs(self.app.config.programs())
        self.loaded = True
        self.page = 0
        self.invalidate()

    # ------------------------------------------------------------------
    def build(self, ctx: RenderContext, f: Frame) -> None:
        u = ctx.u
        if not self.loaded:
            self.programs = discover_programs(self.app.config.programs())
            self.loaded = True
        self.add_chrome(ctx, f, title=ctx.strings["myStartMenu"])
        cx, cy, cw, ch = f.content

        self.g.add(TextButton(
            "hdr", (cx, cy, cw, u(1.3)),
            label=ctx.strings["myUserButtons"], color="accent3",
            label_color="accent4",
        ))

        grid_y = cy + u(2.0)
        grid_h = ch - u(4.6)
        cols = 3 if cw > u(30) else 2
        btn_h = u(2.0)
        gap = u(0.35)
        rows = max(int((grid_h + gap) // (btn_h + gap)), 1)
        self.per_page = rows * cols
        col_w = (cw - gap * (cols - 1)) / cols

        start = self.page * self.per_page
        chunk = self.programs[start:start + self.per_page]
        for i, program in enumerate(chunk):
            row, col = divmod(i, cols)
            x = cx + col * (col_w + gap)
            y = grid_y + row * (btn_h + gap)
            self.g.add(HalfPillButton(
                f"prog_{start + i}", (x, y, col_w, btn_h), side="left",
                label=program.name, label_align="right",
                color=self.COLORS[(start + i) % len(self.COLORS)],
                tooltip=program.command,
                on_click=(lambda c=program.command, n=program.name:
                          self.app.launch(c, name=n)),
            ))

        pages = max(1, (len(self.programs) + self.per_page - 1) // self.per_page)
        nav_y = cy + ch - u(2.2)
        self.g.add(ArrowButton(
            "prev", (cx, nav_y, u(3.2), u(2.0)), direction="left",
            color="accent4", on_click=lambda: self.turn(-1),
        ))
        self.g.add(ArrowButton(
            "next", (cx + u(3.6), nav_y, u(3.2), u(2.0)), direction="right",
            color="accent4", on_click=lambda: self.turn(1),
        ))
        self.g.add(Label(
            "pageno", (cx + u(7.4), nav_y, cw - u(7.4), u(2.0)),
            text=f"PAGE {self.page + 1} OF {pages}   ·   {len(self.programs)} PROGRAMS",
            size=u(0.8), color="accent5", align="left", valign="middle",
        ))

    def turn(self, delta: int) -> None:
        pages = max(1, (len(self.programs) + self.per_page - 1) // self.per_page)
        self.page = max(0, min(self.page + delta, pages - 1))
        self.invalidate()
        self.app.redraw()

    def on_scroll(self, x: float, y: float, dy: float) -> bool:
        self.turn(1 if dy > 0 else -1)
        return True

    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        """Report what happened to the last launch, here where it was pressed."""
        if self.frame is None or not self.app.status_message:
            return
        from ..widgets import draw_text
        cx, cy, cw, ch = self.frame.content
        message = self.app.status_message
        bad = any(word in message.lower() for word in
                  ("fail", "not installed", "exited", "cannot"))
        draw_text(cr, ctx, message.upper(), cx, cy + ch + ctx.u(0.1),
                  ctx.u(0.66),
                  ctx.color("alert" if bad else "accent5"),
                  align="left", valign="bottom", max_width=cw)
