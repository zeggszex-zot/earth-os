"""Recently used programs, ranked and persisted.

The shell already kept a ``launched`` list, but only in memory and only as a
label — it vanished on restart and could not start anything again, which makes
it a log rather than a feature.  This is the durable version: what was opened,
how often, how recently, and the command needed to open it again.

Portability was a requirement from the start, so the design separates two
things that are easy to tangle:

* **The history itself is platform-neutral.**  It records what this shell
  launched, which works identically on any system the shell runs on.  No
  registry, no ``.desktop`` files, no platform APIs — a name and a command.
* **Where *extra* recents come from is platform-specific**, and lives behind
  :class:`RecentProvider`.  Linux has ``recently-used.xbel``; Windows has the
  Recent folder and jump lists; macOS has its own.  Each is a subclass that
  reports ``available`` honestly, and the store merges whatever answers.

That split means the screen works on day one everywhere the shell runs, and
gets better on any platform someone writes a provider for, without the screen
or the store changing at all.

Ranking is frecency rather than raw recency: something opened forty times last
week should not be buried by something opened once an hour ago.  See
:func:`RecentEntry.score`.
"""

from __future__ import annotations

import json
import os
import platform
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

SCHEMA_VERSION = 1

#: Half-life for the recency term, in seconds. A week: something used daily
#: stays near the top, something used once a month falls away without ever
#: quite disappearing.
HALF_LIFE = 7 * 24 * 3600.0


def default_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "earth-os" / "recent.json"


