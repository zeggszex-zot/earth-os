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
        # What the BROWSER button opens, in whatever browser the desktop
        # calls its default.
        "homepage": "https://duckduckgo.com/",
        "start_screen": "main",
        "fullscreen": True,
        "cursor": True,
        "fps": 30,
        # Draw a frame because something changed, not because a clock said
        # so. False restores the original fixed-rate frame clock exactly,
        # which is the way back if an adaptive frame ever misses something.
        "adaptive": True,
        # A floor under the idle rate, in frames per second. 0 means no
        # floor: a static, untouched screen is not redrawn at all. Set it to
        # 1 or 2 on a machine where something draws without saying so.
        "idle_fps": 0,
        # How long the full rate is held after the last input event, in
        # seconds, so a drag and the flash that follows a release are smooth.
        "interaction_grace": 0.45,
        # How often the hardware gauges are sampled, in seconds. Sampling is
        # not drawing: a frame follows only when a reading a screen shows has
        # actually changed.
        "telemetry_interval": 1.0,
        # How often screens are given a chance to notice their own news —
        # command output, a refreshed mailbox. Cheap; it is not a redraw.
        "tick_interval": 0.1,
        # Keep the last composited frame so a repaint GTK asks for on its own
        # is a blit rather than a second trip through the glow.
        "frame_cache": True,
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
        # The same as the file written on first run says. This said "ollama"
        # while that file said "cdos", so the very first launch — the one
        # before the file exists — ran a different brain from every later one.
        "llm": "cdos",
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
        # ESI phase 1: read a question by meaning ("which app is hogging my
        # RAM") when no phrase matches it as written. Questions only; an act
        # is never reached this way. docs/ESI_ARCHITECTURE.md.
        "lexical": True,
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
        "num_ctx": 8192,
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
    "computer": {
        # CDOS Computer (cdos-computer/): the corpus, the navigator, the
        # calculator and the Governor, asked after cdos-bridge and before any
        # model. See docs/COMPUTER.md.
        "enabled": True,
        # auto     = the cdos-api service's socket if it exists, else the
        #            computer's code run in this process
        # socket   = the service only
        # embedded = in this process only
        "mode": "auto",
        "socket": "/run/cdos/api.sock",
        "port": 0,                     # instead of a socket: 127.0.0.1 only
        "timeout": 10,
        # Where the computer's code is, for embedded mode. Blank = beside this
        # checkout, then /opt/cdos-computer/current.
        "source": "",
        # The computer's own config.toml, for embedded mode. Blank = its
        # defaults, with its log kept in this shell's data directory.
        "config": "",
        # False: when the corpus has no answer, the computer's NOT FOUND is
        # what is said, and a model may only propose a command. True: a
        # model's free answer is spoken instead, labelled as unverified.
        "free_answers": False,
    },
    "companion": {
        # The Companion Engine (docs/COMPANION.md): conversation, with CDOS
        # underneath it. Commands still go to the command path first.
        "enabled": True,
        # ollama = its own small-model client (one call per turn)
        # reasoner = the shell's [voice] llm reasoner, through its ask()
        "backend": "ollama",
        "model": "llama3.2:3b",       # Llama 3.2 3B Instruct, Q4_K_M
        "host": "",                   # blank = [ollama] host
        # How freely it talks (earth_os/companion/profile.py):
        # open     = like a conventional chat model: replies of several
        #            paragraphs, higher temperatures, a larger prompt with
        #            more of the conversation in it, its own general
        #            knowledge alongside the corpus, and a question the
        #            corpus cannot answer answered and marked unverified
        # grounded = Earth OS 2.0's behaviour: short replies, facts only
        #            from the corpus
        # Neither changes what never reaches a model (sensitive turns,
        # sums, commands) or lets the companion act.
        "profile": "open",
        # 0 = what the profile needs: 60 s and 4096 tokens grounded, 240 s
        # and 8192 open. A figure written here is used as written, and the
        # profile's prompt and reply lengths are scaled to fit it. Whatever
        # it is, it is the same for every call: changing it reloads the model.
        "timeout": 0,
        "num_ctx": 0,
        "keep_alive": "10m",
        # Take plainly conversational sentences away from a command that
        # only recognised a word of them ("going home after work").
        "guard": True,
        # File what you say about yourself ("I love astronomy") as a pending
        # note; it reaches no prompt until you say "keep note N".
        "observe": True,
        # worded = a model words the computer's fact, checked against the
        # evidence; verbatim = the computer's sentence, no model at all.
        "casual_facts": "worded",
        "idle_minutes": 30,
    },
    "mind": {
        # Earth Mind (docs/EARTH_MIND.md): the one coordination layer over
        # CDOS, the Companion, the system and game worlds. False puts the
        # shell back to the Companion Engine on its own.
        "enabled": True,
        # Which OS personality the terminal and the voice talk to. Others are
        # JSON files in ~/.config/earth-os/personalities/.
        "persona": "companion",
        # The model every voice shares, as provider:model[@host] — for
        # example "ollama:llama3.2:3b". Blank = the [companion] model, so a
        # machine configured before Earth Mind needs no new setting.
        "model": "",
        # A different (usually smaller) model for game characters. Blank =
        # the same one, loaded once.
        "game_model": "",
        # Where memories are kept. Blank = mind/memory.db in the data
        # directory; ":memory:" keeps nothing between runs.
        "memory": "",
        # Let the Companion put verified system findings into plain words.
        # False: a diagnosis is said as the findings themselves.
        "explain": True,
        # The terminal sends plain English through Earth Mind. False: it is
        # the translator and the shell only, as in 1.0.
        "terminal": True,
        # The game world "talk to <name>" looks in first. Blank = none.
        "world": "harbour",
        # Keep what was said with each personality between runs, in
        # conversation/<personality> of the memory store. Off by default:
        # nothing said to this computer outlives the session unless asked.
        "keep_conversations": False,
    },
    "world": {
        # The World Model (docs/WORLD_MODEL.md): what this computer knows
        # about itself, and what the person has let it remember.
        "enabled": True,
        "observe": True,              # notes others suggest are always pending
        "briefing": True,             # give a reasoner the world's shape
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
    "evolution": {
        # The System Evolution Center (docs/EVOLUTION_CENTER.md): where Earth
        # OS examines itself and its development is planned. It proposes; it
        # does not act. False removes it: nothing is built and nothing sampled.
        "enabled": True,
        # While the center is on screen: seconds between samples. It starts
        # no sampler of its own — these are the shell's existing readings,
        # looked at this often.
        "poll_interval": 2.0,
        # The full process list costs more than everything else together, so
        # it has its own, slower interval and is only read for the sections
        # that show it.
        "process_interval": 5.0,
        # While it is not on screen: one trend sample this often, from
        # readings the shell already holds. 0 = nothing at all while hidden.
        # A change applies at once: the running timer is replaced.
        "background_interval": 60.0,
        # Samples kept for behaviour over time, in memory only.
        "history": 360,
        # Let a model put findings into plain words. The findings themselves
        # never come from it, and the center works the same without one.
        "explain": True,
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
fps           = 30                  # the rate when something is actually moving
adaptive      = true                # draw on change, not on a clock; false = old engine
idle_fps      = 0                   # floor under the idle rate; 0 = do not redraw at rest
interaction_grace  = 0.45           # seconds of full rate after the last input
telemetry_interval = 1.0            # how often the gauges are sampled, in seconds
tick_interval      = 0.1            # how often screens notice their own news
frame_cache   = true                # reuse the composited frame when nothing changed
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

# CDOS Computer: facts from the corpus, sums from the calculator, and the
# Governor. Asked after cdos-bridge and before any model. A model never
# answers a factual question in its place unless free_answers = true.
[computer]
enabled      = true
mode         = "auto"               # auto | socket | embedded | off
socket       = "/run/cdos/api.sock" # the cdos-api service
source       = ""                   # embedded: blank = beside this checkout
free_answers = false

# The Companion Engine: conversation with a persistent identity. Facts come
# from CDOS Computer, style from GODCORE, memories from the World Model (only
# what you kept). It talks; it never acts. See docs/COMPANION.md.
[companion]
enabled      = true
model        = "llama3.2:3b"        # ollama pull llama3.2:3b
profile      = "open"               # open (long, free, like a chat model) | grounded (2.0: short, corpus only)
guard        = true                 # talk is not taken for a command
observe      = true                 # what you say about yourself → a pending note
casual_facts = "worded"             # worded (checked) | verbatim

[semantics]
# The learned layer. CDOS resolves what it knows; the model explains what it
# does not; the explanation is then written down and CDOS knows it too.
enabled   = true
learn     = true                    # false = use what is learned, add nothing
seed      = true                    # install the starter aliases on first run
execute_threshold = 0.75            # confidence needed to act without asking
seed_confidence   = 0.55            # where a newly learned reading starts

# Earth Mind: one coordination layer over CDOS (knowledge), the Companion
# (conversation), the system (real readings; operations wait for a press) and
# game worlds. See docs/EARTH_MIND.md.
[mind]
enabled    = true
persona    = "companion"            # companion | technician | robot | one of your own
model      = ""                     # provider:model[@host]; blank = the [companion] model
game_model = ""                     # blank = the same model, loaded once
explain    = true                   # the Companion explains verified system findings
terminal   = true                   # plain English in the terminal goes through Earth Mind
world      = "harbour"              # the demonstration game world; "" for none
memory     = ""                     # blank = mind/memory.db in the data directory; ":memory:" keeps nothing
keep_conversations = false          # true: what was said with each personality survives a restart

[world]
# The World Model: what this computer knows about itself and about you.
# Nothing about you is kept unless you said "remember that…" or answered
# "keep note N". Everything lives in world.json in the data directory;
# "forget everything about me", or deleting that file, removes all of it.
enabled  = true
observe  = true                     # let apps suggest notes (always pending)
briefing = true                     # give the model the world's shape

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
num_ctx = 8192                      # context window requested from Ollama

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

# The System Evolution Center: what Earth OS is doing, what needs improvement,
# what is missing and what to build next (docs/EVOLUTION_CENTER.md). It reads
# the machine and writes proposals. It changes nothing until you approve a
# proposal, and then only a setting from a short fixed list, backed up first.
[evolution]
enabled             = true
poll_interval       = 2.0      # seconds between samples while it is on screen
process_interval    = 5.0      # seconds between reads of the full process list
background_interval = 60.0     # one trend sample this often while hidden; 0 = none
history             = 360      # samples kept in memory for trends
explain             = true     # a model may reword findings; it is never their source

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
