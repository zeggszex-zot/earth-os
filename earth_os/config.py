"""Configuration: defaults, TOML file, environment overrides.

Config lives at ``$XDG_CONFIG_HOME/earth-os/config.toml`` (usually
``~/.config/earth-os/config.toml``).  A commented default file is written on
first run so the machine is configurable without reading the source.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "earth-os"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "earth-os"


DEFAULTS: Dict[str, Any] = {
    "shell": {
        "palette": "Earth OS",
        # The interface face: "VT323" (EARTH OS) or "LCARS" (the original).
        "font": "VT323",
        # Scanlines, glow and a darkened edge, like the tube it imitates.
        "phosphor": True,
        "language": "Earth OS",
        "computer_name": "EARTH OS",
        # Shown beside the computer name in the header. Empty means "use the
        # account name of whoever is logged in", which is what it is for.
        "operator": "",
        "start_screen": "main",
        "fullscreen": True,
        "cursor": True,
        "fps": 30,
        "sounds": True,
        "volume": 0.6,
        "shell_mode": False,
        # What DEACTIVATE hands the machine over to.
        "exit_command": "startx",
        "track_recent": True,
        "recent_limit": 60,
    },
    "voice": {
        "enabled": True,
        "wake_word": "computer",
        "continuous": False,
        "input_device": "default",
        "stt": "whisper",
        "tts": "piper",
        "llm": "ollama",
        "acknowledge": True,
    },
    "semantics": {
        # The learned layer: aliases and rules the model teaches this machine
        # so the same explanation is not re-derived on every utterance.
        "enabled": True,
        "learn": True,
        "seed": True,
        # Confidence a learned reading must reach before it runs without
        # being confirmed. An effectful command ignores this and always asks.
        "execute_threshold": 0.75,
        # What a freshly learned relationship starts at — deliberately below
        # the execution threshold, so its first uses are questions.
        "seed_confidence": 0.55,
    },
    "whisper": {
        "binary": "whisper-cli",
        "model": "~/.local/share/earth-os/models/ggml-base.en.bin",
        "language": "en",
        "threads": 4,
    },
    "vosk": {
        "model": "~/.local/share/earth-os/models/vosk-model-small-en-us",
    },
    "piper": {
        "binary": "piper",
        "voice": "~/.local/share/earth-os/voices/en_US-amy-medium.onnx",
        "speed": 1.0,
    },
    "espeak": {
        "binary": "espeak-ng",
        "voice": "en-us",
        "speed": 165,
        "pitch": 35,
    },
    "ollama": {
        "host": "http://127.0.0.1:11434",
        "model": "llama3.2",
        "timeout": 60,
    },
    "cloud": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url": "",
        "timeout": 60,
    },
    "cdos": {
        # Where the .cdos journals live; the binaries are run with this as
        # their working directory.
        "root": "~/.local/share/earth-os/cdos",
        "bridge": "cdos-bridge",
        "cli": "cdos",
        "timeout": 20,
        # Which reasoner puts CDOS's bare facts into sentences.  "none" keeps
        # the shell fully deterministic: it will answer, just more flatly.
        "wording": "ollama",
        "narrate": True,
        "godcore": True,
        # Fold shell occasions into GODCORE so its pulse advances with use.
        "godcore_events": True,
    },
    "persona": {
        "system_prompt": (
            "You are EARTH OS, the computer of a workstation running CDOS. "
            "You answer in the clipped, factual register of an onboard "
            "computer: brief, precise, no filler, no pleasantries. State facts "
            "plainly. If a request cannot be completed, say so and say why in "
            "one sentence. Never invent system readings; if you were not given "
            "a value, say it is unavailable."
        ),
        "greeting": "Computer online. Awaiting input.",
        "unknown": "Unable to comply. Command not recognised.",
        "max_history": 20,
    },
    "windows": {
        # auto | sway | wlrctl | wmctrl | none
        "backend": "auto",
        # frame  = programs live inside the LCARS chrome (needs sway or wmctrl)
        # screen  = programs cover the whole display
        # none    = the shell does not move anything
        "mode": "frame",
        # Place a program in the frame the moment it opens, however it was
        # started — not only when launched from the LCARS menu.
        "auto_frame": True,
        # Fullscreen a program when switching to it (mode = "screen" only).
        "fullscreen_launched": True,
        # Drop the shell out of fullscreen when handing over.
        "step_aside": True,
        # Seconds between window-list polls. Each poll is a subprocess, so
        # this is a floor on how often one runs however many things ask.
        "poll_interval": 1.2,
        # Programs shown in the strip along the top of every screen.
        "strip_limit": 8,
    },
    "claude": {
        # Claude Code, built in: the CLAUDE CODE screen, "Claude, <request>"
        # by voice, and the control bridge. See docs/CLAUDE_CODE.md.
        "enabled": True,
        "command": "claude",
        # Blank = Claude Code's own default model.
        "model": "",
        # Blank = this checkout if it is a git tree, else
        # ~/.local/share/earth-os/src (created on first request).
        "workspace": "",
        # The interpreter the gate runs the suite under. AGENTS.md: 3.12+.
        "python": "/usr/bin/python3.12",
        # Judge every turn's changes as soon as it finishes.
        "auto_verify": True,
    },
    "colors": {},
    "programs": [],
}

DEFAULT_TOML = '''# EARTH OS shell configuration
# Written on first run.  Delete a key to fall back to its built-in default.

[shell]
palette       = "Earth OS"          # any .lxcp in assets/palettes or this directory
font          = "VT323"             # VT323 (EARTH OS CRT) | LCARS (the original face)
phosphor      = true                # CRT glow and scanlines; false for a flat screen
language      = "Earth OS"          # any .lng in assets/lang: Earth OS, Standard, TNG
computer_name = "EARTH OS"
operator      = ""                  # blank = the logged-in account's name
fullscreen    = true
cursor        = true                # false hides the pointer, for touch panels
fps           = 30
sounds        = true
volume        = 0.6
shell_mode    = false
exit_command  = "startx"            # what DEACTIVATE hands the machine to
track_recent  = true                # remember which programs you keep opening
recent_limit  = 60

[voice]
enabled     = true
wake_word   = "computer"
continuous  = false                 # true = no wake word needed
stt         = "whisper"             # whisper | vosk | none
tts         = "piper"               # piper | espeak | none
llm         = "cdos"                # cdos | ollama | cloud | none
acknowledge = true                  # play the acknowledge chirp on wake

# CDOS is the preferred backend: it resolves what it can deterministically,
# reports ambiguity instead of guessing, and refuses to let a model change
# the machine.  A model is used only to put its bare facts into sentences.
[cdos]
root    = "~/.local/share/earth-os/cdos"   # where the .cdos journals live
bridge  = "cdos-bridge"
cli     = "cdos"
wording = "ollama"                  # ollama | cloud | none
narrate = true
godcore = true

[semantics]
# The learned layer. CDOS resolves what it knows; the model explains what it
# does not; the explanation is then written down and CDOS knows it too.
enabled   = true
learn     = true                    # false = use what is learned, add nothing
seed      = true                    # install the starter aliases on first run
execute_threshold = 0.75            # confidence needed to act without asking
seed_confidence   = 0.55            # where a newly learned reading starts

[whisper]
binary  = "whisper-cli"
model   = "~/.local/share/earth-os/models/ggml-base.en.bin"
threads = 4

[piper]
binary = "piper"
voice  = "~/.local/share/earth-os/voices/en_US-amy-medium.onnx"

[ollama]
host  = "http://127.0.0.1:11434"
model = "llama3.2"

[cloud]
provider    = "anthropic"
model       = "claude-sonnet-4-5"
api_key_env = "ANTHROPIC_API_KEY"

# Reaching other running programs needs the compositor's cooperation.
# sway and labwc offer it; GNOME and KDE on Wayland do not. On X11, wmctrl.
[windows]
backend             = "auto"   # auto | sway | wlrctl | wmctrl | none
mode                = "frame"  # frame | screen | none
auto_frame          = true     # frame a program as soon as it opens
fullscreen_launched = true     # mode = "screen": fullscreen on switch
step_aside          = true     # mode = "screen": leave fullscreen when handing over
poll_interval       = 1.2      # seconds between window-list polls
strip_limit         = 8        # programs shown in the top strip

# Claude Code, built into the shell (docs/CLAUDE_CODE.md). It edits an
# editable copy of CDOS; every change must pass the full test suite before it
# is kept, and anything that changes the machine waits for a press.
[claude]
enabled     = true
command     = "claude"             # the Claude Code CLI; sign in once from a terminal
model       = ""                   # blank = Claude Code's default
workspace   = ""                   # blank = this checkout, or ~/.local/share/earth-os/src
python      = "/usr/bin/python3.12" # what the gate runs the suite under
auto_verify = true                 # judge each turn's changes when it finishes

# Override individual palette slots by name if you disagree with the mapping.
# Slots: accent1 accent2 accent3 accent4 accent5 accent6 text alert frame
#        highlight select
[colors]
# accent4 = "#FFCC00"

# Buttons for the PERSONAL PROGRAMS panel.
# [[programs]]
# label = "TERMINAL"
# exec  = "foot"
# color = "accent2"
'''


@dataclass
class Config:
    values: Dict[str, Any] = field(default_factory=lambda: _deep_copy(DEFAULTS))
    path: Path = field(default_factory=lambda: config_dir() / "config.toml")

    @classmethod
    def load(cls, path: Path | str | None = None, write_default: bool = True) -> "Config":
        cfg = cls()
        cfg.path = Path(path) if path else (config_dir() / "config.toml")
        if cfg.path.exists() and tomllib is not None:
            try:
                with cfg.path.open("rb") as fh:
                    _merge(cfg.values, tomllib.load(fh))
            except Exception as exc:  # malformed config must not brick the shell
                print(f"[earth-os] ignoring bad config {cfg.path}: {exc}")
        elif write_default:
            try:
                cfg.path.parent.mkdir(parents=True, exist_ok=True)
                cfg.path.write_text(DEFAULT_TOML, encoding="utf-8")
            except OSError:
                pass
        cfg.apply_environment()
        return cfg

    def apply_environment(self) -> None:
        """``EARTH_OS_<SECTION>_<KEY>`` overrides any config value."""
        for key, raw in os.environ.items():
            if not key.startswith("EARTH_OS_"):
                continue
            parts = key[len("EARTH_OS_"):].lower().split("_", 1)
            if len(parts) != 2:
                continue
            section, name = parts
            if section in self.values and isinstance(self.values[section], dict):
                self.values[section][name] = _coerce(raw)

    # -- access ------------------------------------------------------------
    def get(self, section: str, key: str, default: Any = None) -> Any:
        block = self.values.get(section)
        if isinstance(block, dict) and key in block:
            return block[key]
        fallback = DEFAULTS.get(section, {})
        if isinstance(fallback, dict):
            return fallback.get(key, default)
        return default

    def section(self, name: str) -> Dict[str, Any]:
        block = self.values.get(name)
        return dict(block) if isinstance(block, dict) else {}

    def path_for(self, section: str, key: str) -> Path:
        return Path(os.path.expanduser(str(self.get(section, key, "")))).resolve()

    def programs(self) -> List[Dict[str, Any]]:
        progs = self.values.get("programs")
        return list(progs) if isinstance(progs, list) else []


def _deep_copy(src: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in src.items():
        out[key] = _deep_copy(value) if isinstance(value, dict) else (
            list(value) if isinstance(value, list) else value)
    return out


def _merge(base: Dict[str, Any], extra: Dict[str, Any]) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def _coerce(raw: str) -> Any:
    low = raw.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw
