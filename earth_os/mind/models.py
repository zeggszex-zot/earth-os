"""Model management: one model, loaded once, shared by every voice.

A personality is not a model. The Earth OS Companion, the Technical Assistant
and every character in a game world are prompts over the *same* loaded model,
which is what lets a machine with one GPU — or none — host many of them.
This module is where that is arranged:

* a **spec** names a model: ``ollama:llama3.2:3b``, optionally with
  ``@http://host:port``; or ``reasoner``, meaning whatever the shell's
  ``[voice] llm`` already is (the cloud backend, say);
* a **client** is one connection to one model. Two environments that name the
  same spec get the *same* client object, so nothing is connected or loaded
  twice (``ModelManager.clients`` is keyed by the resolved spec);
* an **environment** (``os``, ``game``, ``game:harbour``) is assigned a spec.
  Unassigned environments use the default, and a game falls back to ``game``
  and then to the default — so giving game characters a smaller model is one
  configuration line, and giving them the same one is none.

Three things happen at this layer because it is the one place every call
passes through:

* **secrets are redacted** from every message (:mod:`.redact`);
* **calls are serialised per client** — a local model runs one generation at
  a time, and twelve characters asking at once should queue, not thrash;
* **usage is counted per environment**, which is what Phase 7's measurements
  read.

The existing Ollama integration is reused, not rewritten: the Ollama provider
is :class:`earth_os.companion.model.CompanionModel`, with its fixed
``num_ctx`` (changing it makes Ollama reload the model), ``keep_alive`` and
cached availability check. New providers are a function registered with
:meth:`ModelManager.register_provider`.

If no model is available nothing here raises on construction and nothing is
contacted until a turn asks: deterministic CDOS answers and the operating
system do not pass through this module at all.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .redact import redact_messages

DEFAULT_ENVIRONMENT = "os"


@dataclass(frozen=True)
class ModelSpec:
    provider: str = "none"            # ollama | reasoner | none | a registered provider
    model: str = ""
    host: str = ""

    @classmethod
    def parse(cls, text: str) -> "ModelSpec":
        """``ollama:llama3.2:3b@http://127.0.0.1:11434`` → provider, model, host."""
        text = (text or "").strip()
        if not text or text.lower() in ("none", "off"):
            return cls()
        host = ""
        if "@" in text:
            text, host = text.rsplit("@", 1)
        provider, _, model = text.partition(":")
        return cls(provider.strip().lower(), model.strip(), host.strip().rstrip("/"))

    def key(self) -> Tuple[str, str, str]:
        return (self.provider, self.model, self.host)

    def __str__(self) -> str:
        text = self.provider + (f":{self.model}" if self.model else "")
        return text + (f"@{self.host}" if self.host else "")


@dataclass
class Usage:
    calls: int = 0
    failures: int = 0
    prompt_chars: int = 0
    output_chars: int = 0
    seconds: float = 0.0
    redactions: int = 0
    waited: float = 0.0               # time spent queued behind another voice

    def to_dict(self) -> Dict[str, float]:
        return {"calls": self.calls, "failures": self.failures, "prompt_chars": self.prompt_chars,
                "output_chars": self.output_chars, "seconds": round(self.seconds, 3),
                "redactions": self.redactions, "waited": round(self.waited, 3)}


class ManagedModel:
    """One client, shared. Every call is redacted, serialised and counted."""

    def __init__(self, spec: ModelSpec, client, clock=time.monotonic):
        self.spec = spec
        self.client = client
        self.clock = clock
        self._lock = threading.Lock()
        self.usage: Dict[str, Usage] = {}
        self.last_redactions = 0

    @property
    def name(self) -> str:
        return str(self.spec)

    @property
    def available(self) -> bool:
        try:
            return bool(self.client.available)
        except Exception:
            return False

    def status(self) -> str:
        try:
            return str(self.client.status())
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

    def chat(self, messages: List[Dict[str, str]], mode: str = "CASUAL",
             length_factor: float = 1.0, temperature_shift: float = 0.0,
             environment: str = DEFAULT_ENVIRONMENT) -> str:
        usage = self.usage.setdefault(environment, Usage())
        clean, redactions = redact_messages(messages)
        self.last_redactions = redactions
        queued = self.clock()
        with self._lock:
            started = self.clock()
            usage.waited += started - queued
            usage.calls += 1
            usage.redactions += redactions
            usage.prompt_chars += sum(len(m.get("content", "")) for m in clean)
            try:
                out = self.client.chat(clean, mode, length_factor, temperature_shift)
            except Exception:
                usage.failures += 1
                raise
            finally:
                usage.seconds += self.clock() - started
        out = str(out or "")
        usage.output_chars += len(out)
        return out

    def bound(self, environment: str) -> "BoundModel":
        return BoundModel(self, environment)


