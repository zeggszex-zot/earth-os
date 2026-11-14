"""The words the System Evolution Center uses, and the shapes it passes around.

Nothing here reads the machine or decides anything. It is the vocabulary:
five severities, eight statuses, and a reading that is either a value or an
honest *could not be taken, and here is why*.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

# -- severity ------------------------------------------------------------------
INFORMATIONAL, LOW, MODERATE, HIGH, CRITICAL = (
    "INFORMATIONAL", "LOW", "MODERATE", "HIGH", "CRITICAL")
SEVERITIES = (INFORMATIONAL, LOW, MODERATE, HIGH, CRITICAL)


def severity_rank(severity: str) -> int:
    """0 for INFORMATIONAL up to 4 for CRITICAL; unknown words rank lowest."""
    return SEVERITIES.index(severity) if severity in SEVERITIES else 0


# -- roadmap statuses ----------------------------------------------------------
DISCOVERED, PROPOSED, APPROVED, IN_DEVELOPMENT, TESTING, COMPLETED, REJECTED, ROLLED_BACK = (
    "DISCOVERED", "PROPOSED", "APPROVED", "IN DEVELOPMENT", "TESTING",
    "COMPLETED", "REJECTED", "ROLLED BACK")
STATUSES = (DISCOVERED, PROPOSED, APPROVED, IN_DEVELOPMENT, TESTING,
            COMPLETED, REJECTED, ROLLED_BACK)

# -- capability categories -----------------------------------------------------
MISSING, INCOMPLETE, NEEDS_IMPROVEMENT, EXPERIMENTAL, FUTURE = (
    "MISSING", "INCOMPLETE", "NEEDS IMPROVEMENT", "EXPERIMENTAL", "FUTURE")
CATEGORIES = (MISSING, INCOMPLETE, NEEDS_IMPROVEMENT, EXPERIMENTAL, FUTURE)

# -- optimization states -------------------------------------------------------
RECOMMENDED, AVAILABLE, IN_EFFECT = "RECOMMENDED", "AVAILABLE", "IN EFFECT"


@dataclass(frozen=True)
class Reading:
    """One thing asked of the machine: a value, or why there is none.

    ``ok`` False means the reading could not be taken. It never means zero.
    A gauge may draw an empty bar for that; anything that reasons about the
    figure has to be able to tell the two apart, because *the processor is
    idle* and *the processor could not be read* lead to opposite advice.
    """

    name: str
    value: object = None
    ok: bool = True
    why: str = ""

    @classmethod
    def missing(cls, name: str, why: str) -> "Reading":
        return cls(name, None, False, why or "unavailable")


@dataclass
class Snapshot:
    """Everything read in one pass, by name."""

    taken: float = field(default_factory=time.time)
    readings: Dict[str, Reading] = field(default_factory=dict)

    def put(self, reading: Reading) -> Reading:
        self.readings[reading.name] = reading
        return reading

    def get(self, name: str) -> Reading:
        found = self.readings.get(name)
        return found if found is not None else Reading.missing(name, "not read")

    def has(self, name: str) -> bool:
        return self.get(name).ok

    def value(self, name: str, default: object = None) -> object:
        reading = self.get(name)
        return reading.value if reading.ok else default

    def unavailable(self) -> List[Reading]:
        return [r for r in self.readings.values() if not r.ok]


@dataclass
class Finding:
    """Something the Health Analyzer concluded, and what it rests on.

    ``evidence`` is the figures, as sentences. A finding with no evidence is
    an opinion, and the analyzer is not allowed opinions.
    """

    id: str
    area: str
    severity: str
    title: str
    explanation: str = ""
    suggestion: str = ""
    evidence: List[str] = field(default_factory=list)
    #: The optimization or capability entry that addresses it, if one does.
    remedy: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class Optimization:
    """One possible change, with the six things a person is owed about it."""

    id: str
    title: str
    area: str
    changes: str                # 1. what it changes
    why: str                    # 2. why it could improve performance
    benefits: str               # 3. expected benefits — an estimate, said as one
    drawbacks: str              # 4. potential disadvantages
    restart: bool               # 5. whether a restart is necessary
    reverse: str                # 6. how to reverse the change
    #: Where the expectation comes from. Never a measurement of this change
    #: on this machine: that only exists after it has been applied.
    basis: str = ""
    state: str = AVAILABLE
    #: Why it is recommended now (or already in effect), from live readings.
    reason: str = ""
    #: ``{"section", "key", "value"}`` when the center can apply it once
    #: approved; None when a person has to carry it out.
    change: Optional[Dict[str, object]] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class CapabilityEntry:
    """Something Earth OS cannot do, half does, or could do better."""

    id: str
    title: str
    category: str
    #: What was found, and how: detected on this machine now, read from the
    #: source at the time this entry was written, or a matter of policy.
    evidence: List[str] = field(default_factory=list)
    basis: str = "detected live"
    description: str = ""
    requirements: List[str] = field(default_factory=list)
    #: The rung of docs/SELF_MODIFICATION.md it would be built on: 1 a new
    #: arrangement of existing power, 2 a new mechanism for an existing
    #: capability, 3 new power that only a person may grant. 0 is none of
    #: those: installing something, or configuration.
    rung: int = 0
    difficulty: str = "MODERATE"
    depends: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def human_bytes(count: object) -> str:
    """A byte figure a person can read; ``"?"`` for something that is not one."""
    try:
        size = float(count)                     # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_span(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 90:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes:02d} min"
    return f"{hours // 24} d {hours % 24} h"


def plain_os_error(exc: BaseException) -> str:
    """An operating-system error as a person would put it, with the path.

    ``[Errno 17] File exists: '/…/reports'`` is the system's account of why
    an export failed; *"something that is not a folder is in the way at
    /…/reports"* is the reason. Unrecognised errors keep the system's words.
    """
    import errno
    if not isinstance(exc, OSError):
        return str(exc)
    where = f" ({exc.filename})" if getattr(exc, "filename", None) else ""
    reasons = {
        errno.EEXIST: "something that is not a folder is in the way",
        errno.ENOTDIR: "part of the path is a file, not a folder",
        errno.EISDIR: "a folder is where the file should be",
        errno.EACCES: "permission denied",
        errno.EPERM: "permission denied",
        errno.ENOSPC: "the disk is full",
        errno.EROFS: "the file system is read-only",
        errno.ENOENT: "the folder does not exist",
        errno.EDQUOT: "the disk quota is used up",
    }
    reason = reasons.get(exc.errno or -1)
    return f"{reason}{where}" if reason else f"{exc.strerror or exc}{where}"

