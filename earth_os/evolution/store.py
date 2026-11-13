"""Where the System Evolution Center keeps what it remembers.

One directory, beside the other things this computer remembers::

    ~/.local/share/earth-os/evolution/
        roadmap.json      the proposals and where each one stands
        history.jsonl     every decision and every change, appended, never edited
        backups/          a copy of any file taken before an approved change
        reports/          reports a person asked to have exported

**This module and** :mod:`.changes` **are the only two under**
``earth_os/evolution/`` **that write a file**, and ``tests/test_evolution.py``
holds the package to that. Everything written here is the center's own
record. Nothing here can reach the rest of the machine.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..config import data_dir


def default_directory() -> Path:
    return data_dir() / "evolution"



def _tail_lines(path: Path, count: int, block: int = 65536) -> List[str]:
    """The last *count* lines of *path*, reading backwards in blocks.

    Reads only as much of the end of the file as holds that many lines. A
    final line with no newline (a write torn by a crash) is still returned,
    and the caller skips it as it skips any line that does not parse.
    """
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        end = fh.tell()
        data = b""
        while end > 0 and data.count(b"\n") <= count:
            start = max(0, end - block)
            fh.seek(start)
            data = fh.read(end - start) + data
            end = start
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-count:]

class Store:
    """The center's files. Every method either reads, or writes inside *root*."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else default_directory()
        #: Why the last write failed, if it did. A record that cannot be kept
        #: is said, not swallowed: the history is the audit trail.
        self.error = ""

    # -- places ------------------------------------------------------------------
    @property
    def roadmap_path(self) -> Path:
        return self.root / "roadmap.json"

    @property
    def history_path(self) -> Path:
        return self.root / "history.jsonl"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    def _inside(self, path: Path) -> Path:
        """*path*, refused unless it is under the center's own directory."""
        resolved = path.resolve()
        root = self.root.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"{path} is outside {self.root}")
        return resolved

    # -- the roadmap -------------------------------------------------------------
    def load_roadmap(self) -> Dict[str, object]:
        try:
            data = json.loads(self.roadmap_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            self.error = f"roadmap unreadable: {exc}"
            return {}
        return data if isinstance(data, dict) else {}

    def save_roadmap(self, data: Dict[str, object]) -> bool:
        """Write the roadmap whole, through a temporary file, so a crash
        half-way leaves the old one rather than half of the new one."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            target = self._inside(self.roadmap_path)
            scratch = target.with_suffix(".json.tmp")
            scratch.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
            os.replace(scratch, target)
        except (OSError, ValueError) as exc:
            self.error = f"roadmap not saved: {exc}"
            return False
        self.error = ""
        return True

    # -- the history -------------------------------------------------------------
    def append_history(self, event: Dict[str, object]) -> bool:
        """Add one line to the history. Lines are only ever added."""
        record = dict(event)
        record.setdefault("at", time.time())
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self._inside(self.history_path).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        except (OSError, ValueError, TypeError) as exc:
            self.error = f"history not written: {exc}"
            return False
        return True

    def read_history(self, limit: int = 0) -> List[Dict[str, object]]:
        """The history, oldest first; the last *limit* entries if given.

        With a *limit*, only the end of the file is read. The history is
        append-only and kept for good, so reading all of it to show the last
        two hundred lines got slower and hungrier every year: at 50,000
        lines it peaked at 18 MB and took 35 ms on each visit to HISTORY.
        """
        try:
            if limit > 0:
                lines = _tail_lines(self.history_path, limit)
            else:
                lines = self.history_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        events: List[Dict[str, object]] = []
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue                      # a torn line is skipped, not fatal
            if isinstance(event, dict):
                events.append(event)
        return events

    # -- backups -----------------------------------------------------------------
    def back_up(self, source: Path, label: str) -> Optional[Path]:
        """Copy *source* into ``backups/`` and return the copy, or None.

        None means there was nothing to copy (the file does not exist yet) —
        which is itself the thing to restore to. A copy that *fails* raises:
        the caller must not go on to change a file it could not preserve.
        """
        source = Path(source)
        if not source.exists():
            return None
        self.backups.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in label)[:48]
        target = self._inside(self.backups / f"{stamp}-{safe}-{source.name}")
        n = 1
        while target.exists():
            target = self._inside(self.backups / f"{stamp}-{safe}-{n}-{source.name}")
            n += 1
        shutil.copy2(source, target)
        return target

    # -- reports -----------------------------------------------------------------
    def write_report(self, name: str, text: str, extension: str) -> Path:
        self.reports.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:48]
        target = self._inside(self.reports / f"{safe}-{stamp}.{extension.lstrip('.')}")
        target.write_text(text, encoding="utf-8")
        return target

    def list_reports(self) -> Iterable[Path]:
        try:
            return sorted(self.reports.iterdir(), reverse=True)
        except OSError:
            return []
