"""COMMUNICATIONS — wireless networks and Bluetooth devices.

Two tabs over one layout: a list on the left, the selected entry and what can
be done with it on the right. Every act goes through
:class:`earth_os.comms.Comms`, which asks the capability layer; this module
only draws and routes presses, so nothing here knows how a network is joined.

Two things this screen has that others do not:

* **The confirmation strip.** Switching a radio off and forgetting a network
  are effectful, so they wait for CONFIRM. The strip used to be drawn only on
  the main screen, and a confirmation nobody can see is a press nobody can
  make — so it is drawn here too, under the controls that asked for it.
* **A keyboard.** A wireless password has to be typed, and the machines this
  shell is built for include 1024×600 touch panels with no keyboard attached.
  Physical keys work whenever the password field is active; the on-screen one
  is there for when there are none.
"""

from __future__ import annotations

import math
import time
from typing import List, Optional, Tuple

from ..widgets import (
    FlatButton, HalfPillButton, Label, RenderContext, StandardButton,
    TabStrip, TextButton, draw_text, text_size,
)
from .base import Frame, Screen

TABS = ["WIRELESS", "BLUETOOTH"]

#: The on-screen keyboard, by layer.
KEY_ROWS = {
    "lower": ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"),
    "upper": ("1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"),
    "symbol": ("!@#$%^&*()", "-_=+[]{}\\|", ";:'\",.<>/?", "`~€£¥§"),
}

ROW_COLOURS = ("accent3", "accent5", "accent2", "accent6")


def _short_kind(kind: str) -> str:
    """``audio-headset`` -> ``HEADSET``; the icon name BlueZ reports."""
    if not kind:
        return ""
    return kind.split("-")[-1].upper()


