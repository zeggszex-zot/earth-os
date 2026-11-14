"""The Diagnostic Reporter: what the center knows, written down.

Seven reports, each as plain text or JSON. A report is built from the same
snapshot, findings and roadmap the panels show — it takes no reading of its
own beyond asking the center to make sure it has one — so a report and the
screen it was exported from cannot disagree.

Every report carries when it was made, on what, and **which readings were
unavailable**. A report that silently omitted what it could not measure
would read as a clean bill of health for it.
"""

from __future__ import annotations

import json
import time
from typing import Dict, List, Tuple

from ..capability_corpus import rank_memory
from .health import counts, worst
from .model import (
    CATEGORIES, COMPLETED, IN_DEVELOPMENT, ROLLED_BACK, STATUSES, TESTING, Snapshot,
    human_bytes, human_span,
)

KINDS: Dict[str, str] = {
    "health": "System health report",
    "cpu": "CPU performance report",
    "ram": "RAM consumption report",
    "applications": "Application diagnostics",
    "hardware": "Hardware compatibility report",
    "optimization": "Optimization recommendations",
    "progress": "Development progress report",
}

Section = Dict[str, object]


def _section(heading: str, rows: List[Tuple[str, str]] = (), lines: List[str] = ()) -> Section:
    return {"heading": heading, "rows": [list(r) for r in rows], "lines": list(lines)}


def _pct(value: object) -> str:
    try:
        return f"{float(value) * 100:.0f}%"                     # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "unavailable"


