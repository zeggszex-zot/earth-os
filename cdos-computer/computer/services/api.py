"""The local CDOS API: HTTP with JSON bodies, over a Unix socket (default) or TCP bound to 127.0.0.1 only.

    python3 -m computer.services.api --config /etc/cdos-computer/config.toml       (systemd/cdos-api.service)

    GET  /v1/health                     corpus version and integrity, neural model, plugins, components, robotics
    GET  /v1/capabilities               every declared capability, and why any is off
    GET  /v1/components                 every component's name, version, kind and input/output interface
    POST /v1/components/<name>          {"input": {...}} -> {"output": {...}}   (both checked against the interface)
    POST /v1/ask                        {"question": "..."} -> {"text", "faculty", "status", "certainty", "envelope"}
    GET  /v1/robot/state                the controller's current state of the robot
    POST /v1/robot/observations         {"observations": [...]} -> {"accepted", "refused": [{"index", "why"}]}
    POST /v1/robot/action               {"goal": [x, y]} -> the decision: allowed, candidate, violations, command
    POST /v1/robot/e_stop               {"on": true} anyone; {"on": false, "reason": "..."} an operator (latched)
    GET  /v1/growth/tasks               the Growth Engine's tasks (answers and runs: python3 -m computer.growth)
    GET  /v1/governor                   the Governor: policy, audit log, emergency-stop latch
    GET  /v1/governor/whoami            who the server thinks you are, your roles, what you may do

Every request is decided by the Governor (computer/governor.py) before it runs.  Over the Unix socket the caller is
the uid the kernel reports for the connection (SO_PEERCRED); over TCP the caller is unverified and gets at most read
and ask.  Robot observations and motions need the robot role; clearing the emergency stop needs the operator role and
a reason ({"on": false, "reason": "..."}); engaging it is open to every caller.  A denial is a 403 FORBIDDEN, and is
written to the Governor's audit log.

One request at a time (the computer isn't shared between threads).  Requests over 256 KB, bodies that aren't JSON
objects and unknown paths get a JSON error with code and message.  Nothing here can promote a model, write the corpus
or reach the network: the server binds a Unix socket, or 127.0.0.1 and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socketserver
import stat
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

MAX_BODY = 256 * 1024


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# (method, path) -> the permission it needs.  Anything not listed needs api.read (and then gets its 404).
PERMISSION = {("POST", "/v1/ask"): "api.ask", ("POST", "/v1/robot/observations"): "robot.observe",
              ("POST", "/v1/robot/action"): "robot.command"}


def permission_for(method: str, path: str) -> str:
    if method == "POST" and path.startswith("/v1/components/"):
        return "api.ask"
    return PERMISSION.get((method, path), "api.read")


class Handler(BaseHTTPRequestHandler):
    platform = None
    identify = None                           # connection -> Identity; default: the kernel's peer credentials
    server_version = "cdos-api/0.2"
    protocol_version = "HTTP/1.1"

    def address_string(self) -> str:          # a Unix socket has no client address
        return "local"

    def log_message(self, fmt, *args) -> None:
        if os.environ.get("CDOS_API_LOG"):
            sys.stderr.write("cdos-api: " + (fmt % args) + "\n")

    def _send(self, status: int, obj) -> None:
        body = (json.dumps(obj, default=str) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drain(self) -> None:
        """A request refused before its body was read (a 403, a 404): read the body anyway, so it isn't taken for the
        next request on this connection, and the client isn't cut off mid-send.  Too big: close instead."""
        if getattr(self, "_read_body", False):
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = MAX_BODY + 1
        if 0 < n <= MAX_BODY:
            self.rfile.read(n)
        elif n:
            self.close_connection = True

    def _body(self) -> dict:
        self._read_body = True
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            self.close_connection = True       # its body is left unread: this connection can't carry another request
            raise ApiError(413, "INVALID_INPUT", f"request body over {MAX_BODY} bytes")
        raw = self.rfile.read(n) if n else b"{}"
        try:
            d = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "INVALID_INPUT", "the body isn't JSON") from None
        if not isinstance(d, dict):
            raise ApiError(400, "INVALID_INPUT", "the body must be a JSON object")
        return d

    def _identity(self):
        from computer.governor import peer_identity
        fn = type(self).identify
        return fn(self.connection) if fn is not None else peer_identity(self.connection)

    def _route(self, method: str):
        from computer.governor import Forbidden
        p = self.platform
        path = self.path.split("?", 1)[0].rstrip("/")
        ident = self._identity()
        gov = p.governor
        if path.startswith("/v1/robot/") and p.robot is None:
            raise ApiError(503, "UNAVAILABLE", p.robot_error or "robotics is off (robotics = false)")
        if method == "POST" and path == "/v1/robot/e_stop":
            body = self._body()
            on, reason = body.get("on"), body.get("reason", "")
            if not isinstance(on, bool):
                raise ApiError(400, "INVALID_INPUT", "on must be true or false")
            if not isinstance(reason, str) or len(reason) > 500:
                raise ApiError(400, "INVALID_INPUT", "reason must be a string of at most 500 characters")
            try:
                latch = p.estop(ident, on, reason)
            except Forbidden as e:
                raise ApiError(403, "FORBIDDEN", e.why) from None
            return {"e_stop": p.robot.e_stop, "latch": latch}
        try:
            gov.authorize(ident, permission_for(method, path), target=f"{method} {path}", audit_allowed=False)
        except Forbidden as e:
            raise ApiError(403, "FORBIDDEN", e.why) from None
        if method == "GET" and path == "/v1/governor":
            return gov.status()
        if method == "GET" and path == "/v1/governor/whoami":
            return gov.whoami(ident)
        if method == "GET" and path == "/v1/health":
            return p.health()
        if method == "GET" and path == "/v1/capabilities":
            return {"capabilities": p.capabilities()}
        if method == "GET" and path == "/v1/components":
            return {"components": p.components.describe(), "refused": dict(p.components.refused)}
        m = re.fullmatch(r"/v1/components/([a-z][a-z0-9_.]*)", path)
        if method == "POST" and m:
            from computer.neural.components import ComponentFailure, InterfaceError
            body = self._body()
            try:
                return {"output": p.components.call(m.group(1), body.get("input"))}
            except InterfaceError as e:
                raise ApiError(400, "INVALID_INPUT", str(e)) from None
            except ComponentFailure as e:
                raise ApiError(404 if "not registered" in e.reason else 503, "UNAVAILABLE", str(e)) from None
        if method == "POST" and path == "/v1/ask":
            q = self._body().get("question")
            if not isinstance(q, str) or not q.strip() or len(q) > 2000:
                raise ApiError(400, "INVALID_INPUT", "question must be a non-empty string of at most 2000 characters")
            return p.ask(q)
        if path.startswith("/v1/robot/"):
            if p.robot is None:
                raise ApiError(503, "UNAVAILABLE", p.robot_error or "robotics is off (robotics = false)")
            if method == "GET" and path == "/v1/robot/state":
                return {"state": p.robot.state().summary(), "e_stop": p.robot.e_stop}
            if method == "POST" and path == "/v1/robot/observations":
                obs = self._body().get("observations")
                if not isinstance(obs, list):
                    raise ApiError(400, "INVALID_INPUT", "observations must be a list")
                return p.robot.submit(obs)
            if method == "POST" and path == "/v1/robot/action":
                return p.robot.request_action(self._body().get("goal"))
        if method == "GET" and path == "/v1/growth/tasks":
            from computer.services.platform import resolve
            reg = resolve(p.cfg.neural_registry)
            if reg is None:
                raise ApiError(503, "UNAVAILABLE", "neural_registry not set")
            from computer.growth.engine import Growth
            return {"tasks": [{k: t[k] for k in ("id", "kind", "status", "created")} | {"questions": len(t["questions"]),
                                                                                        "answers": len(t["answers"])}
                              for t in Growth(reg).tasks()]}
        raise ApiError(404, "INVALID_INPUT", f"no {method} {path}")

    def _handle(self, method: str) -> None:
        self._read_body = False
        try:
            self._send(200, self._route(method))
        except ApiError as e:
            self._drain()
            self._send(e.status, {"error": {"code": e.code, "message": e.message}})
        except Exception as e:  # noqa: BLE001 - a failed request is answered, never a dead server
            self._send(500, {"error": {"code": "UNAVAILABLE", "message": f"{type(e).__name__}: {e}"}})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


