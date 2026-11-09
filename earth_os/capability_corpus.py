"""The system corpus: how a person asks for what this machine can do.

The capability catalogue says what the computer *can* do.  It said nothing
about how a person would ask for it, so until this module existed 31 of the
38 capabilities were unreachable by any words at all — the machine could read
its own temperature, set its own brightness and list its own networks, and
there was no sentence that would make it.  ``run_capability`` was called only
by tests.

This is the missing half: **meaning for every capability**, and it lives above
the boundary because meaning always does.  The adapter is never asked what
something is called.  A phrasing here names a capability and nothing else —
no mechanism appears in this file, and ``tests/test_boundary.py`` enforces it.

The important property is that the two halves cannot drift apart.  The
phrasebook is keyed by capability name and checked against the catalogue, so:

* a phrasing for a capability that does not exist is a **test failure**;
* a capability with no phrasing is a **test failure**.

Add a capability and the suite tells you it cannot be spoken yet.  That is the
guarantee worth having, because the alternative — a vocabulary maintained by
hand alongside a catalogue maintained by hand — drifts silently, and the
symptom is a machine that can do something nobody can ask for.

Relative adjustment gets its own table.  *"Make it brighter"* is not a
capability; it is a reading followed by a write, and the arithmetic between
them is meaning rather than mechanism, so it belongs up here too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .platform.capabilities import CATALOGUE

#: How a person asks for each capability.
#:
#: Phrasings are matched against a normalised utterance, longest first, so a
#: specific reading wins over a general one that happens to be a prefix of it.
#: They are deliberately plain: this is the fixed floor that works with no
#: model at all, and the learned layer is what grows it.
PHRASEBOOK: Dict[str, Tuple[str, ...]] = {
    # -- system ---------------------------------------------------------
    "system.reboot": ("reboot", "restart the computer", "restart the machine",
                      "restart", "start over", "reboot the computer"),
    "system.shutdown": ("shut down", "shutdown", "power off", "turn it off",
                        "turn the computer off", "switch off"),
    "system.suspend": ("suspend", "sleep", "go to sleep", "standby"),
    "system.lock": ("lock", "lock the screen", "lock it", "lock the session"),
    "system.logout": ("log out", "log off", "sign out", "end my session"),
    "system.kernel": ("what kernel", "kernel version", "what kernel are you running"),
    "system.distro": ("what are you running on", "what distro", "what system is this",
                      "what linux is this"),
    "system.hostname": ("what is your name", "hostname", "what machine is this",
                        "what is this computer called"),
    # -- display --------------------------------------------------------
    "display.get_brightness": ("how bright is the screen", "what is the brightness",
                               "screen brightness"),
    "display.set_brightness": ("set the brightness", "set brightness",
                               "change the brightness"),
    "display.get_resolution": ("what is the resolution", "screen resolution",
                               "how big is the screen"),
    "display.set_resolution": ("set the resolution", "change the resolution"),
    "display.screenshot": ("take a screenshot", "screenshot", "screen shot",
                           "take a screen shot", "capture the screen",
                           "take a picture of the screen"),
    # -- audio ----------------------------------------------------------
    "audio.get_volume": ("how loud is it", "what is the volume", "volume"),
    "audio.set_volume": ("set the volume", "change the volume"),
    "audio.speak": ("say", "speak", "read it out", "say out loud"),
    "audio.listen": ("listen", "start listening", "take dictation"),
    # -- hardware -------------------------------------------------------
    "hardware.temperature": ("how hot is it", "what is the temperature",
                             "temperature", "are you overheating"),
    "hardware.battery": ("how is the battery", "battery", "battery level",
                         "how much charge is left"),
    "hardware.memory": ("how much memory", "memory", "how much ram",
                        "memory usage"),
    "hardware.cpu_count": ("how many cores", "how many processors", "cpu count"),
    "hardware.cpu_model": ("what processor", "what cpu", "cpu model"),
    "hardware.enumerate": ("what hardware", "list hardware", "what devices",
                           "what are you made of"),
    "hardware.cpu_load": ("how busy are you", "cpu load", "how busy is the cpu",
                          "processor load"),
    "hardware.cpu_load_per_core": ("load per core", "how busy is each core"),
    "hardware.load_average": ("load average", "what is the load"),
    "hardware.swap": ("how much swap", "swap usage", "is it swapping"),
    "hardware.disk": ("how full is the disk", "disk space", "how much space is left",
                      "disk usage"),
    "hardware.uptime": ("how long have you been up", "uptime", "how long running"),
    "hardware.processes": ("how many processes", "how many programs are running",
                           "process count"),
    "hardware.net_rate": ("how fast is the network", "network speed",
                          "network rate"),
    # -- network --------------------------------------------------------
    "network.interfaces": ("what networks", "list interfaces", "network interfaces",
                           "how am i connected"),
    "network.status": ("network status", "is the network up"),
    "network.connect": ("connect to", "join the network", "connect to wifi"),
    "network.disconnect": ("disconnect", "go offline", "drop the network"),
    "network.wireless": ("wifi status", "is wifi on", "what wifi am i on",
                         "what network am i on", "wireless status",
                         "am i online"),
    "network.scan": ("scan for networks", "scan for wifi", "list wifi networks",
                     "what wifi networks are there", "find wifi",
                     "wireless networks", "networks in range"),
    "network.wifi_on": ("turn on wifi", "turn wifi on", "switch on wifi",
                        "switch wifi on", "enable wifi", "wifi on",
                        "turn on the wifi", "turn the wifi on",
                        "switch on the wifi"),
    "network.wifi_off": ("turn off wifi", "turn wifi off", "switch off wifi",
                         "switch wifi off", "disable wifi", "wifi off",
                         "turn off the wifi", "turn the wifi off",
                         "switch off the wifi"),
    "network.forget": ("forget the network", "forget this network",
                       "forget the wifi network"),
    # -- process --------------------------------------------------------
    "process.list": ("what is running", "list processes", "running processes",
                     "what programs are running"),
    "process.launch": ("open", "start", "run", "launch"),
    "process.inspect": ("inspect process", "look at process", "process details"),
    "process.terminate": ("kill", "stop the program", "terminate", "close it"),
    # -- filesystem -----------------------------------------------------
    "filesystem.list": ("list files", "what files", "show the folder",
                        "what is in this folder"),
    "filesystem.read": ("read the file", "open the file", "show the file"),
    "filesystem.write": ("write the file", "save the file"),
    "filesystem.delete": ("delete the file", "remove the file", "throw it away"),
    "filesystem.exists": ("does the file exist", "is there a file"),
    "filesystem.create_directory": ("make a folder", "create a folder",
                                    "new folder", "make a directory"),
    # -- window ---------------------------------------------------------
    "window.list": ("what windows are open", "list windows", "what is on screen",
                    "what windows"),
    "window.focus": ("switch to", "bring to the front", "focus the window"),
    "window.close": ("close the window", "close that window", "shut the window"),
    "window.fullscreen": ("full screen", "make it full screen", "maximise"),
    "window.place": ("place the window", "move the window"),
    "window.can_place": ("can you place windows", "can windows be moved"),
    # -- user -----------------------------------------------------------
    "user.identity": ("who am i", "who is logged in", "what is my name"),
    "user.full_name": ("what is my full name", "my full name"),
    "user.home": ("where is my home", "my home folder", "home directory"),
    "user.sessions": ("who else is logged in", "list sessions", "active sessions"),
    # -- bluetooth ------------------------------------------------------
    "bluetooth.state": ("is bluetooth on", "bluetooth status"),
    "bluetooth.on": ("turn on bluetooth", "turn bluetooth on",
                     "switch on bluetooth", "switch bluetooth on",
                     "enable bluetooth", "bluetooth on",
                     "turn on the bluetooth", "switch on the bluetooth"),
    "bluetooth.off": ("turn off bluetooth", "turn bluetooth off",
                      "switch off bluetooth", "switch bluetooth off",
                      "disable bluetooth", "bluetooth off",
                      "turn off the bluetooth", "switch off the bluetooth"),
    "bluetooth.devices": ("bluetooth devices", "what bluetooth devices",
                          "list bluetooth devices", "paired devices"),
    "bluetooth.scan": ("scan for bluetooth", "find bluetooth devices",
                       "look for bluetooth devices", "bluetooth scan",
                       "search for bluetooth devices"),
    "bluetooth.connect": ("connect bluetooth", "pair bluetooth device",
                          "pair a device"),
    "bluetooth.disconnect": ("disconnect bluetooth", "disconnect the headphones",
                             "disconnect bluetooth devices"),
    "bluetooth.forget": ("forget the bluetooth device", "unpair",
                         "unpair the device"),
}


@dataclass(frozen=True)
class Adjustment:
    """A relative change: read a value, move it, write it back.

    *"Brighter"* names no capability.  It names an arithmetic relationship
    between two of them, which is meaning, so the sum happens up here and the
    adapter is only ever told an absolute figure.
    """

    read: str
    write: str
    delta: float
    argument: str = "level"


#: Relative phrasings, and what they do to the reading they act on.
ADJUSTMENTS: Dict[str, Adjustment] = {
    "brighter": Adjustment("display.get_brightness", "display.set_brightness", +0.10),
    "make it brighter": Adjustment("display.get_brightness", "display.set_brightness", +0.10),
    "make the screen brighter": Adjustment("display.get_brightness", "display.set_brightness", +0.10),
    "turn up the screen": Adjustment("display.get_brightness", "display.set_brightness", +0.10),
    "dimmer": Adjustment("display.get_brightness", "display.set_brightness", -0.10),
    "make it dimmer": Adjustment("display.get_brightness", "display.set_brightness", -0.10),
    "darker": Adjustment("display.get_brightness", "display.set_brightness", -0.10),
    "turn down the screen": Adjustment("display.get_brightness", "display.set_brightness", -0.10),
    "louder": Adjustment("audio.get_volume", "audio.set_volume", +0.10),
    "turn it up": Adjustment("audio.get_volume", "audio.set_volume", +0.10),
    "turn the volume up": Adjustment("audio.get_volume", "audio.set_volume", +0.10),
    "quieter": Adjustment("audio.get_volume", "audio.set_volume", -0.10),
    "turn it down": Adjustment("audio.get_volume", "audio.set_volume", -0.10),
    "turn the volume down": Adjustment("audio.get_volume", "audio.set_volume", -0.10),
}

#: Which capability an absolute percentage is being given to.
PERCENT_TARGETS: Dict[str, Tuple[str, str]] = {
    "brightness": ("display.set_brightness", "level"),
    "screen": ("display.set_brightness", "level"),
    "volume": ("audio.set_volume", "level"),
    "sound": ("audio.set_volume", "level"),
}

_PERCENT = re.compile(r"(\d{1,3})\s*(?:%|per ?cent)")

#: Capabilities that answer a question rather than do a thing.
#:
#: The distinction matters at the moment of replying.  An action that returns
#: nothing succeeded silently and should stay silent — saying "nothing to
#: report" after locking the screen would be odd.  A *reading* that returns
#: nothing has failed to find anything, and staying silent there looks like
#: the machine ignored the question.  ``hardware.temperature`` on a box with
#: no thermal sensor is exactly that case.
READINGS: frozenset = frozenset({
    "system.hostname",
    "display.get_brightness", "display.get_resolution",
    "audio.get_volume",
    "hardware.temperature", "hardware.battery", "hardware.memory",
    "hardware.cpu_load", "hardware.cpu_load_per_core", "hardware.load_average",
    "hardware.swap", "hardware.disk", "hardware.uptime", "hardware.processes",
    "hardware.net_rate", "system.kernel", "system.distro",
    "hardware.cpu_count", "hardware.cpu_model", "hardware.enumerate",
    "network.interfaces", "network.status",
    "window.list", "window.can_place",
    "process.list", "process.inspect",
    "filesystem.exists", "filesystem.list", "filesystem.read",
    "user.identity", "user.full_name", "user.home", "user.sessions",
    "network.wireless", "network.scan",
    "bluetooth.state", "bluetooth.devices", "bluetooth.scan",
    "display.screenshot",
})


def _bytes(count: object) -> str:
    """A byte figure a person can hear without counting zeroes."""
    try:
        size = float(count)
    except (TypeError, ValueError):
        return str(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def normalise(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    text = (text or "").lower().strip()
    text = re.sub(r"[^\w\s%]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class CapabilityIntent:
    """A reading of an utterance as something the machine can do."""

    capability: str
    arguments: Dict[str, object] = field(default_factory=dict)
    phrase: str = ""
    explanation: str = ""

    @property
    def effectful(self) -> bool:
        spec = CATALOGUE.get(self.capability)
        return bool(spec and spec.effectful)


class CapabilityCorpus:
    """Resolves what a person said into something the catalogue names.

    Consulted after the fixed vocabulary and the learned layer, and *before*
    any model: the machine's own account of itself is better evidence than a
    model's guess about it, and it costs nothing to ask.
    """

    def __init__(self, registry=None):
        self.registry = registry
        self._ordered = sorted(
            ((phrase, capability)
             for capability, phrases in PHRASEBOOK.items()
             for phrase in phrases),
            key=lambda pair: len(pair[0]), reverse=True)
        self._adjustments = sorted(ADJUSTMENTS.items(),
                                   key=lambda pair: len(pair[0]), reverse=True)

    # ------------------------------------------------------------------
    def resolve(self, text: str) -> Optional[CapabilityIntent]:
        """The best reading of *text*, or None if it names nothing."""
        cleaned = normalise(text)
        if not cleaned:
            return None
        return (self._as_adjustment(cleaned)
                or self._as_percentage(cleaned)
                or self._as_phrase(cleaned))

    # ------------------------------------------------------------------
    def _as_adjustment(self, cleaned: str) -> Optional[CapabilityIntent]:
        """"Make it brighter" — read the value, move it, hand back an absolute.

        Needs a live reading, so it needs the registry.  If the reading cannot
        be taken this declines rather than guessing a starting point: moving
        from an invented value would put the screen somewhere the person did
        not ask for, which is worse than saying so.
        """
        for phrase, adjustment in self._adjustments:
            if not self._mentions(cleaned, phrase):
                continue
            if self.registry is None:
                return None
            reading = self.registry.invoke(adjustment.read)
            if not reading.ok:
                return CapabilityIntent(
                    capability=adjustment.write, arguments={},
                    phrase=phrase,
                    explanation=f"cannot tell how it is set now: {reading.error}")
            try:
                current = float(reading.value)          # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
            target = max(0.0, min(1.0, current + adjustment.delta))
            return CapabilityIntent(
                capability=adjustment.write,
                arguments={adjustment.argument: target},
                phrase=phrase,
                explanation=f"{current:.0%} to {target:.0%}")
        return None

    def _as_percentage(self, cleaned: str) -> Optional[CapabilityIntent]:
        """"Set the brightness to 40 percent"."""
        match = _PERCENT.search(cleaned)
        if not match:
            return None
        for word, (capability, argument) in PERCENT_TARGETS.items():
            if word in cleaned:
                level = max(0, min(100, int(match.group(1)))) / 100.0
                return CapabilityIntent(
                    capability=capability, arguments={argument: level},
                    phrase=match.group(0), explanation=f"to {level:.0%}")
        return None

    def _as_phrase(self, cleaned: str) -> Optional[CapabilityIntent]:
        for phrase, capability in self._ordered:
            if self._mentions(cleaned, phrase):
                spec = CATALOGUE.get(capability)
                if spec is None:
                    continue
                # Anything needing an argument this cannot supply is left to
                # the shell's own vocabulary, which knows about paths and
                # program names. Claiming it here would shadow that.
                if spec.params:
                    continue
                return CapabilityIntent(
                    capability=capability, phrase=phrase,
                    explanation=spec.summary)
        return None

    @staticmethod
    def _mentions(cleaned: str, phrase: str) -> bool:
        """Whole-phrase match, so "lock" does not fire inside "clock"."""
        return re.search(rf"(?:^|\s){re.escape(phrase)}(?:\s|$)", cleaned) is not None

    # ------------------------------------------------------------------
    def speakable(self) -> Dict[str, Tuple[str, ...]]:
        """Capability to phrasings, for putting in front of a person or model."""
        return dict(PHRASEBOOK)

    def unreachable(self) -> Tuple[str, ...]:
        """Capabilities the catalogue has that no phrasing reaches."""
        return tuple(sorted(set(CATALOGUE) - set(PHRASEBOOK)))

    def say_result(self, capability: str, value: object) -> str:
        """Put a capability's answer into words.

        Asking *"how hot is it"* and being told ``hardware.temperature ok`` is
        not an answer, and until queries became reachable nothing noticed —
        the only capabilities anyone could invoke were ones where the doing
        was the point. Turning a reading into a sentence is meaning, not
        mechanism, so it happens here rather than in the adapter: a robot and
        a laptop report the same temperature the same way.
        """
        if value is None:
            return "Nothing to report."

        if capability == "hardware.memory" and isinstance(value, (tuple, list)) \
                and len(value) == 2:
            total, available = value
            used = max(int(total) - int(available), 0)
            return (f"{_bytes(used)} of {_bytes(total)} in use, "
                    f"{_bytes(available)} free.")
        if capability == "hardware.temperature":
            return f"{float(value):.0f} degrees."
        if capability == "hardware.battery" and isinstance(value, (tuple, list)) \
                and len(value) == 2:
            level, state = value
            return f"Battery {float(level):.0%}, {state}."
        if capability in ("display.get_brightness", "audio.get_volume"):
            return f"{float(value):.0%}."
        if capability == "display.get_resolution" and isinstance(value, (tuple, list)):
            return f"{value[0]} by {value[1]}."
        if capability == "hardware.uptime":
            seconds = int(float(value))
            days, rest = divmod(seconds, 86400)
            hours, rest = divmod(rest, 3600)
            minutes = rest // 60
            if days:
                return f"{days} days, {hours} hours."
            return f"{hours} hours, {minutes} minutes." if hours \
                else f"{minutes} minutes."
        if capability == "hardware.cpu_load":
            return f"{float(value):.0%}."
        if capability == "hardware.cpu_load_per_core" and isinstance(value, list):
            return ", ".join(f"{v:.0%}" for v in value) + "." if value else "Unknown."
        if capability == "hardware.load_average" and isinstance(value, (tuple, list)):
            return " / ".join(f"{float(v):.2f}" for v in value) + "."
        if capability in ("hardware.swap", "hardware.disk") \
                and isinstance(value, (tuple, list)) and len(value) == 2:
            total, free = value
            if not total:
                return "None." if capability == "hardware.swap" else "Unknown."
            used = max(int(total) - int(free), 0)
            return (f"{_bytes(used)} of {_bytes(total)} used, "
                    f"{_bytes(free)} free.")
        if capability == "hardware.net_rate" and isinstance(value, (tuple, list)):
            rx, tx = value
            return f"{_bytes(rx)} down, {_bytes(tx)} up, per second."
        if capability == "hardware.processes":
            return f"{value} processes."
        if capability == "hardware.cpu_count":
            return f"{value} cores."
        if capability == "network.interfaces" and isinstance(value, list):
            names = [getattr(i, "name", str(i)) for i in value]
            return f"{len(names)}: {', '.join(names)}." if names else "None."
        if capability == "network.wireless":
            if not getattr(value, "hardware", True):
                return "No wireless hardware."
            if not getattr(value, "enabled", False):
                return "Wireless is off."
            ssid = getattr(value, "ssid", "")
            return f"Connected to {ssid}." if ssid \
                else "Wireless is on, not connected."
        if capability == "network.scan" and isinstance(value, list):
            names = [getattr(p, "ssid", str(p)) for p in value]
            if not names:
                return "No wireless networks in range."
            more = f", and {len(names) - 5} more" if len(names) > 5 else ""
            return (f"{len(names)} in range: "
                    f"{', '.join(names[:5])}{more}.")
        if capability == "bluetooth.state":
            return ("Bluetooth is on." if getattr(value, "powered", False)
                    else "Bluetooth is off.")
        if capability in ("bluetooth.devices", "bluetooth.scan") \
                and isinstance(value, list):
            if not value:
                return "No bluetooth devices."
            named = [d for d in value
                     if getattr(d, "name", "") != getattr(d, "address", "")]
            connected = [d.name for d in value if getattr(d, "connected", False)]
            text = f"{len(value)} devices"
            if connected:
                text += f", connected: {', '.join(connected)}"
            elif named:
                text += f": {', '.join(d.name for d in named[:5])}"
            return text + "."
        if capability == "display.screenshot":
            return f"Screenshot saved to {value}."
        if capability == "process.list" and isinstance(value, list):
            return f"{len(value)} processes running."
        if capability == "hardware.enumerate" and isinstance(value, list):
            names = [getattr(d, "name", str(d)) for d in value]
            return ", ".join(names) + "." if names else "Nothing enumerated."
        if capability == "user.sessions" and isinstance(value, list):
            return f"{len(value)}: {', '.join(map(str, value))}." if value \
                else "No other sessions."
        if isinstance(value, bool):
            return "Yes." if value else "No."
        if isinstance(value, list):
            return f"{len(value)} items."
        text = str(value).strip()
        return f"{text}." if text and not text.endswith(".") else (text or "Done.")

    def describe(self, limit: int = 3) -> str:
        """The corpus as text: what this machine can be asked, and how."""
        lines = []
        for capability in sorted(PHRASEBOOK):
            spec = CATALOGUE.get(capability)
            if spec is None:
                continue
            phrases = " · ".join(PHRASEBOOK[capability][:limit])
            mark = " [asks first]" if spec.effectful else ""
            lines.append(f"{capability}{mark}\n    {phrases}")
        return "\n".join(lines)