class Reporter:
    def __init__(self, center):
        self.center = center

    # -- building ----------------------------------------------------------------
    def build(self, kind: str) -> Dict[str, object]:
        if kind not in KINDS:
            raise ValueError(f"no such report: {kind} (one of {', '.join(KINDS)})")
        center = self.center
        # A report wants the process list whatever panel happens to be up.
        if center.snapshot is None or not center.snapshot.get("processes").ok:
            center.sample(visible=True, force=True)
        snap = center.snapshot
        sections: List[Section] = getattr(self, f"_{kind}")(snap)
        missing = [f"{r.name}: {r.why}" for r in snap.unavailable() if r.why != "not read"]
        if missing:
            sections.append(_section("Readings that were unavailable", lines=missing))
        return {
            "report": kind, "title": KINDS[kind],
            "generated": time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(snap.taken)),
            "generated_epoch": snap.taken,
            "host": snap.value("host", "unknown"),
            "system": snap.value("distro", "unknown"),
            "earth_os": snap.value("version", "unknown"),
            "note": "Every figure is a reading taken from this machine through the "
                    "capability layer, except Earth OS's own processor time, which is "
                    "this process's own clock. Anything marked ESTIMATE is an expectation, "
                    "not a measurement.",
            "sections": sections,
        }

    def _identity(self, snap: Snapshot) -> Section:
        uptime = snap.get("uptime")
        return _section("System", rows=[
            ("Earth OS version", str(snap.value("version", "unknown"))),
            ("Platform adapter", str(snap.value("platform", "unavailable"))),
            ("Operating system", str(snap.value("distro", "unavailable"))),
            ("Kernel", str(snap.value("kernel", "unavailable"))),
            ("Processor", str(snap.value("cpu_model", "unavailable"))),
            ("Cores", str(snap.value("cpu_count", "unavailable"))),
            ("Uptime", human_span(float(uptime.value)) if uptime.ok else "unavailable"),  # type: ignore[arg-type]
        ])

    def _findings(self, areas: Tuple[str, ...] = ()) -> List[Section]:
        out = []
        for f in self.center.findings():
            if areas and f.area not in areas:
                continue
            lines = list(f.evidence)
            if f.explanation:
                lines.append("Why it matters: " + f.explanation)
            if f.suggestion:
                lines.append("Suggested: " + f.suggestion)
            out.append(_section(f"[{f.severity}] {f.title}", lines=lines))
        return out

    def _health(self, snap: Snapshot) -> List[Section]:
        found = self.center.findings()
        tally = counts(found)
        summary = _section("Summary", rows=[
            ("Overall", worst(found)),
            *[(name, str(tally[name])) for name in tally],
            ("Component checks", "run" if self.center.doctor is not None
             else "not run this session"),
        ])
        return [self._identity(snap), summary] + self._findings()

    def _trend_rows(self, name: str, as_pct: bool = True) -> List[Tuple[str, str]]:
        stat = self.center.trends.stat(name)
        if stat is None:
            return [("History", "no samples yet")]
        show = _pct if as_pct else (lambda v: human_bytes(v))
        return [("Samples", f"{stat.count} over {human_span(stat.span)}"),
                ("Lowest", show(stat.low)), ("Mean", show(stat.mean)),
                ("Highest", show(stat.high)), ("Latest", show(stat.last))]

    def _cpu(self, snap: Snapshot) -> List[Section]:
        cpu, cores, load = snap.get("cpu"), snap.get("cores"), snap.get("load")
        now = _section("Now", rows=[
            ("Utilisation", _pct(cpu.value) if cpu.ok else f"unavailable ({cpu.why})"),
            ("Load average (1, 5, 15 min)",
             " / ".join(f"{v:.2f}" for v in load.value) if load.ok else "unavailable"),  # type: ignore[union-attr]
            ("Cores", str(snap.value("cpu_count", "unavailable"))),
            ("Processor", str(snap.value("cpu_model", "unavailable"))),
        ])
        per_core = _section("Per core", rows=(
            [(f"Core {i}", _pct(v)) for i, v in enumerate(cores.value)]    # type: ignore[arg-type]
            if cores.ok else [("Per core", f"unavailable ({cores.why})")]))
        history = _section("This session", rows=self._trend_rows("cpu"))
        return [now, per_core, history] + self._findings(("processor", "thermal", "rendering"))

    def _ram(self, snap: Snapshot) -> List[Section]:
        memory, swap = snap.get("memory"), snap.get("swap")
        rows: List[Tuple[str, str]] = []
        if memory.ok:
            m = memory.value
            rows += [("In use", f"{human_bytes(m['used'])} of {human_bytes(m['total'])} "      # type: ignore[index]
                                f"({_pct(m['fraction'])})"),                                   # type: ignore[index]
                     ("Available", human_bytes(m["available"]))]                               # type: ignore[index]
        else:
            rows.append(("Memory", f"unavailable ({memory.why})"))
        if swap.ok:
            s = swap.value
            rows.append(("Swap", "none configured" if not s["total"] else                      # type: ignore[index]
                         f"{human_bytes(s['used'])} of {human_bytes(s['total'])}"))            # type: ignore[index]
        own = snap.get("own_memory")
        rows.append(("Earth OS itself", human_bytes(own.value) if own.ok
                     else f"unavailable ({own.why})"))
        sections = [_section("Now", rows=rows),
                    _section("This session", rows=self._trend_rows("memory"))]
        ranking = rank_memory(snap.value("processes"))
        if ranking is not None:
            sections.append(_section(
                "Largest consumers, by program",
                rows=[(name, f"{human_bytes(size)} in {count} process{'es' if count != 1 else ''}")
                      for name, size, count in ranking.top(12)],
                lines=["Resident memory summed over each program's processes. Memory shared "
                       "between processes is counted in each."]))
        growth = self.center.trends.growth("own_memory")
        if growth is not None:
            sections.append(_section("Earth OS's own memory over time", rows=[
                ("First third of the span", human_bytes(growth.start)),
                ("Last third of the span", human_bytes(growth.end)),
                ("Span", f"{human_span(growth.span)}, {growth.count} samples"),
                ("Rose in every third", "yes" if growth.steady else "no")]))
        return sections + self._findings(("memory", "leaks"))

    def _applications(self, snap: Snapshot) -> List[Section]:
        sections = [self._identity(snap)]
        errors = {k: v for k, v in self.center.errors().items() if v}
        sections.append(_section("Earth OS components", rows=(
            list(errors.items()) or [("Start-up errors", "none recorded")])))
        render = snap.get("render")
        if render.ok:
            r = render.value
            sections.append(_section("Renderer", rows=[
                ("State", str(r.get("state"))), ("Adaptive", str(r.get("adaptive"))),          # type: ignore[union-attr]
                ("Frame-rate ceiling", str(r.get("fps"))),                                      # type: ignore[union-attr]
                ("Frames drawn", str(r.get("frames_drawn"))),                                   # type: ignore[union-attr]
                ("Mean draw", f"{float(r.get('draw_ms_mean') or 0):.1f} ms over "               # type: ignore[union-attr]
                              f"{r.get('draws_timed')} draws")]))                               # type: ignore[union-attr]
            jobs = r.get("jobs") or {}                                                          # type: ignore[union-attr]
            sections.append(_section("Background jobs", rows=[
                (name, f"ran {j.get('ran', 0)}, changed the picture {j.get('changed', 0)}")
                for name, j in sorted(jobs.items())]))
        if self.center.doctor is not None:
            sections.append(_section("Component checks", rows=[
                (name, f"{status.upper()} — {detail.splitlines()[0] if detail else ''}")
                for status, name, detail in self.center.doctor]))
        else:
            sections.append(_section("Component checks",
                                     lines=["Not run this session. RUN CHECKS on DIAGNOSTICS, "
                                            "or earth-os --doctor."]))
        overhead = self.center.overhead()
        sections.append(_section("The Evolution Center's own cost", rows=[
            ("Samples taken", str(int(overhead["samples"]))),
            ("Mean time per sample", f"{overhead['mean_ms']:.2f} ms"),
            ("Total", f"{overhead['total_ms']:.1f} ms this session")]))
        return sections + self._findings(("applications", "services", "configuration", "processes"))

    def _hardware(self, snap: Snapshot) -> List[Section]:
        sections = [self._identity(snap)]
        hardware = self.center.inspector.hardware()
        if hardware.ok:
            rows = []
            for device in hardware.value:                                  # type: ignore[union-attr]
                data = getattr(device, "data", {}) or {}
                detail = ", ".join(f"{k} {human_bytes(v) if k in ('total', 'available') else v}"
                                   for k, v in data.items())
                rows.append((f"{getattr(device, 'name', '?')} "
                             f"({getattr(device, 'device_type', '?')})",
                             f"{getattr(device, 'status', '?')}" + (f" — {detail}" if detail else "")))
            sections.append(_section("Hardware the platform lists", rows=rows))
        else:
            sections.append(_section("Hardware", lines=[f"Unavailable: {hardware.why}."]))

        registry = getattr(self.center.port, "capabilities", None)
        if registry is not None:
            names = list(registry.names())
            domains: Dict[str, List[int]] = {}
            for name in names:
                tally = domains.setdefault(name.split(".", 1)[0], [0, 0])
                tally[1] += 1
                tally[0] += 1 if registry.supported(name) else 0
            working = sum(t[0] for t in domains.values())
            sections.append(_section(
                f"Capabilities available on this machine: {working} of {len(names)}",
                rows=[(domain, f"{ok} of {total}" + ("" if ok == total else "  — incomplete"))
                      for domain, (ok, total) in sorted(domains.items())]))
        platform = getattr(self.center.port, "platform", None)
        mechanisms = getattr(platform, "mechanisms", None)
        if callable(mechanisms):
            try:
                found = dict(mechanisms())
                sections.append(_section("What the adapter uses for each area",
                                         rows=sorted((k, str(v)) for k, v in found.items())))
            except Exception:
                pass
        for name in ("temperature", "battery"):
            reading = snap.get(name)
            sections.append(_section(name.capitalize(), lines=[
                (f"{reading.value}" if reading.ok else f"Unavailable: {reading.why}.")]))
        return sections

    def _optimization(self, snap: Snapshot) -> List[Section]:
        sections = []
        for o in self.center.optimizations():
            sections.append(_section(f"[{o.state}] {o.title}", rows=[
                ("1. What it changes", o.changes),
                ("2. Why it could help", o.why),
                ("3. Expected benefit", o.benefits),
                ("   Basis of the estimate", o.basis),
                ("4. Disadvantages", o.drawbacks),
                ("5. Restart needed", "yes" if o.restart else "no"),
                ("6. How to reverse", o.reverse),
                ("Now", o.reason),
                ("Applied by", "the center, once approved" if o.change else "a person")]))
        return sections

    def _progress(self, snap: Snapshot) -> List[Section]:
        planner = self.center.planner
        tally = planner.counts()
        sections = [_section("Roadmap", rows=[(s, str(tally[s])) for s in STATUSES])]
        for p in planner.ordered():
            rows = [("Status", p.status + ("  (postponed)" if p.postponed else "")),
                    ("Priority / difficulty", f"{p.priority} / {p.difficulty}"),
                    ("Kind", f"{p.kind}, rung {p.rung}"),
                    ("Dependencies", ", ".join(p.dependencies) or "none"),
                    ("Testing", " | ".join(p.testing) or "none stated"),
                    ("Reverse", p.reverse or "not stated")]
            if p.applied:
                rows.append(("Applied", f"{p.applied.get('setting')} = {p.applied.get('value')} "
                                        f"(was {p.applied.get('previous')}); "
                                        f"backup {p.applied.get('backup') or 'none needed'}"))
            if p.expected:
                rows.append(("Expected — an ESTIMATE, not a measurement",
                             p.expected + (f" Basis: {p.basis}" if p.basis else "")))
            for label in ("before", "after"):
                if label in p.measured:
                    rows.append((f"Measured {label}", _vitals(p.measured[label])))
            if "before" in p.measured and p.status in (IN_DEVELOPMENT, TESTING, COMPLETED,
                                                       ROLLED_BACK):
                comparison = self.center.comparison(p)
                verified = isinstance(p.measured.get("comparison"), dict)
                heading = ("MEASURED before → after" if verified
                           else "MEASURED before → after, so far (not yet verified)")
                for row in comparison.get("rows") or []:
                    rows.append((f"{heading}: {row['label']}", _comparison_row(row)))
                for note in comparison.get("notes") or []:
                    rows.append((f"{heading}: note", note))
            history = [f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(e.get('at', 0)))}  "
                       f"{e.get('status')}  by {e.get('by')}  — {e.get('note')}"
                       for e in p.events]
            sections.append(_section(f"{p.id}  {p.title}", rows=rows, lines=history))
        groups = self.center.registry.grouped(self.center.capabilities())
        sections.append(_section("Capability gaps found", rows=[
            (name, str(len(groups.get(name, [])))) for name in CATEGORIES]))
        return sections

    # -- formats -----------------------------------------------------------------
    @staticmethod
    def as_json(report: Dict[str, object]) -> str:
        return json.dumps(report, indent=2, default=str) + "\n"

    @staticmethod
    def as_text(report: Dict[str, object]) -> str:
        out = [str(report["title"]).upper(), "=" * 72,
               f"Generated  {report['generated']}",
               f"Host       {report['host']}  ·  {report['system']}",
               f"Earth OS   {report['earth_os']}", "", str(report["note"]), ""]
        for section in report["sections"]:                               # type: ignore[union-attr]
            out.append(str(section["heading"]))
            out.append("-" * min(len(str(section["heading"])), 72))
            rows = section["rows"]
            width = min(max((len(str(r[0])) for r in rows), default=0), 34)
            for key, value in rows:
                out.append(f"  {str(key).ljust(width)}  {value}")
            for line in section["lines"]:
                out.append(f"  {line}")
            out.append("")
        return "\n".join(out)


