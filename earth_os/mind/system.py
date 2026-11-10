"""The system subsystem: the real machine, read through the capability layer.

Earth Mind does not look at the computer itself and does not let a model
describe it. Everything it says about the machine comes from here, and
everything here comes from the shell's closed capability catalogue
(``earth_os/platform/capabilities.py``) by way of the corpus that already
decides what the machine can be asked (``earth_os/capability_corpus.py``).
Nothing in this module names a mechanism, runs a program or reads a file.

Three things a request about the machine can be, and what happens to each:

**a reading or an analysis** — *"what is the CPU load"*, *"what is using the
most RAM"*. Taken now, from the machine, through the shell's own
``answer_reading`` / ``answer_analysis``, which re-check at the moment of
running that the capability is a parameterless, non-effectful **reading**.
The answer is the capability layer's sentence; a reading that cannot be taken
is said to be unavailable, never estimated.

**a diagnosis** — *"why is my computer running slowly"*. Still only readings:
an analysis whose findings the Companion is then asked to explain. The
figures are fixed before any model sees the question, and the Companion's
explanation is checked against them.

**an operation** — *"shut down"*, *"open Firefox"*. Never performed here.
:meth:`SystemSubsystem.propose` returns a
:class:`~earth_os.mind.envelope.Proposal` and the shell's gated path decides:
an effectful capability waits there for a deliberate press, and a program is
started only if it is one of the applications this machine has installed —
by that application's own launcher entry, never by anything typed or
generated.

This is why a model cannot invent a system condition (it is never asked for
one) and cannot run a command (nothing it writes is read as one).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..capability_corpus import ANALYSES, READINGS
from .envelope import Proposal

READING, ANALYSIS, DIAGNOSIS, ACTION, PROGRAM = "reading", "analysis", "diagnosis", "action", "program"

_LAUNCH = re.compile(
    r"^(?:please\s+)?(?:can you\s+|could you\s+)?(?:open|launch|start|run|bring up)\s+"
    r"(?:up\s+)?(?:the\s+|my\s+)?(?:program\s+|app\s+|application\s+)?(.{2,60}?)(?:\s+for me)?(?:\s+please)?$",
    re.I)


@dataclass
class SystemIntent:
    kind: str                          # reading | analysis | diagnosis | action | program
    capability: str = ""
    analysis: str = ""
    phrase: str = ""                   # the words that were recognised
    effectful: bool = False
    program: str = ""
    summary: str = ""


@dataclass
class SystemAnswer:
    ok: bool
    text: str
    findings: List[str] = field(default_factory=list)      # the verified sentences, one per finding
    table: str = ""


class SystemSubsystem:
    """What Earth Mind may know and ask of the machine. *port* is the shell.

    The port needs four things, all of which the shell already has or gains in
    this release: ``capability_corpus`` (to read a sentence), ``answer_reading``
    and ``answer_analysis`` (to take readings, checked), and ``find_program``
    (to look a name up among installed applications).
    """

    def __init__(self, port):
        self.port = port

    @property
    def available(self) -> bool:
        return getattr(self.port, "capabilities", None) is not None

    def status(self) -> str:
        if self.available:
            return "capability layer ready"
        return f"no platform adapter: {getattr(self.port, 'platform_error', '') or 'unknown'}"

    # -- reading a sentence -----------------------------------------------------------
    def resolve(self, text: str) -> Optional[SystemIntent]:
        """What *text* asks of the machine, or None if it is not about the machine."""
        corpus = getattr(self.port, "capability_corpus", None)
        intent = corpus.resolve(text) if corpus is not None and self.available else None
        if intent is not None:
            if intent.analysis:
                analysis = ANALYSES.get(intent.analysis)
                kind = DIAGNOSIS if getattr(analysis, "explained", False) else ANALYSIS
                return SystemIntent(kind, capability=intent.capability, analysis=intent.analysis,
                                    phrase=intent.phrase, summary=intent.explanation)
            reading = (intent.capability in READINGS and not intent.effectful
                       and not intent.arguments and not intent.explanation.startswith("cannot tell"))
            return SystemIntent(READING if reading else ACTION, capability=intent.capability,
                                phrase=intent.phrase, effectful=bool(intent.effectful),
                                summary=intent.explanation)
        return self._as_program(text)

    def _as_program(self, text: str) -> Optional[SystemIntent]:
        find = getattr(self.port, "find_program", None)
        match = _LAUNCH.match(" ".join((text or "").split()).rstrip(".!?"))
        if find is None or not match:
            return None
        found = find(match.group(1))
        if found is None:
            return None
        return SystemIntent(PROGRAM, capability="process.launch", phrase=match.group(0),
                            program=str(found[0]), summary=f"start {found[0]}")

    # -- taking readings --------------------------------------------------------------
    def answer(self, intent: SystemIntent, table: bool = False) -> SystemAnswer:
        """Take the readings *intent* asks for. Reads only; refuses anything else."""
        if intent.kind == DIAGNOSIS:
            # One set of readings, said as findings: the same figures are what
            # is shown, what is explained and what the explanation is checked
            # against. Taking them twice would let those three disagree.
            ok, text = self.port.answer_analysis(intent.analysis)
            return SystemAnswer(ok, text, findings=_sentences(text) if ok else [])
        if intent.kind == ANALYSIS:
            ok, text = self.port.answer_analysis(intent.analysis, table=table)
            return SystemAnswer(ok, text, findings=_sentences(text) if ok and not table else [],
                                table=text if ok and table else "")
        if intent.kind == READING:
            ok, text = self.port.answer_reading(intent.capability)
            return SystemAnswer(ok, text, findings=[text] if ok else [])
        return SystemAnswer(False, f"{intent.kind} is an operation, not a reading")

    # -- asking for an operation --------------------------------------------------------
    def propose(self, intent: SystemIntent, text: str) -> Optional[Proposal]:
        """The operation *intent* names, as a request for the shell's gated path."""
        if intent.kind == PROGRAM:
            return Proposal("program", text, target=intent.program, effectful=False,
                            summary=intent.summary)
        if intent.kind == ACTION:
            return Proposal("capability", text, target=intent.capability,
                            effectful=intent.effectful, summary=intent.summary)
        return None


def _sentences(text: str) -> List[str]:
    """An answer as separate findings: one per line, else one per sentence."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) > 1:
        return lines
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]
