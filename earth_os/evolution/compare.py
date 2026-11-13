"""Before and after: what an approved change measurably did.

An optimization arrives with an *estimate* — arithmetic, or a figure measured
on another machine, always marked ``ESTIMATE:``. Once it is applied the center
can say what actually happened here, and this module is where that is worked
out. Nothing is read in it: it is arithmetic on samples the center already
took, so it costs nothing until somebody looks.

Three rules keep a comparison honest.

1. **Windows that do not overlap.** *Before* is the samples taken in the five
   minutes up to the change; *after* is the samples taken from a few seconds
   after it. Earlier versions summarised "the last two minutes" at each end,
   so an *after* taken soon enough held *before* samples too.

2. **Earth OS's own cost, from counters.** Processor time, frames drawn and
   time spent drawing are running totals in each sample. A window's figure
   is the change in the total over the change in time — exact for that
   window, however unevenly the samples fell. The whole machine's load is
   shown for context and labelled as such: everything else running moves it.

3. **A difference is only called one if it is larger than the noise.** Each
   figure is also worked out interval by interval, and a difference is
   reported as measured only when it exceeds twice the standard error of the
   two windows together. Otherwise the comparison says the difference is
   within the variation measured — which is a result, not a failure.

The windows also record how much of the time the center itself was on
screen, because sampling and drawing it costs something: a *before* taken
while looking at the center and an *after* taken while it was hidden are not
like for like, and the comparison says so.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .trends import Sample

#: The samples before the change that are compared: this many seconds of them.
BEFORE_SECONDS = 300.0
#: Samples this soon after the change are left out: the frame clock and the
#: timers are being re-armed, and the first interval straddles the change.
SETTLE_SECONDS = 5.0
#: Fewer intervals than this on either side and nothing is concluded.
MIN_INTERVALS = 4

MEASURED = "MEASURED DIFFERENCE"
WITHIN = "WITHIN THE VARIATION MEASURED"
TOO_FEW = "TOO FEW SAMPLES"

#: ``(key, label, unit, scope)``. Scope ``earth-os`` is the effect being looked
#: for; ``machine`` is context.
FIGURES: Tuple[Tuple[str, str, str, str], ...] = (
    ("own_cpu", "Earth OS processor time", "% of one core", "earth-os"),
    ("frames_per_second", "Frames drawn", "per second", "earth-os"),
    ("draw_ms", "Time to draw one frame", "ms", "earth-os"),
    ("own_memory", "Earth OS memory", "MB", "earth-os"),
    ("cpu", "Whole machine: processor", "%", "machine"),
    ("memory", "Whole machine: memory in use", "%", "machine"),
)


def _mean_sd(values: Sequence[float]) -> Tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    return mean, math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def _counter(samples: List[Sample], name: str, scale: float = 1.0
             ) -> Optional[Tuple[float, List[float]]]:
    """``(window rate, per-interval rates)`` of a running total, or None.

    An interval whose total went down was a reset (a new window) and is
    skipped, as :meth:`Trends.rate` does.
    """
    total_change = total_time = 0.0
    rates: List[float] = []
    for a, b in zip(samples, samples[1:]):
        va, vb = a.get(name), b.get(name)
        dt = b.at - a.at
        if va is None or vb is None or dt <= 0 or vb < va:
            continue
        total_change += vb - va
        total_time += dt
        rates.append((vb - va) / dt * scale)
    if not rates or total_time <= 0:
        return None
    return total_change / total_time * scale, rates


def _per_frame(samples: List[Sample]) -> Optional[Tuple[float, List[float]]]:
    """Mean milliseconds per frame over the window, from the two totals."""
    total_ms = total_frames = 0.0
    each: List[float] = []
    for a, b in zip(samples, samples[1:]):
        ma, mb = a.get("draw_total_ms"), b.get("draw_total_ms")
        ca, cb = a.get("draws_timed"), b.get("draws_timed")
        if None in (ma, mb, ca, cb) or cb < ca or mb < ma:
            continue
        if cb > ca:
            total_ms += mb - ma
            total_frames += cb - ca
            each.append((mb - ma) / (cb - ca))
    if not each or total_frames <= 0:
        return None
    return total_ms / total_frames, each


def summarize(samples: List[Sample]) -> Dict[str, object]:
    """One window of samples as figures, ready to store as JSON.

    Every figure carries its mean, the spread between intervals and how many
    there were. A figure no sample carried is simply absent.
    """
    out: Dict[str, object] = {"samples": len(samples), "figures": {}}
    if not samples:
        return out
    out["from"] = samples[0].wall
    out["to"] = samples[-1].wall
    out["span_seconds"] = round(samples[-1].at - samples[0].at, 1)
    out["center_on_screen"] = round(sum(1 for s in samples if s.visible) / len(samples), 2)
    figures: Dict[str, Dict[str, float]] = {}

    found = {
        "own_cpu": _counter(samples, "cpu_time", 100.0),
        "frames_per_second": _counter(samples, "frames_drawn"),
        "draw_ms": _per_frame(samples),
    }
    for key, result in found.items():
        if result is not None:
            mean, parts = result
            _m, sd = _mean_sd(parts)
            figures[key] = {"mean": mean, "sd": sd, "n": len(parts)}

    for key, field, scale in (("own_memory", "own_memory", 1 / (1024 * 1024)),
                              ("cpu", "cpu", 100.0), ("memory", "memory", 100.0)):
        values = [float(v) * scale for v in (s.get(field) for s in samples) if v is not None]
        if values:
            mean, sd = _mean_sd(values)
            figures[key] = {"mean": mean, "sd": sd, "n": len(values)}
    out["figures"] = figures
    return out


def compare(before: object, after: object) -> Dict[str, object]:
    """The rows of a before-and-after comparison, and what to bear in mind.

    Each row: the figure, its two means, the difference, and one of
    :data:`MEASURED`, :data:`WITHIN` or :data:`TOO_FEW`.
    """
    result: Dict[str, object] = {"rows": [], "notes": []}
    notes: List[str] = result["notes"]                     # type: ignore[assignment]
    if not isinstance(before, dict) or "figures" not in before:
        notes.append("No before-figures in this format were recorded (the change was "
                     "carried out before comparisons existed, or with no samples).")
        return result
    if not isinstance(after, dict) or not after.get("samples"):
        notes.append("Nothing has been sampled since the change yet. Leave Earth OS "
                     "running — with the center open it samples every few seconds.")
        return result

    rows: List[Dict[str, object]] = result["rows"]          # type: ignore[assignment]
    b_fig, a_fig = before.get("figures") or {}, after.get("figures") or {}
    for key, label, unit, scope in FIGURES:
        b, a = b_fig.get(key), a_fig.get(key)
        if not isinstance(b, dict) or not isinstance(a, dict):
            continue
        delta = float(a["mean"]) - float(b["mean"])
        row = {"key": key, "label": label, "unit": unit, "scope": scope,
               "before": float(b["mean"]), "after": float(a["mean"]), "delta": delta,
               "percent": (delta / float(b["mean"]) * 100.0) if b["mean"] else None,
               "n_before": int(b["n"]), "n_after": int(a["n"])}
        if min(int(b["n"]), int(a["n"])) < MIN_INTERVALS:
            row["verdict"] = TOO_FEW
        else:
            noise = 2.0 * math.sqrt(float(b["sd"]) ** 2 / int(b["n"])
                                    + float(a["sd"]) ** 2 / int(a["n"]))
            row["verdict"] = MEASURED if abs(delta) > max(noise, 1e-9) else WITHIN
        rows.append(row)

    on_b, on_a = before.get("center_on_screen"), after.get("center_on_screen")
    if isinstance(on_b, (int, float)) and isinstance(on_a, (int, float)) \
            and abs(on_b - on_a) > 0.5:
        notes.append(f"Not like for like: the center was on screen for {on_b:.0%} of the "
                     f"samples before and {on_a:.0%} after, and looking at it costs "
                     "something itself.")
    notes.append("Whole-machine figures include everything else running: they are "
                 "context, not the effect of the change.")
    notes.append("Measured on this machine, over these two windows, and nowhere else.")
    return result


def format_figure(row: Dict[str, object], which: str) -> str:
    value = row.get(which)
    if value is None:
        return "–"
    unit = str(row.get("unit"))
    if unit == "ms":
        return f"{float(value):.2f} ms"
    if unit == "MB":
        return f"{float(value):.1f} MB"
    if unit.startswith("%"):
        return f"{float(value):.2f}%"
    return f"{float(value):.2f}/s"


def describe(comparison: Dict[str, object]) -> str:
    """The comparison as lines a person reads: one per figure, then the notes."""
    lines: List[str] = []
    for row in comparison.get("rows") or []:        # type: ignore[union-attr]
        change = float(row["delta"])
        percent = row.get("percent")
        sign = "+" if change >= 0 else "−"
        delta = format_figure({**row, "delta": abs(change)}, "delta")
        rel = f", {sign}{abs(percent):.0f}%" if isinstance(percent, (int, float)) else ""
        lines.append(f"{row['label']}: {format_figure(row, 'before')} → "
                     f"{format_figure(row, 'after')} ({sign}{delta}{rel}) — {row['verdict']} "
                     f"[{row['n_before']} vs {row['n_after']} intervals]")
    for note in comparison.get("notes") or []:       # type: ignore[union-attr]
        lines.append(f"Note: {note}")
    return "\n".join(lines) or "Nothing to compare yet."
