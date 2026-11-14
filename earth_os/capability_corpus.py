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
from typing import Dict, List, Optional, Tuple

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


@dataclass(frozen=True)
class Analysis:
    """A question answered by looking at readings, not by taking a new one.

    *"What is using the most memory?"* names no capability.  ``process.list``
    already reports how much memory every program holds; the question asks
    for that reading sorted, grouped and put into words.  Sorting is meaning,
    not mechanism, so it happens up here, exactly as the arithmetic in
    :class:`Adjustment` does — and the machine gains no new power by it.

    Three rules keep that true, and ``tests/test_corpus.py`` checks each:

    1. every read names a capability in the catalogue;
    2. every read is a parameterless **reading** — never effectful — so an
       analysis can look at the machine but never touch it;
    3. the shell runs one through ``Shell.run_analysis``, which re-checks
       rule 2 at the moment of running rather than trusting this table.
    """

    name: str
    reads: Tuple[str, ...]
    summary: str
    #: True for a diagnosis: an answer made of several findings, which Earth
    #: Mind may ask the Companion to explain (docs/EARTH_MIND.md). It changes
    #: nothing about what the analysis may do — it still only reads — and the
    #: findings are fixed here, before any model sees the question.
    explained: bool = False


#: Questions that are answered by reading, then thinking.
ANALYSES: Dict[str, Analysis] = {
    "top_memory": Analysis(
        "top_memory", ("process.list",),
        "which programs are holding the most memory"),
    "memory_report": Analysis(
        "memory_report", ("hardware.memory", "hardware.swap", "process.list"),
        "where the memory is going, and to whom"),
    "performance_report": Analysis(
        "performance_report",
        ("hardware.cpu_load", "hardware.load_average", "hardware.cpu_count",
         "hardware.memory", "hardware.swap", "hardware.disk", "process.list"),
        "what, if anything, is slowing this machine down", explained=True),
}

#: How a person asks each analysis.  Matched before the phrasebook, because
#: every one of these contains a shorter phrasebook entry — "memory" alone
#: is ``hardware.memory`` — and answering "1.6 GB in use" to "what is using
#: the most memory" is answering a different question.
ANALYSIS_PHRASES: Dict[str, Tuple[str, ...]] = {
    "top_memory": (
        "using the most memory", "using the most ram",
        "uses the most memory", "uses the most ram",
        "use the most memory", "use the most ram",
        "using my memory", "using my ram",
        "using all my memory", "using all my ram",
        "using all the memory", "using all the ram",
        "eating my memory", "eating my ram",
        "eating all my memory", "eating all my ram",
        "taking up my memory", "taking up my ram",
        "taking up all my memory", "taking up all my ram",
        "memory hog", "memory hogs", "ram hog", "ram hogs",
        "top memory users", "biggest memory users",
        "top memory", "top ram",
    ),
    "memory_report": (
        "memory report", "ram report", "memory breakdown", "ram breakdown",
        "where is my memory going", "where is my ram going",
        "where did my memory go", "where did my ram go",
        "memory so high", "ram so high",
        "memory usage so high", "ram usage so high",
        "memory usage high", "ram usage high",
        "high memory usage", "high ram usage",
        "using so much memory", "using so much ram",
        "why is memory high", "why is ram high",
        "why is my memory high", "why is my ram high",
        "idle memory", "idle ram", "memory at idle", "ram at idle",
    ),
    # "Why is my computer running slowly?" Every phrase here is about the
    # machine being slow; "slow" alone is not one, because a slow day is not
    # a diagnosis.
    "performance_report": (
        "running slowly", "running slow", "running so slowly", "running so slow",
        "running very slowly", "running very slow",
        "computer is slow", "computer so slow", "computer is so slow",
        "computer slow", "computer feels slow",
        "machine is slow", "machine so slow", "system is slow", "system so slow",
        "laptop is slow", "pc is slow", "everything is slow", "everything so slow",
        "slowing down my computer", "slowing my computer", "slowing the computer",
        "slowing this computer", "slowing it down", "slowing things down",
        "what is slowing", "why is it slow", "why so slow",
        "sluggish", "lagging", "laggy",
        "performance report", "performance check", "performance problem",
        "slowdown report",
    ),
}

