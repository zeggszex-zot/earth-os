"""Backend interfaces and drivers for speech and reasoning.

Everything is a subprocess or an HTTP call, so the shell needs no heavyweight
Python ML stack.  Each driver reports ``available`` honestly; the engine picks
the first configured driver that is actually installed and says so on the
settings screen rather than failing silently.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def expand(path: str) -> Path:
    return Path(os.path.expanduser(str(path or "")))


# --------------------------------------------------------------------------
# Speech to text
# --------------------------------------------------------------------------

class SpeechToText(ABC):
    name = "none"

    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def transcribe(self, wav_path: Path) -> str: ...

    def status(self) -> str:
        return "ready" if self.available else "unavailable"


class NullSTT(SpeechToText):
    name = "none"

    @property
    def available(self) -> bool:
        return False

    def transcribe(self, wav_path: Path) -> str:
        return ""


class WhisperSTT(SpeechToText):
    """Whisper speech recognition, in either of the two flavours people install.

    ``whisper.cpp`` ships a ``whisper-cli`` binary and needs a GGML model file
    on disk.  OpenAI's ``openai-whisper`` pip package ships a ``whisper``
    command that takes a model *name* and downloads it itself.  Both are
    commonly described as "the whisper CLI", so this driver detects which one
    is actually present rather than assuming whisper.cpp and reporting itself
    unavailable to anyone who installed the other.
    """

    name = "whisper"

    # Binaries that speak whisper.cpp's argument style.
    CPP_NAMES = ("whisper-cli", "whisper.cpp", "main")

    #: Places a whisper binary actually ends up, checked when PATH fails.
    #:
    #: PATH is not enough here.  The shell is usually started by a display
    #: manager, which hands it a minimal environment — frequently without
    #: ``~/.local/bin`` — so a perfectly good ``pip install --user`` or pipx
    #: install is invisible to it while being right there in a terminal.
    #: Rather than making every user discover that, look where it lands.
    EXTRA_DIRS = (
        "~/.local/share/earth-os/whisper-env/bin",   # our dedicated venv
        "~/.local/bin",                                # pip --user, pipx shim
        "~/.local/pipx/venvs/openai-whisper/bin",
        "~/whisper.cpp/build/bin",                     # a source build
        "~/whisper.cpp",
        "/usr/local/bin",
        "/opt/whisper/bin",
    )

    def __init__(self, binary: str = "whisper-cli", model: str = "",
                 language: str = "en", threads: int = 4):
        self.binary = binary
        self.model = expand(model)
        self.language = language
        self.threads = int(threads)

    def _search_names(self) -> tuple:
        return (self.binary,) + self.CPP_NAMES + ("whisper",)

    @property
    def binary_path(self) -> Optional[str]:
        # An absolute path in the config wins outright, if it is runnable.
        configured = expand(self.binary)
        if configured.is_absolute():
            if configured.is_file() and os.access(configured, os.X_OK):
                return str(configured)

        # The configured name first, then whisper.cpp's names, then OpenAI's.
        for candidate in self._search_names():
            found = shutil.which(candidate)
            if found:
                return found

        # Not on PATH — look where these things actually get installed.
        for directory in self.EXTRA_DIRS:
            base = expand(directory)
            for candidate in self._search_names():
                if not candidate or os.path.sep in candidate:
                    continue
                found = base / candidate
                if found.is_file() and os.access(found, os.X_OK):
                    return str(found)
        return None

    @property
    def needs_ffmpeg(self) -> bool:
        """OpenAI's whisper shells out to ffmpeg to load audio, even for WAV."""
        return self.flavour == "openai"

    @property
    def ffmpeg_ok(self) -> bool:
        return shutil.which("ffmpeg") is not None

    @property
    def flavour(self) -> str:
        """Which argument style the binary on PATH wants: 'cpp' or 'openai'."""
        path = self.binary_path
        if not path:
            return "none"
        return "cpp" if Path(path).name in self.CPP_NAMES else "openai"

    @property
    def openai_model(self) -> str:
        """Model *name* for the OpenAI CLI, derived from the configured path.

        ``ggml-base.en.bin`` and a bare ``base.en`` both mean the same model,
        so the config keeps working whichever flavour is installed.
        """
        stem = self.model.name
        for prefix in ("ggml-",):
            if stem.startswith(prefix):
                stem = stem[len(prefix):]
        for suffix in (".bin", ".gguf"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
        return stem or "base.en"

    @property
    def available(self) -> bool:
        flavour = self.flavour
        if flavour == "cpp":
            return self.model.exists()
        if flavour == "openai":
            # Without ffmpeg it runs, succeeds, and returns nothing at all.
            # Reporting unavailable is far kinder than silent deafness.
            return self.ffmpeg_ok
        return False

    def status(self) -> str:
        flavour = self.flavour
        if flavour == "none":
            return (f"binary '{self.binary}' not found "
                    "— run tools/setup-whisper.sh")
        if flavour == "cpp":
            if not self.model.exists():
                return (f"model missing: {self.model} "
                        "— run earth-os-fetch-models")
            return f"ready · whisper.cpp · {self.model.name}"
        if not self.ffmpeg_ok:
            return ("openai-whisper found, but ffmpeg is missing "
                    "— sudo apt install ffmpeg")
        return (f"ready · openai-whisper · {self.openai_model} "
                f"· {self.binary_path}")

    def transcribe(self, wav_path: Path) -> str:
        binary = self.binary_path
        if not binary:
            return ""
        if self.flavour == "cpp":
            return self._transcribe_cpp(binary, wav_path)
        return self._transcribe_openai(binary, wav_path)

    def _transcribe_cpp(self, binary: str, wav_path: Path) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            argv = [
                binary, "-m", str(self.model), "-f", str(wav_path),
                "-l", self.language, "-t", str(self.threads),
                "-otxt", "-of", str(out), "-nt", "--no-prints",
            ]
            try:
                subprocess.run(argv, check=False, timeout=120,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                return ""
            text_file = out.with_suffix(".txt")
            if text_file.exists():
                return text_file.read_text(encoding="utf-8", errors="replace").strip()
        return ""

    def _transcribe_openai(self, binary: str, wav_path: Path) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            argv = [
                binary, str(wav_path),
                "--model", self.openai_model,
                "--language", self.language,
                "--task", "transcribe",
                "--output_format", "txt",
                "--output_dir", tmp,
                "--fp16", "False",
                "--threads", str(self.threads),
                "--verbose", "False",
            ]
            try:
                subprocess.run(argv, check=False, timeout=120,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                return ""
            # It names the output after the input file's stem.
            text_file = Path(tmp) / (wav_path.stem + ".txt")
            if text_file.exists():
                return text_file.read_text(encoding="utf-8", errors="replace").strip()
            for fallback in Path(tmp).glob("*.txt"):
                return fallback.read_text(encoding="utf-8", errors="replace").strip()
        return ""


class VoskSTT(SpeechToText):
    """Offline recognition through the ``vosk`` Python package, if installed."""

    name = "vosk"

    def __init__(self, model_dir: str = ""):
        self.model_dir = expand(model_dir)
        self._model = None

    @property
    def available(self) -> bool:
        try:
            import vosk  # noqa: F401
        except ImportError:
            return False
        return self.model_dir.exists()

    def status(self) -> str:
        try:
            import vosk  # noqa: F401
        except ImportError:
            return "python package 'vosk' not installed"
        if not self.model_dir.exists():
            return f"model missing: {self.model_dir}"
        return f"ready · {self.model_dir.name}"

    def _load(self):
        if self._model is None:
            import vosk
            vosk.SetLogLevel(-1)
            self._model = vosk.Model(str(self.model_dir))
        return self._model

    def transcribe(self, wav_path: Path) -> str:
        if not self.available:
            return ""
        import vosk
        model = self._load()
        with wave.open(str(wav_path), "rb") as wf:
            recogniser = vosk.KaldiRecognizer(model, wf.getframerate())
            recogniser.SetWords(False)
            while True:
                data = wf.readframes(4000)
                if not data:
                    break
                recogniser.AcceptWaveform(data)
            try:
                result = json.loads(recogniser.FinalResult())
            except json.JSONDecodeError:
                return ""
        return str(result.get("text", "")).strip()


# --------------------------------------------------------------------------
# Text to speech
# --------------------------------------------------------------------------

class TextToSpeech(ABC):
    name = "none"

    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def speak(self, text: str) -> None: ...

    def stop(self) -> None:
        pass

    def status(self) -> str:
        return "ready" if self.available else "unavailable"


class NullTTS(TextToSpeech):
    name = "none"

    @property
    def available(self) -> bool:
        return False

    def speak(self, text: str) -> None:
        return


class PiperTTS(TextToSpeech):
    """Piper neural TTS, piped straight into an audio sink."""

    name = "piper"

    def __init__(self, binary: str = "piper", voice: str = "", speed: float = 1.0):
        self.binary = binary
        self.voice = expand(voice)
        self.speed = float(speed)
        self._process: Optional[subprocess.Popen] = None

    @property
    def binary_path(self) -> Optional[str]:
        return shutil.which(self.binary) or shutil.which("piper-tts")

    @property
    def sink(self) -> Optional[List[str]]:
        for argv in (["pw-cat", "-p", "--rate", "22050", "--channels", "1",
                      "--format", "s16", "-"],
                     ["paplay", "--raw", "--rate=22050", "--channels=1",
                      "--format=s16le"],
                     ["aplay", "-q", "-r", "22050", "-c", "1", "-f", "S16_LE", "-"]):
            if shutil.which(argv[0]):
                return argv
        return None

    @property
    def available(self) -> bool:
        return bool(self.binary_path) and self.voice.exists() and bool(self.sink)

    def status(self) -> str:
        if not self.binary_path:
            return f"binary '{self.binary}' not on PATH"
        if not self.voice.exists():
            return (f"voice missing: {self.voice} "
                    "— run earth-os-fetch-models")
        if not self.sink:
            return "no audio sink (pw-cat/paplay/aplay)"
        return f"ready · {self.voice.name}"

    def speak(self, text: str) -> None:
        binary, sink = self.binary_path, self.sink
        if not binary or not sink:
            return
        self.stop()
        try:
            piper = subprocess.Popen(
                [binary, "--model", str(self.voice), "--output_raw",
                 "--length_scale", f"{1.0 / max(self.speed, 0.1):.2f}"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL)
            player = subprocess.Popen(sink, stdin=piper.stdout,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
            if piper.stdout:
                piper.stdout.close()
            if piper.stdin:
                piper.stdin.write(text.encode("utf-8"))
                piper.stdin.close()
            self._process = player
        except (OSError, ValueError, BrokenPipeError):
            self._process = None

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
            except OSError:
                pass
        self._process = None


class EspeakTTS(TextToSpeech):
    """espeak-ng: robotic, tiny, and available in every distro."""

    name = "espeak"

    def __init__(self, binary: str = "espeak-ng", voice: str = "en-us",
                 speed: int = 165, pitch: int = 35):
        self.binary = binary
        self.voice = voice
        self.speed = int(speed)
        self.pitch = int(pitch)
        self._process: Optional[subprocess.Popen] = None

    @property
    def binary_path(self) -> Optional[str]:
        return shutil.which(self.binary) or shutil.which("espeak")

    @property
    def available(self) -> bool:
        return bool(self.binary_path)

    def status(self) -> str:
        return "ready" if self.available else f"binary '{self.binary}' not on PATH"

    def speak(self, text: str) -> None:
        binary = self.binary_path
        if not binary:
            return
        self.stop()
        try:
            self._process = subprocess.Popen(
                [binary, "-v", self.voice, "-s", str(self.speed),
                 "-p", str(self.pitch), text],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, ValueError):
            self._process = None

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
            except OSError:
                pass
        self._process = None


# --------------------------------------------------------------------------
# Reasoning
# --------------------------------------------------------------------------

class Reasoner(ABC):
    name = "none"

    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def ask(self, prompt: str, system: str, context: str,
            history: List[Dict[str, str]]) -> str: ...

    def status(self) -> str:
        return "ready" if self.available else "unavailable"


class NullReasoner(Reasoner):
    name = "none"

    @property
    def available(self) -> bool:
        return False

    def ask(self, prompt, system, context, history) -> str:
        return ""


def _post_json(url: str, payload: dict, timeout: float,
               headers: Optional[Dict[str, str]] = None) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class OllamaReasoner(Reasoner):
    """Local models through the Ollama HTTP API."""

    name = "ollama"

    def __init__(self, host: str = "http://127.0.0.1:11434",
                 model: str = "llama3.2", timeout: float = 60.0,
                 num_ctx: int = 8192):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = float(timeout)
        #: Context window to request. Ollama's own default is small enough
        #: that a long prompt — the game composer's library listing — is
        #: silently truncated, and the model never sees the start of it.
        self.num_ctx = int(num_ctx)
        #: "json" asks Ollama to constrain output to a JSON value. Off by
        #: default: conversation is prose. The composer evaluation measures
        #: whether turning it on for the composer is worth it.
        self.format = ""
        #: Token counts and timing from the last call, for measurement.
        self.last_usage: Dict[str, float] = {}

    @property
    def available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=2.0):
                return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def status(self) -> str:
        if not self.available:
            return f"no server at {self.host}"
        return f"ready · {self.model} @ {self.host}"

    def ask(self, prompt: str, system: str, context: str,
            history: List[Dict[str, str]]) -> str:
        messages = [{"role": "system", "content": f"{system}\n\nCurrent system state:\n{context}"}]
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.4, "num_ctx": self.num_ctx},
        }
        if self.format:
            payload["format"] = self.format
        result = _post_json(f"{self.host}/api/chat", payload, self.timeout)
        self.last_usage = {
            "prompt_tokens": result.get("prompt_eval_count", 0),
            "output_tokens": result.get("eval_count", 0),
            "seconds": result.get("total_duration", 0) / 1e9,
        }
        return str(result.get("message", {}).get("content", "")).strip()


class CloudReasoner(Reasoner):
    """Anthropic or any OpenAI-compatible endpoint."""

    name = "cloud"

    def __init__(self, provider: str = "anthropic", model: str = "claude-sonnet-4-5",
                 api_key_env: str = "ANTHROPIC_API_KEY", base_url: str = "",
                 timeout: float = 60.0):
        self.provider = provider
        self.model = model
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.timeout = float(timeout)

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def status(self) -> str:
        if not self.api_key:
            return f"${self.api_key_env} not set"
        return f"ready · {self.provider}/{self.model}"

    def ask(self, prompt: str, system: str, context: str,
            history: List[Dict[str, str]]) -> str:
        if not self.api_key:
            return ""
        full_system = f"{system}\n\nCurrent system state:\n{context}"
        if self.provider == "anthropic":
            url = (self.base_url or "https://api.anthropic.com") + "/v1/messages"
            messages = list(history) + [{"role": "user", "content": prompt}]
            result = _post_json(url, {
                "model": self.model,
                "max_tokens": 1024,
                "system": full_system,
                "messages": messages,
            }, self.timeout, {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            })
            usage = result.get("usage") or {}
            self.last_usage = {"prompt_tokens": usage.get("input_tokens", 0),
                               "output_tokens": usage.get("output_tokens", 0)}
            blocks = result.get("content") or []
            return "".join(b.get("text", "") for b in blocks
                           if isinstance(b, dict)).strip()

        url = (self.base_url or "https://api.openai.com/v1") + "/chat/completions"
        messages = [{"role": "system", "content": full_system}]
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        result = _post_json(url, {
            "model": self.model, "messages": messages, "max_tokens": 1024,
        }, self.timeout, {"Authorization": f"Bearer {self.api_key}"})
        usage = result.get("usage") or {}
        self.last_usage = {"prompt_tokens": usage.get("prompt_tokens", 0),
                           "output_tokens": usage.get("completion_tokens", 0)}
        choices = result.get("choices") or [{}]
        return str(choices[0].get("message", {}).get("content", "")).strip()
