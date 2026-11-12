"""The Capability Registry: what Earth OS cannot do yet, and what it would take.

This is the part of the center that answers *"what capabilities are
missing?"* and *"what should we develop next?"*. Two rules keep it honest.

**Every entry says how it knows.** ``basis`` is one of:

``detected live``
    Checked on this machine a moment ago — the catalogue was asked, the
    platform was asked, a reading was taken. The evidence is the answer.
``source review``
    Read in the source or the documents when the entry was written. True of
    the code as shipped; it does not re-check itself, and says so.
``policy``
    Not a gap in the code but a decision about what the machine may do.

**Every entry says who may build it.** ``rung`` is the rung of
``docs/SELF_MODIFICATION.md``: 1 is a new arrangement of what the machine can
already do, 2 is a new mechanism for a capability it already has (which the
built-in agent's gate can verify), 3 is new machine power — an entry in the
closed catalogue, which only a person may write. The center never offers to
build a rung-3 entry by itself or to hand one to an agent.

This registry lists gaps. It does not fill one: nothing here adds to the
capability catalogue, and the name is not an accident — the catalogue is the
machine's, this is a list of what the machine wishes it had.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from .model import (
    EXPERIMENTAL, FUTURE, INCOMPLETE, MISSING, NEEDS_IMPROVEMENT, CATEGORIES,
    CapabilityEntry, Snapshot,
)

LIVE, REVIEW, POLICY = "detected live", "source review", "policy"

#: Areas the platform adapter reports a mechanism for, in words.
AREAS: Dict[str, Tuple[str, str]] = {
    "volume": ("Audio volume control", "audio"),
    "speech": ("Spoken output", "audio"),
    "brightness": ("Screen brightness control", "display"),
    "resolution": ("Display resolution control", "display"),
    "network": ("Wireless networking", "network"),
    "bluetooth": ("Bluetooth", "bluetooth"),
    "screenshot": ("Whole-screen capture", "display"),
    "windows": ("Control of other programs' windows", "window"),
    "thermal": ("Temperature sensing", "hardware"),
}


class Facts:
    """What is true of this machine right now, gathered once per refresh.

    Everything is asked of the shell (*port*) and nothing is assumed: a port
    that cannot answer leaves the fact as None, and an entry that depends on
    it says the fact could not be established.
    """

    def __init__(self, port, snap: Optional[Snapshot] = None,
                 doctor: Optional[List[Tuple[str, str, str]]] = None,
                 hardware: Optional[List[object]] = None):
        self.port = port
        self.snap = snap or Snapshot()
        self.doctor = {name: (status, detail) for status, name, detail in (doctor or [])}
        self.doctor_run = doctor is not None
        self.hardware = hardware

        registry = getattr(port, "capabilities", None)
        self.catalogue: List[str] = []
        self.supported: List[str] = []
        if registry is not None:
            try:
                self.catalogue = list(registry.names())
                self.supported = [n for n in self.catalogue if registry.supported(n)]
            except Exception:
                pass
        self.domains = sorted({n.split(".", 1)[0] for n in self.catalogue})

        platform = getattr(port, "platform", None)
        self.platform_name = str(getattr(platform, "name", "") or "")
        self.mechanisms: Optional[Dict[str, str]] = None
        mechanisms = getattr(platform, "mechanisms", None)
        if callable(mechanisms):
            try:
                self.mechanisms = dict(mechanisms())
            except Exception:
                self.mechanisms = None

        corpus = getattr(port, "capability_corpus", None)
        self.unreachable: Tuple[str, ...] = ()
        self.phrases = 0
        if corpus is not None:
            try:
                self.unreachable = tuple(corpus.unreachable())
                self.phrases = sum(len(v) for v in corpus.speakable().values())
            except Exception:
                pass

        semantics = getattr(port, "semantics", None)
        self.learned = len(getattr(semantics, "aliases", {}) or {}) if semantics else None
        self.config = getattr(port, "config", None)

    def get(self, section: str, key: str, default: object = None) -> object:
        get = getattr(self.config, "get", None)
        return get(section, key, default) if callable(get) else default

    def has_domain(self, domain: str) -> bool:
        return domain in self.domains

    def unsupported(self, domain: str) -> List[str]:
        return [n for n in self.catalogue
                if n.startswith(domain + ".") and n not in self.supported]

    def check(self, name: str) -> Optional[Tuple[str, str]]:
        """``(status, first line of detail)`` for a doctor check, if it was run."""
        found = self.doctor.get(name)
        if found is None:
            return None
        lines = [l.strip() for l in str(found[1]).splitlines() if l.strip()]
        return found[0], (lines[0] if lines else "")

    def installed(self, program: str) -> Optional[bool]:
        """Whether *program* is among this machine's installed applications."""
        find = getattr(self.port, "find_program", None)
        if not callable(find):
            return None
        try:
            return find(program) is not None
        except Exception:
            return None


