"""Diagnostics: "what is the processor temperature?", "system status", "how much memory is free?".

Every figure is read from the machine at the moment it's asked (Linux /sys and /proc, the corpus's own
meta.json), and the trace says where.  A reading that can't be taken is said to be unavailable; it is never
estimated.  Nothing here writes anything.

    The processor temperature is 54 degrees Celsius.
    Processor load is 12 percent. 310 of 512 megabytes of memory are available. The corpus holds 1,460,000 facts
    about 140,000 subjects. Compute profile: balanced.
"""

from __future__ import annotations

import os
import re
import resource
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .compute import Sensors

ASKS = [   # (reading, regex) in the order a status report says them
    ("temperature", r"\b(?:processor|cpu|soc|chip|board|system|your)(?:'s)? (?:temperature|temp|heat)\b|"
                    r"\bhow (?:hot|warm) (?:are you|is (?:the|your) (?:processor|cpu|board|system))\b|\b(?:are you|is it) "
                    r"(?:overheating|too hot)\b"),
    ("load", r"\b(?:processor|cpu|system) (?:load|usage|use|utili[sz]ation|busy)\b|\bhow busy are you\b|\bload average\b"),
    ("memory", r"\b(?:memory|ram) (?:usage|use|free|left|available|status)\b|\bhow much (?:memory|ram) (?:is |do you have |"
               r"have you got )?(?:free|left|available|used|in use|do you have)\b|\bfree (?:memory|ram)\b|"
               r"\b(?:your|system) (?:memory|ram)\b"),
    ("disk", r"\b(?:disk|storage|ssd|sd card)(?: space)? (?:free|left|available|usage|used|status)\b|\bhow much (?:disk|"
             r"storage) (?:space )?(?:is )?(?:free|left|available|used)\b|\bfree (?:disk|storage) space\b|\bfree space\b"),
    ("battery", r"\bbattery (?:level|charge|percentage|status|state)\b|\bhow much (?:battery|charge) (?:is )?left\b|"
                r"\bare you (?:charging|on battery|plugged in)\b"),
    ("corpus", r"\bcorpus (?:status|size|stats|statistics)\b|\bhow (?:big|large) is (?:the|your) corpus\b|"
               r"\bhow many facts (?:do you|are in)\b"),
    ("uptime", r"\buptime\b|\bhow long have you been (?:on|running|up|awake)\b"),
]
REPORT = re.compile(r"\b(?:system|systems|status|health|diagnostic)s? (?:status|report|check|diagnostics?)\b|"
                    r"\b(?:run )?(?:a )?(?:self[- ]test|diagnostics?|status report|health check)\b|^status\??$|"
                    r"\bsystem status\b|\bhow are your systems\b", re.I)
STATUS = re.compile("|".join(f"(?:{rx})" for _, rx in ASKS) + "|" + REPORT.pattern, re.I)


def wants(text: str) -> list[str]:
    """Which readings a question asks for ([] if it isn't a diagnostics question).  A general report asks for all."""
    t = text.lower()
    got = [name for name, rx in ASKS if re.search(rx, t)]
    if REPORT.search(t):
        return ["temperature", "load", "memory", "disk", "battery", "corpus", "profile"]
    return got


@dataclass
class Readings:
    values: dict = field(default_factory=dict)     # name -> value (None: couldn't be read)
    sources: dict = field(default_factory=dict)    # name -> where it was read from


