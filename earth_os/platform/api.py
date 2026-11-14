"""Abstract capability interfaces for CDOS Platform.

Each capability defines what CDOS can ask of the platform, independent of
how that platform implements the answer. Implementations inherit from these
and provide concrete methods for their target platform.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


class PlatformError(Exception):
    """Base exception for platform errors."""
    pass


@dataclass
class ProcessInfo:
    """Information about a running process.

    ``cpu_percent`` is processor time as a percentage of one core, measured
    over the last ``cpu_window`` seconds — so a busy program on a four-core
    machine can read up to 400. It is **None when it was not measured**: a
    process too new to have two samples, or a platform that cannot take them.
    None is not zero. Zero means the process was measured and did not run.
    """
    pid: int
    name: str
    command: str
    state: str                             # "running", "stopped", etc.
    cpu_percent: Optional[float]
    memory_bytes: int
    cpu_window: Optional[float] = None     # seconds the cpu figure covers


class ProcessHandle(ABC):
    """A process that was just started, and a grip on it.

    Returning a bare pid from :meth:`Process.launch` would be tidier, but a
    pid is not enough to answer the question the shell actually asks a second
    later: *did that program survive?*  Plenty of things start and die
    immediately — a D-Bus activated program with no session bus is the common
    one — and reporting "launched" for something the user never sees is worse
    than reporting an error.  Answering that needs the exit code and whatever
    went to stderr, and a pid gives neither.

    Platforms that have no processes in the Unix sense (a robot running queued
    commands, say) implement this over whatever they do have.
    """

    pid: int
    label: str

    @abstractmethod
    def poll(self) -> Optional[int]:
        """Exit code if it has finished, or None while it is still running."""
        pass

    @abstractmethod
    def wait(self, timeout: Optional[float] = None) -> Tuple[Optional[int], str]:
        """Wait for exit, up to *timeout* seconds.

        Returns ``(exit_code, stderr_text)``.  An exit code of ``None`` means
        it was still running when the timeout ran out — which, for a program
        that was supposed to open a window, is the good outcome.
        """
        pass

    @abstractmethod
    def release(self) -> None:
        """Stop watching, and stop holding anything the program is filling.

        A caller that captures a program's error output only cares about it
        for the first few seconds — long enough to catch one that dies on
        startup.  After that the capture becomes a liability: a program that
        keeps writing to a buffer nobody is emptying eventually blocks on a
        full one and hangs, and plenty of ordinary desktop programs chatter
        warnings for as long as they run.  Releasing says the watch is over
        and whatever is still arriving can be thrown away.

        Must be safe to call more than once, and on a program that has
        already exited.
        """
        pass


@dataclass
class NetworkInterface:
    """A network interface on this system."""
    name: str
    address: str                           # IP address
    state: str                             # "up", "down", etc.
    rx_bytes: int
    tx_bytes: int


@dataclass
class HardwareDevice:
    """A hardware device attached to this system."""
    name: str
    device_type: str                       # "cpu", "gpu", "disk", "memory", etc.
    status: str
    data: Dict[str, object]


@dataclass
class AccessPoint:
    """A wireless network in range.

    One entry per network name, not per transmitter: a house with three
    access points broadcasting one name is one choice to a person, and the
    adapter keeps the strongest of them.
    """
    ssid: str
    signal: int                            # 0-100
    security: str                          # "" for an open network
    in_use: bool = False                   # this machine is joined to it now
    saved: bool = False                    # credentials are already stored


@dataclass
class WirelessState:
    """The wireless radio, and what it is joined to."""
    hardware: bool                         # there is a wireless device at all
    enabled: bool                          # the radio is switched on
    ssid: str = ""                         # the network joined, "" if none
    device: str = ""                       # the interface doing it
    address: str = ""                      # its IP address, "" if none


@dataclass
class BluetoothDevice:
    """A Bluetooth device this machine knows about or can see."""
    address: str
    name: str
    paired: bool = False
    connected: bool = False
    trusted: bool = False
    kind: str = ""                         # "audio-headset", "input-mouse", ...
    signal: Optional[int] = None           # RSSI in dBm, while discovering


@dataclass
class BluetoothState:
    """The Bluetooth adapter."""
    present: bool
    powered: bool
    discovering: bool = False
    name: str = ""
    address: str = ""


# --------------------------------------------------------------------------
# Abstract Capabilities
# --------------------------------------------------------------------------


class Capability(ABC):
    """Base class for all platform capabilities."""

    @abstractmethod
    def available(self) -> bool:
        """True if this capability is available on this platform."""
        pass


class System(Capability):
    """System-level operations: power, reboot, shutdown."""

    @abstractmethod
    def reboot(self) -> None:
        """Restart the system."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Power off the system."""
        pass

    @abstractmethod
    def suspend(self) -> None:
        """Suspend to RAM."""
        pass

    @abstractmethod
    def lock(self) -> None:
        """Lock the session."""
        pass

    @abstractmethod
    def logout(self) -> None:
        """End the current user's session, leaving the machine running."""
        pass

    @abstractmethod
    def get_hostname(self) -> str:
        """Return the system hostname."""
        pass

    @abstractmethod
    def kernel(self) -> str:
        """What is running underneath, as a version string.

        In ``system`` rather than ``hardware`` because it describes the
        software this machine is running, not the metal it runs on — the same
        board can boot a different kernel tomorrow.
        """
        pass

    @abstractmethod
    def distro(self) -> str:
        """What this environment calls itself.

        The one place a distribution name is legitimately read, and it is
        below the boundary where it can never become a branch. CDOS does not
        ask *are we on Debian* in order to decide what to do; the adapter
        tries mechanisms in order. This exists so the machine can answer a
        person who asks, and for nothing else.
        """
        pass