def _checked(facts: Facts, name: str, label: str) -> Tuple[str, bool]:
    """A sentence about a doctor check, and whether it is known to be good."""
    found = facts.check(name)
    if found is None:
        return (f"{label}: not checked this session (RUN CHECKS on DIAGNOSTICS).", False)
    status, detail = found
    return (f"{label}: {status.upper()} — {detail}", status == "ok")


# -- entries. Each returns an entry, or None when there is no gap to report. ------
def _platform_gaps(f: Facts) -> List[CapabilityEntry]:
    """One entry per area this machine has no mechanism for."""
    if f.mechanisms is None:
        return []
    out = []
    for area, (title, domain) in AREAS.items():
        if f.mechanisms.get(area, "none") != "none":
            continue
        off = f.unsupported(domain)
        evidence = [f"The {f.platform_name or 'platform'} adapter reports no mechanism "
                    f"for {area} on this machine."]
        if off:
            evidence.append(f"{len(off)} catalogue capabilit{'ies' if len(off) != 1 else 'y'} "
                            f"cannot run here: {', '.join(off)}.")
        out.append(CapabilityEntry(
            id=f"platform-{area}", title=f"{title}: no mechanism on this machine",
            category=INCOMPLETE, evidence=evidence, basis=LIVE,
            description="The capability exists in the catalogue; this machine lacks the "
                        "program or device the adapter uses to carry it out.",
            requirements=["Install the component the adapter looks for (docs/INSTALL.md) — "
                          "a person's act; the center installs nothing.",
                          "Or write an alternative mechanism in earth_os/platform/ "
                          "(docs/PORTING.md), verified by the conformance harness."],
            rung=2, difficulty="LOW"))
    return out


def _unreachable(f: Facts) -> Optional[CapabilityEntry]:
    if not f.unreachable:
        return None
    return CapabilityEntry(
        id="unreachable-capabilities", title="Capabilities nobody can ask for",
        category=INCOMPLETE, basis=LIVE,
        evidence=[f"{len(f.unreachable)} catalogue capabilities have no phrasing: "
                  + ", ".join(f.unreachable[:8]) + "."],
        description="A capability with no words for it can be pressed but never spoken.",
        requirements=["Add phrasings in earth_os/capability_corpus.py.",
                      "tests/test_corpus.py must pass."],
        rung=1, difficulty="LOW")


def _service_state(f: Facts) -> Optional[CapabilityEntry]:
    if f.has_domain("service"):
        return None
    return CapabilityEntry(
        id="service-state", title="Reading the state of system services",
        category=MISSING, basis=LIVE,
        evidence=[f"The catalogue has {len(f.catalogue)} capabilities in the domains "
                  f"{', '.join(f.domains) or 'none'}; none reads service state."],
        description="Earth OS cannot say whether a system service has failed, so SYSTEM "
                    "HEALTH reports services as unmeasurable rather than as healthy.",
        requirements=["A new read-only capability (for example service.list and "
                      "service.failed) in the catalogue — new machine power, so a person "
                      "writes the entry.",
                      "A platform method that raises PlatformError where there is no "
                      "service manager, rather than returning an empty list.",
                      "Phrasings in the corpus, and an entry in READINGS.",
                      "Then a rule in earth_os/evolution/health.py that reads it."],
        rung=3, difficulty="MODERATE")


def _system_logs(f: Facts) -> Optional[CapabilityEntry]:
    if f.has_domain("log"):
        return None
    return CapabilityEntry(
        id="system-logs", title="Reading system and application logs",
        category=MISSING, basis=LIVE,
        evidence=["The catalogue has no log-reading capability.",
                  "The only log the center can examine is Earth OS's own failed-start note."],
        description="Errors that programs and the kernel write down are invisible to "
                    "Earth OS, so 'application errors' is limited to its own components.",
        requirements=["A new read-only capability (for example log.recent with a bounded "
                      "line count) — new machine power; a person writes the entry.",
                      "A decision on what is shown: logs contain things a person may not "
                      "want read aloud or sent to a model.",
                      "earth_os/mind/redact.py applied before anything reaches a model."],
        rung=3, difficulty="MODERATE")


