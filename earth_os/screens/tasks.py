"""Running programs — switch to, fullscreen or close another window.

A fullscreen shell that cannot reach the programs it launched is a trap, so
this is the way back out. What it can actually do depends entirely on the
compositor, and when the answer is "nothing" the screen says so and explains
the fix rather than showing an empty list.
"""

from __future__ import annotations

import time  # noqa: F401  (kept for subclass use)
from typing import List, Optional

from ..widgets import (
    ArrowButton, BarRun, HalfPillButton, Label, RenderContext, StandardButton,
    TextButton, draw_text,
)
from ..windows import Window
from .base import Frame, Screen


class TasksScreen(Screen):
    name = "tasks"
    title = "RUNNING PROGRAMS"

    COLORS = ("accent2", "accent3", "accent5", "accent4", "accent6")

    def __init__(self, app):
        super().__init__(app)
        self.selected: Optional[Window] = None
        self._signature: List[str] = []
        self._next_poll = 0.0

    # ------------------------------------------------------------------
    def on_enter(self) -> None:
        self.app.windows.refresh()
        self._signature = self._current_signature()
        self.invalidate()

    def _current_signature(self) -> List[str]:
        return [w.handle for w in self.app.windows.windows]

    def _get_sorted_windows(self) -> List[Window]:
        """Return windows sorted with most recently focused on top."""
        windows = self.app.windows.windows
        # Put focused window first if one exists
        focused = [w for w in windows if w.focused]
        unfocused = [w for w in windows if not w.focused]
        return focused + unfocused

    def tick(self) -> None:
        """Redraw when the window list moves.

        The poll itself runs on the frame clock for every screen now; this
        only notices the result, because the row list below is this screen's
        own and not part of the chrome strip.
        """
        signature = self._current_signature()
        if signature != self._signature:
            self._signature = signature
            self.invalidate()
            self.app.redraw()

    # ------------------------------------------------------------------
    def build(self, ctx: RenderContext, f: Frame) -> None:
        u = ctx.u
        manager = self.app.windows
        self.add_chrome(ctx, f, title=ctx.strings.get("myTasks", "RUNNING PROGRAMS"))
        cx, cy, cw, ch = f.content

        self.g.add(TextButton(
            "hdr", (cx, cy, cw, u(1.3)),
            label=f"Windows · {len(manager.windows)}",
            color="accent3", label_color="accent4",
        ))

        if not manager.available:
            self.notice_rect = (cx, cy + u(2.4), cw, ch - u(3.0))
            return
        self.notice_rect = None

        windows = self._get_sorted_windows()
        if not windows:
            self.notice_rect = (cx, cy + u(2.4), cw, ch - u(3.0))
            return

        list_y = cy + u(2.2)
        btn_h = u(1.9)
        gap = u(0.25)
        close_w = u(9.0)
        home_w = u(7.0)
        rows = max(int((ch - u(4.0)) // (btn_h + gap)), 1)

        for i, window in enumerate(windows[:rows]):
            y = list_y + i * (btn_h + gap)
            colour = "accent4" if window.focused else self.COLORS[i % len(self.COLORS)]
            self.g.add(HalfPillButton(
                f"win_{i}", (cx, y, cw - close_w - home_w - gap * 2, btn_h),
                side="left",
                label=window.label, label_align="right", color=colour,
                on_click=(lambda w=window: self.switch(w)),
                tooltip=window.title,
            ))
            # HOME sits beside CLOSE because the two things you want from a
            # program that has taken the screen are "get rid of it" and "give
            # me the interface back", and only one of those existed.
            self.g.add(StandardButton(
                f"home_{i}", (cx + cw - close_w - home_w - gap, y, home_w, btn_h),
                label=ctx.strings.get("myHome", "HOME"), label_align="center",
                color="accent2", tooltip="back to the main screen",
                on_click=(lambda w=window: self.home(w)),
            ))
            self.g.add(StandardButton(
                f"close_{i}", (cx + cw - close_w, y, close_w, btn_h),
                label="CLOSE", label_align="center", color="accent6",
                on_click=(lambda w=window: self.close(w)),
            ))

        self.g.add(BarRun(
            "tasks_bar", (cx, cy + ch - u(1.1), cw, u(1.0)),
            weights=(2, 1, 3, 1, 2, 1.5), cap_left=True, cap_right=True,
        ))

    # ------------------------------------------------------------------
    def switch(self, window: Window) -> None:
        ok, message = self.app.windows.switch_to(window)
        self.app.status_message = message
        if ok:
            self.app.play("ack")
            # Hand the screen over; the other program is now in front.
            self.app.minimise_for(window)
        else:
            self.app.say(message, speak=False)
        self.app.redraw()

    def home(self, window: Window) -> None:
        """Take a program off the whole screen and show the main screen.

        A program at true fullscreen covers the shell, so this puts it back
        inside the frame (or simply out of fullscreen when frame mode is
        unavailable) before raising the shell — otherwise "home" would mean
        going somewhere you still cannot see.
        """
        self.app.play("ack")
        self.app.return_home(window)

    def close(self, window: Window) -> None:
        ok, message = self.app.windows.close(window)
        self.app.status_message = message
        self.app.windows.refresh()
        self.invalidate()
        self.app.redraw()

    # ------------------------------------------------------------------
    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        rect = getattr(self, "notice_rect", None)
        if rect is None:
            return
        u = ctx.u
        x, y, w, h = rect
        manager = self.app.windows

        if manager.available:
            draw_text(cr, ctx, "NO OTHER PROGRAMS RUNNING", x + w / 2, y + h * 0.4,
                      u(1.3), ctx.color("accent5"), align="center", valign="middle")
            draw_text(cr, ctx, f"watching via {manager.backend.name}",
                      x + w / 2, y + h * 0.4 + u(2.0), u(0.7),
                      ctx.ink("accent3"), align="center", valign="middle")
            return

        draw_text(cr, ctx, "WINDOW CONTROL UNAVAILABLE", x + w / 2, y + u(1.5),
                  u(1.3), ctx.color("alert"), align="center", valign="middle")
        draw_text(cr, ctx, manager.backend.status(), x + w / 2, y + u(4.0),
                  u(0.78), ctx.color("accent5"), align="center", valign="top",
                  max_width=w * 0.8, wrap=True, line_height=1.4)
        draw_text(cr, ctx,
                  "A Wayland program cannot see another program's windows. "
                  "Only the compositor can, so this needs one that offers it.",
                  x + w / 2, y + u(9.0), u(0.68), ctx.ink("accent3"),
                  align="center", valign="top", max_width=w * 0.72,
                  wrap=True, line_height=1.4)