class Process(Capability):
    """Process and program management."""

    @abstractmethod
    def launch(self, argv: List[str], name: str = "", detached: bool = True,
               capture_stderr: bool = True) -> ProcessHandle:
        """Start a program. Returns a handle the caller can watch.

        *argv* is an already-split command line; no platform is ever handed a
        string to pass through a shell.
        *name* is a human label for logging.
        *detached* means the process outlives the shell.
        *capture_stderr* keeps the program's error output so a caller can say
        why it died.  Pass ``False`` for a program that must survive this one
        — a handover to another session, say — because error output captured
        by a process that is about to exit leaves the program writing into a
        pipe with nothing on the other end.

        Raises :class:`PlatformError` if it could not be started at all.
        """
        pass

    @abstractmethod
    def terminate(self, pid: int, signal: str = "TERM") -> bool:
        """Send a signal to a process by name ("TERM", "KILL", "HUP").

        Named rather than numbered because signal numbers are a Unix detail,
        and this interface is meant to outlive Unix.
        """
        pass

    @abstractmethod
    def list_processes(self) -> List[ProcessInfo]:
        """Get info on running processes."""
        pass

    @abstractmethod
    def inspect(self, pid: int) -> Optional[ProcessInfo]:
        """Get info on a specific process."""
        pass

    @abstractmethod
    def wait_for_exit(self, pid: int, timeout: Optional[float] = None) -> int:
        """Wait for a process to exit. Returns exit code."""
        pass


class FileSystem(Capability):
    """File and directory operations."""

    @abstractmethod
    def read(self, path: str) -> bytes:
        """Read a file."""
        pass

    @abstractmethod
    def write(self, path: str, data: bytes, append: bool = False) -> bool:
        """Write a file. Returns True if successful."""
        pass

    @abstractmethod
    def list_directory(self, path: str) -> List[str]:
        """List contents of a directory."""
        pass

    @abstractmethod
    def create_directory(self, path: str, parents: bool = True) -> bool:
        """Create a directory. Returns True if successful."""
        pass

    @abstractmethod
    def delete(self, path: str, recursive: bool = False) -> bool:
        """Delete a file or directory. Returns True if successful."""
        pass

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a path exists."""
        pass

    @abstractmethod
    def is_directory(self, path: str) -> bool:
        """Check if a path is a directory."""
        pass


class Network(Capability):
    """Network operations."""

    @abstractmethod
    def list_interfaces(self) -> List[NetworkInterface]:
        """Get available network interfaces."""
        pass

    @abstractmethod
    def interface_status(self, name: str) -> Optional[NetworkInterface]:
        """Get status of a specific interface."""
        pass

    @abstractmethod
    def connect(self, ssid: str, password: str = "") -> bool:
        """Connect to a wireless network. Returns True if successful."""
        pass

    @abstractmethod
    def disconnect(self, name: str = "") -> bool:
        """Leave a network. With no name, leave the wireless network joined
        now; the radio stays on. Returns True if successful."""
        pass

    # -- wireless ----------------------------------------------------------
    #
    # Not abstract: a platform with no radio is a real platform. The default
    # refuses, which the capability layer reports as an honest "cannot",
    # rather than an adapter that has to invent an empty answer to compile.
    def wireless_state(self) -> WirelessState:
        """Whether there is a radio, whether it is on, and what it joined."""
        raise PlatformError("wireless is not supported on this platform")

    def scan(self, rescan: bool = True) -> List[AccessPoint]:
        """Wireless networks in range, strongest first."""
        raise PlatformError("wireless is not supported on this platform")

    def set_wireless(self, on: bool) -> bool:
        """Switch the wireless radio on or off."""
        raise PlatformError("wireless is not supported on this platform")

    def forget(self, ssid: str) -> bool:
        """Delete the stored credentials for a network."""
        raise PlatformError("wireless is not supported on this platform")


class Hardware(Capability):
    """Hardware enumeration and sensor access."""

    @abstractmethod
    def enumerate(self) -> List[HardwareDevice]:
        """List all hardware devices."""
        pass

    @abstractmethod
    def cpu_count(self) -> int:
        """Number of CPU cores."""
        pass

    @abstractmethod
    def cpu_model(self) -> str:
        """CPU model name."""
        pass

    @abstractmethod
    def memory_bytes(self) -> Tuple[int, int]:
        """Total and available memory in bytes. Returns (total, available)."""
        pass

    @abstractmethod
    def temperature(self) -> Optional[float]:
        """System temperature in Celsius, if available."""
        pass

    @abstractmethod
    def battery(self) -> Optional[Tuple[float, str]]:
        """Battery level (0.0-1.0) and charging state, if available.

        Returns (percentage, state) where state is "charging", "discharging",
        "full", or "unknown". Returns None if no battery.
        """
        pass

    # -- live readings --------------------------------------------------
    #
    # These answer *what is this machine doing now* rather than *what is this
    # machine*.  They live here rather than in the shell because the shell had
    # its own copy of every one of them — a second reader of ``/proc`` and
    # ``/sys`` feeding the gauges while these fed the catalogue.  Two
    # implementations of one number disagree eventually, and the one that is
    # wrong will be the one nobody is testing.
    #
    # Each returns a *meaning* rather than a raw counter: CPU load is a
    # fraction, not jiffies; network is bytes per second, not a byte total.
    # Turning counters into rates needs sampling, and how a platform exposes
    # its counters is a fact about that platform, so the sampling and its
    # state stay down here.

    @abstractmethod
    def cpu_load(self) -> float:
        """Overall CPU load, 0.0 to 1.0, sampled over time."""
        pass

    @abstractmethod
    def cpu_load_per_core(self) -> List[float]:
        """Load per core, in core order. Empty if the platform cannot say."""
        pass

    @abstractmethod
    def load_average(self) -> Tuple[float, float, float]:
        """One, five and fifteen minute load averages."""
        pass

    @abstractmethod
    def swap_bytes(self) -> Tuple[int, int]:
        """Total and free swap in bytes. ``(0, 0)`` where there is none."""
        pass

    @abstractmethod
    def disk_bytes(self, path: str = "/") -> Tuple[int, int]:
        """Total and free bytes on the filesystem holding *path*."""
        pass

    @abstractmethod
    def uptime(self) -> float:
        """Seconds since this machine came up."""
        pass

    @abstractmethod
    def process_count(self) -> int:
        """How many processes — or whatever this platform runs — exist now."""
        pass

    @abstractmethod
    def net_rate(self) -> Tuple[float, float]:
        """Receive and transmit rate in bytes per second, sampled over time."""
        pass


class Audio(Capability):
    """Audio input and output."""

    @abstractmethod
    def speak(self, text: str, rate: float = 1.0) -> bool:
        """Synthesize speech. Returns True if successful."""
        pass

    @abstractmethod
    def listen(self, timeout: float = 10.0) -> Optional[str]:
        """Capture and recognize speech. Returns recognized text or None."""
        pass

    @abstractmethod
    def set_volume(self, level: float) -> bool:
        """Set audio volume (0.0-1.0). Returns True if successful."""
        pass

    @abstractmethod
    def get_volume(self) -> float:
        """Get current audio volume (0.0-1.0)."""
        pass


class Display(Capability):
    """Display and screen operations."""

    @abstractmethod
    def set_brightness(self, level: float) -> bool:
        """Set screen brightness (0.0-1.0). Returns True if successful."""
        pass

    @abstractmethod
    def get_brightness(self) -> float:
        """Get current screen brightness (0.0-1.0)."""
        pass

    @abstractmethod
    def set_resolution(self, width: int, height: int) -> bool:
        """Set screen resolution. Returns True if successful."""
        pass

    @abstractmethod
    def get_resolution(self) -> Tuple[int, int]:
        """Get current screen resolution. Returns (width, height)."""
        pass

    def screenshot(self, path: str = "") -> str:
        """Capture the whole screen to a PNG at *path*; return where it went.

        The whole screen, not one program's window, so it shows whatever a
        person is looking at. Raises if nothing on this machine can do it.
        """
        raise PlatformError("screen capture is not supported on this platform")


class Bluetooth(Capability):
    """Short-range radio: headphones, keyboards, controllers, phones.

    Every method refuses by default, for the same reason as the wireless
    half of :class:`Network`: having no Bluetooth is a fact about a machine,
    not a broken adapter.
    """

    def available(self) -> bool:
        return False

    def state(self) -> BluetoothState:
        """The adapter: present, powered, discovering."""
        raise PlatformError("bluetooth is not supported on this platform")

    def set_power(self, on: bool) -> bool:
        """Switch the adapter on or off."""
        raise PlatformError("bluetooth is not supported on this platform")

    def devices(self) -> List[BluetoothDevice]:
        """Devices already known: paired ones, and any seen recently."""
        raise PlatformError("bluetooth is not supported on this platform")

    def scan(self, seconds: float = 8.0) -> List[BluetoothDevice]:
        """Look for devices for a while, then report everything known."""
        raise PlatformError("bluetooth is not supported on this platform")

    def connect(self, address: str) -> bool:
        """Connect, pairing and trusting first if it has never been paired."""
        raise PlatformError("bluetooth is not supported on this platform")

    def disconnect(self, address: str = "") -> bool:
        """Disconnect one device, or every connected one."""
        raise PlatformError("bluetooth is not supported on this platform")

    def forget(self, address: str) -> bool:
        """Remove the pairing; the device must be paired again to use it."""
        raise PlatformError("bluetooth is not supported on this platform")


@dataclass
class WindowInfo:
    """One window belonging to some other program.

    Flattened to plain data on purpose.  The adapter's own window type carries
    a compositor handle whose meaning differs per backend — a sway ``con_id``,
    an X11 window id — and letting that cross the boundary would put a
    compositor detail in the shell's hands.  A handle is passed back down as
    an opaque string and only the adapter knows what it means.
    """

    handle: str
    title: str
    app: str
    focused: bool = False


class WindowControl(Capability):
    """Other programs' windows: listing, focusing, placing.

    Named ``WindowControl`` rather than ``Window`` because a *window* is the
    thing and this is the ability to do something to it; the domain is still
    ``window``.

    Not every machine has this even when it has a screen.  GNOME and KDE on
    Wayland expose no such protocol to unprivileged clients — deliberately —
    so :meth:`available` being false is a normal, permanent state on a real
    desktop rather than a fault, and a robot has no windows at all.
    """

    @abstractmethod
    def list_windows(self) -> List[WindowInfo]:
        """Every window the compositor will admit to."""
        pass

    @abstractmethod
    def focus(self, handle: str) -> bool:
        """Bring a window to the front."""
        pass

    @abstractmethod
    def close(self, handle: str) -> bool:
        """Ask a window to close."""
        pass

    @abstractmethod
    def fullscreen(self, handle: str, on: bool = True) -> bool:
        """Put a window full screen, or take it out again."""
        pass

    @abstractmethod
    def place(self, handle: str, x: int, y: int, w: int, h: int) -> bool:
        """Put a window at an exact rectangle, in screen pixels.

        This is what makes frame mode possible: the shell keeps its chrome and
        hands the middle of the screen to the program, instead of the program
        covering everything.
        """
        pass

    @abstractmethod
    def can_place(self) -> bool:
        """Whether this machine can place a window at all.

        Listing and focusing are widely available; placing is not, so the two
        are asked about separately rather than lumped into ``available``.
        """
        pass


class User(Capability):
    """User identity and permissions."""

    @abstractmethod
    def current_user(self) -> str:
        """Get the currently logged-in username."""
        pass

    @abstractmethod
    def user_full_name(self, username: str) -> str:
        """Get a user's full name from the system database."""
        pass

    @abstractmethod
    def user_home(self) -> str:
        """Get the current user's home directory."""
        pass

    @abstractmethod
    def has_permission(self, action: str) -> bool:
        """Check if the current user can perform an action.

        Actions are platform-specific: "reboot", "shutdown", "install", etc.
        """
        pass

    @abstractmethod
    def list_sessions(self) -> List[str]:
        """List active sessions."""
        pass