def _application_management(f: Facts) -> Optional[CapabilityEntry]:
    if f.has_domain("package"):
        return None
    return CapabilityEntry(
        id="application-management", title="Installing, updating and removing applications",
        category=MISSING, basis=LIVE,
        evidence=["The catalogue can start a program (process.launch) and stop one "
                  "(process.terminate); it has no capability that installs or removes one."],
        description="Applications are managed outside Earth OS, from a terminal.",
        requirements=["New effectful capabilities — new machine power; a person writes "
                      "the entries, and every one waits for a press.",
                      "An adapter per package system, each verified by conformance.",
                      "A read-only 'what is installed, what is out of date' first: it is "
                      "useful alone and changes nothing."],
        rung=3, difficulty="HIGH")


def _maintenance(f: Facts) -> Optional[CapabilityEntry]:
    return CapabilityEntry(
        id="automated-maintenance", title="Scheduled maintenance",
        category=MISSING, basis=POLICY,
        evidence=["Nothing in Earth OS runs maintenance on a schedule.",
                  "By rule (AGENTS.md §4) nothing effectful runs without a deliberate press."],
        description="Maintenance here can only mean *noticing on a schedule and proposing*: "
                    "the center already keeps a slow trend while hidden; it does not act on it.",
        requirements=["Service state and log reading first (they are what there is to maintain).",
                      "A notice — a toast, or a count on the home column — when discovery "
                      "finds something HIGH or above.",
                      "No unattended action. A maintenance step is a proposal like any other."],
        rung=1, difficulty="MODERATE", depends=["capability:service-state",
                                                "capability:system-logs"])


def _wine(f: Facts) -> Optional[CapabilityEntry]:
    installed = f.installed("wine")
    if installed is None:
        evidence = ["Could not establish whether Wine is installed: this shell has no "
                    "application index."]
    elif installed:
        evidence = ["Wine is among this machine's installed applications."]
    else:
        evidence = ["Wine is not among this machine's installed applications."]
    return CapabilityEntry(
        id="windows-compatibility", title="Running Windows programs through Wine",
        category=EXPERIMENTAL if installed else MISSING, basis=LIVE, evidence=evidence,
        description="Earth OS has no integration with Wine. With it installed, a Windows "
                    "program is one more program to launch; nothing lists, frames or "
                    "diagnoses them as such.",
        requirements=([] if installed else
                      ["Install Wine — a person's act; the center installs nothing."]) + [
            "Launcher entries for installed Windows programs, so PROGRAMS shows them.",
            "A check that frame mode can place their windows.",
            "No new machine power: this uses process.launch."],
        rung=1, difficulty="MODERATE")


def _per_process_cpu(f: Facts) -> Optional[CapabilityEntry]:
    processes = f.snap.value("processes")
    if not isinstance(processes, list) or not processes:
        return None
    timed = sum(1 for p in processes if float(getattr(p, "cpu_percent", 0.0) or 0.0) > 0.0)
    if timed:
        return None
    return CapabilityEntry(
        id="per-process-cpu", title="Processor time per program",
        category=NEEDS_IMPROVEMENT, basis=LIVE,
        evidence=[f"process.list returned {len(processes)} processes; none carries a "
                  "processor figure.",
                  "So 'which program is using the processor' cannot be answered, here or "
                  "in the spoken performance report."],
        description="The process reading reports memory for each process but leaves "
                    "processor time at zero, which needs two samples to compute.",
        requirements=["Keep the previous sample in the platform adapter and report the "
                      "difference — a new mechanism for an existing capability.",
                      "Raise PlatformError when it cannot be computed; do not report zero.",
                      "Conformance must show no capability disagreeing."],
        rung=2, difficulty="LOW")


