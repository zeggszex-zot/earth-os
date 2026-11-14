"""Microphone capture with a simple energy gate.

Streams 16 kHz mono PCM from whichever recorder the machine has (PipeWire,
PulseAudio or ALSA), watches short-term RMS to find the start and end of an
utterance, and writes each utterance to a temporary WAV for the STT driver.

An energy gate is crude next to a neural VAD, but it has no dependencies, adds
no latency, and for a machine sitting in a room waiting to be spoken to it is
entirely adequate.
"""

from __future__ import annotations

import array
import math
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable, List, Optional

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2                      # s16le
CHUNK_MS = 30
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000
CHUNK_BYTES = CHUNK_FRAMES * SAMPLE_WIDTH * CHANNELS

RECORDERS: List[List[str]] = [
    ["pw-record", "--rate", str(SAMPLE_RATE), "--channels", str(CHANNELS),
     "--format", "s16", "-"],
    ["parecord", f"--rate={SAMPLE_RATE}", f"--channels={CHANNELS}",
     "--format=s16le", "--raw"],
    ["arecord", "-q", "-r", str(SAMPLE_RATE), "-c", str(CHANNELS),
     "-f", "S16_LE", "-t", "raw"],
]


def detect_recorder() -> Optional[List[str]]:
    for argv in RECORDERS:
        if shutil.which(argv[0]):
            return argv
    return None


def with_device(argv: List[str], device: str) -> List[str]:
    """Point a recorder command at one specific input.

    Each recorder spells this differently, and two of the three were being
    given no device at all — so a machine whose default source was the wrong
    one (a webcam mic that is present but not default, say) recorded silence
    no matter what the config said.
    """
    argv = list(argv)
    if not device or device == "default":
        return argv
    tool = argv[0]
    if tool == "pw-record":
        argv[1:1] = ["--target", device]
    elif tool == "parecord":
        argv[1:1] = [f"--device={device}"]
    elif tool == "arecord":
        argv[1:1] = ["-D", device]
    return argv


def list_input_devices() -> List[str]:
    """Names of the capture sources this machine offers, for the doctor.

    Empty when nothing can enumerate them; that is not an error, it just means
    the only option is whatever the system considers default.
    """
    found: List[str] = []
    if shutil.which("pactl"):
        try:
            out = subprocess.run(["pactl", "list", "short", "sources"],
                                 capture_output=True, text=True, timeout=5)
            for line in out.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and not parts[1].endswith(".monitor"):
                    found.append(parts[1])
        except (OSError, subprocess.SubprocessError):
            pass
    if not found and shutil.which("arecord"):
        try:
            out = subprocess.run(["arecord", "-L"], capture_output=True,
                                 text=True, timeout=5)
            for line in out.stdout.splitlines():
                if line and not line[0].isspace():
                    found.append(line.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    return found


def rms(chunk: bytes) -> float:
    """Root-mean-square of a signed 16-bit chunk, normalised to 0..1."""
    if not chunk:
        return 0.0
    samples = array.array("h")
    samples.frombytes(chunk[:len(chunk) - (len(chunk) % 2)])
    if not samples:
        return 0.0
    total = sum(float(s) * float(s) for s in samples)
    return math.sqrt(total / len(samples)) / 32768.0


class Microphone:
    """Background capture thread that emits complete utterances as WAV paths."""

    def __init__(self, on_utterance: Callable[[Path], None],
                 on_level: Optional[Callable[[float], None]] = None,
                 threshold: float = 0.018,
                 silence_ms: int = 700,
                 min_speech_ms: int = 300,
                 max_speech_ms: int = 15000,
                 preroll_ms: int = 300,
                 device: str = "default"):
        self.on_utterance = on_utterance
        self.on_level = on_level
        self.threshold = threshold
        self.silence_chunks = max(silence_ms // CHUNK_MS, 1)
        self.min_chunks = max(min_speech_ms // CHUNK_MS, 1)
        self.max_chunks = max(max_speech_ms // CHUNK_MS, 1)
        self.preroll_chunks = max(preroll_ms // CHUNK_MS, 1)
        self.device = device

        self.recorder = detect_recorder()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._process: Optional[subprocess.Popen] = None
        self.level = 0.0
        self.error = ""
        self.noise_floor = threshold

    @property
    def available(self) -> bool:
        return self.recorder is not None

    def status(self) -> str:
        if not self.recorder:
            return "no recorder (pw-record/parecord/arecord)"
        state = "listening" if self.running else "idle"
        return f"{self.recorder[0]} · {state}"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    def start(self) -> bool:
        if self.running or not self.recorder:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="cdos-mic")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._thread = None
        self.level = 0.0

    # ------------------------------------------------------------------
    def _run(self) -> None:
        argv = with_device(list(self.recorder or []), self.device)
        try:
            self._process = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except (OSError, ValueError) as exc:
            self.error = str(exc)
            return

        stream = self._process.stdout
        if stream is None:
            return

        preroll: List[bytes] = []
        speech: List[bytes] = []
        silence_run = 0
        in_speech = False

        try:
            while not self._stop.is_set():
                chunk = stream.read(CHUNK_BYTES)
                if not chunk:
                    break
                level = rms(chunk)
                self.level = level
                if self.on_level:
                    self.on_level(level)

                if not in_speech:
                    # Track a slow noise floor so the gate adapts to the room.
                    self.noise_floor = self.noise_floor * 0.995 + level * 0.005

                gate = max(self.threshold, self.noise_floor * 2.6)

                if level >= gate:
                    if not in_speech:
                        in_speech = True
                        speech = list(preroll)
                    speech.append(chunk)
                    silence_run = 0
                    if len(speech) >= self.max_chunks:
                        self._emit(speech)
                        speech, in_speech, silence_run = [], False, 0
                elif in_speech:
                    speech.append(chunk)
                    silence_run += 1
                    if silence_run >= self.silence_chunks:
                        if len(speech) >= self.min_chunks + self.silence_chunks:
                            self._emit(speech)
                        speech, in_speech, silence_run = [], False, 0
                else:
                    preroll.append(chunk)
                    del preroll[:-self.preroll_chunks]
        finally:
            if self._process and self._process.poll() is None:
                try:
                    self._process.terminate()
                except OSError:
                    pass
            self.level = 0.0

    def _emit(self, chunks: List[bytes]) -> None:
        payload = b"".join(chunks)
        handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        path = Path(handle.name)
        handle.close()
        try:
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(payload)
            self.on_utterance(path)
        except (OSError, wave.Error):
            pass
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
