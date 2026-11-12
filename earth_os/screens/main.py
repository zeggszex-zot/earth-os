"""The main shell screen.

Keeps the button inventory and the control names of the original
``frmMainscreen1`` — the ``.lng`` keys still drive the labels — but gives the
centre of the screen to the computer conversation rather than to a wallpaper,
since on this machine the computer is the point.
"""

from __future__ import annotations

from typing import List, Tuple

from .. import sysinfo
from ..chrome import ClockBlock
from ..widgets import (
    HalfPillButton, Label, LevelIndicator, RenderContext, ScrollPanel,
    StandardButton, TextButton, draw_text,
)
from .base import Frame, Screen

#: (.lng key, action name, palette slot) for the left column, in the order the
#: original listed them.
COLUMN: List[Tuple[str, str, str]] = [
    ("myStartMenu", "menu", "accent4"),
    ("myComp", "my computer", "accent2"),
    ("mySettings", "specify parameters", "accent3"),
    ("myEngineering", "engineering", "accent2"),
    ("myComms", "communications", "accent5"),
    ("myTerminal", "terminal", "accent5"),
    ("myClaude", "claude", "accent4"),
    ("myDocuments", "my documents", "accent3"),
    ("myPictures", "my pictures", "accent2"),
    ("myUserButtons", "programs", "accent4"),
    ("myRecent", "recent", "accent3"),
    ("myTasks", "programs running", "accent5"),
    ("myScreenshot", "screen shot", "accent2"),
    ("mySpeech", "speech", "accent3"),
    # EXIT asks how to leave: log out, shut down or reboot.
    ("myExit", "exit options", "accent6"),
]


#: Screens that show the home column instead of BACK / HOME / SCREEN SHOT,
#: keyed by screen name, with the column action that is "you are here".
HOME_COLUMN_SCREENS = {"main": None, "engineering": "engineering"}


def add_command_column(screen: Screen, ctx: RenderContext, f: Frame) -> None:
    """The home screen's side column, on whichever screen asks for it.

    One function, so a screen that keeps the column keeps *this* column: the
    same buttons in the same places, not a copy that drifts. The entry for
    the screen being shown is lit, and pressing it goes home — the column has
    no HOME of its own, and the way back should be where the eye already is.
    """
    u = ctx.u
    strings = ctx.strings
    app = screen.app
    here = HOME_COLUMN_SCREENS.get(screen.name)

    col_x = f.margin + u(0.3)
    col_w = f.col_w - u(0.6)
    # Below the square box at the head of the column.
    top = f.column_top
    # Down to the foot of the screen: the last button's bottom edge sits on
    # the frame's bottom, level with the foot of the clock opposite.
    bottom = f.bottom
    count = len(COLUMN)
    btn_h = min((bottom - top) / count * 0.80, u(1.9))
    pitch = (bottom - top - btn_h) / max(count - 1, 1)
    for i, (key, action, color) in enumerate(COLUMN):
        y = top + i * pitch
        current = here is not None and action == here
        screen.g.add(HalfPillButton(
            key, (col_x, y, col_w, btn_h), side="left",
            label=strings[key], color="select" if current else color,
            label_align="right",
            on_click=((lambda: app.go("main")) if current
                      else (lambda a=action: app.run_action(a))),
            tooltip="home" if current else action,
        ))