class Diagnostics:
    def __init__(self, sys_root: str = "/sys", proc_root: str = "/proc", corpus=None, engine=None):
        self.sensors = Sensors(sys_root)
        self.proc = Path(proc_root)
        self.corpus = corpus
        self.engine = engine

    def read(self, names: list[str]) -> Readings:
        r = Readings()
        if "temperature" in names or "load" in names or "battery" in names:
            s = self.sensors.read()
            r.values.update(temperature=s.temp_c, load=s.load, battery=s.battery_pct, on_battery=s.on_battery)
            r.sources.update(temperature=f"{self.sensors.root}/class/thermal/thermal_zone0/temp",
                             load="os.getloadavg() / cores", battery=f"{self.sensors.root}/class/power_supply")
        if "memory" in names:
            total = avail = None
            try:
                info = dict(re.findall(r"^(\w+):\s+(\d+)", (self.proc / "meminfo").read_text(), re.M))
                total, avail = int(info["MemTotal"]) / 1024, int(info.get("MemAvailable", info.get("MemFree"))) / 1024
            except (OSError, KeyError, ValueError, TypeError):
                pass
            r.values.update(mem_total_mb=total, mem_avail_mb=avail,
                            process_peak_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
            r.sources["memory"] = f"{self.proc}/meminfo, getrusage"
        if "disk" in names:
            base = Path(getattr(self.corpus, "base", ".") or ".")
            try:
                du = shutil.disk_usage(base if base.exists() else ".")
                r.values.update(disk_free_gb=du.free / 1e9, disk_total_gb=du.total / 1e9)
            except OSError:
                r.values.update(disk_free_gb=None)
            r.sources["disk"] = f"disk_usage({base})"
        if "corpus" in names:
            meta = getattr(self.corpus, "meta", None) or {}
            r.values.update(facts=meta.get("facts"), subjects=meta.get("subjects"),
                            shards_cached=len(getattr(self.corpus, "cache", {}) or {}))
            r.sources["corpus"] = f"{getattr(self.corpus, 'base', '?')}/meta.json"
        if "uptime" in names:
            try:
                r.values["uptime_s"] = float((self.proc / "uptime").read_text().split()[0])
            except (OSError, ValueError, IndexError):
                r.values["uptime_s"] = None
            r.sources["uptime"] = f"{self.proc}/uptime"
        if "profile" in names and self.engine is not None:
            r.values["profile"] = self.engine.profile
        return r

    def answer(self, text: str) -> tuple[str, list[str]] | None:
        names = wants(text)
        if not names:
            return None
        r = self.read(names)
        v = r.values
        out = []
        for n in names:
            if n == "temperature":
                t = v.get("temperature")
                out.append(f"The processor temperature is {t:.0f} degrees Celsius." if t is not None
                           else "The processor temperature is not available on this machine.")
            elif n == "load":
                ld = v.get("load")
                out.append(f"Processor load is {ld * 100:.0f} percent." if ld is not None
                           else "Processor load is not available.")
            elif n == "memory":
                if v.get("mem_avail_mb") is not None:
                    out.append(f"{v['mem_avail_mb']:,.0f} of {v['mem_total_mb']:,.0f} megabytes of memory are available; "
                               f"this process has used at most {v['process_peak_mb']:,.0f}.")
                else:
                    out.append(f"System memory figures are not available; this process has used at most {v['process_peak_mb']:,.0f} megabytes.")
            elif n == "disk":
                f = v.get("disk_free_gb")
                out.append(f"{f:,.1f} of {v['disk_total_gb']:,.1f} gigabytes of storage are free where the corpus is kept."
                           if f is not None else "Storage figures are not available.")
            elif n == "battery":
                b = v.get("battery")
                if b is None:
                    if len(names) == 1:
                        out.append("A battery reading is not available on this machine.")
                else:
                    state = " and discharging" if v.get("on_battery") else (" and on mains power" if v.get("on_battery") is False else "")
                    out.append(f"The battery is at {b:.0f} percent{state}.")
            elif n == "corpus":
                if v.get("facts") is not None:
                    out.append(f"The corpus holds {v['facts']:,} facts about {v['subjects']:,} subjects.")
                else:
                    out.append("The corpus is not loaded.")
            elif n == "uptime":
                u = v.get("uptime_s")
                out.append(f"This machine has been running for {_duration(u)}." if u is not None
                           else "Uptime is not available.")
            elif n == "profile" and v.get("profile"):
                out.append(f"Compute profile: {v['profile']}.")
        trace = [f"diagnostics: {', '.join(names)}"] + [f"  {k}: {src}" for k, src in r.sources.items()]
        return " ".join(out), trace


def _duration(s: float) -> str:
    d, rem = divmod(int(s), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = [f"{d} day{'s' * (d != 1)}"] * bool(d) + [f"{h} hour{'s' * (h != 1)}"] * bool(h) + \
            [f"{m} minute{'s' * (m != 1)}"] * (not d)
    return " and ".join(p for p in parts if p) or "under a minute"
