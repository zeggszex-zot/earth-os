"""Wireless networks and Bluetooth on Linux.

Below the boundary, so this is where ``nmcli`` and ``bluetoothctl`` are
named. Nothing above it knows either exists: the communications screen asks
for ``network.scan`` and ``bluetooth.connect`` and is told an answer or an
honest reason there is not one.

Two rules from ``AGENTS.md`` shape everything here:

* **Failure is data.** Every path that cannot do what it was asked raises
  :class:`PlatformError` with a sentence a person can act on. The old
  ``connect`` swallowed ``nmcli``'s reason and returned ``False``, so a wrong
  password and a network out of range looked identical.
* **The adapter contains its own noise.** Every subprocess captures stderr.

Bluetooth pairing is the one part that cannot be a single command. A pair
request can stop and ask for a PIN or a yes/no, and only a live
``bluetoothctl`` session can answer it, so :class:`_BluetoothSession` drives
one. It registers a *NoInputNoOutput* agent, which is what makes pairing
work without a dialog: when either side declares no input and no output,
Secure Simple Pairing uses "just works", the same as pairing a headset from a
phone. A pre-2.1 device that insists on a PIN is offered ``0000``, the PIN
nearly all of them ship with.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from .api import (
    AccessPoint, Bluetooth, BluetoothDevice, BluetoothState, PlatformError,
    WirelessState,
)

# --------------------------------------------------------------------------
# NetworkManager
# --------------------------------------------------------------------------

NMCLI = "nmcli"


def split_terse(line: str) -> List[str]:
    r"""Split one line of ``nmcli -t`` output into fields.

    Terse mode separates fields with ``:`` and escapes a literal colon as
    ``\:`` and a backslash as ``\\``. A network called ``Café:5G`` would
    otherwise come back as two fields, and a BSSID as six.
    """
    fields: List[str] = []
    current: List[str] = []
    escaped = False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def nmcli(args: List[str], timeout: float = 15.0,
          runner: Callable = subprocess.run) -> str:
    """Run ``nmcli``, return stdout, or raise with its own explanation."""
    if shutil.which(NMCLI) is None:
        raise PlatformError(
            "wireless control needs NetworkManager (nmcli), which is not "
            "installed")
    try:
        completed = runner([NMCLI, *args], capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PlatformError(f"NetworkManager did not answer within "
                            f"{timeout:.0f} seconds")
    except OSError as exc:
        raise PlatformError(f"could not run nmcli: {exc}")
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        message = message.removeprefix("Error:").strip()
        if "NMClient" in message or "NetworkManager is not running" in message:
            message = ("wireless control is not available: "
                       "NetworkManager is not running")
        raise PlatformError(message or f"nmcli exited {completed.returncode}")
    return completed.stdout or ""


def _clean_nm_error(ssid: str, message: str) -> str:
    """Turn NetworkManager's reason into something a person can act on."""
    lowered = message.lower()
    if "secrets were required" in lowered or "psk" in lowered \
            or "password" in lowered or "802-1x" in lowered:
        return f"wrong or missing password for {ssid}"
    if "no network with ssid" in lowered:
        return f"{ssid} is not in range"
    if "not running" in lowered or "not available" in lowered:
        return message
    return message


