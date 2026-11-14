"""CDOS Computer, linked into the shell: the corpus, the navigator, the Governor.

``cdos-bridge`` knows the machine's state. CDOS Computer (``cdos-computer/``
beside this tree) knows facts: a sharded corpus, a navigator that answers in
checked plans, unit-aware calculation, and a Governor that decides who may ask
what. This module is how the shell reaches it.

Two ways in, and the contract is the same for both:

``socket``
    The ``cdos-api`` service, over its Unix socket (``/run/cdos/api.sock`` when
    installed with ``tools/install_services.sh``). The kernel tells the service
    who is asking, so the Governor decides with a verified identity. This is
    the production path, and the shell needs nothing from the computer's code
    to use it: HTTP with JSON bodies, spoken here with the standard library.

``embedded``
    The computer's code, imported in-process, for a checkout with no service
    running — ``python3 -m earth_os --windowed`` straight from the merged
    repository. Only ``Platform.ask`` and ``Platform.health`` are called, which
    is what the Governor's default ``ask`` and ``read`` roles allow anyway.

``auto`` (the default) uses the service when this machine has it installed,
and the embedded computer when it does not. A machine with the service
installed never quietly falls back to embedding: if the service is down, that
is reported, because an embedded computer there could not read the service's
corpus and would answer worse while looking fine. ``off`` disables the link.

What the shell may do with an answer is decided by :attr:`Answer.final`, not by
the caller's taste. CDOS Computer's rules are that facts come only from the
corpus, sums only from its calculator, and health, money and safety never go
to anything that guesses. So an answer it gave is spoken as given, and a
NOTICE is never passed on to a model to "improve". Only NOT FOUND, UNSUPPORTED
and an unavailable corpus leave room for anything else to try — and even then
:class:`~earth_os.voice.cdos_backend.CdosReasoner` lets a model propose a
command, not a fact, unless ``free_answers`` is turned on.

Failure is data: a link that cannot reach the computer returns an
``Answer`` with ``reached=False`` and the reason. It never invents a reply.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import stat
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Where tools/install_services.sh puts the service's socket.
DEFAULT_SOCKET = "/run/cdos/api.sock"
#: Where tools/install_services.sh puts the computer's code.
INSTALLED_SOURCE = "/opt/cdos-computer/current"
#: Written by tools/install_services.sh: its presence means this machine runs
#: the service, so ``auto`` means the service and nothing else.
SERVICE_CONFIG = "/etc/cdos-computer/config.toml"
MODES = ("auto", "socket", "embedded", "off")


@dataclass
class Answer:
    """One reply from CDOS Computer, as the API returns it."""

    text: str = ""
    faculty: str = ""                  # knowledge | logic | analysis | instinct | priority | ...
    status: str = ""                   # answered | unsupported | conflict | invalid | unavailable
    certainty: str = ""                # ESTABLISHED | INFERRED | UNKNOWN | CONFLICT
    truth: Optional[str] = None        # TRUE | FALSE | UNKNOWN | CONFLICT | None
    envelope: Dict[str, Any] = field(default_factory=dict)
    via: str = ""                      # socket | embedded
    reached: bool = False              # False: the computer was not reached at all
    error: str = ""

    @property
    def notice(self) -> bool:
        """Health, money, safety or care: the computer's own policy answered.

        The computer files "someone may be hurt" under the ``care`` faculty
        and says PRIORITY; health and money under knowledge, saying NOTICE.
        """
        head = self.text.lstrip().upper()
        return (self.faculty in ("priority", "care") or head.startswith("NOTICE")
                or head.startswith("PRIORITY"))

    @property
    def not_found(self) -> bool:
        return self.text.lstrip().upper().startswith("NOT FOUND")

    @property
    def unsupported(self) -> bool:
        return self.text.lstrip().upper().startswith("UNSUPPORTED")

    @property
    def final(self) -> bool:
        """True when nothing else may answer in the computer's place.

        An answer from the corpus, a calculation, a stated conflict, a refused
        unit and every NOTICE are final. NOT FOUND, UNSUPPORTED and an
        unavailable corpus are not: they say what the computer could not do.
        """
        if not self.reached or not self.text:
            return False
        if self.notice:
            return True
        if self.not_found or self.unsupported:
            return False
        return self.status in ("answered", "conflict", "invalid")

    @property
    def evidence(self) -> List[Dict[str, Any]]:
        ev = self.envelope.get("evidence") if isinstance(self.envelope, dict) else None
        return list(ev) if isinstance(ev, list) else []

    @property
    def tag(self) -> str:
        """``[KNOWLEDGE]``, as the computer's own terminal labels a line."""
        return f"[{self.faculty.upper()}]" if self.faculty else ""

    @classmethod
    def from_api(cls, body: Dict[str, Any], via: str) -> "Answer":
        env = body.get("envelope") if isinstance(body.get("envelope"), dict) else {}
        return cls(text=str(body.get("text") or ""),
                   faculty=str(body.get("faculty") or ""),
                   status=str(body.get("status") or env.get("status") or ""),
                   certainty=str(body.get("certainty") or env.get("certainty") or ""),
                   truth=body.get("truth"), envelope=env, via=via, reached=True)

    @classmethod
    def failed(cls, why: str, via: str = "") -> "Answer":
        return cls(via=via, reached=False, error=why)