#: Programs that run *other* programs.  Grouping by the first word of the
#: command line would put every Python program on the machine into one row
#: called "python3", so for these the script or module being run is the name.
_INTERPRETER = re.compile(
    r"(?:python|pypy)\d*(?:\.\d+)?|node(?:js)?|bun|deno|java|perl|ruby|php"
    r"|bash|sh|dash|zsh|env|uv|uvx")


def _program(process: object) -> str:
    """The program a process belongs to, in words a person would recognise.

    A browser is a dozen processes with names like "Isolated Web Co"; the
    command that started each of them still says which browser it was.
    """
    words = str(getattr(process, "command", "") or "").split()
    fallback = str(getattr(process, "name", "") or "").strip()
    if not words:
        return fallback or "unknown"
    head = words[0].rsplit("/", 1)[-1] or fallback
    if not _INTERPRETER.fullmatch(head):
        return head or fallback or "unknown"
    rest = iter(words[1:])
    for word in rest:
        if word == "-m":
            module = next(rest, "")
            return module.split(".", 1)[0] or head
        if word == "-c":
            return head
        if word.startswith("-") or "=" in word:
            continue
        script = word.rsplit("/", 1)[-1]
        return re.sub(r"\.(?:py|js|mjs|cjs|pl|rb|php|sh|jar)$", "", script) or head
    return head


@dataclass
class MemoryRanking:
    """Programs ordered by the memory their processes hold together.

    Figures are resident memory summed over each program's processes.
    Libraries shared between processes are counted in each of them, so a
    program made of many processes reads somewhat higher than it costs —
    which is why a reply says "about".
    """

    rows: list                     # [(program, bytes, process_count)]
    measured: int                  # processes with a memory figure

    def top(self, count: int) -> list:
        return self.rows[:count]


def rank_memory(processes: object) -> Optional[MemoryRanking]:
    """Group a ``process.list`` reading by program, biggest first.

    None when there is nothing to rank — a machine whose process list carries
    no memory figures has not said which program is biggest, and naming one
    anyway would be inventing a reading.
    """
    if not isinstance(processes, (list, tuple)):
        return None
    totals: Dict[str, list] = {}
    measured = 0
    for process in processes:
        try:
            size = int(getattr(process, "memory_bytes", 0) or 0)
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        measured += 1
        row = totals.setdefault(_program(process), [0, 0])
        row[0] += size
        row[1] += 1
    if not totals:
        return None
    rows = sorted(((name, size, count) for name, (size, count) in totals.items()),
                  key=lambda row: (-row[1], row[0]))
    return MemoryRanking(rows=rows, measured=measured)

