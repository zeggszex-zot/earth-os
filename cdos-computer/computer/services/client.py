"""A client for the local API (computer/services/api.py): a Unix socket path, or a loopback port."""

from __future__ import annotations

import http.client
import json
import socket


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 30.0):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(self.timeout)
            s.connect(self._path)
        except OSError:
            s.close()
            raise
        self.sock = s


class Client:
    def __init__(self, socket_path: str = "", port: int = 0, timeout: float = 30.0):
        if not socket_path and not port:
            raise ValueError("a socket path or a loopback port")
        self.socket_path, self.port, self.timeout = socket_path, port, timeout

    def _conn(self):
        if self.socket_path:
            return _UnixConnection(self.socket_path, self.timeout)
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        c = self._conn()
        try:
            data = json.dumps(body).encode() if body is not None else None
            c.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
            r = c.getresponse()
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
        finally:
            c.close()

    def get(self, path: str) -> dict:
        status, d = self.request("GET", path)
        if status != 200:
            raise RuntimeError(f"{status}: {d}")
        return d

    def post(self, path: str, body: dict) -> dict:
        status, d = self.request("POST", path, body)
        if status != 200:
            raise RuntimeError(f"{status}: {d}")
        return d
