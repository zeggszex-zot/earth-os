"""Engineering: live system status.

Where the original showed decorative bar graphs, this reads the real machine —
``/proc`` and ``/sys`` via :mod:`earth_os.sysinfo` — so the panel is actually
useful as a system monitor.
"""

from __future__ import annotations

from typing import Tuple

from .. import sysinfo
from ..widgets import (
    BarRun, Elbow, Label, LevelIndicator, Meter, RenderContext, TextButton,
    draw_text,
)
from .base import Frame, Screen


class EngineeringScreen(Screen):
    name = "engineering"
    title = "ENGINEERING"

    def __init__(self, app):
        super().__init__(app)
        self.monitor = app.monitor

    def build(self, ctx: RenderContext, f: Frame) -> None:
        u = ctx.u
        mon = self.monitor
        self.add_chrome(ctx, f, title=ctx.strings["myEngineering"])
        cx, cy, cw, ch = f.content

        self.g.add(TextButton(
            "hdr_power", (cx, cy, cw * 0.58, u(1.3)),
            label="Power And Core Systems", color="accent3", label_color="accent4",
        ))

        meters = [
            ("PROCESSOR", lambda: (mon.cpu(), f"{mon.cpu() * 100:.0f}%"), "accent2"),
            ("MEMORY", self._mem, "accent3"),
            ("SWAP", self._swap, "accent6"),
            ("STORAGE", self._disk, "accent5"),
            ("NETWORK", self._net, "accent4"),
        ]
        y = cy + u(2.0)
        for i, (label, provider, color) in enumerate(meters):
            self.g.add(Meter(
                f"meter_{i}", (cx, y + i * u(1.9), cw * 0.58, u(1.5)),
                label=label, provider=provider, bar_color=color,
            ))

        # Per-core column of level indicators.
        cores = max(len(mon.cores()), 1)
        rail_x = cx + cw * 0.62
        rail_w = cw - (rail_x - cx)
        self.g.add(TextButton(
            "hdr_cores", (rail_x, cy, rail_w, u(1.3)),
            label=f"Cores · {cores}", color="accent2", label_color="accent4",
        ))
        shown = min(cores, 16)
        gap = u(0.25)
        gw = min((rail_w - gap * (shown - 1)) / shown, u(2.4))
        rail_y = cy + u(2.0)
        rail_h = u(9.5)
        for i in range(shown):
            self.g.add(LevelIndicator(
                f"core_{i}", (rail_x + i * (gw + gap), rail_y, gw, rail_h),
                provider=(lambda idx=i: self._core(idx)), ticks=14,
                fill_color="accent2", empty_color="accent2",
            ))
            self.g.add(Label(
                f"corelbl_{i}",
                (rail_x + i * (gw + gap), rail_y + rail_h + u(0.2), gw, u(0.8)),
                text=str(i), size=u(0.55), align="center", color="accent2",
            ))
        self.g.add(Label(
            "core_caption", (rail_x, rail_y + rail_h + u(1.1), rail_w, u(0.9)),
            text="per-core utilisation", size=u(0.6), color="accent5",
        ))

        # Identity block, sitting below both columns with clearance for the
        # closing bar run.  When CDOS is the backend it takes the right half,
        # showing its own interpretation beside the raw kernel figures.
        ident_hdr_y = cy + u(13.8)
        cdos_live = self.app.cdos_bridge() is not None
        ident_w = cw * 0.56 if cdos_live else cw
        self.g.add(TextButton(
            "hdr_ident", (cx, ident_hdr_y, ident_w, u(1.3)),
            label="System Identification", color="accent5", label_color="accent4",
        ))
        info_y = ident_hdr_y + u(2.0)
        info_h = max(ch - u(17.3), u(4))
        self.info_rect = (cx, info_y, ident_w, info_h)

        self.cdos_rect = None
        if cdos_live:
            gx = cx + cw * 0.62
            gw_ = cw - (gx - cx)
            self.g.add(TextButton(
                "hdr_cdos", (gx, ident_hdr_y, gw_, u(1.3)),
                label="GODCORE", color="accent3", label_color="accent4",
            ))
            self.cdos_rect = (gx, info_y, gw_, info_h)

        self.g.add(BarRun(
            "eng_bar", (cx, cy + ch - u(1.1), cw, u(1.0)),
            weights=(2, 1, 3, 1, 2, 1.5, 2.5), cap_left=True, cap_right=True,
        ))

    # ------------------------------------------------------------------
    def _mem(self) -> Tuple[float, str]:
        frac, used, total = self.monitor.memory()
        return (frac, f"{sysinfo.human_bytes(used)} / {sysinfo.human_bytes(total)}")

    def _swap(self) -> Tuple[float, str]:
        frac, used, total = self.monitor.swap()
        if not total:
            return (0.0, "none")
        return (frac, f"{sysinfo.human_bytes(used)} / {sysinfo.human_bytes(total)}")

    def _disk(self) -> Tuple[float, str]:
        frac, used, total = self.monitor.disk()
        return (frac, f"{sysinfo.human_bytes(used)} / {sysinfo.human_bytes(total)}")

    def _net(self) -> Tuple[float, str]:
        import math
        rx, tx = self.monitor.net_rate()
        total = max(rx + tx, 1.0)
        level = max(0.0, min(1.0, math.log10(total) / math.log10(25_000_000.0)))
        return (level, f"↓{sysinfo.human_bytes(rx)}/s ↑{sysinfo.human_bytes(tx)}/s")

    def _core(self, index: int) -> float:
        cores = self.monitor.cores()
        return cores[index] if index < len(cores) else 0.0

    # ------------------------------------------------------------------
    def tick(self) -> None:
        self.app.refresh_cdos()

    def _draw_cdos(self, cr, ctx: RenderContext) -> None:
        """GODCORE's self and CDOS's own reading of the machine."""
        rect = getattr(self, "cdos_rect", None)
        if rect is None:
            return
        u = ctx.u
        x, y, w, h = rect
        app = self.app

        if not app.cdos_snapshot:
            draw_text(cr, ctx, "READING...", x, y, u(0.68),
                      ctx.color("accent5"), align="left", valign="top")
            return
        error = app.cdos_snapshot.get("error")
        if error:
            draw_text(cr, ctx, str(error).upper(), x, y, u(0.62),
                      ctx.color("alert"), align="left", valign="top",
                      max_width=w, wrap=True)
            return

        rows = [
            ("NAME", app.godcore_value("name", "-")),
            ("ROLE", app.godcore_value("role", "-")),
            ("MOOD", app.godcore_value("mood", "-")),
            ("ENERGY", app.godcore_value("energy", "-")),
            ("ATTENTION", app.godcore_value("attention", "-")),
            ("ACTIVITY", app.godcore_value("activity", "-")),
            ("PULSE", app.weather_value("pulse", app.godcore_value("pulse", "-"))),
            ("I-ZENE", app.weather_value("i-zene", app.godcore_value("i-zene", "-"))),
            ("TONE", app.weather_value("tone", "-")),
            ("DOMAIN", app.weather_value("domain", "-")),
        ]
        size = u(0.66)
        row_h = min(h / max(len(rows), 1), u(1.15))
        for i, (key, value) in enumerate(rows):
            ry = y + i * row_h
            draw_text(cr, ctx, key, x, ry, size, ctx.ink("accent3"),
                      align="left", valign="top")
            draw_text(cr, ctx, value, x + w * 0.99, ry, size, ctx.color("text"),
                      align="right", valign="top", max_width=w * 0.66)

    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        self._draw_cdos(cr, ctx)
        rect = getattr(self, "info_rect", None)
        if rect is None:
            return
        u = ctx.u
        x, y, w, h = rect
        mon = self.monitor
        battery = mon.battery()
        temp = mon.temperature()
        rows = [
            ("HOST", mon.hostname()),
            ("SYSTEM", mon.distro()),
            ("KERNEL", mon.kernel()),
            ("PROCESSOR", mon.cpu_model()),
            ("LOAD AVERAGE", " / ".join(f"{v:.2f}" for v in mon.load())),
            ("UPTIME", sysinfo.human_duration(mon.uptime())),
            ("PROCESSES", str(mon.processes())),
            ("THERMAL", f"{temp:.0f} °C" if temp is not None else "no sensor"),
            ("POWER", f"{battery[0] * 100:.0f}% {battery[1]}" if battery else "external"),
            ("OPERATOR", self.app.operator_name() or "unknown"),
        ]
        size = u(0.66)
        row_h = min(h / max(len(rows), 1), u(1.15))
        for i, (key, value) in enumerate(rows):
            ry = y + i * row_h
            draw_text(cr, ctx, key, x, ry, size, ctx.color("accent5"),
                      align="left", valign="top")
            draw_text(cr, ctx, value, x + w * 0.99, ry, size, ctx.color("text"),
                      align="right", valign="top", max_width=w * 0.72)
