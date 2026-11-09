"""The built-in agent as the shell sees it: one object, no toolkit.

Owns the workspace, the gate, the Claude Code session and the control bridge,
and turns what they do into lines a screen can draw. The CLAUDE CODE screen is
a view over this; the voice path reaches it through ``Shell.agent``. Keeping
the decisions here rather than in the screen is AGENTS.md §5: *logic that
belongs to the machine does not go in screens/*.

The life of one request::

    ask("make the header orange")
      -> refuse if the workspace has changes nobody has judged
      -> base = HEAD
      -> claude -p ... (edits, screenshots, tests, maybe a press or two)
      -> gate.run(base): protected? boundary? suite? shrank?
           accepted -> one commit, RELOAD INTERFACE lights up
           rejected -> git stash, with the reason as its name
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Deque, List, Optional, Tuple

from ..config import data_dir
from .bridge import ControlBridge
from .gate import DEFAULT_PYTHON, Gate, GateResult
from .session import ClaudeSession, TurnResult
from .workspace import PACKAGE_DIR, Workspace, WorkspaceError, locate, provision

#: Marks a commit the gate made, so UNDO only ever undoes the agent's work.
GATE_MARK = "Verified by the CDOS gate"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and os.path.isdir(base):
        path = Path(base) / "earth-os"
    else:
        path = Path(tempfile.gettempdir()) / f"earth-os-{os.getuid()}"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


class AgentController:
    def __init__(self, shell, *, state_dir: Optional[Path] = None,
                 socket_path: Optional[str] = None):
        self.shell = shell
        cfg = shell.config
        self.enabled = bool(cfg.get("claude", "enabled", True))
        self.command = str(cfg.get("claude", "command", "claude") or "claude")
        self.model = str(cfg.get("claude", "model", "") or "")
        self.python = str(cfg.get("claude", "python", DEFAULT_PYTHON) or DEFAULT_PYTHON)
        self.auto_verify = bool(cfg.get("claude", "auto_verify", True))
        self.state_dir = Path(state_dir) if state_dir else data_dir() / "claude"
        self.socket_path = socket_path or str(runtime_dir() / "agent.sock")

        self.lines: Deque[Tuple[str, str]] = deque(maxlen=4000)
        self._lock = threading.Lock()
        self.state = "OFFLINE"
        self.workspace: Optional[Workspace] = None
        self.gate: Optional[Gate] = None
        self.session: Optional[ClaudeSession] = None
        self.bridge: Optional[ControlBridge] = None
        self.base = ""
        self.last_request = ""
        self.last_gate: Optional[GateResult] = None
        #: An accepted change the running process has not loaded yet.
        self.reload_ready = False
        self.problem = ""

    # ------------------------------------------------------------------
    # Transcript
    # ------------------------------------------------------------------
    def emit(self, kind: str, text: str) -> None:
        with self._lock:
            for line in str(text).splitlines() or [""]:
                self.lines.append((kind, line))
        self.shell.redraw()

    def clear(self) -> None:
        with self._lock:
            self.lines.clear()

    def snapshot(self) -> List[Tuple[str, str]]:
        with self._lock:
            return list(self.lines)

    # ------------------------------------------------------------------
    # Bring-up
    # ------------------------------------------------------------------
    def ensure_ready(self, provision_ok: bool = False) -> bool:
        """Find the workspace and set up the session. Cheap and quiet unless
        *provision_ok*, which may create the workspace — only a real request
        does that, so opening the screen (or rendering it in a test) never
        copies a tree or opens a socket."""
        if not self.enabled:
            self.problem = "disabled in config ([claude] enabled = false)"
            self.state = "DISABLED"
            return False
        if self.session is not None:
            return True
        try:
            ws = locate(self.shell.config)
            if ws is None:
                if not provision_ok:
                    self.state = "NO WORKSPACE YET"
                    self.problem = ("the editable workspace is created on "
                                    "the first request")
                    return False
                self.emit("system", "Preparing an editable copy of CDOS "
                                    "for Claude Code…")
                ws = provision()
                self.emit("system", f"Workspace created at {ws.describe()}. "
                                    "The launcher runs from it from now on.")
            if not ws.is_repo:
                raise WorkspaceError(f"{ws.root} is not a git working tree")
            if not ws.has_tests:
                raise WorkspaceError(f"{ws.root} has no tests/ — nothing "
                                     "could verify a change")
        except WorkspaceError as exc:
            self.problem = str(exc)
            self.state = "NO WORKSPACE"
            self.emit("error", f"Workspace unavailable: {exc}")
            return False

        self.workspace = ws
        self.gate = Gate(ws, python=self.python)
        self.session = ClaudeSession(
            ws.root, command=self.command, model=self.model,
            state_dir=self.state_dir, socket_path=self.socket_path,
            running_from=PACKAGE_DIR.parent,
            on_event=self.emit, on_finished=self._turn_finished)
        if not self.session.available:
            self.state = "NOT INSTALLED"
            self.problem = f"'{self.command}' not found on PATH"
        else:
            self.state = "READY"
        return True

    def ensure_bridge(self) -> None:
        """Open the control channel the first time Claude Code will need it."""
        if self.bridge is not None:
            return
        schedule = getattr(self.shell, "call_on_main", None)
        self.bridge = ControlBridge(
            self.shell, workspace=self.workspace.root if self.workspace else None,
            schedule=schedule, shots_dir=runtime_dir() / "shots")
        try:
            self.bridge.start(self.socket_path)
        except OSError as exc:
            self.emit("error", f"Control channel unavailable: {exc}. "
                               "Claude Code can edit, but not drive the shell.")

    @property
    def runs_from_workspace(self) -> bool:
        """Will a restart load the workspace's code?"""
        if self.workspace is None:
            return False
        if self.workspace.root.resolve() == PACKAGE_DIR.parent.resolve():
            return True
        # The installed launcher prefers ~/.local/share/earth-os/src.
        from .workspace import user_tree
        return self.workspace.root.resolve() == user_tree().resolve()

    # ------------------------------------------------------------------
    # A request
    # ------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self.state in ("WORKING", "VERIFYING")

    def ask(self, prompt: str) -> bool:
        prompt = (prompt or "").strip()
        if not prompt:
            return False
        if not self.ensure_ready(provision_ok=True):
            self.emit("error", self.problem)
            return False
        if self.busy:
            self.emit("error", "Claude Code is still working. "
                               "Press INTERRUPT to stop it.")
            return False
        assert self.workspace is not None and self.session is not None
        try:
            if self.workspace.dirty():
                self.emit("error", "The workspace has changes nobody has "
                          "verified. Commit or stash them in "
                          f"{self.workspace.describe()} first — the gate "
                          "judges everything that differs, and it must not "
                          "set aside work that is not Claude's.")
                return False
            self.base = self.workspace.head()
        except WorkspaceError as exc:
            self.emit("error", str(exc))
            return False

        self.ensure_bridge()
        self.last_request = prompt
        self.emit("user", prompt)
        self.state = "WORKING"
        if not self.session.ask(prompt):
            self.state = "READY" if self.session.available else "NOT INSTALLED"
            return False
        self.shell.redraw()
        return True

    def interrupt(self) -> None:
        if self.session is not None and self.session.interrupt():
            self.emit("system", "Interrupting…")
        else:
            self.emit("system", "Nothing is running.")

    def new_conversation(self) -> None:
        if self.busy:
            self.emit("error", "Finish or interrupt the current request first.")
            return
        if self.session is not None:
            self.session.new_conversation()
        self.clear()
        self.emit("system", "New conversation. Claude Code starts fresh "
                            "(the workspace and its history are unchanged).")

    def _turn_finished(self, result: TurnResult) -> None:
        if result.ok:
            cost = f" · ${result.cost_usd:.3f}" if result.cost_usd else ""
            self.emit("system", f"— done in {result.turns} turn"
                                f"{'' if result.turns == 1 else 's'}{cost}")
        else:
            self.emit("error", f"— stopped: {result.error[:300]}")
            if "login" in result.error.lower() or "auth" in result.error.lower():
                self.emit("system", "Sign in once from a terminal: run "
                                    "'claude' and follow the prompt, or set "
                                    "ANTHROPIC_API_KEY for the session.")
        if self.auto_verify:
            self.verify()
        else:
            self.state = "READY"
            self.shell.redraw()

    # ------------------------------------------------------------------
    # The gate
    # ------------------------------------------------------------------
    def verify(self) -> None:
        """Judge the turn's changes on a worker; the frame clock keeps going."""
        if self.gate is None or not self.base:
            self.state = "READY"
            return
        self.state = "VERIFYING"
        self.shell.redraw()

        def work() -> None:
            try:
                result = self.gate.run(self.base, self.last_request)
            except Exception as exc:
                result = GateResult("rejected", f"gate error: {exc}")
            self.last_gate = result
            if result.verdict != "nothing":
                self.emit("gate-ok" if result.accepted else "gate-bad",
                          "GATE · " + result.summary())
                if not result.accepted and result.output:
                    for line in result.output.splitlines()[-6:]:
                        self.emit("gate-bad", "   " + line)
                if result.accepted:
                    self.reload_ready = True
                    where = ("" if self.runs_from_workspace else
                             f" (the shell is not running from "
                             f"{self.workspace.describe()}; see docs/CLAUDE_CODE.md)")
                    self.emit("system", "Press RELOAD INTERFACE to run the "
                                        f"new code.{where}")
                    self.shell.play("ack")
                else:
                    self.emit("system", "Nothing was kept. The attempt is in "
                                        "git stash if you want to read it.")
                    self.shell.play("beep")
            self.state = "READY"
            self.shell.redraw()

        threading.Thread(target=work, daemon=True, name="cdos-gate").start()

    # ------------------------------------------------------------------
    # Undo and reload (both need a press: they change what is running)
    # ------------------------------------------------------------------
    def last_gate_commit(self) -> str:
        if self.workspace is None:
            return ""
        out = self.workspace.git("log", "-1", "--pretty=%H%n%B").stdout
        head, _, body = out.partition("\n")
        return head.strip() if GATE_MARK in body else ""

    def offer_undo(self) -> None:
        shell = self.shell
        if not self.ensure_ready() or self.busy:
            self.emit("system", self.problem or "Busy.")
            return
        commit = self.last_gate_commit()
        if not commit:
            self.emit("system", "The latest commit was not made by the gate; "
                                "there is no Claude Code change to undo.")
            return
        title = self.workspace.git("log", "-1", "--pretty=%s").stdout.strip()
        shell.pending_prompt = f"CONFIRM · UNDO — {title}"[:200]
        shell.pending_options = [("CONFIRM", self._undo_confirmed),
                                 ("CANCEL", shell.cancel_pending)]
        shell.redraw()

    def _undo_confirmed(self) -> None:
        self.shell.pending_options = []
        self.shell.pending_prompt = ""
        result = self.workspace.git("revert", "--no-edit", "HEAD")
        if result.returncode != 0:
            self.emit("error", f"Undo failed: {result.stderr.strip()[:200]}")
            return
        # A revert of a verified commit lands on a tree the gate has already
        # passed, so it needs no second run — but it is still a commit, and
        # says what it is.
        self.emit("gate-ok", "Undone: " + self.workspace.log(1)[0])
        self.reload_ready = True
        self.emit("system", "Press RELOAD INTERFACE to run it.")
        self.shell.redraw()

    def offer_reload(self) -> None:
        shell = self.shell
        hook = getattr(shell, "restart_hook", None)
        if hook is None:
            self.emit("error", "This shell cannot restart itself here.")
            return
        shell.pending_prompt = "CONFIRM · RELOAD INTERFACE — restart into the workspace code"
        shell.pending_options = [("CONFIRM", self._reload_confirmed),
                                 ("CANCEL", shell.cancel_pending)]
        shell.redraw()

    def _reload_confirmed(self) -> None:
        self.shell.pending_options = []
        self.shell.pending_prompt = ""
        self.shutdown()
        self.shell.restart_hook()

    def history(self) -> None:
        if not self.ensure_ready():
            self.emit("system", f"No history: {self.problem}.")
            return
        self.emit("system", f"History of {self.workspace.describe()}:")
        for line in self.workspace.log(12):
            self.emit("system", "   " + line)
        stashes = self.workspace.git("stash", "list").stdout.splitlines()
        if stashes:
            self.emit("system", f"Set aside ({len(stashes)}):")
            for line in stashes[:6]:
                self.emit("gate-bad", "   " + line)

    def status_line(self) -> str:
        parts = [self.state]
        if self.workspace is not None:
            parts.insert(0, self.workspace.describe())
        if self.session is not None and self.session.session_id:
            parts.append(f"SESSION {self.session.session_id[:8]}")
        if self.session is not None and self.session.total_cost:
            parts.append(f"${self.session.total_cost:.2f}")
        if self.reload_ready:
            parts.append("RELOAD PENDING")
        return "  ·  ".join(parts)

    def shutdown(self) -> None:
        if self.session is not None:
            self.session.interrupt()
        if self.bridge is not None:
            self.bridge.stop()
