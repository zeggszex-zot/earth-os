"""The verification gate: rung 2 of ``docs/SELF_MODIFICATION.md``, executable.

**This file is human-write-only** (see :mod:`.policy`). It is the arbiter; an
agent that could edit it could decide what counts as passing.

The document specified the gate as five steps. This is those steps::

    1. the diff touches nothing human-write-only       -> policy.PROTECTED
    2. the diff does not move the boundary              -> policy.BOUNDARY_PAIR
    3. the full suite passes under python3.12           -> Gate.test_command
    4. the suite did not shrink to get there            -> the recorded count
    5. git commit, with that output in the message      -> Gate._accept

If any step fails, the change is **set aside, not argued with**. "Set aside"
rather than "deleted": it goes to ``git stash`` with the reason as its name,
so a person can read what was attempted and keep it by hand if they disagree
with the suite. The suite still wins by default.

The gate judges everything that differs from the *base* — the commit the
agent's turn started from — so a change cannot slip through by being
committed mid-turn. Whatever the agent did, the gate rewinds to the base,
judges the whole of it, and makes the only commit.

Every screen is rendered at four resolutions by the suite on every run, which
is why a green suite means the interface still draws, not just that the logic
still holds.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import policy
from .workspace import Workspace, WorkspaceError

DEFAULT_PYTHON = "/usr/bin/python3.12"

#: Where the size of the last accepted suite is kept. Inside ``.git`` so it is
#: not part of any change the agent could make.
COUNT_FILE = "cdos-gate-tests"

_RAN = re.compile(r"^Ran (\d+) tests? in", re.MULTILINE)


@dataclass
class GateResult:
    #: "accepted", "rejected" or "nothing" (no change to judge)
    verdict: str
    reason: str
    changed: List[str] = field(default_factory=list)
    commit: str = ""
    stash: str = ""
    tests_run: int = 0
    output: str = ""
    seconds: float = 0.0

    @property
    def accepted(self) -> bool:
        return self.verdict == "accepted"

    def summary(self) -> str:
        if self.verdict == "nothing":
            return "No changes to verify."
        files = f"{len(self.changed)} file{'' if len(self.changed) == 1 else 's'}"
        if self.accepted:
            return (f"ACCEPTED · {files} · {self.tests_run} tests pass · "
                    f"commit {self.commit[:8]}")
        where = f" · set aside as {self.stash}" if self.stash else ""
        return f"REJECTED · {files} · {self.reason}{where}"


class Gate:
    def __init__(self, workspace: Workspace, python: str = DEFAULT_PYTHON,
                 timeout: float = 900.0):
        self.workspace = workspace
        self.python = python
        self.timeout = timeout

    # ------------------------------------------------------------------
    def test_command(self) -> List[str]:
        return [self.python, "-m", "unittest", "discover", "-s", "tests", "-q"]

    def _count_path(self) -> Path:
        return self.workspace.root / ".git" / COUNT_FILE

    def recorded_count(self) -> Optional[int]:
        try:
            return int(self._count_path().read_text().strip())
        except (OSError, ValueError):
            return None

    def _record_count(self, count: int) -> None:
        try:
            self._count_path().write_text(f"{count}\n")
        except OSError:
            pass

    # ------------------------------------------------------------------
    def run_suite(self):
        """Run the full suite; returns ``(ok, tests_run, output)``."""
        if not Path(self.python).exists():
            return (False, 0, f"cannot verify: {self.python} is not installed "
                              "(AGENTS.md: a green run on the wrong "
                              "interpreter is worse than a red one)")
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            proc = subprocess.run(
                self.test_command(), cwd=self.workspace.root,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=self.timeout, env=env)
        except subprocess.TimeoutExpired:
            return (False, 0, f"suite did not finish in {self.timeout:.0f}s")
        output = (proc.stdout or "") + (proc.stderr or "")
        match = _RAN.search(output)
        ran = int(match.group(1)) if match else 0
        ok = proc.returncode == 0 and ran > 0 and \
            re.search(r"^OK\b", output, re.MULTILINE) is not None
        return (ok, ran, output)

    # ------------------------------------------------------------------
    def run(self, base: str, message: str) -> GateResult:
        """Judge everything that differs from *base*, then keep or set aside."""
        started = time.monotonic()
        ws = self.workspace
        try:
            changed = ws.changed_since(base)
        except WorkspaceError as exc:
            return GateResult("rejected", f"cannot read the workspace: {exc}")
        if not changed:
            # Nothing on disk differs; if the agent committed, undo that too.
            if ws.head() != base:
                ws.git("reset", "--soft", base, check=True)
            return GateResult("nothing", "no changes")

        def finish(result: GateResult) -> GateResult:
            result.seconds = time.monotonic() - started
            return result

        protected = policy.protected_in(changed)
        if protected:
            return finish(self._reject(
                base, changed, "touches human-write-only "
                f"{', '.join(protected)}"))
        if policy.crosses_boundary(changed):
            return finish(self._reject(
                base, changed, "edits platform/ and capability_corpus.py "
                "together — that is a new capability, and a person grants those"))

        ok, ran, output = self.run_suite()
        tail = _tail(output)
        if not ok:
            reason = (_first_failure(output)
                      or (output.strip().splitlines()[0][:200]
                          if ran == 0 and output.strip() else "")
                      or "the suite failed")
            return finish(self._reject(base, changed, reason, output=tail,
                                       ran=ran))

        floor = self.recorded_count()
        if floor is not None and ran < floor:
            return finish(self._reject(
                base, changed, f"the suite shrank from {floor} to {ran} tests",
                output=tail, ran=ran))

        return finish(self._accept(base, changed, message, ran, tail))

    # ------------------------------------------------------------------
    def _accept(self, base: str, changed: List[str], message: str,
                ran: int, tail: str) -> GateResult:
        ws = self.workspace
        ws.git("reset", "--soft", base, check=True)
        ws.git("add", "-A", check=True)
        title = (message or "Claude Code change").strip().splitlines()[0][:72]
        body = "\n".join([
            title, "",
            "Verified by the CDOS gate (earth_os/agent/gate.py):",
            f"  {' '.join(self.test_command())}",
            f"  {ran} tests, OK",
            "", "Changed:", *[f"  {p}" for p in changed],
            "", "Suite output (tail):", *[f"  {line}" for line in tail.splitlines()],
            "", "Co-Authored-By: Claude Code (via CDOS) <noreply@anthropic.com>",
        ])
        ws.git("commit", "-q", "-m", body, check=True)
        self._record_count(ran)
        return GateResult("accepted", "suite passed", changed=changed,
                          commit=ws.head(), tests_run=ran, output=tail)

    def _reject(self, base: str, changed: List[str], reason: str,
                output: str = "", ran: int = 0) -> GateResult:
        ws = self.workspace
        if ws.head() != base:
            ws.git("reset", "--soft", base, check=True)
        label = f"cdos-gate rejected: {reason}"[:200]
        stashed = ws.git("stash", "push", "--include-untracked", "-m", label)
        stash = "stash@{0}" if stashed.returncode == 0 else ""
        if not stash:
            reason += f" (and could not be set aside: {stashed.stderr.strip()})"
        return GateResult("rejected", reason, changed=changed, stash=stash,
                          tests_run=ran, output=output)


def _tail(output: str, lines: int = 12) -> str:
    kept = [line for line in output.splitlines()
            if line.strip() and "ResourceWarning" not in line
            and "tracemalloc" not in line]
    return "\n".join(kept[-lines:])


def _first_failure(output: str) -> str:
    for line in output.splitlines():
        if line.startswith(("FAIL:", "ERROR:")):
            return line.strip()[:160]
    return ""