class LinuxWireless:
    """The wireless half of :class:`LinuxNetwork`, through NetworkManager."""

    def __init__(self, address_of: Callable[[str], str],
                 run: Callable[..., str] = nmcli):
        self._address_of = address_of
        self._run = run

    # ------------------------------------------------------------------
    def wifi_devices(self) -> List[Tuple[str, str, str]]:
        """(device, state, connection) for every wireless interface."""
        out = self._run(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
        devices = []
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) >= 4 and fields[1] == "wifi":
                devices.append((fields[0], fields[2], fields[3]))
        return devices

    def saved(self) -> Set[str]:
        """Names of the stored wireless profiles."""
        out = self._run(["-t", "-f", "NAME,TYPE", "connection", "show"])
        names = set()
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) >= 2 and fields[1] in ("802-11-wireless", "wifi"):
                names.add(fields[0])
        return names

    def state(self) -> WirelessState:
        devices = self.wifi_devices()
        radio = self._run(["radio", "wifi"]).strip().lower()
        enabled = radio.startswith("enabled")
        state = WirelessState(hardware=bool(devices), enabled=enabled)
        for device, dev_state, connection in devices:
            if dev_state.startswith("connected"):
                state.device = device
                state.ssid = self._active_ssid(device) or connection
                state.address = self._address_of(device)
                break
        if not state.device and devices:
            state.device = devices[0][0]
        return state

    def _active_ssid(self, device: str) -> str:
        """The broadcast name, which a profile's name need not match."""
        try:
            out = self._run(["-t", "-f", "ACTIVE,SSID", "device", "wifi", "list",
                             "ifname", device, "--rescan", "no"])
        except PlatformError:
            return ""
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) >= 2 and fields[0] == "yes":
                return fields[1]
        return ""

    def scan(self, rescan: bool = True) -> List[AccessPoint]:
        args = ["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi",
                "list", "--rescan"]
        try:
            out = self._run(args + ["yes" if rescan else "no"], timeout=30)
        except PlatformError:
            if not rescan:
                raise
            # NetworkManager refuses a rescan straight after another one, and
            # while the radio is settling. The cached list is still true.
            out = self._run(args + ["no"])
        try:
            saved = self.saved()
        except PlatformError:
            saved = set()

        best: Dict[str, AccessPoint] = {}
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) < 4:
                continue
            in_use, ssid, signal, security = fields[:4]
            if not ssid or ssid == "--":
                continue                       # hidden network
            try:
                strength = max(0, min(100, int(signal)))
            except ValueError:
                strength = 0
            security = "" if security in ("", "--") else security.strip()
            point = best.get(ssid)
            if point is None:
                best[ssid] = AccessPoint(ssid=ssid, signal=strength,
                                         security=security,
                                         in_use=in_use.strip() == "*",
                                         saved=ssid in saved)
            else:
                point.in_use = point.in_use or in_use.strip() == "*"
                if strength > point.signal:
                    point.signal = strength
                    point.security = security or point.security
        return sorted(best.values(),
                      key=lambda p: (not p.in_use, -p.signal, p.ssid.lower()))

    def set_enabled(self, on: bool) -> bool:
        self._run(["radio", "wifi", "on" if on else "off"])
        return True

    def forget(self, ssid: str) -> bool:
        if not ssid:
            raise PlatformError("which network?")
        if ssid not in self.saved():
            raise PlatformError(f"no saved network called {ssid}")
        self._run(["connection", "delete", "id", ssid])
        return True

    def connect(self, ssid: str, password: str = "") -> bool:
        if not ssid:
            raise PlatformError("which network?")
        try:
            saved_before = ssid in self.saved()
        except PlatformError:
            saved_before = False
        # A new password for a stored network replaces the stored one. Left
        # in place, NetworkManager keeps trying the old secret.
        if password and saved_before:
            self._run(["connection", "delete", "id", ssid])
        args = ["-w", "45", "device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        try:
            self._run(args, timeout=60)
        except PlatformError as exc:
            # A failed first attempt leaves a profile holding the wrong
            # secret, which the next attempt would silently reuse.
            if not saved_before:
                try:
                    if ssid in self.saved():
                        self._run(["connection", "delete", "id", ssid])
                except PlatformError:
                    pass
            raise PlatformError(_clean_nm_error(ssid, str(exc)))
        return True

    def disconnect(self, name: str = "") -> bool:
        if name and name != "all":
            self._run(["device", "disconnect", name])
            return True
        joined = [d for d, state, _ in self.wifi_devices()
                  if state.startswith("connected")]
        if not joined:
            raise PlatformError("not joined to a wireless network")
        for device in joined:
            self._run(["device", "disconnect", device])
        return True


# --------------------------------------------------------------------------
# BlueZ
# --------------------------------------------------------------------------

BLUETOOTHCTL = "bluetoothctl"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x01\x02\r]")
_ADDRESS = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def parse_info(address: str, text: str) -> Optional[BluetoothDevice]:
    """``bluetoothctl info`` output to a device, or None if it is unknown."""
    text = strip_ansi(text)
    if "not available" in text or f"Device {address}" not in text:
        return None
    values: Dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition(": ")
        if sep and key not in values:
            values[key] = value.strip()
    name = values.get("Alias") or values.get("Name") or address
    signal = None
    rssi = values.get("RSSI", "")
    match = re.search(r"-?\d+", rssi.split("(")[-1] if "(" in rssi else rssi)
    if match:
        signal = int(match.group(0))
    return BluetoothDevice(
        address=address, name=name,
        paired=values.get("Paired") == "yes" or values.get("Bonded") == "yes",
        connected=values.get("Connected") == "yes",
        trusted=values.get("Trusted") == "yes",
        kind=values.get("Icon", ""),
        signal=signal,
    )