class LinkError(Exception):
    """The computer could not be reached, or refused the request."""

    def __init__(self, why: str, status: int = 0, code: str = ""):
        super().__init__(why)
        self.why, self.status, self.code = why, status, code


# ---------------------------------------------------------------------------
# The socket: HTTP over AF_UNIX, standard library only
# ---------------------------------------------------------------------------

class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(self._path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


class SocketTransport:
    """The ``cdos-api`` service. One connection per request, as it expects."""

    via = "socket"

    def __init__(self, path: str = DEFAULT_SOCKET, port: int = 0, timeout: float = 10.0):
        self.path = os.path.expanduser(path) if path else ""
        self.port = int(port or 0)
        self.timeout = float(timeout)

    @property
    def present(self) -> bool:
        if self.path:
            try:
                return stat.S_ISSOCK(os.stat(self.path).st_mode)
            except PermissionError:
                return True          # it is there; asking will say who may not
            except OSError:
                return False
        return bool(self.port)

    def where(self) -> str:
        return self.path if self.path else f"127.0.0.1:{self.port}"

    def request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self.path:
            conn: http.client.HTTPConnection = _UnixHTTPConnection(self.path, self.timeout)
        elif self.port:
            # Loopback only, as the service binds it.
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        else:
            raise LinkError("no socket or port configured")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        try:
            conn.request(method, path, body=data,
                         headers={"Content-Type": "application/json"} if data else {})
            reply = conn.getresponse()
            raw = reply.read().decode("utf-8", errors="replace")
            status = reply.status
        except PermissionError:
            raise LinkError(f"permission denied on {self.where()}: add this account to the "
                            f"service's group (sudo usermod -aG cdos $USER) and log in again") from None
        except (FileNotFoundError, ConnectionRefusedError):
            raise LinkError(f"{self.where()} is not open (is cdos-api.service running?)") from None
        except (OSError, http.client.HTTPException) as exc:
            raise LinkError(f"{self.where()}: {type(exc).__name__}: {exc}") from None
        finally:
            conn.close()
        try:
            parsed = json.loads(raw or "{}")
        except ValueError:
            raise LinkError(f"{self.where()} answered with something that is not JSON",
                            status) from None
        if not isinstance(parsed, dict):
            raise LinkError(f"{self.where()} answered with a JSON {type(parsed).__name__}", status)
        if status != 200:
            err = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
            raise LinkError(str(err.get("message") or f"HTTP {status}"), status,
                            str(err.get("code") or ""))
        return parsed


# ---------------------------------------------------------------------------
# Embedded: the computer's own code, in this process
# ---------------------------------------------------------------------------

def _looks_like_source(path: Path) -> bool:
    return (path / "computer" / "core.py").is_file() and \
        (path / "computer" / "services" / "platform.py").is_file()


def find_source(configured: str = "") -> Optional[Path]:
    """Where the computer's code is: configured, environment, beside us, installed."""
    candidates: List[Path] = []
    if configured:
        candidates.append(Path(os.path.expanduser(configured)))
    env = os.environ.get("CDOS_COMPUTER_SOURCE", "")
    if env:
        candidates.append(Path(os.path.expanduser(env)))
    # EARTH OS carries the computer inside its own tree: <root>/cdos-computer
    # beside <root>/earth_os in a checkout, and <prefix>/lib/earth-os/cdos-computer
    # beside <prefix>/lib/earth-os/earth_os once installed. The same arithmetic
    # finds both, and an EARTH OS install never needs a CDOS one to be present.
    here = Path(__file__).resolve()
    if len(here.parents) > 2:
        candidates.append(here.parents[2] / "cdos-computer")
    # A CDOS merged repository or install, when this tree has no copy of its
    # own: <root>/cdos-computer beside <root>/earth-os.
    if len(here.parents) > 3:
        candidates.append(here.parents[3] / "cdos-computer")
    # A shell running from its Claude Code workspace is not beside anything,
    # so the places the installer uses are named too.
    candidates.append(Path.home() / ".local" / "lib" / "cdos-computer")
    candidates.append(Path("/usr/local/lib/cdos-computer"))
    candidates.append(Path("/usr/lib/cdos-computer"))
    candidates.append(Path(INSTALLED_SOURCE))
    for candidate in candidates:
        if _looks_like_source(candidate):
            return candidate.resolve()
    return None


class EmbeddedTransport:
    """CDOS Computer's ``Platform`` built in this process, on first use.

    The computer is not shared between threads, so every call holds a lock. It
    is built lazily, on whichever worker thread asks first, so loading a corpus
    never lands on the frame clock or in the boot path.
    """

    via = "embedded"

    def __init__(self, source: Optional[Path], config: str = "", log_dir: Optional[Path] = None,
                 name: str = "", user: str = ""):
        self.source = source
        self.config = os.path.expanduser(config) if config else ""
        self.log_dir = log_dir
        self.name, self.user = name, user
        self._platform = None
        self._error = ""
        self._lock = threading.Lock()

    @property
    def present(self) -> bool:
        return self.source is not None and not self._error

    @property
    def built(self) -> bool:
        return self._platform is not None

    def where(self) -> str:
        return str(self.source) if self.source else "no source"

    def _build(self):
        if self._platform is not None:
            return self._platform
        if self._error:
            raise LinkError(self._error)
        if self.source is None:
            raise LinkError("the computer's code was not found")
        src = str(self.source)
        if src not in sys.path:
            # Appended, never prepended: nothing here may shadow the shell's own modules.
            sys.path.append(src)
        try:
            from computer.config import load               # type: ignore[import-not-found]
            from computer.services.platform import Platform  # type: ignore[import-not-found]
            cfg = load(self.config or None)
            if not self.config:
                # No config named: keep the computer's writes in the shell's data
                # directory rather than beside its code, which may be read-only.
                if self.log_dir is not None:
                    try:
                        self.log_dir.mkdir(parents=True, exist_ok=True)
                        cfg.log_file = str(self.log_dir / "computer.log.jsonl")
                    except OSError:
                        cfg.log_file = ""
                if self.name:
                    cfg.name = self.name
                if self.user:
                    cfg.user = self.user
                cfg.robotics = False          # a shell has no business driving a robot
            self._platform = Platform(cfg)
        except LinkError:
            raise
        except Exception as exc:              # a broken checkout must not take the shell down
            self._error = f"embedded computer failed to start: {type(exc).__name__}: {exc}"
            raise LinkError(self._error) from None
        return self._platform

    def request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            platform = self._build()
            try:
                if method == "POST" and path == "/v1/ask":
                    return platform.ask(str((body or {}).get("question", "")))
                if method == "GET" and path == "/v1/health":
                    return platform.health()
                if method == "GET" and path == "/v1/capabilities":
                    return {"capabilities": platform.capabilities()}
            except Exception as exc:
                raise LinkError(f"{type(exc).__name__}: {exc}") from None
        # Deliberately narrow: the embedded path serves what the Governor's
        # default roles allow. Anything privileged goes through the service.
        raise LinkError(f"{method} {path} needs the cdos-api service", 404, "UNAVAILABLE")


# ---------------------------------------------------------------------------
# The link
# ---------------------------------------------------------------------------

class ComputerLink:
    """The shell's one handle on CDOS Computer, however it is reached."""

    def __init__(self, mode: str = "auto", socket_path: str = DEFAULT_SOCKET, port: int = 0,
                 timeout: float = 10.0, source: str = "", config: str = "",
                 log_dir: Optional[Path] = None, name: str = "", user: str = "",
                 service_config: str = SERVICE_CONFIG):
        self.mode = mode if mode in MODES else "auto"
        self.socket = SocketTransport(socket_path, port, timeout)
        self.service_config = service_config
        self.embedded = EmbeddedTransport(
            find_source(source) if self.mode in ("auto", "embedded") else None,
            config=config, log_dir=log_dir, name=name, user=user)
        self.last_error = ""
        self.last_via = ""

    @classmethod
    def from_config(cls, cfg) -> "ComputerLink":
        """Build from the shell's ``[computer]`` section."""
        from ..config import data_dir
        block = cfg.section("computer")
        if not bool(block.get("enabled", True)):
            return cls(mode="off")
        return cls(mode=str(block.get("mode", "auto")),
                   socket_path=str(block.get("socket", DEFAULT_SOCKET)),
                   port=int(block.get("port", 0) or 0),
                   timeout=float(block.get("timeout", 10)),
                   source=str(block.get("source", "")),
                   config=str(block.get("config", "")),
                   log_dir=data_dir() / "computer",
                   name=str(cfg.get("shell", "computer_name", "") or ""),
                   user=str(cfg.get("shell", "operator", "") or ""))

    # -- which way in --------------------------------------------------------
    def transport(self):
        """The transport to use now. Re-decided per call, so a service that
        starts after the shell is picked up without a restart."""
        if self.mode == "off":
            return None
        if self.mode == "socket":
            return self.socket
        if self.mode == "embedded":
            return self.embedded if self.embedded.present else None
        if self.socket.present or self.service_installed:
            # Installed but down is still the service's to answer for: asking
            # fails and says so, rather than an embedded computer that cannot
            # read the service's corpus answering in its place.
            return self.socket
        return self.embedded if self.embedded.present else None

    @property
    def service_installed(self) -> bool:
        return bool(self.service_config) and Path(self.service_config).is_file()

    @property
    def available(self) -> bool:
        return self.transport() is not None

    def status(self) -> str:
        if self.mode == "off":
            return "off in config"
        t = self.transport()
        if t is None:
            why = self.embedded._error or "no service socket and no source to embed"
            return f"unreachable: {why}"
        if t is self.socket and not self.socket.present:
            return (f"unreachable: {self.socket.where()} is not open "
                    f"(is cdos-api.service running?)")
        detail = f"{t.via} {t.where()}"
        if t is self.embedded and not self.embedded.built:
            detail += " (starts on first question)"
        if self.last_error:
            detail += f" · last error: {self.last_error}"
        return detail

    # -- requests ------------------------------------------------------------
    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], str]:
        t = self.transport()
        if t is None:
            raise LinkError("CDOS Computer is not reachable: " + self.status())
        try:
            reply = t.request(method, path, body)
        except LinkError as exc:
            self.last_error = exc.why
            raise
        self.last_error = ""
        self.last_via = t.via
        return reply, t.via

    def ask(self, question: str) -> Answer:
        """Ask the computer. Never raises: an unreachable computer is an Answer too."""
        question = (question or "").strip()
        if not question:
            return Answer.failed("empty question")
        try:
            body, via = self._request("POST", "/v1/ask", {"question": question[:2000]})
        except LinkError as exc:
            return Answer.failed(exc.why, getattr(self.transport(), "via", ""))
        return Answer.from_api(body, via)

    def health(self) -> Dict[str, Any]:
        body, via = self._request("GET", "/v1/health")
        body = dict(body)
        body["via"] = via
        return body

    def whoami(self) -> Dict[str, Any]:
        """Who the service thinks this shell is (socket only: the kernel says)."""
        body, _ = self._request("GET", "/v1/governor/whoami")
        return body


