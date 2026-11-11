"""The terminal screen: a real shell inside the LCARS chrome.

This is CDOS's Universal Translator given a place in the interface. You type
plain English, the translator works out the shell command, the line shows you
what it decided, and the command runs. Ordinary shell commands pass straight
through untouched, so nothing is taken away from someone who already knows
the command they want.

Two things shape the implementation:

* **Output must stream.** Commands run in a worker thread that pushes lines
  onto a queue; ``tick`` drains that queue on the frame clock. The interface
  never blocks waiting for a command, so a slow ``find`` or a ``ping`` fills
  in progressively and the shell stays responsive throughout.

* **The screen must never be a trap.** ``Escape`` and the function keys are
  deliberately *not* swallowed, so every route out of this screen still
  works while the cursor is in the input line. This mirrors the rule the
  programs strip follows.

There is no pseudo-terminal here, so full-screen console programs (nano, vim,
top, less) cannot run inside this panel. Rather than hanging forever on one,
the screen recognises them and says where they will work instead.

Earth Mind (docs/EARTH_MIND.md) sits behind the same input line. A line is
tried in this order, and the first that claims it has it:

1. ``!command`` — the shell, exactly as typed, nothing consulted.
2. a real command (an option flag, a pipe, a redirect) — the shell.
3. something Earth Mind knows better than a command would: a question the
   machine answers from live readings, an installed program's name, a game
   character's name — or anything at all while a character is being talked to.
4. English the translator knows — the command it stands for, shown first.
5. a line that starts with a program on this machine, or a built-in — the shell.
6. anything else that reads as a sentence — Earth Mind: knowledge to CDOS,
   talk to the Companion.
7. otherwise — the shell, which will say it does not know the command.

Steps 1, 2, 4, 5 and 7 are 1.0's terminal, unchanged, and they are all there is
when Earth Mind is switched off, cannot be built, or TRANSLATOR is off. The
header shows which of them answered last. Nothing typed is ever *executed* by
Earth Mind: it answers, or it asks the shell's gated path for an operation,
and one that would change the machine waits for a typed ``yes``.
"""

from __future__ import annotations

import os
import queue
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from typing import Callable, Deque, List, Optional, Tuple

from ..translator import CommandTranslator
from ..widgets import (
    FlatButton, Label, RenderContext, ScrollPanel, StandardButton, TextButton,
    draw_text,
)
from .base import Frame, Screen

#: Monospace is not a preference here. Command output is laid out in columns
#: and the LCARS face is proportional, which turns `ls -la` into a mess.
MONO = "monospace"

#: Programs that need a real terminal. Without a pty they would either hang
#: forever or scribble escape codes into the scrollback, so they are caught
#: before they start and redirected somewhere they actually work.
NEEDS_TTY = {
    "nano", "vim", "vi", "nvim", "emacs", "pico", "joe",
    "top", "htop", "btop", "atop",
    "less", "more", "man", "watch",
    "ssh", "telnet", "tmux", "screen",
    "python", "python3", "ipython", "node", "irb", "psql", "mysql", "sqlite3",
}

#: How much scrollback to keep. Enough to scroll back through a big listing,
#: bounded so a runaway command cannot eat memory.
MAX_LINES = 4000

#: Built into the panel rather than run as programs.
BUILTINS = ("cd", "pwd", "clear", "help", "exit", "quit", "logout")

#: A second word no command takes as an argument: "who is…", "which are…".
#: With one of these after it, a line that starts with a program's name is a
#: sentence all the same.
SENTENCE_WORDS = {"is", "are", "was", "were", "do", "does", "did", "can", "could", "should",
                  "would", "will", "the", "my", "your", "you", "me", "about", "to", "it", "we"}

#: Commands that destroy something. Worked out from English, one of these
#: waits for a typed yes; typed as a command, it is the person's own.
DESTRUCTIVE = re.compile(r"^\s*(?:sudo\s+)?(?:rm|rmdir|shred|mkfs\S*|dd)\b")

