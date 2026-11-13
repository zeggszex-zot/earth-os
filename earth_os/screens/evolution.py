"""SYSTEM EVOLUTION CENTER — where Earth OS is examined and its future planned.

One presentation of :mod:`earth_os.evolution`, and only that. Every figure on
this screen was produced by the engine; every decision made here is a button
calling the planner with the operator's name. Nothing is concluded, and
nothing is changed, in this file — a robot with no screen gets the same
findings from ``earth-os --evolution-report``.

Eight sections, of which only the one on show is ever computed:

    OVERVIEW · HEALTH · OPTIMIZATION · CAPABILITIES · ROADMAP ·
    DIAGNOSTICS · HISTORY · SETTINGS

The screen costs nothing while it is not up: ``tick`` is only called for the
screen on display, and that is where sampling is driven from. It is not
``live`` in the frame scheduler's sense either — it asks for a frame when a
new sample changed what is shown, which is at most once per poll interval.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Tuple

from ..widgets import (
    BarRun, Element, Label, Meter, RenderContext, ScrollPanel, StandardButton,
    TabStrip, TextButton, draw_text,
)
from ..widgets import shapes
from .base import Frame, Screen

#: (key, tab label, full name)
TABS: List[Tuple[str, str, str]] = [
    ("overview", "OVERVIEW", "System Overview"),
    ("health", "HEALTH", "System Health"),
    ("optimization", "OPTIMIZATION", "Optimization Center"),
    ("capabilities", "CAPABILITIES", "Capability Development"),
    ("roadmap", "ROADMAP", "Evolution Roadmap"),
    ("diagnostics", "DIAGNOSTICS", "Diagnostics and Reports"),
    ("history", "HISTORY", "Evolution History"),
    ("settings", "SETTINGS", "Center Settings"),
]

#: Colour is never the only carrier: every one of these is also written out.
SEVERITY_SLOT = {"CRITICAL": "alert", "HIGH": "accent6", "MODERATE": "accent4",
                 "LOW": "accent3", "INFORMATIONAL": "accent2"}
STATE_SLOT = {"RECOMMENDED": "accent4", "AVAILABLE": "accent3", "IN EFFECT": "accent2"}
CATEGORY_SLOT = {"MISSING": "accent6", "INCOMPLETE": "accent4",
                 "NEEDS IMPROVEMENT": "accent3", "EXPERIMENTAL": "accent5", "FUTURE": "accent2"}
STATUS_SLOT = {"DISCOVERED": "accent2", "PROPOSED": "accent3", "APPROVED": "accent4",
               "IN DEVELOPMENT": "accent5", "TESTING": "accent5", "COMPLETED": "accent2",
               "REJECTED": "accent6", "ROLLED BACK": "accent6"}
SHORT = {"INFORMATIONAL": "INFO", "NEEDS IMPROVEMENT": "IMPROVE", "IN DEVELOPMENT": "IN DEV",
         "EXPERIMENTAL": "EXPERIMENT", "RECOMMENDED": "RECOMMEND"}

#: (key, tag, palette slot, text)
Row = Tuple[str, str, str, str]
#: (heading, body, palette slot for the body)
Block = Tuple[str, str, str]


class Rows(Element):
    """A scrolling list of tagged rows, one of which is selected."""

    interactive = True

    def __init__(self, name="", rect=(0, 0, 0, 0), *,
                 provider: Callable[[], List[Row]], selected: Callable[[], str],
                 row_units: float = 1.5, **kw):
        super().__init__(name, rect, **kw)
        self.provider = provider
        self.selected = selected
        self.row_units = row_units
        self.offset = 0
        self._row_h = 0.0

    def visible_rows(self) -> int:
        return max(int(self.h // self._row_h), 1) if self._row_h else 1

    def scroll(self, rows: int) -> None:
        total = len(self.provider())
        self.offset = max(0, min(self.offset + rows, max(0, total - self.visible_rows())))

    def row_at(self, px: float, py: float) -> str:
        if self._row_h <= 0 or not self.contains(px, py):
            return ""
        rows = self.provider()
        index = self.offset + int((py - self.y) // self._row_h)
        return rows[index][0] if 0 <= index < len(rows) else ""

    def draw(self, cr, ctx: RenderContext) -> None:
        x, y, w, h = self.rect
        rh = ctx.u(self.row_units)
        self._row_h = rh
        gap = rh * 0.14
        rows = self.provider()
        self.offset = max(0, min(self.offset, max(0, len(rows) - self.visible_rows())))
        chosen = self.selected()
        tag_w = min(ctx.u(6.6), w * 0.34)
        for i in range(int(h // rh)):
            index = self.offset + i
            if index >= len(rows):
                break
            key, tag, slot, text = rows[index]
            ry = y + i * rh
            col = ctx.color(slot)
            picked = key == chosen
            shapes.pill(cr, x, ry, tag_w, rh - gap, ends="left")
            shapes.fill(cr, col)
            draw_text(cr, ctx, tag, x + tag_w - rh * 0.25, ry + (rh - gap) / 2, rh * 0.42,
                      ctx.palette.ink_for(col), align="right", valign="middle",
                      max_width=tag_w - rh * 0.6)
            body_x = x + tag_w + gap
            shapes.block(cr, body_x, ry, w - tag_w - gap, rh - gap, rh * 0.12)
            fill = ctx.color("select") if picked else tuple(c * 0.22 for c in col)
            shapes.fill(cr, fill)                                # type: ignore[arg-type]
            draw_text(cr, ctx, text, body_x + rh * 0.3, ry + (rh - gap) / 2, rh * 0.46,
                      ctx.palette.ink_for(fill) if picked else ctx.color("text"),  # type: ignore[arg-type]
                      align="left", valign="middle", max_width=w - tag_w - gap - rh * 0.6)
        if len(rows) > self.visible_rows():
            more = len(rows) - self.offset - self.visible_rows()
            if more > 0:
                draw_text(cr, ctx, f"+{more} MORE — SCROLL", x + w, y + h, rh * 0.36,
                          ctx.ink("accent3"), align="right", valign="bottom")




def _history_line(e: Dict[str, object]) -> str:
    """One history record as the line HISTORY shows."""
    what = e.get("event")
    if what == "change-applied":
        text = (f"CHANGE APPLIED  {e.get('proposal')}  {e.get('setting')} = "
                f"{e.get('value')} (was {e.get('previous')})  — by "
                f"{e.get('by') or 'not recorded'}  backup {e.get('backup') or '-'}"
                + ("  · in effect at once" if e.get("in_effect_now") else ""))
    elif what == "change-rolled-back":
        text = (f"CHANGE ROLLED BACK  {e.get('proposal')}  {e.get('setting')} "
                f"restored to {e.get('restored')}  — by {e.get('by') or 'not recorded'}"
                + ("  · in effect at once" if e.get("in_effect_now") else ""))
    else:
        text = (f"{e.get('status')}  {e.get('proposal')}  {e.get('title')}  "
                f"— by {e.get('by')}: {e.get('note')}")
    return f"{_stamp(e.get('at'))}  {text}"

def _took_effect(record: Dict[str, object]) -> str:
    """Whether an applied or rolled-back change is in effect, in a sentence.

    From what happened, not from what the setting usually does: the change
    record says whether the running shell put it into effect.
    """
    if record.get("in_effect_now"):
        return "In effect now."
    if record.get("restart"):
        return "Restart Earth OS for it to take effect."
    return "Recorded, not yet in effect: nothing running could apply it — it applies when Earth OS next starts."

def _stamp(at: object) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(at)))   # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"


class EvolutionScreen(Screen):
    name = "evolution"
    title = "SYSTEM EVOLUTION CENTER"

    def __init__(self, app):
        super().__init__(app)
        self.tab = 0
        #: The selected row's key, per section.
        self.picked = {key: "" for key, _label, _full in TABS}
        self._signature: Tuple = ()
        self._report_kind = "health"
        self._report_text = ""
        self._exported = ""
        #: The HISTORY section's lines, newest first, formatted once when
        #: loaded. The decoded records were ten times the size and were
        #: formatted again on every frame.
        self._history: List[str] = []

    # -- the engine --------------------------------------------------------------
    def center(self):
        evolution = getattr(self.app, "evolution", None)
        return evolution() if callable(evolution) else None

    @property
    def section(self) -> str:
        return TABS[self.tab][0]

    def operator(self) -> str:
        return (self.app.operator_name() or "operator").strip() or "operator"

    def hidden(self) -> bool:
        """True while another program has the screen: nothing here is visible."""
        return bool(getattr(self.app, "handed_over_to", ""))

    # -- lifecycle ---------------------------------------------------------------
    def on_leave(self) -> None:
        """Keep nothing that opening the center again would rebuild anyway.

        Measured: after a visit, the screen and engine held about 270 KB
        that nothing used while hidden — the decoded history, the report
        text and the last process list. Trends and records stay.
        """
        self._history = []
        self._report_text = ""
        center = self.center()
        if center is not None:
            center.release()

    def on_enter(self) -> None:
        center = self.center()
        if center is not None:
            center.sample(visible=True, panel=self.section, force=True)
            self._refresh_section()
        self.invalidate()

    def tick(self) -> None:
        """Drive sampling while on show; ask for a frame only if it changed."""
        center = self.center()
        if center is None or self.hidden():
            return
        if not center.sample(visible=True, panel=self.section):
            return
        signature = self._current_signature()
        if self.section == "overview" or signature != self._signature:
            buttons_changed = signature != self._signature
            self._signature = signature
            if buttons_changed and self.section != "overview":
                self.invalidate()
            else:
                self.app.redraw()

    def _current_signature(self) -> Tuple:
        """What the section on show is built from, cheaply, to spot a change."""
        rows = self._rows()
        return (self.section, tuple((r[0], r[1]) for r in rows), self._chosen(),
                self._detail_signature())

    def _detail_signature(self) -> Tuple:
        center = self.center()
        if center is None:
            return ()
        if self.section == "health":
            found = next((f for f in center.findings() if f.id == self._chosen()), None)
            return tuple(found.evidence) if found is not None else ()
        return (center.checking, center.explaining)

    # -- rows, by section --------------------------------------------------------
    def _rows(self) -> List[Row]:
        center = self.center()
        if center is None:
            return []
        section = self.section
        if section == "health":
            return [(f.id, SHORT.get(f.severity, f.severity), SEVERITY_SLOT[f.severity], f.title)
                    for f in center.findings()]
        if section == "optimization":
            return [(o.id, SHORT.get(o.state, o.state), STATE_SLOT.get(o.state, "accent3"), o.title)
                    for o in center.optimizations()]
        if section == "capabilities":
            return [(e.id, SHORT.get(e.category, e.category),
                     CATEGORY_SLOT.get(e.category, "accent3"), e.title)
                    for e in center.capabilities()]
        if section == "roadmap":
            return [(p.id, SHORT.get(p.status, p.status), STATUS_SLOT.get(p.status, "accent3"),
                     f"{p.id}  {p.title}" + ("  (postponed)" if p.postponed else ""))
                    for p in center.planner.ordered()]
        if section == "diagnostics":
            from ..evolution.reporter import KINDS
            return [(kind, "REPORT", "accent3", title) for kind, title in KINDS.items()]
        return []

    def _chosen(self) -> str:
        """The selected key for this section; the first row if none is valid."""
        rows = self._rows()
        keys = [r[0] for r in rows]
        current = self.picked.get(self.section, "")
        if current not in keys:
            current = keys[0] if keys else ""
            self.picked[self.section] = current
        return current

    def _select(self, key: str) -> None:
        if not key or key == self.picked.get(self.section):
            return
        self.picked[self.section] = key
        if self.section == "diagnostics":
            self._report_kind = key
            self._build_report()
        self._signature = self._current_signature()
        self.invalidate()

    def _refresh_section(self) -> None:
        center = self.center()
        if center is None:
            return
        if self.section == "diagnostics":
            self._report_kind = self._chosen() or "health"
            self._build_report()
        elif self.section == "history":
            self._history = [_history_line(e) for e in
                             reversed(center.store.read_history(limit=200))]
        self._signature = self._current_signature()

    def _build_report(self) -> None:
        center = self.center()
        if center is None:
            return
        from ..evolution.reporter import Reporter
        try:
            self._report_text = Reporter.as_text(center.report(self._report_kind))
        except Exception as exc:
            self._report_text = f"The report could not be built: {type(exc).__name__}: {exc}"

    # -- building ----------------------------------------------------------------
    def build(self, ctx: RenderContext, f: Frame) -> None:
        u = ctx.u
        self.add_chrome(ctx, f, title=ctx.strings.get("myEvolutionTitle", self.title))
        cx, cy, cw, ch = f.content
        self.notice_rect = None

        strip = TabStrip("tabs", (cx, cy, cw, u(1.8)), tabs=[t[1] for t in TABS],
                         on_change=self._switch)
        strip.index = self.tab
        self.g.add(strip)

        head_y = cy + u(2.3)
        self.g.add(TextButton("sec_header", (cx, head_y, cw, u(1.3)), label=TABS[self.tab][2],
                              color="accent3", label_color="accent4"))
        self.g.add(Label("sec_status", (cx, head_y, cw - u(0.8), u(1.3)),
                         provider=self._status_line, size=u(0.6), color="accent5",
                         align="right", valign="middle", uppercase=False, z=2))

        body = (cx, cy + u(4.1), cw, ch - u(4.1) - u(2.5))
        actions_y = cy + ch - u(2.0)

        center = self.center()
        if center is None:
            self.notice_rect = body
            return

        section = self.section
        if section == "overview":
            self._build_overview(ctx, body)
        elif section == "history":
            self._add_detail(body)
        elif section == "settings":
            self._build_settings(ctx, body)
        else:
            x, y, w, h = body
            list_w = w * 0.42
            rows = Rows("rows", (x, y, list_w, h), provider=self._rows, selected=self._chosen)
            self.g.add(rows)
            self._add_detail((x + list_w + u(0.6), y, w - list_w - u(0.6), h))

        self._add_actions(ctx, cx, actions_y, cw, u(1.9))
        self._signature = self._current_signature()

    def _add_detail(self, rect) -> None:
        panel = ScrollPanel("detail", rect, draw_content=self._draw_detail, bar_color="accent3")
        panel.follow_tail = False
        self.g.add(panel)

    def _switch(self, index: int, _name: str) -> None:
        self.tab = index
        center = self.center()
        if center is not None:
            # A section that shows the process list gets one now rather than
            # at the next poll, so it does not open on an empty panel.
            center.sample(visible=True, panel=self.section, force=True)
        self._refresh_section()
        self.invalidate()

    def _status_line(self) -> str:
        center = self.center()
        if center is None:
            return ""
        summary = center.health_summary()
        tally = summary["counts"]
        cost = center.overhead()
        parts = [f"health: {str(summary['worst']).lower()}"]
        flagged = [f"{tally[name]} {name.lower()}" for name in ("CRITICAL", "HIGH", "MODERATE", "LOW")
                   if tally.get(name)]
        if flagged:
            parts.append(", ".join(flagged))
        parts.append(f"sampling every {center.poll_interval:g} s")
        parts.append(f"{cost['mean_ms']:.1f} ms a sample")
        return "   ·   ".join(parts)

    # -- overview ----------------------------------------------------------------
    def _build_overview(self, ctx: RenderContext, body) -> None:
        u = ctx.u
        x, y, w, h = body
        left_w = w * 0.52
        meters = [("PROCESSOR", self._m_cpu, "accent2"), ("MEMORY", self._m_memory, "accent3"),
                  ("STORAGE", self._m_disk, "accent5"), ("SWAP", self._m_swap, "accent6"),
                  ("TEMPERATURE", self._m_temp, "accent4")]
        for i, (label, provider, color) in enumerate(meters):
            self.g.add(Meter(f"ov_meter_{i}", (x, y + i * u(1.9), left_w, u(1.5)),
                             label=label, provider=provider, bar_color=color))

        tally_y = y + len(meters) * u(1.9) + u(0.6)
        self.g.add(TextButton("ov_health_hdr", (x, tally_y, left_w, u(1.2)),
                              label="Findings by severity", color="accent3",
                              label_color="accent4"))
        names = ("CRITICAL", "HIGH", "MODERATE", "LOW", "INFORMATIONAL")
        gap = u(0.3)
        bw = (left_w - gap * (len(names) - 1)) / len(names)
        for i, name in enumerate(names):
            self.g.add(StandardButton(
                f"ov_sev_{i}", (x + i * (bw + gap), tally_y + u(1.7), bw, u(2.4)),
                label=SHORT.get(name, name), label_align="center", label_size=0.30,
                label_valign="middle",
                color=SEVERITY_SLOT[name], tooltip=f"{name.lower()} findings",
                on_click=lambda: self._go_section("health")))
            self.g.add(Label(
                f"ov_sevn_{i}", (x + i * (bw + gap), tally_y + u(4.2), bw, u(1.3)),
                provider=(lambda n=name: self._tally(n)), size=u(1.0),
                color=SEVERITY_SLOT[name], align="center", valign="middle"))

        self.overview_rect = (x + left_w + u(1.2), y, w - left_w - u(1.2), h)
        self.g.add(BarRun("ov_bar", (x, y + h - u(1.0), left_w, u(1.0)),
                          weights=(2, 1, 3, 1, 2), cap_left=True, cap_right=True))

    def _go_section(self, key: str) -> None:
        for i, (name, _label, _full) in enumerate(TABS):
            if name == key and i != self.tab:
                self._switch(i, key)
                return

    def _tally(self, name: str) -> str:
        center = self.center()
        if center is None:
            return "-"
        return str(center.health_summary()["counts"].get(name, 0))

    def _reading(self, name: str):
        center = self.center()
        if center is None or center.snapshot is None:
            return None
        return center.snapshot.get(name)

    def _m_cpu(self) -> Tuple[float, str]:
        r = self._reading("cpu")
        if r is None or not r.ok:
            return (0.0, "UNAVAILABLE")
        return (float(r.value), f"{float(r.value) * 100:.0f}%")

    def _m_fraction(self, name: str, free_key: str = "") -> Tuple[float, str]:
        from ..evolution.model import human_bytes
        r = self._reading(name)
        if r is None or not r.ok:
            return (0.0, "UNAVAILABLE")
        v = r.value
        if not v.get("total"):
            return (0.0, "NONE")
        text = f"{human_bytes(v['used'])} / {human_bytes(v['total'])}"
        return (float(v["fraction"]), text)

    def _m_memory(self) -> Tuple[float, str]:
        return self._m_fraction("memory")

    def _m_disk(self) -> Tuple[float, str]:
        return self._m_fraction("disk")

    def _m_swap(self) -> Tuple[float, str]:
        return self._m_fraction("swap")

    def _m_temp(self) -> Tuple[float, str]:
        r = self._reading("temperature")
        if r is None or not r.ok:
            return (0.0, "NO SENSOR")
        degrees = float(r.value)
        return (max(0.0, min(1.0, degrees / 100.0)), f"{degrees:.0f} °C")

    def _overview_rows(self) -> List[Tuple[str, str]]:
        from ..evolution.model import human_bytes, human_span
        center = self.center()
        if center is None or center.snapshot is None:
            return []
        snap = center.snapshot

        def said(name: str, fmt=str) -> str:
            r = snap.get(name)
            return fmt(r.value) if r.ok else f"unavailable — {r.why}"

        memory = snap.get("memory")
        render = snap.get("render")
        rows = [
            ("OPERATING SYSTEM", said("distro")),
            ("EARTH OS VERSION", said("version")),
            ("KERNEL", said("kernel")),
            ("UPTIME", said("uptime", lambda v: human_span(float(v)))),
            ("AVAILABLE RAM", human_bytes(memory.value["available"]) if memory.ok
             else f"unavailable — {memory.why}"),
            ("RUNNING PROCESSES", said("process_count")),
        ]
        if render.ok:
            r = render.value
            jobs = r.get("jobs") or {}
            rows.append(("BACKGROUND SERVICES",
                         f"{len(jobs)} Earth OS jobs: {', '.join(sorted(jobs))}"))
            drawn = f"{r.get('frames_drawn')} frames drawn, {str(r.get('state')).lower()}"
            if r.get("draws_timed"):
                drawn += f", mean {float(r.get('draw_ms_mean') or 0):.1f} ms"
            rows.append(("DESKTOP RENDERING", drawn))
            rate = center.trends.rate("frames_drawn", 60.0)
            if rate is not None:
                rows.append(("FRAMES A SECOND, LAST MINUTE", f"{rate:.1f} of a ceiling of {r.get('fps')}"))
        else:
            rows.append(("BACKGROUND SERVICES", f"unavailable — {render.why}"))
            rows.append(("DESKTOP RENDERING", f"unavailable — {render.why}"))
        rows.append(("SYSTEM SERVICES", "no reading — see CAPABILITIES"))
        stat = center.trends.stat("cpu")
        if stat is not None and stat.count >= 2:
            rows.append(("PROCESSOR THIS SESSION",
                         f"mean {stat.mean * 100:.0f}%, peak {stat.high * 100:.0f}% "
                         f"({stat.count} samples, {human_span(stat.span)})"))
        cost = center.overhead()
        rows.append(("THIS CENTER'S COST", f"{int(cost['samples'])} samples, "
                                           f"mean {cost['mean_ms']:.1f} ms each"))
        return rows

    # -- settings ----------------------------------------------------------------
    SETTINGS = (
        ("poll_interval", "SAMPLE EVERY", ((1.0, "1 S"), (2.0, "2 S"), (5.0, "5 S"), (10.0, "10 S"))),
        ("process_interval", "PROCESS LIST EVERY", ((5.0, "5 S"), (15.0, "15 S"), (30.0, "30 S"), (60.0, "60 S"))),
        ("background_interval", "WHILE HIDDEN, EVERY", ((0.0, "NEVER"), (30.0, "30 S"), (60.0, "60 S"), (300.0, "5 MIN"))),
        ("explain", "REWORD WITH A MODEL", ((True, "ON"), (False, "OFF"))),
    )

    def _saved_values(self) -> Dict[str, object]:
        """The ``[evolution]`` block as ``config.toml`` holds it, read now."""
        import tomllib
        try:
            with open(self.app.config.path, "rb") as fh:
                block = tomllib.load(fh).get("evolution", {})
        except (OSError, ValueError):
            return {}
        return block if isinstance(block, dict) else {}

    def _setting_state(self, key: str, saved: Dict[str, object]) -> Tuple[str, str]:
        """What a person needs to know about one setting: is it in effect, is it kept."""
        live = self.app.config.get("evolution", key, None)
        kept = key in saved and saved[key] == live
        if key == "background_interval" and not callable(getattr(self.app, "settings_hook", None)):
            when = "NO WINDOW"           # a headless shell has no background job to change
        else:
            when = "IN EFFECT NOW"
        return f"{when} · {'SAVED' if kept else 'NOT SAVED'}", ("accent5" if kept else "accent4")

    def _build_settings(self, ctx: RenderContext, body) -> None:
        u = ctx.u
        x, y, w, h = body
        cfg = self.app.config
        saved = self._saved_values()
        for r, (key, label, options) in enumerate(self.SETTINGS):
            ry = y + r * u(2.4)
            self.g.add(Label(f"set_l_{r}", (x, ry, w * 0.26, u(1.8)), text=label,
                             size=u(0.72), color="accent5", valign="middle"))
            current = cfg.get("evolution", key, options[0][0])
            state, slot = self._setting_state(key, saved)
            self.g.add(Label(f"set_s_{r}", (x + w * 0.80, ry, w * 0.20, u(1.8)), text=state,
                             size=u(0.56), color=slot, valign="middle", align="right"))
            bw = (w * 0.50 - u(0.4) * (len(options) - 1)) / len(options)
            for i, (value, text) in enumerate(options):
                on = (bool(current) == value) if isinstance(value, bool) \
                    else abs(float(current) - float(value)) < 1e-6
                self.g.add(StandardButton(
                    f"set_{key}_{i}", (x + w * 0.28 + i * (bw + u(0.4)), ry, bw, u(1.8)),
                    label=text, label_align="center",
                    color="accent4" if on else "accent2",
                    on_click=(lambda k=key, v=value: self._set(k, v))))
        top = y + len(self.SETTINGS) * u(2.4) + u(0.6)
        self._add_detail((x, top, w, y + h - top))

    def _set(self, key: str, value) -> None:
        """One of the center's own settings, changed by a person's press.

        The same convention as the SETTINGS screen: it is in effect at once
        — the shell applies it, the background timer included — and SAVE
        there is what writes ``config.toml``. Each row says both. This is a
        person pressing a button, not the center changing its own settings.
        """
        self.app.config.values.setdefault("evolution", {})[key] = value
        apply = getattr(self.app, "apply_setting", None)
        live = bool(apply("evolution", key)) if callable(apply) else False
        label = next((l for k, l, _o in self.SETTINGS if k == key), key)
        self._after(f"{label}: {'in effect now' if live else 'recorded; not in effect — no window is running'}. "
                    "SAVE on the SETTINGS screen keeps it after a restart.")

    # -- the detail pane ---------------------------------------------------------
    def _blocks(self) -> List[Block]:
        center = self.center()
        if center is None:
            return []
        section, chosen = self.section, self._chosen()

        if section == "health":
            blocks: List[Block] = []
            found = next((f for f in center.findings() if f.id == chosen), None)
            if found is not None:
                blocks.append((f"{found.severity} · {found.area}", found.title,
                               SEVERITY_SLOT[found.severity]))
                if found.evidence:
                    blocks.append(("Measured", "\n".join(found.evidence), "text"))
                if found.explanation:
                    blocks.append(("Why it matters", found.explanation, "accent5"))
                if found.suggestion:
                    blocks.append(("Suggested", found.suggestion, "accent5"))
            if center.checking:
                blocks.append(("Component checks", "Running…", "accent4"))
            if center.explaining:
                blocks.append(("In plain words", "Asking…", "accent4"))
            elif center.explanation is not None:
                text, source = center.explanation
                blocks.append((f"In plain words — source: {source}", text, "accent5"))
            return blocks

        if section == "optimization":
            o = next((o for o in center.optimizations() if o.id == chosen), None)
            if o is None:
                return []
            return [(f"{o.state} · {o.area}", o.title, STATE_SLOT.get(o.state, "text")),
                    ("Now", o.reason, "text"),
                    ("1 · What it changes", o.changes, "text"),
                    ("2 · Why it could help", o.why, "text"),
                    ("3 · Expected benefit", o.benefits, "accent5"),
                    ("Basis of that estimate", o.basis, "accent5"),
                    ("4 · Disadvantages", o.drawbacks, "text"),
                    ("5 · Restart needed", "Yes." if o.restart else "No.", "text"),
                    ("6 · How to reverse", o.reverse, "text"),
                    ("Carried out by", "The center, once you have approved it on the ROADMAP."
                     if o.change else "A person. The center cannot do this itself.", "accent5")]

        if section == "capabilities":
            e = next((e for e in center.capabilities() if e.id == chosen), None)
            if e is None:
                return []
            who = {0: "Installation or configuration — a person's act.",
                   1: "Rung 1 — a new arrangement of what the machine can already do.",
                   2: "Rung 2 — a new mechanism for an existing capability; the agent's "
                      "gate can verify it.",
                   3: "Rung 3 — new machine power. Only a person may grant it."}[min(e.rung, 3)]
            blocks = [(f"{e.category} · {e.basis}", e.title, CATEGORY_SLOT.get(e.category, "text")),
                      ("Found", "\n".join(e.evidence), "text"),
                      ("Development description", e.description, "text"),
                      ("Implementation requirements",
                       "\n".join(f"• {r}" for r in e.requirements), "text"),
                      ("Who may build it", who, "accent5"),
                      ("Difficulty", e.difficulty, "text")]
            if e.depends:
                blocks.append(("Depends on", ", ".join(e.depends), "text"))
            return blocks

        if section == "roadmap":
            try:
                p = center.planner.get(chosen)
            except Exception:
                return [("Roadmap", "Nothing is on the roadmap yet. DISCOVER puts what the "
                                    "center has noticed here as DISCOVERED; PROPOSE on "
                                    "OPTIMIZATION or CAPABILITIES adds one item.", "accent5")]
            waiting = center.planner.blocked_by(p)
            blocks = [(f"{p.id} · {p.status}" + (" · postponed" if p.postponed else ""),
                       p.title, STATUS_SLOT.get(p.status, "text")),
                      ("Priority · difficulty · kind",
                       f"{p.priority} · {p.difficulty} · {p.kind}, rung {p.rung}", "text"),
                      ("Proposed upgrade", p.summary, "text"),
                      ("Dependencies", (", ".join(p.dependencies) or "None.")
                       + (f"\nWaiting on: {', '.join(waiting)}" if waiting else ""), "text"),
                      ("Testing requirements", "\n".join(f"• {t}" for t in p.testing) or "None stated.", "text"),
                      ("How to reverse", p.reverse or "Not stated.", "text")]
            if p.change:
                blocks.insert(3, ("The change the center will make when you press APPLY",
                                  f"[{p.change['section']}] {p.change['key']} = {p.change['value']} "
                                  "in config.toml — backed up first.", "accent4"))
            if p.applied:
                blocks.append(("Applied", f"{p.applied.get('setting')} = {p.applied.get('value')} "
                                          f"(was {p.applied.get('previous')}). "
                                          + _took_effect(p.applied) + " Backup: "
                                          f"{p.applied.get('backup') or 'none needed'}.", "accent4"))
                reverted = p.applied.get("reverted")
                if isinstance(reverted, dict):
                    blocks.append(("Rolled back", f"{p.applied.get('setting')} restored to "
                                                  f"{reverted.get('restored')}. "
                                                  + _took_effect(reverted)
                                                  + (" The whole file was restored from the backup."
                                                     if reverted.get("whole_file_restored") else
                                                     " Only that key was restored: the file had "
                                                     "been edited since, and those edits were kept."),
                                   "accent4"))
            if p.measured:
                from ..evolution.reporter import _vitals
                for label in ("before", "after"):
                    if label in p.measured:
                        blocks.append((f"Measured {label}", _vitals(p.measured[label]), "accent5"))
            blocks.append(("Completion history",
                           "\n".join(f"{_stamp(e.get('at'))}  {e.get('status')}  by {e.get('by')} "
                                     f"— {e.get('note')}" for e in p.events), "text"))
            return blocks

        if section == "diagnostics":
            blocks = []
            if self._exported:
                blocks.append(("Exported", self._exported, "accent4"))
            if center.checking:
                blocks.append(("Component checks", "Running…", "accent4"))
            blocks.append(("", self._report_text, "text"))
            return blocks

        if section == "history":
            if not self._history:
                return [("Evolution history", "Nothing has been decided or changed yet. Every "
                                              "decision and every approved change is recorded "
                                              "here, and never edited.", "accent5")]
            return [(f"Newest first · {len(self._history)} entries · {center.store.history_path}",
                     "\n".join(self._history), "text")]

        if section == "settings":
            from ..evolution.changes import TUNABLES
            return [("When these take effect",
                     "At once, all four — the background timer is replaced on the press. "
                     "They last for this session: SAVE on the SETTINGS screen writes them "
                     "to config.toml. Each row says whether it is saved.", "accent5"),
                    ("What the center may change, once you approve it",
                     "\n".join(f"[{t.section}] {t.key} — {t.what}"
                               + ("  (needs a restart)" if t.restart else "")
                               for t in TUNABLES.values())
                     + "\nEverything not marked takes effect the moment it is applied.",
                     "text"),
                    ("What it may never do",
                     "Install a package. Run a command. Edit source code. Delete anything. "
                     "Change CDOS or the LCARS frame. Approve its own proposals.", "accent4"),
                    ("Where it keeps its records", str(center.store.root), "text")]
        return []

    def _draw_detail(self, cr, ctx: RenderContext, x: float, y: float, w: float,
                     offset: float) -> float:
        u = ctx.u
        cursor = y + u(0.15)
        pad = u(0.2)
        for heading, body, slot in self._blocks():
            if heading:
                _, th = draw_text(cr, ctx, heading.upper(), x + pad, cursor, u(0.58),
                                  ctx.ink("accent3"), align="left", valign="top",
                                  max_width=w - pad * 2)
                cursor += th + u(0.12)
            if body:
                _, th = draw_text(cr, ctx, body, x + pad, cursor, u(0.72),
                                  ctx.ink(slot or "text"), align="left", valign="top",
                                  max_width=w - pad * 2, wrap=True, line_height=1.25)
                cursor += th
            cursor += u(0.5)
        return cursor - y + u(0.4)

    # -- actions -----------------------------------------------------------------
    def _actions(self) -> List[Tuple[str, str, Callable[[], None]]]:
        center = self.center()
        if center is None:
            return []
        section, chosen = self.section, self._chosen()
        if section == "overview":
            return [("REFRESH NOW", "accent3", self._refresh_now)]
        if section == "health":
            return [("REFRESH NOW", "accent3", self._refresh_now),
                    ("RUN CHECKS", "accent5", self._run_checks),
                    ("EXPLAIN", "accent4", self._explain)]
        if section in ("optimization", "capabilities"):
            kind = "optimization" if section == "optimization" else "capability"
            existing = center.planner.by_source(f"{kind}:{chosen}")
            if existing is not None:
                return [(f"ON ROADMAP · {existing.id}", "accent2",
                         lambda: self._show_proposal(existing.id))]
            if section == "optimization":
                item = next((o for o in center.optimizations() if o.id == chosen), None)
                if item is None or not center.proposable(item):
                    # Already so: the engine would refuse, so do not offer.
                    return []
            return [("PROPOSE", "accent4", lambda: self._propose(kind, chosen))]
        if section == "roadmap":
            out: List[Tuple[str, str, Callable[[], None]]] = []
            try:
                p = center.planner.get(chosen)
            except Exception:
                p = None
            if p is not None:
                def act(method: str, **kw) -> Callable[[], None]:
                    return lambda: self._decide(method, p.id, **kw)
                if p.status == "DISCOVERED":
                    out.append(("PROPOSE", "accent3", lambda: self._propose_existing(p.id)))
                if p.status in ("DISCOVERED", "PROPOSED"):
                    out += [("APPROVE", "accent4", act("approve")),
                            ("REJECT", "accent6", act("reject"))]
                elif p.status == "APPROVED":
                    out.append(("APPLY CHANGE" if p.automatic else "START WORK", "alert",
                                lambda: self._implement(p.id)))
                    if not p.automatic and p.rung in (1, 2) \
                            and getattr(self.app, "agent", None) is not None:
                        out.append(("HAND TO CLAUDE", "accent5", lambda: self._hand_over(p.id)))
                    out.append(("REJECT", "accent6", act("reject")))
                elif p.status == "IN DEVELOPMENT":
                    out += [("TO TESTING", "accent4", act("begin_testing")),
                            ("ROLL BACK", "accent6", act("roll_back"))]
                elif p.status == "TESTING":
                    out += [("VERIFIED", "accent4", lambda: self._complete(p.id)),
                            ("ROLL BACK", "accent6", act("roll_back"))]
                elif p.status == "COMPLETED":
                    out.append(("ROLL BACK", "accent6", act("roll_back")))
                else:
                    out.append(("RECONSIDER", "accent3", act("reconsider")))
                if p.open:
                    out.append(("RESUME", "accent3", act("resume")) if p.postponed
                               else ("POSTPONE", "accent3", act("postpone")))
                    out += [("PRIORITY +", "accent2", act("prioritize", step=1)),
                            ("PRIORITY -", "accent2", act("prioritize", step=-1))]
            out.append(("DISCOVER", "accent5", self._discover))
            return out
        if section == "diagnostics":
            return [("REBUILD", "accent3", self._rebuild_report),
                    ("EXPORT TEXT", "accent4", lambda: self._export("text")),
                    ("EXPORT JSON", "accent4", lambda: self._export("json")),
                    ("RUN CHECKS", "accent5", self._run_checks)]
        if section == "history":
            return [("RELOAD", "accent3", self._reload_history)]
        return []

    def _add_actions(self, ctx: RenderContext, x: float, y: float, w: float, h: float) -> None:
        u = ctx.u
        actions = self._actions()
        if not actions:
            return
        gap = u(0.35)
        bw = min((w - gap * (len(actions) - 1)) / len(actions), u(11.0))
        for i, (label, color, callback) in enumerate(actions):
            self.g.add(StandardButton(f"act_{i}", (x + i * (bw + gap), y, bw, h),
                                      label=label, label_align="center", color=color,
                                      on_click=callback))

    def _after(self, message: str = "") -> None:
        if message:
            self.app.toast(message)
        self._refresh_section()
        self.invalidate()

    def _refresh_now(self) -> None:
        center = self.center()
        if center is not None:
            center.sample(visible=True, panel=self.section, force=True)
        self._after()

    def _run_checks(self) -> None:
        center = self.center()
        if center is None:
            return
        started = center.run_checks(done=self._landed)
        self._after("Running the component checks…" if started else "Checks are already running.")

    def _explain(self) -> None:
        center = self.center()
        if center is None:
            return
        center.explain(done=self._landed)
        self._after()

    def _landed(self) -> None:
        """A worker finished: show what it found."""
        if self.section == "diagnostics":
            self._build_report()
        self._after()

    def _propose(self, kind: str, key: str) -> None:
        center = self.center()
        proposal = center.propose_from(kind, key, by=self.operator()) if center else None
        self._after(f"{proposal.id} is on the roadmap as {proposal.status}. Nothing has changed."
                    if proposal is not None else
                    "Nothing to propose: that is already in effect, or is no longer listed.")

    def _propose_existing(self, pid: str) -> None:
        center = self.center()
        try:
            center.planner.propose(pid, by=self.operator())
            self._after(f"{pid} proposed.")
        except Exception as exc:
            self._after(str(exc))

    def _show_proposal(self, pid: str) -> None:
        self.picked["roadmap"] = pid
        self._go_section("roadmap")

    def _decide(self, method: str, pid: str, **kw) -> None:
        """A decision, made by a press, in the operator's name."""
        center = self.center()
        try:
            proposal = getattr(center.planner, method)(pid, by=self.operator(), **kw)
            self._after(f"{proposal.id}: {proposal.status}"
                        + (" (postponed)" if proposal.postponed else "")
                        + (f", priority {proposal.priority}" if method == "prioritize" else ""))
        except Exception as exc:
            self._after(str(exc))

    def _implement(self, pid: str) -> None:
        center = self.center()
        try:
            proposal = center.planner.implement(pid, by=self.operator(), before=center.vitals())
        except Exception as exc:
            self._after(str(exc))
            return
        if proposal.applied:
            self._after(f"{pid}: setting changed, backup taken. {_took_effect(proposal.applied)}")
        else:
            self._after(f"{pid}: in development. The work is yours; nothing was changed.")

    def _complete(self, pid: str) -> None:
        center = self.center()
        try:
            center.planner.complete(pid, by=self.operator(), after=center.vitals())
            self._after(f"{pid}: COMPLETED.")
        except Exception as exc:
            self._after(str(exc))

    def _hand_over(self, pid: str) -> None:
        """Give an approved, verifiable proposal to the built-in agent.

        The agent's gate — the suite, the ratchet, the protected files —
        decides whether anything it writes is kept. The center only passes
        the brief on, and only on this press.
        """
        center = self.center()
        try:
            p = center.planner.get(pid)
        except Exception as exc:
            self._after(str(exc))
            return
        brief = (f"{p.title}\n\n{p.summary}\n\nIt is done when:\n"
                 + "\n".join(f"- {t}" for t in p.testing)
                 + "\n\nFollow AGENTS.md. This was approved in the System Evolution Center "
                   f"as {p.id}.")
        hand = getattr(self.app, "hand_to_agent", None)
        ok, message = hand(brief) if callable(hand) else (False, "The built-in agent is not available.")
        if not ok:
            self._after(message or "The agent did not take the request.")
            return
        try:
            center.planner.implement(pid, by=self.operator(), note="handed to the built-in agent",
                                     before=center.vitals())
        except Exception as exc:
            self.app.toast(str(exc))

    def _discover(self) -> None:
        center = self.center()
        found = center.discover() if center is not None else []
        self._after(f"{len(found)} new item{'s' if len(found) != 1 else ''} discovered. "
                    "Nothing is approved until you approve it." if found
                    else "Nothing new: everything the center has noticed is already here.")

    def _rebuild_report(self) -> None:
        center = self.center()
        if center is not None:
            center.sample(visible=True, panel="diagnostics", force=True)
        self._build_report()
        self._after()

    def _export(self, fmt: str) -> None:
        center = self.center()
        try:
            path = center.export(self._report_kind, fmt)
            self._exported = str(path)
            self._after(f"Report saved: {path.name}")
        except Exception as exc:
            self._after(f"Unable to export: {exc}")

    def _reload_history(self) -> None:
        self._after()

    # -- input -------------------------------------------------------------------
    def on_release(self, x: float, y: float, pressed) -> None:
        strip = self.g.get("tabs")
        if pressed is strip and strip is not None:
            strip.pressed = False
            self.app.play("beep")
            strip.click_at(x, y)                                  # type: ignore[attr-defined]
            return
        rows = self.g.get("rows")
        if pressed is rows and rows is not None:
            rows.pressed = False
            key = rows.row_at(x, y)                               # type: ignore[attr-defined]
            if key:
                self.app.play("beep")
                self._select(key)
            return
        super().on_release(x, y, pressed)

    def on_scroll(self, x: float, y: float, dy: float) -> bool:
        rows = self.g.get("rows")
        if rows is not None and rows.contains(x, y):
            rows.scroll(2 if dy > 0 else -2)                      # type: ignore[attr-defined]
            return True
        panel = self.g.get("detail")
        if panel is not None and panel.contains(x, y):
            step = self.frame.ctx.u(2.0) if self.frame is not None else 30.0
            panel.scroll_by(dy * step)                            # type: ignore[attr-defined]
            return True
        return False

    # -- painting ----------------------------------------------------------------
    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        u = ctx.u
        notice = getattr(self, "notice_rect", None)
        if notice is not None:
            x, y, w, h = notice
            why = getattr(self.app, "evolution_error", "") or \
                "Switched off: [evolution] enabled = false in config.toml."
            draw_text(cr, ctx, "SYSTEM EVOLUTION CENTER UNAVAILABLE", x + w / 2, y + u(2.0),
                      u(1.2), ctx.color("alert"), align="center", valign="middle")
            draw_text(cr, ctx, why, x + w / 2, y + u(4.0), u(0.78), ctx.color("accent5"),
                      align="center", valign="top", max_width=w * 0.8, wrap=True)
            return
        if self.section != "overview":
            return
        rect = getattr(self, "overview_rect", None)
        if rect is None:
            return
        x, y, w, h = rect
        rows = self._overview_rows()
        size = u(0.62)
        cursor = y
        for key, value in rows:
            draw_text(cr, ctx, key, x, cursor, size * 0.92, ctx.ink("accent3"),
                      align="left", valign="top", max_width=w)
            _, th = draw_text(cr, ctx, value, x, cursor + size * 1.25, size * 1.12,
                              ctx.color("text"), align="left", valign="top",
                              max_width=w, wrap=True, line_height=1.15)
            cursor += size * 1.25 + max(th, size) + u(0.42)
            if cursor > y + h:
                break
