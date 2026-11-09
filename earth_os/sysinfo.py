"""Live telemetry for the gauges, taken from the capability layer.

This module used to read ``/proc`` and ``/sys`` itself, in parallel with
:class:`~earth_os.platform.linux_adapter.LinuxHardware`, which was reading
the same files for the same numbers.  Two implementations of one reading is
not a tidiness problem — it is **two realities**:

    CDOS says the CPU is at 52 degrees.
    The gauge beside it says 57.

Both are "right", both were measured, and nothing in the system can say which
to believe.  Worse, the one that drifts will be the one nobody is testing,
because the tests were written against the other.

So the readings now come from one place.  What stays here is the part that
was never a mechanism:

* **Rate limiting.** The gauges are drawn on a thirty-times-a-second frame
  clock and the readings change perhaps once a second.  Deciding how often to
  ask is a display concern, not a fact about Linux.
* **Shapes the interface wants.** ``memory()`` returns a *fraction* alongside
  the bytes, because a bar needs a fraction and every caller would otherwise
  do the same division.
* **Formatting.** :func:`human_bytes` and :func:`human_duration` turn numbers
  into something a person reads.

The public surface is unchanged, so every screen and gauge kept working
through the move.  Underneath, each reading is now one call into the platform
— which means the engineering screen and the spoken answer to *"how hot is
it"* cannot disagree, because they are the same number.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from .platform import PlatformError, get_platform


class SystemMonitor:
    """A rate-limited view of the machine, for things drawn on a clock.

    Holds no counters and parses no kernel files.  It asks the platform, and
    remembers the answer briefly so that thirty frames a second cost one
    reading a second.
    """

    def __init__(self, interval: float = 1.0, platform=None):
        self.interval = interval
        self._platform = platform
        self._cache: Dict[str, object] = {}
        self._taken: Dict[str, float] = {}
        self._error = ""

    # ------------------------------------------------------------------
    @property
    def platform(self):
        """The adapter, fetched late so a shell can start without one.

        A machine with no adapter is a machine that cannot answer these
        questions, and the gauges show a dash rather than the shell refusing
        to start.
        """
        if self._platform is None:
            try:
                self._platform = get_platform()
            except PlatformError as exc:
                self._error = str(exc)
        return self._platform

    def _ask(self, key: str, take, default):
        """One reading, at most once per interval.

        Failure is cached like any other answer.  A machine with no thermal
        sensor would otherwise be asked thirty times a second, forever, for a
        number it has already said it does not have.
        """
        now = time.monotonic()
        if key in self._cache and now - self._taken.get(key, 0.0) < self.interval:
            return self._cache[key]
        platform = self.platform
        if platform is None:
            return default
        try:
            value = take(platform)
        except (PlatformError, OSError, ValueError, AttributeError) as exc:
            self._error = str(exc)
            value = default
        self._cache[key] = value
        self._taken[key] = now
        return value

    def poll(self, force: bool = False) -> None:
        """Refresh everything the gauges read.

        Kept because callers still invoke it, and because the sampled
        readings want asking regularly whether or not anyone is looking —
        a rate is the difference between two readings, so a gauge nobody
        watched for a minute would otherwise report that minute's average.
        """
        if force:
            self._cache.clear()
            self._taken.clear()
        self.cpu()
        self.net_rate()

    @property
    def last_error(self) -> str:
        return self._error

    # -- readings ------------------------------------------------------
    def cpu(self) -> float:
        return float(self._ask("cpu", lambda p: p.hardware.cpu_load(), 0.0))

    def cores(self) -> List[float]:
        return list(self._ask("cores", lambda p: p.hardware.cpu_load_per_core(), []))

    def memory(self) -> Tuple[float, int, int]:
        """``(fraction_used, used_bytes, total_bytes)``."""
        total, available = self._ask(
            "memory", lambda p: p.hardware.memory_bytes(), (0, 0))
        used = max(int(total) - int(available), 0)
        return ((used / total) if total else 0.0, used, int(total))

    def swap(self) -> Tuple[float, int, int]:
        total, free = self._ask("swap", lambda p: p.hardware.swap_bytes(), (0, 0))
        used = max(int(total) - int(free), 0)
        return ((used / total) if total else 0.0, used, int(total))

    def disk(self, path: str = "/") -> Tuple[float, int, int]:
        total, free = self._ask(
            f"disk:{path}", lambda p: p.hardware.disk_bytes(path), (0, 0))
        used = max(int(total) - int(free), 0)
        return ((used / total) if total else 0.0, used, int(total))

    def load(self) -> Tuple[float, float, float]:
        return tuple(self._ask(                       # type: ignore[return-value]
            "load", lambda p: p.hardware.load_average(), (0.0, 0.0, 0.0)))

    def uptime(self) -> float:
        return float(self._ask("uptime", lambda p: p.hardware.uptime(), 0.0))

    def temperature(self) -> Optional[float]:
        return self._ask("temp", lambda p: p.hardware.temperature(), None)

    def battery(self) -> Optional[Tuple[float, str]]:
        return self._ask("battery", lambda p: p.hardware.battery(), None)

    def net_rate(self) -> Tuple[float, float]:
        return tuple(self._ask(                       # type: ignore[return-value]
            "net", lambda p: p.hardware.net_rate(), (0.0, 0.0)))

    def hostname(self) -> str:
        return str(self._ask("host", lambda p: p.system.get_hostname(), "unknown"))

    def kernel(self) -> str:
        return str(self._ask("kernel", lambda p: p.system.kernel(), "unknown"))

    def distro(self) -> str:
        return str(self._ask("distro", lambda p: p.system.distro(), "unknown"))

    def cpu_model(self) -> str:
        return str(self._ask("cpu_model", lambda p: p.hardware.cpu_model(), "unknown"))

    def processes(self) -> int:
        return int(self._ask("procs", lambda p: p.hardware.process_count(), 0))


def human_bytes(n: float) -> str:
    for unit in ("B", "K", "M", "G", "T", "P"):
        if abs(n) < 1024.0:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}E"


def human_duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}"
    return f"{hours:02d}:{minutes:02d}"