class MainScreen(Screen):
    name = "main"
    #: A clock ticking seconds and three hardware gauges.
    live = True
    title = "MAIN"

    def __init__(self, app):
        super().__init__(app)
        self.monitor: sysinfo.SystemMonitor = app.monitor

    # ------------------------------------------------------------------
    def build(self, ctx: RenderContext, f: Frame) -> None:
        u = ctx.u
        strings = ctx.strings
        name = self.app.config.get("shell", "computer_name", "EARTH OS")
        operator = self.app.operator_name()

        self.add_chrome(ctx, f,
                        title=f"{name} · {operator}" if operator else name,
                        subtitle="", back=False)

        # -- left column ------------------------------------------------
        add_command_column(self, ctx, f)
        bottom = f.bottom
        clock_x, clock_y, clock_w, clock_h = f.clock

        # -- right rail: gauges, live numbers, then the voice controls ----
        #
        # The rail is the whole status column now, which is what frees the
        # centre to be one uninterrupted rectangle. It budgets from the
        # bottom up: the voice controls and the numeric readings have fixed
        # heights they need to stay legible, and the gauges take whatever is
        # left over. Sizing the gauges first and squeezing the text is the
        # wrong way round — unreadable numbers are worse than shorter bars.
        rail_w = f.rail_w
        rail_x = f.W - f.margin - rail_w
        rail_y = f.margin + f.top_h + u(1.4)
        self.rail_x, self.rail_w = rail_x, rail_w

        gauges = [
            ("CPU", "accent2", lambda: self.monitor.cpu()),
            ("MEM", "accent3", lambda: self.monitor.memory()[0]),
            ("DSK", "accent5", lambda: self.monitor.disk()[0]),
            ("NET", "accent4", self._net_level),
        ]

        tick_h = u(0.95)                       # CPU/MEM/DSK/NET captions
        usage_head_h = u(1.15)                 # "CURRENT USAGE"
        usage_row_h = u(1.15)
        usage_h = usage_head_h + usage_row_h * len(gauges)
        listen_h = u(2.0)
        state_h = u(1.4)
        spacer = u(0.5)

        fixed = tick_h + usage_h + listen_h + state_h + spacer * 3
        # The rail stacks down to the clock, which takes the rest of the
        # column to the bottom of the screen.
        rail_bottom = clock_y - f.gap
        rail_h = max(rail_bottom - rail_y - fixed, u(4.0))

        gap = u(0.35)
        gw = (rail_w - gap * (len(gauges) - 1)) / len(gauges)
        for i, (gname, color, provider) in enumerate(gauges):
            gx = rail_x + i * (gw + gap)
            self.g.add(LevelIndicator(
                f"gauge_{gname}", (gx, rail_y, gw, rail_h),
                provider=provider, ticks=20,
                fill_color=color, empty_color=color,
            ))
            self.g.add(Label(
                f"gaugelbl_{gname}",
                (gx, rail_y + rail_h + u(0.25), gw, u(0.9)),
                text=gname, size=u(0.6), align="center", color=color,
            ))

        # Numeric readings under the bars. The bars answer "roughly how
        # loaded" at a glance; the numbers answer "how loaded exactly", which
        # a twenty-tick bar cannot.
        usage_y = rail_y + rail_h + tick_h + spacer
        self.g.add(TextButton(
            "usage_head", (rail_x, usage_y, rail_w, usage_head_h),
            label="CURRENT USAGE", color="accent3", label_color="accent4",
        ))
        for i, (gname, color, provider) in enumerate(gauges):
            ry = usage_y + usage_head_h + i * usage_row_h
            self.g.add(Label(
                f"usagelbl_{gname}", (rail_x + u(0.3), ry, rail_w * 0.45, usage_row_h),
                text=gname, size=u(0.68), color=color,
                align="left", valign="middle",
            ))
            self.g.add(Label(
                f"usageval_{gname}",
                (rail_x + rail_w * 0.45, ry, rail_w * 0.55 - u(0.3), usage_row_h),
                provider=(lambda p=provider: self._usage_text(p)),
                size=u(0.68), color="accent5",
                align="right", valign="middle", uppercase=False,
            ))

        # Voice controls at the foot of the rail, where the column ends and
        # the eye already is after reading the numbers.
        listen_y = usage_y + usage_h + spacer
        self.g.add(StandardButton(
            "listen_toggle", (rail_x, listen_y, rail_w, listen_h),
            label=strings.get("myListening", "LISTEN"), label_align="center",
            color="accent4", on_click=self.app.toggle_listening,
        ))
        self.g.add(Label(
            "status_state", (rail_x, listen_y + listen_h + u(0.2), rail_w, state_h),
            provider=self._status_text, size=u(0.78), color="accent4",
            align="center", valign="middle",
        ))

        # The clock: a block from under the voice state to the bottom of the
        # screen. Its label is filled in every frame by draw_clock.
        self.g.add(ClockBlock("chrome_clock",
                              (clock_x, clock_y, clock_w, clock_h)))

        # -- centre panel -----------------------------------------------
        cx = f.margin + f.col_w + f.gap * 2
        cy = f.margin + f.top_h + u(1.4)
        cw = rail_x - cx - f.gap * 2
        ch = bottom - cy

        framed = self.app.framed_window if self.app.frame_mode else None
        self.g.add(TextButton(
            "conv_header", (cx, cy, cw, u(1.3)),
            label=(framed.label if framed is not None
                   else strings.get("myConversation", "COMPUTER")),
            color="accent4" if framed is not None else "accent3",
            label_color="accent4",
        ))

        # The centre is now one rectangle from under its header to the bottom
        # of the frame. The status strip that used to sit below it has moved
        # to the rail, which is what makes the hole a known, stable shape
        # rather than one that changed height depending on what was showing.
        panel_y = cy + u(1.3) + f.gap
        panel_h = ch - u(1.3) - f.gap

        # Everything below the panel stacks in a fixed order: the panel, then
        # the ambiguity choices, then the status row. Each one takes its
        # height off the panel, and each is placed at the running offset —
        # getting this wrong is what made them overlap.
        #
        # The running-programs strip used to sit here, at the bottom of this
        # panel and only when something was running. It now lives in the
        # chrome along the top of every screen; see Screen.add_program_strip.
        options = list(self.app.pending_options)
        self._built_options = [label for label, _ in options]

        choice_h = u(3.2) if options else 0.0
        panel_h -= choice_h

        below = panel_y + panel_h
        if options:
            self._add_choices(ctx, options, cx, below, cw, choice_h)
            below += choice_h

        # In frame mode a program occupies the middle of the screen, so the
        # shell leaves that rectangle empty rather than drawing a transcript
        # underneath it.
        # Published every frame so the shell can tell the compositor where to
        # put a program, whether or not one is there yet.
        self.viewport_candidate = (cx, panel_y, cw, panel_h)
        self.viewport_rect = None
        if self.app.framed_window is not None and self.app.frame_mode:
            self.viewport_rect = (cx, panel_y, cw, panel_h)
            self.transcript = None
        else:
            self.transcript = ScrollPanel(
                "transcript", (cx, panel_y, cw, panel_h),
                draw_content=self._draw_transcript, bar_color="accent3",
            )
            self.g.add(self.transcript)

        # The telemetry line rides along the centre header rather than taking
        # a row of its own at the bottom, so the hole keeps its full height.
        self.g.add(Label(
            "status_detail", (cx, cy, cw - u(0.6), u(1.3)),
            provider=self._detail_text, size=u(0.6), color="accent5",
            align="right", valign="middle", uppercase=False, z=2,
        ))

    # ------------------------------------------------------------------
    def _add_choices(self, ctx: RenderContext, options, x: float, y: float,
                     w: float, h: float) -> None:
        """A prompt line and one button per reading CDOS offered.

        CDOS reports ambiguity rather than resolving it, so the shell asks
        rather than picking. A confirmation for an effectful command uses the
        same strip: nothing changes the machine without a press here.
        """
        u = ctx.u
        self.g.add(Label(
            "choice_prompt", (x, y, w, u(1.0)),
            text=self.app.pending_prompt or "WHICH DID YOU MEAN?",
            size=u(0.72), color="accent4", align="left", valign="middle",
        ))
        row_y = y + u(1.1)
        row_h = h - u(1.2)
        gap = u(0.4)
        count = max(len(options), 1)
        bw = min((w - gap * (count - 1)) / count, u(14))
        colors = ("accent2", "accent3", "accent5", "accent4", "accent6")
        for i, (label, callback) in enumerate(options):
            color = "alert" if label in ("CONFIRM", "ALLOW") else (
                "accent6" if label in ("CANCEL", "DENY")
                else colors[i % len(colors)])
            self.g.add(StandardButton(
                f"choice_{i}", (x + i * (bw + gap), row_y, bw, row_h),
                label=label, label_align="center", color=color,
                on_click=callback,
            ))

    # ------------------------------------------------------------------
    def _net_level(self) -> float:
        rx, tx = self.monitor.net_rate()
        # Log scale against a 25 MB/s ceiling so idle links still show motion.
        total = max(rx + tx, 1.0)
        import math
        return max(0.0, min(1.0, math.log10(total) / math.log10(25_000_000.0)))

    def _status_text(self) -> str:
        return self.app.voice_state_label()

    def _usage_text(self, provider) -> str:
        """One gauge's reading as a percentage, for the numeric column.

        NET is a log-scaled level rather than a true percentage, so it reads
        as a relative load figure; the byte rates stay in the telemetry line
        where there is room for units.
        """
        try:
            value = float(provider())
        except Exception:
            return "--"
        return f"{max(0.0, min(1.0, value)) * 100:.0f}%"

    def _detail_text(self) -> str:
        mon = self.monitor
        used_frac, used, total = mon.memory()
        temp = mon.temperature()
        rx, tx = mon.net_rate()
        parts = [
            f"MEM {sysinfo.human_bytes(used)}/{sysinfo.human_bytes(total)}",
            f"NET {sysinfo.human_bytes(rx)}/s",
            f"UP {sysinfo.human_duration(mon.uptime())}",
            f"PROC {mon.processes()}",
        ]
        if temp is not None:
            parts.append(f"{temp:.0f}°C")
        battery = mon.battery()
        if battery:
            parts.append(f"PWR {battery[0] * 100:.0f}% {battery[1].upper()}")
        return "   ".join(parts)

    # ------------------------------------------------------------------
    def _draw_transcript(self, cr, ctx: RenderContext, x: float, y: float,
                         w: float, offset: float) -> float:
        u = ctx.u
        entries = self.app.conversation
        if not entries:
            greeting = self.app.config.get("persona", "greeting", "")
            draw_text(cr, ctx, greeting.upper(), x + u(0.4), y + u(0.6),
                      u(0.8), ctx.color("accent5"), align="left", valign="top",
                      max_width=w - u(0.8), wrap=True)
            return u(2.0)

        cursor = y + u(0.3)
        size = u(0.74)
        for entry in entries:
            role = entry.get("role", "computer")
            text = entry.get("text", "")
            if not text:
                continue
            is_user = role == "user"
            tag = self.app.config.get("shell", "computer_name", "EARTH OS") if not is_user else "INPUT"
            color = ctx.color("accent2" if is_user else "accent4")
            tag_w = u(4.6)

            draw_text(cr, ctx, tag, x + tag_w - u(0.5), cursor, size * 0.82,
                      color, align="right", valign="top")
            body_x = x + tag_w + u(0.3)
            body_w = max(w - tag_w - u(0.9), u(4))
            _, th = draw_text(
                cr, ctx, text, body_x, cursor, size,
                ctx.color("text" if is_user else "accent5"),
                align="left", valign="top", max_width=body_w, wrap=True,
            )
            cursor += max(th, size) + u(0.55)
        return cursor - y + u(0.6)

    # ------------------------------------------------------------------
    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        # The pending options are state, not geometry, so the screen has to be
        # rebuilt when they change rather than only on resize.
        current = [label for label, _ in self.app.pending_options]
        if current != getattr(self, "_built_options", []):
            self.invalidate()
        # The window list and what is framed are state too, but they are the
        # programs strip's business now, and Screen.ensure_built watches them
        # for every screen.
        # The stacked date/time clock is chrome, shared by every screen.
        self.draw_clock(cr, ctx)

    def tick(self) -> None:
        """Nothing to do: the window poll runs on the frame clock now, for
        every screen rather than only for this one."""

    def on_enter(self) -> None:
        # However the main screen was reached — BACK, HOME, F1, Escape, a
        # word — the program in the frame comes back to the front. See
        # Shell.restore_frame for why it was behind.
        self.app.restore_frame()

    def on_leave(self) -> None:
        # The frame is a hole in this screen only; anywhere else the program
        # would cover that screen's content.
        self.app.raise_shell()

    def on_release(self, x: float, y: float, pressed) -> None:
        super().on_release(x, y, pressed)
        # A press that stayed on this screen still focused the shell, which
        # lifts it over the framed program; put the program back on top.
        if self.app.current == self.name:
            self.app.raise_framed()

    def _draw_viewport(self, cr, ctx: RenderContext) -> None:
        """Frame the rectangle a program is living in.

        The program's own window covers this area, so all that is drawn is the
        border and, along the bottom, the reminder of how to get back — the
        thing that stops a fullscreen shell from being a trap.
        """
        rect = getattr(self, "viewport_rect", None)
        if rect is None:
            return
        u = ctx.u
        x, y, w, h = rect
        window = self.app.framed_window
        colour = ctx.color("accent4")

        cr.save()
        cr.set_source_rgba(colour[0], colour[1], colour[2], 0.85)
        cr.set_line_width(max(u(0.12), 2.0))
        cr.rectangle(x, y, w, h)
        cr.stroke()
        cr.restore()

        # The program's name goes in the panel header, not here: anything
        # drawn inside this rectangle is hidden behind the program itself.

    def on_scroll(self, x: float, y: float, dy: float) -> bool:
        panel = self.g.get("transcript")
        if panel is not None and panel.contains(x, y):
            panel.scroll_by(dy * self.frame.ctx.u(2.0) if self.frame else dy * 30)
            return True
        return False