class BoundModel:
    """A :class:`ManagedModel` as one environment sees it.

    Has the three things the Companion Engine asks of a model —
    ``available``, ``status()`` and ``chat(messages, mode, …)`` — so the
    existing engine uses it unchanged.
    """

    def __init__(self, managed: ManagedModel, environment: str):
        self.managed = managed
        self.environment = environment

    @property
    def name(self) -> str:
        return self.managed.name

    @property
    def available(self) -> bool:
        return self.managed.available

    def status(self) -> str:
        return self.managed.status()

    def chat(self, messages, mode: str = "CASUAL", length_factor: float = 1.0,
             temperature_shift: float = 0.0) -> str:
        return self.managed.chat(messages, mode, length_factor, temperature_shift,
                                 environment=self.environment)


Provider = Callable[[ModelSpec, Dict[str, object]], object]


def _ollama(spec: ModelSpec, options: Dict[str, object]):
    from ..companion.model import DEFAULT_MODEL, CompanionModel
    return CompanionModel(
        host=spec.host or str(options.get("host") or "http://127.0.0.1:11434"),
        model=spec.model or DEFAULT_MODEL,
        timeout=float(options.get("timeout", 60)),
        num_ctx=int(options.get("num_ctx", 4096)),
        keep_alive=str(options.get("keep_alive", "10m")))


