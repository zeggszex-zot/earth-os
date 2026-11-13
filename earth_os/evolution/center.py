"""SEC Core: the System Evolution Center's engine, in one object.

It owns the parts — inspector, trends, health analyzer, optimization advisor,
capability registry, planner, reporter — and decides *when* each does any
work, which is most of what keeps the center cheap:

* **It has no clock of its own.** :meth:`sample` is called by the screen's
  tick while the center is on show, and by one slow background job while it
  is not. Called too soon, it returns at once.
* **Each panel pays for itself.** Findings, advice and the capability survey
  are computed when asked for and kept until the next sample. A panel nobody
  opens costs nothing.
* **The expensive reading is rationed.** The full process list is taken only
  for the panels that show it, and no more often than ``process_interval``.
* **Slow things happen on a press.** The doctor's checks start programs, and
  an explanation calls a model; both run on a worker, only when asked.

What it may do to the roadmap is :meth:`discover` and :meth:`propose_from` —
noticing, and writing up. Every decision is the planner's, on a person's
name, from a button. ``tests/test_evolution.py`` reads this file to check
that it never calls one.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from .changes import ChangeApplier
from .health import HealthAnalyzer, counts, worst
from .inspector import Inspector
from .model import (
    IN_EFFECT, INCOMPLETE, MISSING, NEEDS_IMPROVEMENT, RECOMMENDED, CapabilityEntry,
    Finding, INFORMATIONAL, Optimization, Reading, Snapshot,
)
from .optimizer import OptimizationAdvisor
from .planner import Planner, Proposal
from .registry import CapabilityRegistry, Facts
from .store import Store
from .trends import Sample, Trends

#: Panels that show the process list, and so are worth taking it for.
PROCESS_PANELS = ("health", "capabilities", "diagnostics")

SUITE = "/usr/bin/python3.12 -m unittest discover -s tests -q passes"


class EvolutionCenter:
    def __init__(self, port, directory=None, clock: Callable[[], float] = time.monotonic):
        self.port = port
        self.clock = clock
        self.store = Store(directory)
        self.trends = Trends(capacity=int(self._setting("history", 360)))
        self.inspector = Inspector(port)
        self.health = HealthAnalyzer(self.trends)
        self.advisor = OptimizationAdvisor()
        self.registry = CapabilityRegistry()
        self.applier = ChangeApplier(getattr(port, "config", None), self.store,
                                     on_change=getattr(port, "apply_setting", None))
        self.planner = Planner(self.store, self.applier)

        self.snapshot: Optional[Snapshot] = None
        self._last_sample = float("-inf")
        self._last_processes = float("-inf")
        self._processes: Optional[Tuple[Reading, Reading]] = None
        self._findings: Optional[List[Finding]] = None
        self._advice: Optional[List[Optimization]] = None
        self._survey: Optional[List[CapabilityEntry]] = None
        self._hardware: Optional[Reading] = None

        #: ``Shell.doctor_rows()`` once the checks have been run, and when.
        self.doctor: Optional[List[Tuple[str, str, str]]] = None
        self.doctor_at = 0.0
        self.checking = False
        #: ``(text, source)`` of the last explanation asked for.
        self.explanation: Optional[Tuple[str, str]] = None
        self.explaining = False

        #: What the center itself has cost, measured: samples taken and the
        #: seconds spent taking them.
        self.samples_taken = 0
        self.sample_seconds = 0.0
        self.sample_last = 0.0

    # -- settings ----------------------------------------------------------------
    def _setting(self, key: str, default: float) -> float:
        get = getattr(getattr(self.port, "config", None), "get", None)
        try:
            return float(get("evolution", key, default)) if callable(get) else float(default)
        except (TypeError, ValueError):
            return float(default)

    @property
    def poll_interval(self) -> float:
        return max(self._setting("poll_interval", 2.0), 0.5)

    @property
    def process_interval(self) -> float:
        return max(self._setting("process_interval", 5.0), 1.0)

    # -- sampling ----------------------------------------------------------------
    def sample(self, visible: bool = True, panel: str = "", force: bool = False) -> bool:
        """Read the machine if it is time to. True when there is something new.

        Visible, this is rate-limited to ``poll_interval``; the caller may ask
        as often as it likes. Hidden, the caller is the slow background job
        and its interval is the limit, so every call samples.
        """
        now = self.clock()
        if visible and not force and now - self._last_sample < self.poll_interval:
            return False
        started = time.perf_counter()
        wants = force or not visible or panel in PROCESS_PANELS
        due = force or not visible or now - self._last_processes >= self.process_interval
        take = wants and due
        snap = self.inspector.snapshot(processes=take)
        if take:
            self._last_processes = now
            self._processes = (snap.get("processes"), snap.get("own_memory"))
        elif self._processes is not None \
                and now - self._last_processes < self.process_interval * 3:
            # Carried forward rather than read again: a list a few seconds
            # old is still the list, and reading it is the costly part.
            for reading in self._processes:
                snap.put(reading)
        self.snapshot = snap
        self._last_sample = now
        self._findings = self._advice = self._survey = None
        self.trends.add(self._as_sample(snap, now, visible))
        spent = time.perf_counter() - started
        self.samples_taken += 1
        self.sample_seconds += spent
        self.sample_last = spent
        return True

    def ensure(self, panel: str = "") -> Snapshot:
        """A snapshot, taking one now if there is none yet."""
        if self.snapshot is None:
            self.sample(visible=True, panel=panel, force=True)
        assert self.snapshot is not None
        return self.snapshot

    @staticmethod
    def _as_sample(snap: Snapshot, now: float, visible: bool) -> Sample:
        memory, disk, swap = snap.value("memory"), snap.value("disk"), snap.value("swap")
        render = snap.value("render")
        values: Dict[str, Optional[float]] = {
            "cpu": snap.value("cpu"),                                   # type: ignore[dict-item]
            "memory": memory["fraction"] if isinstance(memory, dict) else None,
            "memory_used": memory["used"] if isinstance(memory, dict) else None,
            "swap_used": swap["used"] if isinstance(swap, dict) else None,
            "disk": disk["fraction"] if isinstance(disk, dict) else None,
            "temperature": snap.value("temperature"),                   # type: ignore[dict-item]
            "processes": snap.value("process_count"),                   # type: ignore[dict-item]
            "own_memory": snap.value("own_memory"),                     # type: ignore[dict-item]
            "frames_drawn": render.get("frames_drawn") if isinstance(render, dict) else None,
            "draw_ms": render.get("draw_ms_mean") if isinstance(render, dict)
            and render.get("draws_timed") else None,
        }
        return Sample(at=now, wall=snap.taken, values=values, visible=visible)

    # -- what each panel asks for ------------------------------------------------
    def errors(self) -> Dict[str, str]:
        """What the shell recorded failing as it started, by component."""
        names = (("platform", "platform_error"), ("compositions", "composition_error"),
                 ("world model", "world_error"), ("earth mind", "mind_error"),
                 ("evolution center", "evolution_error"))
        return {label: str(getattr(self.port, attr, "") or "") for label, attr in names}

    def error_log(self) -> Optional[Tuple[str, float]]:
        """``(path, age in seconds)`` of Earth OS's failed-start note, if any."""
        try:
            from ..cli import log_path
            path = log_path()
            return (str(path), max(time.time() - path.stat().st_mtime, 0.0))
        except Exception:
            return None

    def findings(self) -> List[Finding]:
        if self._findings is None:
            snap = self.ensure("health")
            self._findings = self.health.analyze(
                snap, config=getattr(self.port, "config", None), doctor=self.doctor,
                errors=self.errors(), error_log=self.error_log())
        return self._findings

    def health_summary(self) -> Dict[str, object]:
        found = self.findings()
        return {"worst": worst(found), "counts": counts(found), "total": len(found)}

    def optimizations(self) -> List[Optimization]:
        if self._advice is None:
            self._advice = self.advisor.advise(getattr(self.port, "config", None),
                                               self.ensure(), self.findings())
        return self._advice

    def capabilities(self) -> List[CapabilityEntry]:
        if self._survey is None:
            snap = self.ensure("capabilities")
            if self._hardware is None:
                self._hardware = self.inspector.hardware()
            hardware = list(self._hardware.value) if self._hardware.ok else None  # type: ignore[arg-type]
            self._survey = self.registry.survey(
                Facts(self.port, snap, self.doctor, hardware))
        return self._survey

    def vitals(self) -> Dict[str, object]:
        """The current readings worth comparing before and after a change.

        All measured, each with the number of samples and the span it rests
        on. Kept on a proposal when it is implemented and again when it is
        verified; the difference is the only *measured* effect the center
        ever reports for a change.
        """
        out: Dict[str, object] = {"at": time.time(), "samples": len(self.trends),
                                  "span_seconds": round(self.trends.span(), 1)}
        for name in ("cpu", "memory", "own_memory", "draw_ms"):
            stat = self.trends.stat(name, 120.0)
            if stat is not None:
                out[name] = {"mean": stat.mean, "low": stat.low, "high": stat.high,
                             "samples": stat.count, "span_seconds": round(stat.span, 1)}
        rate = self.trends.rate("frames_drawn", 120.0)
        if rate is not None:
            out["frames_per_second"] = rate
        return out

    def overhead(self) -> Dict[str, float]:
        """What sampling has cost this session, measured."""
        mean = self.sample_seconds / self.samples_taken if self.samples_taken else 0.0
        return {"samples": self.samples_taken, "mean_ms": mean * 1000.0,
                "last_ms": self.sample_last * 1000.0,
                "total_ms": self.sample_seconds * 1000.0}

    # -- slow work, on a press ---------------------------------------------------
    def _on_main(self, fn: Callable[[], None]) -> None:
        post = getattr(self.port, "call_on_main", None)
        if callable(post):
            post(fn)
        else:
            fn()

    def run_checks(self, done: Optional[Callable[[], None]] = None,
                   wait: bool = False) -> bool:
        """Run the doctor's checks. False if they are already running."""
        if self.checking:
            return False
        rows = getattr(self.port, "doctor_rows", None)
        if not callable(rows):
            self.doctor, self.doctor_at = [], time.time()
            return True
        self.checking = True

        def work() -> None:
            try:
                result = list(rows())
            except Exception as exc:
                result = [("fail", "checks", f"{type(exc).__name__}: {exc}")]

            def land() -> None:
                self.doctor, self.doctor_at = result, time.time()
                self.checking = False
                self._findings = self._advice = self._survey = None
                if done is not None:
                    done()
            self._on_main(land)

        if wait:
            work()
        else:
            threading.Thread(target=work, name="evolution-checks", daemon=True).start()
        return True

    def explainable(self) -> List[str]:
        """The findings worth explaining, as sentences with their figures."""
        sentences = []
        for finding in self.findings():
            if finding.severity == INFORMATIONAL:
                continue
            sentences.append(f"{finding.title}. " + " ".join(finding.evidence))
        if not sentences:
            sentences = [f"{f.title}. " + " ".join(f.evidence[:1])
                         for f in self.findings() if f.id.endswith(".normal")]
        return sentences[:8]

    def explain(self, done: Optional[Callable[[], None]] = None, wait: bool = False) -> bool:
        """Ask for the findings in plain words. Never changes a finding.

        With no model, or with one whose answer contains a figure the
        findings do not, the findings themselves come back and the source
        says so. The center works the same either way.
        """
        if self.explaining:
            return False
        sentences = self.explainable()
        ask = getattr(self.port, "explain_findings", None)
        if not callable(ask) or not self._setting("explain", 1.0):
            self.explanation = (" ".join(sentences), "findings (explanation is off)")
            return True
        self.explaining = True

        def work() -> None:
            try:
                result = ask("What is the condition of this computer?", sentences,
                             origin="the System Evolution Center's findings")
            except Exception as exc:
                result = (" ".join(sentences), f"findings ({type(exc).__name__}: {exc})")

            def land() -> None:
                self.explanation = (str(result[0]), str(result[1]))
                self.explaining = False
                if done is not None:
                    done()
            self._on_main(land)

        if wait:
            work()
        else:
            threading.Thread(target=work, name="evolution-explain", daemon=True).start()
        return True

    # -- discovery and proposal: all the engine may do to the roadmap ---------------
    def _from_optimization(self, item: Optimization) -> Dict[str, object]:
        testing = []
        if item.restart:
            testing.append("Restart Earth OS so the setting takes effect.")
        testing += ["Use Earth OS as you normally would for at least two minutes after "
                    "the change, then read MEASURED BEFORE → AFTER on this proposal.",
                    f"Check what it costs is acceptable: {item.drawbacks}"]
        if item.change is None:
            testing = ["Carry out by hand: " + item.reason] + testing[-1:]
        return dict(
            title=item.title, kind="optimization", source=f"optimization:{item.id}",
            summary=f"{item.changes} WHY: {item.why} EXPECTED ({item.benefits}) "
                    f"BASIS: {item.basis} COST: {item.drawbacks}",
            priority="HIGH" if item.state == RECOMMENDED else "NORMAL", difficulty="LOW",
            testing=testing, rung=0, restart=item.restart, reverse=item.reverse,
            change=item.change)

    def _from_capability(self, entry: CapabilityEntry) -> Dict[str, object]:
        testing = [SUITE, "No allowance in tests/test_boundary.py is raised."]
        if entry.rung >= 2:
            testing.append("make conformance, and compare with a report taken before: "
                           "no capability disagrees.")
        if entry.rung == 3:
            testing.append("A person wrote the catalogue entry; tests/test_corpus.py shows "
                           "the new capability can be asked for in words.")
        how = {0: "Installation or configuration: a person's act.",
               1: "Rung 1: a new arrangement of what the machine can already do.",
               2: "Rung 2: a new mechanism for an existing capability; the agent's gate "
                  "can verify it.",
               3: "Rung 3: new machine power. Only a person may grant it."}[min(entry.rung, 3)]
        return dict(
            title=entry.title, kind="capability", source=f"capability:{entry.id}",
            summary=f"{entry.description} {how} REQUIRES: " + " ".join(entry.requirements),
            priority="NORMAL", difficulty=entry.difficulty, dependencies=list(entry.depends),
            testing=testing, rung=entry.rung,
            reverse="git revert the commit that made the change; every accepted change "
                    "is one commit (docs/SELF_MODIFICATION.md §3)." if entry.rung else
                    "Remove what was installed, or restore the configuration file.")

    def discover(self) -> List[Proposal]:
        """Put what the center has noticed on the roadmap as DISCOVERED.

        Recommended optimizations, and capabilities that are missing,
        incomplete or need improvement and would take development to fix.
        Returns only what is new: something already on the roadmap — in any
        status — is not added again.
        """
        created: List[Proposal] = []
        for item in self.optimizations():
            if item.state != RECOMMENDED:
                continue
            proposal, new = self.planner.discover(**self._from_optimization(item))  # type: ignore[arg-type]
            if new:
                created.append(proposal)
        for entry in self.capabilities():
            if entry.category not in (MISSING, INCOMPLETE, NEEDS_IMPROVEMENT):
                continue
            if entry.rung == 0 or entry.id.startswith("platform-"):
                # Something to install or configure on this one machine. It
                # is listed under CAPABILITIES and can be proposed from
                # there; it is not development work to put on a roadmap.
                continue
            proposal, new = self.planner.discover(**self._from_capability(entry))   # type: ignore[arg-type]
            if new:
                created.append(proposal)
        return created

    @staticmethod
    def proposable(item: Optimization) -> bool:
        """Is there anything to propose? Not for what is already so."""
        return item.state != IN_EFFECT

    def propose_from(self, kind: str, item_id: str, by: str = "engine") -> Optional[Proposal]:
        """Write one optimization or capability up as a proposal. None if unknown.

        This is the PROPOSE button. It creates a record; it approves nothing.
        An optimization that is already in effect is not written up: there
        would be nothing to carry out, and its "how to reverse" would describe
        undoing a change nobody made.
        """
        if kind == "optimization":
            item = next((o for o in self.optimizations() if o.id == item_id), None)
            if item is not None and not self.proposable(item):
                return None
            fields = self._from_optimization(item) if item is not None else None
        elif kind == "capability":
            entry = next((c for c in self.capabilities() if c.id == item_id), None)
            fields = self._from_capability(entry) if entry is not None else None
        else:
            fields = None
        if fields is None:
            return None
        proposal, _new = self.planner.discover(**fields)                 # type: ignore[arg-type]
        if proposal.status == "DISCOVERED":
            self.planner.propose(proposal.id, by=by or "engine")
        return proposal

    # -- reports -----------------------------------------------------------------
    def report(self, kind: str) -> Dict[str, object]:
        from .reporter import Reporter
        return Reporter(self).build(kind)

    def export(self, kind: str, fmt: str = "text"):
        """Write a report into the center's ``reports/`` and return its path."""
        from .reporter import Reporter
        reporter = Reporter(self)
        report = reporter.build(kind)
        if fmt == "json":
            return self.store.write_report(kind, reporter.as_json(report), "json")
        return self.store.write_report(kind, reporter.as_text(report), "txt")
