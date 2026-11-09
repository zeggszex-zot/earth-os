"""The CLAUDE CODE screen: a coding agent inside the LCARS chrome.

A view over :class:`earth_os.agent.controller.AgentController`. Type (or
say *"Claude, …"*) and Claude Code works on CDOS's own source in the
workspace, drives the running shell through the control bridge, and has every
change judged by the gate before it is kept.

What this screen owns is presentation only: the transcript, the input line,
the confirmation strip and the buttons. What is allowed, what is kept and what
needs a press are all decided below it — in the policy, the gate and the
shell's gated path.

Like the terminal, it is never a trap: Escape and the function keys are not
swallowed by the input line.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from ..widgets import (
    FlatButton, Label, RenderContext, ScrollPanel, StandardButton, TextButton,
    draw_text,
)
from .base import Frame, Screen

KIND_COLORS = {
    "user": "accent2",
    "assistant": "text",
    "tool": "accent3",
    "system": "accent4",
    "refused": "alert",
    "error": "alert",
    "gate-ok": "accent5",
    "gate-bad": "alert",
}

PREFIX = {"user": "> ", "tool": "  ▸ ", "refused": "  ✕ "}

PASSTHROUGH_KEYS = ("Escape", "F1", "F2", "F3", "F4", "F5", "F6", "F7",
                    "F8", "F11", "F12")


class ClaudeScreen(Screen):
    name = "claude"
    title = "CLAUDE CODE"

    def __init__(self, app):
        super().__init__(app)
        from ..agent.controller import AgentController
        self.agent = AgentController(app)
        # The voice path reaches the agent through the shell, not the screen.
        app.agent = self.agent
        self.input = ""
        self.cursor = 0
        self.history: List[str] = []
        self.history_index: Optional[int] = None
        self._input_rect: Tuple[float, float, float, float] = (0, 0, 0, 0)
        self._built_options: List[str] = []
        self._built_reload = False
        self._greeted = False
        self._seen = 0

    # ------------------------------------------------------------------
    def on_enter(self) -> None:
        if self._greeted:
            return
        self._greeted = True
        agent = self.agent
        agent.emit("system", "CLAUDE CODE — built into "
                   f"{self.app.config.get('shell', 'computer_name', 'CDOS')}")
        agent.emit("system", "Ask for a change to this interface, or ask the "
                             "computer to do something. Every change is "
                             "verified by the full test suite before it is "
                             "kept; anything that changes the machine waits "
                             "for your press.")
        agent.emit("system", "Try: 'make the main screen buttons larger' · "
                             "'switch to the Nemesis palette' · 'add a clock "
                             "to the engineering screen'")
        agent.emit("system", "")
        if agent.ensure_ready() and agent.state == "NOT INSTALLED":
            agent.emit("error", f"{agent.problem}. Install Claude Code "
                       "(npm install -g @anthropic-ai/claude-code) and sign "
                       "in once by running 'claude' in a terminal.")

    # ------------------------------------------------------------------
    def build(self, ctx: RenderContext, f: Frame) -> None:
        u = ctx.u
        self.add_chrome(ctx, f, title="CLAUDE CODE", subtitle="")
        x, y, w, h = f.content

        self.g.add(TextButton(
            "claude_header", (x, y, w, u(1.3)),
            label=self.agent.status_line(),
            color="accent3", label_color="accent4",
        ))

        options = list(self.app.pending_options)
        self._built_options = [label for label, _ in options]
        self._built_reload = self.agent.reload_ready

        controls_h = u(1.8)
        input_h = u(1.9)
        choice_h = u(3.2) if options else 0.0
        panel_y = y + u(1.3) + f.gap
        panel_h = (h - u(1.3) - f.gap - choice_h - input_h - f.gap
                   - controls_h - f.gap)

        self.panel = ScrollPanel(
            "claude_scroll", (x, panel_y, w, panel_h),
            draw_content=self._draw_lines, bar_color="accent3",
        )
        self.g.add(self.panel)

        below = panel_y + panel_h
        if options:
            self._add_choices(ctx, options, x, below, w, choice_h)
            below += choice_h

        input_y = below + f.gap
        self._input_rect = (x, input_y, w, input_h)
        self.g.add(FlatButton(
            "claude_inputplate", (x, input_y, w, input_h),
            color="accent6", label="", enabled=False,
        ))

        cy = input_y + input_h + f.gap
        gap = u(0.4)
        buttons = [
            ("claude_new", "NEW SESSION", "accent4", self.agent.new_conversation, True),
            ("claude_interrupt", "INTERRUPT", "alert", self.agent.interrupt, True),
            ("claude_history", "HISTORY", "accent3", self.agent.history, True),
            ("claude_undo", "UNDO LAST CHANGE", "accent2", self.agent.offer_undo, True),
            ("claude_reload", "RELOAD INTERFACE", "accent5",
             self.agent.offer_reload, self.agent.reload_ready),
        ]
        bw = (w - gap * (len(buttons) - 1)) / len(buttons)
        for i, (bname, label, color, callback, enabled) in enumerate(buttons):
            self.g.add(StandardButton(
                bname, (x + i * (bw + gap), cy, bw, controls_h),
                label=label, label_align="center", color=color,
                on_click=callback, enabled=enabled,
            ))

    def _add_choices(self, ctx: RenderContext, options, x: float, y: float,
                     w: float, h: float) -> None:
        """The shell's confirmation strip, shown here too so a request made
        from this screen is answered on this screen."""
        u = ctx.u
        self.g.add(Label(
            "claude_choice_prompt", (x, y + u(0.1), w, u(1.0)),
            text=self.app.pending_prompt or "WHICH DID YOU MEAN?",
            size=u(0.68), color="accent4", align="left", valign="middle",
        ))
        row_y = y + u(1.2)
        row_h = h - u(1.3)
        gap = u(0.4)
        count = max(len(options), 1)
        bw = min((w - gap * (count - 1)) / count, u(14))
        for i, (label, callback) in enumerate(options):
            color = ("alert" if label in ("CONFIRM", "ALLOW") else
                     "accent6" if label in ("CANCEL", "DENY") else "accent3")
            self.g.add(StandardButton(
                f"claude_choice_{i}", (x + i * (bw + gap), row_y, bw, row_h),
                label=label, label_align="center", color=color,
                on_click=callback,
            ))

    # ------------------------------------------------------------------
    def _draw_lines(self, cr, ctx: RenderContext, x: float, y: float,
                    w: float, offset: float) -> float:
        u = ctx.u
        size = u(0.74)
        pad = u(0.4)
        lines = self.agent.snapshot()
        if not lines:
            return u(1.0)
        cursor = y + pad
        panel_h = self.panel.h if hasattr(self, "panel") else 0.0
        line_h = size * 1.45
        for kind, text in lines:
            if not text:
                cursor += line_h * 0.6
                continue
            color = ctx.color(KIND_COLORS.get(kind, "accent5"))
            shown = PREFIX.get(kind, "") + text
            # Lines far outside the view are measured, not laid out, so a long
            # session does not make the panel stutter.
            if panel_h and (cursor > y + offset + panel_h + line_h * 4
                            or cursor + line_h * 6 < y + offset):
                chars = max(int((w - pad * 2) / (size * 0.55)), 20)
                cursor += line_h * (1 + len(shown) // chars)
                continue
            _, th = draw_text(cr, ctx, shown, x + pad, cursor, size, color,
                              align="left", valign="top",
                              max_width=w - pad * 2, wrap=True,
                              letter_spacing=0.03)
            cursor += max(th, size) + u(0.25)
        return cursor - y + pad

    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        current = [label for label, _ in self.app.pending_options]
        if current != self._built_options \
                or self.agent.reload_ready != self._built_reload:
            self.invalidate()
        header = self.g.get("claude_header")
        if header is not None:
            header.label = self.agent.status_line()  # type: ignore[attr-defined]
        self.draw_clock(cr, ctx)

        u = ctx.u
        x, y, w, h = self._input_rect
        if w <= 0:
            return
        size = u(0.7)
        pad = u(0.45)
        busy = self.agent.busy
        prompt = "···" if busy else "CLAUDE >"
        pw, _ = draw_text(cr, ctx, prompt, x + pad, y + h / 2, size,
                          ctx.color("accent6" if busy else "accent4"),
                          align="left", valign="middle")
        text_x = x + pad + pw + u(0.5)
        text_w = w - (text_x - x) - pad
        cw = size * 0.6
        visible = max(int(text_w / cw), 8)
        start = max(0, self.cursor - visible)
        shown = self.input[start:start + visible]
        if shown:
            draw_text(cr, ctx, shown, text_x, y + h / 2, size,
                      ctx.color("text"), align="left", valign="middle",
                      letter_spacing=0.0, family="monospace")
        if not busy and int(time.time() * 2) % 2 == 0:
            caret_x = text_x + (self.cursor - start) * cw
            colour = ctx.color("accent4")
            cr.save()
            cr.set_source_rgba(colour[0], colour[1], colour[2], 0.85)
            cr.rectangle(caret_x, y + h / 2 - size * 0.55, max(cw * 0.85, 2.0),
                         size * 1.1)
            cr.fill()
            cr.restore()

    def tick(self) -> None:
        count = len(self.agent.lines)
        if count != self._seen:
            self._seen = count
            panel = getattr(self, "panel", None)
            if panel is not None and panel.follow_tail:
                panel.scroll_to_end()

    # ------------------------------------------------------------------
    def submit(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.history.append(text)
        self.agent.ask(text)

    def on_key(self, keyval: int, name: str, ctrl: bool = False) -> bool:
        if name in PASSTHROUGH_KEYS:
            return False
        if ctrl:
            if name in ("c", "C"):
                if self.agent.busy:
                    self.agent.interrupt()
                else:
                    self.input, self.cursor = "", 0
                return True
            if name in ("u", "U"):
                self.input, self.cursor = self.input[self.cursor:], 0
                return True
            if name in ("a", "A"):
                self.cursor = 0
                return True
            if name in ("e", "E"):
                self.cursor = len(self.input)
                return True
            return False
        if name in ("Return", "KP_Enter"):
            line, self.input, self.cursor = self.input, "", 0
            self.history_index = None
            self.submit(line)
            return True
        if name == "BackSpace":
            if self.cursor:
                self.input = self.input[:self.cursor - 1] + self.input[self.cursor:]
                self.cursor -= 1
            return True
        if name == "Delete":
            self.input = self.input[:self.cursor] + self.input[self.cursor + 1:]
            return True
        if name == "Left":
            self.cursor = max(0, self.cursor - 1)
            return True
        if name == "Right":
            self.cursor = min(len(self.input), self.cursor + 1)
            return True
        if name in ("Home", "KP_Home"):
            self.cursor = 0
            return True
        if name in ("End", "KP_End"):
            self.cursor = len(self.input)
            return True
        if name in ("Up", "Down"):
            self._recall(-1 if name == "Up" else 1)
            return True
        if name in ("Page_Up", "Prior", "Page_Down", "Next"):
            panel = getattr(self, "panel", None)
            if panel is not None:
                sign = -1 if name in ("Page_Up", "Prior") else 1
                panel.scroll_by(sign * panel.h * 0.8)
            return True
        if 0x20 <= keyval <= 0x7E:
            ch = chr(keyval)
            self.input = self.input[:self.cursor] + ch + self.input[self.cursor:]
            self.cursor += 1
            return True
        return False

    def _recall(self, direction: int) -> None:
        if not self.history:
            return
        if self.history_index is None:
            if direction > 0:
                return
            self.history_index = len(self.history) - 1
        else:
            self.history_index += direction
        if self.history_index >= len(self.history):
            self.history_index = None
            self.input = ""
        else:
            self.history_index = max(0, self.history_index)
            self.input = self.history[self.history_index]
        self.cursor = len(self.input)

    def on_scroll(self, x: float, y: float, dy: float) -> bool:
        panel = self.g.get("claude_scroll")
        if panel is not None and panel.contains(x, y):
            panel.scroll_by(dy * (self.frame.ctx.u(2.0) if self.frame else 30))
            return True
        return False