class ModelManager:
    """Which model each environment speaks through, and the clients behind them."""

    def __init__(self, default: str = "", assignments: Optional[Dict[str, str]] = None,
                 options: Optional[Dict[str, object]] = None, reasoner=None):
        self.options: Dict[str, object] = dict(options or {})
        self.providers: Dict[str, Provider] = {"ollama": _ollama}
        if reasoner is not None:
            self.register_reasoner(reasoner)
        self.default = ModelSpec.parse(default)
        self.assignments: Dict[str, ModelSpec] = {
            env: ModelSpec.parse(spec) for env, spec in (assignments or {}).items() if spec}
        #: One client per resolved spec — the whole point of this class.
        self.clients: Dict[Tuple[str, str, str], ManagedModel] = {}
        self.errors: Dict[str, str] = {}
        self._lock = threading.Lock()

    # -- configuration ---------------------------------------------------------
    @classmethod
    def from_config(cls, config, reasoner=None) -> "ModelManager":
        """From ``[mind]``, falling back to the existing ``[companion]`` and ``[ollama]``.

        A machine configured for the Companion Engine before Earth Mind existed
        keeps talking to the same model with no new settings.
        """
        from ..companion.profile import from_block
        mind = config.section("mind")
        companion = config.section("companion")
        # The context window and the wait are the conversation profile's unless
        # the configuration names its own ([companion] profile, num_ctx, timeout).
        _profile, num_ctx, timeout = from_block(companion)
        default = str(mind.get("model") or "")
        if not default:
            backend = str(companion.get("backend", "ollama"))
            if backend == "ollama":
                default = "ollama:" + str(companion.get("model", "llama3.2:3b"))
            elif backend == "reasoner":
                default = "reasoner"
        options = {
            "host": str(companion.get("host") or config.get("ollama", "host", "http://127.0.0.1:11434")),
            "timeout": timeout,
            "num_ctx": num_ctx,
            "keep_alive": companion.get("keep_alive", "10m"),
        }
        assignments = {"game": str(mind.get("game_model") or "")}
        return cls(default=default, assignments=assignments, options=options, reasoner=reasoner)

    def register_provider(self, name: str, factory: Provider) -> None:
        """A new kind of model: ``factory(spec, options)`` returns a client with
        ``available``, ``status()`` and ``chat(messages, mode, length_factor, temperature_shift)``."""
        self.providers[name.lower()] = factory

    def register_reasoner(self, reasoner) -> None:
        """Let any shell reasoner (the cloud backend, say) serve as ``reasoner``."""
        def build(_spec: ModelSpec, _options: Dict[str, object]):
            from ..companion.model import ReasonerModel
            return ReasonerModel(reasoner)
        self.providers["reasoner"] = build

    def assign(self, environment: str, spec: str) -> None:
        self.assignments[environment] = ModelSpec.parse(spec)

    # -- resolution ------------------------------------------------------------
    def spec_for(self, environment: str = DEFAULT_ENVIRONMENT) -> ModelSpec:
        """``game:harbour`` → its own assignment, else ``game``'s, else the default."""
        probe = environment or DEFAULT_ENVIRONMENT
        while probe:
            if probe in self.assignments:
                return self.assignments[probe]
            probe = probe.rpartition(":")[0]
        return self.default

    def managed(self, spec: ModelSpec) -> Optional[ManagedModel]:
        if spec.provider in ("", "none"):
            return None
        with self._lock:
            found = self.clients.get(spec.key())
            if found is not None:
                return found
            factory = self.providers.get(spec.provider)
            if factory is None:
                self.errors[str(spec)] = f"no provider called {spec.provider!r}"
                return None
            try:
                client = factory(spec, self.options)
            except Exception as exc:                 # a model must never stop the machine
                self.errors[str(spec)] = f"{type(exc).__name__}: {exc}"
                return None
            managed = ManagedModel(spec, client)
            self.clients[spec.key()] = managed
            return managed

    def model(self, environment: str = DEFAULT_ENVIRONMENT, spec: str = "") -> Optional[BoundModel]:
        """The model *environment* speaks through, or None when it has none.

        *spec* overrides the assignment — a personality that names its own
        model. Either way the client comes from the shared table.
        """
        chosen = ModelSpec.parse(spec) if spec else self.spec_for(environment)
        managed = self.managed(chosen)
        return managed.bound(environment) if managed is not None else None

    def use(self, client, environment: str = "", spec: str = "custom") -> BoundModel:
        """Install an already-built client (a test's recording model, say)."""
        parsed = ModelSpec.parse(spec)
        managed = ManagedModel(parsed, client)
        with self._lock:
            self.clients[parsed.key()] = managed
        if environment:
            self.assignments[environment] = parsed
        else:
            self.default = parsed
        return managed.bound(environment or DEFAULT_ENVIRONMENT)

    # -- reporting -------------------------------------------------------------
    def available(self, environment: str = DEFAULT_ENVIRONMENT) -> bool:
        model = self.model(environment)
        return model is not None and model.available

    def usage(self) -> Dict[str, Dict[str, float]]:
        """Per environment, across every client."""
        total: Dict[str, Usage] = {}
        for managed in self.clients.values():
            for env, use in managed.usage.items():
                into = total.setdefault(env, Usage())
                for name in ("calls", "failures", "prompt_chars", "output_chars", "seconds",
                             "redactions", "waited"):
                    setattr(into, name, getattr(into, name) + getattr(use, name))
        return {env: use.to_dict() for env, use in sorted(total.items())}

    def status_rows(self) -> List[Tuple[str, str]]:
        rows = [("MODEL", str(self.default) or "none")]
        for env, spec in sorted(self.assignments.items()):
            if spec != self.default:
                rows.append((f"MODEL {env.upper()}", str(spec) or "none"))
        rows.append(("MODELS LOADED", str(len(self.clients))))
        for label, why in sorted(self.errors.items()):
            rows.append((f"MODEL ERROR {label}", why))
        return rows

    def describe(self) -> str:
        model = self.model(DEFAULT_ENVIRONMENT)
        if model is None:
            return "no model configured"
        return model.status()