@dataclass
class RecentEntry:
    """One program, with enough to rank it and start it again."""

    name: str
    command: str
    count: int = 1
    last_used: float = field(default_factory=time.time)
    first_used: float = field(default_factory=time.time)
    source: str = "shell"          # shell | desktop | os
    pinned: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)

    def score(self, now: Optional[float] = None) -> float:
        """Frequency weighted by an exponential recency decay.

        Raw recency makes the list churn — whatever you touched last is always
        first, so the list tells you nothing you did not already know.  Raw
        frequency makes it stale.  Decaying the count by age gives a list that
        is mostly stable but still moves when your habits do.
        """
        now = time.time() if now is None else now
        age = max(now - self.last_used, 0.0)
        decay = 0.5 ** (age / HALF_LIFE)
        return (self.count ** 0.5) * (0.25 + 0.75 * decay)

    def age_label(self) -> str:
        seconds = max(time.time() - self.last_used, 0.0)
        if seconds < 90:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)}m ago"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h ago"
        days = int(seconds // 86400)
        return "yesterday" if days == 1 else f"{days}d ago"


# --------------------------------------------------------------------------
# Platform providers
# --------------------------------------------------------------------------

class RecentProvider:
    """Extra recents from whatever the host operating system tracks.

    Subclasses report ``available`` honestly and return an empty list rather
    than raising, because a provider that throws on an unexpected host would
    take the whole screen down with it.
    """

    name = "none"
    platforms: tuple = ()

    @property
    def available(self) -> bool:
        return platform.system() in self.platforms

    def entries(self, limit: int = 20) -> List[RecentEntry]:
        return []


class LinuxRecentProvider(RecentProvider):
    """Linux: the freedesktop recently-used list.

    This tracks recently used *documents* rather than programs, so what is
    harvested is the applications that opened them — which is a decent proxy
    for "programs you have been using" and costs nothing to read.
    """

    name = "freedesktop"
    platforms = ("Linux", "FreeBSD")

    def _xbel_path(self) -> Path:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        return Path(base) / "recently-used.xbel"

    @property
    def available(self) -> bool:
        return super().available and self._xbel_path().is_file()

    def entries(self, limit: int = 20) -> List[RecentEntry]:
        import xml.etree.ElementTree as ET
        path = self._xbel_path()
        found: Dict[str, RecentEntry] = {}
        try:
            root = ET.parse(str(path)).getroot()
        except (OSError, ET.ParseError):
            return []
        for bookmark in root.iter("bookmark"):
            modified = bookmark.get("modified") or ""
            stamp = _parse_iso(modified)
            for app in bookmark.iter("bookmark:application"):
                name = app.get("name") or ""
                exec_line = (app.get("exec") or "").strip("'\" ")
                if not name or not exec_line:
                    continue
                command = exec_line.split("%")[0].strip()
                if not command:
                    continue
                existing = found.get(name)
                if existing is None:
                    found[name] = RecentEntry(
                        name=name, command=command, count=1,
                        last_used=stamp, first_used=stamp, source="os")
                else:
                    existing.count += 1
                    existing.last_used = max(existing.last_used, stamp)
        ranked = sorted(found.values(), key=lambda e: e.score(), reverse=True)
        return ranked[:limit]


class WindowsRecentProvider(RecentProvider):
    """Windows: the Recent folder. Present so the shape is proven, not guessed."""

    name = "windows-recent"
    platforms = ("Windows",)

    def _recent_dir(self) -> Optional[Path]:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        path = Path(appdata) / "Microsoft" / "Windows" / "Recent"
        return path if path.is_dir() else None

    @property
    def available(self) -> bool:
        return super().available and self._recent_dir() is not None

    def entries(self, limit: int = 20) -> List[RecentEntry]:
        # Resolving .lnk shortcuts needs a Windows-only library, so this
        # reports nothing rather than guessing. The shell's own history still
        # populates the screen; this is the hook for someone with a Windows
        # box to finish.
        return []


class MacRecentProvider(RecentProvider):
    """macOS: the shared file list. Same reasoning as the Windows provider."""

    name = "macos"
    platforms = ("Darwin",)

    @property
    def available(self) -> bool:
        return False

    def entries(self, limit: int = 20) -> List[RecentEntry]:
        return []


ALL_PROVIDERS = (LinuxRecentProvider, WindowsRecentProvider, MacRecentProvider)


def active_provider() -> RecentProvider:
    """The provider for this host, or a neutral one that returns nothing."""
    for factory in ALL_PROVIDERS:
        try:
            provider = factory()
            if provider.available:
                return provider
        except Exception:
            continue
    return RecentProvider()


def _parse_iso(text: str) -> float:
    """Parse an xbel timestamp. Unparseable means "long ago", never a crash."""
    if not text:
        return 0.0
    cleaned = text.replace("Z", "+00:00")
    try:
        from datetime import datetime
        return datetime.fromisoformat(cleaned).timestamp()
    except (ValueError, OSError):
        return 0.0


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------

class RecentStore:
    """What this shell has launched, ranked and persisted."""

    def __init__(self, path: Optional[Path] = None, limit: int = 60):
        self.path = Path(path) if path else default_path()
        self.limit = int(limit)
        self.entries: Dict[str, RecentEntry] = {}
        #: The last thing launched, floated to the top of the list.
        #:
        #: Frecency alone makes the screen unhelpful in the one case you use
        #: it most: you start something, it turns out to be the wrong thing or
        #: you close it, you come back — and what you just launched is buried
        #: somewhere down the ranking because a single use cannot outweigh a
        #: habit. Remembering it explicitly keeps the ranking honest while
        #: putting the obvious candidate where the hand already is.
        self.last_command: str = ""
        self.provider = active_provider()
        self._lock = threading.RLock()
        self.last_error = ""

    @classmethod
    def load(cls, path: Optional[Path] = None, limit: int = 60) -> "RecentStore":
        store = cls(path, limit)
        store.reload()
        return store

    def reload(self) -> None:
        with self._lock:
            self.entries.clear()
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                return
            self.last_command = str(raw.get("last_command", "") or "")
            for item in raw.get("entries", []):
                try:
                    entry = RecentEntry(**item)
                except TypeError:
                    continue
                self.entries[entry.command] = entry

    def save(self) -> bool:
        with self._lock:
            payload = {
                "schema": SCHEMA_VERSION,
                "saved": time.time(),
                "last_command": self.last_command,
                "entries": [e.to_dict() for e in self.entries.values()],
            }
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                os.replace(tmp, self.path)
                return True
            except OSError as exc:
                self.last_error = str(exc)
                return False

    # ------------------------------------------------------------------
    def record(self, name: str, command: str) -> Optional[RecentEntry]:
        """Note that something was launched."""
        command = (command or "").strip()
        name = (name or command).strip()
        if not command:
            return None
        with self._lock:
            entry = self.entries.get(command)
            now = time.time()
            if entry is None:
                entry = RecentEntry(name=name, command=command,
                                    count=1, last_used=now, first_used=now)
                self.entries[command] = entry
            else:
                entry.count += 1
                entry.last_used = now
                if name:
                    entry.name = name
            self.last_command = command
            self._prune()
            return entry

    def _prune(self) -> None:
        """Keep the file bounded, dropping the weakest unpinned entries."""
        if len(self.entries) <= self.limit:
            return
        ranked = sorted(self.entries.values(),
                        key=lambda e: (e.pinned, e.score()), reverse=True)
        kept = {e.command: e for e in ranked[:self.limit]}
        # Never prune away the thing the screen is about to put first.
        if self.last_command and self.last_command not in kept:
            survivor = self.entries.get(self.last_command)
            if survivor is not None:
                kept[self.last_command] = survivor
        self.entries = kept

    def forget(self, command: str) -> bool:
        with self._lock:
            if self.last_command == command:
                self.last_command = ""
            return self.entries.pop(command, None) is not None

    def clear(self) -> None:
        with self._lock:
            self.entries = {c: e for c, e in self.entries.items() if e.pinned}
            if self.last_command not in self.entries:
                self.last_command = ""

    def toggle_pin(self, command: str) -> bool:
        with self._lock:
            entry = self.entries.get(command)
            if entry is None:
                return False
            entry.pinned = not entry.pinned
            return entry.pinned

    # ------------------------------------------------------------------
    def top(self, count: int = 12, include_os: bool = True) -> List[RecentEntry]:
        """The ranked list for the screen: pinned first, then by score.

        OS-provided entries are merged underneath the shell's own, and never
        displace something the user actually started from here.
        """
        with self._lock:
            merged: Dict[str, RecentEntry] = dict(self.entries)

        if include_os and self.provider.available:
            try:
                for entry in self.provider.entries(limit=count * 2):
                    merged.setdefault(entry.command, entry)
            except Exception:
                pass                    # a bad provider must not empty the screen

        ranked = sorted(merged.values(),
                        key=lambda e: (e.pinned, e.score()), reverse=True)

        # The most recent launch goes to the very top, above pins. A pin says
        # "always keep this handy"; having just launched something says "I am
        # dealing with this right now", which is the more immediate claim.
        if self.last_command:
            for i, entry in enumerate(ranked):
                if entry.command == self.last_command:
                    ranked.insert(0, ranked.pop(i))
                    break
        return ranked[:count]

    def describe(self) -> str:
        source = self.provider.name if self.provider.available else "shell only"
        return f"{len(self.entries)} tracked · source: {source}"