def _hardware_detection(f: Facts) -> Optional[CapabilityEntry]:
    if f.hardware is None:
        return None
    kinds = sorted({str(getattr(d, "device_type", "?")) for d in f.hardware})
    wanted = {"storage", "display", "network", "input", "gpu"}
    if wanted <= set(kinds):
        return None
    return CapabilityEntry(
        id="hardware-detection", title="Fuller hardware detection",
        category=NEEDS_IMPROVEMENT, basis=LIVE,
        evidence=[f"hardware.enumerate lists {len(f.hardware)} "
                  f"device{'s' if len(f.hardware) != 1 else ''}, of kinds: "
                  f"{', '.join(kinds) or 'none'}.",
                  "Not listed: " + ", ".join(sorted(wanted - set(kinds))) + "."],
        description="The hardware list covers the processor and memory. Storage devices, "
                    "displays, graphics, network adapters and input devices are not listed, "
                    "so a hardware compatibility report cannot cover them.",
        requirements=["Extend the adapter behind the existing hardware.enumerate — a new "
                      "mechanism, no new capability.",
                      "Each device kind raises or is omitted when it cannot be read; none "
                      "is invented."],
        rung=2, difficulty="MODERATE")


def _storage_volumes(f: Facts) -> Optional[CapabilityEntry]:
    return CapabilityEntry(
        id="storage-volumes", title="Storage beyond the system volume",
        category=INCOMPLETE, basis=REVIEW,
        evidence=["hardware.disk reads one path, and every caller asks for the system "
                  "volume (earth_os/sysinfo.py).",
                  "Nothing lists the volumes there are, so a second disk filling up is "
                  "not seen."],
        description="Storage monitoring covers one volume.",
        requirements=["A reading that lists mounted volumes — a new capability, so a "
                      "person writes the catalogue entry.",
                      "Then the storage rule in health.py runs once per volume."],
        rung=3, difficulty="LOW")


def _honest_failure(f: Facts) -> Optional[CapabilityEntry]:
    return CapabilityEntry(
        id="honest-failure", title="Readings that still return a number on failure",
        category=NEEDS_IMPROVEMENT, basis=REVIEW,
        evidence=["In earth_os/platform/linux_adapter.py, uptime() returns 0.0 and "
                  "load_average() returns (0.0, 0.0, 0.0) when they cannot be read.",
                  "AGENTS.md §3 asks for PlatformError instead; the center works around "
                  "it by treating an uptime of zero as unavailable."],
        description="Two hardware readings still answer a failure with a plausible "
                    "figure — the defect AGENTS.md names as the one this codebase keeps "
                    "producing.",
        requirements=["Raise PlatformError in both; SystemMonitor already turns that "
                      "into a default for the gauges and a reason for anything that asks.",
                      "A new mechanism for an existing capability; conformance must agree."],
        rung=2, difficulty="LOW")


def _terminal_intelligence(f: Facts) -> Optional[CapabilityEntry]:
    mind = bool(f.get("mind", "enabled", True)) and bool(f.get("mind", "terminal", True))
    sentence, good = _checked(f, "earth mind", "Earth Mind")
    evidence = [f"Plain English in the terminal goes through Earth Mind: "
                f"{'on' if mind else 'off'} in the configuration.", sentence]
    if mind and good:
        return None
    return CapabilityEntry(
        id="terminal-intelligence", title="Terminal intelligence",
        category=NEEDS_IMPROVEMENT if mind else INCOMPLETE, basis=LIVE, evidence=evidence,
        description="The terminal translates plain English and answers questions about "
                    "the machine from readings. Without a model it cannot converse or "
                    "explain; the translator and readings still work.",
        requirements=["A local model the Companion can reach (see 'Local language model').",
                      "[mind] enabled and terminal = true."],
        rung=0, difficulty="LOW", depends=["capability:local-llm"])


def _natural_language(f: Facts) -> Optional[CapabilityEntry]:
    evidence = [f"{f.phrases} phrasings reach {len(f.catalogue) - len(f.unreachable)} of "
                f"{len(f.catalogue)} capabilities."]
    if f.learned is not None:
        evidence.append(f"{f.learned} learned meanings in the semantic store.")
    evidence.append("Naming a sequence by voice ('call that movie mode') is not built: "
                    "docs/SELF_MODIFICATION.md lists it as still to come.")
    return CapabilityEntry(
        id="natural-language-commands", title="Teaching a named sequence by voice",
        category=INCOMPLETE, basis=REVIEW, evidence=evidence,
        description="Compositions exist, are safe by construction and can be defined in "
                    "code. The utterance that captures a sequence and names it is missing.",
        requirements=["An utterance pattern that records the last capabilities run and "
                      "names them, calling Shell.define_composition.",
                      "No change to the five composition rules; tests/test_compositions.py "
                      "must pass unedited."],
        rung=1, difficulty="MODERATE")