def health_rows(health: Dict[str, Any]) -> List[Tuple[str, str]]:
    """``/v1/health`` as label/value rows for a panel. Missing is said, not guessed."""
    if not health:
        return []
    corpus = health.get("corpus") or {}
    neural = health.get("neural") or {}
    gov = health.get("governor") or {}
    latch = gov.get("latch") if isinstance(gov.get("latch"), dict) else {}
    if "engaged" in latch:
        # The latch starts engaged and stays so until an operator clears it;
        # with robotics off it holds nothing back, but it is still reported.
        estop = "ENGAGED" if latch.get("engaged") else "clear"
    else:
        estop = "unknown"
    version = str(corpus.get("version") or "-")
    if version.startswith("sha256:"):
        version = version[7:19]
    policy = gov.get("policy") if isinstance(gov.get("policy"), dict) else {}
    if not gov:
        governor = "unknown"
    elif gov.get("degraded"):
        governor = "DEGRADED"
    elif not policy.get("loaded"):
        governor = "no policy"
    else:
        governor = "ok"
    off = health.get("capabilities_off") or {}
    plugins = health.get("plugins") or []
    return [
        ("LINK", str(health.get("via", "-"))),
        ("CORPUS", "ready" if corpus.get("ready") else "unavailable"),
        ("VERSION", version),
        ("INTEGRITY", str(corpus.get("integrity") or "-")),
        ("NEURAL", str(neural.get("model_id") or "rules only")),
        ("GOVERNOR", governor),
        ("E-STOP", estop),
        ("PLUGINS", str(len(plugins))),
        ("OFF", str(len(off)) if off else "none"),
    ]
