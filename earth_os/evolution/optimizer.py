"""The Optimization Advisor: changes that could make this machine run better.

Each entry answers six questions, because a person is owed all six before
being asked to approve anything: what it changes, why that could help, what
to expect, what it costs, whether a restart is needed, and how to undo it.

**An expectation is not a measurement, and this module never lets one pass
for the other.** ``benefits`` is always an estimate, and ``basis`` says where
the estimate comes from — usually ``docs/RENDERING.md``, where the figures
were measured on the machine the rendering work was done on. Whether a
change helped *here* is only known after it has been applied, by comparing
the readings the center took before and after; that comparison is stored on
the proposal as ``measured``, and it is the only place the word belongs.

Whether an entry is RECOMMENDED is decided from the live configuration and
the current findings, not from a list of good ideas: an optimization that is
already in effect says so, and one whose trigger has not been seen is merely
AVAILABLE.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .changes import TUNABLES
from .model import (
    AVAILABLE, IN_EFFECT, MODERATE, RECOMMENDED, Finding, Optimization, Snapshot,
    human_bytes, severity_rank,
)

RENDERING_DOC = ("docs/RENDERING.md, measured on the development machine at 1920x1080. "
                 "Not measured on this machine.")


class Context:
    """What the rules look at: the configuration, a snapshot and the findings."""

    def __init__(self, config, snap: Snapshot, findings: List[Finding]):
        self.config = config
        self.snap = snap
        self.findings = {f.id: f for f in findings}

    def get(self, section: str, key: str, default: object = None) -> object:
        get = getattr(self.config, "get", None)
        return get(section, key, default) if callable(get) else default

    def number(self, section: str, key: str, default: float) -> float:
        try:
            return float(self.get(section, key, default))        # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    def flag(self, section: str, key: str, default: bool) -> bool:
        return bool(self.get(section, key, default))

    def at_least(self, finding: str, severity: str) -> Optional[Finding]:
        found = self.findings.get(finding)
        if found is not None and severity_rank(found.severity) >= severity_rank(severity):
            return found
        return None

    def render(self) -> Dict[str, object]:
        value = self.snap.value("render")
        return value if isinstance(value, dict) else {}


def _change(section: str, key: str, value: object) -> Dict[str, object]:
    assert f"{section}.{key}" in TUNABLES, f"{section}.{key} is not a tunable"
    return {"section": section, "key": key, "value": value}


def _undo(section: str, key: str, previous: object) -> str:
    shown = str(previous).lower() if isinstance(previous, bool) else str(previous)
    return (f"ROLL BACK on the proposal restores it, or set [{section}] {key} = {shown} "
            "in config.toml by hand. A copy of the file is kept in the center's backups.")


# -- the rules. Each returns one Optimization, or None when it does not apply. -----
def _adaptive(c: Context) -> Optional[Optimization]:
    on = c.flag("shell", "adaptive", True)
    return Optimization(
        id="adaptive-rendering", title="Draw frames on change, not on a clock",
        area="rendering",
        changes="Sets [shell] adaptive = true: a frame is drawn when something changed, "
                "when someone is interacting, or when a screen says it is animating.",
        why="On a fixed clock most frames are identical to the one before, and each still "
            "costs a full composite.",
        benefits="ESTIMATE: most of the processor time the interface uses while idle.",
        basis="92–100% of frames were identical to their predecessor on four screens. "
              + RENDERING_DOC,
        drawbacks="A screen that changes without asking for a frame would stop updating. "
                  "None is known; [shell] idle_fps is the safety net.",
        restart=True, reverse=_undo("shell", "adaptive", on),
        state=IN_EFFECT if on else RECOMMENDED,
        reason=("Adaptive rendering is on." if on else
                "Adaptive rendering is switched off in the configuration."),
        change=None if on else _change("shell", "adaptive", True))


def _frame_cache(c: Context) -> Optional[Optimization]:
    on = c.flag("shell", "frame_cache", True)
    surface = c.snap.value("surface")
    cost = ""
    if isinstance(surface, tuple):
        # Arithmetic on a reading, not a measurement: four bytes a pixel.
        cost = (f" At {surface[0]}x{surface[1]} that is about "
                f"{human_bytes(surface[0] * surface[1] * 4)} (width x height x 4 bytes).")
    return Optimization(
        id="frame-cache", title="Keep the last composited frame", area="rendering",
        changes="Sets [shell] frame_cache = true: a repaint the toolkit asks for on its own "
                "is copied from the kept frame instead of being composed again.",
        why="Copying a finished frame is far cheaper than composing one.",
        benefits="ESTIMATE: repaints of an unchanged screen cost a fraction of a full draw.",
        basis="0.64 ms to copy against 14.26 ms to compose. " + RENDERING_DOC,
        drawbacks="Holds one full-size frame in memory." + cost,
        restart=True, reverse=_undo("shell", "frame_cache", on),
        state=IN_EFFECT if on else RECOMMENDED,
        reason="The frame cache is on." if on else "The frame cache is switched off.",
        change=None if on else _change("shell", "frame_cache", True))


def _idle_floor(c: Context) -> Optional[Optimization]:
    floor = c.number("shell", "idle_fps", 0)
    if floor <= 0:
        return Optimization(
            id="idle-floor", title="Draw nothing while nothing changes", area="rendering",
            changes="Sets [shell] idle_fps = 0.",
            why="A floor under the idle rate redraws a screen that has not changed.",
            benefits="ESTIMATE: no draws at all on a static, untouched screen.",
            basis="By construction: zero frames requested is zero frames drawn.",
            drawbacks="None known. The floor exists for a machine where something draws "
                      "without saying so.",
            restart=True, reverse=_undo("shell", "idle_fps", int(floor)),
            state=IN_EFFECT, reason="There is no idle floor.")
    return Optimization(
        id="idle-floor", title="Draw nothing while nothing changes", area="rendering",
        changes="Sets [shell] idle_fps = 0.",
        why="A floor under the idle rate redraws a screen that has not changed.",
        benefits=f"ESTIMATE: {floor:g} fewer draws a second while idle.",
        basis="By construction: the floor is exactly this many draws a second.",
        drawbacks="If something on this machine draws without asking for a frame, it "
                  "would stop updating until touched.",
        restart=True, reverse=_undo("shell", "idle_fps", int(floor)),
        state=RECOMMENDED, reason=f"An idle floor of {floor:g} frames a second is configured.",
        change=_change("shell", "idle_fps", 0))


def _phosphor(c: Context) -> Optional[Optimization]:
    on = c.flag("shell", "phosphor", True)
    heavy = c.findings.get("rendering.over-budget") or c.findings.get("rendering.heavy")
    busy = c.at_least("processor.saturated", MODERATE)
    reason, state = "The phosphor effect is on; nothing measured calls for removing it.", AVAILABLE
    if not on:
        state, reason = IN_EFFECT, "The phosphor effect is already off."
    elif heavy is not None:
        state, reason = RECOMMENDED, f"{heavy.title}: {heavy.evidence[-1] if heavy.evidence else ''}"
    elif busy is not None:
        state, reason = RECOMMENDED, f"{busy.title}, and the effect is a large part of each draw."
    return Optimization(
        id="phosphor-off", title="Switch the phosphor effect off", area="rendering",
        changes="Sets [shell] phosphor = false: no glow, scanlines or darkened edge.",
        why="The glow is drawn by shrinking and re-stretching every frame, which is a "
            "large share of what a frame costs.",
        benefits="ESTIMATE: roughly two fifths less work per frame drawn.",
        basis="The glow was 6.8 ms of a 14.3 ms frame. " + RENDERING_DOC,
        drawbacks="The interface loses the look of the tube it imitates. This is a "
                  "matter of taste as much as performance.",
        restart=True, reverse=_undo("shell", "phosphor", on), state=state, reason=reason,
        change=_change("shell", "phosphor", False) if on else None)


def _frame_rate(c: Context) -> Optional[Optimization]:
    fps = int(c.number("shell", "fps", 30))
    over = c.findings.get("rendering.over-budget")
    target, state, reason = 20, AVAILABLE, f"The ceiling is {fps} frames a second."
    if fps > 30:
        target, state = 30, RECOMMENDED
        reason = f"The ceiling is {fps} frames a second; the interface was designed around 30."
    elif over is not None and fps > 20:
        state, reason = RECOMMENDED, f"{over.title}."
    elif fps <= 20:
        return None
    return Optimization(
        id="frame-rate", title=f"Lower the frame-rate ceiling to {target}", area="rendering",
        changes=f"Sets [shell] fps = {target}. This is a ceiling used while interacting or "
                "animating, not a rate held at rest.",
        why="Fewer frames during a drag or an animation is proportionally less drawing.",
        benefits=f"ESTIMATE: about {(1 - target / fps) * 100:.0f}% fewer frames while "
                 "something is moving; no change at rest.",
        basis=f"Arithmetic: {target} is {target / fps * 100:.0f}% of {fps}.",
        drawbacks="Dragging a slider and typed-out text look less smooth.",
        restart=True, reverse=_undo("shell", "fps", fps), state=state, reason=reason,
        change=_change("shell", "fps", target))


def _telemetry(c: Context) -> Optional[Optimization]:
    interval = c.number("shell", "telemetry_interval", 1.0)
    battery = c.snap.value("battery")
    discharging = isinstance(battery, tuple) and len(battery) == 2 \
        and "discharg" in str(battery[1]).lower()
    if interval < 1.0:
        target, state = 1.0, RECOMMENDED
        reason = f"Hardware is sampled every {interval:g} s, faster than the gauges can show."
    elif interval < 2.0:
        target, state = 2.0, RECOMMENDED if discharging else AVAILABLE
        reason = ("Running on battery; sampling half as often saves a little."
                  if discharging else f"Hardware is sampled every {interval:g} s.")
    else:
        return None
    return Optimization(
        id="telemetry-interval", title=f"Sample the hardware every {target:g} seconds",
        area="background",
        changes=f"Sets [shell] telemetry_interval = {target:g}.",
        why="Each sample wakes the process, reads the counters and may ask for a frame.",
        benefits=f"ESTIMATE: {interval / target * 100:.0f}% of the samples taken now, and "
                 "correspondingly fewer gauge redraws on the screens that show them.",
        basis=f"Arithmetic: one sample every {target:g} s instead of every {interval:g} s.",
        drawbacks="Gauges and the clock's neighbours update less often.",
        restart=True, reverse=_undo("shell", "telemetry_interval", interval),
        state=state, reason=reason, change=_change("shell", "telemetry_interval", target))


def _window_poll(c: Context) -> Optional[Optimization]:
    interval = c.number("windows", "poll_interval", 1.2)
    if interval >= 2.0:
        return None
    target = 1.2 if interval < 1.2 else 2.0
    return Optimization(
        id="window-poll", title=f"Look for new windows every {target:g} seconds",
        area="background",
        changes=f"Sets [windows] poll_interval = {target:g}.",
        why="Each look asks the compositor for its window list, which starts a program.",
        benefits=f"ESTIMATE: {interval / target * 100:.0f}% of the requests made now.",
        basis=f"Arithmetic: one look every {target:g} s instead of every {interval:g} s.",
        drawbacks="A newly started program takes up to that long to appear in the strip "
                  "and be put in the frame.",
        restart=True, reverse=_undo("windows", "poll_interval", interval),
        state=RECOMMENDED if interval < 1.2 else AVAILABLE,
        reason=f"The window list is polled every {interval:g} s.",
        change=_change("windows", "poll_interval", target))


def _tick(c: Context) -> Optional[Optimization]:
    interval = c.number("shell", "tick_interval", 0.1)
    if interval >= 0.2:
        return None
    target = 0.1 if interval < 0.1 else 0.2
    return Optimization(
        id="tick-interval", title=f"Give screens their turn every {target:g} seconds",
        area="background",
        changes=f"Sets [shell] tick_interval = {target:g}.",
        why="The tick wakes the process so the screen on show can notice its own news.",
        benefits=f"ESTIMATE: {interval / target * 100:.0f}% of the wake-ups at rest now.",
        basis=f"Arithmetic: one tick every {target:g} s instead of every {interval:g} s.",
        drawbacks="Terminal output and agent messages appear up to that much later.",
        restart=True, reverse=_undo("shell", "tick_interval", interval),
        state=RECOMMENDED if interval < 0.1 else AVAILABLE,
        reason=f"Screens are ticked every {interval:g} s.",
        change=_change("shell", "tick_interval", target))


def _voice(c: Context) -> Optional[Optimization]:
    if not (c.flag("voice", "enabled", True) and c.flag("voice", "continuous", False)):
        return None
    busy = c.at_least("processor.saturated", MODERATE) or c.findings.get("processor.busy")
    return Optimization(
        id="voice-continuous", title="Listen only after the wake word", area="background",
        changes="Sets [voice] continuous = false.",
        why="Continuous listening sends everything heard to speech recognition.",
        benefits="ESTIMATE: speech recognition runs only on what follows the wake word.",
        basis="How the voice engine is built; no figure has been measured.",
        drawbacks="Every command has to start with the wake word again.",
        restart=True, reverse=_undo("voice", "continuous", True),
        state=RECOMMENDED if busy else AVAILABLE,
        reason="Continuous listening is on" + (" and the processor is busy." if busy else "."),
        change=_change("voice", "continuous", False))


def _companion(c: Context) -> Optional[Optimization]:
    if not c.flag("companion", "enabled", True) \
            or str(c.get("companion", "backend", "ollama")) != "ollama":
        return None
    keep = str(c.get("companion", "keep_alive", "10m"))
    if keep in ("0", "2m"):
        return None
    pressure = c.at_least("memory.pressure", MODERATE)
    return Optimization(
        id="companion-keep-alive", title="Unload the companion model sooner", area="memory",
        changes="Sets [companion] keep_alive = \"2m\": the language model is released two "
                "minutes after it was last used.",
        why="A loaded model holds its whole weight in memory whether or not anyone is talking.",
        benefits="ESTIMATE: the model's memory comes back sooner. How much depends on the "
                 "model, and the center has no figure for it.",
        basis="How the model server holds models; no figure has been measured here.",
        drawbacks="The first reply after a pause waits for the model to load again.",
        restart=True, reverse=_undo("companion", "keep_alive", f'"{keep}"'),
        state=RECOMMENDED if pressure else AVAILABLE,
        reason=(f"{pressure.title}; the model is kept loaded for {keep}." if pressure
                else f"The model is kept loaded for {keep} after use."),
        change=_change("companion", "keep_alive", "2m"))


def _own_poll(c: Context) -> Optional[Optimization]:
    interval = c.number("evolution", "poll_interval", 2.0)
    if interval >= 2.0:
        return None
    return Optimization(
        id="center-poll", title="Slow the Evolution Center's own sampling", area="background",
        changes="Sets [evolution] poll_interval = 2.",
        why="A tool for watching overhead should not be the overhead.",
        benefits=f"ESTIMATE: {interval / 2.0 * 100:.0f}% of the samples taken now while the "
                 "center is on screen.",
        basis=f"Arithmetic: one sample every 2 s instead of every {interval:g} s.",
        drawbacks="OVERVIEW figures update every two seconds.",
        restart=False, reverse=_undo("evolution", "poll_interval", interval),
        state=RECOMMENDED, reason=f"The center samples every {interval:g} s.",
        change=_change("evolution", "poll_interval", 2.0))


def _startup(c: Context) -> Optional[Optimization]:
    return Optimization(
        id="startup-measure", title="Measure start-up before optimising it", area="startup",
        changes="Nothing: it runs the existing boot check and reports where the time goes.",
        why="Start-up was already tuned by deferring imports; the next gain has to be "
            "found by measuring, not guessed.",
        benefits="ESTIMATE: none by itself. It tells you whether there is anything to gain.",
        basis="Time to first frame was 135 ms with 202 ms of shell construction behind it "
              "on the development machine (AGENTS.md).",
        drawbacks="None; it changes nothing.",
        restart=False, reverse="Nothing to reverse.", state=AVAILABLE,
        reason="Manual: run `make bootcheck` from the source tree.")


def _leak(c: Context) -> Optional[Optimization]:
    finding = c.findings.get("leaks.own-memory")
    if finding is None:
        return None
    return Optimization(
        id="leak-investigation", title="Find what is growing in Earth OS", area="memory",
        changes="Nothing by itself: it is development work on the source.",
        why="Memory that only rises is eventually all of it.",
        benefits="ESTIMATE: stable memory over a long session.",
        basis=finding.evidence[0] if finding.evidence else "",
        drawbacks="Developer time.",
        restart=False, reverse="Nothing to reverse until a change is made.",
        state=RECOMMENDED, reason=finding.title + ".")


RULES: List[Callable[[Context], Optional[Optimization]]] = [
    _adaptive, _frame_cache, _idle_floor, _phosphor, _frame_rate, _telemetry,
    _window_poll, _tick, _voice, _companion, _own_poll, _leak, _startup,
]

_ORDER = {RECOMMENDED: 0, AVAILABLE: 1, IN_EFFECT: 2}


class OptimizationAdvisor:
    def advise(self, config, snap: Snapshot, findings: List[Finding]) -> List[Optimization]:
        """Every optimization that applies to this machine, recommended ones first."""
        context = Context(config, snap, findings)
        out: List[Optimization] = []
        for rule in RULES:
            try:
                found = rule(context)
            except Exception:
                # One rule tripping over an odd configuration value must not
                # take the rest of the advice with it.
                continue
            if found is not None:
                out.append(found)
        out.sort(key=lambda o: (_ORDER.get(o.state, 3), o.area, o.id))
        return out