def _local_llm(f: Facts) -> Optional[CapabilityEntry]:
    reasoning, ok_reasoning = _checked(f, "reasoning", "Reasoning backend")
    companion, ok_companion = _checked(f, "companion", "Companion model")
    if ok_reasoning and ok_companion:
        return None
    return CapabilityEntry(
        id="local-llm", title="Local language model",
        category=INCOMPLETE, basis=LIVE, evidence=[reasoning, companion],
        description="Conversation, explanation of findings and learning a new phrase all "
                    "need a model. Readings, commands and CDOS answers do not.",
        requirements=["A model server reachable at the configured host, with the "
                      "configured model pulled (docs/INSTALL.md) — a person's act.",
                      "Nothing in Earth OS needs changing."],
        rung=0, difficulty="LOW")


def _ai_diagnostics(f: Facts) -> Optional[CapabilityEntry]:
    return CapabilityEntry(
        id="ai-diagnostics", title="Model-assisted diagnosis beyond rewording",
        category=FUTURE, basis=POLICY,
        evidence=["Today a model may reword findings, and its answer is discarded if it "
                  "contains a figure the readings do not.",
                  "It is never asked what is wrong: the rules in health.py decide that."],
        description="A model could propose *hypotheses* — which of several measured things "
                    "is the likely cause — if each hypothesis named the reading that would "
                    "confirm it and the center then took that reading.",
        requirements=["Hypotheses as data: {claim, reading that tests it}; never an action.",
                      "Every claim checked against a reading before it is shown as a finding.",
                      "Service state, logs and per-process processor time, or there is "
                      "little for a hypothesis to be about."],
        rung=1, difficulty="HIGH",
        depends=["capability:per-process-cpu", "capability:system-logs"])


def _voice(f: Facts) -> Optional[CapabilityEntry]:
    enabled = bool(f.get("voice", "enabled", True))
    stt, ok_stt = _checked(f, "speech to text", "Speech to text")
    tts, ok_tts = _checked(f, "text to speech", "Text to speech")
    mic, ok_mic = _checked(f, "audio input", "Audio input")
    if enabled and ok_stt and ok_tts and ok_mic:
        return None
    return CapabilityEntry(
        id="voice-interaction", title="Voice interaction",
        category=INCOMPLETE, basis=LIVE,
        evidence=[f"Voice is {'enabled' if enabled else 'disabled'} in the configuration.",
                  stt, tts, mic],
        description="Speaking to the computer and being answered aloud needs a recorder, "
                    "a recogniser and a voice. Each is optional and each is checked.",
        requirements=["Install the missing components (make models; docs/INSTALL.md) — "
                      "a person's act.",
                      "The voice stack still names its mechanisms above the boundary "
                      "(tests/test_boundary.py allowances); moving them below is open work."],
        rung=0, difficulty="LOW")


def _cdos(f: Facts) -> Optional[CapabilityEntry]:
    sentence, good = _checked(f, "computer", "CDOS Computer")
    if good:
        return None
    return CapabilityEntry(
        id="cdos-integration", title="CDOS Computer link",
        category=INCOMPLETE, basis=LIVE,
        evidence=[sentence, f"[computer] mode = {f.get('computer', 'mode', 'auto')}."],
        description="Factual answers come from CDOS Computer, over its service socket or "
                    "embedded from the checkout.",
        requirements=["Run the cdos-api service (cdos-computer/tools/install_services.sh), "
                      "or keep cdos-computer/ beside the source — a person's act.",
                      "The center does not modify CDOS."],
        rung=0, difficulty="LOW")


def _robotics(f: Facts) -> Optional[CapabilityEntry]:
    return CapabilityEntry(
        id="robotics-integration", title="Robotics integration",
        category=EXPERIMENTAL, basis=REVIEW,
        evidence=[f"This machine runs the {f.platform_name or 'unknown'} adapter.",
                  "A robot stub adapter (earth_os/platform/robot_stub.py) and a simulator "
                  "(cdos-computer/computer/robotics/) ship; no adapter drives hardware."],
        description="The capability layer was built so the same CDOS could run a body "
                    "with no screen. That is demonstrated by a stub and a simulator only.",
        requirements=["A real adapter for one body, written to docs/PORTING.md.",
                      "The safety table in cdos-computer/computer/data/robot_safety.json "
                      "reviewed by a person for that body.",
                      "Every motion capability effectful: it waits for a press."],
        rung=3, difficulty="HIGH")


