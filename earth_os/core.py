"""Shell core: state, navigation, actions and the command bus.

Deliberately free of GTK so the whole UI can be rendered to an image surface in
tests and screenshots.  :mod:`earth_os.app` adds the window, the input
plumbing and the frame clock on top of this.
"""

from __future__ import annotations

import os
import shlex
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import sysinfo
from .audio import SoundPlayer
from .capability_corpus import CapabilityCorpus
from .commands import Dispatcher, Vocabulary, normalise
from .config import Config
from .openers import OPENERS
from .platform import (
    CapabilityRegistry, CapabilityResult, PlatformError, get_platform,
)
from .theme import Palette, Strings, ensure_font, font_family, set_face
from .windows import WindowManager
from .widgets.base import RenderContext

ASSETS = Path(__file__).parent / "assets"


class Shell:
    """Everything the shell knows, minus the toolkit it is drawn with."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        ensure_font()
        set_face(str(self.config.get("shell", "font", "VT323")))

        #: The machine underneath, behind a capability interface.
        #:
        #: Everything that touches the computer itself — starting a program,
        #: powering down, asking who is logged in — goes through here rather
        #: than through a command this file knows the name of. That is what
        #: lets the same shell run somewhere that has never heard of
        #: ``systemctl``.
        #:
        #: There is deliberately no fallback to running commands directly. A
        #: host with no adapter genuinely cannot do these things, and saying
        #: so is more useful than a second, quieter code path that only works
        #: on Linux — which is the arrangement this layer exists to end.
        self.platform = None
        self.capabilities: Optional[CapabilityRegistry] = None
        try:
            self.platform = get_platform()
            self.capabilities = CapabilityRegistry(self.platform)
        except PlatformError as exc:
            self.platform_error = str(exc)
        else:
            self.platform_error = ""

        #: How a person asks for what the catalogue can do.
        #:
        #: The catalogue says what this computer is capable of; this says what
        #: to call those things. Kept apart because they are different kinds
        #: of knowledge — one changes when the machine changes, the other when
        #: the language does — and kept in step by a test rather than by care.
        self.capability_corpus = CapabilityCorpus(self.capabilities)

        #: Named sequences of capabilities — "movie mode", "status report".
        #:
        #: A new name, not new power: every step is in the catalogue already.
        #: The store cannot invoke anything, which is why it can be data a
        #: person edits. Running one goes through :meth:`run_composition`, and
        #: that is the only path, because it is the one that confirms.
        self.compositions = None
        #: Why the store could not be loaded, if it could not. Its own
        #: attribute rather than ``status_message``, which is set to "" further
        #: down this constructor and would swallow anything written here.
        self.composition_error = ""
        if bool(self.config.get("compositions", "enabled", True)):
            from .compositions import CompositionStore
            try:
                self.compositions = CompositionStore.load()
            except Exception as exc:                # never stop the shell
                self.composition_error = f"{type(exc).__name__}: {exc}"

        #: What the person has let this computer remember, and the installed
        #: applications described as data. The World Model itself is never
        #: stored: :meth:`world_model` assembles it from these and the
        #: catalogue each time, so it cannot drift from what is really here.
        #: See docs/WORLD_MODEL.md.
        self.ledger = None
        self.apps = None
        self.world_error = ""
        if bool(self.config.get("world", "enabled", True)):
            from .world_model import ApplicationIndex, UserLedger
            try:
                self.ledger = UserLedger.load(
                    observe=bool(self.config.get("world", "observe", True)))
                self.apps = ApplicationIndex.load()
            except Exception as exc:                # never stop the shell
                self.world_error = f"{type(exc).__name__}: {exc}"

        #: Earth Mind (docs/EARTH_MIND.md): the one coordination layer that
        #: CDOS, the Companion, the system and game worlds are subsystems of.
        #: Built on first use, so nothing about it is paid for during boot.
        self._mind = None
        self._mind_built = False
        self.mind_error = ""
        #: The Companion Engine on its own, for ``[mind] enabled = false``.
        self._companion = None
        self._companion_built = False

        self.palette = self._load_palette()
        self.strings = self._load_strings()
        self.monitor = sysinfo.SystemMonitor()
        self.sound = SoundPlayer(
            ASSETS / "sounds",
            enabled=bool(self.config.get("shell", "sounds", True)),
            volume=float(self.config.get("shell", "volume", 0.6)),
        )

        self.conversation: List[Dict[str, object]] = []
        self._status_message = ""
        self.redraw_hook: Optional[Callable[[], None]] = None
        self.quit_hook: Optional[Callable[[], None]] = None
        #: Run a callable on the interface thread. Set by the application
        #: (``GLib.idle_add``); None means "already on it", which is the case
        #: in tests and headless rendering.
        self.call_on_main: Optional[Callable[[Callable[[], None]], None]] = None
        #: Restart the shell into whatever code is on disk now. Set by the
        #: application; the built-in agent asks for it after an accepted change.
        self.restart_hook: Optional[Callable[[], None]] = None
        #: The built-in Claude Code agent (earth_os.agent), registered by the
        #: CLAUDE CODE screen when it is constructed.
        self.agent = None

        self.screens: Dict[str, object] = {}
        self.history: List[str] = []
        self.current: str = "main"

        #: Learned relationships — aliases and IF/THEN rules the model taught
        #: this machine. Sits between the fixed vocabulary and the model, so
        #: an explanation only has to be worked out once.
        self.semantics = None
        self._semantics_dirty = False
        if bool(self.config.get("semantics", "enabled", True)):
            from .semantics import SemanticStore, seed as seed_semantics
            self.semantics = SemanticStore.load()
            if not self.semantics.aliases and \
                    bool(self.config.get("semantics", "seed", True)):
                if seed_semantics(self.semantics):
                    self.semantics.save()

        #: Recently used programs, ranked and persisted across restarts. The
        #: history is platform-neutral; only the optional OS-supplied extras
        #: behind RecentStore.provider are platform-specific.
        self.recent = None
        if bool(self.config.get("shell", "track_recent", True)):
            from .recent import RecentStore
            try:
                self.recent = RecentStore.load(
                    limit=int(self.config.get("shell", "recent_limit", 60)))
            except Exception:
                self.recent = None

        self.dispatcher = Dispatcher(Vocabulary.load(), semantics=self.semantics)
        self._register_actions()

        self.voice = None          # set by app once the engine is constructed
        self.listening = False
        self.thinking = False

        #: (label, callback) pairs the interface must offer before the shell
        #: can continue — CDOS reporting an ambiguity, or a command that
        #: would change the machine and needs a person to press something.
        self.pending_options: List[Tuple[str, Callable[[], None]]] = []
        self.pending_prompt = ""

        #: The last result :meth:`_capability_confirmed` obtained, so a caller
        #: that went through the gated path (the built-in agent's bridge) can
        #: report the value rather than only the fact that it was spoken.
        self.last_capability_result = None

        #: Cached CDOS readings: GODCORE, emotional weather, semantic system
        #: state and signals. Refreshed on a worker, never on the frame clock.
        self.cdos_snapshot: Dict[str, object] = {}
        self._cdos_next = 0.0
        self._cdos_busy = False

        #: (when, label) of things this shell started, for the task screen.
        self.launched: List[Tuple[float, str]] = []

        #: A short message shown over whatever screen is up, and until when.
        #: The answer to a press has to appear where the press was — a
        #: screenshot taken from the engineering screen cannot report itself
        #: in the main screen's transcript and call that feedback.
        self.toast_text = ""
        self.toast_until = 0.0
        #: The output volume behind the volume spot in the upper-left box.
        from .volume import SystemVolume
        self.volume = SystemVolume(self)
        #: The window's size in pixels, published by the GTK layer on every
        #: draw. Used to render the shell itself when the screen cannot be
        #: captured whole.
        self.surface_size: Tuple[int, int] = (1600, 1000)
        #: Where the last screenshot went.
        self.last_screenshot = ""
        #: Until when a new window is assumed to be the capture's own, and
        #: left alone rather than framed. See :meth:`poll_windows`.
        self._capture_quiet_until = 0.0

        #: Wireless and Bluetooth state for the communications screen.
        from .comms import Comms
        self.comms = Comms(self)

        #: Other programs' windows, via whatever the compositor allows.
        self.windows = WindowManager(
            preferred=str(self.config.get("windows", "backend", "auto")),
            fullscreen_launched=bool(
                self.config.get("windows", "fullscreen_launched", True)),
        )
        #: Set while another program is in front of the shell.
        self.handed_over_to = ""
        #: The window currently living inside the LCARS frame, if any.
        self.framed_window = None
        #: Handles of programs that have been in the frame, oldest first, so
        #: the main screen can go back to the last one when the current one
        #: has gone.
        self._frame_history: List[str] = []
        #: Content rectangle in screen pixels, published by the GTK layer.
        self.frame_rect: Optional[Tuple[int, int, int, int]] = None
        #: The output rectangle, when a compositor can tell us.
        self.output_rect: Optional[Tuple[int, int, int, int]] = None
        #: Window handles already seen, so a new one can be spotted.
        self._known_windows: set = set()
        self._seeded_windows = False
        #: Next monotonic time a clock-driven poll is due. The poll itself
        #: runs a subprocess, so the frame clock asks :attr:`poll_due` first
        #: rather than polling thirty times a second.
        self._windows_next_poll = 0.0
        #: The option list EXIT put on the strip, kept by identity so "is the
        #: exit menu up" is true only while *that* list is the one showing —
        #: not for an ambiguity question that replaced it.
        self._exit_options: Optional[list] = None
        #: The shell loses focus for a moment as the menu comes forward; a
        #: loss of focus inside this window is not the person looking away.
        self._exit_grace = 0.0

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def _load_palette(self) -> Palette:
        name = str(self.config.get("shell", "palette", "Earth OS"))
        local = Path(os.path.expanduser(name))
        palette = (Palette.from_file(local) if local.suffix == ".lxcp" and local.exists()
                   else Palette.builtin(name))
        palette.apply_overrides(self.config.section("colors"))
        return palette

    def _load_strings(self) -> Strings:
        name = str(self.config.get("shell", "language", "Standard"))
        local = Path(os.path.expanduser(name))
        if local.suffix == ".lng" and local.exists():
            return Strings.from_file(local)
        return Strings.builtin(name)

    def set_palette(self, name: str) -> None:
        self.config.values.setdefault("shell", {})["palette"] = name
        self.palette = self._load_palette()
        self.invalidate_all()

    def _display_name(self) -> str:
        """What the shell calls itself to a person: the configured name."""
        return str(self.config.get("shell", "computer_name", "EARTH OS")
                   or "EARTH OS")

    def set_font(self, name: str) -> None:
        """Switch the interface face; an unknown name falls back to the default."""
        self.config.values.setdefault("shell", {})["font"] = set_face(name)
        self.invalidate_all()

    def set_language(self, name: str) -> None:
        self.config.values.setdefault("shell", {})["language"] = name
        self.strings = self._load_strings()
        self.invalidate_all()

    # ------------------------------------------------------------------
    # Rendering context
    # ------------------------------------------------------------------
    def context(self, width: float, height: float) -> RenderContext:
        unit = max(height / 40.0, 8.0)
        return RenderContext(
            palette=self.palette, strings=self.strings,
            width=width, height=height, unit=unit,
            now=time.monotonic(), font=font_family(),
        )

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def register_screen(self, screen) -> None:
        self.screens[screen.name] = screen

    def screen(self):
        return self.screens.get(self.current)

    def go(self, name: str) -> None:
        if name not in self.screens or name == self.current:
            return
        current = self.screens.get(self.current)
        if current is not None:
            current.on_leave()
        self.history.append(self.current)
        del self.history[:-32]
        self.current = name
        self.screens[name].on_enter()
        self.play("beep")
        self.redraw()

    def go_back(self) -> None:
        if not self.history:
            self.go("main")
            return
        previous = self.history.pop()
        current = self.screens.get(self.current)
        if current is not None:
            current.on_leave()
        self.current = previous
        screen = self.screens.get(previous)
        if screen is not None:
            screen.on_enter()
        self.redraw()

    def invalidate_all(self) -> None:
        for screen in self.screens.values():
            screen.invalidate()  # type: ignore[attr-defined]
        self.redraw()

    def redraw(self) -> None:
        if self.redraw_hook:
            self.redraw_hook()

    @property
    def status_message(self) -> str:
        """The line screens show to explain what just happened.

        A property rather than a plain attribute because frames are no
        longer produced on a clock: whatever sets this has, by definition,
        changed what is on screen, and there are forty places that set it —
        several from worker threads finishing a launch or a capability. A
        redraw at each of them is forty chances to forget one, and a
        forgotten one is an error message that never appears. Here it cannot
        be forgotten. Unchanged text asks for nothing, so assigning the same
        message twice does not cost a frame.
        """
        return self._status_message

    @status_message.setter
    def status_message(self, text: str) -> None:
        text = text or ""
        if text == self._status_message:
            return
        self._status_message = text
        self.redraw()

    # ------------------------------------------------------------------
    # What changed? — the two questions the background jobs ask
    # ------------------------------------------------------------------
    def telemetry_signature(self) -> Tuple:
        """Every live hardware reading a screen might draw, as one value.

        The point of this is to keep sampling separate from rendering. The
        gauges are live, but at thirty frames a second the readings behind
        them only move once — ``sysinfo`` caches for a second — so
        twenty-nine of those frames redrew an identical picture. Sampling
        now happens on its own one-second timer, and a frame is asked for
        only when this value differs from the last one.

        Finer than what is displayed on purpose. The gauges quantise to
        twentieths and the numbers to whole percents, so a signature this
        precise occasionally asks for a frame that draws the same pixels.
        That costs one frame; the opposite mistake would be a gauge that
        sticks, so the rounding errs this way deliberately.
        """
        mon = self.monitor
        try:
            return (
                round(mon.cpu(), 4),
                round(mon.memory()[0], 5), mon.memory()[1],
                round(mon.swap()[0], 5),
                round(mon.disk()[0], 5),
                tuple(round(c, 4) for c in mon.cores()),
                tuple(round(v, 1) for v in mon.net_rate()),
                int(mon.uptime()),
                mon.processes(),
                mon.temperature(),
                mon.battery(),
                tuple(round(v, 3) for v in mon.load()),
            )
        except Exception:
            # A reading that cannot be taken is not a reason to stop drawing;
            # the monotonic clock makes every call differ, so the next frame
            # happens and the gauge shows whatever the screen makes of it.
            return (time.monotonic(),)

    def window_signature(self) -> Tuple:
        """The running-program list as the chrome strip draws it.

        ``poll_windows`` does its own work regardless; this is how the
        caller tells whether the result would change the picture, so a poll
        that finds nothing new costs no frame.
        """
        manager = getattr(self, "windows", None)
        windows = list(getattr(manager, "windows", []) or [])
        framed = (self.framed_window.handle
                  if getattr(self, "framed_window", None) is not None else None)
        return (tuple((w.handle, w.app or w.title) for w in windows),
                framed, bool(getattr(manager, "available", False)),
                self.handed_over_to)

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------
    def play(self, name: str) -> None:
        self.sound.play(name)

    # ------------------------------------------------------------------
    # Conversation
    # ------------------------------------------------------------------
    def say(self, text: str, role: str = "computer", speak: bool = True) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.conversation.append({"role": role, "text": text, "at": time.time()})
        limit = int(self.config.get("persona", "max_history", 20)) * 2
        if len(self.conversation) > limit:
            del self.conversation[:-limit]
        panel = getattr(self.screens.get("main"), "transcript", None)
        if panel is not None:
            panel.scroll_to_end()
        if speak and role == "computer" and self.voice is not None:
            self.voice.speak(text)
        self.redraw()

    def user_said(self, text: str) -> None:
        self.say(text, role="user", speak=False)

    def clear_conversation(self) -> None:
        self.conversation.clear()
        self.redraw()

    # ------------------------------------------------------------------
    # Voice state
    # ------------------------------------------------------------------
    def voice_state_label(self) -> str:
        if self.thinking:
            return self.strings.get("myWorking", "WORKING")
        if self.listening:
            return self.strings.get("myListening", "LISTENING")
        if self.voice is None or not self.voice.available:
            return "VOICE OFFLINE"
        return self.strings.get("myStandby", "STANDBY")

    def toggle_listening(self) -> None:
        if self.voice is None:
            self.status_message = "voice subsystem unavailable"
            self.say("Voice subsystem offline.", speak=False)
            return
        if self.listening:
            self.voice.stop_listening()
        else:
            self.voice.start_listening()
        self.redraw()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _register_actions(self) -> None:
        self.dispatcher.register_many({
            "menu": lambda: self.go("programs"),
            "programs": lambda: self.go("programs"),
            "list programs": lambda: self.go("programs"),
            "my computer": lambda: self.browse(str(Path.home())),
            "my documents": lambda: self.browse(self.user_dir("DOCUMENTS", "Documents")),
            "my pictures": lambda: self.browse(self.user_dir("PICTURES", "Pictures")),
            "my music": lambda: self.browse(self.user_dir("MUSIC", "Music")),
            "system browser": lambda: self.browse("/"),
            "specify parameters": lambda: self.go("settings"),
            "engineering": lambda: self.go("engineering"),
            # The terminal, reachable by voice as well as by button.
            "terminal": lambda: self.go("terminal"),
            "command line": lambda: self.go("terminal"),
            "console": lambda: self.go("terminal"),
            "shell": lambda: self.go("terminal"),
            # The built-in coding agent. "Claude, <request>" is routed to it
            # directly in handle_utterance; the bare name opens its screen.
            "claude": lambda: self.go("claude"),
            "home": lambda: self.go("main"),
            "programs running": lambda: self.go("tasks"),
            "switch": lambda: self.go("tasks"),
            "windows": lambda: self.go("tasks"),
            "recent": lambda: self.go("recent"),
            "recent programs": lambda: self.go("recent"),
            "recently used": lambda: self.go("recent"),
            "communications": lambda: self.go("comms"),
            "comms": lambda: self.go("comms"),
            "wireless": lambda: self.go("comms"),
            "wifi": lambda: self.go("comms"),
            "bluetooth": lambda: self.go("comms"),
            "screen shot": self.take_screenshot,
            "back to earth os": self.reclaim,
            "back": self.go_back,
            "speech": self.toggle_listening,
            "access": self.toggle_listening,
            "log off": self.log_off,
            "shut down": self.shutdown,
            "exit options": self.exit_options,
            "deactivate": self.deactivate,
            "continuous commands": lambda: self.set_continuous(True),
            "end commands": lambda: self.set_continuous(False),
            "scroll down": lambda: self.scroll_active(1),
            "scroll up": lambda: self.scroll_active(-1),
            # The wake word on its own simply opens the microphone.
            "computer": self.toggle_listening,
            # The original grammar shipped one launcher example; on Linux the
            # equivalent is whatever the desktop has registered as the editor.
            "notepad": lambda: self.launch(
                os.environ.get("EDITOR_GUI", "xdg-open /etc/os-release")),
        })

    def run_action(self, action: str) -> bool:
        """Run a canonical action by name; returns whether it was handled."""
        key = normalise(action)
        handler = self.dispatcher.handlers.get(key)
        if handler:
            handler()
            return True
        resolved = self.dispatcher.dispatch(action)
        return resolved is not None

    def handle_utterance(self, text: str) -> None:
        """A recognised utterance: run it as a command, else ask the model.

        Three tiers, in descending order of how much the shell trusts itself:
        the fixed vocabulary, then the learned semantic layer (which must clear
        a confidence threshold, and must ask when it cannot), then the model —
        whose answer, if it works out, is written down so this utterance never
        needs a model again.
        """
        text = (text or "").strip()
        if not text:
            return

        # "Claude, make the header orange" is addressed to the built-in agent
        # by name, so it goes to the agent whole rather than being matched
        # word by word — the vocabulary's bare "home" would otherwise fire
        # inside "Claude, change the home screen".
        request = self.agent_request(text)
        if request:
            self.user_said(text)
            self.go("claude")
            self.agent.ask(request)
            return

        self.user_said(text)

        # A conversation with a game character is in progress on this
        # interface: what is said next is said to them. Checked before any
        # command layer, or the vocabulary's bare "home" would walk off with
        # "I am going home" halfway through a conversation in a shop.
        if self.talking_to_a_character("voice"):
            self.converse(text)
            return

        # "Remember that I prefer cooperative games." Anchored at the start of
        # the sentence, so it cannot fire inside some other request.
        if self.ledger is not None:
            from .world_model import parse_request
            world = parse_request(text)
            if world is not None:
                self.user_said(text)
                self.say(self.handle_world_request(world))
                return

        # Most specific wins.
        #
        # The vocabulary matches by containment, so its bare "home" fires
        # inside "where is my home" and answers a different question. Whoever
        # recognised more of the sentence understood more of it, so a longer
        # capability phrase is preferred over a shorter vocabulary one. Equal
        # lengths leave the vocabulary in front, because it is the older and
        # better-tested layer.
        spoken = self.dispatcher.vocabulary.match_span(text)
        intent = (self.capability_corpus.resolve(text)
                  if self.capabilities is not None else None)

        # A composition is a name somebody chose, so it competes on the same
        # terms. It is checked first only when it matched *more* of the
        # sentence than either other layer: "status" as a composition must not
        # shadow a longer phrase that meant something else, and a composition
        # somebody named after an existing phrase should still win when they
        # say the whole of it.
        match = (self.compositions.resolve(text)
                 if self.compositions is not None else None)
        # The conversation guard. "I was thinking about going home after
        # work" contains the vocabulary's "home"; it is still talk. A sentence
        # that is plainly conversational, and that the command layers
        # recognised less than 60% of, goes to Earth Mind. The guard can
        # only take a sentence *away* from a command, never cause one.
        if self._companion_claims(text, max(
                spoken, len(intent.phrase) if intent else 0,
                len(match.phrase) if match else 0)):
            self.converse(text)
            return

        if match is not None and len(match.phrase) > spoken \
                and len(match.phrase) >= len(intent.phrase if intent else ""):
            self.run_composition(match.composition)
            return

        if intent is not None and len(intent.phrase) > spoken:
            if self.run_capability_intent(text):
                return

        action, resolution = self.dispatcher.resolve_learned(text)

        # Tier 1: somebody wrote this phrase down. Just run it.
        if action and resolution is None and action in self.dispatcher.handlers:
            self._run_action(action)
            return

        # Tier 2: the learned layer recognised it.
        if action and resolution is not None and action in self.dispatcher.handlers:
            threshold = float(self.config.get("semantics", "execute_threshold", 0.75))
            if resolution.should_execute(threshold):
                if self.semantics is not None:
                    self.semantics.touch(resolution.term)
                    self._semantics_dirty = True
                self._run_action(action, note=resolution.explanation)
                return
            # Understood, but not confidently enough to act unasked — or
            # effectful, in which case no confidence is ever enough.
            self._propose_interpretation(text, action, resolution)
            return

        # Tier 3: the machine's own account of itself.
        #
        # Before asking a model what someone meant, ask what this computer can
        # actually do. The catalogue is a better witness than a guess is, it
        # costs nothing, and it works with no model present at all — which is
        # the difference between a computer that degrades when the network
        # goes and one that does not.
        if self.run_capability_intent(text):
            return

        # Tier 4: nothing known matched. Earth Mind takes it: knowledge goes
        # to CDOS, talk to the Companion, a character's name to its world.
        # A request it recognises as a task comes back to the model path below.
        # With ``[companion] enabled = false`` there is nobody to talk to, and
        # this is the model path exactly as it was.
        if self.companion_enabled():
            self.converse(text)
            return
        self._fallback_to_model(text)

    def _fallback_to_model(self, text: str) -> None:
        """The tier 4 of before Earth Mind: ask the reasoner, and learn from it."""
        if self.voice is not None and self.voice.can_reason:
            self.ask_model(text, learn_from=text)
        else:
            self.say(str(self.config.get("persona", "unknown",
                                         "Unable to comply.")))

    # ------------------------------------------------------------------
    # Earth Mind (docs/EARTH_MIND.md)
    # ------------------------------------------------------------------
    def mind_enabled(self) -> bool:
        return bool(self.config.get("mind", "enabled", True))

    def companion_enabled(self) -> bool:
        return bool(self.config.get("companion", "enabled", True))

    def _conversation_link(self):
        """CDOS Computer for conversation: the voice's link, else one of our own."""
        link = self.voice.computer if self.voice is not None else None
        if link is None:
            from .voice.computer_link import ComputerLink
            link = ComputerLink.from_config(self.config)
            if link.mode == "off":
                link = None
        return link

    def _wording_model(self):
        """The shell's own reasoner, unwrapped: what ``backend = "reasoner"`` speaks through."""
        reasoner = getattr(self.voice, "reasoner", None) if self.voice is not None else None
        return getattr(reasoner, "inner", None) or reasoner

    def earth_mind(self):
        """Earth Mind, built once on first use. None if disabled or it cannot be built.

        Building it calls no model, loads no corpus and takes no reading, and
        it never happens during boot. A failure to build is recorded in
        :attr:`mind_error` and leaves the shell exactly as it was before
        Earth Mind existed: commands, the terminal and the model path all
        still work.
        """
        if not self.mind_enabled():
            return None
        if self._mind_built:
            return self._mind
        self._mind_built = True
        try:
            from .mind import build_mind
            self._mind = build_mind(
                self.config, shell=self, link=self._conversation_link(),
                ledger=self.ledger, reasoner=self._wording_model(),
                godcore=lambda: self.cdos_snapshot)
        except Exception as exc:                  # never stop the shell
            self.mind_error = f"{type(exc).__name__}: {exc}"
            self.status_message = f"earth mind unavailable: {self.mind_error}"
            self._mind = None
        return self._mind

    def companion_engine(self):
        """The Companion Engine the voice talks to. None if disabled or unbuildable.

        With Earth Mind on, this is its default personality's engine — the
        same object the terminal reaches — so there is one conversation, not
        one per interface. With ``[mind] enabled = false`` it is the Companion
        Engine on its own, as in CDOS.
        """
        if not self.companion_enabled():
            return None
        mind = self.earth_mind()
        if mind is not None:
            return mind.companion(mind.persona("voice").id)
        if self._companion_built:
            return self._companion
        self._companion_built = True
        try:
            from .companion import build_engine
            self._companion = build_engine(
                self.config, link=self._conversation_link(), ledger=self.ledger,
                godcore=lambda: self.cdos_snapshot, reasoner=self._wording_model())
        except Exception as exc:                  # never stop the shell
            self.status_message = f"companion unavailable: {type(exc).__name__}: {exc}"
            self._companion = None
        return self._companion

    def talking_to_a_character(self, interface: str) -> bool:
        """Is a game character being talked to on *interface*? Never builds anything."""
        mind = self._mind
        if mind is None:
            return False
        try:
            return mind.games.active(interface) is not None
        except Exception:
            return False

    def _companion_claims(self, text: str, recognised: int) -> bool:
        if not self.companion_enabled() or not bool(self.config.get("companion", "guard", True)):
            return False
        if not recognised:
            return False                     # nothing to take it from; tier 4 will have it
        from .companion import is_conversational
        whole = len(normalise(text)) or 1
        return recognised < 0.6 * whole and is_conversational(text)

    def converse(self, text: str) -> None:
        """One turn through Earth Mind, on a worker thread like every other reasoner call."""
        mind = self.earth_mind()
        engine = None if mind is not None else self.companion_engine()
        if mind is None and engine is None:
            self._fallback_to_model(text)
            return
        self.thinking = True
        self.redraw()
        # GODCORE is read from the cache; this only keeps the cache warm, and
        # is throttled and off the frame clock like every other refresh.
        try:
            self.refresh_cdos()
        except Exception:
            pass

        def worker() -> None:
            try:
                if mind is not None:
                    response = mind.handle(text, interface="voice")
                else:
                    response = engine.respond(text)
            except Exception as exc:
                self.thinking = False
                self.say(f"Conversation subsystem error. {type(exc).__name__}: {exc}")
                return
            self.thinking = False
            if mind is not None:
                self.apply_mind_response(response, text)
                return
            if response.handoff:
                self._fallback_to_model(text)
                return
            self.status_message = response.note()
            self.say(response.text or "…")

        threading.Thread(target=worker, daemon=True, name="earth-mind").start()

    def apply_mind_response(self, response, text: str) -> None:
        """Say what Earth Mind answered, or do what it asked for — through the gate.

        Earth Mind cannot act. When its answer is a request for a system
        operation, the request is carried out *here*, by the same paths a
        spoken command takes: an effectful capability stops at the choice
        strip for a press, and a program is started only if it is one this
        machine has installed.
        """
        if response.handoff:
            self._fallback_to_model(text)
            return
        if response.proposal is not None:
            ok, message = self.carry_out(response.proposal)
            if message:
                self.say(message)
            return
        self.status_message = response.note()
        self.say(response.text or "…")

    def carry_out(self, proposal) -> Tuple[bool, str]:
        """A proposal from Earth Mind, taken down the shell's gated path.

        Returns ``(understood, message)``. *message* is something to say when
        the gated path has not already said it; an effectful capability
        returns with the choice strip up and nothing run.
        """
        if proposal.kind == "program":
            return self.open_program(proposal.target)
        if proposal.kind == "capability":
            if self.run_capability_intent(proposal.text):
                return True, ""
            return False, f"Unable to comply. {proposal.summary or proposal.target} is not available."
        return False, f"Unable to comply. Unknown kind of request: {proposal.kind}."

    # -- what Earth Mind's system subsystem asks of the shell ------------------
    def answer_reading(self, capability: str) -> Tuple[bool, str]:
        """Take one reading and put it into words. Readings only.

        The same check :meth:`answer_analysis` makes, at the moment of
        running: in the catalogue, parameterless, **not effectful**, and
        listed as a reading. Anything else is refused before it is invoked, so
        this cannot be talked into changing the machine.
        """
        from .capability_corpus import READINGS
        if self.capabilities is None:
            return False, f"no platform adapter: {self.platform_error}"
        spec = self.capabilities.spec(capability)
        if spec is None or spec.effectful or spec.params or capability not in READINGS:
            return False, f"{capability} is not a reading"
        result = self.capabilities.invoke(capability)
        if not result.ok:
            return False, f"{spec.summary}: {result.error}"
        said = self.capability_corpus.say_result(capability, result.value)
        return True, f"{spec.summary[:1].upper()}{spec.summary[1:]}: {said}"

    def _program_index(self) -> list:
        now = time.monotonic()
        cached = getattr(self, "_programs_cache", None)
        if cached is not None and now - cached[0] < 30.0:
            return cached[1]
        from .screens.programs import discover_programs
        try:
            programs = discover_programs(self.config.programs())
        except Exception:
            programs = []
        self._programs_cache = (now, programs)
        return programs

    def find_program(self, name: str) -> Optional[Tuple[str, str]]:
        """An installed application called *name*: ``(its name, its own command)``, or None.

        Looked up among the applications this machine has — the same index
        the PROGRAM INDEX screen shows — and never built from what was typed.
        "Open Firefox" can start Firefox; it cannot start a command line.
        """
        import re
        wanted = " ".join(re.sub(r"[^a-z0-9+. -]", " ", (name or "").lower()).split())
        if not wanted:
            return None
        programs = self._program_index()

        def binary(program) -> str:
            try:
                first = shlex.split(program.command)[0]
            except (ValueError, IndexError):
                return ""
            return Path(first).name.lower()

        tiers = (
            [p for p in programs if p.name.lower() == wanted],
            [p for p in programs if binary(p) == wanted],
            [p for p in programs if wanted in p.name.lower().split()],
            [p for p in programs if p.name.lower().startswith(wanted + " ")],
        )
        for tier in tiers:
            if tier:
                return tier[0].name, tier[0].command
        return None

    def open_program(self, name: str) -> Tuple[bool, str]:
        """Start the installed application called *name*. Returns ``(started, message)``."""
        found = self.find_program(name)
        if found is None:
            return False, f"No installed program called {name}."
        label, command = found
        if self.launch(command, name=label):
            return True, f"Opening {label}."
        return False, f"Unable to comply. {self.status_message or label + ' did not start'}."

    def agent_request(self, text: str) -> str:
        """The request in an utterance addressed to Claude by name, or ""."""
        if self.agent is None:
            return ""
        import re
        match = re.match(r"^\s*(?:hey\s+|ok\s+)?claude(?:\s+code)?[\s,:;.!-]+(.+)$",
                         text, re.IGNORECASE)
        if not match:
            return ""
        request = match.group(1).strip()
        # "claude code" on its own is a name, not a request.
        return "" if request.lower() in ("code", "") else request

    def _run_action(self, action: str, note: str = "") -> None:
        """Fire a resolved action and acknowledge it."""
        self.play("ack")
        self.dispatcher.handlers[action]()
        description = self.dispatcher.vocabulary.descriptions.get(action, "")
        reply = f"Acknowledged. {description}".strip() if description \
            else "Acknowledged."
        self.say(reply)
        if note:
            self.status_message = note

    # ------------------------------------------------------------------
    # The learned interpretation loop
    # ------------------------------------------------------------------
    def _propose_interpretation(self, utterance: str, action: str,
                                resolution) -> None:
        """Offer a reading the learned layer is not yet entitled to act on.

        This is where confidence is actually earned.  A provisional alias is
        put in front of the person as a question; agreeing reinforces it until
        it crosses the threshold and stops asking, and disagreeing weakens it
        until it is forgotten.  An effectful command never stops asking,
        however well established the wording becomes.
        """
        reason = "this would change the machine" if resolution.effectful else \
            f"confidence {resolution.confidence:.0%}"
        self.pending_prompt = f"{action.upper()} — {reason}?"
        self.pending_options = [
            ("YES", lambda: self._accept_interpretation(action, resolution)),
            ("NO", lambda: self._reject_interpretation(resolution)),
        ]
        self.say(f"Did you mean {action}? {resolution.explanation}", speak=True)
        self.redraw()

    def _accept_interpretation(self, action: str, resolution) -> None:
        self.pending_options = []
        self.pending_prompt = ""
        if self.semantics is not None and resolution.via in ("alias", "fuzzy"):
            self.semantics.confirm(resolution.term)
            self._semantics_dirty = True
        if action in self.dispatcher.handlers:
            self._run_action(action)
        self.save_semantics()

    def _reject_interpretation(self, resolution) -> None:
        self.pending_options = []
        self.pending_prompt = ""
        if self.semantics is not None and resolution.via in ("alias", "fuzzy"):
            self.semantics.reject(resolution.term)
            self._semantics_dirty = True
            self.save_semantics()
        self.say("Understood. I will not read it that way again.", speak=False)
        self.redraw()

    def learn_from_exchange(self, utterance: str, canonical: str,
                            effectful: bool = False) -> None:
        """Write down what the model worked out, so it is not worked out twice.

        Only the content words of the utterance that are not already understood
        become aliases, and only when the fixed table can actually reach
        *canonical* — an alias pointing at nothing would be a permanent dead
        end that shadows the model for that phrasing.
        """
        if self.semantics is None:
            return
        if not bool(self.config.get("semantics", "learn", True)):
            return
        from .semantics import key_terms

        if not self.dispatcher.vocabulary.match(canonical) \
                and not self.dispatcher.known(canonical):
            return

        seed = float(self.config.get("semantics", "seed_confidence", 0.55))
        learned = []
        for term in key_terms(utterance):
            if self.dispatcher.vocabulary.match(term):
                continue                       # already understood; leave alone
            alias = self.semantics.learn_alias(
                term, canonical, domain="shell", source="llm",
                effectful=effectful, example=utterance, seed=seed)
            if alias is not None:
                learned.append(alias.term)
        if learned:
            self._semantics_dirty = True
            self.save_semantics()
            self.status_message = f"learned: {', '.join(learned)} → {canonical}"

    def save_semantics(self) -> None:
        if self.semantics is not None and getattr(self, "_semantics_dirty", False):
            self.semantics.save()
            self._semantics_dirty = False

    def ask_model(self, prompt: str, learn_from: str = "") -> None:
        """Send a prompt to the reasoning backend on a worker thread.

        *learn_from* is the raw utterance that failed to resolve.  If the
        model's answer turns out to name something the shell already knows how
        to do, that relationship is recorded on the way out, which is the whole
        point of the exercise: the model solves it once, not every time.
        """
        if self.voice is None:
            return
        self.thinking = True
        self.redraw()

        def worker() -> None:
            try:
                answer = self.voice.reason(prompt, self.reasoning_context())
            except Exception as exc:                      # backend failure
                answer = f"Reasoning subsystem error. {exc}"
            finally:
                self.thinking = False
            note = self._computer_note()
            if note:
                self.status_message = note
            self.say(answer or "No response.")
            if learn_from:
                try:
                    self._learn_from_answer(learn_from)
                except Exception:
                    pass          # learning must never break the voice path
            self.refresh_pending()

        threading.Thread(target=worker, daemon=True, name="cdos-reason").start()

    def _learn_from_answer(self, utterance: str) -> None:
        """Turn whatever the backend just resolved into a stored relationship.

        The CDOS backend records the command it settled on; that command is
        the canonical meaning, and it has already been through CDOS's own
        validator, so nothing enters the store that the fixed table would
        have refused.
        """
        if self.voice is None or self.semantics is None:
            return
        reasoner = self.voice.reasoner
        canonical = ""
        effectful = False

        last = getattr(reasoner, "last_command", None)
        if last:
            canonical = getattr(last, "target", "") or getattr(last, "command", "")
            effectful = bool(getattr(last, "needs_human", False))
        if not canonical:
            return
        self.learn_from_exchange(utterance, canonical, effectful=effectful)

    # ------------------------------------------------------------------
    # CDOS snapshot (GODCORE and the semantic system reading)
    # ------------------------------------------------------------------
    def cdos_bridge(self):
        """The CDOS bridge, when CDOS is the reasoning backend."""
        if self.voice is None:
            return None
        return getattr(self.voice.reasoner, "bridge", None)

    def _computer_note(self) -> str:
        """Where the last answer came from, when CDOS Computer gave it.

        "computer · knowledge · ESTABLISHED · 2 facts". The words are the
        computer's; this says what stands behind them, so a person can tell a
        fact from the corpus from a calculation from the random sampler.
        """
        if self.voice is None:
            return ""
        answer = getattr(self.voice.reasoner, "last_answer", None)
        if answer is None or not getattr(answer, "reached", False):
            return ""
        parts = ["computer", answer.faculty or "-"]
        if answer.certainty:
            parts.append(answer.certainty)
        facts = len(answer.evidence)
        if facts:
            parts.append(f"{facts} fact{'s' if facts != 1 else ''}")
        if not answer.final:
            parts.append("not answered")
        return " · ".join(parts)

    def _learn_from_answer(self, utterance: str) -> None:
        """Turn whatever the backend just resolved into a stored relationship.

        The CDOS backend records the command it settled on; that command is
        the canonical meaning, and it has already been through CDOS's own
        validator, so nothing enters the store that the fixed table would
        have refused.
        """
        if self.voice is None or self.semantics is None:
            return
        reasoner = self.voice.reasoner
        canonical = ""
        effectful = False

        last = getattr(reasoner, "last_command", None)
        if last:
            canonical = getattr(last, "target", "") or getattr(last, "command", "")
            effectful = bool(getattr(last, "needs_human", False))
        if not canonical:
            return
        self.learn_from_exchange(utterance, canonical, effectful=effectful)

    # ------------------------------------------------------------------
    # CDOS snapshot (GODCORE and the semantic system reading)
    # ------------------------------------------------------------------
    def cdos_bridge(self):
        """The CDOS bridge, when CDOS is the reasoning backend."""
        if self.voice is None:
            return None
        return getattr(self.voice.reasoner, "bridge", None)

    def computer_link(self):
        """The CDOS Computer link, when CDOS is the backend and it is enabled."""
        if self.voice is None:
            return None
        return getattr(self.voice.reasoner, "computer", None)

    def refresh_cdos(self, interval: float = 6.0) -> None:
        """Refresh the cached CDOS readings on a worker thread.

        Every reading costs a subprocess or a request to CDOS Computer, so this
        is throttled and never runs on the frame clock. Screens read
        :attr:`cdos_snapshot`, which is only ever replaced wholesale — a
        half-updated dict would flicker.
        """
        bridge = self.cdos_bridge()
        bridge_live = bridge is not None and bridge.available
        link = self.computer_link()
        link_live = link is not None and link.available
        if not bridge_live and not link_live:
            return
        now = time.monotonic()
        if now < self._cdos_next or self._cdos_busy:
            return
        self._cdos_next = now + interval
        self._cdos_busy = True

        def worker() -> None:
            snapshot: Dict[str, object] = {}
            try:
                if bridge_live:
                    try:
                        snapshot["godcore"] = bridge.godcore()
                        snapshot["weather"] = bridge.godcore_weather()
                        snapshot["system"] = bridge.system_rows()
                        snapshot["signals"] = bridge.signals()
                    except Exception as exc:
                        snapshot = {"error": f"{type(exc).__name__}: {exc}"}
                if link_live:
                    # Kept apart from "error": one half of CDOS failing must
                    # not blank the other half's panel.
                    try:
                        snapshot["computer"] = link.health()
                    except Exception as exc:
                        why = getattr(exc, "why", "") or f"{type(exc).__name__}: {exc}"
                        snapshot["computer_error"] = why
            finally:
                self._cdos_busy = False
            self.cdos_snapshot = snapshot
            self.redraw()

        threading.Thread(target=worker, daemon=True, name="cdos-read").start()

    # There is deliberately no accessor here for putting GODCORE on a screen.
    # It is the computer's own state, read by the AI (the Companion, Earth
    # Mind, game characters) straight from :attr:`cdos_snapshot`, and nothing
    # in the interface shows it. tests/test_godcore_private.py holds that.

    # ------------------------------------------------------------------
    # Pending choices (CDOS reports ambiguity rather than resolving it)
    # ------------------------------------------------------------------
    def refresh_pending(self) -> None:
        """Rebuild the option buttons from whatever the backend is waiting on."""
        self.pending_options = []
        self.pending_prompt = ""
        if self.voice is None:
            self.redraw()
            return

        options = self.voice.pending_options()
        if options:
            self.pending_prompt = "WHICH DID YOU MEAN?"
            self.pending_options = [
                (alt, (lambda a=alt: self.choose_reading(a))) for alt in options
            ]
        elif self.voice.pending is not None:
            proposal = self.voice.pending
            self.pending_prompt = f"CONFIRM · {proposal.command}"
            self.pending_options = [
                ("CONFIRM", self.confirm_pending),
                ("CANCEL", self.cancel_pending),
            ]
        self.redraw()

    def choose_reading(self, command_text: str) -> None:
        """The user picked one of the readings CDOS offered."""
        if self.voice is None:
            return
        self.user_said(command_text)
        self.thinking = True
        self.pending_options = []
        self.redraw()

        def worker() -> None:
            try:
                answer = self.voice.choose(command_text)
            except Exception as exc:
                answer = f"Reasoning subsystem error. {exc}"
            finally:
                self.thinking = False
            self.say(answer or "No response.")
            self.refresh_pending()

        threading.Thread(target=worker, daemon=True, name="cdos-choose").start()

    def confirm_pending(self) -> None:
        """A deliberate human press: the only way an effectful command runs."""
        if self.voice is None:
            return
        self.thinking = True
        self.pending_options = []
        self.redraw()

        def worker() -> None:
            try:
                answer = self.voice.confirm_pending()
            except Exception as exc:
                answer = f"Reasoning subsystem error. {exc}"
            finally:
                self.thinking = False
            self.say(answer or "No response.")
            self.refresh_pending()

        threading.Thread(target=worker, daemon=True, name="cdos-confirm").start()

    def cancel_pending(self) -> None:
        if self.voice is not None and hasattr(self.voice, "clear_pending"):
            self.voice.clear_pending()
        self.pending_options = []
        self.pending_prompt = ""
        self.say("Cancelled. Nothing was run.")
        self.redraw()

    def telemetry_summary(self) -> str:
        mon = self.monitor
        mem_frac, used, total = mon.memory()
        disk_frac, dused, dtotal = mon.disk()
        lines = [
            f"host={mon.hostname()}",
            f"distro={mon.distro()}",
            f"kernel={mon.kernel()}",
            f"cpu_model={mon.cpu_model()}",
            f"cpu_load={mon.cpu() * 100:.0f}%",
            f"loadavg={'/'.join(f'{v:.2f}' for v in mon.load())}",
            f"memory={sysinfo.human_bytes(used)} of {sysinfo.human_bytes(total)} "
            f"({mem_frac * 100:.0f}%)",
            f"disk={sysinfo.human_bytes(dused)} of {sysinfo.human_bytes(dtotal)} "
            f"({disk_frac * 100:.0f}%)",
            f"uptime={sysinfo.human_duration(mon.uptime())}",
            f"processes={mon.processes()}",
            f"local_time={time.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        temp = mon.temperature()
        if temp is not None:
            lines.append(f"temperature={temp:.0f}C")
        battery = mon.battery()
        if battery:
            lines.append(f"battery={battery[0] * 100:.0f}% {battery[1]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Individual actions
    # ------------------------------------------------------------------
    def user_dir(self, xdg_key: str, fallback: str) -> str:
        """Resolve an XDG user directory, falling back to the classic name."""
        config = Path.home() / ".config" / "user-dirs.dirs"
        try:
            for line in config.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"XDG_{xdg_key}_DIR="):
                    value = line.split("=", 1)[1].strip().strip('"')
                    return os.path.expandvars(value.replace("$HOME", str(Path.home())))
        except OSError:
            pass
        return str(Path.home() / fallback)

    def browse(self, path: str) -> None:
        browser = self.screens.get("browser")
        if browser is not None:
            browser.open(path)     # type: ignore[attr-defined]
            self.go("browser")

    def operator_name(self) -> str:
        """Who is using this machine, for the header.

        The configured value wins; otherwise the account's full name from the
        password database, falling back to the login name. This replaced a
        fixed registry number, which said the same thing on every machine and
        so said nothing at all.
        """
        configured = str(self.config.get("shell", "operator", "") or "").strip()
        if configured:
            return configured
        if self.capabilities is not None:
            login = self.capabilities.invoke("user.identity")
            if login.ok and login.value:
                full = self.capabilities.invoke("user.full_name",
                                                username=login.value)
                return str(full.value if full.ok and full.value else login.value)
        return os.environ.get("USER") or os.environ.get("USERNAME") or ""

    def set_continuous(self, value: bool) -> None:
        self.config.values.setdefault("voice", {})["continuous"] = value
        if self.voice is not None:
            self.voice.continuous = value
        self.say("Continuous command mode engaged." if value
                 else "Continuous command mode disengaged.")

    def scroll_active(self, direction: int) -> None:
        screen = self.screen()
        panel = getattr(screen, "transcript", None)
        if panel is not None:
            panel.scroll_by(direction * 240)
            self.redraw()

    def launch(self, command: str, name: str = "") -> bool:
        """Start an external program, detached from the shell.

        Takes a command *line*, so it is only for things that genuinely are
        one — a ``.desktop`` Exec, a configured program button.  Never use it
        for a path: see :meth:`open_path`, which does not split.

        *name* is the human label to remember it by; without one the command
        itself is used, which is right for anything launched by voice.
        """
        if not command:
            return False
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            self.status_message = f"cannot parse command: {exc}"
            self.redraw()
            return False
        started = self.spawn(argv, label=command)
        if started:
            self.remember_launch(name or Path(argv[0]).name, command)
        return started

    def remember_launch(self, name: str, command: str) -> None:
        """Add a successful launch to the recent list, and persist it.

        Persisting on every launch rather than on exit is deliberate: this
        shell is frequently the login session, and a session does not always
        get a tidy shutdown to write things out in.
        """
        if self.recent is None:
            return
        try:
            self.recent.record(name, command)
            self.recent.save()
        except Exception:
            pass          # the recent list is a convenience, never a blocker

    def spawn(self, argv: List[str], label: str = "") -> bool:
        """Start an already-parsed argv, detached. No shell anywhere.

        The one place in the shell that starts a program, and it does so by
        asking the platform for ``process.launch`` rather than by knowing how
        processes work here.
        """
        if not argv:
            return False
        label = label or " ".join(argv)
        if self.capabilities is None:
            self.status_message = f"cannot start programs: {self.platform_error}"
            self.redraw()
            return False

        result = self.capabilities.invoke(
            "process.launch", argv=list(argv), name=label)
        if not result.ok:
            self.status_message = result.error
            self.redraw()
            return False

        self.status_message = f"launched {label}"
        self.launched.append((time.time(), label))
        del self.launched[:-20]
        self.redraw()
        self._watch_launch(result.value, label, argv)
        # Catch the new window as soon as it maps rather than waiting for the
        # next poll. Without this, a program started from the PROGRAM INDEX
        # sat loose on top of the shell until something happened to poll.
        self.hand_over_soon(label)
        return True

    def _watch_launch(self, handle, label: str, argv: List[str]) -> None:
        """Notice a program that starts and immediately dies.

        Starting successfully only means the binary ran. A D-Bus activated
        program like GNOME Terminal exits straight away when there is no
        session bus to ask, so without this the shell reports "launched" for
        something the user never sees — which is worse than an error.
        """
        def watcher() -> None:
            try:
                code, detail = handle.wait(timeout=3.0)
                # Whatever happened, the watch is over — let go of the error
                # output so a long-lived chatty program is not left writing
                # into a buffer nobody empties.
                handle.release()
                if code is None or code == 0:
                    return              # still running, or ran and was happy
                detail = detail.splitlines()[-1] if detail else ""
                hint = ""
                if "DBUS_SESSION_BUS_ADDRESS" in detail or "session bus" in detail.lower():
                    hint = " — no session D-Bus bus in this session"
                self.status_message = (
                    f"{label} exited immediately ({code})"
                    f"{hint}: {detail}"[:200])
                self.say(f"{argv[0]} did not start. {detail or 'No error given.'}",
                         speak=False)
                self.redraw()
            except Exception:
                pass

        threading.Thread(target=watcher, daemon=True,
                         name=f"watch-{argv[0]}").start()

    #: Openers tried in order for :meth:`open_path`.  Defined in
    #: :mod:`earth_os.openers` so ``--doctor`` can read it without importing
    #: anything that needs GTK.
    OPENERS = OPENERS

    #: How long an opener is given to fail before it is believed. Openers that
    #: find no handler exit within a fraction of a second; one that is still
    #: running after this has handed the file to a program that is using it.
    OPEN_GRACE = 2.0

    def open_path(self, path: str,
                  on_done: Optional[Callable[[bool, str], None]] = None
                  ) -> Tuple[bool, str]:
        """Open a file or folder with the desktop's handler.

        The path is passed as its own argv element, so spaces, quotes and
        every other awkward character survive. Returns ``(ok, message)`` so a
        screen can report the outcome where the user is actually looking.

        An opener *starting* is not the file opening. Each one is watched for
        :attr:`OPEN_GRACE` seconds, and one that exits with an error ("no
        application registered for text/plain") passes the file to the next
        instead of being reported as success. Reporting the launch as the
        result is how FILES came to say "opening" for files that never opened.

        With *on_done*, every outcome — including the immediate ones, like
        a missing file — is delivered to *on_done* on the interface thread,
        and the watching happens off it. The return value then only says
        whether an attempt was started. Without *on_done*, the call blocks
        and the return value is the outcome.
        """
        def finish(ok: bool, message: str) -> Tuple[bool, str]:
            if on_done is not None:
                on_done(ok, message)
            return (ok, message)

        target = str(path)
        if self.capabilities is None:
            return finish(False, f"no platform adapter: {self.platform_error}")
        if not self.capabilities.invoke("filesystem.exists", path=target).value:
            return finish(False, f"no such path: {target}")

        openers = [o for o in self.OPENERS if shutil.which(o[0]) is not None]
        if not openers:
            return finish(False,
                          "no opener installed — try: sudo apt install xdg-utils")
        name = os.path.basename(target) or target

        if on_done is None or self.call_on_main is None:
            # Tests and headless rendering: there is no loop to come back to.
            return finish(*self._open_with(openers, target, name))

        post = self.call_on_main

        def worker() -> None:
            ok, message = self._open_with(openers, target, name)
            post(lambda: on_done(ok, message))

        threading.Thread(target=worker, daemon=True,
                         name="cdos-open").start()
        return (True, f"opening {name}...")

    def _open_with(self, openers: List[List[str]], target: str,
                   name: str) -> Tuple[bool, str]:
        """Try each opener in turn until one keeps the file."""
        problems: List[str] = []
        for opener in openers:
            result = self.capabilities.invoke(
                "process.launch", argv=[*opener, target])
            if not result.ok:
                problems.append(f"{opener[0]}: {result.error}")
                continue
            handle = result.value
            code, detail = handle.wait(timeout=self.OPEN_GRACE)
            # Still running, or finished: either way stop listening, so a
            # long-lived program is not left writing into a full pipe.
            handle.release()
            if code not in (None, 0):
                last = detail.splitlines()[-1].strip() if detail else ""
                if not last.startswith(f"{opener[0]}:"):
                    last = f"{opener[0]}: {last or f'exit {code}'}"
                problems.append(last)
                continue
            self.status_message = f"opened {name}"
            self.launched.append((time.time(), name))
            del self.launched[:-20]
            self.redraw()
            return (True, f"opened {name} with {opener[0]}")

        reason = "; ".join(problems)[:240]
        self.status_message = f"could not open {name}"
        self.redraw()
        return (False, f"could not open {name} — {reason}")

    def claim_screen(self) -> None:
        """Take the whole output under a tiling compositor.

        sway treats the shell as an ordinary window and tiles anything new
        beside it, halving the screen. The shell is meant to *be* the frame,
        so it claims the output and tells sway to float everything else.
        """
        from .windows import SELF_APP_IDS
        rect = self.windows.claim_screen(SELF_APP_IDS)
        if rect is not None:
            self.output_rect = rect
            self.redraw()

    @property
    def frame_mode(self) -> bool:
        """True when programs should live inside the LCARS chrome."""
        return (str(self.config.get("windows", "mode", "frame")) == "frame"
                and self.windows.can_place)

    def viewport(self) -> Optional[Tuple[int, int, int, int]]:
        """The rectangle programs are given, in screen pixels."""
        return self.frame_rect

    def poll_windows(self) -> None:
        """Refresh the window list and frame anything that just appeared.

        Programs have to land in the frame however they were started — from
        the LCARS launcher, from a file, or from a terminal the shell knows
        nothing about. Watching for new windows covers all of those, where
        hooking only the launch path covered one.

        Driven by the frame clock rather than by a screen, because a program
        opened while the PROGRAM INDEX is up has to be framed just the same —
        when only the main and task screens polled, launching from the index
        left the program loose on top of the shell. Each call does the work;
        the caller on the clock decides how often (:attr:`poll_due`).
        """
        if not self.windows.available:
            return
        current = self.windows.refresh()
        handles = {w.handle for w in current}

        if not self._seeded_windows:
            # First look: everything is "new" but none of it just opened.
            self._known_windows = handles
            self._seeded_windows = True
            return

        fresh = [w for w in current if w.handle not in self._known_windows]
        self._known_windows = handles

        # The framed program closed. Leaving it as the framed window drew an
        # empty black frame under its old title; instead the frame goes to
        # the program before it, now if the frame is on screen, otherwise
        # when the main screen next comes up.
        if (self.framed_window is not None
                and self.framed_window.handle not in handles and not fresh):
            self.framed_window = None
            if self.current == "main":
                self.restore_frame()
            else:
                self.redraw()

        if not fresh:
            return
        if time.monotonic() < self._capture_quiet_until:
            # A window that opens while the screen is being captured belongs
            # to the capture, not to the person. Framing it used to take them
            # off the screen they were capturing and onto the main one.
            return
        if not (self.frame_mode
                and bool(self.config.get("windows", "auto_frame", True))):
            return
        if self.exit_menu_open:
            # A program that opens while the menu is up must not take the
            # focus, and with it the front, from the question being asked.
            self._known_windows -= {w.handle for w in fresh}
            return
        # Newest last; frame it and let it have the focus.
        self.focus_program(fresh[-1])

    @property
    def poll_due(self) -> bool:
        """True when the clock should poll for windows again, and arms the
        next interval. Each poll shells out, so this keeps a 30fps frame
        clock down to roughly one look per second."""
        now = time.monotonic()
        if now < self._windows_next_poll:
            return False
        self._windows_next_poll = now + max(
            float(self.config.get("windows", "poll_interval", 1.2)), 0.2)
        return True

    def focus_program(self, window) -> Tuple[bool, str]:
        """Put a program in the frame and show the screen the frame is on.

        The frame is a hole in the *main* screen, so framing a program while
        the PROGRAM INDEX or SETTINGS is up would drop it on top of that
        screen's content. Going to main first is what makes a launched
        program appear to jump into the interface rather than land loose.
        """
        result = self.show_in_frame(window)
        if result[0]:
            self.go("main")
        return result

    def return_home(self, window=None) -> None:
        """Bring the shell back and show the main screen.

        Paired with the per-program HOME button: a program that was taken to
        full screen covers the shell entirely, so this drops it back into the
        frame (or out of fullscreen when there is no frame to drop into),
        raises the shell and lands on the main screen.
        """
        window = window or self.framed_window
        if window is not None and self.windows.available:
            if self.frame_mode:
                rect = self.viewport()
                self.windows.switch_to(window, frame=rect)
                self.framed_window = window
            else:
                self.windows.backend.fullscreen(window, False)
        self.handed_over_to = ""
        hook = getattr(self, "fullscreen_hook", None)
        if hook:
            hook()
        self.go("main")
        self.redraw()

    def show_in_frame(self, window) -> Tuple[bool, str]:
        """Put a program inside the LCARS frame and keep the chrome visible."""
        rect = self.viewport()
        ok, message = self.windows.switch_to(window, frame=rect)
        if ok:
            self.framed_window = window
            self.handed_over_to = ""
            self._frame_history = [h for h in self._frame_history
                                   if h != window.handle][-15:]
            self._frame_history.append(window.handle)
        self.status_message = message
        self.redraw()
        return (ok, message)

    def restore_frame(self) -> bool:
        """Bring a program back into the frame as the main screen comes up.

        Under sway the shell and the programs are all floating windows, and
        a floating window is raised whenever it is focused. Any press on
        another screen focuses the shell, so by the time BACK or HOME lands
        on the main screen the shell is lying on top of the framed program:
        the frame showed black, with the program's name still over it.

        So arriving on the main screen puts the program back in front. The
        one that was framed if it is still running; otherwise the last one
        that was; otherwise the newest program running at all. With nothing
        running, the frame is let go and the conversation comes back, rather
        than a black hole. Returns whether a program is in the frame.
        """
        if not (self.frame_mode and self.windows.available):
            return False
        if self.viewport() is None:
            return False
        running = self.windows.refresh()
        by_handle = {w.handle: w for w in running}
        window = None
        if self.framed_window is not None:
            window = by_handle.get(self.framed_window.handle)
        if window is None:
            for handle in reversed(self._frame_history):
                if handle in by_handle:
                    window = by_handle[handle]
                    break
        if window is None and running:
            window = running[-1]
        if window is None:
            self.framed_window = None
            self.redraw()
            return False
        ok, _ = self.show_in_frame(window)
        return ok

    def raise_shell(self) -> None:
        """Put the shell in front of the framed program, for leaving main.

        Leaving by a press already did this — the press focused the shell —
        but leaving by voice or a key did not, and the program stayed
        floating over whichever screen came up.
        """
        if self.framed_window is None or not self.windows.available:
            return
        backend = self.windows.backend
        if not hasattr(backend, "find_by_app"):
            return
        from .windows import SELF_APP_IDS
        own = backend.find_by_app(SELF_APP_IDS)
        if own is not None:
            backend.focus(own)

    def raise_framed(self) -> None:
        """Put the framed program back in front of the shell, in place.

        For a press on the main screen that stays on it — LISTEN, the volume
        spot — which focuses the shell and so lifts it over the program.
        """
        window = self.framed_window
        if window is None or not self.frame_mode or not self.windows.available:
            return
        if self.exit_menu_open:
            # The press that opened the menu ends here, and lifting the
            # program now would put it straight back over the menu.
            return
        self.windows.backend.focus(window)

    def break_out(self, window=None) -> None:
        """Let a program cover the whole screen, out of the frame."""
        window = window or self.framed_window
        if window is None:
            return
        ok, message = self.windows.break_out(window)
        self.status_message = message
        if ok:
            self.handed_over_to = getattr(window, "label", "")
            self.framed_window = None
            # Once the program covers the screen there is no LCARS left to
            # read a reminder from, so say it out loud before it does.
            # Once the program covers the screen the shell has no keyboard
            # focus, so a bare F1 goes to the program, not to us. The
            # compositor binding is the one that still works.
            self.say(f"Full screen. Press Super plus F1 to return to "
                     f"{self._display_name()}.")
        self.redraw()

    def minimise_for(self, window) -> None:
        """Hand the screen to another program.

        In frame mode the shell stays exactly where it is — it *is* the frame,
        and disappearing would strand the user with no way back except a key
        they may not know. Only outside frame mode does it step aside, and
        even then it does not minimise: it drops out of fullscreen and stays
        on screen, so there is always something to click.
        """
        if self.frame_mode:
            self.framed_window = window
            self.redraw()
            return
        self.handed_over_to = getattr(window, "label", "")
        hook = getattr(self, "unfullscreen_hook", None)
        if hook:
            hook()
        self.redraw()

    def hand_over_soon(self, hint: str = "", delay: float = 1.2,
                       before: Optional[set] = None) -> None:
        """Step aside once a just-launched program has had time to map.

        There is no event for "the window I asked for has appeared", so this
        waits briefly, then looks for a window that was not there before.

        In frame mode this runs whatever ``step_aside`` says: the setting is
        about the shell getting out of the way, and in frame mode it never
        does — the program is placed inside the chrome instead.

        *before* is the set of window handles to treat as already there. By
        default it is taken now; a caller that learns of the launch late —
        an opener that was watched for a couple of seconds first — passes
        the set from before it started, or the new window is already in it.
        """
        if not self.windows.available:
            return
        if not (self.frame_mode
                or bool(self.config.get("windows", "step_aside", True))):
            return
        if before is None:
            before = {w.handle for w in self.windows.windows}

        def worker() -> None:
            import time as _time
            for _ in range(int(delay / 0.3) + 6):
                _time.sleep(0.3)
                self.windows.refresh()
                fresh = [w for w in self.windows.windows if w.handle not in before]
                if fresh:
                    window = fresh[-1]
                    # Record it as seen, or the next poll treats it as new
                    # again and frames a window that is already framed.
                    self._known_windows |= {w.handle for w in self.windows.windows}
                    if self.frame_mode:
                        self.focus_program(window)
                    else:
                        self.windows.switch_to(window)
                        self.minimise_for(window)
                    return
            self.redraw()

        threading.Thread(target=worker, daemon=True, name="cdos-handover").start()

    def reclaim(self) -> None:
        """Bring the shell back to the front, fullscreen."""
        self.handed_over_to = ""
        if self.framed_window is not None and self.windows.available:
            # A program that broke out is put back in its frame.
            rect = self.viewport()
            if rect is not None:
                self.windows.switch_to(self.framed_window, frame=rect)
        hook = getattr(self, "fullscreen_hook", None)
        if hook:
            hook()
        self.redraw()

    def switch_to_program(self, name: str) -> bool:
        """Spoken: 'computer, switch to firefox'."""
        self.windows.refresh()
        window = self.windows.find(name)
        if window is None:
            self.say(f"No window matching {name}.")
            return False
        if self.frame_mode:
            ok, message = self.show_in_frame(window)
            self.say(message)
            return ok
        ok, message = self.windows.switch_to(window)
        self.say(message)
        if ok:
            self.minimise_for(window)
        return ok

    def log_off(self) -> None:
        self._end_session("system.logout")

    def shutdown(self) -> None:
        self.say("Initiating shutdown sequence.")
        self._end_session("system.shutdown")

    def reboot(self) -> None:
        self._end_session("system.reboot")

    #: What EXIT offers, in the order the buttons appear.
    EXIT_CHOICES = (
        ("LOG OUT", "system.logout"),
        ("SHUT DOWN", "system.shutdown"),
        ("REBOOT", "system.reboot"),
    )

    def exit_options(self) -> None:
        """EXIT: ask how to leave — log out, shut down or reboot.

        Nothing happens on the first press; it only offers the three, on the
        same strip every other confirmation uses. Pressing one of them is the
        deliberate press the effectful gate asks for, so the choice runs
        directly rather than asking a second time.

        The strip is drawn by the main screen, so EXIT pressed from anywhere
        else (the DIAGNOSTICS column) goes there to show it.
        """
        self.pending_prompt = "EXIT — LOG OUT, SHUT DOWN OR REBOOT?"
        self.pending_options = [
            (label, lambda c=capability: self._exit_chosen(c))
            for label, capability in self.EXIT_CHOICES
        ] + [("CANCEL", self.cancel_pending)]
        if self.current not in ("main", "claude", "comms"):
            self.go("main")
        self._mark_exit_menu()
        self._bring_menu_forward()
        self.redraw()

    # -- the exit menu is modal: nothing may cover it ----------------------
    @property
    def exit_menu_open(self) -> bool:
        """True while EXIT's strip (or its CONFIRM step) is what is showing."""
        return bool(self.pending_options) and self.pending_options is self._exit_options

    def _mark_exit_menu(self) -> None:
        self._exit_options = self.pending_options
        self._exit_grace = time.monotonic() + 0.6

    def _bring_menu_forward(self) -> None:
        """Put the shell in front of every program, whatever they are doing.

        The strip is drawn by the shell, along the bottom of its own window,
        and a program is a separate window that the compositor stacks by
        focus. So the strip was at the mercy of whichever program had been
        touched last: a framed program lay over it, a loose one too, and a
        fullscreen one covered everything. Nothing the shell draws can fix
        that; only putting its window in front can.

        Fullscreen is released first because a fullscreen program stays on
        top of whatever is focused next. While the menu is up nothing lifts
        the framed program back (see :meth:`raise_framed`); it returns the
        moment the menu is dismissed.
        """
        self.handed_over_to = ""
        if not self.windows.available:
            return
        backend = self.windows.backend
        try:
            for window in self.windows.refresh():
                if window.fullscreen:
                    backend.fullscreen(window, False)
            if hasattr(backend, "find_by_app"):
                from .windows import SELF_APP_IDS
                own = backend.find_by_app(SELF_APP_IDS)
                if own is not None:
                    backend.focus(own)
        except Exception as exc:
            self.status_message = f"could not bring the menu forward: {exc}"

    def dismiss_exit_menu(self, quiet: bool = True) -> bool:
        """Close the exit menu without choosing anything.

        For a press anywhere that is not the menu, and for the shell losing
        focus. It only closes it: the press that did so is not also acted on,
        so reaching for a button and missing cannot power the machine down —
        or press whatever was underneath. Returns whether a menu was open.
        """
        if not self.exit_menu_open:
            return False
        if self.voice is not None and hasattr(self.voice, "clear_pending"):
            self.voice.clear_pending()
        self.pending_options = []
        self.pending_prompt = ""
        self._exit_options = None
        if not quiet:
            self.say("Cancelled. Nothing was run.")
        # The program that was waiting behind the menu comes back.
        self.raise_framed()
        self.redraw()
        return True

    def shell_lost_focus(self) -> None:
        """The shell stopped being the focused window: look away, so close."""
        if self.exit_menu_open and time.monotonic() >= self._exit_grace:
            self.dismiss_exit_menu()

    def _exit_chosen(self, capability: str) -> None:
        if capability == "system.shutdown":
            self.say("Initiating shutdown sequence.")
        if self.capabilities is None:
            self.pending_options = []
            self.pending_prompt = ""
            self.quit()
            return
        self._end_session_confirmed(capability)

    def _end_session(self, capability: str) -> None:
        """Ask the platform to end the session — after a person confirms it.

        Powering the machine off is the most effectful thing this shell can
        do, and this used to reach the registry through ``invoke``, which is
        the ungated door.  The effect was that saying *"shut down"* powered
        the machine off on the strength of one phrase, with no press and no
        way back: a speech engine mishearing something close to it was enough.
        That contradicted the rule the catalogue marks these capabilities for
        in the first place — *no confidence score is ever high enough* — so
        the confirmation happens here, on the same strip everything else
        waiting on a person uses.

        Quitting on failure keeps the old promise: on a machine where the
        shell has no way to power itself down, ending the shell is the nearest
        thing to what was asked for, and better than appearing to ignore it.
        """
        if self.capabilities is None:
            self.quit()
            return

        spec = self.capabilities.spec(capability)
        # Always asks, even when something else is already waiting on the
        # strip. It used to skip the question whenever any choice was up —
        # so with EXIT's three choices showing, a misheard "shut down"
        # would have powered off with no press. The new question replaces
        # whatever was waiting.
        if spec is not None and spec.effectful:
            self.pending_prompt = f"CONFIRM · {spec.name.upper()} — {spec.summary}"
            self.pending_options = [
                ("CONFIRM", lambda: self._end_session_confirmed(capability)),
                ("CANCEL", self.cancel_pending),
            ]
            # Powering off asks, and the question must not be coverable
            # either, whether it came from EXIT or from a spoken "shut down".
            self._mark_exit_menu()
            self._bring_menu_forward()
            self.say(f"{spec.summary}?", speak=True)
            self.redraw()
            return
        self._end_session_confirmed(capability)

    def _end_session_confirmed(self, capability: str) -> None:
        """The deliberate press: the only way the machine actually goes down."""
        self.pending_options = []
        self.pending_prompt = ""
        result = self.capabilities.invoke(capability)
        if not result.ok:
            self.status_message = result.error
            self.quit()

    @property
    def is_the_session(self) -> bool:
        """True when this shell is the login session, not an app on a desktop.

        Set by the session script. It matters because quitting then ends the
        whole session rather than closing a window.
        """
        return (os.environ.get("XDG_SESSION_DESKTOP", "") == "earth-os"
                or os.environ.get("XDG_CURRENT_DESKTOP", "") == "EARTHOS")

    def deactivate(self) -> None:
        """Power down in shell mode, otherwise exit — but confirm first.

        DEACTIVATE sits in the button column where it is easy to hit, and when
        the shell is the session, pressing it ends the session and closes
        whatever else was open. That deserves a second press, not a surprise.
        """
        command = self._exit_command()
        if not self.pending_options:
            target = command[0] if command else "exit"
            self.pending_prompt = f"CONFIRM — HAND OVER TO {target.upper()}"
            self.pending_options = [
                (target.upper(), self._deactivate_confirmed),
                ("CANCEL", self.cancel_pending),
            ]
            self.redraw()
            return
        self._deactivate_confirmed()

    def _exit_command(self) -> List[str]:
        """What DEACTIVATE hands the machine over to, as an argv list."""
        raw = str(self.config.get("shell", "exit_command", "startx") or "").strip()
        if not raw:
            return []
        try:
            return shlex.split(raw)
        except ValueError:
            return [raw]

    def _deactivate_confirmed(self) -> None:
        """Leave the shell and start the handover program.

        The order matters. LCARS is normally holding the display, so the
        replacement cannot come up until this process has let go of it — which
        is why the command is started detached and the quit follows rather
        than the other way around. If the command cannot be started at all,
        quitting anyway would drop the user at a bare tty, so it says what
        went wrong and stays put.
        """
        self.pending_options = []
        self.pending_prompt = ""
        command = self._exit_command()
        if not command:
            self.quit()
            return

        if shutil.which(command[0]) is None:
            self.status_message = f"{command[0]} not found — staying put"
            self.say(f"{command[0]} is not installed. Remaining active.",
                     speak=False)
            self.redraw()
            return

        self.say(f"Handing over to {command[0]}.", speak=False)
        if self.capabilities is None:
            self.status_message = f"no platform adapter: {self.platform_error}"
            self.redraw()
            return
        # Nothing is captured from the handover program. This process is about
        # to exit, and a program left writing into a pipe belonging to a dead
        # process gets a broken pipe for its trouble — which would take down
        # the very thing the machine is being handed to.
        result = self.capabilities.invoke(
            "process.launch", argv=command, name=command[0],
            capture_stderr=False)
        if not result.ok:
            self.status_message = f"could not start {command[0]}: {result.error}"
            self.redraw()
            return
        self.quit()

    def quit(self) -> None:
        if self.quit_hook:
            self.quit_hook()

    # ------------------------------------------------------------------
    # Capabilities — the corpus's way in
    # ------------------------------------------------------------------
    def capability_catalogue(self) -> str:
        """What this machine can be asked to do, as text.

        Handed to a reasoning backend so it answers from what this computer
        actually has, rather than from what computers generally tend to have.
        """
        if self.capabilities is None:
            return f"no platform adapter: {self.platform_error}"
        return self.capabilities.describe()

    def run_capability_intent(self, text: str) -> bool:
        """Read an utterance as something the catalogue names, and do it.

        This is the path that makes the machine's own abilities speakable.
        Without it the catalogue was a list of things the computer could do
        and nobody could ask for: 31 of 38 capabilities had no words at all,
        and ``run_capability`` was reached only from tests.

        Returns whether the utterance was *understood*, not whether the work
        succeeded — an effectful capability that is now waiting on a press has
        been understood perfectly well, and the caller must not go on to ask a
        model what the person meant.
        """
        if self.capabilities is None or not text:
            return False
        intent = self.capability_corpus.resolve(text)
        if intent is None:
            return False

        # A reading that could not be taken. Say so rather than acting on a
        # value nobody measured; this is the same rule the adapter follows.
        if intent.explanation.startswith("cannot tell"):
            self.status_message = intent.explanation
            self.say(f"Unable to comply. {intent.explanation}")
            return True

        self.play("ack")
        if intent.analysis:
            if self._explained_by_mind(intent.analysis):
                self.converse(text)
                return True
            self.run_analysis(intent.analysis)
            return True
        if intent.capability == "display.screenshot":
            # The shell's own folder, and its fallback when the screen cannot
            # be captured whole; the bare capability has neither.
            self.take_screenshot()
            return True
        self.run_capability(intent.capability, **intent.arguments)
        return True

    def _explained_by_mind(self, analysis: str) -> bool:
        """Is *analysis* a diagnosis Earth Mind should have the Companion explain?

        The readings are taken the same way either way
        (:meth:`answer_analysis`); the difference is only whether the findings
        are then put into plain words.
        """
        from .capability_corpus import ANALYSES
        entry = ANALYSES.get(analysis)
        return bool(entry is not None and entry.explained and self.mind_enabled()
                    and self.config.get("mind", "explain", True)
                    and self.earth_mind() is not None)

    def answer_analysis(self, name: str, table: bool = False) -> Tuple[bool, str]:
        """Take an analysis's readings and put the answer into words.

        Every read is checked here, at the moment of running, rather than
        trusted from the table: it must be in the catalogue, take no
        argument, and **not be effectful**. An analysis looks at the machine;
        if one ever named something that changes it, this refuses before
        anything is invoked. That is what lets these run without the
        confirmation strip — and why there is no other way to run one.
        """
        from .capability_corpus import ANALYSES, READINGS
        analysis = ANALYSES.get(name)
        if analysis is None:
            return False, f"no such analysis: {name}"
        if self.capabilities is None:
            return False, f"no platform adapter: {self.platform_error}"
        for capability in analysis.reads:
            spec = self.capabilities.spec(capability)
            if spec is None or spec.effectful or spec.params \
                    or capability not in READINGS:
                return False, f"{name} may not read {capability}"
        values: Dict[str, object] = {}
        for capability in analysis.reads:
            result = self.capabilities.invoke(capability)
            if not result.ok:
                return False, f"{capability}: {result.error}"
            values[capability] = result.value
        return self.capability_corpus.explain(name, values, table=table)

    def run_analysis(self, name: str) -> bool:
        """Answer an analysis out loud. See :meth:`answer_analysis`."""
        ok, text = self.answer_analysis(name)
        self.status_message = f"{name} ok" if ok else text
        self.say(text if ok else f"Unable to comply. {text}")
        self.redraw()
        return ok

    def answer_question_as_text(self, text: str) -> Optional[Tuple[bool, str]]:
        """The terminal's way to ask the same question the voice can.

        None when *text* is not an analysis question at all, so the terminal
        goes on to its own translator. Only analyses are answered here: the
        terminal's English otherwise means shell commands, and taking that
        over wholesale would change what every existing phrase does there.
        """
        intent = self.capability_corpus.resolve_analysis(text)
        if intent is None:
            return None
        return self.answer_analysis(intent.analysis, table=True)

    def run_capability(self, capability: str, /, **kwargs) -> bool:
        """Invoke a named capability as a deliberate intent.

        The difference between this and calling the registry directly is the
        confirmation step. A capability marked effectful changes the machine
        in a way that is not trivially undone, so it never runs off the back
        of an interpretation alone, however confident that interpretation was
        — it goes to the same choice strip that everything else waiting on a
        person goes to.

        Returns whether the capability *ran*; an effectful one returns False
        here because at that point it has only been offered.
        """
        if self.capabilities is None:
            self.status_message = f"no platform adapter: {self.platform_error}"
            self.redraw()
            return False

        spec = self.capabilities.spec(capability)
        problem = self.capabilities.validate(capability, **kwargs)
        if problem:
            self.status_message = problem
            self.say(f"Unable to comply. {problem}")
            return False

        if spec is not None and spec.effectful:
            self.pending_prompt = f"CONFIRM · {spec.name.upper()} — {spec.summary}"
            self.pending_options = [
                ("CONFIRM", lambda: self._capability_confirmed(capability, **kwargs)),
                ("CANCEL", self.cancel_pending),
            ]
            self.say(f"{spec.summary}?", speak=True)
            self.redraw()
            return False

        return self._capability_confirmed(capability, **kwargs)

    def _capability_confirmed(self, capability: str, /, **kwargs) -> bool:
        self.pending_options = []
        self.pending_prompt = ""
        result = self.capabilities.invoke(capability, **kwargs)
        self.last_capability_result = result
        if not result.ok:
            self.status_message = result.error
            self.say(f"Unable to comply. {result.error}")
            self.redraw()
            return False
        self.play("ack")
        self.status_message = f"{capability} ok"
        # Say the answer, not the fact that an answer was obtained. A question
        # whose reply is "user.identity ok" has not been answered.
        from .capability_corpus import READINGS
        if result.value is not None or capability in READINGS:
            self.say(self.capability_corpus.say_result(capability, result.value))
        self.redraw()
        return True

    def run_capability_in_background(self, capability: str, /, *,
                                     on_done: Optional[Callable] = None,
                                     on_start: Optional[Callable] = None,
                                     **kwargs) -> bool:
        """:meth:`run_capability`, for work that takes seconds.

        Joining a wireless network can take thirty seconds and pairing a
        headset longer; done on the interface thread, the whole shell would
        freeze for that long. The gate is the same one: an effectful
        capability is only *offered* here, on the same strip as everything
        else, and runs when CONFIRM is pressed. Only the waiting moves.

        ``on_start`` runs on the interface thread when the work actually
        begins — after the press, for an effectful one, and never if it is
        cancelled. ``on_done`` gets the :class:`CapabilityResult`, also on the
        interface thread. Returns whether the work started now.
        """
        def refuse(reason: str) -> bool:
            self.status_message = reason
            if on_done is not None:
                on_done(CapabilityResult(ok=False, capability=capability,
                                         error=reason))
            self.redraw()
            return False

        if self.capabilities is None:
            return refuse(f"no platform adapter: {self.platform_error}")
        problem = self.capabilities.validate(capability, **kwargs)
        if problem:
            return refuse(problem)

        def start() -> None:
            self._start_in_background(capability, on_done, on_start, kwargs)

        spec = self.capabilities.spec(capability)
        if spec is not None and spec.effectful:
            self.pending_prompt = f"CONFIRM · {spec.name.upper()} — {spec.summary}"
            self.pending_options = [
                ("CONFIRM", start),
                ("CANCEL", self.cancel_pending),
            ]
            self.say(f"{spec.summary}?", speak=True)
            self.redraw()
            return False
        start()
        return True

    def _start_in_background(self, capability: str, on_done, on_start,
                             kwargs: Dict[str, object]) -> None:
        """The press has happened, or was never needed: do the work."""
        self.pending_options = []
        self.pending_prompt = ""
        if on_start is not None:
            on_start()

        def finish(result) -> None:
            self.last_capability_result = result
            if result.ok:
                self.status_message = f"{capability} ok"
            else:
                self.status_message = result.error
            if on_done is not None:
                on_done(result)
            self.redraw()

        if self.call_on_main is None:
            # Tests and headless rendering: there is no loop to come back to.
            finish(self.capabilities.invoke(capability, **kwargs))
            return

        post = self.call_on_main

        def worker() -> None:
            result = self.capabilities.invoke(capability, **kwargs)
            post(lambda: finish(result))

        threading.Thread(target=worker, daemon=True,
                         name=f"cdos-{capability}").start()
        self.redraw()

    # ------------------------------------------------------------------
    # Feedback over any screen, and screenshots
    # ------------------------------------------------------------------
    def toast(self, text: str, seconds: float = 4.0) -> None:
        self.toast_text = text
        self.toast_until = time.monotonic() + seconds
        self.redraw()

    def screenshot_dir(self) -> Path:
        from .screengrab import screenshot_folder
        return screenshot_folder()

    def notice_screenshots(self, interval: float = 1.0) -> None:
        """Say so on screen when a screenshot appears that this shell did not take.

        The session's Print key belongs to the compositor, which runs
        ``earth-os --screenshot`` rather than telling this process. Without
        this the picture would be saved in silence, and silence after pressing
        a key reads as the key not working. One stat a second, on the folder.
        """
        now = time.monotonic()
        if now < getattr(self, "_shots_next", 0.0):
            return
        self._shots_next = now + interval
        folder = self.screenshot_dir()
        try:
            stamp = folder.stat().st_mtime
        except OSError:
            return
        seen = getattr(self, "_shots_seen", None)
        self._shots_seen = stamp
        if seen is None or stamp <= seen:
            return
        try:
            newest = max(folder.glob("*.png"), key=lambda p: p.stat().st_mtime)
        except (OSError, ValueError):
            return
        if str(newest) != self.last_screenshot:
            self.screenshot_saved(str(newest), whole=True)

    def take_screenshot(self, delay: float = 0.4) -> None:
        """Capture the screen into ``Pictures/EARTH OS Screenshots``.

        The whole screen when this machine can — other programs included,
        which is what you want when showing someone what is wrong. When it
        cannot (no capture tool, or none that works in this session) the
        shell draws itself into the file instead and says which it did, so
        the button always produces a picture.

        The short delay lets the pressed button and the hover highlight clear
        first, so the picture shows the screen rather than the act of
        taking it.
        """
        folder = self.screenshot_dir()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = folder / f"cdos-{stamp}.png"
        # The screen it was pressed on is the screen it is of, whatever
        # happens on the display while the capture runs.
        screen_name = self.current
        self._capture_quiet_until = time.monotonic() + delay + 20.0

        def done(result) -> None:
            self._capture_quiet_until = time.monotonic() + 2.0
            if result.ok:
                self.screenshot_saved(str(result.value), whole=True)
                return
            reason = result.error
            try:
                from .screengrab import render_shell
                width, height = self.surface_size
                saved = render_shell(self, str(path), width, height,
                                     screen_name=screen_name)
            except Exception as exc:                # never take the shell down
                self.status_message = f"screenshot failed: {reason}; {exc}"
                self.toast("SCREENSHOT FAILED — SEE ENGINEERING", 6.0)
                self.say(f"Unable to take a screenshot. {reason}", speak=False)
                return
            self.screenshot_saved(saved, whole=False, reason=reason)

        def run() -> None:
            self.run_capability_in_background(
                "display.screenshot", on_done=done, path=str(path))

        if self.call_on_main is None or delay <= 0:
            run()
            return
        post = self.call_on_main

        def wait() -> None:
            time.sleep(delay)
            post(run)

        threading.Thread(target=wait, daemon=True, name="cdos-screenshot").start()

    def screenshot_saved(self, path: str, whole: bool = True,
                         reason: str = "") -> None:
        self.last_screenshot = path
        shown = path.replace(str(Path.home()), "~", 1)
        self.play("ack")
        if whole:
            self.status_message = f"screenshot saved: {shown}"
            self.toast(f"SCREENSHOT SAVED · {shown}", 5.0)
            self.say(f"Screenshot saved to {shown}.", speak=False)
        else:
            name = self._display_name()
            self.status_message = (f"saved the {name} screen only ({reason}): "
                                   f"{shown}")
            self.toast(f"{name.upper()} SCREEN SAVED · {shown}", 6.0)
            self.say(f"Saved the {name} screen to {shown}. The whole screen "
                     f"could not be captured: {reason}", speak=False)

    # ------------------------------------------------------------------
    # Compositions — rung 1 of docs/SELF_MODIFICATION.md
    # ------------------------------------------------------------------
    def run_composition(self, composition) -> bool:
        """Run a named sequence of capabilities.

        One confirmation for the whole composition, not one per step. Asking
        four times to run four steps somebody named once would train people to
        press through prompts without reading them, which is the failure
        ``effectful`` exists to avoid rather than cause.

        The confirmation is required if **any** step is effectful, which is
        what stops a composition being a laundering route: "tidy up" cannot
        contain ``system.shutdown`` and avoid the press.

        Returns whether the composition *ran*; an effectful one returns False
        because at that point it has only been offered.
        """
        if self.capabilities is None:
            self.status_message = f"no platform adapter: {self.platform_error}"
            self.redraw()
            return False

        # Check every step before running any of them. A composition that
        # fails at step three has already changed the machine twice, into a
        # state nobody has a name for — worse than not starting.
        problems = [
            f"{step.capability}: {problem}"
            for step in composition.steps
            for problem in (self.capabilities.validate(step.capability,
                                                       **step.arguments),)
            if problem]
        if problems:
            self.status_message = problems[0]
            self.say(f"Unable to comply. {problems[0]}")
            self.redraw()
            return False

        if composition.effectful:
            # Name what will actually happen, in the catalogue's own words.
            # "This will system.shutdown" tells somebody who already knows;
            # "this will power the computer off" tells somebody who does not,
            # and they are the person the confirmation is for.
            reason = ", and ".join(
                self.capabilities.spec(name).summary
                for name in composition.effectful_steps
                if self.capabilities.spec(name) is not None)
            count = len(composition.steps)
            self.pending_prompt = (
                f"CONFIRM · {composition.name.upper()} — "
                f"{count} step{'' if count == 1 else 's'}")
            self.pending_options = [
                ("CONFIRM", lambda: self._composition_confirmed(composition)),
                ("CANCEL", self.cancel_pending),
            ]
            summary = composition.summary or composition.name
            self.say(f"{summary}? This will {reason}.", speak=True)
            self.redraw()
            return False

        return self._composition_confirmed(composition)

    def _composition_confirmed(self, composition) -> bool:
        """Run the steps in order, stopping at the first one that fails.

        Carrying on past a failure would finish in a state that matches
        neither what was asked for nor what was there before. Stopping and
        saying which step failed is the same rule the platform layer follows:
        failure is data, never a fabricated success.
        """
        self.pending_options = []
        self.pending_prompt = ""
        self.play("ack")

        done: List[str] = []
        answers: List[str] = []
        from .capability_corpus import READINGS
        for step in composition.steps:
            result = self.capabilities.invoke(step.capability, **step.arguments)
            if not result.ok:
                trailer = (f" Completed {len(done)} of "
                           f"{len(composition.steps)} steps."
                           if done else "")
                self.status_message = f"{composition.name}: {result.error}"
                self.say(f"Unable to comply. {step.capability}: "
                         f"{result.error}.{trailer}")
                self.redraw()
                return False
            done.append(step.capability)
            if result.value is not None or step.capability in READINGS:
                answers.append(self.capability_corpus.say_result(
                    step.capability, result.value))

        self.status_message = f"{composition.name} ok ({len(done)} steps)"
        # A composition of readings is a question, so it answers. One of
        # actions is a thing done, so it acknowledges and stops talking.
        if answers:
            self.say(" ".join(answers))
        else:
            self.say(f"{composition.summary or composition.name}. Done.")
        self.redraw()
        return True

    def define_composition(self, name: str, steps, phrasings=(),
                           summary: str = "") -> bool:
        """Teach the machine a new name for things it can already do.

        Refusals come back as ``False`` with the reason in the status line,
        because every one of them is a rule worth reading: an unknown
        capability, a name shaped like a capability, a composition with no
        phrasing that nothing could ever ask for.
        """
        if self.compositions is None:
            self.status_message = "compositions are not enabled"
            return False
        from .compositions import CompositionError
        try:
            composition = self.compositions.define(
                name, steps, phrasings=phrasings, summary=summary)
        except CompositionError as exc:
            self.status_message = str(exc)
            self.say(f"Unable to comply. {exc}")
            self.redraw()
            return False
        self.compositions.save()
        count = len(composition.steps)
        mark = " It will ask first." if composition.effectful else ""
        self.say(f"Learned {composition.name}, {count} "
                 f"step{'' if count == 1 else 's'}.{mark}")
        self.redraw()
        return True

    # ------------------------------------------------------------------
    # World Model (docs/WORLD_MODEL.md)
    # ------------------------------------------------------------------
    def world_model(self):
        """The machine's picture of itself and its person, assembled now."""
        from .world_model import WorldModel
        return WorldModel(
            name=str(self.config.get("shell", "computer_name", "CDOS")),
            registry=self.capabilities, compositions=self.compositions,
            ledger=self.ledger, apps=self.apps,
            programs=self.config.programs())

    def reasoning_context(self) -> str:
        """What any reasoner is given: the live readings, then the world.

        The same block whichever model is answering, which is what makes the
        model replaceable — the context belongs to CDOS, not to the backend.
        """
        parts = [self.telemetry_summary()]
        if self.ledger is not None and \
                bool(self.config.get("world", "briefing", True)):
            try:
                parts.append(self.world_model().briefing())
            except Exception:
                pass                  # the world is context, never a blocker
        return "\n\n".join(parts)

    def handle_world_request(self, request) -> str:
        """Answer a remember / forget / recall sentence. Returns the reply."""
        ledger = self.ledger
        if ledger is None:
            return "Memory is switched off."
        verb, arg = request.verb, request.argument

        if verb == "remember":
            from .world_model import WorldModelError, guess_kind
            subject = ""
            if self.apps is not None:
                app = self.apps.find(arg)
                subject = app.name if app else ""
            try:
                ledger.remember(arg, kind=guess_kind(arg), subject=subject)
            except WorldModelError as exc:
                return f"Unable to comply. {exc}."
            ledger.save()
            return "Noted. Say forget that, and it is gone."

        if verb == "forget":
            gone = ledger.forget_matching(arg)
            if not gone:
                return f"I have nothing remembered about {arg}."
            ledger.save()
            return f"Forgotten. {len(gone)} note{'s' if len(gone) != 1 else ''} removed."

        if verb == "forget_all":
            count = ledger.forget_all()
            ledger.save()
            return f"Forgotten. {count} note{'s' if count != 1 else ''} removed. I know nothing about you."

        if verb == "recall":
            kept = ledger.remembered()
            if not kept:
                return "You have not asked me to remember anything."
            return "You asked me to remember: " + "; ".join(e.text for e in kept[-8:]) + "."

        if verb == "noticed":
            pending = ledger.pending()
            if not pending:
                return "Nothing noticed that you have not already answered."
            items = "; ".join(f"note {i + 1}, {e.text}" for i, e in enumerate(pending[:8]))
            return f"{items}. Say keep note, or discard note, and the number."

        if verb in ("keep", "discard"):
            pending = ledger.pending()
            index = int(arg) - 1 if arg.isdigit() else -1
            if not 0 <= index < len(pending):
                return f"There is no note {arg}."
            entry = pending[index]
            if verb == "keep":
                ledger.confirm(entry.id)
            else:
                ledger.forget(entry.id)
            ledger.save()
            return "Kept." if verb == "keep" else "Discarded."

        if verb == "self":
            world = self.world_model()
            snap = world.snapshot()
            caps = snap["self"]["capabilities"]
            usable = sum(1 for c in caps if c["supported"] is not False)
            apps = snap["applications"]
            games = [a["title"] for a in apps if a["kind"] == "game"]
            reply = (f"I am {world.name}. I can do {usable} of the {len(caps)} "
                     f"things in my catalogue")
            if snap["self"]["compositions"]:
                reply += f", and know {len(snap['self']['compositions'])} compositions"
            reply += "."
            if apps:
                reply += f" {len(apps)} applications are installed"
                reply += f", including {', '.join(games[:4])}." if games else "."
            return reply

        return "Unable to comply."
