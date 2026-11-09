"""The voice engine: wake word, recognition, dispatch, speech and reasoning.

Flow for one utterance::

    microphone -> energy gate -> WAV -> speech-to-text -> text
        |
        +-- starts with the wake word (or continuous mode is on)?
              |
              +-- matches a vCommands action?  -> run it, chirp, acknowledge
              +-- otherwise                    -> reasoning backend -> speak

Recognition and reasoning both run off the UI thread; results are handed back
through ``Shell.say`` which marshals onto the main loop.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .backends import (
    CloudReasoner, EspeakTTS, NullReasoner, NullSTT, NullTTS, OllamaReasoner,
    PiperTTS, Reasoner, SpeechToText, TextToSpeech, VoskSTT, WhisperSTT,
)
from .capture import Microphone


class VoiceEngine:
    """Owns the microphone and the three pluggable backends."""

    def __init__(self, shell):
        self.shell = shell
        self.config = shell.config
        self.stt: SpeechToText = NullSTT()
        self.tts: TextToSpeech = NullTTS()
        self.reasoner: Reasoner = NullReasoner()
        self.mic: Optional[Microphone] = None
        self.continuous = bool(self.config.get("voice", "continuous", False))
        self.level = 0.0
        self._history: List[Dict[str, str]] = []
        self._lock = threading.Lock()
        self.last_error = ""
        self.reconfigure()

    # ------------------------------------------------------------------
    def reconfigure(self) -> None:
        cfg = self.config
        self.continuous = bool(cfg.get("voice", "continuous", False))

        stt_name = str(cfg.get("voice", "stt", "whisper"))
        if stt_name == "whisper":
            block = cfg.section("whisper")
            self.stt = WhisperSTT(
                binary=str(block.get("binary", "whisper-cli")),
                model=str(block.get("model", "")),
                language=str(block.get("language", "en")),
                threads=int(block.get("threads", 4)),
            )
        elif stt_name == "vosk":
            self.stt = VoskSTT(model_dir=str(cfg.section("vosk").get("model", "")))
        else:
            self.stt = NullSTT()

        tts_name = str(cfg.get("voice", "tts", "piper"))
        if tts_name == "piper":
            block = cfg.section("piper")
            self.tts = PiperTTS(binary=str(block.get("binary", "piper")),
                                voice=str(block.get("voice", "")),
                                speed=float(block.get("speed", 1.0)))
            if not self.tts.available:
                fallback = EspeakTTS(**_espeak_kwargs(cfg))
                if fallback.available:
                    self.tts = fallback
        elif tts_name == "espeak":
            self.tts = EspeakTTS(**_espeak_kwargs(cfg))
        else:
            self.tts = NullTTS()

        self.reasoner = self._build_reasoner(
            str(cfg.get("voice", "llm", "cdos")))

        if self.mic is None:
            self.mic = Microphone(on_utterance=self._on_utterance,
                                  on_level=self._on_level,
                                  device=str(cfg.get("voice", "input_device",
                                                     "default")))

    # ------------------------------------------------------------------
    def _build_reasoner(self, name: str) -> Reasoner:
        """Construct the named reasoner.

        ``cdos`` wraps one of the others: CDOS decides, the model only finds
        words for what CDOS produced.
        """
        cfg = self.config
        if name == "ollama":
            block = cfg.section("ollama")
            return OllamaReasoner(
                host=str(block.get("host", "http://127.0.0.1:11434")),
                model=str(block.get("model", "llama3.2")),
                timeout=float(block.get("timeout", 60)),
            )
        if name == "cloud":
            block = cfg.section("cloud")
            return CloudReasoner(
                provider=str(block.get("provider", "anthropic")),
                model=str(block.get("model", "claude-sonnet-4-5")),
                api_key_env=str(block.get("api_key_env", "ANTHROPIC_API_KEY")),
                base_url=str(block.get("base_url", "")),
                timeout=float(block.get("timeout", 60)),
            )
        if name == "cdos":
            from .cdos_backend import CdosBridge, CdosReasoner
            block = cfg.section("cdos")
            bridge = CdosBridge(
                root=str(block.get("root", "")),
                bridge=str(block.get("bridge", "cdos-bridge")),
                cli=str(block.get("cli", "cdos")),
                timeout=float(block.get("timeout", 20)),
            )
            wording = str(block.get("wording", "ollama"))
            inner = self._build_reasoner(wording) if wording != "none" else None
            if inner is not None and not inner.available:
                inner = None          # CDOS alone still answers, just flatly
            return CdosReasoner(
                bridge, inner=inner,
                narrate=bool(block.get("narrate", True)),
                use_godcore=bool(block.get("godcore", True)),
            )
        return NullReasoner()

    # ------------------------------------------------------------------
    @property
    def pending(self):
        """A proposal awaiting the user's choice or confirmation, if any."""
        return getattr(self.reasoner, "pending", None)

    def pending_options(self) -> List[str]:
        proposal = self.pending
        return list(proposal.alternatives) if proposal else []

    def choose(self, command_text: str) -> str:
        chooser = getattr(self.reasoner, "choose", None)
        if chooser is None:
            return ""
        return chooser(command_text)

    def confirm_pending(self) -> str:
        proposal = self.pending
        confirm = getattr(self.reasoner, "confirm", None)
        if proposal is None or confirm is None:
            return ""
        return confirm(proposal)

    def clear_pending(self) -> None:
        if hasattr(self.reasoner, "pending"):
            self.reasoner.pending = None    # type: ignore[attr-defined]

    def godcore_event(self, event: str) -> None:
        """Fold a shell occasion into GODCORE, if CDOS is the backend."""
        bridge = getattr(self.reasoner, "bridge", None)
        if bridge is None:
            return
        if not bool(self.config.section("cdos").get("godcore_events", True)):
            return
        try:
            bridge.godcore_event(event)
        except Exception:
            pass

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return bool(self.mic and self.mic.available and self.stt.available)

    @property
    def can_reason(self) -> bool:
        return self.reasoner.available

    @property
    def can_speak(self) -> bool:
        return self.tts.available

    def status_rows(self) -> List[Tuple[str, str]]:
        return [
            ("MICROPHONE", self.mic.status() if self.mic else "not constructed"),
            ("SPEECH TO TEXT", f"{self.stt.name}: {self.stt.status()}"),
            ("TEXT TO SPEECH", f"{self.tts.name}: {self.tts.status()}"),
            ("REASONING", f"{self.reasoner.name}: {self.reasoner.status()}"),
            ("WAKE WORD", ", ".join(self.shell.dispatcher.vocabulary.wake_words)),
            ("MODE", "continuous" if self.continuous else "wake word"),
            ("LAST ERROR", self.last_error or "none"),
        ]

    # ------------------------------------------------------------------
    def start_listening(self) -> None:
        if not self.mic:
            return
        if not self.mic.available:
            self.last_error = "no recorder available"
            self.shell.say("Audio input unavailable.", speak=False)
            return
        if not self.stt.available:
            self.last_error = self.stt.status()
            self.shell.say(f"Speech recognition unavailable. {self.stt.status()}",
                           speak=False)
            return
        if self.mic.start():
            self.shell.listening = True
            if self.config.get("voice", "acknowledge", True):
                self.shell.play("computer")
            self.shell.redraw()

    def stop_listening(self) -> None:
        if self.mic:
            self.mic.stop()
        self.shell.listening = False
        self.shell.redraw()

    def shutdown(self) -> None:
        self.stop_listening()
        self.tts.stop()

    # ------------------------------------------------------------------
    def _on_level(self, level: float) -> None:
        self.level = level

    def _on_utterance(self, wav_path: Path) -> None:
        try:
            text = self.stt.transcribe(wav_path)
        except Exception as exc:                       # backend crash
            self.last_error = f"stt: {exc}"
            return
        text = (text or "").strip()
        if not text or len(text) < 2:
            return
        # whisper.cpp emits bracketed non-speech markers; drop those.
        if text.startswith("[") and text.endswith("]"):
            return

        vocabulary = self.shell.dispatcher.vocabulary
        had_wake, remainder = vocabulary.strip_wake(text)
        if had_wake:
            if not remainder:
                self.shell.play("ack")
                self.shell.say("Listening.")
                return
            payload = remainder
        elif self.continuous:
            payload = text
        else:
            return                                     # not addressed to us

        self.shell.handle_utterance(payload)

    # ------------------------------------------------------------------
    def speak(self, text: str) -> None:
        if self.tts.available:
            try:
                self.tts.speak(text)
            except Exception as exc:
                self.last_error = f"tts: {exc}"

    def reason(self, prompt: str, context: str) -> str:
        """Blocking call into the reasoning backend; run it on a worker."""
        if not self.reasoner.available:
            return str(self.config.get("persona", "unknown", "Unable to comply."))
        system = str(self.config.get("persona", "system_prompt", ""))
        with self._lock:
            history = list(self._history)
        try:
            answer = self.reasoner.ask(prompt, system, context, history)
        except Exception as exc:
            self.last_error = f"llm: {exc}"
            return f"Reasoning subsystem unreachable. {exc}"
        with self._lock:
            self._history.append({"role": "user", "content": prompt})
            self._history.append({"role": "assistant", "content": answer})
            limit = int(self.config.get("persona", "max_history", 20))
            del self._history[:-limit * 2]
        return answer

    def reset_history(self) -> None:
        with self._lock:
            self._history.clear()


def _espeak_kwargs(cfg) -> Dict[str, object]:
    block = cfg.section("espeak")
    return {
        "binary": str(block.get("binary", "espeak-ng")),
        "voice": str(block.get("voice", "en-us")),
        "speed": int(block.get("speed", 165)),
        "pitch": int(block.get("pitch", 35)),
    }