def _clean_bt_error(code: str) -> str:
    """BlueZ's D-Bus error names, as reasons."""
    table = {
        "AuthenticationFailed": "pairing was refused — put the device in "
                                "pairing mode and try again",
        "AuthenticationCanceled": "pairing was cancelled on the device",
        "AuthenticationRejected": "the device rejected pairing",
        "AuthenticationTimeout": "the device did not answer in time",
        "ConnectionAttemptFailed": "the device did not answer — is it on "
                                   "and in range?",
        "Blocked": "bluetooth is blocked by the radio kill switch",
        "NotReady": "the bluetooth adapter is not ready — switch it on first",
        "InProgress": "already busy with this device — wait and try again",
        "AlreadyConnected": "already connected",
        "br-connection-profile-unavailable":
            "no profile on this computer can use it — for audio, install "
            "the PipeWire or PulseAudio bluetooth module",
        "br-connection-page-timeout": "the device did not answer — is it "
                                      "on and in range?",
        "le-connection-abort-by-local": "the connection was dropped — try "
                                        "again with the device in pairing mode",
    }
    for key, reason in table.items():
        if key in code:
            return reason
    # "org.bluez.Error.Failed br-connection-unknown": the name is generic and
    # the detail after it is the actual reason.
    words = code.replace("org.bluez.Error.", "").split()
    if len(words) > 1 and words[0] == "Failed":
        words = words[1:]
    return " ".join(words).replace("-", " ") or "failed"


