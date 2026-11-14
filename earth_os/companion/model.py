"""The language model, used sparingly: one call per ordinary turn.

Target: Ollama serving Llama 3.2 3B Instruct, Q4_K_M (``llama3.2:3b``, whose
default quantisation that is). Choices made for a small local model:

* ``num_ctx`` is fixed. Ollama reloads a model when a request changes it, so
  varying it per mode would cost a reload on every mode change. Length is
  limited per mode with ``num_predict`` instead.
* ``keep_alive`` keeps the model loaded between turns of a conversation and
  lets it go after, rather than loading it once per question.
* Availability is asked at most every 30 seconds, and it is a listing, not an
  inference. Nothing here runs between turns.

:class:`ReasonerModel` lets any existing shell reasoner (the cloud one, say)
stand in, through its plain ``ask`` interface.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

DEFAULT_MODEL = "llama3.2:3b"

#: Per mode: (num_predict, temperature)
LIMITS = {"CASUAL": (160, 0.7), "REFLECTIVE": (320, 0.65), "DEEP": (600, 0.45)}


@dataclass
class Usage:
    calls: int = 0
    prompt_chars: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0


class CompanionModel:
    name = "ollama"

    def __init__(self, host: str = "http://127.0.0.1:11434", model: str = DEFAULT_MODEL,
                 timeout: float = 60.0, num_ctx: int = 4096, keep_alive: str = "10m",
                 recheck_seconds: float = 30.0, clock=time.monotonic):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = float(timeout)
        self.num_ctx = int(num_ctx)
        self.keep_alive = keep_alive
        self.recheck = float(recheck_seconds)
        self.clock = clock
        self._checked_at: Optional[float] = None
        self._available = False
        self.why = ""
        self.usage = Usage()
        self.last_error = ""

    # -- availability, cached ------------------------------------------------
    def _check(self) -> None:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=2.0) as r:
                tags = json.loads(r.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._available, self.why = False, f"no Ollama at {self.host} ({type(exc).__name__})"
            return
        names = {str(m.get("name", "")) for m in tags.get("models", []) if isinstance(m, dict)}
        wanted = {self.model, f"{self.model}:latest"} if ":" not in self.model else {self.model}
        if names & wanted:
            self._available, self.why = True, ""
        else:
            self._available = False
            self.why = f"model {self.model} is not pulled (ollama pull {self.model})"

    @property
    def available(self) -> bool:
        now = self.clock()
        if self._checked_at is None or now - self._checked_at >= self.recheck:
            self._checked_at = now
            self._check()
        return self._available

    def status(self) -> str:
        return f"{self.model} @ {self.host}" if self.available else self.why

    # -- the one call -----------------------------------------------------------
    def chat(self, messages: List[Dict[str, str]], mode: str = "CASUAL",
             length_factor: float = 1.0, temperature_shift: float = 0.0) -> str:
        predict, temperature = LIMITS.get(mode, LIMITS["CASUAL"])
        payload = {
            "model": self.model, "messages": messages, "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx, "num_predict": max(48, int(predict * length_factor)),
                        "temperature": max(0.0, min(1.2, temperature + temperature_shift))},
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(f"{self.host}/api/chat", data=data, method="POST",
                                         headers={"Content-Type": "application/json"})
        started = time.monotonic()
        self.usage.calls += 1
        self.usage.prompt_chars += sum(len(m.get("content", "")) for m in messages)
        with urllib.request.urlopen(request, timeout=self.timeout) as r:
            result = json.loads(r.read().decode("utf-8"))
        self.usage.prompt_tokens += int(result.get("prompt_eval_count", 0) or 0)
        self.usage.output_tokens += int(result.get("eval_count", 0) or 0)
        self.usage.seconds += time.monotonic() - started
        return str((result.get("message") or {}).get("content", "")).strip()


class ReasonerModel:
    """Any shell :class:`~earth_os.voice.backends.Reasoner` as a companion model."""

    def __init__(self, reasoner):
        self.reasoner = reasoner
        self.name = getattr(reasoner, "name", "reasoner")
        self.usage = Usage()
        self._available: Optional[bool] = None

    @property
    def available(self) -> bool:
        if self._available is None:          # once: some reasoners ask over the network
            self._available = bool(self.reasoner.available)
        return self._available

    def status(self) -> str:
        return self.reasoner.status()

    def chat(self, messages, mode="CASUAL", length_factor=1.0, temperature_shift=0.0) -> str:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        rest = [m for m in messages if m["role"] != "system"]
        prompt = rest[-1]["content"] if rest else ""
        self.usage.calls += 1
        self.usage.prompt_chars += sum(len(m.get("content", "")) for m in messages)
        return (self.reasoner.ask(prompt, system, "", rest[:-1]) or "").strip()
