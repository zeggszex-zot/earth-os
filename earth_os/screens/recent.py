"""Recently used programs — start again what you were just using.

Sits beside RUNNING PROGRAMS and answers the neighbouring question: that one
lists what is open now, this lists what you keep opening.  Ranked by frecency
rather than plain recency, so the order is stable enough to build muscle
memory against while still following your habits.

Entries can be pinned, which floats them above the ranking permanently — the
escape hatch for the thing you use every morning but which the maths would
otherwise let drift down.
"""

from __future__ import annotations

from typing import List, Optional

from ..recent import RecentEntry
from ..widgets import (
    BarRun, HalfPillButton, RenderContext, StandardButton, TextButton, draw_text,
)
from .base import Frame, Screen


class RecentScreen(Screen):
    name = "recent"
    title = "RECENT"

    COLORS = ("accent2", "accent3", "accent5", "accent4", "accent6")

    def __init__(self, app):
        super().__init__(app)
        self._signature: List[str] = []

    # ------------------------------------------------------------------
    def on_enter(self) -> None:
        self._signature = self._current_signature()
        self.invalidate()

    def _current_signature(self) -> List[str]:
        store = self.app.recent
        if store is None:
            return []
        return [e.command for e in store.top(self._row_budget())]

    def _row_budget(self) -> int:
        """How many entries to ask the store for.

        Generous on purpose: the screen works out how many rows actually fit
        from the frame height and slices, so this only has to be at least as
        large as the tallest screen can show.
        """
        return 28

    def tick(self) -> None:
        signature = self._current_signature()
        if signature != self._signature:
            self._signature = signature
            self.invalidate()
            self.app.redraw()

    # ------------------------------------------------------------------
    def build(self, ctx: RenderContext, f: Frame) -> None:
        u = ctx.u
        store = self.app.recent
        self.add_chrome(ctx, f, title=ctx.strings.get("myRecent", "RECENT"))
        cx, cy, cw, ch = f.content

        if store is None:
            self.notice_rect = (cx, cy + u(2.4), cw, ch - u(3.0))
            return

        entries = store.top(self._row_budget())
        self.g.add(TextButton(
            "hdr", (cx, cy, cw - u(9.6), u(1.3)),
            label=f"Recently used · {len(entries)}",
            color="accent3", label_color="accent4",
        ))
        self.g.add(StandardButton(
            "clear", (cx + cw - u(9.0), cy, u(9.0), u(1.3)),
            label="CLEAR", label_align="center", color="accent6",
            on_click=self.clear, tooltip="forget everything unpinned",
        ))

        if not entries:
            self.notice_rect = (cx, cy + u(2.4), cw, ch - u(3.0))
            return
        self.notice_rect = None

        list_y = cy + u(2.2)
        btn_h = u(1.9)
        gap = u(0.25)
        pin_w = u(5.0)
        forget_w = u(7.0)
        rows = max(int((ch - u(4.0)) // (btn_h + gap)), 1)

        for i, entry in enumerate(entries[:rows]):
            y = list_y + i * (btn_h + gap)
            colour = "accent4" if entry.pinned else self.COLORS[i % len(self.COLORS)]
            label = f"{entry.name}   ·   {entry.age_label()}"
            self.g.add(HalfPillButton(
                f"run_{i}", (cx, y, cw - pin_w - forget_w - gap * 2, btn_h),
                side="left", label=label, label_align="right", color=colour,
                on_click=(lambda e=entry: self.start(e)),
                tooltip=f"{entry.command}  ({entry.count}x)",
            ))
            self.g.add(StandardButton(
                f"pin_{i}", (cx + cw - pin_w - forget_w - gap, y, pin_w, btn_h),
                label="PIN" if not entry.pinned else "UNPIN", label_align="center",
                color="accent4" if entry.pinned else "accent2",
                on_click=(lambda e=entry: self.pin(e)),
                tooltip="keep this at the top",
            ))
            self.g.add(StandardButton(
                f"forget_{i}", (cx + cw - forget_w, y, forget_w, btn_h),
                label="FORGET", label_align="center", color="accent6",
                on_click=(lambda e=entry: self.forget(e)),
            ))

        self.g.add(BarRun(
            "recent_bar", (cx, cy + ch - u(1.1), cw, u(1.0)),
            weights=(2, 1, 3, 1, 2, 1.5), cap_left=True, cap_right=True,
        ))

    # ------------------------------------------------------------------
    def start(self, entry: RecentEntry) -> None:
        self.app.play("ack")
        self.app.launch(entry.command, name=entry.name)

    def pin(self, entry: RecentEntry) -> None:
        store = self.app.recent
        if store is None:
            return
        pinned = store.toggle_pin(entry.command)
        store.save()
        self.app.status_message = (f"pinned {entry.name}" if pinned
                                   else f"unpinned {entry.name}")
        self.invalidate()
        self.app.redraw()

    def forget(self, entry: RecentEntry) -> None:
        store = self.app.recent
        if store is None:
            return
        store.forget(entry.command)
        store.save()
        self.app.status_message = f"forgot {entry.name}"
        self.invalidate()
        self.app.redraw()

    def clear(self) -> None:
        store = self.app.recent
        if store is None:
            return
        store.clear()
        store.save()
        self.app.status_message = "recent list cleared"
        self.invalidate()
        self.app.redraw()

    # ------------------------------------------------------------------
    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        rect = getattr(self, "notice_rect", None)
        if rect is None:
            return
        u = ctx.u
        x, y, w, h = rect
        store = self.app.recent

        if store is None:
            draw_text(cr, ctx, "RECENT TRACKING DISABLED", x + w / 2, y + h * 0.4,
                      u(1.3), ctx.color("accent5"), align="center", valign="middle")
            return

        draw_text(cr, ctx, "NOTHING USED YET", x + w / 2, y + h * 0.4,
                  u(1.3), ctx.color("accent5"), align="center", valign="middle")
        draw_text(cr, ctx,
                  "Programs you start from this shell appear here, "
                  "most-used first.",
                  x + w / 2, y + h * 0.4 + u(2.2), u(0.72),
                  ctx.ink("accent3"), align="center", valign="middle",
                  max_width=w * 0.7, wrap=True, line_height=1.4)
        draw_text(cr, ctx, store.describe(), x + w / 2, y + h * 0.4 + u(4.6),
                  u(0.66), ctx.ink("accent3"), align="center", valign="middle")