def _comparison_row(row: Dict[str, object]) -> str:
    from .compare import format_figure
    change = float(row["delta"])
    sign = "+" if change >= 0 else "−"
    percent = row.get("percent")
    rel = f", {sign}{abs(float(percent)):.0f}%" if isinstance(percent, (int, float)) else ""
    return (f"{format_figure(row, 'before')} → {format_figure(row, 'after')} "
            f"({sign}{format_figure({**row, 'delta': abs(change)}, 'delta')}{rel}) — "
            f"{row['verdict']}"
            + (", but already moving before the change" if row.get("already_moving") else "")
            + f" ({row['n_before']} and {row['n_after']} intervals)")


def _vitals(vitals: object) -> str:
    if not isinstance(vitals, dict):
        return "none"
    if "figures" in vitals:                         # the current format: compare.summarize
        span = human_span(float(vitals.get("span_seconds") or 0.0))
        on = vitals.get("center_on_screen")
        where = f", center on screen {float(on):.0%} of the time" if isinstance(on, (int, float)) else ""
        if not vitals.get("samples"):
            return "no samples in that window"
        return f"{vitals.get('samples')} samples over {span}{where}"
    parts = []
    for name, label, pct in (("cpu", "processor", True), ("memory", "memory", True),
                             ("draw_ms", "draw", False)):
        stat = vitals.get(name)
        if isinstance(stat, dict):
            mean = _pct(stat.get("mean")) if pct else f"{float(stat.get('mean') or 0):.1f} ms"
            parts.append(f"{label} mean {mean} ({stat.get('samples')} samples "
                         f"over {human_span(float(stat.get('span_seconds') or 0))})")
    if "frames_per_second" in vitals:
        parts.append(f"{float(vitals['frames_per_second']):.1f} frames/s")
    return "; ".join(parts) or "no samples"
