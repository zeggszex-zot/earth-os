"""Wireless networks and Bluetooth, as the communications screen sees them.

Holds what was last read and what is in progress, and turns presses into
capability requests. Every request goes through
:meth:`Shell.run_capability_in_background`, which is the gated path: an
effectful one is offered on the confirmation strip and runs only on CONFIRM.
Nothing here names a mechanism and nothing here invokes the registry.

Kept apart from the screen because none of it is drawing, and apart from
``core.py`` because none of it is the shell's own business — the same state
would serve a voice-only CDOS that has no screen at all.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional


class Comms:
    #: How often the screen refreshes the readings while it is up. Reading is
    #: cheap; rescanning the air is not, and is only done on request.
    REFRESH_SECONDS = 12.0

    def __init__(self, shell):
        self.shell = shell

        self.wireless = None                  # WirelessState, once read
        self.networks: List[object] = []      # AccessPoint
        self.wifi_error = ""

        self.bluetooth = None                 # BluetoothState, once read
        self.devices: List[object] = []       # BluetoothDevice
        self.bt_error = ""

        #: What is in progress, by area ("wifi" / "bluetooth"), as the words
        #: to show while it runs.
        self.busy: Dict[str, str] = {}
        #: The last thing that happened, by area, for the screen to show.
        self.note: Dict[str, str] = {}
        self._next_refresh = 0.0

    # ------------------------------------------------------------------
    def _run(self, area: str, capability: str, busy: str,
             then: Optional[Callable] = None, **kwargs) -> bool:
        """Ask for *capability*; mark *area* busy while it runs."""
        def started() -> None:
            self.busy[area] = busy

        def done(result) -> None:
            self.busy.pop(area, None)
            if then is not None:
                then(result)

        return self.shell.run_capability_in_background(
            capability, on_done=done, on_start=started, **kwargs)

    def is_busy(self, area: str) -> bool:
        return area in self.busy

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def refresh(self, rescan: bool = False) -> None:
        self._next_refresh = time.monotonic() + self.REFRESH_SECONDS
        self.refresh_wifi(rescan=rescan)
        self.refresh_bluetooth()

    def refresh_due(self) -> bool:
        return (time.monotonic() >= self._next_refresh
                and not self.busy)

    def refresh_wifi(self, rescan: bool = False) -> None:
        if self.is_busy("wifi"):
            return

        def got_state(result) -> None:
            if not result.ok:
                self.wireless = None
                self.networks = []
                self.wifi_error = result.error
                return
            self.wireless = result.value
            self.wifi_error = ""
            if not getattr(result.value, "enabled", False):
                self.networks = []
                return
            self._run("wifi", "network.scan",
                      "SCANNING" if rescan else "READING",
                      then=got_networks, rescan=rescan)

        def got_networks(result) -> None:
            if result.ok:
                self.networks = list(result.value or [])
            else:
                self.wifi_error = result.error

        self._run("wifi", "network.wireless", "READING", then=got_state)

    def refresh_bluetooth(self) -> None:
        if self.is_busy("bluetooth"):
            return

        def got_state(result) -> None:
            if not result.ok:
                self.bluetooth = None
                self.devices = []
                self.bt_error = result.error
                return
            self.bluetooth = result.value
            self.bt_error = ""
            self._run("bluetooth", "bluetooth.devices", "READING",
                      then=got_devices)

        def got_devices(result) -> None:
            if result.ok:
                self.devices = self.ordered(result.value or [])
            else:
                self.bt_error = result.error

        self._run("bluetooth", "bluetooth.state", "READING", then=got_state)

    @staticmethod
    def ordered(devices: List[object]) -> List[object]:
        """Connected, then paired, then named by signal, then nameless.

        A crowded room returns dozens of devices whose only name is their
        address. They go last; the one you are trying to pair has a name.
        """
        def key(device):
            named = getattr(device, "name", "") != getattr(device, "address", "")
            signal = getattr(device, "signal", None)
            return (not getattr(device, "connected", False),
                    not getattr(device, "paired", False),
                    not named,
                    -(signal if signal is not None else -999),
                    str(getattr(device, "name", "")).lower())
        return sorted(devices, key=key)

    # ------------------------------------------------------------------
    # Wireless acts
    # ------------------------------------------------------------------
    def scan_wifi(self) -> None:
        self.note["wifi"] = ""
        self.refresh_wifi(rescan=True)

    def wifi_power(self, on: bool) -> None:
        def then(result) -> None:
            self._report("wifi", result,
                         "WIRELESS ON" if on else "WIRELESS OFF")
            if result.ok:
                # The radio takes a moment to see anything after power-on.
                self.refresh_wifi(rescan=on)
        self._run("wifi", "network.wifi_on" if on else "network.wifi_off",
                  "SWITCHING ON" if on else "SWITCHING OFF", then=then)

    def connect_wifi(self, ssid: str, password: str = "") -> bool:
        def then(result) -> None:
            self._report("wifi", result, f"JOINED {ssid}")
            self.refresh_wifi()
        kwargs = {"ssid": ssid}
        if password:
            kwargs["password"] = password
        return self._run("wifi", "network.connect", f"JOINING {ssid}",
                         then=then, **kwargs)

    def disconnect_wifi(self) -> None:
        def then(result) -> None:
            self._report("wifi", result, "DISCONNECTED")
            self.refresh_wifi()
        self._run("wifi", "network.disconnect", "DISCONNECTING", then=then)

    def forget_wifi(self, ssid: str) -> None:
        def then(result) -> None:
            self._report("wifi", result, f"FORGOT {ssid}")
            self.refresh_wifi()
        self._run("wifi", "network.forget", "FORGETTING", then=then, ssid=ssid)

    # ------------------------------------------------------------------
    # Bluetooth acts
    # ------------------------------------------------------------------
    def bt_power(self, on: bool) -> None:
        def then(result) -> None:
            self._report("bluetooth", result,
                         "BLUETOOTH ON" if on else "BLUETOOTH OFF")
            self.refresh_bluetooth()
        self._run("bluetooth", "bluetooth.on" if on else "bluetooth.off",
                  "SWITCHING ON" if on else "SWITCHING OFF", then=then)

    def scan_bluetooth(self, seconds: float = 10.0) -> None:
        def then(result) -> None:
            if result.ok:
                self.devices = self.ordered(result.value or [])
                found = len(self.devices)
                self.note["bluetooth"] = f"{found} DEVICES FOUND"
            else:
                self._report("bluetooth", result, "")
        self.note["bluetooth"] = ""
        self._run("bluetooth", "bluetooth.scan",
                  f"SEARCHING · {int(seconds)} SECONDS", then=then,
                  seconds=seconds)

    def connect_device(self, address: str, name: str = "") -> None:
        label = name or address

        def then(result) -> None:
            self._report("bluetooth", result, f"CONNECTED {label}")
            self.refresh_bluetooth()
        self._run("bluetooth", "bluetooth.connect", f"CONNECTING {label}",
                  then=then, address=address)

    def disconnect_device(self, address: str, name: str = "") -> None:
        label = name or address

        def then(result) -> None:
            self._report("bluetooth", result, f"DISCONNECTED {label}")
            self.refresh_bluetooth()
        self._run("bluetooth", "bluetooth.disconnect", "DISCONNECTING",
                  then=then, address=address)

    def forget_device(self, address: str, name: str = "") -> None:
        label = name or address

        def then(result) -> None:
            self._report("bluetooth", result, f"FORGOT {label}")
            self.refresh_bluetooth()
        self._run("bluetooth", "bluetooth.forget", "FORGETTING",
                  then=then, address=address)

    # ------------------------------------------------------------------
    def _report(self, area: str, result, success: str) -> None:
        if result.ok:
            self.note[area] = success
            if success:
                self.shell.toast(success, 4.0)
        else:
            self.note[area] = f"UNABLE TO COMPLY · {result.error}"
            self.shell.toast(f"UNABLE TO COMPLY · {result.error}", 6.0)
