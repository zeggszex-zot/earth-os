"""Running Claude Code headless, and reading what it does as it does it.

One ``claude -p`` process per request, resumed by session id so the
conversation carries on across requests — and across a restart of the shell,
which is exactly what happens after an accepted change to the interface.

The process runs in the workspace, under the settings :mod:`.policy` writes,
with the CDOS MCP server attached and the permission prompt pointed at it.
Nothing in this module decides what is allowed; it only starts the program
with the rules already in place and reports what comes back.

Output is ``stream-json``: one event per line, read on a worker thread and
handed to callbacks. The screen queues them for its frame clock, the same way
the terminal streams command output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import policy

#: kind, text
EventSink = Callable[[str, str], None]


@dataclass
class TurnResult:
    ok: bool
    text: str = ""
    cost_usd: float = 0.0
    turns: int = 0
    session_id: str = ""
    error: str = ""
    raw: Dict[str, object] = field(default_factory=dict)


def system_prompt(workspace: Path, running_from: Path) -> str:
    """What the agent is told about where it is. Short: the rules are in the
    tree it is working on, and it is told to read them rather than given a
    second copy that could drift."""
    same = workspace.resolve() == running_from.resolve()
    return f"""\
You are Claude Code, built into EARTH OS — a green-phosphor desktop shell (LCARS-style layout).
You are talking to the person at this computer through the CLAUDE CODE screen.
Your replies are shown in an LCARS panel: keep them short and plain.

WORKSPACE: {workspace}
This is the CDOS source. {"The shell is running from this same tree." if same else f"The shell is running from {running_from}; it restarts into the workspace after a change is accepted."}
Read AGENTS.md before changing anything. Its rules are enforced by tests.

HOW YOUR CHANGES ARE KEPT
- After each of your turns, the CDOS gate judges everything you changed:
  it runs the full suite with /usr/bin/python3 (which renders every screen
  at four resolutions). Green -> one commit is made. Red -> the change is set
  aside with git stash. Do not commit, stash or reset yourself; you cannot.
- These files are human-write-only and any change touching them is rejected:
  {", ".join(policy.PROTECTED)}.
- Never edit earth_os/platform/ and earth_os/capability_corpus.py together.
- Run the suite yourself before finishing:
  /usr/bin/python3 -m unittest discover -s tests -q
- An accepted change takes effect when the person presses RELOAD INTERFACE.

SEEING THE INTERFACE
- mcp__cdos__screenshot renders the RUNNING shell to a PNG; Read the path.
- To see your EDITED code, render it headlessly and Read the PNG:
  /usr/bin/python3 tools/screenshot.py --only main --size 1600x1000 --out /tmp/cdos-claude-shots

CONTROLLING THE SHELL (mcp__cdos__*)
- status, screens, go, say, palettes, set_palette, capabilities, invoke.
- invoke runs a catalogue capability through the shell's gated path. An
  effectful one waits for the person to press CONFIRM; you get the outcome.
- The catalogue is closed. You cannot add machine power; if something needs a
  new capability, draft it and say so — a person grants those.
- Any shell command not on the allow-list is shown to the person on screen
  and waits for ALLOW or DENY.
