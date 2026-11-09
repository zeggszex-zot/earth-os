"""Sound effects.

Plays the original's ``beep`` / ``ack`` / ``computer`` / ``redalert`` WAVs
through whichever command-line player the machine actually has, so the shell
carries no audio library dependency.  PipeWire, PulseAudio and bare ALSA are
all handled; if none is present, sound quietly does nothing rather than
breaking the UI.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional

#: Player command templates, best first.  ``{path}`` is substituted.
PLAYERS: List[List[str]] = [
    ["pw-play", "{path}"],
    ["paplay", "{path}"],
    ["aplay", "-q", "{path}"],
    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "{path}"],
]


def detect_player() -> Optional[List[str]]:
    for argv in PLAYERS:
        if shutil.which(argv[0]):
            return argv
    return None


class SoundPlayer:
    """Fire-and-forget playback of the bundled effect set."""

    def __init__(self, sounds_dir: Path, enabled: bool = True, volume: float = 0.6):
        self.dir = Path(sounds_dir)
        self.enabled = enabled
        self.volume = max(0.0, min(1.0, volume))
        self.player = detect_player()
        self._sounds: Dict[str, Path] = {
            p.stem.lower(): p for p in self.dir.glob("*.wav")
        }
        self._current: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.enabled and self.player is not None and bool(self._sounds)

    def names(self) -> List[str]:
        return sorted(self._sounds)

    def play(self, name: str, blocking: bool = False) -> None:
        if not self.available:
            return
        path = self._sounds.get(name.lower())
        if path is None:
            return
        argv = [part.format(path=str(path)) for part in (self.player or [])]
        # paplay and pw-play take a linear volume in 0..65536.
        if argv[0] in ("paplay", "pw-play") and self.volume < 0.999:
            argv.insert(1, f"--volume={int(self.volume * 65536)}")

        def run() -> None:
            try:
                process = subprocess.Popen(
                    argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, ValueError):
                return
            with self._lock:
                self._current = process
            process.wait()
            with self._lock:
                if self._current is process:
                    self._current = None

        if blocking:
            run()
        else:
            threading.Thread(target=run, daemon=True, name=f"snd-{name}").start()

    def stop(self) -> None:
        with self._lock:
            process = self._current
            self._current = None
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