KIND_COLORS = {
    "input": "accent2",
    "translate": "accent4",
    "output": "accent5",
    "error": "alert",
    "system": "accent3",
}


class TerminalScreen(Screen):
    name = "terminal"
    title = "TERMINAL"

    def __init__(self, app):
        super().__init__(app)
        self.translator = CommandTranslator()
        self.lines: Deque[Tuple[str, str]] = deque(maxlen=MAX_LINES)
        self.input = ""
        self.cursor = 0
        self.history: List[str] = []
        self.history_index: Optional[int] = None
        self.stash = ""
        self.translate_enabled = True
        self.cwd = os.path.expanduser("~")
        self.previous_cwd = self.cwd

        self._queue: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        self._proc: Optional[subprocess.Popen] = None
        self._worker: Optional[threading.Thread] = None
        #: Who answered the last line: SHELL, TRANSLATED, SYSTEM, CDOS,
        #: COMPANION, GAME · ELARA… Shown in the header.
        self.mode = "SHELL"
        #: True while Earth Mind is answering on its worker thread.
        self._thinking = False
        #: Something waiting for a typed yes: (what, on_yes, on_no).
        self._awaiting: Optional[Tuple[str, Callable[[], None],
                                       Optional[Callable[[], None]]]] = None
        self._input_rect: Tuple[float, float, float, float] = (0, 0, 0, 0)
        self._greeted = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_enter(self) -> None:
        if not self._greeted:
            self._greeted = True
            name = self.app.config.get("shell", "computer_name", "EARTH OS")
            self.emit("system", f"{name} TERMINAL — Universal Translator active")
            self.emit("system", "Type plain English or any shell command. "
                                "'help' for examples.")
            if self._mind() is not None:
                self.emit("system", "Earth Mind is listening: ask about this "
                                    "computer, ask a question, or just talk.")
            self.emit("system", "")

    @property
    def busy(self) -> bool:
        return self._thinking or (self._proc is not None and self._proc.poll() is None)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def build(self, ctx: RenderContext, f: Frame) -> None:
        u = ctx.u
        self.add_chrome(ctx, f, title="TERMINAL", subtitle="")

        x, y, w, h = f.content

        # Header carries the working directory, which is the one piece of
        # state you constantly need and would otherwise have to ask for.
        self.g.add(TextButton(
            "term_header", (x, y, w, u(1.3)),
            label=self._header_text(),
            color="accent3", label_color="accent4",
        ))

        controls_h = u(1.8)
        input_h = u(1.9)
        panel_y = y + u(1.3) + f.gap
        panel_h = h - u(1.3) - f.gap - input_h - f.gap - controls_h - f.gap

        self.panel = ScrollPanel(
            "term_scroll", (x, panel_y, w, panel_h),
            draw_content=self._draw_lines, bar_color="accent3",
        )
        self.g.add(self.panel)

        # The input line is drawn in the overlay (it changes every keystroke),
        # but it needs a reserved rectangle and a backing plate.
        input_y = panel_y + panel_h + f.gap
        self._input_rect = (x, input_y, w, input_h)
        self.g.add(FlatButton(
            "term_inputplate", (x, input_y, w, input_h),
            color="accent6", label="", enabled=False,
        ))

        # Controls along the bottom of the panel.
        cy = input_y + input_h + f.gap
        gap = u(0.4)
        buttons = [
            ("term_translate", self._translate_label(), "accent4", self.toggle_translate),
            ("term_clear", "CLEAR", "accent3", self.clear),
            ("term_interrupt", "INTERRUPT", "alert", self.interrupt),
            ("term_home", "HOME DIR", "accent2", self.go_home_dir),
        ]
        bw = (w - gap * (len(buttons) - 1)) / len(buttons)
        for i, (bname, label, color, callback) in enumerate(buttons):
            self.g.add(StandardButton(
                bname, (x + i * (bw + gap), cy, bw, controls_h),
                label=label, label_align="center",
                color=color, on_click=callback,
            ))

    def _header_text(self) -> str:
        where = self.cwd
        home = os.path.expanduser("~")
        if where == home:
            where = "~"
        elif where.startswith(home + os.sep):
            where = "~" + where[len(home):]
        if self._thinking:
            state = " · THINKING"
        else:
            state = " · RUNNING" if self.busy else ""
        return f"{where} · {self.mode}{state}"

    def _translate_label(self) -> str:
        return "TRANSLATOR ON" if self.translate_enabled else "TRANSLATOR OFF"

    # ------------------------------------------------------------------
    # Scrollback painting
    # ------------------------------------------------------------------
    def _draw_lines(self, cr, ctx: RenderContext, x: float, y: float,
                    w: float, offset: float) -> float:
        u = ctx.u
        size = u(0.62)
        line_h = size * 1.45
        pad = u(0.35)

        if not self.lines:
            return u(1.0)

        cursor_y = y + pad
        panel_h = self.panel.h if hasattr(self, "panel") else 0.0

        for kind, text in self.lines:
            # Skip lines scrolled out of view -- with thousands of lines of
            # scrollback, laying out every one of them every frame is what
            # would make this panel stutter.
            if cursor_y + line_h < y or (panel_h and cursor_y > y + offset + panel_h + line_h):
                cursor_y += line_h
                continue

            color = ctx.color(KIND_COLORS.get(kind, "accent5"))
            if text:
                draw_text(cr, ctx, text, x + pad, cursor_y, size, color,
                          align="left", valign="top",
                          max_width=w - pad * 2, letter_spacing=0.0,
                          family=MONO)
            cursor_y += line_h

        return cursor_y - y + pad

    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        # These two carry live state, so they are refreshed each frame rather
        # than forcing a whole rebuild of the screen.
        header = self.g.get("term_header")
        if header is not None:
            header.label = self._header_text()  # type: ignore[attr-defined]
        toggle = self.g.get("term_translate")
        if toggle is not None:
            toggle.label = self._translate_label()  # type: ignore[attr-defined]

        u = ctx.u
        x, y, w, h = self._input_rect
        if w <= 0:
            return

        size = u(0.7)
        pad = u(0.45)
        prompt = "···" if self.busy else ">"
        pcolor = ctx.color("accent4" if not self.busy else "accent6")
        pw, _ = draw_text(cr, ctx, prompt, x + pad, y + h / 2, size, pcolor,
                          align="left", valign="middle", letter_spacing=0.0,
                          family=MONO)

        text_x = x + pad + pw + u(0.4)
        text_w = w - (text_x - x) - pad

        shown = self.input
        # Keep the caret in view on a long line by scrolling the text left.
        visible_chars = max(int(text_w / (size * 0.62)), 8)
        start = 0
        if self.cursor > visible_chars:
            start = self.cursor - visible_chars
        shown = shown[start:start + visible_chars]

        if shown:
            draw_text(cr, ctx, shown, text_x, y + h / 2, size,
                      ctx.color("text"), align="left", valign="middle",
                      letter_spacing=0.0, family=MONO)

        # Block caret, blinking about twice a second, steady while a command
        # is running so it reads as "not accepting input right now".
        if not self.busy and int(time.time() * 2) % 2 == 0:
            col = self.cursor - start
            cw = size * 0.62
            caret_x = text_x + col * cw
            colr = ctx.color("accent4")
            cr.save()
            cr.set_source_rgba(colr[0], colr[1], colr[2], 0.85)
            cr.rectangle(caret_x, y + h / 2 - size * 0.55, max(cw * 0.85, 2.0),
                         size * 1.1)
            cr.fill()
            cr.restore()

    # ------------------------------------------------------------------
    # Frame clock: drain whatever the worker produced
    # ------------------------------------------------------------------
    def tick(self) -> None:
        drained = 0
        # Bounded so a firehose of output cannot stall a frame.
        while drained < 400:
            try:
                kind, text = self._queue.get_nowait()
            except queue.Empty:
                break
            self.lines.append((kind, text))
            drained += 1
        if drained and getattr(self, "panel", None) is not None:
            if self.panel.follow_tail:
                self.panel.scroll_to_end()

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------
    def emit(self, kind: str, text: str) -> None:
        self._queue.put((kind, text))

    def clear(self) -> None:
        self.lines.clear()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def go_home_dir(self) -> None:
        self.run_line("cd ~")

    def toggle_translate(self) -> None:
        self.translate_enabled = not self.translate_enabled
        self.emit("system", "Universal Translator "
                            + ("enabled" if self.translate_enabled else "disabled"))

    def interrupt(self) -> None:
        proc = self._proc
        if self._thinking:
            self.emit("system", "Earth Mind is still answering; that cannot be interrupted.")
            return
        if proc is None or proc.poll() is not None:
            self.emit("system", "Nothing is running.")
            return
        try:
            proc.terminate()
            self.emit("system", "Interrupted.")
        except Exception as exc:  # pragma: no cover - platform dependent
            self.emit("error", f"Could not interrupt: {exc}")

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------
    def on_key(self, keyval: int, name: str, ctrl: bool = False) -> bool:
        # Never swallow the ways out of this screen, nor the shell's own
        # function keys. A terminal you cannot leave is a trap.
        if name in ("Escape", "F1", "F2", "F3", "F4", "F5", "F6", "F7",
                    "F11", "F12", "Home_key_reserved"):
            return False

        if ctrl and name in ("c", "C"):
            if self.busy:
                self.interrupt()
            else:
                self.emit("input", self._prompt_echo(self.input) + "^C")
                self.input, self.cursor = "", 0
            return True
        if ctrl and name in ("l", "L"):
            self.clear()
            return True
        if ctrl and name in ("u", "U"):
            self.input = self.input[self.cursor:]
            self.cursor = 0
            return True
        if ctrl and name in ("a", "A"):
            self.cursor = 0
            return True
        if ctrl and name in ("e", "E"):
            self.cursor = len(self.input)
            return True
        if ctrl:
            # Leave every other Ctrl chord to the application (Ctrl+Q etc).
            return False

        if name in ("Return", "KP_Enter"):
            line = self.input
            self.input, self.cursor = "", 0
            self.history_index = None
            self.submit(line)
            return True
        if name == "BackSpace":
            if self.cursor > 0:
                self.input = (self.input[:self.cursor - 1]
                              + self.input[self.cursor:])
                self.cursor -= 1
            return True
        if name == "Delete":
            if self.cursor < len(self.input):
                self.input = (self.input[:self.cursor]
                              + self.input[self.cursor + 1:])
            return True
        if name == "Left":
            self.cursor = max(0, self.cursor - 1)
            return True
        if name == "Right":
            self.cursor = min(len(self.input), self.cursor + 1)
            return True
        if name in ("Home", "KP_Home"):
            self.cursor = 0
            return True
        if name in ("End", "KP_End"):
            self.cursor = len(self.input)
            return True
        if name == "Up":
            self._recall(-1)
            return True
        if name == "Down":
            self._recall(1)
            return True
        if name in ("Page_Up", "Prior"):
            if getattr(self, "panel", None):
                self.panel.scroll_by(-self.panel.h * 0.8)
            return True
        if name in ("Page_Down", "Next"):
            if getattr(self, "panel", None):
                self.panel.scroll_by(self.panel.h * 0.8)
            return True
        if name == "Tab":
            self._complete()
            return True

        # Printable ASCII. Gdk keyvals coincide with ASCII in this range, and
        # shifted keys already arrive as the shifted keysym, so this handles
        # capitals and symbols without needing the modifier state.
        if 0x20 <= keyval <= 0x7E:
            ch = chr(keyval)
            self.input = self.input[:self.cursor] + ch + self.input[self.cursor:]
            self.cursor += 1
            return True

        return False

    def _recall(self, direction: int) -> None:
        if not self.history:
            return
        if self.history_index is None:
            if direction > 0:
                return
            self.stash = self.input
            self.history_index = len(self.history) - 1
        else:
            self.history_index += direction

        if self.history_index < 0:
            self.history_index = 0
        elif self.history_index >= len(self.history):
            self.history_index = None
            self.input = self.stash
            self.cursor = len(self.input)
            return

        self.input = self.history[self.history_index]
        self.cursor = len(self.input)

    def _complete(self) -> None:
        """Complete the final word against the filesystem."""
        if not self.input or self.input.endswith(" "):
            return
        head, _, fragment = self.input.rpartition(" ")
        target = os.path.expanduser(fragment)
        directory = os.path.dirname(target) or self.cwd
        if not os.path.isabs(directory):
            directory = os.path.join(self.cwd, directory)
        stem = os.path.basename(target)

        try:
            candidates = [e for e in os.listdir(directory) if e.startswith(stem)]
        except OSError:
            return
        if not candidates:
            return

        if len(candidates) == 1:
            match = candidates[0]
            if os.path.isdir(os.path.join(directory, match)):
                match += "/"
        else:
            # Extend to the longest shared prefix, then show the options.
            match = os.path.commonprefix(candidates)
            if match == stem:
                self.emit("system", "  ".join(sorted(candidates)[:40]))
                return

        completed = os.path.join(os.path.dirname(fragment), match) \
            if os.path.dirname(fragment) else match
        self.input = (head + " " + completed) if head else completed
        self.cursor = len(self.input)

    def _prompt_echo(self, line: str) -> str:
        return f"> {line}"

    # ------------------------------------------------------------------
    # Running things
    # ------------------------------------------------------------------
    def submit(self, line: str) -> None:
        line = line.strip()
        self.emit("input", self._prompt_echo(line))
        if not line:
            return
        self.history.append(line)
        self.run_line(line)

    def run_line(self, line: str) -> None:
        if self.busy:
            self.emit("error", "A command is still running. Press INTERRUPT to stop it.")
            return

        # Something is waiting for a yes. Whatever was typed is the answer.
        if self._awaiting is not None:
            self._answer_approval(line)
            return

        # "!ls" is the shell, exactly as typed: nothing translated, nothing asked.
        if line.startswith("!"):
            self.mode = "SHELL"
            self._run_command(line[1:].strip())
            return

        command = line
        if self.translate_enabled:
            # "? why is the sky blue" is Earth Mind, whatever it looks like.
            if line.startswith("?") and self._ask_mind(line[1:].strip(), force=True):
                return
            if self._answer_question(line):
                return
            translated = self.translator.translate(line)
            if translated and translated != line:
                self.mode = "TRANSLATED"
                self.emit("translate", f"  translated → {translated}")
                if DESTRUCTIVE.match(translated):
                    self._hold_command(translated)
                    return
                command = translated
            elif self._ask_mind(line):
                return
            else:
                self.mode = "SHELL"
        else:
            self.mode = "SHELL"
        self._run_command(command)

    def _run_command(self, command: str) -> None:
        """A command line, as 1.0 ran it: built-ins here, the rest in a shell."""
        # -- built-ins that must run in-process ---------------------------
        head = command.split(maxsplit=1)[0] if command.split() else ""
        rest = command[len(head):].strip()

        if head in ("exit", "quit", "logout"):
            self.app.go_back()
            return
        if head == "clear":
            self.clear()
            return
        if head == "help":
            self._help()
            return
        if head == "cd":
            self._chdir(rest)
            return
        if head == "pwd" and not rest:
            self.emit("output", self.cwd)
            return

        if head in NEEDS_TTY:
            self.emit("error", f"'{head}' needs a full terminal, which this panel is not.")
            self.emit("system", "It will work in the CDOS command-line shell "
                                "(type 'cdos' in a terminal window), or from "
                                "the PROGRAM INDEX screen.")
            return

        self._spawn(command)

    # ------------------------------------------------------------------
    # Approval: a typed yes, for what would destroy or change something
    # ------------------------------------------------------------------
    def _hold(self, what: str, on_yes: Callable[[], None],
              on_no: Optional[Callable[[], None]] = None) -> None:
        self._awaiting = (what, on_yes, on_no)
        self.emit("system", f"  needs approval → {what}")
        self.emit("system", "  Type yes to go ahead; anything else cancels.")

    def _hold_command(self, command: str) -> None:
        """A destructive command worked out from English: nothing runs until a yes.

        The translator adds ``-i`` so that ``rm`` asks first — but this panel
        has no keyboard to give a program, so that question could never be
        answered and the deletion never happened. The question is asked here
        instead, and on a yes the command runs without the ``-i``.
        """
        approved = re.sub(r"^(\s*(?:sudo\s+)?rm) -(r?)i(?=\s)",
                          lambda m: m.group(1) + (" -r" if m.group(2) else ""), command)
        self._hold(approved, lambda: self._run_command(approved))

    def _answer_approval(self, line: str) -> None:
        what, on_yes, on_no = self._awaiting          # type: ignore[misc]
        self._awaiting = None
        if line.strip().lower() in ("yes", "y", "confirm"):
            on_yes()
            return
        if on_no is not None:
            on_no()
        self.emit("system", "Cancelled. Nothing was run.")

    # ------------------------------------------------------------------
    # Earth Mind
    # ------------------------------------------------------------------
    def _mind(self):
        """Earth Mind, or None when it is off, unbuildable, or the terminal opted out."""
        get = getattr(self.app, "earth_mind", None)
        if get is None or not bool(self.app.config.get("mind", "terminal", True)):
            return None
        try:
            return get()
        except Exception:
            return None

    def _reads_as_a_command(self, line: str) -> bool:
        """Does *line* start like something this machine can run?

        A built-in, or a program on the path — unless what follows is the
        second word of a sentence ("who is…") or the line ends in a question
        mark, which no command line does.
        """
        words = line.split()
        if not words:
            return False
        head = words[0]
        if head not in BUILTINS and not self.translator._is_executable(head):
            return False
        if line.rstrip().endswith("?"):
            return False
        return not (len(words) > 1 and words[1].lower() in SENTENCE_WORDS)

    def _ask_mind(self, line: str, force: bool = False, priority_only: bool = False) -> bool:
        """Hand *line* to Earth Mind if it is Earth Mind's. Returns whether it was taken."""
        mind = self._mind()
        if mind is None or not line:
            return False
        if not force and self.translator._looks_like_a_command(line):
            return False                      # a flag, a pipe, a redirect: a command
        try:
            route = mind.route(line, "terminal")
        except Exception:
            return False
        if not force:
            in_game = route.kind == "game"
            if priority_only and not (route.priority or in_game):
                return False
            if not (route.priority or in_game):
                if route.control == "handoff" or self._reads_as_a_command(line):
                    return False
                kind = getattr(route.intent, "kind", "chat")
                # Two unrecognised words are more likely a mistyped command
                # than a conversation; let the shell say so.
                if kind == "chat" and len(line.split()) < 3 and not line.rstrip().endswith(("?", "!", ".")):
                    return False

        if not route.slow:
            self._show(mind.handle(line, "terminal", route=route), line)
            return True

        self._thinking = True

        def worker() -> None:
            try:
                response = mind.handle(line, "terminal", route=route)
            except Exception as exc:          # a subsystem failing must not hang the panel
                self._queue.put(("error", f"Earth Mind: {type(exc).__name__}: {exc}"))
                self._thinking = False
                return
            self._show(response, line)
            self._thinking = False

        threading.Thread(target=worker, daemon=True, name="earth-mind-terminal").start()
        return True

    def _show(self, response, line: str) -> None:
        """Put an Earth Mind response on the scrollback, mode first."""
        if response.handoff and response.proposal is None:
            # Earth Mind read it as a task for the command path; here, that is the shell.
            self.mode = "SHELL"
            self._run_command(line)
            return
        self.mode = response.mode()
        if response.proposal is not None:
            self._carry_out(response)
            return
        where = "answered from live system readings" if response.source == "readings" \
            else response.source
        note = f"  earth mind → {response.mode().lower()} · {where}"
        if response.model_calls:
            note += f" · {response.model_calls} pass{'es' if response.model_calls != 1 else ''}"
        if response.checked is True:
            note += " · checked against the facts"
        elif response.checked is False and response.source == "model":
            note += " · figures beyond the corpus: marked unverified"
        elif response.checked is False:
            note += " · check failed: verified words used"
        self.emit("translate", note)
        hint = response.extra.get("hint") if isinstance(response.extra, dict) else ""
        if hint:
            self.emit("system", f"  {hint}")
        if response.detail and response.source == "model":
            # The verified findings first, then the Companion's explanation of them.
            for row in response.detail.splitlines():
                self.emit("output", row)
            self.emit("output", "")
        body = response.detail if (response.detail and response.source == "readings") else response.text
        for row in (body or "…").splitlines():
            self.emit("output" if response.ok else "error", row)

    def _carry_out(self, response) -> None:
        """A system operation Earth Mind asked for, taken down the shell's gated path."""
        proposal = response.proposal
        before = len(self.app.conversation)
        self.emit("translate", f"  earth mind → system · {proposal.summary or proposal.target}")
        ok, message = self.app.carry_out(proposal)
        self._echo_shell(before)
        if message:
            self.emit("output" if ok else "error", message)
        options = dict(self.app.pending_options)
        if "CONFIRM" in options:
            # Effectful: the shell has stopped at its choice strip. This screen
            # has no strip, so the same choice is offered as a typed yes.
            def confirmed() -> None:
                mark = len(self.app.conversation)
                options["CONFIRM"]()
                self._echo_shell(mark)
            self._hold(self.app.pending_prompt or proposal.summary, confirmed,
                       options.get("CANCEL"))

    def _echo_shell(self, start: int) -> None:
        """What the computer said in the main conversation since *start*, shown here too."""
        for entry in list(self.app.conversation)[start:]:
            if entry.get("role") == "computer":
                self.emit("output", str(entry.get("text", "")))

    def _answer_question(self, line: str) -> bool:
        """Questions about the machine get the shell's answer, not a command.

        *"What is using the most RAM?"* used to fall through the translator
        and run as a shell command called ``what``. The voice already knows
        how to answer it from the process list, so the terminal asks the
        same place rather than growing a second, drifting answer of its own.
        Something typed as a real command is never intercepted.

        With Earth Mind on, this is where it takes the lines it knows better
        than the translator would: an analysis, a diagnosis, an installed
        program, a character. With it off, this is 1.0's analysis answer.
        """
        if self._ask_mind(line, priority_only=True):
            return True
        if self._mind() is not None:
            return False
        ask = getattr(self.app, "answer_question_as_text", None)
        if ask is None or self.translator._looks_like_a_command(line):
            return False
        answer = ask(line)
        if answer is None:
            return False
        ok, text = answer
        self.mode = "SYSTEM"
        self.emit("translate", "  answered from live system readings")
        for row in text.splitlines():
            self.emit("output" if ok else "error", row)
        return True

    def _chdir(self, target: str) -> None:
        target = target.strip() or "~"
        try:
            parts = shlex.split(target)
            target = parts[0] if parts else "~"
        except ValueError:
            target = target.strip("'\"")

        if target == "-":
            target = self.previous_cwd

        target = os.path.expanduser(os.path.expandvars(target))
        if not os.path.isabs(target):
            target = os.path.join(self.cwd, target)

        try:
            resolved = os.path.realpath(target)
            # Distinguish "there is nothing here" from "that is a file" --
            # they are different mistakes and deserve different messages.
            if not os.path.exists(resolved):
                raise FileNotFoundError(resolved)
            if not os.path.isdir(resolved):
                raise NotADirectoryError(resolved)
            os.listdir(resolved)  # surfaces a permission problem now, not later
        except NotADirectoryError:
            self.emit("error", f"Not a directory: {target}")
            return
        except FileNotFoundError:
            self.emit("error", f"No such directory: {target}")
            return
        except PermissionError:
            self.emit("error", f"Permission denied: {target}")
            return
        except OSError as exc:
            self.emit("error", str(exc))
            return

        self.previous_cwd = self.cwd
        self.cwd = resolved
        self.emit("system", resolved)

    def _spawn(self, command: str) -> None:
        try:
            proc = subprocess.Popen(
                command, shell=True, cwd=self.cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                # No pty here, so anything that wants to read from the
                # keyboard gets EOF immediately rather than hanging the panel.
                stdin=subprocess.DEVNULL,
                text=True, errors="replace", bufsize=1,
            )
        except Exception as exc:
            self.emit("error", str(exc))
            return

        self._proc = proc
        self._worker = threading.Thread(
            target=self._pump, args=(proc,), daemon=True)
        self._worker.start()

    def _pump(self, proc: subprocess.Popen) -> None:
        """Read the child's output on a worker thread."""
        try:
            if proc.stdout is not None:
                for raw in proc.stdout:
                    self._queue.put(("output", raw.rstrip("\n")))
        except Exception as exc:  # pragma: no cover - i/o teardown races
            self._queue.put(("error", str(exc)))
        finally:
            try:
                code = proc.wait()
            except Exception:
                code = -1
            if code not in (0, None):
                self._queue.put(("system", f"[exit {code}]"))

    # ------------------------------------------------------------------
    def _help(self) -> None:
        for line in (
            "",
            "Type plain English and the translator works out the command:",
            "",
            "   Show me the files              →  ls -la",
            "   Go to downloads                →  cd ~/Downloads",
            "   What time is it?               →  date",
            "   Create a folder called work    →  mkdir -p work",
            "   How much disk space is left?   →  df -h",
            "   Search for error in log.txt    →  grep -rn error log.txt",
            "   Go back two directories        →  cd ../../",
            "",
            "Ordinary shell commands work too: ls, cd, git, cp, grep, apt.",
            "",
            "Earth Mind answers what is not a command (the header shows who answered):",
            "",
            "   What is using the most RAM?        live readings",
            "   Why is my computer running slowly? readings, then explained",
            "   Open Firefox                       an installed program, by name",
            "   What is 12 times 9?                CDOS: knowledge and sums",
            "   I had a long day                   the Companion",
            "   Talk to the shopkeeper             a game character; goodbye to leave",
            "   Switch to the technical assistant  another personality",
            "",
            "   !command   run it in the shell exactly as typed",
            "   ? words    ask Earth Mind, whatever the words look like",
            "",
            "Keys:  Up/Down history · Tab complete · Ctrl+C interrupt",
            "       Ctrl+L clear · Ctrl+A/E line start/end · Esc leave",
            "",
        ):
            self.emit("system", line)

    # ------------------------------------------------------------------
    def on_scroll(self, x: float, y: float, dy: float) -> bool:
        panel = self.g.get("term_scroll")
        if panel is not None and panel.contains(x, y):
            panel.scroll_by(dy * (self.frame.ctx.u(2.0) if self.frame else 30))
            return True
        return False
