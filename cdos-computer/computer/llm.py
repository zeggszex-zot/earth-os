"""The mini LM: an optional voice, never the source of truth.

Talks to any OpenAI-compatible server on the Pi (llama.cpp's llama-server is
the one the installer sets up).  Standard library only.

The I Ching is offered to the model as a *tool*, `consult_iching`.  Tiny
models are unreliable at deciding to call tools, so by default the computer calls
the tool itself and hands the result to the model to word (mode "precall").
Models that do call tools well can be given the tools directly (mode "tools").
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

TOOLS = [
    {"type": "function", "function": {
        "name": "consult_iching",
        "description": "Cast the I Ching for an open, personal or 'should I' question. Returns a hexagram with "
                       "counsel and stance. It is a perspective for reflection, never a fact or a prediction.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string", "description": "The person's question, in their words."}},
            "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "lookup_fact",
        "description": "Look up facts in the CDOS corpus (planets, countries, words, dates). Use for anything factual.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"}}, "required": ["question"]}}},
]


class MiniLM:
    def __init__(self, endpoint: str = "http://127.0.0.1:8080", model: str = "", timeout: float = 60.0):
        self.endpoint = endpoint.rstrip("/").removesuffix("/v1")
        self.model = model
        self.timeout = timeout
        self._ok: bool | None = None
        self._checked = 0.0
        self.error = ""
        self.last_timings: dict = {}

    @property
    def label(self) -> str:
        return f"{self.model or 'local'} (mini LM)"

    def available(self, force: bool = False) -> bool:
        if not self.endpoint:
            return False
        if not force and self._ok is not None and time.monotonic() - self._checked < 30:
            return self._ok
        self._checked = time.monotonic()
        try:
            with urllib.request.urlopen(f"{self.endpoint}/v1/models", timeout=2.5) as r:
                data = json.loads(r.read() or b"{}")
                self._ok = True
                if not self.model:
                    self.model = (data.get("data") or [{}])[0].get("id", "")
                self.error = ""
        except (urllib.error.URLError, OSError, ValueError) as e:
            self._ok = False
            self.error = str(e)
        return self._ok

    def complete(self, messages, temperature=0.7, max_tokens=90, tools=None, extra=None) -> dict:
        body = {"model": self.model or "local", "messages": messages, "temperature": temperature,
                "max_tokens": max_tokens, "stream": False}
        if tools:
            body["tools"] = tools
        if extra:
            body.update(extra)       # e.g. {"grammar": ...}: llama-server's constrained sampling
        req = urllib.request.Request(f"{self.endpoint}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        # llama-server reports its own timings; the compute engine logs them per profile
        t = data.get("timings") or {}
        u = data.get("usage") or {}
        self.last_timings = {"ms": round((time.perf_counter() - t0) * 1000, 1),
                             "tokens": t.get("predicted_n", u.get("completion_tokens")),
                             "tok_s": round(t["predicted_per_second"], 2) if t.get("predicted_per_second") else None,
                             "prompt_tok_s": round(t["prompt_per_second"], 2) if t.get("prompt_per_second") else None}
        return (data.get("choices") or [{}])[0].get("message") or {}

    def chat(self, messages, **kw) -> str:
        return (self.complete(messages, **kw).get("content") or "").strip()
