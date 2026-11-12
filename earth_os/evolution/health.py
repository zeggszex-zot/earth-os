"""The Health Analyzer: what the readings mean, by rule.

Every finding here is the result of a comparison anyone can re-do by hand:
a reading, a threshold, a sentence. No model is consulted and nothing is
estimated. Three things follow from that and are worth keeping true:

* **A reading that is missing is skipped, never assumed.** The rule for
  memory does not run without a memory reading; it reports that the reading
  is unavailable and says why. *"Nothing is wrong"* is only said about what
  was actually measured.
* **The cut points are the ones the machine already uses.** *"Why is my
  computer running slowly"* is answered by ``performance_report`` in
  :mod:`earth_os.capability_corpus`, which calls a processor *busy* at 60%
  and *saturated* at 85%, and memory *under pressure* at 80% and *nearly
  full* at 90%. Those are the MODERATE and HIGH lines here, so the two
  cannot give a person different verdicts on the same readings.
  ``tests/test_evolution.py`` feeds both the same values.
* **What cannot be measured is said to be unmeasurable.** Earth OS has no
  reading for the state of system services. That is reported as exactly
  that, and filed with the Capability Registry — not papered over with a
  finding that everything is running.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..capability_corpus import rank_memory
from .model import (
    CRITICAL, HIGH, INFORMATIONAL, LOW, MODERATE, Finding, Snapshot, human_bytes,
    human_span, severity_rank,
)
from .trends import Trends

# -- cut points. The first two rows are performance_report's own. ----------------
CPU_BUSY, CPU_SATURATED = 0.60, 0.85
LOAD_BUSY, LOAD_SATURATED = 1.0, 1.5              # load average per core
MEMORY_PRESSURE, MEMORY_NEARLY_FULL, MEMORY_EXHAUSTED = 0.80, 0.90, 0.95
SWAP_BYTES, SWAP_FRACTION = 256 * 1024 * 1024, 0.25
STORAGE_FILLING, STORAGE_PRESSURE, STORAGE_NEARLY_FULL, STORAGE_EXHAUSTED = 0.80, 0.90, 0.95, 0.98
TEMP_WARM, TEMP_HOT, TEMP_CRITICAL = 75.0, 85.0, 95.0
#: How long a saturated processor has to stay that way to be CRITICAL.
SUSTAINED_SECONDS = 60.0
#: One program holding this much of all memory is worth a line.
LARGE_CONSUMER = 0.25
#: Growth in this process's own memory that is worth calling a possible leak.
LEAK_BYTES = 32 * 1024 * 1024
LEAK_MIN_SAMPLES, LEAK_MIN_SPAN = 9, 600.0


#: The order findings of equal severity are listed in: the machine's own
#: condition first, then Earth OS's parts, then the per-component checks.
#: There can be a dozen of those at one severity, and listed alphabetically
#: they stood in front of "storage is filling up" — the one row in the list
#: that was about the machine.
AREA_ORDER = ("processor", "memory", "storage", "thermal", "processes", "leaks",
              "rendering", "applications", "services", "configuration")


def _area_rank(area: str) -> int:
    return AREA_ORDER.index(area) if area in AREA_ORDER else len(AREA_ORDER)


def _pct(fraction: float) -> str:
    return f"{fraction * 100:.0f}%"


class HealthAnalyzer:
    """Turns a snapshot (and what is known over time) into findings."""

    def __init__(self, trends: Optional[Trends] = None):
        self.trends = trends

    def analyze(self, snap: Snapshot, *, config=None,
                doctor: Optional[List[Tuple[str, str, str]]] = None,
                errors: Optional[Dict[str, str]] = None,
                error_log: Optional[Tuple[str, float]] = None) -> List[Finding]:
        """Every finding, most severe first; at one severity, in ``AREA_ORDER``.

        *doctor* is ``Shell.doctor_rows()`` if the checks have been run, else
        None. *errors* is what the shell recorded failing as it started.
        *error_log* is ``(path, age_in_seconds)`` of the last-failure log, if
        there is one.
        """
        findings: List[Finding] = []
        for rule in (self._processor, self._memory, self._swap, self._storage,
                     self._thermal, self._processes, self._leaks, self._rendering):
            findings.extend(rule(snap, config))
        findings.extend(self._services(snap))
        findings.extend(self._applications(errors or {}, error_log))
        findings.extend(self._configuration(config, doctor))
        findings.sort(key=lambda f: (-severity_rank(f.severity), _area_rank(f.area), f.id))
        return findings

    # -- processor ---------------------------------------------------------------
    def _processor(self, snap: Snapshot, config) -> List[Finding]:
        cpu = snap.get("cpu")
        if not cpu.ok:
            return [Finding("processor.unavailable", "processor", INFORMATIONAL,
                            "Processor load could not be read",
                            "No conclusion is drawn about the processor.",
                            evidence=[f"Reading unavailable: {cpu.why}."])]
        load = float(cpu.value)                                   # type: ignore[arg-type]
        evidence = [f"Processor utilisation is {_pct(load)}."]
        per_core = 0.0
        average = snap.value("load")
        cores = snap.value("cpu_count")
        if isinstance(average, tuple) and average and cores:
            per_core = float(average[0]) / int(cores)             # type: ignore[arg-type]
            evidence.append(f"Load average {float(average[0]):.2f} across {int(cores)} "  # type: ignore[arg-type]
                            f"core{'s' if int(cores) != 1 else ''} "                      # type: ignore[arg-type]
                            f"({per_core:.2f} per core).")

        if load >= CPU_SATURATED or per_core >= LOAD_SATURATED:
            severity, title = HIGH, "The processor is saturated"
            stat = self.trends.stat("cpu", SUSTAINED_SECONDS) if self.trends else None
            if stat is not None and stat.count >= 3 and stat.span >= SUSTAINED_SECONDS * 0.8 \
                    and stat.low >= CPU_SATURATED:
                severity = CRITICAL
                title = "The processor has been saturated for over a minute"
                evidence.append(f"Lowest of {stat.count} samples over {human_span(stat.span)}: "
                                f"{_pct(stat.low)}.")
            return [Finding("processor.saturated", "processor", severity, title,
                            "Work is queueing for the processor, so everything else waits.",
                            "See which program is responsible (HEALTH lists the largest "
                            "consumers) and close it from RUNNING PROGRAMS if it is not needed. "
                            "If it is Earth OS itself, see OPTIMIZATION.",
                            evidence, remedy="phosphor-off")]
        if load >= CPU_BUSY or per_core >= LOAD_BUSY:
            return [Finding("processor.busy", "processor", MODERATE, "The processor is busy",
                            "There is still headroom, but less than half of it.",
                            "Nothing needs doing unless it stays this way.", evidence)]
        return [Finding("processor.normal", "processor", INFORMATIONAL,
                        "Processor load is normal", evidence=evidence)]

    # -- memory ------------------------------------------------------------------
    def _memory(self, snap: Snapshot, config) -> List[Finding]:
        memory = snap.get("memory")
        if not memory.ok:
            return [Finding("memory.unavailable", "memory", INFORMATIONAL,
                            "Memory could not be read",
                            "No conclusion is drawn about memory.",
                            evidence=[f"Reading unavailable: {memory.why}."])]
        m: Dict[str, float] = memory.value                        # type: ignore[assignment]
        fraction = float(m["fraction"])
        evidence = [f"Memory is {_pct(fraction)} full: {human_bytes(m['used'])} of "
                    f"{human_bytes(m['total'])} in use, {human_bytes(m['available'])} available."]
        ranking = rank_memory(snap.value("processes"))
        top = ranking.rows[0] if ranking is not None and ranking.rows else None
        if top is not None:
            evidence.append(f"{top[0]} holds the most, about {human_bytes(top[1])} "
                            f"across {top[2]} process{'es' if top[2] != 1 else ''}.")
        swap = snap.value("swap")
        no_swap = isinstance(swap, dict) and not swap.get("total")

        if fraction >= MEMORY_PRESSURE:
            if fraction >= MEMORY_EXHAUSTED:
                severity, title = CRITICAL, "Memory is exhausted"
            elif fraction >= MEMORY_NEARLY_FULL:
                severity, title = HIGH, "Memory is nearly full"
            else:
                severity, title = MODERATE, "Memory is under pressure"
            if no_swap and fraction >= MEMORY_NEARLY_FULL:
                evidence.append("No swap is configured, so there is nowhere for the overflow to go.")
            suggestion = ("Close the largest program if it is not needed."
                          if top is None else
                          f"Close {top[0]} if it is not needed; it is the largest holder.")
            return [Finding("memory.pressure", "memory", severity, title,
                            "When memory runs out the kernel starts stopping programs, "
                            "and before that everything slows as it makes room.",
                            suggestion, evidence, remedy="companion-keep-alive")]
        return [Finding("memory.normal", "memory", INFORMATIONAL,
                        "Memory use is normal", evidence=evidence)]

    def _swap(self, snap: Snapshot, config) -> List[Finding]:
        swap = snap.get("swap")
        if not swap.ok:
            return []
        s: Dict[str, float] = swap.value                          # type: ignore[assignment]
        total, used = int(s.get("total") or 0), int(s.get("used") or 0)
        if total and used >= SWAP_BYTES and used / total >= SWAP_FRACTION:
            return [Finding("memory.swapping", "memory", MODERATE, "The machine is swapping",
                            "Memory that was pushed out to storage is slow to read back, "
                            "which is felt as pauses.",
                            "Free memory: see the largest consumers below.",
                            [f"{human_bytes(used)} of {human_bytes(total)} swap is in use "
                             f"({_pct(used / total)})."])]
        return []

    # -- storage -----------------------------------------------------------------
    def _storage(self, snap: Snapshot, config) -> List[Finding]:
        disk = snap.get("disk")
        if not disk.ok:
            return [Finding("storage.unavailable", "storage", INFORMATIONAL,
                            "Storage could not be read",
                            "No conclusion is drawn about storage.",
                            evidence=[f"Reading unavailable: {disk.why}."])]
        d: Dict[str, float] = disk.value                          # type: ignore[assignment]
        fraction = float(d["fraction"])
        evidence = [f"The system volume is {_pct(fraction)} full: {human_bytes(d['free'])} "
                    f"free of {human_bytes(d['total'])}.",
                    "Only the system volume is read; Earth OS has no reading for other volumes."]
        if fraction >= STORAGE_EXHAUSTED:
            severity, title = CRITICAL, "Storage is exhausted"
        elif fraction >= STORAGE_NEARLY_FULL:
            severity, title = HIGH, "Storage is nearly full"
        elif fraction >= STORAGE_PRESSURE:
            severity, title = MODERATE, "Storage is under pressure"
        elif fraction >= STORAGE_FILLING:
            severity, title = LOW, "Storage is filling up"
        else:
            return [Finding("storage.normal", "storage", INFORMATIONAL,
                            "Storage has room", evidence=evidence)]
        return [Finding("storage.pressure", "storage", severity, title,
                        "A full volume stops programs saving their work and can stop "
                        "the session starting.",
                        "Remove what is not needed from FILES. The center never deletes "
                        "anything itself.", evidence)]

    # -- thermal -----------------------------------------------------------------
    def _thermal(self, snap: Snapshot, config) -> List[Finding]:
        temperature = snap.get("temperature")
        if not temperature.ok:
            return [Finding("thermal.unavailable", "thermal", INFORMATIONAL,
                            "Temperature is not available on this machine",
                            "No conclusion is drawn about temperature.",
                            evidence=[f"Reading unavailable: {temperature.why}."])]
        degrees = float(temperature.value)                        # type: ignore[arg-type]
        evidence = [f"The machine reports {degrees:.0f} °C."]
        if degrees >= TEMP_CRITICAL:
            severity, title = CRITICAL, "The machine is dangerously hot"
        elif degrees >= TEMP_HOT:
            severity, title = HIGH, "The machine is running hot"
        elif degrees >= TEMP_WARM:
            severity, title = MODERATE, "The machine is warm"
        else:
            return [Finding("thermal.normal", "thermal", INFORMATIONAL,
                            "Temperature is normal", evidence=evidence)]
        return [Finding("thermal.hot", "thermal", severity, title,
                        "Processors slow themselves down to shed heat, and sustained heat "
                        "shortens the life of the hardware.",
                        "Check airflow, and what is keeping the processor busy.", evidence)]

    # -- processes ---------------------------------------------------------------
    def _processes(self, snap: Snapshot, config) -> List[Finding]:
        processes = snap.get("processes")
        if not processes.ok:
            if processes.why == "not read":
                return []                 # not asked for on this pass; say nothing
            return [Finding("processes.unavailable", "processes", INFORMATIONAL,
                            "The process list could not be read",
                            evidence=[f"Reading unavailable: {processes.why}."])]
        listed = list(processes.value)                            # type: ignore[arg-type]
        findings: List[Finding] = []

        # Processes that have exited and were never collected by whatever
        # started them. They hold no memory, but each is a slot in the
        # process table and a sign that something is not cleaning up.
        defunct = [p for p in listed if str(getattr(p, "state", "")).upper().startswith("Z")]
        if defunct:
            names = sorted({str(getattr(p, "name", "?")) for p in defunct})
            findings.append(Finding(
                "processes.defunct", "processes", LOW,
                f"{len(defunct)} finished process{'es' if len(defunct) != 1 else ''} "
                "never collected",
                "A program exited and whatever started it has not acknowledged that.",
                "Usually harmless; many of them means the parent program has a fault.",
                [f"{len(defunct)} of {len(listed)} processes are in the finished-but-"
                 f"uncollected state: {', '.join(names[:6])}."]))

        ranking = rank_memory(listed)
        memory = snap.value("memory")
        if ranking is not None and ranking.rows:
            rows = ranking.top(5)
            evidence = [f"{name}: {human_bytes(size)} across {count} "
                        f"process{'es' if count != 1 else ''}." for name, size, count in rows]
            evidence.append(f"{ranking.measured} of {len(listed)} processes report a memory "
                            "figure. Memory shared between processes is counted in each.")
            severity, title = INFORMATIONAL, "Largest memory consumers"
            explanation = suggestion = ""
            if isinstance(memory, dict) and memory.get("total"):
                share = rows[0][1] / float(memory["total"])
                if share >= LARGE_CONSUMER:
                    severity = LOW
                    title = f"{rows[0][0]} holds {_pct(share)} of all memory"
                    explanation = ("One program is holding a large part of the machine. Whether "
                                   "it is unnecessary is yours to say; the center cannot know.")
                    suggestion = ("If you are not using it, close it from RUNNING PROGRAMS. "
                                  "The center will not stop a program itself.")
            findings.append(Finding("processes.memory", "processes", severity, title,
                                    explanation, suggestion, evidence))

        # Per-process processor time. On a platform that does not report it,
        # that is the finding: naming a "busiest program" would be inventing one.
        timed = [p for p in listed if float(getattr(p, "cpu_percent", 0.0) or 0.0) > 0.0]
        if not timed:
            findings.append(Finding(
                "processes.cpu-unreported", "processes", INFORMATIONAL,
                "Processor time per program is not reported",
                "The platform lists processes without a processor figure for any of them, "
                "so the center cannot say which program is using the processor.",
                evidence=[f"{len(listed)} processes listed; none carries a processor figure."],
                remedy="per-process-cpu"))
        return findings

    # -- resource leaks ----------------------------------------------------------
    def _leaks(self, snap: Snapshot, config) -> List[Finding]:
        if self.trends is None:
            return []
        own = self.trends.series("own_memory")
        growth = self.trends.growth("own_memory", LEAK_MIN_SAMPLES, LEAK_MIN_SPAN)
        if growth is None:
            have = len(own)
            return [Finding(
                "leaks.insufficient", "leaks", INFORMATIONAL,
                "Too little history to judge a memory leak",
                "A leak is a trend, and a trend needs time. The center keeps its "
                "history in memory only, so it starts again with each session.",
                evidence=[f"{have} sample{'s' if have != 1 else ''} of Earth OS's own memory "
                          f"over {human_span(self.trends.span())}; "
                          f"{LEAK_MIN_SAMPLES} samples over {human_span(LEAK_MIN_SPAN)} are needed."])]
        evidence = [f"Earth OS's own memory went from {human_bytes(growth.start)} to "
                    f"{human_bytes(growth.end)} over {human_span(growth.span)} "
                    f"({growth.count} samples; first third against last third)."]
        if growth.steady and growth.delta >= LEAK_BYTES:
            evidence.append(f"That is about {human_bytes(growth.per_hour)} an hour, and it "
                            "rose in every third of the span.")
            return [Finding("leaks.own-memory", "leaks", MODERATE,
                            "Earth OS's own memory keeps growing",
                            "Memory that only rises while the machine is doing the same work "
                            "is the signature of a leak. This is a measurement, not a diagnosis: "
                            "it says that it grew, not what is growing.",
                            "Restarting Earth OS returns the memory. Finding the cause is "
                            "development work — propose it from CAPABILITIES.", evidence,
                            remedy="leak-investigation")]
        return [Finding("leaks.none", "leaks", INFORMATIONAL,
                        "No memory growth in Earth OS itself", evidence=evidence)]

    # -- rendering ---------------------------------------------------------------
    def _rendering(self, snap: Snapshot, config) -> List[Finding]:
        render = snap.get("render")
        if not render.ok:
            return [Finding("rendering.unavailable", "rendering", INFORMATIONAL,
                            "Rendering statistics are not available",
                            evidence=[f"Reading unavailable: {render.why}."])]
        r: Dict[str, object] = render.value                       # type: ignore[assignment]
        findings: List[Finding] = []
        fps = int(r.get("fps") or 0)
        drawn, timed = int(r.get("frames_drawn") or 0), int(r.get("draws_timed") or 0)
        mean, peak = float(r.get("draw_ms_mean") or 0.0), float(r.get("draw_ms_peak") or 0.0)
        evidence = [f"Renderer is {str(r.get('state', '?')).lower()}; {drawn} frames drawn "
                    f"this session at a ceiling of {fps} a second."]
        if timed:
            evidence.append(f"Mean cost of a draw: {mean:.1f} ms over {timed} draws "
                            f"(slowest {peak:.1f} ms).")

        if not r.get("adaptive", True):
            findings.append(Finding(
                "rendering.fixed-clock", "rendering", MODERATE,
                "Frames are drawn on a fixed clock",
                "With adaptive rendering off, every frame is drawn whether or not the "
                "picture changed.",
                "Switch adaptive rendering back on (OPTIMIZATION).", list(evidence),
                remedy="adaptive-rendering"))

        slot = 1000.0 / fps if fps > 0 else 0.0
        if timed >= 10 and slot and mean > slot:
            findings.append(Finding(
                "rendering.over-budget", "rendering", HIGH,
                "Drawing a frame takes longer than its slot",
                f"At {fps} frames a second each frame has {slot:.1f} ms. The mean draw "
                "exceeds that, so the interface cannot hold its rate while it is animating.",
                "Lower the frame rate, or switch the phosphor effect off (OPTIMIZATION).",
                list(evidence), remedy="phosphor-off"))
        elif timed >= 10 and slot and mean > slot * 0.5:
            findings.append(Finding(
                "rendering.heavy", "rendering", LOW,
                "Drawing a frame uses more than half its slot",
                f"At {fps} frames a second each frame has {slot:.1f} ms.",
                "No action needed; there is less headroom than there could be.",
                list(evidence), remedy="phosphor-off"))

        rate = self.trends.rate("frames_drawn", 120.0) if self.trends is not None else None
        if rate is not None:
            evidence.append(f"Frames actually drawn: {rate:.1f} a second over the last "
                            "two minutes.")
        if not findings:
            findings.append(Finding("rendering.normal", "rendering", INFORMATIONAL,
                                    "Rendering is within its budget", evidence=evidence))
        return findings

    # -- services ----------------------------------------------------------------
    def _services(self, snap: Snapshot) -> List[Finding]:
        findings = [Finding(
            "services.unmeasurable", "services", INFORMATIONAL,
            "System services cannot be checked",
            "Earth OS has no reading for the state of system services, so the center "
            "cannot tell you whether one has failed. It is not reporting that all is well.",
            "This is a missing capability, not a fault. See CAPABILITIES.",
            ["The capability catalogue has no service-state reading."],
            remedy="service-state")]
        render = snap.value("render")
        jobs = render.get("jobs") if isinstance(render, dict) else None
        if isinstance(jobs, dict) and jobs:
            rows = [f"{name}: ran {int(j.get('ran', 0))} times, changed the picture "
                    f"{int(j.get('changed', 0))} times." for name, j in sorted(jobs.items())]
            findings.append(Finding("services.jobs", "services", INFORMATIONAL,
                                    f"{len(jobs)} Earth OS background jobs are running",
                                    evidence=rows))
        return findings

    # -- application errors ------------------------------------------------------
    def _applications(self, errors: Dict[str, str],
                      error_log: Optional[Tuple[str, float]]) -> List[Finding]:
        findings: List[Finding] = []
        for name, text in sorted(errors.items()):
            if not text:
                continue
            severity = HIGH if name == "platform" else MODERATE
            findings.append(Finding(
                f"applications.{name}", "applications", severity,
                f"Earth OS started without its {name} component",
                "The shell carried on without it, so whatever depends on it is off.",
                "Run the checks on DIAGNOSTICS for what is missing.",
                [f"Recorded at start: {text}"]))
        if error_log is not None:
            path, age = error_log
            recent = age < 86400
            findings.append(Finding(
                "applications.last-error", "applications", MODERATE if recent else LOW,
                "Earth OS recorded a failed start",
                "A previous start failed and wrote down why.",
                f"Read {path}.",
                [f"{path} was written {human_span(age)} ago."]))
        if not findings:
            findings.append(Finding(
                "applications.normal", "applications", INFORMATIONAL,
                "No Earth OS component failed to start",
                evidence=["The shell recorded no start-up error, and there is no "
                          "failed-start log."]))
        return findings

    # -- configuration -----------------------------------------------------------
    def _configuration(self, config, doctor) -> List[Finding]:
        findings: List[Finding] = []
        get = getattr(config, "get", None)
        if callable(get):
            checks = (
                ("shell", "fps", 60, "above",
                 "A frame rate above 60 costs processor time no display here shows."),
                ("shell", "telemetry_interval", 0.5, "below",
                 "Sampling the hardware more than twice a second costs more than it shows."),
                ("shell", "tick_interval", 0.05, "below",
                 "Screens are asked for news more than twenty times a second."),
                ("evolution", "poll_interval", 1.0, "below",
                 "The Evolution Center itself is sampling more than once a second."),
            )
            for section, key, limit, side, why in checks:
                try:
                    value = float(get(section, key, limit))
                except (TypeError, ValueError):
                    findings.append(Finding(
                        f"configuration.{section}.{key}", "configuration", MODERATE,
                        f"[{section}] {key} is not a number",
                        evidence=[f"Configured value: {get(section, key)!r}."]))
                    continue
                if (side == "above" and value > limit) or (side == "below" and value < limit):
                    findings.append(Finding(
                        f"configuration.{section}.{key}", "configuration", LOW,
                        f"[{section}] {key} is set aggressively", why,
                        "See OPTIMIZATION for the usual value.",
                        [f"Configured: {value:g}; usual limit {limit:g}."]))

        if doctor is None:
            findings.append(Finding(
                "configuration.unchecked", "configuration", INFORMATIONAL,
                "Component checks have not been run this session",
                "The same checks as earth-os --doctor. They start programs to probe for "
                "audio, speech and network components, so they run when asked, not on a clock.",
                "Press RUN CHECKS on DIAGNOSTICS, or add --checks to "
                "earth-os --evolution-report."))
            return findings
        failed = [(n, d) for s, n, d in doctor if s == "fail"]
        warned = [(n, d) for s, n, d in doctor if s == "warn"]
        for name, detail in failed:
            findings.append(Finding(
                f"configuration.check.{name}", "configuration", HIGH,
                f"Check failed: {name}",
                "Earth OS reports this as blocking.", "", [_first_line(detail)]))
        for name, detail in warned:
            findings.append(Finding(
                f"configuration.check.{name}", "configuration", LOW,
                f"Optional component unavailable: {name}",
                "The interface works; this feature is off.", "", [_first_line(detail)]))
        passed = len(doctor) - len(failed) - len(warned)
        findings.append(Finding(
            "configuration.checks", "configuration", INFORMATIONAL,
            f"{passed} of {len(doctor)} component checks passed",
            evidence=[f"{len(failed)} failed, {len(warned)} optional components unavailable."]))
        return findings


def _first_line(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[0] if lines else "no detail given"


def worst(findings: List[Finding]) -> str:
    """The highest severity among *findings*; INFORMATIONAL for none."""
    return max((f.severity for f in findings), key=severity_rank, default=INFORMATIONAL)


def counts(findings: List[Finding]) -> Dict[str, int]:
    out = {name: 0 for name in (CRITICAL, HIGH, MODERATE, LOW, INFORMATIONAL)}
    for finding in findings:
        out[finding.severity] = out.get(finding.severity, 0) + 1
    return out
