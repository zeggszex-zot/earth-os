"""Self-diagnosis for ``earth-os --doctor``.

A GUI program that fails when launched from a menu fails invisibly: there is no
terminal to print to, so "nothing happens" is all the user sees.  This module
checks every prerequisite in order and reports what it finds, so the shell can
explain itself instead of dying silently.

Every check is wrapped: the doctor must never be the thing that crashes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

OK = "  ok  "
WARN = " warn "
FAIL = " FAIL "


class Report:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str]] = []
        self.failures = 0
        self.warnings = 0

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))
        if status == FAIL:
            self.failures += 1
        elif status == WARN:
            self.warnings += 1

    def ok(self, name: str, detail: str = "") -> None:
        self.add(OK, name, detail)

    def warn(self, name: str, detail: str = "") -> None:
        self.add(WARN, name, detail)

    def fail(self, name: str, detail: str = "") -> None:
        self.add(FAIL, name, detail)

    def render(self) -> str:
        width = max((len(name) for _, name, _ in self.rows), default=10)
        lines = []
        for status, name, detail in self.rows:
            lines.append(f"[{status}] {name.ljust(width)}  {detail}")
        return "\n".join(lines)


def _safe(report: Report, name: str, fn: Callable[[], Tuple[str, str]]) -> None:
    try:
        status, detail = fn()
        report.add(status, name, detail)
    except Exception as exc:                       # a check must never crash
        report.fail(name, f"check itself failed: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------

def check_python() -> Tuple[str, str]:
    version = ".".join(str(v) for v in sys.version_info[:3])
    detail = f"{sys.executable} ({version})"
    if sys.version_info < (3, 11):
        return (FAIL, detail + " - need 3.11 or newer")
    return (OK, detail)


def check_cairo() -> Tuple[str, str]:
    try:
        import cairo
    except Exception as exc:
        return (FAIL, f"pycairo missing: {exc}. Install python3-cairo.")
    return (OK, f"pycairo {getattr(cairo, 'version', '?')}")


def check_gi() -> Tuple[str, str]:
    try:
        import gi
    except Exception as exc:
        return (FAIL, f"PyGObject missing: {exc}. Install python3-gi.")
    return (OK, f"PyGObject {getattr(gi, '__version__', '?')}")


def check_gtk() -> Tuple[str, str]:
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
    except Exception as exc:
        return (FAIL,
                f"GTK 4 unavailable: {exc}. Install gir1.2-gtk-4.0 / gtk4.")
    version = f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
    return (OK, f"GTK {version}")


def check_display() -> Tuple[str, str]:
    wayland = os.environ.get("WAYLAND_DISPLAY")
    display = os.environ.get("DISPLAY")
    session = os.environ.get("XDG_SESSION_TYPE", "unset")
    if wayland:
        return (OK, f"Wayland ({wayland}), XDG_SESSION_TYPE={session}")
    if display:
        return (OK, f"X11 ({display}), XDG_SESSION_TYPE={session}")
    return (FAIL,
            "no WAYLAND_DISPLAY and no DISPLAY - there is no graphical session "
            "to open a window on. Run this from inside your desktop session, "
            "not from a TTY or over plain ssh.")


def check_can_open_a_window() -> Tuple[str, str]:
    """The decisive test: can GTK actually reach a display and build a window?

    ``Gtk.init_check()`` alone is not trustworthy here — it can report success
    on a machine with no display at all — so this also insists that a display
    is named in the environment and that a real window object can be created.
    """
    if not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        return (FAIL,
                "no display in the environment, so no window can be opened. "
                "See the 'display' line above.")
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gtk
    except Exception as exc:
        return (FAIL, f"GTK import failed: {exc}")
    try:
        if not Gtk.init_check():
            return (FAIL,
                    "Gtk.init_check() failed - GTK cannot reach the display. "
                    "On Wayland check WAYLAND_DISPLAY and XDG_RUNTIME_DIR; "
                    "over ssh you need X forwarding.")
        display = Gdk.Display.get_default()
        if display is None:
            return (FAIL,
                    "GTK initialised but no display was opened. Check "
                    "WAYLAND_DISPLAY / DISPLAY and XDG_RUNTIME_DIR.")
        window = Gtk.Window()
        window.set_default_size(64, 64)
        window.destroy()
    except Exception as exc:
        return (FAIL, f"GTK could not build a window: {type(exc).__name__}: {exc}")
    backend = os.environ.get("GDK_BACKEND", "auto")
    name = display.get_name() or "?"
    return (OK, f"yes (display {name}, GDK_BACKEND={backend})")


def check_font() -> Tuple[str, str]:
    from .theme import ASSETS, ensure_font
    path = ASSETS / "fonts" / "lcars.ttf"
    if not path.exists():
        return (WARN, f"bundled font missing at {path}; a fallback will be used")
    ensure_font()
    return (OK, str(path))


def check_assets() -> Tuple[str, str]:
    from .theme import Palette, Strings
    palettes = Palette.list_builtin()
    languages = Strings.list_builtin()
    if not palettes or not languages:
        return (FAIL,
                "asset files missing - the package was copied without its "
                "assets directory")
    return (OK, f"{len(palettes)} palettes, {len(languages)} language files")


def check_vocabulary() -> Tuple[str, str]:
    from .commands import Vocabulary
    vocab = Vocabulary.load()
    if not vocab.phrases:
        return (WARN, "vCommands.ini did not load; voice commands unavailable")
    return (OK, f"{len(vocab.phrases)} phrases, wake word "
                f"'{', '.join(vocab.wake_words)}'")


def check_config() -> Tuple[str, str]:
    from .config import Config
    config = Config.load(write_default=False)
    if config.path.exists():
        return (OK, str(config.path))
    return (WARN, f"{config.path} not written yet (created on first run)")


def check_audio_out() -> Tuple[str, str]:
    from .audio import detect_player
    player = detect_player()
    if player:
        return (OK, player[0])
    return (WARN, "no pw-play/paplay/aplay - sound effects will be silent")


def check_audio_in() -> Tuple[str, str]:
    from .config import Config
    from .voice.capture import detect_recorder, list_input_devices
    recorder = detect_recorder()
    if not recorder:
        return (WARN, "no pw-record/parecord/arecord - voice input unavailable")

    config = Config.load(write_default=False)
    device = str(config.get("voice", "input_device", "default"))
    sources = list_input_devices()

    detail = f"{recorder[0]} · input_device = {device}"
    if sources:
        detail += "\n      available sources:"
        for source in sources[:12]:
            mark = "  <- configured" if source == device else ""
            detail += f"\n        {source}{mark}"
        if device not in ("default", "") and device not in sources:
            return (WARN, detail + f"\n      '{device}' is not among them")
    return (OK, detail)


def check_semantics() -> Tuple[str, str]:
    from .config import Config
    from .semantics import SemanticStore
    config = Config.load(write_default=False)
    if not bool(config.get("semantics", "enabled", True)):
        return (OK, "disabled in config")
    store = SemanticStore.load()
    threshold = float(config.get("semantics", "execute_threshold", 0.75))
    detail = f"{store.describe()} · threshold {threshold:.0%}"
    detail += f"\n      {store.path}"
    if not store.aliases:
        return (WARN, detail + "\n      nothing learned yet — run with "
                               "--teach or let the model teach it")
    return (OK, detail)


def check_stt() -> Tuple[str, str]:
    from .config import Config
    from .voice.engine import VoiceEngine

    class _Stub:
        pass

    config = Config.load(write_default=False)
    name = str(config.get("voice", "stt", "whisper"))
    if name == "none":
        return (OK, "disabled in config")
    from .voice.backends import VoskSTT, WhisperSTT
    if name == "whisper":
        block = config.section("whisper")
        driver = WhisperSTT(binary=str(block.get("binary", "whisper-cli")),
                            model=str(block.get("model", "")))
    else:
        driver = VoskSTT(model_dir=str(config.section("vosk").get("model", "")))
    status = driver.status()
    detail = f"{name}: {status}"
    # openai-whisper decodes through ffmpeg even for WAV, so a missing ffmpeg
    # means silent deafness rather than an error. Say so plainly.
    if getattr(driver, "needs_ffmpeg", False) and not driver.ffmpeg_ok:
        detail += "\n      without ffmpeg it returns nothing at all"
    return (OK if driver.available else WARN, detail)


def check_tts() -> Tuple[str, str]:
    from .config import Config
    from .voice.backends import EspeakTTS, PiperTTS
    config = Config.load(write_default=False)
    name = str(config.get("voice", "tts", "piper"))
    if name == "none":
        return (OK, "disabled in config")
    if name == "piper":
        block = config.section("piper")
        driver = PiperTTS(binary=str(block.get("binary", "piper")),
                          voice=str(block.get("voice", "")))
        if not driver.available:
            fallback = EspeakTTS()
            if fallback.available:
                return (WARN, f"piper: {driver.status()} - falling back to espeak-ng")
        return (OK if driver.available else WARN, f"piper: {driver.status()}")
    driver = EspeakTTS()
    return (OK if driver.available else WARN, f"espeak: {driver.status()}")


def check_llm() -> Tuple[str, str]:
    from .config import Config
    from .voice.backends import CloudReasoner, OllamaReasoner
    config = Config.load(write_default=False)
    name = str(config.get("voice", "llm", "ollama"))
    if name == "none":
        return (OK, "disabled in config")
    if name == "cdos":
        from .voice.cdos_backend import CdosBridge
        block = config.section("cdos")
        bridge = CdosBridge(root=str(block.get("root", "")),
                            bridge=str(block.get("bridge", "cdos-bridge")),
                            cli=str(block.get("cli", "cdos")))
        wording = str(block.get("wording", "ollama"))
        detail = f"cdos: {bridge.status()}"
        if not bridge.available:
            return (WARN, detail)
        if not bridge.cli_path:
            return (WARN, detail + " — readings unavailable without the cdos CLI")
        return (OK, detail + f" · wording {wording}")
    if name == "ollama":
        block = config.section("ollama")
        driver = OllamaReasoner(host=str(block.get("host", "")),
                                model=str(block.get("model", "")))
    else:
        block = config.section("cloud")
        driver = CloudReasoner(provider=str(block.get("provider", "anthropic")),
                               model=str(block.get("model", "")),
                               api_key_env=str(block.get("api_key_env", "")))
    return (OK if driver.available else WARN, f"{name}: {driver.status()}")


def check_dbus() -> Tuple[str, str]:
    """A session bus, without which D-Bus activated programs never start."""
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not address:
        return (WARN,
                "no session D-Bus bus. GNOME Terminal, Files and anything "
                "else D-Bus activated will not start. A full desktop provides "
                "one; a bare compositor session must run under "
                "'dbus-run-session'.")
    return (OK, address.split(",")[0])


def check_window_control() -> Tuple[str, str]:
    """Can the shell reach other programs' windows?

    This is the one capability that is not ours to have: only the compositor
    can grant it, and some deliberately do not.
    """
    from .config import Config
    from .windows import detect
    config = Config.load(write_default=False)
    backend = detect(str(config.get("windows", "backend", "auto")))
    if backend.available:
        return (OK, f"{backend.name}: {backend.status()}")
    return (WARN, backend.status())


def _mechanism(area: str) -> str:
    """What the platform adapter would use for *area*, by its own account.

    Asked of the adapter rather than probed here, so this module does not
    grow a second list of tool names to keep in step with the first.
    """
    from .platform import get_platform
    try:
        return get_platform().mechanisms().get(area, "none")
    except Exception:
        return "none"


def _reading(capability: str):
    from .platform import CapabilityRegistry, get_platform
    return CapabilityRegistry(get_platform()).invoke(capability)


def check_wireless() -> Tuple[str, str]:
    """Can the COMMUNICATIONS screen list and join wireless networks?"""
    if _mechanism("network") == "none":
        return (WARN, "no wireless control; install NetworkManager")
    result = _reading("network.wireless")
    if not result.ok:
        return (WARN, result.error)
    state = result.value
    if not state.hardware:
        return (WARN, "NetworkManager is running but sees no wireless device")
    if not state.enabled:
        return (OK, f"{state.device}: radio off")
    return (OK, f"{state.device}: " + (f"joined {state.ssid}" if state.ssid
                                         else "not joined"))


def check_bluetooth() -> Tuple[str, str]:
    """Can the COMMUNICATIONS screen pair and connect devices?"""
    if _mechanism("bluetooth") == "none":
        return (WARN, "no bluetooth control; install BlueZ (the 'bluez' package)")
    result = _reading("bluetooth.state")
    if not result.ok:
        return (WARN, result.error)
    state = result.value
    return (OK, f"{state.name or state.address}: "
                f"{'on' if state.powered else 'off'}")


def check_screenshots() -> Tuple[str, str]:
    """Can the whole screen be captured, or only the shell itself?"""
    tool = _mechanism("screenshot")
    if tool == "none":
        # The tool names live in the adapter and in docs/INSTALL.md, not
        # here: tests/test_boundary.py holds this module to its allowance.
        return (WARN, "no capture tool for this session — SCREEN SHOT will "
                      "save the EARTH OS screen only. See 'Screenshots' in "
                      "docs/INSTALL.md for the package to install.")
    from .screengrab import screenshot_folder
    return (OK, f"{tool} → {screenshot_folder()}")


def check_openers() -> Tuple[str, str]:
    """Can the file browser hand a file to the desktop?"""
    from .openers import OPENERS
    found = [o[0] for o in OPENERS if shutil.which(o[0])]
    if found:
        return (OK, ", ".join(found))
    return (WARN, "no xdg-open/gio — the browser cannot open files "
                  "(sudo apt install xdg-utils)")


def check_compositor() -> Tuple[str, str]:
    found = [c for c in ("cage", "labwc", "sway", "weston") if shutil.which(c)]
    if found:
        return (OK, ", ".join(found))
    return (WARN, "none of cage/labwc/sway/weston - the session script will "
                  "run the shell directly instead of in a kiosk compositor")


def check_install() -> Tuple[str, str]:
    """Is the shell reachable by name, and is the launcher pointing at us?"""
    binary = shutil.which("earth-os")
    here = Path(__file__).resolve().parent.parent
    if not binary:
        return (WARN,
                f"'earth-os' is not on PATH (running from {here}). "
                f"Menu entries that call it by name will do nothing. "
                f"Add its bin directory to PATH, or reinstall.")
    return (OK, f"{binary} (package at {here})")


def check_claude_code() -> Tuple[str, str]:
    """The built-in agent: the CLI, and a workspace it could change."""
    from .config import Config
    from .agent.gate import DEFAULT_PYTHON
    from .agent.workspace import locate, templates, user_tree
    config = Config.load(write_default=False)
    if not bool(config.get("claude", "enabled", True)):
        return (OK, "disabled in config")
    command = str(config.get("claude", "command", "claude") or "claude")
    found = shutil.which(command) or (command if os.access(command, os.X_OK) else "")
    if not found:
        return (WARN, f"'{command}' not found — the CLAUDE CODE screen will "
                      "say so. npm install -g @anthropic-ai/claude-code")
    python = str(config.get("claude", "python", DEFAULT_PYTHON))
    if not Path(python).exists():
        return (WARN, f"{found}, but the gate's interpreter {python} is "
                      "missing — no change could be verified, so none is kept")
    ws = locate(config)
    if ws is not None:
        return (OK, f"{found} · workspace {ws.describe()}")
    if any((t / "tests").is_dir() for t in templates()):
        return (OK, f"{found} · workspace created at {user_tree()} on first use")
    return (WARN, f"{found}, but no source template to make a workspace from "
                  "— reinstall with ./install.sh or set [claude] workspace")


# --------------------------------------------------------------------------

CHECKS: List[Tuple[str, Callable[[], Tuple[str, str]]]] = [
    ("python", check_python),
    ("pycairo", check_cairo),
    ("pygobject", check_gi),
    ("gtk4", check_gtk),
    ("display", check_display),
    ("open a window", check_can_open_a_window),
    ("installed as", check_install),
    ("assets", check_assets),
    ("font", check_font),
    ("config", check_config),
    ("voice commands", check_vocabulary),
    ("learned meanings", check_semantics),
    ("audio output", check_audio_out),
    ("audio input", check_audio_in),
    ("speech to text", check_stt),
    ("text to speech", check_tts),
    ("reasoning", check_llm),
    ("claude code", check_claude_code),
    ("session bus", check_dbus),
    ("file openers", check_openers),
    ("window control", check_window_control),
    ("wireless", check_wireless),
    ("bluetooth", check_bluetooth),
    ("screenshots", check_screenshots),
    ("compositor", check_compositor),
]


def run(verbose: bool = True) -> int:
    from . import __version__

    report = Report()
    for name, fn in CHECKS:
        _safe(report, name, fn)

    if verbose:
        print(f"earth-os {__version__} — diagnostics")
        print("=" * 72)
        print(report.render())
        print("=" * 72)
        if report.failures:
            print(f"{report.failures} blocking problem(s); "
                  f"the shell will not start until they are fixed.")
        elif report.warnings:
            print(f"No blocking problems. {report.warnings} optional "
                  f"component(s) unavailable — the interface works, "
                  f"those features are off.")
        else:
            print("Everything checks out.")
        print("")
        print("Environment:")
        for key in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "WAYLAND_DISPLAY",
                    "DISPLAY", "GDK_BACKEND", "XDG_RUNTIME_DIR", "PATH"):
            value = os.environ.get(key)
            if value:
                if key == "PATH" and len(value) > 300:
                    value = value[:300] + " ..."
                print(f"  {key}={value}")
    return 1 if report.failures else 0
