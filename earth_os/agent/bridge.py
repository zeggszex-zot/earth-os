"""The running shell's end of the agent's control channel.

A Unix socket, one JSON request per connection, one JSON reply. The MCP server
(:mod:`.mcp_server`) is the only intended client; it runs as a child of
Claude Code, which is a child of this shell.

Two rules shape every handler:

* **Everything touches the shell on its own thread.** Requests arrive on a
  socket thread; screens and pending choices belong to the frame clock. The
  ``schedule`` callable (``GLib.idle_add`` in the application, direct calls in
  tests) is how work crosses over.

* **Nothing here is a second door.** Capabilities go through
  :meth:`Shell.run_capability`, which is the gated path — an effectful one is
  put on the confirmation strip and this bridge *waits for the person*,
  exactly as a spoken request would. The bridge never calls
  ``capabilities.invoke`` itself; that is the ungated door AGENTS.md §2 rule 5
  describes being used once already to power a machine off unasked.

Claude Code's own permission prompts arrive here too (``approve``) and go to
the same strip, with ALLOW and DENY.
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import policy

Schedule = Callable[[Callable[[], None]], None]

#: How long a person has to answer before the request is refused. Refusing is
#: the safe way for silence to resolve.
DECISION_TIMEOUT = 600.0


class BridgeError(RuntimeError):
    pass


class ControlBridge:
    def __init__(self, shell, *, workspace: Optional[Path] = None,
                 schedule: Optional[Schedule] = None,
                 shots_dir: Optional[Path] = None,
                 decision_timeout: float = DECISION_TIMEOUT,
                 on_request: Optional[Callable[[str, Dict[str, Any]], None]] = None):
        self.shell = shell
        self.workspace = Path(workspace) if workspace else None
        self.schedule = schedule
        self.shots_dir = Path(shots_dir) if shots_dir else None
        self.decision_timeout = decision_timeout
        self.on_request = on_request or (lambda op, args: None)
        self.path = ""
        self._server: Optional[socketserver.UnixStreamServer] = None
        #: One person, one strip: requests needing a press take turns.
        self._press_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Socket lifecycle
    # ------------------------------------------------------------------
    def start(self, path: str) -> str:
        if self._server is not None:
            return self.path
        try:
            os.unlink(path)
        except OSError:
            pass
        bridge = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                line = self.rfile.readline()
                try:
                    request = json.loads(line.decode("utf-8"))
                    reply = bridge.handle(str(request.get("op", "")),
                                          dict(request.get("args") or {}))
                except Exception as exc:
                    reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                self.wfile.write((json.dumps(reply, default=str) + "\n").encode())

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        self._server = Server(path, Handler)
        os.chmod(path, 0o600)
        self.path = path
        threading.Thread(target=self._server.serve_forever, daemon=True,
                         name="cdos-agent-bridge").start()
        return path

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Crossing to the shell's thread
    # ------------------------------------------------------------------
    def on_main(self, fn: Callable[[], Any], timeout: float = 30.0) -> Any:
        if self.schedule is None:
            return fn()
        box: Dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            try:
                box["value"] = fn()
            except Exception as exc:
                box["error"] = exc
            finally:
                done.set()

        self.schedule(run)
        if not done.wait(timeout):
            raise BridgeError("the shell did not respond")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    # ------------------------------------------------------------------
    def handle(self, op: str, args: Dict[str, Any]) -> Dict[str, Any]:
        handler = getattr(self, f"op_{op}", None)
        if handler is None:
            return {"ok": False, "error": f"unknown operation: {op}"}
        self.on_request(op, args)
        return handler(**args)

    # ------------------------------------------------------------------
    # Reading the shell
    # ------------------------------------------------------------------
    def op_status(self) -> Dict[str, Any]:
        def read() -> Dict[str, Any]:
            s = self.shell
            return {
                "ok": True,
                "computer_name": s.config.get("shell", "computer_name", "EARTH OS"),
                "screen": s.current,
                "screens": sorted(s.screens),
                "palette": s.palette.name,
                "language": s.strings.name,
                "status_message": s.status_message,
                "awaiting_press": s.pending_prompt,
                "voice": s.voice_state_label(),
                "conversation": [f"{m['role']}: {m['text']}"
                                 for m in s.conversation[-6:]],
                "workspace": str(self.workspace or ""),
                "platform": ("ok" if s.capabilities is not None
                             else s.platform_error),
            }
        return self.on_main(read)

    def op_screens(self) -> Dict[str, Any]:
        def read() -> Dict[str, Any]:
            return {"ok": True, "current": self.shell.current, "screens": {
                name: getattr(screen, "title", name)
                for name, screen in sorted(self.shell.screens.items())}}
        return self.on_main(read)

    def op_palettes(self) -> Dict[str, Any]:
        from ..theme import Palette
        return {"ok": True, "current": self.shell.palette.name,
                "palettes": Palette.list_builtin()}

    def op_capabilities(self) -> Dict[str, Any]:
        return {"ok": True, "catalogue": self.shell.capability_catalogue()}

    # ------------------------------------------------------------------
    # Acting on the interface (nothing here changes the machine)
    # ------------------------------------------------------------------
    def op_go(self, screen: str = "") -> Dict[str, Any]:
        def act() -> Dict[str, Any]:
            if screen not in self.shell.screens:
                return {"ok": False, "error": f"no screen named {screen!r}",
                        "screens": sorted(self.shell.screens)}
            self.shell.go(screen)
            return {"ok": True, "screen": self.shell.current}
        return self.on_main(act)

    def op_say(self, text: str = "", speak: bool = True) -> Dict[str, Any]:
        text = str(text).strip()
        if not text:
            return {"ok": False, "error": "nothing to say"}
        self.on_main(lambda: self.shell.say(text[:600], speak=bool(speak)))
        return {"ok": True}

    def op_set_palette(self, name: str = "") -> Dict[str, Any]:
        from ..theme import Palette
        if name not in Palette.list_builtin():
            return {"ok": False, "error": f"no palette named {name!r}",
                    "palettes": Palette.list_builtin()}
        self.on_main(lambda: self.shell.set_palette(name))
        return {"ok": True, "palette": name,
                "note": "live until restart; SETTINGS > SAVE keeps it"}

    def op_screenshot(self, screen: str = "", width: int = 1600,
                      height: int = 1000) -> Dict[str, Any]:
        """Render a screen of the *running* shell to a PNG."""
        import cairo

        width = max(320, min(int(width), 3840))
        height = max(240, min(int(height), 2160))
        out = self.shots_dir or Path("/tmp/cdos-claude-shots")
        out.mkdir(parents=True, exist_ok=True)

        def render() -> Dict[str, Any]:
            name = screen or self.shell.current
            target = self.shell.screens.get(name)
            if target is None:
                return {"ok": False, "error": f"no screen named {name!r}"}
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
            cr = cairo.Context(surface)
            cr.set_source_rgb(*self.shell.palette.background)
            cr.paint()
            ctx = self.shell.context(width, height)
            target.draw(cr, ctx)
            target.draw(cr, ctx)          # live labels settle on a second pass
            # The window is a different size; make it rebuild for its own.
            target.invalidate()
            path = out / f"{name}-{width}x{height}-{int(time.time())}.png"
            surface.write_to_png(str(path))
            self.shell.redraw()
            return {"ok": True, "path": str(path), "screen": name,
                    "note": "this is the running code; render the workspace "
                            "with tools/screenshot.py to see edits"}
        return self.on_main(render)

    # ------------------------------------------------------------------
    # The gated paths
    # ------------------------------------------------------------------
    def op_invoke(self, capability: str = "",
                  arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run a catalogue capability the way a spoken request would."""
        arguments = dict(arguments or {})
        shell = self.shell
        if shell.capabilities is None:
            return {"ok": False, "error": f"no platform adapter: {shell.platform_error}"}
        spec = shell.capabilities.spec(capability)
        if spec is None:
            return {"ok": False, "error": f"{capability!r} is not in the "
                    "catalogue, and the catalogue is closed"}

        if not spec.effectful:
            def run() -> Dict[str, Any]:
                shell.last_capability_result = None
                ran = shell.run_capability(capability, **arguments)
                return _outcome(shell, capability, ran)
            return self.on_main(run)

        with self._press_lock:
            decided = threading.Event()
            verdict: Dict[str, Any] = {}

            def offer() -> bool:
                shell.last_capability_result = None
                shell.run_capability(capability, **arguments)
                if not shell.pending_options:
                    return False           # refused by validation; said why
                wrapped = []
                for label, callback in shell.pending_options:
                    def pressed(cb=callback, label=label) -> None:
                        try:
                            cb()
                        finally:
                            verdict["pressed"] = label
                            decided.set()
                    wrapped.append((label, pressed))
                shell.pending_options = wrapped
                self._draw_attention()
                return True

            if not self.on_main(offer):
                return {"ok": False, "error": shell.status_message or "refused"}
            if not decided.wait(self.decision_timeout):
                self.on_main(lambda: self._withdraw("no answer in time"))
                return {"ok": False, "confirmed": False,
                        "error": "nobody pressed CONFIRM; nothing was run"}
            if verdict.get("pressed") != "CONFIRM":
                return {"ok": False, "confirmed": False,
                        "error": "the person pressed CANCEL; nothing was run"}
            out = self.on_main(lambda: _outcome(shell, capability, True))
            out["confirmed"] = True
            return out

    def op_approve(self, tool_name: str = "", input: Optional[Dict[str, Any]] = None,
                   tool_use_id: str = "", **_ignored) -> Dict[str, Any]:
        """Claude Code asking whether it may use a tool: ask the person."""
        tool_input = dict(input or {})
        root = self.workspace or Path.cwd()
        decision, detail = policy.review(tool_name, tool_input, root)
        if decision == "deny":
            return {"behavior": "deny", "message": detail}

        with self._press_lock:
            decided = threading.Event()
            verdict: Dict[str, str] = {}

            def offer() -> bool:
                shell = self.shell
                if shell.pending_options:
                    return False
                def choose(label: str) -> None:
                    shell.pending_options = []
                    shell.pending_prompt = ""
                    verdict["pressed"] = label
                    decided.set()
                    shell.redraw()
                shell.pending_prompt = (f"CLAUDE CODE REQUESTS · {tool_name.upper()}"
                                        f" — {detail}")[:220]
                shell.pending_options = [("ALLOW", lambda: choose("ALLOW")),
                                         ("DENY", lambda: choose("DENY"))]
                self._draw_attention()
                return True

            if not self.on_main(offer):
                return {"behavior": "deny", "message":
                        "the confirmation strip is busy with something else; "
                        "ask again in a moment"}
            if not decided.wait(self.decision_timeout):
                self.on_main(lambda: self._withdraw("no answer in time"))
                return {"behavior": "deny", "message": "nobody answered in time"}
        if verdict.get("pressed") == "ALLOW":
            return {"behavior": "allow", "updatedInput": tool_input}
        return {"behavior": "deny", "message": "the person pressed DENY"}

    # ------------------------------------------------------------------
    def _draw_attention(self) -> None:
        """Make sure the person can see the strip that is waiting on them."""
        shell = self.shell
        if shell.current not in ("main", "claude") and "claude" in shell.screens:
            shell.go("claude")
        shell.play("computer")
        shell.redraw()

    def _withdraw(self, why: str) -> None:
        self.shell.pending_options = []
        self.shell.pending_prompt = ""
        self.shell.status_message = f"Claude Code request withdrawn: {why}"
        self.shell.redraw()


def _outcome(shell, capability: str, ran: bool) -> Dict[str, Any]:
    result = getattr(shell, "last_capability_result", None)
    if result is None:
        return {"ok": bool(ran), "error": shell.status_message if not ran else ""}
    return {"ok": bool(result.ok), "value": result.value,
            "error": result.error if not result.ok else ""}