class _BluetoothSession:
    """A live ``bluetoothctl``, for the operations that can stop and ask.

    Output is read as a byte stream rather than by line, because the agent's
    questions — ``Enter PIN code:`` — end without a newline and a line reader
    would sit waiting for one that never comes.
    """

    def __init__(self, argv: Optional[List[str]] = None):
        try:
            self.proc = subprocess.Popen(
                argv or [BLUETOOTHCTL], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise PlatformError(f"could not start bluetoothctl: {exc}")
        self.text = ""
        self.mark = 0
        self._cond = threading.Condition()
        self._reader = threading.Thread(target=self._read, daemon=True,
                                        name="cdos-bluetoothctl")
        self._reader.start()

    def _read(self) -> None:
        fd = self.proc.stdout.fileno()        # type: ignore[union-attr]
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            with self._cond:
                if not chunk:
                    self.text += "\n<eof>"
                    self._cond.notify_all()
                    return
                self.text += strip_ansi(chunk.decode("utf-8", "replace"))
                self._cond.notify_all()

    def send(self, line: str) -> None:
        try:
            self.proc.stdin.write((line + "\n").encode())   # type: ignore[union-attr]
            self.proc.stdin.flush()                         # type: ignore[union-attr]
        except (OSError, ValueError):
            raise PlatformError("bluetoothctl exited unexpectedly")

    def expect(self, patterns: Dict[str, str],
               timeout: float) -> Tuple[str, Optional[re.Match]]:
        """Wait for the first of *patterns* after the last match.

        Returns ``(key, match)``, or ``("timeout", None)``, or
        ``("eof", None)`` if bluetoothctl went away.
        """
        compiled = {k: re.compile(v) for k, v in patterns.items()}
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                window = self.text[self.mark:]
                found = None
                for key, regex in compiled.items():
                    match = regex.search(window)
                    if match and (found is None
                                  or match.start() < found[1].start()):
                        found = (key, match)
                if found:
                    self.mark += found[1].end()
                    return found
                if "<eof>" in window:
                    return ("eof", None)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ("timeout", None)
                self._cond.wait(remaining)

    def close(self) -> None:
        try:
            self.send("quit")
        except PlatformError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


#: What a pairing agent may be asked, and what this one answers.
_AGENT_ANSWERS = {
    "confirm": (r"Confirm passkey \d+ \(yes/no\)|Request confirmation", "yes"),
    "authorize": (r"Authorize service|Accept pairing \(yes/no\)", "yes"),
    "pin": (r"Enter PIN code|Request PIN code", "0000"),
    "passkey": (r"Enter passkey", "0"),
}


class LinuxBluetooth(Bluetooth):
    """BlueZ, through ``bluetoothctl``."""

    #: Where the kernel lists adapters. Absent or empty: there is no radio,
    #: and asking bluetoothd would only wait for a controller that will
    #: never appear.
    SYSFS = Path("/sys/class/bluetooth")
    LIMIT = 64

    def __init__(self, session_factory: Callable[[], _BluetoothSession] = _BluetoothSession,
                 runner: Callable = subprocess.run):
        self._session = session_factory
        self._runner = runner

    def available(self) -> bool:
        return shutil.which(BLUETOOTHCTL) is not None

    # ------------------------------------------------------------------
    def _ctl(self, args: List[str], timeout: float = 8.0) -> str:
        if shutil.which(BLUETOOTHCTL) is None:
            raise PlatformError("bluetooth control needs BlueZ (bluetoothctl), "
                                "which is not installed")
        try:
            completed = self._runner([BLUETOOTHCTL, *args], capture_output=True,
                                     text=True, timeout=timeout,
                                     stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise PlatformError("the bluetooth service is not available — "
                                "is bluetoothd running?")
        except OSError as exc:
            raise PlatformError(f"could not run bluetoothctl: {exc}")
        out = strip_ansi((completed.stdout or "") + (completed.stderr or ""))
        if "No default controller available" in out:
            raise PlatformError("this machine has no bluetooth adapter")
        if "Waiting to connect to bluetoothd" in out:
            raise PlatformError("the bluetooth service is not available — "
                                "bluetoothd is not running")
        return out

    def _require_adapter(self) -> None:
        if self.SYSFS.is_dir() and not any(self.SYSFS.iterdir()):
            raise PlatformError("this machine has no bluetooth adapter")
        if not self.SYSFS.exists():
            raise PlatformError("this machine has no bluetooth adapter")

    @staticmethod
    def _check_address(address: str) -> str:
        address = (address or "").strip().upper()
        if not _ADDRESS.match(address):
            raise PlatformError(f"not a bluetooth address: {address!r}")
        return address

    # ------------------------------------------------------------------
    def state(self) -> BluetoothState:
        self._require_adapter()
        out = self._ctl(["show"])
        state = BluetoothState(present=False, powered=False)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Controller "):
                state.present = True
                state.address = line.split()[1]
            key, sep, value = line.partition(": ")
            if not sep:
                continue
            if key == "Alias" or (key == "Name" and not state.name):
                state.name = value.strip()
            elif key == "Powered":
                state.powered = value.strip() == "yes"
            elif key == "Discovering":
                state.discovering = value.strip() == "yes"
        if not state.present:
            raise PlatformError("this machine has no bluetooth adapter")
        return state

    def set_power(self, on: bool) -> bool:
        self._require_adapter()
        if on and shutil.which("rfkill"):
            # A soft block is the usual reason "power on" fails. Unblocking
            # needs no root on a desktop session; when it does, the power
            # command below reports the block in words.
            try:
                self._runner(["rfkill", "unblock", "bluetooth"],
                             capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        out = self._ctl(["power", "on" if on else "off"])
        if "succeeded" in out:
            return True
        failure = re.search(r"Failed to set power \w+: ([^\n]+)", out)
        raise PlatformError(_clean_bt_error(failure.group(1)) if failure
                            else (out.strip().splitlines() or ["failed"])[-1])

    def info(self, address: str) -> Optional[BluetoothDevice]:
        return parse_info(address, self._ctl(["info", address]))

    def devices(self) -> List[BluetoothDevice]:
        self._require_adapter()
        out = self._ctl(["devices"])
        found: List[BluetoothDevice] = []
        for line in out.splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) < 2 or parts[0] != "Device" \
                    or not _ADDRESS.match(parts[1]):
                continue
            if len(found) >= self.LIMIT:
                break
            device = self.info(parts[1])
            if device is None:
                device = BluetoothDevice(address=parts[1],
                                         name=parts[2] if len(parts) > 2 else parts[1])
            found.append(device)
        return found

    def scan(self, seconds: float = 8.0) -> List[BluetoothDevice]:
        seconds = max(2.0, min(float(seconds), 30.0))
        state = self.state()
        if not state.powered:
            raise PlatformError("bluetooth is switched off")
        session = self._session()
        try:
            session.send("scan on")
            session.expect({"never": r"(?!x)x"}, seconds)
            session.send("scan off")
            session.expect({"stopped": r"Discovery stopped|Discovering: no"}, 3)
        finally:
            session.close()
        return self.devices()

    def connect(self, address: str) -> bool:
        address = self._check_address(address)
        state = self.state()
        if not state.powered:
            raise PlatformError("bluetooth is switched off")
        known = self.info(address)
        session = self._session()
        try:
            session.send("agent off")
            session.expect({"ok": r"Agent unregistered|No agent is registered"}, 3)
            session.send("agent NoInputNoOutput")
            session.expect({"ok": r"Agent registered|already registered"}, 3)
            session.send("default-agent")
            session.expect({"ok": r"Default agent request successful|"
                                   r"No agent is registered"}, 3)

            scanning = False
            if known is None:
                # BlueZ forgets an unpaired device within about thirty seconds
                # of discovery stopping, so a device picked from a list a
                # minute ago may need finding again before it can be paired.
                session.send("scan on")
                scanning = True
                key, _ = session.expect({"seen": rf"Device {re.escape(address)}"}, 15)
                if key != "seen":
                    session.send("scan off")
                    raise PlatformError(f"{address} is not in range — put it "
                                        f"in pairing mode and scan again")

            if known is None or not known.paired:
                self._pair(session, address)
            session.send(f"trust {address}")
            session.expect({"ok": r"trust succeeded", "no": r"Failed to set trusted"}, 5)
            if scanning:
                session.send("scan off")

            if known is not None and known.connected:
                return True
            session.send(f"connect {address}")
            key, match = session.expect({
                "ok": r"Connection successful",
                "fail": r"Failed to connect: ([^\n]+)\n",
                "gone": r"not available",
            }, 30)
            if key == "ok":
                return True
            if key == "fail" and match is not None:
                reason = _clean_bt_error(match.group(1))
                if reason == "already connected":
                    return True
                raise PlatformError(reason)
            if key == "gone":
                raise PlatformError(f"{address} is not in range")
            raise PlatformError("the device did not answer — is it on and in range?")
        finally:
            session.close()

    def _pair(self, session: _BluetoothSession, address: str) -> None:
        session.send(f"pair {address}")
        patterns = {key: pattern for key, (pattern, _) in _AGENT_ANSWERS.items()}
        patterns.update({
            "ok": r"Pairing successful|AlreadyExists",
            "fail": r"Failed to pair: ([^\n]+)\n",
            "gone": r"Device \S+ not available",
        })
        deadline = time.monotonic() + 40
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PlatformError("pairing timed out — put the device in "
                                    "pairing mode and try again")
            key, match = session.expect(patterns, remaining)
            if key == "ok":
                return
            if key in _AGENT_ANSWERS:
                session.send(_AGENT_ANSWERS[key][1])
                continue
            if key == "fail" and match is not None:
                raise PlatformError(_clean_bt_error(match.group(1)))
            if key == "gone":
                raise PlatformError(f"{address} is not in range")
            if key == "eof":
                raise PlatformError("bluetoothctl exited during pairing")
            raise PlatformError("pairing timed out — put the device in "
                                "pairing mode and try again")

    def disconnect(self, address: str = "") -> bool:
        if address:
            address = self._check_address(address)
            targets = [address]
        else:
            targets = [d.address for d in self.devices() if d.connected]
            if not targets:
                raise PlatformError("no bluetooth device is connected")
        for target in targets:
            out = self._ctl(["disconnect", target], timeout=15)
            failure = re.search(r"Failed to disconnect: ([^\n]+)", out)
            if failure:
                raise PlatformError(_clean_bt_error(failure.group(1)))
            if "not available" in out:
                raise PlatformError(f"{target} is not known to this computer")
        return True

    def forget(self, address: str) -> bool:
        address = self._check_address(address)
        out = self._ctl(["remove", address], timeout=10)
        if "has been removed" in out:
            return True
        if "not available" in out:
            raise PlatformError(f"{address} is not known to this computer")
        failure = re.search(r"Failed to remove device: ([^\n]+)", out)
        raise PlatformError(_clean_bt_error(failure.group(1)) if failure
                            else (out.strip().splitlines() or ["failed"])[-1])