class Platform(ABC):
    """The complete platform abstraction.

    A platform provides implementations of all capabilities. CDOS speaks to
    this interface; the actual implementation handles distribution-specific
    details.
    """

    name: str                              # "Linux", "Robot", "Embedded", etc.
    version: str                           # "1.0", "2.1", etc.

    system: System
    process: Process
    filesystem: FileSystem
    network: Network
    hardware: Hardware
    audio: Audio
    display: Display
    window: WindowControl
    user: User
    bluetooth: Bluetooth

    @abstractmethod
    def available(self) -> bool:
        """True if this platform can run on this system."""
        pass

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the platform. Returns True if successful."""
        pass

    def mechanisms(self) -> Dict[str, str]:
        """What would actually serve each area on this machine, by name.

        Introspection, not a capability: nothing above the boundary may call
        this to decide what to do, and it is deliberately absent from the
        catalogue so that it cannot. It exists for two readers who are both
        below or beside the line — the diagnostics screen, and the
        conformance harness.

        The harness is the reason it is worth having. Proving *the computer
        changed, CDOS didn't* means showing two things at once: that the
        semantic answers matched, **and** that the mechanisms underneath
        genuinely differed. Without this, a run on two distributions that
        happen to use identical tooling looks exactly like a real proof.

        Returns ``{area: mechanism}``; an adapter that cannot say returns an
        empty mapping.
        """
        return {}

    def capabilities(self) -> Dict[str, bool]:
        """Report which capabilities are available on this platform."""
        return {
            "system": self.system.available(),
            "process": self.process.available(),
            "filesystem": self.filesystem.available(),
            "network": self.network.available(),
            "hardware": self.hardware.available(),
            "audio": self.audio.available(),
            "display": self.display.available(),
            "window": self.window.available(),
            "user": self.user.available(),
            "bluetooth": self.bluetooth.available(),
        }