def _networking(f: Facts) -> Optional[CapabilityEntry]:
    return CapabilityEntry(
        id="networking-tools", title="Network diagnosis",
        category=MISSING, basis=LIVE,
        evidence=[f"The network domain has {sum(1 for n in f.catalogue if n.startswith('network.'))} "
                  "capabilities: interfaces, status, wireless state, scan, join, leave, forget.",
                  "None tests whether anything can be reached, or how quickly."],
        description="Earth OS can say what it is joined to but not whether the connection "
                    "works.",
        requirements=["A read-only reachability reading with a fixed, short list of "
                      "targets — new machine power; a person writes the entry.",
                      "No capability that takes an arbitrary address from a model."],
        rung=3, difficulty="MODERATE")


def _conformance(f: Facts) -> Optional[CapabilityEntry]:
    return CapabilityEntry(
        id="conformance-automation", title="Automatic conformance comparison in the gate",
        category=INCOMPLETE, basis=REVIEW,
        evidence=["docs/SELF_MODIFICATION.md: step 4 of the agent's gate — comparing "
                  "conformance before and after — is not automated."],
        description="A change below the boundary is verified by the suite and the ratchet, "
                    "but the portability comparison is run by hand.",
        requirements=["Take a conformance report before the agent's turn and compare after.",
                      "earth_os/agent/gate.py is human-write-only: a person makes this change."],
        rung=3, difficulty="MODERATE")


def _updates(f: Facts) -> Optional[CapabilityEntry]:
    return CapabilityEntry(
        id="update-mechanism", title="Updating Earth OS from inside Earth OS",
        category=FUTURE, basis=REVIEW,
        evidence=["Installation is install.sh; going back is git tags and docs/ROLLBACK.md.",
                  "Nothing checks for, fetches or applies a new version."],
        description="An update today is a person running the installer from a checkout.",
        requirements=["Decide where versions come from and how they are verified.",
                      "An update is a proposal: shown, approved, applied, with the previous "
                      "version kept to go back to.",
                      "Never unattended."],
        rung=3, difficulty="HIGH")


def _trend_persistence(f: Facts) -> Optional[CapabilityEntry]:
    return CapabilityEntry(
        id="trend-persistence", title="Trends that survive a restart",
        category=FUTURE, basis=REVIEW,
        evidence=["The center's samples are kept in memory (earth_os/evolution/trends.py) "
                  "and start again with each session."],
        description="'Compared with last week' cannot be answered. A leak is only "
                    "noticed within one session.",
        requirements=["A small on-disk series in the center's data directory, with a cap.",
                      "No new machine power; it stores readings already taken."],
        rung=1, difficulty="LOW")


ENTRIES: List[Callable[[Facts], Optional[CapabilityEntry]]] = [
    _unreachable, _service_state, _system_logs, _application_management, _maintenance,
    _wine, _per_process_cpu, _hardware_detection, _storage_volumes, _honest_failure,
    _terminal_intelligence, _natural_language, _local_llm, _ai_diagnostics, _voice,
    _cdos, _robotics, _networking, _conformance, _updates, _trend_persistence,
]


class CapabilityRegistry:
    def survey(self, facts: Facts) -> List[CapabilityEntry]:
        """Every gap found, grouped in the order of :data:`CATEGORIES`."""
        out: List[CapabilityEntry] = []
        try:
            out.extend(_platform_gaps(facts))
        except Exception:
            pass
        for entry in ENTRIES:
            try:
                found = entry(facts)
            except Exception:
                continue                  # one detector failing is not no registry
            if found is not None:
                out.append(found)
        out.sort(key=lambda e: (CATEGORIES.index(e.category), e.title))
        return out

    @staticmethod
    def grouped(entries: List[CapabilityEntry]) -> Dict[str, List[CapabilityEntry]]:
        groups: Dict[str, List[CapabilityEntry]] = {name: [] for name in CATEGORIES}
        for entry in entries:
            groups.setdefault(entry.category, []).append(entry)
        return groups
