"""The System Inspector: one honest look at the machine.

It owns no sampler and reads no file. Hardware figures come from the shell's
existing :class:`~earth_os.sysinfo.SystemMonitor` — the one rate-limited view
the gauges already use — so the Evolution Center, the DIAGNOSTICS screen and
the spoken answer to *"how busy is it"* are the same number. The few things
the monitor does not carry (the process list, the core count, the hardware
list) are asked for through ``Shell.take_reading``, which refuses anything
that is not a parameterless, non-effectful reading.

A reading that could not be taken is recorded as unavailable with its reason.
It is never recorded as zero: every rule in :mod:`.health` skips a reading it
does not have, rather than concluding something from a default.

*port* is the shell. Everything is reached with ``getattr`` and a default, so
a port with half of this missing — a robot, a test double — produces a
snapshot with half of it unavailable rather than an exception.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional

from .model import Reading, Snapshot


class Inspector:
    def __init__(self, port):
        self.port = port

    # -- small helpers -----------------------------------------------------------
    def _monitor(self):
        return getattr(self.port, "monitor", None)

    def _problem(self, key: str) -> str:
        monitor = self._monitor()
        problem = getattr(monitor, "problem", None)
        if callable(problem):
            try:
                return str(problem(key) or "")
            except Exception:
                return ""
        return ""

    def _from_monitor(self, snap: Snapshot, name: str, key: str, take) -> Reading:
        """One monitor reading, with the monitor's own account of any failure."""
        monitor = self._monitor()
        if monitor is None:
            return snap.put(Reading.missing(name, "no system monitor"))
        try:
            value = take(monitor)
        except Exception as exc:
            return snap.put(Reading.missing(name, f"{type(exc).__name__}: {exc}"))
        why = self._problem(key)
        if why:
            return snap.put(Reading.missing(name, why))
        return snap.put(Reading(name, value))

    def take(self, capability: str):
        """A capability reading as ``(ok, value, why)``, through the gated path."""
        take = getattr(self.port, "take_reading", None)
        if not callable(take):
            return False, None, "this shell cannot take readings"
        try:
            result = take(capability)
        except Exception as exc:
            return False, None, f"{type(exc).__name__}: {exc}"
        if not getattr(result, "ok", False):
            return False, None, str(getattr(result, "error", "") or "unavailable")
        return True, getattr(result, "value", None), ""

    # -- the snapshot ------------------------------------------------------------
    def snapshot(self, processes: bool = False) -> Snapshot:
        """Read the machine once.

        *processes* asks for the full process list as well, which costs more
        than everything else here together. It is left out unless a panel
        that shows it is on screen.
        """
        snap = Snapshot()
        put = snap.put

        # Earth OS's own processor seconds so far, every thread included.
        # Not a reading of the machine — this process's own clock, the way
        # the center already times its own samples — and the one figure
        # that says directly what an optimization of Earth OS did.
        put(Reading("own_cpu_time", time.process_time()))

        self._from_monitor(snap, "cpu", "cpu", lambda m: float(m.cpu()))

        cores = self._from_monitor(snap, "cores", "cores", lambda m: list(m.cores()))
        if cores.ok and not cores.value:
            put(Reading.missing("cores", "no per-core figures reported"))

        memory = self._from_monitor(snap, "memory", "memory", lambda m: m.memory())
        if memory.ok:
            fraction, used, total = memory.value                # type: ignore[misc]
            if not total:
                put(Reading.missing("memory", "the platform reported no memory"))
            else:
                put(Reading("memory", {"fraction": float(fraction), "used": int(used),
                                       "total": int(total),
                                       "available": max(int(total) - int(used), 0)}))

        swap = self._from_monitor(snap, "swap", "swap", lambda m: m.swap())
        if swap.ok:
            fraction, used, total = swap.value                  # type: ignore[misc]
            # No swap configured is a fact about the machine, not a failure.
            put(Reading("swap", {"fraction": float(fraction), "used": int(used),
                                 "total": int(total)}))

        disk = self._from_monitor(snap, "disk", "disk:/", lambda m: m.disk())
        if disk.ok:
            fraction, used, total = disk.value                  # type: ignore[misc]
            if not total:
                put(Reading.missing("disk", "the platform reported no storage"))
            else:
                put(Reading("disk", {"fraction": float(fraction), "used": int(used),
                                     "total": int(total),
                                     "free": max(int(total) - int(used), 0)}))

        self._from_monitor(snap, "load", "load", lambda m: tuple(float(v) for v in m.load()))

        uptime = self._from_monitor(snap, "uptime", "uptime", lambda m: float(m.uptime()))
        if uptime.ok and not uptime.value:
            put(Reading.missing("uptime", "the platform reported no uptime"))

        temperature = self._from_monitor(snap, "temperature", "temp", lambda m: m.temperature())
        if temperature.ok and temperature.value is None:
            put(Reading.missing("temperature", "no thermal sensor reported"))

        battery = self._from_monitor(snap, "battery", "battery", lambda m: m.battery())
        if battery.ok and battery.value is None:
            put(Reading.missing("battery", "no battery reported"))

        count = self._from_monitor(snap, "process_count", "procs", lambda m: int(m.processes()))
        if count.ok and not count.value:
            put(Reading.missing("process_count", "the platform reported no processes"))

        self._from_monitor(snap, "network", "net", lambda m: tuple(float(v) for v in m.net_rate()))

        for name, key, getter in (("host", "host", "hostname"), ("kernel", "kernel", "kernel"),
                                  ("distro", "distro", "distro"),
                                  ("cpu_model", "cpu_model", "cpu_model")):
            reading = self._from_monitor(snap, name, key,
                                         lambda m, g=getter: str(getattr(m, g)()))
            if reading.ok and reading.value in ("", "unknown"):
                put(Reading.missing(name, "not reported"))

        ok, value, why = self.take("hardware.cpu_count")
        put(Reading("cpu_count", int(value)) if ok and value else
            Reading.missing("cpu_count", why or "not reported"))

        if processes:
            self.add_processes(snap)

        self._add_render(snap)
        self._add_identity(snap)
        return snap

    def add_processes(self, snap: Snapshot) -> None:
        ok, value, why = self.take("process.list")
        if not ok or not isinstance(value, (list, tuple)):
            snap.put(Reading.missing("processes", why or "no process list"))
            snap.put(Reading.missing("own_memory", why or "no process list"))
            return
        listed: List[object] = list(value)
        snap.put(Reading("processes", listed))
        mine = os.getpid()
        own: Optional[int] = None
        for process in listed:
            if getattr(process, "pid", None) == mine:
                size = int(getattr(process, "memory_bytes", 0) or 0)
                own = size if size > 0 else None
                break
        snap.put(Reading("own_memory", own) if own is not None else
                 Reading.missing("own_memory", "this process is not in the list, or has no memory figure"))

    def _add_render(self, snap: Snapshot) -> None:
        stats = getattr(self.port, "render_stats", None)
        if not callable(stats):
            snap.put(Reading.missing("render", "no window is attached (headless)"))
            return
        try:
            value = dict(stats())
        except Exception as exc:
            snap.put(Reading.missing("render", f"{type(exc).__name__}: {exc}"))
            return
        snap.put(Reading("render", value))

    def _add_identity(self, snap: Snapshot) -> None:
        from .. import __version__
        snap.put(Reading("version", __version__))
        platform = getattr(self.port, "platform", None)
        name = getattr(platform, "name", "") if platform is not None else ""
        snap.put(Reading("platform", name) if name else
                 Reading.missing("platform", str(getattr(self.port, "platform_error", "")
                                                 or "no platform adapter")))
        size = getattr(self.port, "surface_size", None)
        if isinstance(size, (tuple, list)) and len(size) == 2:
            snap.put(Reading("surface", (int(size[0]), int(size[1]))))

    # -- on demand ---------------------------------------------------------------
    def hardware(self) -> Reading:
        """The attached hardware, as the platform lists it."""
        ok, value, why = self.take("hardware.enumerate")
        if not ok or not isinstance(value, (list, tuple)):
            return Reading.missing("hardware", why or "no hardware list")
        return Reading("hardware", list(value))
