"""Settings — the port of ``LCARSsettings.exe`` / "SPECIFY PARAMETERS".

Tabs for appearance, voice, audio and diagnostics.  Changes apply immediately;
``SAVE`` writes them back to ``config.toml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from .. import sysinfo
from ..theme import Palette, Strings, rgb_to_hex
from ..widgets import (
    BarRun, FlatButton, HalfPillButton, Label, RenderContext, StandardButton,
    TabStrip, TextButton, draw_text,
)
from ..widgets import shapes
from .base import Frame, Screen

TABS = ["APPEARANCE", "VOICE", "AUDIO", "DIAGNOSTICS"]


class SettingsScreen(Screen):
    name = "settings"
    title = "PARAMETERS"

    def __init__(self, app):
        super().__init__(app)
        self.tab = 0

    # ------------------------------------------------------------------
    def build(self, ctx: RenderContext, f: Frame) -> None:
        u = ctx.u
        cfg = self.app.config
        self.add_chrome(ctx, f, title=ctx.strings["mySettings"])
        cx, cy, cw, ch = f.content

        strip = TabStrip("tabs", (cx, cy, cw, u(1.8)), tabs=TABS,
                         on_change=self._switch)
        strip.index = self.tab
        self.g.add(strip)

        body_y = cy + u(2.4)
        body_h = ch - u(2.4) - u(2.6)

        if self.tab == 0:
            self._build_appearance(ctx, cx, body_y, cw, body_h)
        elif self.tab == 1:
            self._build_voice(ctx, cx, body_y, cw, body_h)
        elif self.tab == 2:
            self._build_audio(ctx, cx, body_y, cw, body_h)
        else:
            self.diag_rect = (cx, body_y, cw, body_h)

        self.g.add(StandardButton(
            "save", (cx, cy + ch - u(2.2), u(8.0), u(2.0)),
            label="SAVE", label_align="center", color="accent4",
            on_click=self.save,
        ))
        self.g.add(Label(
            "savepath", (cx + u(8.6), cy + ch - u(2.2), cw - u(8.6), u(2.0)),
            text=str(cfg.path), size=u(0.62), color="accent5",
            align="left", valign="middle", uppercase=False,
        ))

    def _switch(self, index: int, name: str) -> None:
        self.tab = index
        self.invalidate()
        self.app.redraw()

    # ------------------------------------------------------------------
    def _build_appearance(self, ctx, x, y, w, h) -> None:
        u = ctx.u
        self.g.add(TextButton("hdr_pal", (x, y, w, u(1.2)), label="Colour Palette",
                              color="accent3", label_color="accent4"))
        palettes = Palette.list_builtin()
        current = str(self.app.config.get("shell", "palette", "Standard Colors"))
        bw = (w - u(0.4) * (len(palettes) - 1)) / max(len(palettes), 1)
        for i, name in enumerate(palettes):
            self.g.add(StandardButton(
                f"pal_{i}", (x + i * (bw + u(0.4)), y + u(1.8), bw, u(1.8)),
                label=name, label_align="center",
                color="accent4" if name == current else "accent2",
                on_click=(lambda n=name: self._set_palette(n)),
            ))
        self.swatch_rect = (x, y + u(4.2), w, u(2.6))

        self.g.add(TextButton("hdr_lang", (x, y + u(7.6), w, u(1.2)),
                              label="Language File", color="accent3",
                              label_color="accent4"))
        langs = Strings.list_builtin()
        current_lang = str(self.app.config.get("shell", "language", "Standard"))
        bw = (w - u(0.4) * (len(langs) - 1)) / max(len(langs), 1)
        for i, name in enumerate(langs):
            self.g.add(StandardButton(
                f"lng_{i}", (x + i * (bw + u(0.4)), y + u(9.4), bw, u(1.8)),
                label=name, label_align="center",
                color="accent4" if name == current_lang else "accent3",
                on_click=(lambda n=name: self._set_language(n)),
            ))

        self.g.add(TextButton("hdr_misc", (x, y + u(12.2), w, u(1.2)),
                              label="Display", color="accent3",
                              label_color="accent4"))
        self._toggle(ctx, "cursor", "POINTER", "shell", "cursor",
                     (x, y + u(14.0), w * 0.48, u(1.8)))
        self._toggle(ctx, "shellmode", "SHELL MODE", "shell", "shell_mode",
                     (x + w * 0.52, y + u(14.0), w * 0.48, u(1.8)))

    def _build_voice(self, ctx, x, y, w, h) -> None:
        u = ctx.u
        cfg = self.app.config
        self.g.add(TextButton("hdr_v", (x, y, w, u(1.2)), label="Voice Subsystem",
                              color="accent3", label_color="accent4"))
        self._toggle(ctx, "venabled", "VOICE ENABLED", "voice", "enabled",
                     (x, y + u(1.8), w * 0.32, u(1.8)))
        self._toggle(ctx, "vcont", "CONTINUOUS", "voice", "continuous",
                     (x + w * 0.34, y + u(1.8), w * 0.32, u(1.8)))
        self._toggle(ctx, "vack", "ACK CHIRP", "voice", "acknowledge",
                     (x + w * 0.68, y + u(1.8), w * 0.32, u(1.8)))

        rows = [
            ("SPEECH TO TEXT", "voice", "stt", ["whisper", "vosk", "none"]),
            ("TEXT TO SPEECH", "voice", "tts", ["piper", "espeak", "none"]),
            ("REASONING", "voice", "llm", ["ollama", "cloud", "none"]),
        ]
        for r, (label, section, key, options) in enumerate(rows):
            ry = y + u(4.4) + r * u(2.6)
            self.g.add(Label(f"vl_{r}", (x, ry, w * 0.26, u(1.8)), text=label,
                             size=u(0.72), color="accent5", valign="middle"))
            current = str(cfg.get(section, key, options[0]))
            bw = (w * 0.70 - u(0.4) * (len(options) - 1)) / len(options)
            for i, option in enumerate(options):
                self.g.add(StandardButton(
                    f"v_{key}_{i}",
                    (x + w * 0.28 + i * (bw + u(0.4)), ry, bw, u(1.8)),
                    label=option, label_align="center",
                    color="accent4" if option == current else "accent2",
                    on_click=(lambda s=section, k=key, o=option: self._set(s, k, o)),
                ))
        self.voice_status_rect = (x, y + u(12.8), w, u(4.0))

    def _build_audio(self, ctx, x, y, w, h) -> None:
        u = ctx.u
        self.g.add(TextButton("hdr_a", (x, y, w, u(1.2)), label="Audio",
                              color="accent3", label_color="accent4"))
        self._toggle(ctx, "sounds", "SOUND EFFECTS", "shell", "sounds",
                     (x, y + u(1.8), w * 0.32, u(1.8)))

        levels = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        current = float(self.app.config.get("shell", "volume", 0.6))
        bw = (w - u(0.4) * (len(levels) - 1)) / len(levels)
        self.g.add(Label("vol_l", (x, y + u(4.2), w, u(1.2)), text="VOLUME",
                         size=u(0.72), color="accent5"))
        for i, level in enumerate(levels):
            self.g.add(StandardButton(
                f"vol_{i}", (x + i * (bw + u(0.4)), y + u(5.6), bw, u(1.8)),
                label=f"{int(level * 100)}%", label_align="center",
                color="accent4" if abs(level - current) < 0.01 else "accent2",
                on_click=(lambda v=level: self._set_volume(v)),
            ))

        self.g.add(Label("snd_l", (x, y + u(8.4), w, u(1.2)), text="TEST SOUNDS",
                         size=u(0.72), color="accent5"))
        names = self.app.sound.names() or ["beep"]
        bw = (w - u(0.4) * (len(names) - 1)) / len(names)
        for i, name in enumerate(names):
            self.g.add(StandardButton(
                f"snd_{i}", (x + i * (bw + u(0.4)), y + u(9.8), bw, u(1.8)),
                label=name, label_align="center", color="accent3",
                on_click=(lambda n=name: self.app.sound.play(n)),
            ))

    # ------------------------------------------------------------------
    def _toggle(self, ctx, key: str, label: str, section: str, name: str,
                rect) -> None:
        value = bool(self.app.config.get(section, name, False))
        self.g.add(StandardButton(
            key, rect, label=f"{label} · {'ON' if value else 'OFF'}",
            label_align="center", color="accent4" if value else "accent6",
            on_click=(lambda s=section, n=name: self._flip(s, n)),
        ))

    def _flip(self, section: str, name: str) -> None:
        value = not bool(self.app.config.get(section, name, False))
        self._set(section, name, value)

    def _set(self, section: str, key: str, value) -> None:
        self.app.config.values.setdefault(section, {})[key] = value
        if section == "shell" and key == "sounds":
            self.app.sound.enabled = bool(value)
        if section == "voice" and self.app.voice is not None:
            self.app.voice.reconfigure()
        self.invalidate()
        self.app.redraw()

    def _set_palette(self, name: str) -> None:
        self.app.set_palette(name)
        self.invalidate()

    def _set_language(self, name: str) -> None:
        self.app.set_language(name)
        self.invalidate()

    def _set_volume(self, value: float) -> None:
        self.app.config.values.setdefault("shell", {})["volume"] = value
        self.app.sound.volume = value
        self.app.sound.play("beep")
        self.invalidate()
        self.app.redraw()

    def save(self) -> None:
        from ..config import DEFAULT_TOML
        path = self.app.config.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._serialise(), encoding="utf-8")
            self.app.say(f"Parameters saved to {path}.", speak=False)
        except OSError as exc:
            self.app.say(f"Unable to save parameters: {exc}", speak=False)
        self.app.play("ack")

    def _serialise(self) -> str:
        """Write the live config back out as TOML (no external dependency)."""
        def fmt(value) -> str:
            if isinstance(value, bool):
                return "true" if value else "false"
            if isinstance(value, (int, float)):
                return str(value)
            return '"' + str(value).replace('"', '\\"') + '"'

        lines = ["# EARTH OS shell configuration", ""]
        for section, block in self.app.config.values.items():
            if isinstance(block, dict):
                if not block:
                    continue
                lines.append(f"[{section}]")
                for key, value in block.items():
                    if isinstance(value, (str, int, float, bool)):
                        lines.append(f"{key} = {fmt(value)}")
                lines.append("")
            elif isinstance(block, list):
                for item in block:
                    if isinstance(item, dict):
                        lines.append(f"[[{section}]]")
                        for key, value in item.items():
                            lines.append(f"{key} = {fmt(value)}")
                        lines.append("")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    def on_release(self, x: float, y: float, pressed) -> None:
        strip = self.g.get("tabs")
        if pressed is strip and strip is not None:
            strip.pressed = False
            self.app.play("beep")
            strip.click_at(x, y)   # type: ignore[attr-defined]
            return
        super().on_release(x, y, pressed)

    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        u = ctx.u
        if self.tab == 0 and hasattr(self, "swatch_rect"):
            x, y, w, h = self.swatch_rect
            slots = list(self.app.palette.colors.items())
            sw = (w - u(0.3) * (len(slots) - 1)) / max(len(slots), 1)
            for i, (slot, rgb) in enumerate(slots):
                sx = x + i * (sw + u(0.3))
                shapes.block(cr, sx, y, sw, h * 0.62, u(0.25))
                shapes.fill(cr, rgb)
                draw_text(cr, ctx, slot, sx + sw / 2, y + h * 0.68, u(0.52),
                          ctx.color("accent5"), align="center", valign="top")
                draw_text(cr, ctx, rgb_to_hex(rgb), sx + sw / 2, y + h * 0.68 + u(0.62),
                          u(0.5), ctx.color("text"), align="center", valign="top")
        elif self.tab == 1 and hasattr(self, "voice_status_rect"):
            x, y, w, h = self.voice_status_rect
            voice = self.app.voice
            rows = (voice.status_rows() if voice is not None
                    else [("VOICE ENGINE", "not constructed")])
            for i, (key, value) in enumerate(rows):
                ry = y + i * u(1.0)
                draw_text(cr, ctx, key, x, ry, u(0.66), ctx.color("accent5"),
                          align="left", valign="top")
                draw_text(cr, ctx, value, x + w, ry, u(0.66), ctx.color("text"),
                          align="right", valign="top", max_width=w * 0.7)
        elif self.tab == 3 and hasattr(self, "diag_rect"):
            x, y, w, h = self.diag_rect
            mon = self.app.monitor
            unhandled = self.app.dispatcher.unhandled()
            rows = [
                ("SHELL", "earth-os"),
                ("PALETTE", self.app.palette.name),
                ("LANGUAGE", self.app.strings.name),
                ("SOUND PLAYER", " ".join(self.app.sound.player or ["none"])),
                ("SOUNDS LOADED", ", ".join(self.app.sound.names()) or "none"),
                ("VOICE PHRASES", str(len(self.app.dispatcher.vocabulary.phrases))),
                ("WAKE WORDS", ", ".join(self.app.dispatcher.vocabulary.wake_words)),
                ("UNBOUND ACTIONS", ", ".join(unhandled) or "none"),
                ("CONFIG", str(self.app.config.path)),
                ("HOST", f"{mon.hostname()} · {mon.distro()}"),
                ("OPERATOR", self.app.operator_name() or "unknown"),
            ]
            for i, (key, value) in enumerate(rows):
                ry = y + i * u(1.15)
                draw_text(cr, ctx, key, x, ry, u(0.68), ctx.color("accent5"),
                          align="left", valign="top")
                draw_text(cr, ctx, value, x + w, ry, u(0.68), ctx.color("text"),
                          align="right", valign="top", max_width=w * 0.72)
