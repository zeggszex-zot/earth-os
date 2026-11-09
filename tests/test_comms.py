"""Wireless, Bluetooth, the COMMUNICATIONS screen, and screenshots.

Three layers, tested at each:

* **The adapter** (``platform/linux_radio.py``). NetworkManager is replaced
  by a function that answers like ``nmcli -t`` does, escapes included. BlueZ
  is replaced by a fake ``bluetoothctl`` *program* on PATH, so the real
  session code runs against a real pipe — including the PIN prompt that ends
  without a newline, which is the case a line reader would hang on.
* **The shell** (``run_capability_in_background``, ``Comms``). The real
  catalogue and the real gate, with only the bindings swapped, so an
  effectful act is proven to wait for CONFIRM rather than assumed to.
* **The screen**, rendered with data at the smallest and largest sizes, and
  driven by key presses the way a person would.

Nothing here touches the machine's own radios.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earth_os.capability_corpus import CapabilityCorpus      # noqa: E402
from earth_os.config import Config                           # noqa: E402
from earth_os.core import Shell                              # noqa: E402
from earth_os.platform.api import (                          # noqa: E402
    AccessPoint, BluetoothDevice, BluetoothState, PlatformError, WirelessState,
)
from earth_os.platform.linux_radio import (                  # noqa: E402
    LinuxBluetooth, LinuxWireless, parse_info, split_terse,
)
from earth_os.screens import ALL_SCREENS                     # noqa: E402


def temp_config() -> Config:
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    return config


def shell_with_screens() -> Shell:
    shell = Shell(temp_config())
    for screen_class in ALL_SCREENS:
        shell.register_screen(screen_class(shell))
    return shell


# ==========================================================================
# NetworkManager
# ==========================================================================

class FakeNmcli:
    """Answers the way ``nmcli -t`` does, and records what it was asked."""

    def __init__(self):
        self.calls = []
        self.saved = {"Enterprise-D", "Holodeck 3"}
        self.radio = "enabled"
        self.connected = "Enterprise-D"
        self.fail_connect = ""
        self.fail_rescan = False

    def __call__(self, args, timeout=15.0):
        self.calls.append(list(args))
        if args[:5] == ["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"]:
            state = "connected" if self.connected else "disconnected"
            return (f"wlp2s0:wifi:{state}:{self.connected}\n"
                    "enp3s0:ethernet:unavailable:\nlo:loopback:connected (externally):lo\n")
        if args == ["radio", "wifi"]:
            return self.radio + "\n"
        if args[:4] == ["-t", "-f", "NAME,TYPE", "connection"]:
            return "".join(f"{n}:802-11-wireless\n" for n in sorted(self.saved)) \
                + "Wired connection 1:802-3-ethernet\n"
        if "ACTIVE,SSID" in args:
            return f"yes:{self.connected}\nno:Ten Forward\n"
        if "IN-USE,SSID,SIGNAL,SECURITY" in args:
            if self.fail_rescan and args[-1] == "yes":
                raise PlatformError("Scanning not allowed immediately following previous scan")
            return ("*:Enterprise-D:92:WPA2\n"
                    ":Enterprise-D:40:WPA2\n"          # a second access point
                    ":Ten Forward:71:WPA2 WPA3\n"
                    "::80:WPA2\n"                       # hidden network
                    ":Café\\:5G:55:WPA2\n"              # an escaped colon
                    ":Starbase Guest:44:\n"
                    ":Shuttlebay:23:--\n")
        if args[:2] == ["connection", "delete"]:
            self.saved.discard(args[-1])
            return "deleted\n"
        if "connect" in args and "wifi" in args:
            ssid = args[args.index("connect") + 1]
            if self.fail_connect:
                self.saved.add(ssid)       # NetworkManager leaves the profile
                raise PlatformError(self.fail_connect)
            self.saved.add(ssid)
            self.connected = ssid
            return "Device 'wlp2s0' successfully activated\n"
        if args[:2] == ["device", "disconnect"]:
            self.connected = ""
            return "disconnected\n"
        if args[:2] == ["radio", "wifi"]:
            self.radio = "enabled" if args[2] == "on" else "disabled"
            return ""
        raise AssertionError(f"unexpected nmcli call {args}")


class TestNmcliOutput(unittest.TestCase):

    def test_terse_escapes_are_honoured(self):
        self.assertEqual(split_terse(r"*:Café\:5G:55:WPA2"),
                         ["*", "Café:5G", "55", "WPA2"])
        self.assertEqual(split_terse(r"a\\b:c"), ["a\\b", "c"])
        self.assertEqual(split_terse("::80:"), ["", "", "80", ""])


class TestLinuxWireless(unittest.TestCase):

    def setUp(self):
        self.nm = FakeNmcli()
        self.wifi = LinuxWireless(lambda device: "192.168.1.47", run=self.nm)

    def test_state(self):
        state = self.wifi.state()
        self.assertEqual(state, WirelessState(
            hardware=True, enabled=True, ssid="Enterprise-D",
            device="wlp2s0", address="192.168.1.47"))

    def test_radio_off_is_reported_not_guessed(self):
        self.nm.radio = "disabled"
        self.nm.connected = ""
        state = self.wifi.state()
        self.assertTrue(state.hardware)
        self.assertFalse(state.enabled)
        self.assertEqual(state.ssid, "")

    def test_scan_is_one_entry_per_name_strongest_first(self):
        networks = self.wifi.scan()
        names = [p.ssid for p in networks]
        self.assertEqual(names[0], "Enterprise-D", "the joined network leads")
        self.assertEqual(names.count("Enterprise-D"), 1)
        self.assertNotIn("", names, "hidden networks have no name to show")
        self.assertIn("Café:5G", names)
        joined = networks[0]
        self.assertTrue(joined.in_use and joined.saved)
        self.assertEqual(joined.signal, 92)
        rest = [p.signal for p in networks[1:]]
        self.assertEqual(rest, sorted(rest, reverse=True))
        open_ones = {p.ssid: p.security for p in networks}
        self.assertEqual(open_ones["Starbase Guest"], "")
        self.assertEqual(open_ones["Shuttlebay"], "", "'--' means no security")

    def test_a_refused_rescan_falls_back_to_the_cached_list(self):
        self.nm.fail_rescan = True
        self.assertTrue(self.wifi.scan(rescan=True))

    def test_connect_with_a_new_password_replaces_the_stored_one(self):
        self.wifi.connect("Holodeck 3", "arch")
        deletes = [c for c in self.nm.calls if c[:2] == ["connection", "delete"]]
        self.assertEqual(deletes, [["connection", "delete", "id", "Holodeck 3"]])
        connect = [c for c in self.nm.calls if "connect" in c][-1]
        self.assertEqual(connect[-2:], ["password", "arch"])

    def test_a_wrong_password_says_so_and_leaves_no_broken_profile(self):
        self.nm.fail_connect = ("Connection activation failed: Secrets were "
                                "required, but not provided.")
        with self.assertRaises(PlatformError) as caught:
            self.wifi.connect("Ten Forward", "wrong")
        self.assertIn("password", str(caught.exception))
        self.assertNotIn("Ten Forward", self.nm.saved,
                         "the failed attempt's profile would be reused next time")

    def test_out_of_range_says_so(self):
        self.nm.fail_connect = "No network with SSID 'Defiant' found."
        with self.assertRaises(PlatformError) as caught:
            self.wifi.connect("Defiant")
        self.assertEqual(str(caught.exception), "Defiant is not in range")

    def test_disconnect_leaves_the_network_and_keeps_the_radio(self):
        self.wifi.disconnect()
        self.assertIn(["device", "disconnect", "wlp2s0"], self.nm.calls)
        self.assertNotIn(["radio", "wifi", "off"], self.nm.calls)
        with self.assertRaises(PlatformError):
            self.wifi.disconnect()          # nothing joined now

    def test_forgetting_an_unknown_network_refuses(self):
        with self.assertRaises(PlatformError):
            self.wifi.forget("Defiant")
        self.wifi.forget("Holodeck 3")
        self.assertNotIn("Holodeck 3", self.nm.saved)


# ==========================================================================
# BlueZ
# ==========================================================================

FAKE_BLUETOOTHCTL = r'''#!@PYTHON@
"""A stand-in for bluetoothctl: one-shot commands, and an interactive session."""
import json, os, sys
STATE = os.environ["FAKE_BT_STATE"]
def load():
    with open(STATE) as f: return json.load(f)
def save(s):
    with open(STATE, "w") as f: json.dump(s, f)
def out(text):
    sys.stdout.write(text); sys.stdout.flush()
def info(s, addr):
    d = s["devices"].get(addr)
    if d is None or d.get("hidden"):
        return f"Device {addr} not available\n"
    yn = lambda v: "yes" if v else "no"
    return (f"Device {addr} (public)\n\tName: {d['name']}\n\tAlias: {d['name']}\n"
            f"\tIcon: {d.get('icon','')}\n\tPaired: {yn(d['paired'])}\n"
            f"\tTrusted: {yn(d.get('trusted'))}\n\tConnected: {yn(d['connected'])}\n"
            + (f"\tRSSI: 0xffffffc4 ({d['rssi']})\n" if 'rssi' in d else ""))
s = load()
args = sys.argv[1:]
if args:
    cmd = args[0]
    if cmd == "show":
        out("Controller 00:1A:7D:DA:71:13 (public)\n\tName: fake\n\tAlias: cdos-console\n"
            f"\tPowered: {'yes' if s['powered'] else 'no'}\n\tDiscovering: no\n")
    elif cmd == "devices":
        for a, d in s["devices"].items():
            if not d.get("hidden"):
                out(f"Device {a} {d['name']}\n")
    elif cmd == "info":
        out(info(s, args[1]))
    elif cmd == "power":
        if s.get("blocked") and args[1] == "on":
            out("Failed to set power on: org.bluez.Error.Blocked\n")
        else:
            s["powered"] = args[1] == "on"; save(s)
            out(f"Changing power {args[1]} succeeded\n")
    elif cmd == "remove":
        if s["devices"].pop(args[1], None) is None:
            out(f"Device {args[1]} not available\n")
        else:
            save(s); out("Device has been removed\n")
    elif cmd == "disconnect":
        s["devices"][args[1]]["connected"] = False; save(s)
        out(f"Attempting to disconnect from {args[1]}\nSuccessful disconnected\n")
    sys.exit(0)

prompt = "\x1b[0;94m[bluetooth]\x1b[0m# "
out("Agent registered\n" + prompt)
for line in sys.stdin:
    line = line.strip()
    s = load()
    if line == "quit":
        break
    if line == "agent off":
        out("Agent unregistered\n")
    elif line.startswith("agent "):
        s["agent"] = line.split()[1]; save(s); out("Agent registered\n")
    elif line == "default-agent":
        out("Default agent request successful\n")
    elif line == "scan on":
        out("Discovery started\n")
        for a, d in s["devices"].items():
            if d.get("hidden"):
                d["hidden"] = False
                out(f"\x1b[0;93m[NEW]\x1b[0m Device {a} {d['name']}\n")
        save(s)
    elif line == "scan off":
        out("Discovery stopped\n")
    elif line.startswith("pair "):
        addr = line.split()[1]
        mode = s.get("pair", "ok")
        out(f"Attempting to pair with {addr}\n")
        if mode == "pin":
            out("[agent] Enter PIN code: ")         # no newline, as the real one
            answer = sys.stdin.readline().strip()
            s = load()
            if answer != "0000":
                out("Failed to pair: org.bluez.Error.AuthenticationFailed\n"); continue
        if mode == "fail":
            out("Failed to pair: org.bluez.Error.AuthenticationFailed\n"); continue
        s["devices"][addr]["paired"] = True; save(s)
        out("[CHG] Device " + addr + " Paired: yes\nPairing successful\n")
    elif line.startswith("trust "):
        addr = line.split()[1]
        s["devices"][addr]["trusted"] = True; save(s)
        out(f"Changing {addr} trust succeeded\n")
    elif line.startswith("connect "):
        addr = line.split()[1]
        if s.get("connect") == "noprofile":
            out("Failed to connect: org.bluez.Error.Failed br-connection-profile-unavailable\n")
            continue
        s["devices"][addr]["connected"] = True; save(s)
        out(f"Attempting to connect to {addr}\n[CHG] Device {addr} Connected: yes\nConnection successful\n")
    out(prompt)
'''

PHONE = "AA:BB:CC:11:22:55"
HEADSET = "AA:BB:CC:11:22:33"


class TestLinuxBluetooth(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        tool = root / "bin" / "bluetoothctl"
        tool.parent.mkdir()
        tool.write_text(FAKE_BLUETOOTHCTL.replace("@PYTHON@", sys.executable))
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
        self.state_path = root / "state.json"
        self.write_state({
            "powered": True,
            "devices": {
                HEADSET: {"name": "WH-1000XM5", "paired": True, "connected": True,
                          "icon": "audio-headset", "rssi": -48},
                PHONE: {"name": "Pixel 9", "paired": False, "connected": False,
                        "icon": "phone"},
            },
        })
        sysfs = root / "sys-bluetooth"
        (sysfs / "hci0").mkdir(parents=True)
        self.env = unittest.mock.patch.dict(os.environ, {
            "PATH": f"{tool.parent}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_BT_STATE": str(self.state_path),
        })
        self.env.start()
        self.bt = LinuxBluetooth()
        self.bt.SYSFS = sysfs

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write_state(self, state):
        self.state_path.write_text(json.dumps(state))

    def state(self):
        return json.loads(self.state_path.read_text())

    def test_info_parsing(self):
        device = parse_info(HEADSET, (
            f"Device {HEADSET} (public)\n\tName: WH\n\tAlias: WH-1000XM5\n"
            "\tIcon: audio-headset\n\tPaired: yes\n\tBonded: yes\n"
            "\tTrusted: yes\n\tConnected: no\n\tRSSI: 0xffffffd0 (-48)\n"))
        self.assertEqual(device.name, "WH-1000XM5", "the alias is what people set")
        self.assertTrue(device.paired and device.trusted)
        self.assertFalse(device.connected)
        self.assertEqual(device.signal, -48)
        self.assertIsNone(parse_info(PHONE, f"Device {PHONE} not available\n"))

    def test_state_and_devices(self):
        state = self.bt.state()
        self.assertEqual(state, BluetoothState(
            present=True, powered=True, discovering=False,
            name="cdos-console", address="00:1A:7D:DA:71:13"))
        devices = {d.address: d for d in self.bt.devices()}
        self.assertTrue(devices[HEADSET].connected)
        self.assertEqual(devices[HEADSET].kind, "audio-headset")
        self.assertFalse(devices[PHONE].paired)

    def test_no_adapter_is_an_absence_not_a_hang(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.bt.SYSFS = empty
        with self.assertRaises(PlatformError) as caught:
            self.bt.state()
        self.assertIn("this machine has no bluetooth adapter", str(caught.exception))

    def test_connect_pairs_trusts_and_connects_in_one_act(self):
        self.assertTrue(self.bt.connect(PHONE))
        phone = self.state()["devices"][PHONE]
        self.assertTrue(phone["paired"] and phone["trusted"] and phone["connected"])
        self.assertEqual(self.state()["agent"], "NoInputNoOutput",
                         "the agent that makes pairing need no dialog")

    def test_a_pin_prompt_without_a_newline_is_answered(self):
        state = self.state()
        state["pair"] = "pin"
        self.write_state(state)
        started = time.monotonic()
        self.assertTrue(self.bt.connect(PHONE))
        self.assertLess(time.monotonic() - started, 10,
                        "a line reader would have waited for the timeout")

    def test_a_device_bluez_has_forgotten_is_found_again(self):
        state = self.state()
        state["devices"][PHONE]["hidden"] = True
        self.write_state(state)
        self.assertTrue(self.bt.connect(PHONE))
        self.assertTrue(self.state()["devices"][PHONE]["connected"])

    def test_refused_pairing_is_a_reason(self):
        state = self.state()
        state["pair"] = "fail"
        self.write_state(state)
        with self.assertRaises(PlatformError) as caught:
            self.bt.connect(PHONE)
        self.assertIn("pairing mode", str(caught.exception))

    def test_no_audio_profile_says_what_to_install(self):
        state = self.state()
        state["connect"] = "noprofile"
        state["devices"][HEADSET]["connected"] = False
        self.write_state(state)
        with self.assertRaises(PlatformError) as caught:
            self.bt.connect(HEADSET)
        self.assertIn("bluetooth module", str(caught.exception))

    def test_power_and_rfkill_block(self):
        self.assertTrue(self.bt.set_power(False))
        self.assertFalse(self.state()["powered"])
        state = self.state()
        state["blocked"] = True
        self.write_state(state)
        with self.assertRaises(PlatformError) as caught:
            self.bt.set_power(True)
        self.assertIn("kill switch", str(caught.exception))

    def test_connecting_while_off_refuses_plainly(self):
        self.bt.set_power(False)
        with self.assertRaises(PlatformError) as caught:
            self.bt.connect(PHONE)
        self.assertIn("switched off", str(caught.exception))

    def test_disconnect_all_and_forget(self):
        self.assertTrue(self.bt.disconnect())
        self.assertFalse(self.state()["devices"][HEADSET]["connected"])
        with self.assertRaises(PlatformError):
            self.bt.disconnect()            # nothing connected now
        self.assertTrue(self.bt.forget(HEADSET))
        self.assertNotIn(HEADSET, self.state()["devices"])
        with self.assertRaises(PlatformError):
            self.bt.forget(HEADSET)

    def test_an_address_is_checked_before_anything_runs(self):
        for bad in ("", "pixel", "AA:BB:CC:11:22", "AA:BB:CC:11:22:55; reboot"):
            with self.assertRaises(PlatformError):
                self.bt.connect(bad)


# ==========================================================================
# The shell's half
# ==========================================================================

class FakeRadios:
    """Bindings for the communications capabilities, in place of a machine."""

    def __init__(self):
        self.calls = []
        self.wireless = WirelessState(True, True, "Enterprise-D", "wlp2s0", "192.168.1.47")
        self.networks = [AccessPoint("Enterprise-D", 92, "WPA2", True, True),
                         AccessPoint("Ten Forward", 71, "WPA2", False, False),
                         AccessPoint("Starbase Guest", 44, "", False, False)]
        self.bt = BluetoothState(True, True, False, "cdos-console", "00:1A")
        self.devices = [
            BluetoothDevice(PHONE, "Pixel 9", kind="phone", signal=-67),
            BluetoothDevice("AA:BB:CC:11:22:66", "AA:BB:CC:11:22:66", signal=-40),
            BluetoothDevice(HEADSET, "WH-1000XM5", True, True, True, "audio-headset"),
        ]
        self.shot_error = ""

    def record(self, name, value=True):
        def call(**kwargs):
            self.calls.append((name, kwargs))
            return value() if callable(value) else value
        return call

    def screenshot(self, path=""):
        self.calls.append(("display.screenshot", {"path": path}))
        if self.shot_error:
            raise PlatformError(self.shot_error)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG fake")
        return path

    def install(self, shell):
        bindings = shell.capabilities._bindings
        bindings["network.wireless"] = self.record("network.wireless", lambda: self.wireless)
        bindings["network.scan"] = self.record("network.scan", lambda: self.networks)
        for name in ("network.connect", "network.disconnect", "network.wifi_on",
                     "network.wifi_off", "network.forget", "bluetooth.on",
                     "bluetooth.off", "bluetooth.connect", "bluetooth.disconnect",
                     "bluetooth.forget"):
            bindings[name] = self.record(name)
        bindings["bluetooth.state"] = self.record("bluetooth.state", lambda: self.bt)
        bindings["bluetooth.devices"] = self.record("bluetooth.devices", lambda: self.devices)
        bindings["bluetooth.scan"] = self.record("bluetooth.scan", lambda: self.devices)
        bindings["display.screenshot"] = self.screenshot
        # The registry also asks whether the domain exists on this machine.
        shell.capabilities.domain_available = lambda domain: True

    def names(self):
        return [name for name, _ in self.calls]


class TestBackgroundCapabilities(unittest.TestCase):

    def setUp(self):
        self.shell = shell_with_screens()
        self.radios = FakeRadios()
        self.radios.install(self.shell)

    def test_a_harmless_one_runs_and_reports(self):
        results = []
        started = self.shell.run_capability_in_background(
            "network.scan", on_done=results.append)
        self.assertTrue(started)
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].value[0].ssid, "Enterprise-D")

    def test_an_effectful_one_waits_for_confirm(self):
        results = []
        started = self.shell.run_capability_in_background(
            "network.wifi_off", on_done=results.append)
        self.assertFalse(started)
        self.assertEqual(results, [])
        self.assertNotIn("network.wifi_off", self.radios.names())
        labels = [label for label, _ in self.shell.pending_options]
        self.assertEqual(labels, ["CONFIRM", "CANCEL"])
        dict(self.shell.pending_options)["CONFIRM"]()
        self.assertIn("network.wifi_off", self.radios.names())
        self.assertTrue(results[0].ok)
        self.assertEqual(self.shell.pending_options, [])

    def test_cancel_runs_nothing_and_never_starts(self):
        starts = []
        self.shell.run_capability_in_background(
            "bluetooth.forget", on_start=lambda: starts.append(1), address=PHONE)
        dict(self.shell.pending_options)["CANCEL"]()
        self.assertNotIn("bluetooth.forget", self.radios.names())
        self.assertEqual(starts, [])

    def test_a_refusal_still_answers(self):
        results = []
        self.shell.run_capability_in_background("bluetooth.connect",
                                                on_done=results.append)
        self.assertFalse(results[0].ok)
        self.assertIn("address", results[0].error)

    def test_on_a_real_loop_the_work_leaves_the_interface_thread(self):
        posted = []
        self.shell.call_on_main = posted.append
        import threading
        seen = {}
        original = self.shell.capabilities._bindings["network.scan"]

        def scan(**kwargs):
            seen["thread"] = threading.current_thread().name
            return original(**kwargs)
        self.shell.capabilities._bindings["network.scan"] = scan
        results = []
        self.shell.run_capability_in_background("network.scan",
                                                on_done=results.append)
        deadline = time.monotonic() + 5
        while not posted and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotEqual(seen.get("thread"), threading.main_thread().name)
        self.assertEqual(results, [], "the answer waits for the interface thread")
        posted[0]()
        self.assertTrue(results[0].ok)


class TestComms(unittest.TestCase):

    def setUp(self):
        self.shell = shell_with_screens()
        self.radios = FakeRadios()
        self.radios.install(self.shell)
        self.comms = self.shell.comms

    def test_refresh_reads_both_radios(self):
        self.comms.refresh()
        self.assertEqual(self.comms.wireless.ssid, "Enterprise-D")
        self.assertEqual(len(self.comms.networks), 3)
        self.assertEqual(self.comms.busy, {})
        self.assertEqual([d.name for d in self.comms.devices][0], "WH-1000XM5",
                         "connected first")
        self.assertEqual(self.comms.devices[-1].name, "AA:BB:CC:11:22:66",
                         "nameless last, however strong")

    def test_a_radio_that_is_off_is_not_scanned(self):
        self.radios.wireless = WirelessState(True, False)
        self.comms.refresh_wifi()
        self.assertNotIn("network.scan", self.radios.names())
        self.assertEqual(self.comms.networks, [])

    def test_an_unavailable_radio_keeps_its_reason(self):
        def broken(**kwargs):
            raise PlatformError("wireless control is not available: "
                                "NetworkManager is not running")
        self.shell.capabilities._bindings["network.wireless"] = broken
        self.comms.refresh_wifi()
        self.assertIsNone(self.comms.wireless)
        self.assertIn("NetworkManager", self.comms.wifi_error)

    def test_connect_passes_the_password_through_the_gate(self):
        self.comms.connect_wifi("Ten Forward", "engage")
        self.assertNotIn("network.connect", self.radios.names())
        dict(self.shell.pending_options)["CONFIRM"]()
        name, kwargs = [c for c in self.radios.calls if c[0] == "network.connect"][0]
        self.assertEqual(kwargs, {"ssid": "Ten Forward", "password": "engage"})
        self.assertEqual(self.comms.note["wifi"], "JOINED Ten Forward")

    def test_bluetooth_connect_needs_no_confirmation(self):
        """Reversible by disconnecting, so asking first would only teach
        people to press CONFIRM without reading it."""
        self.comms.connect_device(PHONE, "Pixel 9")
        self.assertIn(("bluetooth.connect", {"address": PHONE}), self.radios.calls)
        self.assertEqual(self.shell.pending_options, [])

    def test_a_failure_is_shown_where_the_press_was(self):
        def refuse(**kwargs):
            raise PlatformError("pairing was refused — put the device in "
                                "pairing mode and try again")
        self.shell.capabilities._bindings["bluetooth.connect"] = refuse
        self.comms.connect_device(PHONE, "Pixel 9")
        self.assertTrue(self.comms.note["bluetooth"].startswith("UNABLE TO COMPLY"))
        self.assertIn("pairing mode", self.shell.toast_text)


class TestTheScreen(unittest.TestCase):

    def setUp(self):
        self.shell = shell_with_screens()
        self.radios = FakeRadios()
        self.radios.install(self.shell)
        self.shell.go("comms")
        self.screen = self.shell.screens["comms"]

    def draw(self, width=1024, height=600):
        import cairo
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(surface)
        ctx = self.shell.context(width, height)
        self.screen.invalidate()
        self.screen.draw(cr, ctx)
        return surface

    def press(self, name):
        element = self.screen.g.get(name)
        self.assertIsNotNone(element, f"no control named {name}")
        self.assertTrue(element.enabled, f"{name} is disabled")
        x, y = element.x + element.w / 2, element.y + element.h / 2
        pressed = self.screen.on_press(x, y)
        self.screen.on_release(x, y, pressed)
        self.draw()

    def key(self, name, char=""):
        keyval = ord(char) if char else 0
        return self.screen.on_key(keyval, name)

    def test_it_is_reachable(self):
        self.assertIn("comms", self.shell.screens)
        self.shell.go("main")
        self.shell.run_action("communications")
        self.assertEqual(self.shell.current, "comms")

    def test_every_state_renders_at_every_size(self):
        states = [
            lambda: None,
            lambda: self.screen.select_network("Ten Forward"),
            lambda: setattr(self.screen, "keyboard", True),
            lambda: self.screen.switch_tab(1),
            lambda: self.screen.select_device(PHONE),
        ]
        for setup in states:
            setup()
            for size in ((1024, 600), (1920, 1080), (3840, 2160)):
                with self.subTest(size=size):
                    self.draw(*size)

    def test_typing_a_password_and_joining(self):
        self.draw()
        self.screen.select_network("Ten Forward")
        self.draw()
        self.assertFalse(self.screen.g.get("connect").enabled,
                         "no password, nothing to join with")
        for ch in "Engagé!":
            self.screen.on_key(ord(ch) if ord(ch) < 0x100 else 0x01000000 + ord(ch), ch)
        self.assertEqual(self.screen.password, "Engagé!")
        self.key("BackSpace")
        self.key("Return")
        confirm = dict(self.shell.pending_options)["CONFIRM"]
        self.draw()
        self.assertIsNotNone(self.screen.g.get("confirm_0"),
                             "the confirmation must be on this screen")
        confirm()
        call = [c for c in self.radios.calls if c[0] == "network.connect"][-1]
        self.assertEqual(call[1], {"ssid": "Ten Forward", "password": "Engagé"})

    def test_the_on_screen_keyboard_types(self):
        self.draw()
        self.screen.select_network("Ten Forward")
        self.draw()
        self.press("pw_keyboard")
        self.press("key_shift")
        self.press("key_1_0")          # Q, then back to lower case
        self.press("key_1_1")          # w
        self.press("key_symbols")
        self.press("key_0_0")          # !
        self.assertEqual(self.screen.password, "Qw!")
        self.press("key_delete")
        self.assertEqual(self.screen.password, "Qw")

    def test_the_way_out_is_never_swallowed(self):
        self.draw()
        self.screen.select_network("Ten Forward")
        for key in ("F1", "F5", "F9", "F12"):
            self.assertFalse(self.key(key), f"{key} was swallowed")
        self.assertTrue(self.key("Escape"), "first Escape leaves the field")
        self.assertFalse(self.screen.typing)
        self.assertFalse(self.key("Escape"), "the second goes back")

    def test_an_open_or_saved_network_asks_for_no_password(self):
        self.draw()
        self.screen.select_network("Starbase Guest")
        self.draw()
        self.assertIsNone(self.screen.g.get("pw_field"))
        self.assertTrue(self.screen.g.get("connect").enabled)

    def test_bluetooth_tab_connects_a_new_device(self):
        self.draw()
        self.key("Tab")
        self.draw()
        self.assertEqual(self.screen.tab, 1)
        self.screen.select_device(PHONE)
        self.draw()
        self.assertEqual(self.screen.g.get("connect").label, "PAIR + CONNECT")
        self.press("connect")
        self.assertIn(("bluetooth.connect", {"address": PHONE}), self.radios.calls)


class TestScreenshots(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.env = unittest.mock.patch.dict(os.environ, {
            "HOME": self.home.name,
            "XDG_CONFIG_HOME": str(Path(self.home.name) / ".config")})
        self.env.start()
        self.shell = shell_with_screens()
        self.radios = FakeRadios()
        self.radios.install(self.shell)

    def tearDown(self):
        self.env.stop()
        self.home.cleanup()

    def test_it_goes_in_its_own_folder_under_pictures(self):
        self.shell.take_screenshot(delay=0)
        path = Path(self.shell.last_screenshot)
        self.assertEqual(path.parent.name, "EARTH OS Screenshots")
        self.assertEqual(path.parent.parent, Path(self.home.name) / "Pictures")
        self.assertTrue(path.exists())
        self.assertIn("SCREENSHOT SAVED", self.shell.toast_text)

    def test_when_the_screen_cannot_be_captured_the_shell_draws_itself(self):
        self.radios.shot_error = "this machine has no display session to capture"
        self.shell.surface_size = (800, 500)
        self.shell.take_screenshot(delay=0)
        path = Path(self.shell.last_screenshot)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes()[:4], b"\x89PNG")
        import cairo
        image = cairo.ImageSurface.create_from_png(str(path))
        self.assertEqual((image.get_width(), image.get_height()), (800, 500))
        # Named after the shell (EARTH OS by default), never "SCREENSHOT":
        # the toast must say only the shell was saved, not the whole screen.
        name = str(self.shell.config.get("shell", "computer_name")).upper()
        self.assertIn(f"{name} SCREEN SAVED", self.shell.toast_text,
                      "it says it saved the shell only, not the whole screen")

    def test_every_way_to_ask_reaches_it(self):
        corpus = CapabilityCorpus(self.shell.capabilities)
        for phrase in ("take a screenshot", "computer, screenshot please"):
            self.assertEqual(corpus.resolve(phrase).capability,
                             "display.screenshot", phrase)
        self.shell.handle_utterance("take a screenshot")
        self.assertTrue(Path(self.shell.last_screenshot).exists())
        self.assertIsNotNone(self.shell.screens["engineering"])
        self.shell.go("engineering")
        self.shell.screens["engineering"].ensure_built(self.shell.context(1024, 600))
        # DIAGNOSTICS carries the home column, so its SCREEN SHOT is the
        # column's entry rather than the chrome's; either one keeps the
        # promise that every screen has the button.
        group = self.shell.screens["engineering"].g
        self.assertIsNotNone(group.get("chrome_screenshot") or group.get("myScreenshot"),
                             "a SCREEN SHOT button on every screen")

    def test_a_picture_taken_by_the_session_key_is_announced(self):
        folder = self.shell.screenshot_dir()
        folder.mkdir(parents=True)
        self.shell.notice_screenshots(interval=0)          # first look: baseline
        self.assertEqual(self.shell.toast_text, "")
        time.sleep(0.02)
        (folder / "cdos-from-the-key.png").write_bytes(b"\x89PNG")
        os.utime(folder, (time.time() + 1, time.time() + 1))
        self.shell.notice_screenshots(interval=0)
        self.assertIn("cdos-from-the-key.png", self.shell.toast_text)

    def test_the_command_line_flag_exists_without_gtk(self):
        from earth_os.cli import build_parser
        self.assertTrue(build_parser().parse_args(["--screenshot"]).screenshot)


class TestTheWordsReachTheRightThing(unittest.TestCase):
    """Short phrasings already meant something before these capabilities
    existed — "switch off" is shutdown. The longer phrase has to win."""

    def test_radio_phrasings_do_not_fall_through_to_power(self):
        corpus = CapabilityCorpus()
        cases = {
            "switch off the wifi": "network.wifi_off",
            "turn off bluetooth": "bluetooth.off",
            "switch off bluetooth": "bluetooth.off",
            "turn on the wifi": "network.wifi_on",
            "disconnect bluetooth": "bluetooth.disconnect",
            "what wifi networks are there": "network.scan",
            "is bluetooth on": "bluetooth.state",
        }
        for phrase, capability in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(corpus.resolve(phrase).capability, capability)

    def test_switching_a_radio_off_asks_first(self):
        corpus = CapabilityCorpus()
        self.assertTrue(corpus.resolve("turn off bluetooth").effectful)
        self.assertTrue(corpus.resolve("turn off wifi").effectful)
        self.assertFalse(corpus.resolve("turn on wifi").effectful)


if __name__ == "__main__":
    unittest.main()


class TestCapturingDoesNotDisturbTheScreen(unittest.TestCase):
    """Reported: SCREEN SHOT on the engineering screen opened a program
    called gnome-screenshot, the shell framed it and jumped to the main
    screen, and the picture was of that instead.

    Two causes, one test class each half: gnome-screenshot was tried outside
    GNOME, where it opens a window rather than saving; and the shell adopted
    the window that capture opened as if the person had opened it.
    """

    def chain(self, **env):
        from earth_os.platform.linux_adapter import LinuxDisplay
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("WAYLAND_DISPLAY", "DISPLAY", "XDG_CURRENT_DESKTOP")}
        clean.update(env)
        with unittest.mock.patch.dict(os.environ, clean, clear=True):
            return [tool[0] for tool in LinuxDisplay().screenshot_chain()]

    def test_desktop_tools_only_on_their_own_desktop(self):
        self.assertEqual(self.chain(WAYLAND_DISPLAY="wayland-1",
                                    XDG_CURRENT_DESKTOP="EARTHOS"), ["grim"])
        self.assertEqual(self.chain(DISPLAY=":0", XDG_CURRENT_DESKTOP="XFCE"),
                         ["maim", "scrot", "import"])
        self.assertIn("gnome-screenshot",
                      self.chain(WAYLAND_DISPLAY="wayland-0",
                                 XDG_CURRENT_DESKTOP="ubuntu:GNOME"))
        self.assertIn("spectacle", self.chain(DISPLAY=":0",
                                              XDG_CURRENT_DESKTOP="KDE"))
        self.assertNotIn("gnome-screenshot",
                         self.chain(DISPLAY=":0", XDG_CURRENT_DESKTOP="KDE"))

    def setUp(self):
        from earth_os.windows import Window
        self.Window = Window
        self.home = tempfile.TemporaryDirectory()
        self.env = unittest.mock.patch.dict(os.environ, {
            "HOME": self.home.name,
            "XDG_CONFIG_HOME": str(Path(self.home.name) / ".config")})
        self.env.start()
        self.shell = shell_with_screens()
        self.radios = FakeRadios()
        self.radios.install(self.shell)

        class Backend:
            name = "fake"
            available = True
            can_place = True

            def __init__(self):
                self.windows = []
                self.placed = []

            def list_windows(self):
                return list(self.windows)

            def status(self):
                return "fake"

            def focus(self, w):
                return True

            def place(self, w, x, y, width, height):
                self.placed.append(w.handle)
                return True

            def fullscreen(self, w, on=True):
                return True

        self.backend = Backend()
        self.shell.windows.backend = self.backend
        self.shell.frame_rect = (200, 100, 1000, 600)
        self.shell.poll_windows()                       # seed

    def tearDown(self):
        self.env.stop()
        self.home.cleanup()

    def test_a_window_opened_by_the_capture_is_left_alone(self):
        self.shell.go("engineering")

        def capture_that_opens_a_window(path=""):
            self.backend.windows.append(
                self.Window(handle="9", title="Screenshot", app="gnome-screenshot"))
            self.shell.poll_windows()
            raise PlatformError("could not capture the screen")
        self.shell.capabilities._bindings["display.screenshot"] = \
            capture_that_opens_a_window
        self.shell.take_screenshot(delay=0)
        self.assertEqual(self.shell.current, "engineering",
                         "the capture must not move the person off their screen")
        self.assertIsNone(self.shell.framed_window)
        self.assertEqual(self.backend.placed, [])

    def test_the_picture_is_of_the_screen_it_was_pressed_on(self):
        self.shell.go("engineering")
        drawn = []
        import earth_os.screengrab as screengrab
        original = screengrab.render_shell

        def spy(shell, path, width, height, screen_name=""):
            drawn.append(screen_name)
            return original(shell, path, width, height, screen_name=screen_name)

        def capture_that_changes_the_screen(path=""):
            self.shell.go("main")
            raise PlatformError("could not capture the screen")
        self.shell.capabilities._bindings["display.screenshot"] = \
            capture_that_changes_the_screen
        with unittest.mock.patch.object(screengrab, "render_shell", spy):
            self.shell.take_screenshot(delay=0)
        self.assertEqual(drawn, ["engineering"])

    def test_programs_opened_afterwards_are_framed_as_before(self):
        self.shell.take_screenshot(delay=0)
        self.shell._capture_quiet_until = 0.0          # the quiet spell is over
        self.backend.windows.append(
            self.Window(handle="7", title="Firefox", app="firefox"))
        self.shell.poll_windows()
        self.assertEqual(self.backend.placed, ["7"])