"""


def describe_tool(name: str, tool_input: Dict[str, object], root: Path) -> str:
    """One line for the transcript, naming what a tool call touches."""
    def rel(value: object) -> str:
        text = str(value or "")
        return policy.normalise(text, root) or text

    if name in ("Read", "Edit", "MultiEdit", "Write", "NotebookEdit"):
        return f"{name.upper()} {rel(tool_input.get('file_path') or tool_input.get('notebook_path'))}"
    if name == "Bash":
        return f"RUN {str(tool_input.get('command', '')).strip()}"
    if name in ("Grep", "Glob"):
        return f"{name.upper()} {tool_input.get('pattern', '')}"
    if name.startswith(f"mcp__{policy.MCP_NAME}__"):
        short = name.rsplit("__", 1)[-1]
        args = " ".join(f"{k}={v}" for k, v in tool_input.items())
        return f"CDOS {short.upper()} {args}".strip()
    return name.upper()


def _flatten(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts)
    return ""


class ClaudeSession:
    """A resumable Claude Code conversation rooted in the workspace."""

    def __init__(self, workspace: Path, *, command: str = "claude",
                 model: str = "", state_dir: Path, socket_path: str = "",
                 running_from: Optional[Path] = None,
                 on_event: Optional[EventSink] = None,
                 on_finished: Optional[Callable[[TurnResult], None]] = None,
                 extra_args: Optional[List[str]] = None):
        self.workspace = Path(workspace)
        self.command = command
        self.model = model
        self.state_dir = Path(state_dir)
        self.socket_path = socket_path
        self.running_from = running_from or Path(__file__).resolve().parents[2]
        self.on_event = on_event or (lambda kind, text: None)
        self.on_finished = on_finished or (lambda result: None)
        self.extra_args = list(extra_args or [])
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self.session_id = self._load_session_id()
        self.last_result: Optional[TurnResult] = None
        self.total_cost = 0.0

    # ------------------------------------------------------------------
    # Persistence: the conversation outlives a restart of the shell
    # ------------------------------------------------------------------
    @property
    def _session_file(self) -> Path:
        return self.state_dir / "session-id"

    def _load_session_id(self) -> str:
        try:
            return self._session_file.read_text().strip()
        except OSError:
            return ""

    def _save_session_id(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._session_file.write_text(self.session_id + "\n")
        except OSError:
            pass

    def new_conversation(self) -> None:
        self.session_id = ""
        try:
            self._session_file.unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.resolve_command() is not None

    def resolve_command(self) -> Optional[str]:
        if os.path.isabs(self.command):
            return self.command if os.access(self.command, os.X_OK) else None
        return shutil.which(self.command)

    @property
    def busy(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def write_config(self) -> Dict[str, Path]:
        """The settings and MCP files ``claude`` is started with."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        settings = self.state_dir / "settings.json"
        settings.write_text(policy.settings_json(self.workspace))
        mcp = self.state_dir / "mcp.json"
        env = {"PYTHONPATH": str(self.running_from)}
        mcp.write_text(json.dumps({"mcpServers": {policy.MCP_NAME: {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "earth_os.agent.mcp_server",
                     "--socket", self.socket_path],
            "env": env,
        }}}, indent=2))
        return {"settings": settings, "mcp": mcp}

    def argv(self, prompt: str) -> List[str]:
        files = self.write_config()
        command = self.resolve_command() or self.command
        argv = [
            command, "-p", prompt,
            "--output-format", "stream-json", "--verbose",
            "--permission-mode", "acceptEdits",
            "--settings", str(files["settings"]),
            "--mcp-config", str(files["mcp"]), "--strict-mcp-config",
            "--permission-prompt-tool", policy.PERMISSION_TOOL,
            "--append-system-prompt",
            system_prompt(self.workspace, self.running_from),
        ]
        if self.session_id:
            argv += ["--resume", self.session_id]
        if self.model:
            argv += ["--model", self.model]
        return argv + self.extra_args

    # ------------------------------------------------------------------
    def ask(self, prompt: str) -> bool:
        """Start one turn. Returns False, having said why, if it cannot."""
        prompt = (prompt or "").strip()
        if not prompt:
            return False
        with self._lock:
            if self.busy:
                self.on_event("error", "Claude Code is still working. "
                                       "Press INTERRUPT to stop it.")
                return False
            if not self.available:
                self.on_event("error", f"'{self.command}' is not installed. "
                              "Install Claude Code: npm install -g "
                              "@anthropic-ai/claude-code, then run 'claude' "
                              "once in a terminal to sign in.")
                return False
            env = dict(os.environ)
            # If CDOS itself was started from inside a Claude Code session, the
            # child would inherit that session's identity and try to become
            # it. This conversation is its own.
            for inherited in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
                              "CLAUDE_CODE_REMOTE_SESSION_ID",
                              "CLAUDE_CODE_CHILD_SESSION",
                              "CLAUDE_CODE_ENTRYPOINT"):
                env.pop(inherited, None)
            # A press can take a while; the permission prompt must not time
            # out underneath the person reading it.
            env.setdefault("MCP_TOOL_TIMEOUT", "900000")
            env["EARTH_OS_AGENT"] = "1"
            try:
                self._proc = subprocess.Popen(
                    self.argv(prompt), cwd=self.workspace,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, errors="replace",
                    bufsize=1, env=env)
            except OSError as exc:
                self.on_event("error", f"could not start Claude Code: {exc}")
                return False
        threading.Thread(target=self._pump, args=(self._proc,), daemon=True,
                         name="claude-code").start()
        return True

    def interrupt(self) -> bool:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            proc.terminate()
        except OSError:
            return False
        return True

    # ------------------------------------------------------------------
    def _pump(self, proc: subprocess.Popen) -> None:
        errors: List[str] = []

        def drain_stderr() -> None:
            if proc.stderr is None:
                return
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    errors.append(line)
                    del errors[:-20]

        err_thread = threading.Thread(target=drain_stderr, daemon=True)
        err_thread.start()

        result: Optional[TurnResult] = None
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    self.on_event("system", raw[:300])
                    continue
                maybe = self.handle_event(event)
                if maybe is not None:
                    result = maybe
        except Exception as exc:  # pragma: no cover - teardown races
            self.on_event("error", f"stream error: {exc}")
        code = proc.wait()
        err_thread.join(timeout=2.0)

        if result is None:
            detail = errors[-1] if errors else f"exit {code}"
            if code in (-15, 143):
                detail = "interrupted"
            result = TurnResult(ok=False, error=detail,
                                session_id=self.session_id)
            if detail != "interrupted":
                for line in errors[-6:]:
                    self.on_event("error", line)
        self.last_result = result
        self.on_finished(result)

    def handle_event(self, event: Dict[str, object]) -> Optional[TurnResult]:
        """Turn one stream-json event into transcript lines."""
        kind = event.get("type")
        sid = str(event.get("session_id") or "")
        if sid and sid != self.session_id:
            self.session_id = sid
            self._save_session_id()

        if kind == "system" and event.get("subtype") == "init":
            servers = event.get("mcp_servers") or []
            cdos = next((s for s in servers if isinstance(s, dict)
                         and s.get("name") == policy.MCP_NAME), None)
            state = cdos.get("status", "?") if isinstance(cdos, dict) else "absent"
            model = event.get("model", "")
            self.on_event("system", f"session {sid[:8]} · {model} · "
                                    f"shell link {str(state).upper()}")
            return None

        if kind == "assistant":
            message = event.get("message") or {}
            for block in (message.get("content") or []) if isinstance(message, dict) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = str(block.get("text", "")).strip()
                    if text:
                        self.on_event("assistant", text)
                elif block.get("type") == "tool_use":
                    self.on_event("tool", describe_tool(
                        str(block.get("name", "")),
                        block.get("input") or {}, self.workspace))
            return None

        if kind == "user":
            message = event.get("message") or {}
            for block in (message.get("content") or []) if isinstance(message, dict) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result" \
                        and block.get("is_error"):
                    text = _flatten(block.get("content")).strip()
                    if text.startswith("{"):
                        # A bridge reply: say its reason, not its punctuation.
                        try:
                            text = str(json.loads(text).get("error") or text)
                        except (ValueError, AttributeError):
                            pass
                    if text:
                        self.on_event("refused", text.splitlines()[0][:240])
            return None

        if kind == "result":
            cost = float(event.get("total_cost_usd") or 0.0)
            self.total_cost += cost
            ok = not bool(event.get("is_error")) and \
                event.get("subtype") == "success"
            return TurnResult(
                ok=ok, text=str(event.get("result") or ""), cost_usd=cost,
                turns=int(event.get("num_turns") or 0), session_id=sid,
                error="" if ok else str(event.get("result")
                                        or event.get("subtype") or "error"),
                raw=dict(event))
        return None