def rank_processor(processes: object) -> Optional[Tuple[str, float]]:
    """The program using the most processor time, and how much, or None.

    The figure is the process list's own percentage, summed over a program's
    processes, so it is "percent of one core". None when the list carries no
    processor figures: naming a busiest program then would be inventing one.
    """
    if not isinstance(processes, (list, tuple)):
        return None
    totals: Dict[str, float] = {}
    for process in processes:
        try:
            share = float(getattr(process, "cpu_percent", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if share <= 0:
            continue
        name = _program(process)
        totals[name] = totals.get(name, 0.0) + share
    if not totals:
        return None
    name = min(totals, key=lambda n: (-totals[n], n))
    return name, totals[name]


def _listed(items: list) -> str:
    """["a", "b", "c"] → "a, b and c"."""
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


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


#: The readings a sentence may reach by meaning rather than by its exact
#: phrase. All of ``READINGS`` but the screenshot, which writes a file: it
#: touches nothing that matters, but it does leave something behind, and a
#: looser match should only ever look.
MEANING_READINGS: frozenset = READINGS - {"display.screenshot"}

#: "not", "never", "cannot", and every "n't" (which ``normalise`` leaves as a
#: separate "t").
_NEGATED = re.compile(r"(?:^|\s)(?:not|never|cannot|dont|\w+n t)(?:\s|$)")


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
    #: Set when this is a question about readings rather than one reading:
    #: the name of an entry in :data:`ANALYSES`, run by ``Shell.run_analysis``.
    analysis: str = ""
    #: "phrase" when a phrasing was found as written; "lexical" when the
    #: sentence was read by meaning (``earth_os/lexical.py``). A lexical
    #: reading is only ever a reading or an analysis, never an act.
    via: str = "phrase"
    #: For a lexical reading, the phrasebook phrase it was read as meaning.
    like: str = ""

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

    def __init__(self, registry=None, lexical: bool = True):
        self.registry = registry
        self.lexical = lexical
        self._ordered = sorted(
            ((phrase, capability)
             for capability, phrases in PHRASEBOOK.items()
             for phrase in phrases),
            key=lambda pair: len(pair[0]), reverse=True)
        self._adjustments = sorted(ADJUSTMENTS.items(),
                                   key=lambda pair: len(pair[0]), reverse=True)
        self._analyses = sorted(
            ((phrase, name)
             for name, phrases in ANALYSIS_PHRASES.items()
             for phrase in phrases),
            key=lambda pair: len(pair[0]), reverse=True)
        self._lexicon = None
        self._meanings: List[Tuple[frozenset, str, str]] = []
        self.ambiguous: Dict[frozenset, Tuple[str, ...]] = {}
        if lexical:
            self._build_meanings()

    # ------------------------------------------------------------------
    def _build_meanings(self) -> None:
        """Every phrase that may be read by meaning, as the concepts it needs.

        Only questions qualify: analyses, and parameterless readings. An act
        is reached only by a phrase as written, because a reading by meaning
        is a looser match, and a looser match must not be able to do things.
        A set of concepts that two different questions both reduce to means
        nothing on its own, so it is left out and recorded in ``ambiguous``
        — the exact phrases still work, and the suite reports the collision.
        """
        from .lexical import MACHINE, Lexicon

        words = [p for phrases in PHRASEBOOK.values() for p in phrases]
        words += [p for phrases in ANALYSIS_PHRASES.values() for p in phrases]
        self._lexicon = Lexicon(words)
        seen: Dict[frozenset, Dict[str, str]] = {}
        for phrase, name in self._analyses:
            seen.setdefault(self._lexicon.key(phrase), {}).setdefault(name, phrase)
        for phrase, capability in self._ordered:
            if capability not in MEANING_READINGS:
                continue
            spec = CATALOGUE.get(capability)
            if spec is None or spec.params or spec.effectful:
                continue
            seen.setdefault(self._lexicon.key(phrase), {}).setdefault(capability, phrase)
        meanings = []
        for key, targets in seen.items():
            if not key & MACHINE:
                continue
            if len(targets) > 1:
                self.ambiguous[key] = tuple(sorted(targets))
                continue
            (target, phrase), = targets.items()
            meanings.append((key, target, phrase))
        # Most concepts first: the more a phrase requires, the more specific
        # it is. Analyses before readings at equal size, as with phrases.
        meanings.sort(key=lambda m: (-len(m[0]), m[1] not in ANALYSES, m[1]))
        self._meanings = meanings

    def resolve(self, text: str) -> Optional[CapabilityIntent]:
        """The best reading of *text*, or None if it names nothing.

        Phrases as written are tried first, exactly as before. Then the
        sentence is read by meaning, and that reading wins only if it
        accounts for *more* of what was said than the phrase did — "which app
        is eating the most memory" is a question about programs, not the
        bare "memory" a phrase found in it.
        """
        cleaned = normalise(text)
        if not cleaned:
            return None
        adjusted = self._as_adjustment(cleaned) or self._as_percentage(cleaned)
        if adjusted is not None:
            return None if self._forbidden(cleaned, adjusted) else adjusted
        found = self._as_analysis(cleaned) or self._as_phrase(cleaned)
        if found is not None and self._forbidden(cleaned, found):
            found = None
        meant = self._as_meaning(cleaned)
        if meant is None:
            return found
        if found is None:
            # A phrase for an act that needs an argument ("forget the wifi
            # network") is left to the shell's vocabulary, which knows names
            # and paths. It was still said, so meaning must not reread the
            # sentence as a question about something else.
            named = self._named_act(cleaned)
            if named is not None and not self._refines(meant, named):
                return None
            return meant
        return meant if self._refines(meant, found) else found

    def _named_act(self, cleaned: str) -> Optional[CapabilityIntent]:
        """The longest act phrase in *cleaned*, including ones needing arguments."""
        for phrase, capability in self._ordered:
            if capability not in MEANING_READINGS and self._mentions(cleaned, phrase):
                return CapabilityIntent(capability=capability, phrase=phrase)
        return None

    def _refines(self, meant: CapabilityIntent, found: CapabilityIntent) -> bool:
        """Whether a reading by meaning should replace a phrase found as written.

        Only if it says everything the phrase said and more. "Which app is
        eating the most memory" found "memory"; read by meaning it is
        {use, most, memory}, which contains it — a refinement. "What's the
        cpu temperature" found "temperature"; by meaning it also fits "what
        cpu", which does not contain temperature — a contradiction, so the
        phrase stands. Meaning may sharpen what was found, never overrule it.
        """
        have = self._lexicon.key(found.phrase)
        want = self._lexicon.key(meant.like)
        return have < want

    def _forbidden(self, cleaned: str, intent: CapabilityIntent) -> bool:
        """A sentence that says *not* to do something never does it.

        "Don't lock the screen" contains "lock the screen", and until this
        check it locked the screen. A screenshot counts as an act here: it
        leaves a file behind. Questions are still answered — "why
        isn't the wifi connecting" is a fair thing to read the wifi for —
        but an act is refused, and the sentence goes on to be talked about.
        """
        if intent.analysis or intent.capability in MEANING_READINGS:
            return False
        if not _NEGATED.search(cleaned):
            return False
        return True

    def _as_meaning(self, cleaned: str) -> Optional[CapabilityIntent]:
        """The sentence read as concepts, against every question's concepts."""
        if self._lexicon is None or not self._meanings:
            return None
        reading = self._lexicon.read(cleaned)
        if reading.negated:
            return None
        have = reading.concepts
        for key, target, phrase in self._meanings:
            if not key <= have:
                continue
            renamed = [f"{said} as {meant}" for said, meant in reading.renamed()
                       if meant in key]
            span = reading.span(key) or phrase
            note = f"read by meaning, like {phrase!r}"
            if renamed:
                note += "; " + ", ".join(renamed)
            if target in ANALYSES:
                analysis = ANALYSES[target]
                return CapabilityIntent(capability=analysis.reads[-1],
                                        phrase=span, explanation=note,
                                        analysis=target, via="lexical",
                                        like=phrase)
            return CapabilityIntent(capability=target, phrase=span,
                                    explanation=note, via="lexical", like=phrase)
        return None

    def resolve_analysis(self, text: str) -> Optional[CapabilityIntent]:
        """Only the analysis reading of *text*, or None.

        For the terminal, which must not have its English turned into
        capabilities wholesale — it has its own translator for that — but
        should give the same answer as the voice to the same question.
        """
        cleaned = normalise(text)
        if not cleaned:
            return None
        found = self._as_analysis(cleaned)
        meant = self._as_meaning(cleaned)
        if meant is not None and meant.analysis:
            if found is None or self._refines(meant, found):
                return meant
        return found

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

    def _as_analysis(self, cleaned: str) -> Optional[CapabilityIntent]:
        """"What is using the most memory" — a question about a reading."""
        for phrase, name in self._analyses:
            if self._mentions(cleaned, phrase):
                analysis = ANALYSES[name]
                return CapabilityIntent(
                    capability=analysis.reads[-1], phrase=phrase,
                    explanation=analysis.summary, analysis=name)
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

    def explain(self, analysis: str, values: Dict[str, object],
                table: bool = False) -> Tuple[bool, str]:
        """Answer an analysis from its readings.

        Returns ``(ok, text)``.  ``ok`` is False when the readings came back
        but do not answer the question — a process list with no memory
        figures in it — and the text then says so rather than guessing.
        ``table`` lays the ranking out in columns, for the terminal; without
        it the answer is a sentence that can be spoken.
        """
        if analysis == "performance_report":
            return self._explain_performance(values, table)

        ranking = rank_memory(values.get("process.list"))
        if ranking is None:
            return False, ("This machine is not reporting how much memory "
                           "each program holds, so I cannot say which is "
                           "using the most.")

        lines = []
        if analysis == "memory_report":
            memory = values.get("hardware.memory")
            if isinstance(memory, (tuple, list)) and len(memory) == 2 \
                    and int(memory[0] or 0) > 0:
                total, available = int(memory[0]), int(memory[1])
                used = max(total - available, 0)
                lines.append(f"{_bytes(used)} of {_bytes(total)} in use "
                             f"({used / total:.0%}), {_bytes(available)} "
                             f"available.")
            swap = values.get("hardware.swap")
            if isinstance(swap, (tuple, list)) and len(swap) == 2:
                swap_total, swap_free = int(swap[0] or 0), int(swap[1] or 0)
                swapped = max(swap_total - swap_free, 0)
                if not swap_total:
                    lines.append("No swap configured.")
                elif swapped:
                    lines.append(f"{_bytes(swapped)} pushed out to swap.")
                else:
                    lines.append("Nothing in swap.")

        if table:
            count = 12
            lines.append("")
            lines.append(f"{'MEMORY':>10}  {'PROCS':>5}  PROGRAM")
            for name, size, procs in ranking.top(count):
                lines.append(f"{_bytes(size):>10}  {procs:>5}  {name}")
            others = ranking.rows[count:]
            if others:
                rest = sum(row[1] for row in others)
                lines.append(f"{_bytes(rest):>10}  {sum(r[2] for r in others):>5}"
                             f"  ({len(others)} other programs)")
            lines.append("")
            lines.append("Each program's processes are added together. Shared "
                         "libraries count once per process, so programs with "
                         "many processes read a little high.")
            return True, "\n".join(lines).strip("\n")

        top = ranking.top(5)
        first, size, procs = top[0]
        spread = f" across {procs} processes" if procs > 1 else ""
        if analysis == "memory_report":
            lines.append(f"Biggest is {first}, about {_bytes(size)}{spread}.")
        else:
            lines.append(f"{first} is using the most memory, about "
                         f"{_bytes(size)}{spread}.")
        if len(top) > 1:
            lines.append("Then " + ", ".join(
                f"{name} {_bytes(held)}" for name, held, _n in top[1:]) + ".")
        if analysis == "memory_report":
            together = sum(row[1] for row in top)
            lines.append(f"Those {len(top)} hold about {_bytes(together)} "
                         f"between them.")
        return True, " ".join(lines)

    def _explain_performance(self, values: Dict[str, object],
                             table: bool = False) -> Tuple[bool, str]:
        """"Why is my computer running slowly?" — from readings, as findings.

        The verdict comes first and every finding after it is one sentence
        with the figure it rests on, so the answer can be said as it stands
        and, when a model is asked to explain it, checked figure by figure.
        A reading that did not come back is left out rather than assumed
        normal, and if none came back the answer is that nothing could be
        measured.
        """
        findings: List[str] = []
        causes: List[str] = []
        measured: List[str] = []

        cores = values.get("hardware.cpu_count")
        cores = int(cores) if isinstance(cores, (int, float)) and cores else 0
        load = values.get("hardware.cpu_load")
        average = values.get("hardware.load_average")
        one = None
        if isinstance(average, (tuple, list)) and average:
            try:
                one = float(average[0])
            except (TypeError, ValueError):
                one = None
        if isinstance(load, (int, float)):
            measured.append("processor")
            sentence = f"The processor is at {float(load):.0%}"
            if one is not None and cores:
                sentence += (f", with a load average of {one:.2f} across "
                             f"{cores} core{'s' if cores != 1 else ''}")
            findings.append(sentence + ".")
            per_core = one / cores if one is not None and cores else 0.0
            if float(load) >= 0.85 or per_core >= 1.5:
                causes.append("the processor is saturated")
            elif float(load) >= 0.60 or per_core >= 1.0:
                causes.append("the processor is busy")

        processes = values.get("process.list")
        busiest = rank_processor(processes)
        if busiest is not None and busiest[1] >= 25.0:
            findings.append(f"{busiest[0]} is using the most processor time, "
                            f"about {busiest[1]:.0f} percent of one core.")

        memory = values.get("hardware.memory")
        fraction = None
        if isinstance(memory, (tuple, list)) and len(memory) == 2 \
                and int(memory[0] or 0) > 0:
            measured.append("memory")
            total, available = int(memory[0]), int(memory[1])
            used = max(total - available, 0)
            fraction = used / total
            findings.append(f"Memory is {fraction:.0%} full: {_bytes(used)} of "
                            f"{_bytes(total)} in use, {_bytes(available)} available.")
            if fraction >= 0.90:
                causes.append("memory is nearly full")
            elif fraction >= 0.80:
                causes.append("memory is under pressure")
        ranking = rank_memory(processes)
        if ranking is not None and ranking.rows:
            name, size, _procs = ranking.rows[0]
            findings.append(f"{name} holds the most memory, about {_bytes(size)}.")

        swap = values.get("hardware.swap")
        if isinstance(swap, (tuple, list)) and len(swap) == 2:
            measured.append("swap")
            swap_total, swap_free = int(swap[0] or 0), int(swap[1] or 0)
            swapped = max(swap_total - swap_free, 0)
            if swap_total and swapped >= 256 * 1024 * 1024 \
                    and swapped / swap_total >= 0.25:
                findings.append(f"{_bytes(swapped)} has been pushed out to swap, "
                                f"and reading it back is slow.")
                causes.append("the machine is swapping")
            elif not swap_total and fraction is not None and fraction >= 0.90:
                findings.append("No swap is configured, so there is nowhere "
                                "for the overflow to go.")

        disk = values.get("hardware.disk")
        if isinstance(disk, (tuple, list)) and len(disk) == 2 \
                and int(disk[0] or 0) > 0:
            measured.append("storage")
            disk_total, disk_free = int(disk[0]), int(disk[1])
            full = max(disk_total - disk_free, 0) / disk_total
            findings.append(f"Storage is {full:.0%} full, {_bytes(disk_free)} free.")
            if full >= 0.95:
                causes.append("storage is nearly full")

        if not measured:
            return False, ("This machine is not reporting its processor, memory "
                           "or storage readings, so I cannot say what is "
                           "slowing it down.")
        if causes:
            verdict = "Likely cause: " + _listed(causes) + "."
        else:
            verdict = ("Nothing I can measure explains a slowdown: "
                       + _listed(measured) + " all read normal."
                       if len(measured) > 1 else
                       f"Nothing I can measure explains a slowdown: {measured[0]} reads normal.")
        lines = [verdict] + findings
        if not table:
            return True, " ".join(lines)

        if ranking is not None and ranking.rows:
            lines.append("")
            lines.append(f"{'MEMORY':>10}  {'PROCS':>5}  PROGRAM")
            for name, size, procs in ranking.top(6):
                lines.append(f"{_bytes(size):>10}  {procs:>5}  {name}")
        return True, "\n".join(lines)

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
