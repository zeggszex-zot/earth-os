"""Ears and voice.

  ears   Vosk (offline speech recognition) fed by `arecord` while the button is held
  voice  Piper (offline neural TTS) if installed, else espeak-ng, else print

Both are optional: with neither, the computer runs as a terminal session.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


class Voice:
    def __init__(self, cfg):
        self.cfg = cfg
        self.kind = cfg.voice
        if self.kind == "auto":
            if shutil.which("piper") and Path(cfg.piper_model).exists():
                self.kind = "piper"
            elif shutil.which("espeak-ng"):
                self.kind = "espeak"
            else:
                self.kind = "print"
        self.rate = 22050
        if self.kind == "piper":
            meta = Path(cfg.piper_model + ".json")
            try:
                self.rate = json.loads(meta.read_text())["audio"]["sample_rate"]
            except (OSError, KeyError, ValueError):
                pass

    @staticmethod
    def for_speech(text: str) -> str:
        """Small fixes so a TTS reads naturally."""
        text = text.replace("I Ching", "Ee Ching")
        return re.sub(r"\s+", " ", text).strip()

    def say(self, text: str):
        print(f">> {text}", flush=True)
        spoken = self.for_speech(text)
        try:
            if self.kind == "piper":
                piper = subprocess.Popen(["piper", "--model", self.cfg.piper_model, "--output-raw"],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                aplay = subprocess.Popen(["aplay", "-q", "-r", str(self.rate), "-f", "S16_LE", "-t", "raw", "-"],
                                         stdin=piper.stdout, stderr=subprocess.DEVNULL)
                piper.stdin.write(spoken.encode())
                piper.stdin.close()
                aplay.wait()
                piper.wait()
            elif self.kind == "espeak":
                # flat, even machine delivery: neutral US voice, low pitch, short word gaps
                subprocess.run(["espeak-ng", "-v", "en-us", "-s", "165", "-p", "30", "-g", "4", spoken],
                               stderr=subprocess.DEVNULL, check=False)
        except OSError as e:
            print(f"[voice] {e}")


class Ears:
    """Push-to-talk speech recognition.  `listen(still_pressed)` records until the callback returns False."""

    RATE = 16000

    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel  # type: ignore
            SetLogLevel(-1)
            if Path(cfg.vosk_model).is_dir() and shutil.which("arecord"):
                self.model = Model(cfg.vosk_model)
                self._Rec = KaldiRecognizer
        except Exception as e:  # noqa: BLE001
            print(f"[ears] speech recognition off ({e})")

    @property
    def ready(self) -> bool:
        return self.model is not None

    def listen(self, still_pressed, max_seconds: float = 12.0) -> str:
        rec = self._Rec(self.model, self.RATE)
        cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(self.RATE), "-c", "1", "-t", "raw"]
        if self.cfg.mic_device:
            cmd[1:1] = ["-D", self.cfg.mic_device]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        chunk = self.RATE // 5 * 2      # 0.2 s of 16-bit audio
        read = 0
        try:
            while still_pressed() and read < max_seconds * self.RATE * 2:
                data = proc.stdout.read(chunk)
                if not data:
                    break
                read += len(data)
                rec.AcceptWaveform(data)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
        return json.loads(rec.FinalResult()).get("text", "").strip()
