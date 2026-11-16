"""A generation swap, staged (Q2): the candidate corpus a source's run would make, written beside the run, never
over the master.

    candidate.pf = the master without the rows of the generation the source replaces (registry `replaces`), except
                   those of things whose new rows the run held back (held.pf)
                   + the run's delta.pf

The master is read under the corpus guard's shared lock when it is guarded, and only read.  Making the candidate the
master is integration, which is off (Q1); when it is on, it goes through CorpusGuard.writing with verify.
"""

from __future__ import annotations

import contextlib
import os
from collections import Counter
from pathlib import Path

from computer.corpus_guard import CorpusGuard, GuardError
from computer.governor import sha256_file

from . import AcquireError
from .index import replaced_by


def candidate_lines(master: Path, runs: list[tuple[Path, dict | None]], counts: dict):
    """The lines of the corpus several staged runs would make together: the master without each replaced generation
    (except the rows of things a run held back), then each run's delta.pf in order.  `counts` gets kept, removed and
    added.  One definition for the candidate check (tools/candidate_check.py) and integration (tools/integrate.py),
    so what is checked is what is integrated."""
    skips = [replaced_by(rep, delta.parent / "held.pf") for delta, rep in runs if rep]
    counts.update(kept=0, removed=0, added=0)
    with open(master, encoding="utf-8") as m:
        for line in m:
            c = (line.rstrip("\n").split("\t") + [""] * 11)[:11]
            if line.strip() and not line.startswith("#") and any(s(c) for s in skips):
                counts["removed"] += 1
                continue
            yield line if line.endswith("\n") else line + "\n"
            counts["kept"] += 1
    for delta, _ in runs:
        with open(delta, encoding="utf-8") as d:
            for line in d:
                if line.strip() and not line.startswith("#"):
                    yield line if line.endswith("\n") else line + "\n"
                    counts["added"] += 1


def write_candidate(master: Path, delta: Path, replaces: dict, out: Path) -> dict:
    """Write candidate.pf; return what changed: rows kept, and rows removed and added by relation."""
    if not replaces:
        raise AcquireError("this source replaces no generation; there is no swap to preview")
    skip = replaced_by(replaces, delta.parent / "held.pf")
    removed, added, kept = Counter(), Counter(), 0
    tmp = out.with_name(f".{out.name}.part")
    guard = CorpusGuard(master)
    try:
        with (guard.reading() if guard.guarded else contextlib.nullcontext()):
            master_sha = sha256_file(master)
            with open(master, encoding="utf-8") as m, open(tmp, "w", encoding="utf-8") as fh:
                for line in m:
                    line = line.rstrip("\n")
                    if not line.strip() or line.startswith("#"):
                        fh.write(line + "\n")
                        continue
                    c = (line.split("\t") + [""] * 11)[:11]
                    if skip(c):
                        removed[c[1]] += 1
                        continue
                    fh.write(line + "\n")
                    kept += 1
                with open(delta, encoding="utf-8") as d:
                    for line in d:
                        line = line.rstrip("\n")
                        if not line.strip() or line.startswith("#"):
                            continue
                        added[line.split("\t")[1]] += 1
                        fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            if sha256_file(master) != master_sha:
                raise AcquireError("the master changed while the candidate was being written")
    except GuardError as e:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise AcquireError(f"the master can't be read for the candidate: {e}") from None
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    os.replace(tmp, out)
    return {"master_sha256": master_sha, "kept": kept, "removed": dict(sorted(removed.items())),
            "added": dict(sorted(added.items())), "removed_total": sum(removed.values()),
            "added_total": sum(added.values()), "candidate_sha256": sha256_file(out)}
