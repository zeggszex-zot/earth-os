"""The agent's end of the control channel: a stdio MCP server.

Claude Code starts this as a child process (``--mcp-config``) and talks
JSON-RPC to it over stdin/stdout. Every tool call becomes one request to the
bridge socket inside the running shell. This process holds no state and
decides nothing; the shell does both.

Standard library only, and no import of GTK or of the shell, so it starts in
milliseconds and cannot drift from the bridge it forwards to.

    python3 -m earth_os.agent.mcp_server --socket /run/user/1000/earth-os/agent.sock
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
from typing import Any, Dict, List, Optional

PROTOCOL = "2025-06-18"

#: Operations that wait for a person, so they must not time out underneath
#: somebody reading the confirmation strip.
WAITS_FOR_A_PRESS = {"approve", "invoke"}

_obj = {"type": "object"}

TOOLS: List[Dict[str, Any]] = [
    {"name": "status",
     "description": "What the CDOS shell is showing right now: current screen, "
                    "palette, language, status line, anything awaiting a press, "
                    "and the last few lines of the computer conversation.",
     "inputSchema": {**_obj, "properties": {}}},
    {"name": "screens",
     "description": "List the screens of EARTH OS and which is current.",
     "inputSchema": {**_obj, "properties": {}}},
    {"name": "go",
     "description": "Switch EARTH OS to a screen by name "
                    "(main, programs, recent, browser, engineering, settings, "
                    "tasks, terminal, claude).",
     "inputSchema": {**_obj, "properties": {"screen": {"type": "string"}},
                     "required": ["screen"]}},
    {"name": "say",
     "description": "Say something as the computer: it appears in the main "
                    "conversation and is spoken if a voice is configured.",
     "inputSchema": {**_obj, "properties": {
         "text": {"type": "string"},
         "speak": {"type": "boolean", "default": True}},
         "required": ["text"]}},
    {"name": "palettes",
     "description": "List the EARTH OS colour palettes and which is in use.",
     "inputSchema": {**_obj, "properties": {}}},
    {"name": "set_palette",
     "description": "Switch the live EARTH OS colour palette.",
     "inputSchema": {**_obj, "properties": {"name": {"type": "string"}},
                     "required": ["name"]}},
    {"name": "screenshot",
     "description": "Render a screen of the RUNNING shell and return the image. "
                    "Shows the code currently running, not unaccepted edits.",
     "inputSchema": {**_obj, "properties": {
         "screen": {"type": "string", "description": "default: current"},
         "width": {"type": "integer", "default": 1600},
         "height": {"type": "integer", "default": 1000}}}},
    {"name": "capabilities",
     "description": "The closed catalogue of what this computer can be asked to "
                    "do, with each capability's arguments and whether it is "
                    "effectful (needs a person's press).",
     "inputSchema": {**_obj, "properties": {}}},
    {"name": "invoke",
     "description": "Run one catalogue capability through the shell's gated "
                    "path. Effectful ones are put on the EARTH OS confirmation "
                    "strip and this call waits until the person presses "
                    "CONFIRM or CANCEL.",
     "inputSchema": {**_obj, "properties": {
         "capability": {"type": "string"},
         "arguments": {**_obj, "additionalProperties": True}},
         "required": ["capability"]}},
    {"name": "approve",
     "description": "Permission prompt for Claude Code itself; answered by the "
                    "person at the EARTH OS screen. Not for direct use.",
     "inputSchema": {**_obj, "properties": {
         "tool_name": {"type": "string"},
         "input": {**_obj, "additionalProperties": True},
         "tool_use_id": {"type": "string"}},
         "required": ["tool_name", "input"]}},
]


class BridgeClient:
    def __init__(self, path: str):
        self.path = path

    def request(self, op: str, args: Dict[str, Any]) -> Dict[str, Any]:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(None if op in WAITS_FOR_A_PRESS else 60.0)
        try:
            sock.connect(self.path)
            sock.sendall((json.dumps({"op": op, "args": args}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        finally:
            sock.close()
        return json.loads(data.decode("utf-8") or "{}")


class Server:
    def __init__(self, client: BridgeClient, out=None):
        self.client = client
        self.out = out or sys.stdout

    def send(self, message: Dict[str, Any]) -> None:
        self.out.write(json.dumps(message) + "\n")
        self.out.flush()

    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method", "")
        mid = message.get("id")
        params = message.get("params") or {}
        if mid is None:
            return None                              # a notification
        if method == "initialize":
            return self._ok(mid, {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cdos", "version": "1.0"},
                "instructions": "Controls the EARTH OS shell this agent is "
                                "built into. Effectful acts wait for a press.",
            })
        if method == "ping":
            return self._ok(mid, {})
        if method == "tools/list":
            return self._ok(mid, {"tools": TOOLS})
        if method == "tools/call":
            return self._ok(mid, self.call(str(params.get("name", "")),
                                           dict(params.get("arguments") or {})))
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"unknown method {method}"}}

    @staticmethod
    def _ok(mid: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if name not in {tool["name"] for tool in TOOLS}:
            return _text({"ok": False, "error": f"unknown tool {name}"}, True)
        try:
            reply = self.client.request(name, arguments)
        except (OSError, ValueError) as exc:
            return _text({"ok": False, "error": "the CDOS shell is not "
                          f"reachable: {exc}"}, True)

        if name == "approve":
            # The permission-prompt contract: the text *is* the decision.
            return {"content": [{"type": "text", "text": json.dumps(reply)}]}

        failed = reply.get("ok") is False
        result = _text(reply, failed)
        if name == "screenshot" and not failed:
            try:
                with open(reply["path"], "rb") as fh:
                    data = base64.b64encode(fh.read()).decode("ascii")
                result["content"].insert(0, {"type": "image", "data": data,
                                             "mimeType": "image/png"})
            except (OSError, KeyError):
                pass
        return result


def _text(payload: Dict[str, Any], error: bool) -> Dict[str, Any]:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, indent=1, default=str)}],
            "isError": bool(error)}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="cdos-mcp")
    parser.add_argument("--socket", required=True)
    args = parser.parse_args(argv)
    server = Server(BridgeClient(args.socket))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            server.send({"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32700, "message": "parse error"}})
            continue
        reply = server.handle(message)
        if reply is not None:
            server.send(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