class UnixServer(socketserver.UnixStreamServer):
    allow_reuse_address = True

    def server_bind(self):
        p = Path(self.server_address)
        if p.exists() and stat.S_ISSOCK(p.stat().st_mode):
            p.unlink()                         # a socket left by a process that died
        p.parent.mkdir(parents=True, exist_ok=True)
        super().server_bind()
        os.chmod(self.server_address, 0o660)   # the service's user and group only


class LoopbackServer(socketserver.TCPServer):
    allow_reuse_address = True


def make_server(platform, socket_path: str = "", tcp_port: int = 0, identify=None):
    """identify: connection -> Identity, for tests; the default asks the kernel (Unix socket) or says unverified."""
    attrs = {"platform": platform}
    if identify is not None:
        attrs["identify"] = staticmethod(identify)
    handler = type("BoundHandler", (Handler,), attrs)
    if socket_path:
        return UnixServer(socket_path, handler)
    if tcp_port:
        return LoopbackServer(("127.0.0.1", int(tcp_port)), handler)   # loopback only, by construction
    raise SystemExit("set api_socket (or api_tcp_port, loopback only) in config.toml")


def main(argv=None) -> int:
    from computer.config import load
    from computer.services.platform import Platform
    ap = argparse.ArgumentParser(prog="python3 -m computer.services.api", description=__doc__.split("\n\n")[0])
    ap.add_argument("--config")
    ap.add_argument("--socket", help="override api_socket")
    ap.add_argument("--port", type=int, help="override api_tcp_port (127.0.0.1 only)")
    a = ap.parse_args(argv)
    cfg = load(a.config)
    plat = Platform(cfg)
    srv = make_server(plat, a.socket or cfg.api_socket, a.port or cfg.api_tcp_port)
    where = srv.server_address
    print(f"cdos-api: serving on {where}; corpus {plat.computer.corpus.status()}; "
          f"neural {plat.computer.neural.model_id if plat.computer.neural else 'off'}; "
          f"governor {'DEGRADED: ' + plat.governor.audit_error if plat.governor.degraded else 'ok'}"
          f"{'' if plat.governor.policy else ' (' + plat.governor.policy_error + ')'}; "
          f"e-stop {'engaged' if plat.governor.latch.engaged else 'clear'}", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if isinstance(where, str):
            try:
                os.unlink(where)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