class CommsScreen(Screen):
    name = "comms"
    #: Draws the clock.
    live = True
    title = "COMMUNICATIONS"

    def __init__(self, app):
        super().__init__(app)
        self.tab = 0
        self.selected_ssid: Optional[str] = None
        self.selected_address: Optional[str] = None
        self.scroll = [0, 0]
        self.password = ""
        self.reveal = False
        self.typing = False
        self.keyboard = False
        self.layer = "lower"
        self._signature: Tuple = ()
        self._rows: List[Tuple[Tuple[float, float, float, float], str, Optional[int]]] = []
        self.notice: Optional[Tuple[Tuple[float, float, float, float], str, str]] = None
        self.detail_rect: Optional[Tuple[float, float, float, float]] = None
        self.field_rect: Optional[Tuple[float, float, float, float]] = None
        self.list_rect: Optional[Tuple[float, float, float, float]] = None
        self._rows_fit = 1
        self._info_y = 0.0

    @property
    def comms(self):
        return self.app.comms

    # ------------------------------------------------------------------
    def on_enter(self) -> None:
        self.comms.refresh()
        self.invalidate()

    def tick(self) -> None:
        if self.comms.refresh_due():
            self.comms.refresh()
        signature = self._current_signature()
        if signature != self._signature:
            self._signature = signature
            self.invalidate()
            self.app.redraw()

    def _current_signature(self) -> Tuple:
        c = self.comms
        wireless = c.wireless
        bt = c.bluetooth
        return (
            self.tab, self.selected_ssid, self.selected_address,
            tuple(self.scroll), self.typing, self.keyboard, self.layer,
            self.reveal, bool(self.password),
            tuple(sorted(c.busy.items())), tuple(sorted(c.note.items())),
            c.wifi_error, c.bt_error,
            (getattr(wireless, "hardware", None), getattr(wireless, "enabled", None),
             getattr(wireless, "ssid", None), getattr(wireless, "address", None)),
            tuple((p.ssid, p.signal // 5, p.in_use, p.saved, p.security)
                  for p in c.networks),
            (getattr(bt, "powered", None), getattr(bt, "name", None)),
            tuple((d.address, d.name, d.paired, d.connected) for d in c.devices),
            tuple(label for label, _ in self.app.pending_options),
            self.app.pending_prompt,
        )

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------
    def selected_network(self):
        for point in self.comms.networks:
            if point.ssid == self.selected_ssid:
                return point
        return None

    def selected_device(self):
        for device in self.comms.devices:
            if device.address == self.selected_address:
                return device
        return None

    @staticmethod
    def needs_password(point) -> bool:
        return bool(point is not None and point.security
                    and not point.saved and not point.in_use)

    def select_network(self, ssid: str) -> None:
        if ssid != self.selected_ssid:
            self.password = ""
            self.reveal = False
        self.selected_ssid = ssid
        self.typing = self.needs_password(self.selected_network())
        if not self.typing:
            self.keyboard = False
        self.invalidate()

    def select_device(self, address: str) -> None:
        self.selected_address = address
        self.invalidate()

    def switch_tab(self, index: int, _name: str = "") -> None:
        self.tab = index
        self.typing = False
        self.keyboard = False
        self.invalidate()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------
    def build(self, ctx: RenderContext, f: Frame) -> None:
        u = ctx.u
        self.add_chrome(ctx, f, title=ctx.strings.get("myComms", "COMMUNICATIONS"))
        cx, cy, cw, ch = f.content
        self._rows = []
        self.notice = None
        self.field_rect = None

        strip = TabStrip("tabs", (cx, cy, cw, u(1.8)), tabs=TABS,
                         on_change=self.switch_tab)
        strip.index = self.tab
        self.g.add(strip)

        head_y = cy + u(2.4)
        head_h = u(1.5)
        body_y = head_y + head_h + u(0.7)
        body_h = cy + ch - body_y
        gap = u(0.9)
        list_w = cw * 0.56
        detail_x = cx + list_w + gap
        detail_w = cw - list_w - gap
        self.list_rect = (cx, body_y, list_w, body_h)
        self.detail_rect = (detail_x, body_y, detail_w, body_h)

        if self.tab == 0:
            self._build_wireless(ctx, cx, cw, head_y, head_h)
        else:
            self._build_bluetooth(ctx, cx, cw, head_y, head_h)
        self._build_confirm(ctx)

    # -- shared pieces -----------------------------------------------------
    def _header(self, ctx: RenderContext, cx: float, cw: float, y: float,
                h: float, text: str, buttons) -> None:
        """The status line, with its buttons along the right."""
        u = ctx.u
        bw = u(7.6)
        gap = u(0.4)
        width = cw - (bw + gap) * len(buttons)
        self.g.add(TextButton("head", (cx, y, width - gap, h), label=text,
                              color="accent3", label_color="accent4"))
        for i, (name, label, colour, enabled, action) in enumerate(buttons):
            self.g.add(StandardButton(
                name, (cx + width + i * (bw + gap), y, bw, h),
                label=label, label_align="center", color=colour,
                enabled=enabled, on_click=action,
            ))

    def _rows_area(self, ctx: RenderContext, count: int) -> Tuple[float, float, int, bool]:
        """Row height, gap, how many fit, and whether paging is needed."""
        u = ctx.u
        _x, _y, _w, h = self.list_rect
        row_h = u(1.9)
        gap = u(0.3)
        fit = max(int((h + gap) // (row_h + gap)), 1)
        paged = count > fit
        if paged:
            fit = max(fit - 1, 1)            # the last row holds the pager
        self._rows_fit = fit
        return row_h, gap, fit, paged

    def _pager(self, ctx: RenderContext, total: int, fit: int,
               y: float, h: float) -> None:
        u = ctx.u
        x, _y, w, _h = self.list_rect
        start = self.scroll[self.tab]
        half = (w - u(0.4)) / 2
        self.g.add(StandardButton(
            "page_up", (x, y, half, h), label="PREVIOUS", label_align="center",
            color="accent4", enabled=start > 0,
            on_click=lambda: self.page(-1)))
        self.g.add(StandardButton(
            "page_down", (x + half + u(0.4), y, half, h),
            label=f"NEXT · {max(total - start - fit, 0)} MORE",
            label_align="center", color="accent4",
            enabled=start + fit < total, on_click=lambda: self.page(1)))

    def page(self, direction: int) -> None:
        total = len(self.comms.networks if self.tab == 0 else self.comms.devices)
        start = self.scroll[self.tab] + direction * self._rows_fit
        self.scroll[self.tab] = max(0, min(start, max(total - self._rows_fit, 0)))
        self.invalidate()

    def _set_notice(self, title: str, detail: str = "") -> None:
        self.notice = (self.list_rect, title, detail)

    def _action_row(self, ctx: RenderContext, y: float, buttons) -> float:
        u = ctx.u
        x, _y, w, _h = self.detail_rect
        gap = u(0.4)
        h = u(2.0)
        count = max(len(buttons), 1)
        bw = (w - gap * (count - 1)) / count
        for i, (name, label, colour, enabled, action) in enumerate(buttons):
            self.g.add(StandardButton(
                name, (x + i * (bw + gap), y, bw, h), label=label,
                label_align="center", color=colour, enabled=enabled,
                on_click=action))
        return y + h + u(0.5)

    def _build_confirm(self, ctx: RenderContext) -> None:
        """The CONFIRM / CANCEL strip, along the bottom of the detail panel."""
        options = list(self.app.pending_options)
        if not options:
            return
        u = ctx.u
        x, y, w, h = self.detail_rect
        strip_h = u(3.4)
        top = y + h - strip_h
        self.g.add(Label(
            "confirm_prompt", (x, top, w, u(1.1)),
            text=self.app.pending_prompt or "CONFIRM", size=u(0.66),
            color="alert", align="left", valign="middle"))
        gap = u(0.4)
        bw = (w - gap * (len(options) - 1)) / len(options)
        for i, (label, callback) in enumerate(options):
            colour = ("alert" if label in ("CONFIRM", "ALLOW")
                      else "accent6" if label in ("CANCEL", "DENY") else "accent3")
            self.g.add(StandardButton(
                f"confirm_{i}", (x + i * (bw + gap), top + u(1.3), bw, u(2.0)),
                label=label, label_align="center", color=colour,
                on_click=callback))

    # -- wireless ------------------------------------------------------------
    def _build_wireless(self, ctx: RenderContext, cx: float, cw: float,
                        head_y: float, head_h: float) -> None:
        u = ctx.u
        c = self.comms
        state = c.wireless
        busy = c.busy.get("wifi", "")
        enabled = bool(getattr(state, "enabled", False))

        if state is None:
            head = "WIRELESS · UNAVAILABLE"
        elif not state.hardware:
            head = "WIRELESS · NO ADAPTER"
        elif not enabled:
            head = "WIRELESS · RADIO OFF"
        elif state.ssid:
            head = f"WIRELESS · ONLINE · {state.ssid}"
            if state.address:
                head += f" · {state.address}"
        else:
            head = "WIRELESS · NOT CONNECTED"

        can_switch = state is not None and state.hardware and not busy
        self._header(ctx, cx, cw, head_y, head_h, head, [
            ("power", "SWITCH OFF" if enabled else "SWITCH ON",
             "accent6" if enabled else "accent2", can_switch,
             lambda: c.wifi_power(not enabled)),
            ("scan", busy or "SCAN", "accent4",
             enabled and not busy, c.scan_wifi),
        ])

        if self.keyboard and self.typing:
            self._build_keyboard(ctx)
        elif state is None:
            self._set_notice("WIRELESS UNAVAILABLE", c.wifi_error)
        elif not state.hardware:
            self._set_notice("NO WIRELESS ADAPTER",
                             "This computer has no wireless device that "
                             "NetworkManager can see.")
        elif not enabled:
            self._set_notice("WIRELESS RADIO IS OFF",
                             "Press SWITCH ON to search for networks.")
        elif not c.networks:
            self._set_notice("SCANNING" if busy else "NO NETWORKS IN RANGE",
                             c.wifi_error or "Press SCAN to search again.")
        else:
            self._build_network_rows(ctx)

        self._build_network_detail(ctx)

    def _build_network_rows(self, ctx: RenderContext) -> None:
        x, y, w, _h = self.list_rect
        networks = self.comms.networks
        row_h, gap, fit, paged = self._rows_area(ctx, len(networks))
        start = min(self.scroll[0], max(len(networks) - fit, 0))
        self.scroll[0] = start
        for i, point in enumerate(networks[start:start + fit]):
            ry = y + i * (row_h + gap)
            if point.ssid == self.selected_ssid:
                colour = "accent4"
            elif point.in_use:
                colour = "accent2"
            else:
                colour = ROW_COLOURS[(start + i) % len(ROW_COLOURS)]
            self.g.add(HalfPillButton(
                f"net_{i}", (x, ry, w, row_h), side="left",
                label=point.ssid, label_align="left", uppercase=False,
                color=colour,
                on_click=(lambda s=point.ssid: self.select_network(s)),
            ))
            parts = [point.security or "OPEN"]
            if point.saved:
                parts.append("SAVED")
            if point.in_use:
                parts.append("CONNECTED")
            self._rows.append(((x, ry, w, row_h), "  ·  ".join(parts),
                               point.signal))
        if paged:
            self._pager(ctx, len(networks), fit, y + fit * (row_h + gap), row_h)

    def _build_network_detail(self, ctx: RenderContext) -> None:
        u = ctx.u
        c = self.comms
        x, y, w, _h = self.detail_rect
        point = self.selected_network()
        busy = c.busy.get("wifi", "")
        if point is None:
            self.selected_ssid = None
            self.g.add(TextButton("sel_head", (x, y, w, u(1.3)),
                                  label="NO NETWORK SELECTED",
                                  color="accent5", label_color="accent5"))
            self._info_y = y + u(1.9)
            return

        self.g.add(TextButton("sel_head", (x, y, w, u(1.3)), label=point.ssid,
                              uppercase=False, color="accent4",
                              label_color="accent4"))
        self._info_y = y + u(1.9)
        cursor = y + u(1.9) + u(1.05) * 3 + u(0.6)

        if self.needs_password(point):
            fh = u(2.0)
            show_w = u(5.6)
            self.field_rect = (x, cursor, w - show_w - u(0.4), fh)
            self.g.add(FlatButton(
                "pw_field", self.field_rect, color="accent5", radius=fh * 0.2,
                on_click=self.focus_password))
            self.g.add(StandardButton(
                "pw_show", (x + w - show_w, cursor, show_w, fh),
                label="HIDE" if self.reveal else "SHOW", label_align="center",
                color="accent3", on_click=self.toggle_reveal))
            cursor += fh + u(0.5)
            self.g.add(StandardButton(
                "pw_keyboard", (x, cursor, w, u(1.6)),
                label="HIDE KEYBOARD" if self.keyboard else "ON-SCREEN KEYBOARD",
                label_align="center", color="accent5",
                on_click=self.toggle_keyboard))
            cursor += u(1.6) + u(0.6)

        buttons = []
        if point.in_use:
            buttons.append(("disconnect", "DISCONNECT", "accent6", not busy,
                            c.disconnect_wifi))
        else:
            ready = not busy and (not self.needs_password(point) or bool(self.password))
            buttons.append(("connect", "CONNECT", "accent2", ready, self.connect))
        if point.saved:
            buttons.append(("forget", "FORGET", "accent6", not busy,
                            lambda s=point.ssid: c.forget_wifi(s)))
        cursor = self._action_row(ctx, cursor, buttons)
        self._note_y = cursor

    def connect(self) -> None:
        point = self.selected_network()
        if point is None:
            return
        password = self.password if self.needs_password(point) else ""
        if self.needs_password(point) and not password:
            self.focus_password()
            return
        self.keyboard = False
        self.comms.connect_wifi(point.ssid, password)
        self.invalidate()

    def focus_password(self) -> None:
        self.typing = True
        self.invalidate()

    def toggle_reveal(self) -> None:
        self.reveal = not self.reveal
        self.invalidate()

    def toggle_keyboard(self) -> None:
        self.keyboard = not self.keyboard
        self.typing = True
        self.invalidate()

    # -- on-screen keyboard -------------------------------------------------
    def _build_keyboard(self, ctx: RenderContext) -> None:
        u = ctx.u
        x, y, w, h = self.list_rect
        rows = KEY_ROWS[self.layer]
        gap = u(0.3)
        kw = (w - gap * 9) / 10
        kh = min(u(2.4), (h - gap * 4) / 5)
        for r, keys in enumerate(rows):
            indent = (w - (len(keys) * kw + (len(keys) - 1) * gap)) / 2
            for k, ch in enumerate(keys):
                # A rounded block rather than a pill: a pill's label inset is
                # sized for words and leaves no room for a "w" in a key.
                self.g.add(FlatButton(
                    f"key_{r}_{k}",
                    (x + indent + k * (kw + gap), y + r * (kh + gap), kw, kh),
                    label=ch, label_align="center", label_valign="middle",
                    uppercase=False, radius=kh * 0.3,
                    color=ROW_COLOURS[r % len(ROW_COLOURS)],
                    on_click=(lambda c=ch: self.type_text(c))))
        last = y + 4 * (kh + gap)
        specials = [
            ("key_shift", "ABC" if self.layer == "upper" else "SHIFT", 1.6,
             lambda: self.set_layer("lower" if self.layer == "upper" else "upper")),
            ("key_symbols", "ABC" if self.layer == "symbol" else "#+=", 1.6,
             lambda: self.set_layer("lower" if self.layer == "symbol" else "symbol")),
            ("key_space", "SPACE", 3.2, lambda: self.type_text(" ")),
            ("key_delete", "DELETE", 1.8, self.backspace),
            ("key_join", "JOIN", 1.8, self.connect),
        ]
        total = sum(weight for _, _, weight, _ in specials)
        avail = w - gap * (len(specials) - 1)
        sx = x
        for name, label, weight, action in specials:
            sw = avail * weight / total
            colour = "accent2" if name == "key_join" else (
                "accent6" if name == "key_delete" else "accent4")
            self.g.add(StandardButton(name, (sx, last, sw, kh), label=label,
                                      label_align="center", color=colour,
                                      label_size=0.42, on_click=action))
            sx += sw + gap

    def set_layer(self, layer: str) -> None:
        self.layer = layer
        self.invalidate()

    def type_text(self, text: str) -> None:
        self.typing = True
        self.password += text
        # One-shot shift, the way a phone keyboard does it.
        if self.layer == "upper":
            self.layer = "lower"
        self.invalidate()

    def backspace(self) -> None:
        self.password = self.password[:-1]
        self.invalidate()

    # -- bluetooth -----------------------------------------------------------
    def _build_bluetooth(self, ctx: RenderContext, cx: float, cw: float,
                         head_y: float, head_h: float) -> None:
        c = self.comms
        state = c.bluetooth
        busy = c.busy.get("bluetooth", "")
        powered = bool(getattr(state, "powered", False))

        if state is None:
            head = "BLUETOOTH · UNAVAILABLE"
        elif not powered:
            head = "BLUETOOTH · OFF"
        else:
            connected = [d.name for d in c.devices if d.connected]
            head = f"BLUETOOTH · ON · {state.name}" if state.name else "BLUETOOTH · ON"
            if connected:
                head += f" · {len(connected)} CONNECTED"

        self._header(ctx, cx, cw, head_y, head_h, head, [
            ("power", "SWITCH OFF" if powered else "SWITCH ON",
             "accent6" if powered else "accent2",
             state is not None and not busy,
             lambda: c.bt_power(not powered)),
            ("scan", busy or "SCAN", "accent4",
             powered and not busy, c.scan_bluetooth),
        ])

        if state is None:
            self._set_notice("BLUETOOTH UNAVAILABLE", c.bt_error)
        elif not powered:
            self._set_notice("BLUETOOTH IS OFF",
                             "Press SWITCH ON, then SCAN to find devices.")
        elif not c.devices:
            self._set_notice(
                "SEARCHING" if busy.startswith("SEARCHING") else "NO DEVICES",
                "Put the device in pairing mode — usually by holding its "
                "power or Bluetooth button until a light flashes — then "
                "press SCAN.")
        else:
            self._build_device_rows(ctx)
        self._build_device_detail(ctx)

    def _build_device_rows(self, ctx: RenderContext) -> None:
        x, y, w, _h = self.list_rect
        devices = self.comms.devices
        row_h, gap, fit, paged = self._rows_area(ctx, len(devices))
        start = min(self.scroll[1], max(len(devices) - fit, 0))
        self.scroll[1] = start
        for i, device in enumerate(devices[start:start + fit]):
            ry = y + i * (row_h + gap)
            if device.address == self.selected_address:
                colour = "accent4"
            elif device.connected:
                colour = "accent2"
            else:
                colour = ROW_COLOURS[(start + i) % len(ROW_COLOURS)]
            self.g.add(HalfPillButton(
                f"dev_{i}", (x, ry, w, row_h), side="left",
                label=device.name, label_align="left", uppercase=False,
                color=colour,
                on_click=(lambda a=device.address: self.select_device(a)),
            ))
            parts = []
            if device.connected:
                parts.append("CONNECTED")
            elif device.paired:
                parts.append("PAIRED")
            kind = _short_kind(device.kind)
            if kind:
                parts.append(kind)
            self._rows.append(((x, ry, w, row_h), "  ·  ".join(parts), None))
        if paged:
            self._pager(ctx, len(devices), fit, y + fit * (row_h + gap), row_h)

    def _build_device_detail(self, ctx: RenderContext) -> None:
        u = ctx.u
        c = self.comms
        x, y, w, _h = self.detail_rect
        device = self.selected_device()
        busy = c.busy.get("bluetooth", "")
        if device is None:
            self.selected_address = None
            self.g.add(TextButton("sel_head", (x, y, w, u(1.3)),
                                  label="NO DEVICE SELECTED",
                                  color="accent5", label_color="accent5"))
            self._info_y = y + u(1.9)
            self._note_y = y + u(1.9) + u(1.05) * 4 + u(0.8)
            return
        self.g.add(TextButton("sel_head", (x, y, w, u(1.3)), label=device.name,
                              uppercase=False, color="accent4",
                              label_color="accent4"))
        self._info_y = y + u(1.9)
        cursor = y + u(1.9) + u(1.05) * 4 + u(0.6)
        powered = bool(getattr(c.bluetooth, "powered", False))
        buttons = []
        if device.connected:
            buttons.append(("disconnect", "DISCONNECT", "accent6", not busy,
                            lambda d=device: c.disconnect_device(d.address, d.name)))
        else:
            buttons.append(("connect", "CONNECT" if device.paired else "PAIR + CONNECT",
                            "accent2", powered and not busy,
                            lambda d=device: c.connect_device(d.address, d.name)))
        if device.paired:
            buttons.append(("forget", "FORGET", "accent6", not busy,
                            lambda d=device: c.forget_device(d.address, d.name)))
        self._note_y = self._action_row(ctx, cursor, buttons)

    # ------------------------------------------------------------------
    # Overlay: the text that changes without a rebuild
    # ------------------------------------------------------------------
    def draw_overlay(self, cr, ctx: RenderContext) -> None:
        u = ctx.u
        background = ctx.palette.background
        for (x, y, w, h), text, signal in self._rows:
            right = x + w - u(0.8)
            if signal is not None:
                right = self._draw_bars(cr, ctx, right, y, h, signal)
            if text:
                draw_text(cr, ctx, text, right, y + h / 2, h * 0.36, background,
                          align="right", valign="middle", max_width=w * 0.45)

        if self.notice is not None:
            (x, y, w, h), title, detail = self.notice
            draw_text(cr, ctx, title, x + w / 2, y + h * 0.32, u(1.2),
                      ctx.color("accent5"), align="center", valign="middle",
                      max_width=w * 0.95)
            if detail:
                draw_text(cr, ctx, detail, x + w / 2, y + h * 0.32 + u(1.6),
                          u(0.66), ctx.ink("accent3"), align="center",
                          valign="top", max_width=w * 0.85, wrap=True,
                          line_height=1.35)

        if self.detail_rect is not None:
            if self.tab == 0:
                self._draw_network_info(cr, ctx)
            else:
                self._draw_device_info(cr, ctx)
            self._draw_note(cr, ctx)
        self.draw_clock(cr, ctx)

    def _draw_bars(self, cr, ctx: RenderContext, right: float, y: float,
                   h: float, signal: int) -> float:
        """Four bars of signal, right-aligned; returns where the text goes."""
        bar_w = h * 0.13
        gap = h * 0.07
        lit = max(1, math.ceil(signal / 25)) if signal > 0 else 0
        background = ctx.palette.background
        x = right - 4 * bar_w - 3 * gap
        for i in range(4):
            bh = h * (0.22 + 0.13 * i)
            cr.save()
            cr.set_source_rgba(background[0], background[1], background[2],
                               1.0 if i < lit else 0.30)
            cr.rectangle(x + i * (bar_w + gap), y + h * 0.80 - bh, bar_w, bh)
            cr.fill()
            cr.restore()
        return x - h * 0.45

    def _info_lines(self, cr, ctx: RenderContext, lines) -> None:
        u = ctx.u
        x, _y, w, _h = self.detail_rect
        y = self._info_y
        for key, value in lines:
            draw_text(cr, ctx, key, x + u(0.2), y, u(0.66), ctx.ink("accent3"),
                      align="left", valign="top")
            draw_text(cr, ctx, value, x + w * 0.38, y, u(0.66),
                      ctx.color("accent5"), align="left", valign="top",
                      max_width=w * 0.62)
            y += u(1.05)

    def _draw_network_info(self, cr, ctx: RenderContext) -> None:
        u = ctx.u
        point = self.selected_network()
        if point is None:
            x, _y, w, _h = self.detail_rect
            draw_text(cr, ctx,
                      "Choose a network on the left. Secured networks you "
                      "have not joined before will ask for their password.",
                      x + u(0.2), self._info_y, u(0.66), ctx.ink("accent3"),
                      max_width=w - u(0.4), wrap=True, line_height=1.35)
            return
        status = ("CONNECTED" if point.in_use
                  else "SAVED" if point.saved else "NEW")
        self._info_lines(cr, ctx, [
            ("SIGNAL", f"{point.signal}%"),
            ("SECURITY", point.security or "OPEN — NO PASSWORD"),
            ("STATUS", status),
        ])
        if self.field_rect is not None:
            x, y, w, h = self.field_rect
            shown = self.password if self.reveal else "•" * len(self.password)
            colour = ctx.palette.background
            if not self.password:
                draw_text(cr, ctx, "PASSWORD", x + h * 0.4, y + h / 2, h * 0.42,
                          colour, align="left", valign="middle")
            else:
                draw_text(cr, ctx, shown, x + h * 0.4, y + h / 2, h * 0.42,
                          colour, align="left", valign="middle",
                          max_width=w - h * 0.8, letter_spacing=0.02)
            if self.typing and int(time.monotonic() * 2) % 2 == 0:
                # The caret: where a typed key will go.
                tw, _ = text_size(cr, ctx, shown, h * 0.42, letter_spacing=0.02) \
                    if self.password else (0, 0)
                cx = min(x + h * 0.4 + tw + u(0.15), x + w - h * 0.3)
                cr.save()
                cr.set_source_rgb(*colour)
                cr.rectangle(cx, y + h * 0.22, max(u(0.12), 2.0), h * 0.56)
                cr.fill()
                cr.restore()

    def _draw_device_info(self, cr, ctx: RenderContext) -> None:
        u = ctx.u
        device = self.selected_device()
        if device is None:
            x, _y, w, _h = self.detail_rect
            draw_text(cr, ctx,
                      "Choose a device on the left. A new device must be in "
                      "pairing mode; this computer pairs, trusts and "
                      "connects it in one press.",
                      x + u(0.2), self._info_y, u(0.66), ctx.ink("accent3"),
                      max_width=w - u(0.4), wrap=True, line_height=1.35)
            return
        state = ("CONNECTED" if device.connected
                 else "PAIRED" if device.paired else "NOT PAIRED")
        signal = f"{device.signal} dBm" if device.signal is not None else "—"
        self._info_lines(cr, ctx, [
            ("ADDRESS", device.address),
            ("TYPE", _short_kind(device.kind) or "UNKNOWN"),
            ("STATE", state),
            ("SIGNAL", signal),
        ])

    def _draw_note(self, cr, ctx: RenderContext) -> None:
        u = ctx.u
        x, _y, w, h = self.detail_rect
        area = "wifi" if self.tab == 0 else "bluetooth"
        busy = self.comms.busy.get(area, "")
        note = self.comms.note.get(area, "")
        y = getattr(self, "_note_y", self._info_y + u(5))
        if busy:
            dots = "." * (int(time.monotonic() * 3) % 4)
            draw_text(cr, ctx, f"{busy}{dots}", x + u(0.2), y, u(0.8),
                      ctx.color("accent4"), align="left", valign="top",
                      max_width=w - u(0.4))
        elif note:
            failed = note.startswith("UNABLE")
            draw_text(cr, ctx, note, x + u(0.2), y, u(0.7),
                      ctx.color("alert" if failed else "accent4"),
                      align="left", valign="top", max_width=w - u(0.4),
                      wrap=True, line_height=1.3)

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def on_release(self, x: float, y: float, pressed) -> None:
        strip = self.g.get("tabs")
        if pressed is strip and strip is not None:
            strip.pressed = False
            self.app.play("beep")
            strip.click_at(x, y)   # type: ignore[attr-defined]
            return
        super().on_release(x, y, pressed)

    def on_scroll(self, x: float, y: float, dy: float) -> bool:
        if self.list_rect is None:
            return False
        lx, ly, lw, lh = self.list_rect
        if not (lx <= x <= lx + lw and ly <= y <= ly + lh):
            return False
        total = len(self.comms.networks if self.tab == 0 else self.comms.devices)
        step = 1 if dy > 0 else -1
        start = self.scroll[self.tab] + step
        self.scroll[self.tab] = max(0, min(start, max(total - self._rows_fit, 0)))
        self.invalidate()
        return True

    def on_key(self, keyval: int, name: str, ctrl: bool = False) -> bool:
        # The shell's own keys always get through: a screen that swallows the
        # way out of itself is a trap.
        if name.startswith("F") and name[1:].isdigit():
            return False
        if ctrl:
            return False

        if self.typing and self.tab == 0:
            if name in ("Return", "KP_Enter"):
                self.connect()
                return True
            if name == "BackSpace":
                self.backspace()
                return True
            if name == "Escape":
                self.typing = False
                self.keyboard = False
                self.invalidate()
                return True
            ch = self._printable(keyval)
            if ch:
                self.password += ch
                self.invalidate()
                return True

        if name == "Tab":
            self.switch_tab(1 - self.tab)
            return True
        if name in ("Up", "Down"):
            self._move_selection(-1 if name == "Up" else 1)
            return True
        if name in ("Return", "KP_Enter"):
            if self.tab == 0:
                point = self.selected_network()
                if point is not None and not point.in_use:
                    self.connect()
            else:
                device = self.selected_device()
                if device is not None and not device.connected:
                    self.comms.connect_device(device.address, device.name)
            return True
        return False

    @staticmethod
    def _printable(keyval: int) -> str:
        """The character a key types, for a password field.

        GDK keyvals coincide with Latin-1 for printable characters, and carry
        any other Unicode character as ``0x01000000 + codepoint`` — so
        accented and non-Latin passwords can be typed, not only ASCII.
        """
        if 0x20 <= keyval <= 0x7E or 0xA0 <= keyval <= 0xFF:
            return chr(keyval)
        if keyval & 0xFF000000 == 0x01000000:
            code = keyval & 0x00FFFFFF
            if code >= 0x20:
                return chr(code)
        return ""

    def _move_selection(self, step: int) -> None:
        if self.tab == 0:
            items = [p.ssid for p in self.comms.networks]
            current = self.selected_ssid
        else:
            items = [d.address for d in self.comms.devices]
            current = self.selected_address
        if not items:
            return
        index = items.index(current) + step if current in items else 0
        index = max(0, min(index, len(items) - 1))
        if self.tab == 0:
            self.select_network(items[index])
        else:
            self.select_device(items[index])
        start = self.scroll[self.tab]
        if index < start:
            self.scroll[self.tab] = index
        elif index >= start + self._rows_fit:
            self.scroll[self.tab] = index - self._rows_fit + 1
