"""Fixtures shared by the Earth Mind tests. Not a test module.

The model is a recording fake, or a local HTTP server speaking Ollama's API so
that the real client (``companion.model.CompanionModel``) is what gets
exercised. Everything else is real: the shell, the capability layer, the
memory store (SQLite), the World Model's ledger and CDOS Computer embedded
from ``cdos-computer/``.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os.config import Config                                  # noqa: E402
from earth_os.mind import EarthMind, MemoryStore, ModelManager      # noqa: E402
from earth_os.mind.personas import PersonalityRegistry             # noqa: E402
from earth_os.mind.system import SystemSubsystem                    # noqa: E402
from earth_os.world_model import UserLedger                         # noqa: E402


class FakeModel:
    """Records every call. *reply* is a string, or a function of the messages."""

    name = "fake"

    def __init__(self, reply="Glad to talk.", available=True, fail=False, delay=0.0):
        self.reply = reply
        self._available = available
        self.fail = fail
        self.delay = delay
        self.calls: List[dict] = []
        self.running = 0
        self.most_at_once = 0
        self._lock = threading.Lock()

    @property
    def available(self):
        return self._available

    def status(self):
        return "fake" if self._available else "fake: not running"

    def chat(self, messages, mode="CASUAL", length_factor=1.0, temperature_shift=0.0):
        with self._lock:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        try:
            self.calls.append({"messages": [dict(m) for m in messages], "mode": mode})
            if self.delay:
                time.sleep(self.delay)
            if self.fail:
                raise TimeoutError("model timed out")
            return self.reply(messages) if callable(self.reply) else self.reply
        finally:
            with self._lock:
                self.running -= 1

    def system(self, i=-1) -> str:
        return self.calls[i]["messages"][0]["content"]

    def everything(self, i=-1) -> str:
        return "\n".join(m["content"] for m in self.calls[i]["messages"])

    def all_text(self) -> str:
        return "\n".join(self.everything(i) for i in range(len(self.calls)))


class FakeOllama:
    """A local server speaking Ollama's HTTP API: ``/api/tags`` and ``/api/chat``.

    *reply* is a string or a function of the request payload. Every payload is
    kept, so a test can read exactly what would have reached a real model.
    """

    def __init__(self, reply="Hello from a small model.", models=("llama3.2:3b",)):
        outer = self
        self.reply = reply
        self.models = list(models)
        self.payloads: List[dict] = []
        self.tags_hits = 0

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.tags_hits += 1
                self._json({"models": [{"name": m} for m in outer.models]})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n))
                outer.payloads.append(payload)
                text = outer.reply(payload) if callable(outer.reply) else outer.reply
                self._json({"message": {"role": "assistant", "content": text},
                            "prompt_eval_count": 100, "eval_count": 10})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f"http://127.0.0.1:{self.server.server_address[1]}"

    def prompts(self) -> str:
        return "\n".join(m.get("content", "") for p in self.payloads for m in p.get("messages", []))

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def dead_host() -> str:
    """An address nothing is listening on: Ollama, switched off."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def temp_config(**mind) -> Config:
    """A fresh configuration in a scratch directory: no voice, memory in a scratch file."""
    tmp = Path(tempfile.mkdtemp(prefix="earth-mind-test-"))
    config = Config.load(tmp / "config.toml")
    config.values["voice"]["enabled"] = False
    config.values["mind"]["memory"] = str(tmp / "memory.db")
    config.values["mind"].update(mind)
    return config


def ledger_in_tmp() -> UserLedger:
    return UserLedger.load(Path(tempfile.mkdtemp(prefix="earth-mind-ledger-")) / "world.json")


def bare_mind(model=None, store: Optional[MemoryStore] = None, system=None, link=None, ledger=None,
              **kw) -> EarthMind:
    """Earth Mind with no shell: a fake model (or none), an in-memory store unless given."""
    models = ModelManager()
    if model is not None:
        models.use(model)
    return EarthMind(models=models, store=store or MemoryStore(ledger=ledger), system=system, link=link,
                     ledger=ledger, personalities=PersonalityRegistry(), default_world="harbour", **kw)


class FakePort:
    """What the system subsystem asks of the shell, with every call recorded.

    Stands in for the shell where a test is about Earth Mind's own decisions
    (who may ask for what) rather than about readings. ``acted`` stays empty:
    nothing Earth Mind does may reach it.
    """

    platform_error = ""

    def __init__(self, corpus=None, programs=()):
        from earth_os.capability_corpus import CapabilityCorpus
        self.capabilities = object()
        self.capability_corpus = corpus or CapabilityCorpus()
        self.programs = dict(programs)
        self.readings: List[str] = []
        self.analyses: List[str] = []
        self.acted: List[str] = []

    def answer_reading(self, capability):
        self.readings.append(capability)
        return True, f"reading of {capability}: 42%"

    def answer_analysis(self, name, table=False):
        self.analyses.append(name)
        if name == "performance_report":
            return True, ("The processor is at 91%, with a load average of 7.50 across 4 cores. "
                          "Memory is 88% full: 14.1 GB of 16.0 GB in use. "
                          "firefox holds the most memory, about 6.2 GB.")
        return True, "    MEMORY  PROCS  PROGRAM\n    6.2 GB      9  firefox"

    def find_program(self, name):
        name = name.strip().lower()
        return (self.programs[name], f"/usr/bin/{name}") if name in self.programs else None


def fake_system(**kw) -> SystemSubsystem:
    return SystemSubsystem(FakePort(**kw))


def wait_for(condition: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting")
