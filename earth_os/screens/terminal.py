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
"""

from __future__ import annotations

import os
import queue
import shlex
import subprocess
import threading
import time
from collections import deque
from typing import Deque, List, Optional, Tuple

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
            self.emit("system", "")

    @property
    def busy(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

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
        state = " · RUNNING" if self.busy else ""
        return f"{where}{state}"

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

        command = line
        if self.translate_enabled:
            translated = self.translator.translate(line)
            if translated and translated != line:
                self.emit("translate", f"  translated → {translated}")
                command = translated

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
