"""Working state that survives a restart: the open calculation, so "and at half the load?" still works after the
computer was switched off, and a clarification asked before a power cut can still be answered.

Separate from knowledge on purpose.  The corpus is never written; this file holds only transient task state, and
deleting it forgets nothing the computer knows.

    {"version": 1, "saved": 1729650000.0, "analysis": {...}}

Writes are atomic (a temporary file in the same folder, fsync, then os.replace), so an interrupted write leaves the
previous state, never half a file.  A file that is unreadable, from another version, or older than `max_age` is
ignored.  The computer writes only when the state actually changed, to spare an SD card or SSD.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

VERSION = 1


def save(path: str | Path, state: dict) -> bool:
    """Atomically replace `path` with `state` (plus version and time).  False if it couldn't be written."""
    p = Path(path)
    body = json.dumps({"version": VERSION, "saved": time.time(), **state}, separators=(",", ":"))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", dir=str(p.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False


def load(path: str | Path, max_age: float | None = None) -> dict | None:
    """The saved state, or None if there's none usable (missing, corrupt, another version, or too old)."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("version") != VERSION:
        return None
    if max_age is not None and time.time() - float(d.get("saved") or 0) > max_age:
        return None
    return d
