"""A robot that does nothing, to prove that a robot could.

This adapter drives no hardware.  That is the point of it.  The expensive
question in Phase 3 is not *"can we talk to a servo"* — it is whether the
architecture actually lets a second platform take over from Linux at all, and
that question can be answered in an afternoon by an adapter made of nothing.

It has already earned its keep.  The registry used to ignore the ``priority``
argument it documented, and :meth:`LinuxPlatform.available` returns true on
*any* Linux — including a Linux robot.  A real robot adapter would therefore
have lost every selection to the general adapter, and the failure would have
looked like a broken robot rather than a broken registry.  A stub finds that
in minutes; hardware finds it after weeks, in a lab, with motors attached.

The second thing it proves is more interesting than selection.  A robot has no
screen, so ``display`` genuinely is not available here — and the right answer
to *"make the screen brighter"* on a machine with no screen is to say so.  Not
to crash, not to pretend.  The vocabulary does not change; the honest answer
does.  That is what *the computer changed, CDOS didn't* has to mean in
practice, and it is testable without a single motor.

Enabled by setting ``CDOS_ROBOT`` in the environment, so it never takes over a
desktop by accident.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Tuple

from .api import (
    Audio, Bluetooth, Display, FileSystem, Hardware, HardwareDevice, Network,
    NetworkInterface, Platform, PlatformError, Process, ProcessHandle,
    ProcessInfo, System, User, WindowControl, WindowInfo,
)

#: Set this to let the robot adapter claim the machine.
MARKER = "CDOS_ROBOT"


def _absent(what: str) -> PlatformError:
    """The refusal a robot gives for something it does not have.

    Phrased as a fact about the machine rather than an error, because that is
    what it is.  A robot with no screen is not malfunctioning.
    """
    return PlatformError(f"this machine has no {what}")


class RobotSystem(System):
    """Power, for something that moves.

    Powering down a robot is not the same act as powering down a desktop: the
    motors have to stop first, or the machine falls over on the way out.  That
    ordering is a fact about robots and lives here, while the *intent* —
    "shut down" — is unchanged above the boundary.
    """

    def __init__(self, robot: "RobotPlatform"):
        self.robot = robot

    def available(self) -> bool:
        return True

    def reboot(self) -> None:
        self.robot.log("motors.stop")
        self.robot.log("power.cycle")

    def shutdown(self) -> None:
        self.robot.log("motors.stop")
        self.robot.log("power.off")

    def suspend(self) -> None:
        self.robot.log("motors.hold")

    def lock(self) -> None:
        raise _absent("session to lock")

    def logout(self) -> None:
        raise _absent("session to end")

    def get_hostname(self) -> str:
        return self.robot.unit

    def kernel(self) -> str:
        return "cdos-robot 0.1"

    def distro(self) -> str:
        return "CDOS Robot Edition"


class RobotProcess(Process):
    """Queued commands, not Unix processes.

    A robot controller runs a queue.  ``ProcessHandle`` exists so the shell can
    ask *did that survive?* without caring whether the answer came from a pid
    or from a queue slot — which is exactly the abstraction being tested here.
    """

    def __init__(self, robot: "RobotPlatform"):
        self.robot = robot

    def available(self) -> bool:
        return True

    def launch(self, argv: List[str], name: str = "", detached: bool = True,
               capture_stderr: bool = True) -> ProcessHandle:
        if not argv:
            raise PlatformError("empty command")
        return self.robot.enqueue(argv, name or " ".join(argv))

    def terminate(self, pid: int, signal: str = "TERM") -> bool:
        return self.robot.cancel(pid)

    def list_processes(self) -> List[ProcessInfo]:
        return [ProcessInfo(pid=task.pid, name=task.label, command=task.label,
                            state="queued", cpu_percent=None, memory_bytes=0)
                for task in self.robot.queue]

    def inspect(self, pid: int) -> Optional[ProcessInfo]:
        return next((p for p in self.list_processes() if p.pid == pid), None)

    def wait_for_exit(self, pid: int, timeout: Optional[float] = None) -> int:
        return 0


class RobotFileSystem(FileSystem):
    """A small in-memory store, so the stub needs no disk."""

    def __init__(self, robot: "RobotPlatform"):
        self.robot = robot
        self.files: Dict[str, bytes] = {"/etc/unit": b"stub\n"}

    def available(self) -> bool:
        return True

    def read(self, path: str) -> bytes:
        if path not in self.files:
            raise PlatformError(f"no such path: {path}")
        return self.files[path]

    def write(self, path: str, data: bytes, append: bool = False) -> bool:
        self.files[path] = (self.files.get(path, b"") + data) if append else data
        return True

    def list_directory(self, path: str) -> List[str]:
        prefix = path.rstrip("/") + "/"
        return sorted({name[len(prefix):].split("/")[0]
                       for name in self.files if name.startswith(prefix)})

    def create_directory(self, path: str, parents: bool = True) -> bool:
        return True

    def delete(self, path: str, recursive: bool = False) -> bool:
        return self.files.pop(path, None) is not None

    def exists(self, path: str) -> bool:
        return path in self.files

    def is_directory(self, path: str) -> bool:
        return any(name.startswith(path.rstrip("/") + "/") for name in self.files)


class RobotNetwork(Network):
    def __init__(self, robot: "RobotPlatform"):
        self.robot = robot

    def available(self) -> bool:
        return True

    def list_interfaces(self) -> List[NetworkInterface]:
        return [NetworkInterface(name="radio0", address="10.0.0.9", state="up",
                                 rx_bytes=0, tx_bytes=0)]

    def interface_status(self, name: str) -> Optional[NetworkInterface]:
        return next((i for i in self.list_interfaces() if i.name == name), None)

    def connect(self, ssid: str, password: str = "") -> bool:
        self.robot.log(f"radio.join {ssid}")
        return True

    def disconnect(self, name: str = "") -> bool:
        self.robot.log("radio.leave")
        return True

    # The robot's radio is a fixed link, not a wireless LAN it can browse:
    # wireless_state, scan, set_wireless and forget keep Network's refusals.


class RobotBluetooth(Bluetooth):
    """No short-range radio. Every method keeps the base class's refusal."""

    def available(self) -> bool:
        return False


class RobotHardware(Hardware):
    """Where a robot stops looking like a PC.

    ``hardware.enumerate`` is the same capability the desktop has, and it
    answers with servos and a gripper instead of a CPU and a disk.  The
    question did not change; the machine did.
    """

    def __init__(self, robot: "RobotPlatform"):
        self.robot = robot

    def available(self) -> bool:
        return True

    def enumerate(self) -> List[HardwareDevice]:
        return [
            HardwareDevice("Left drive", "motor", "idle", {"rpm": 0}),
            HardwareDevice("Right drive", "motor", "idle", {"rpm": 0}),
            HardwareDevice("Arm", "servo", "stowed", {"angle": 0}),
            HardwareDevice("Gripper", "servo", "open", {"grip": 0.0}),
            HardwareDevice("Front camera", "camera", "ready", {}),
            HardwareDevice("Range finder", "sensor", "ready", {"metres": 1.4}),
        ]

    def cpu_count(self) -> int:
        return 4

    def cpu_model(self) -> str:
        return "Robot controller"

    def memory_bytes(self) -> Tuple[int, int]:
        return (2 * 1024 ** 3, 1536 * 1024 ** 2)

    def temperature(self) -> Optional[float]:
        return 41.0

    def battery(self) -> Optional[Tuple[float, str]]:
        return (self.robot.charge, "discharging")

    # -- live readings --------------------------------------------------
    #
    # A robot answers the same questions from entirely different places: its
    # "processes" are queued commands, and its uptime is since the controller
    # came up. Same question, different machine.

    def cpu_load(self) -> float:
        return 0.12

    def cpu_load_per_core(self) -> List[float]:
        return [0.12, 0.08, 0.05, 0.19]

    def load_average(self) -> Tuple[float, float, float]:
        return (0.3, 0.2, 0.1)

    def swap_bytes(self) -> Tuple[int, int]:
        return (0, 0)                    # no swap on a controller

    def disk_bytes(self, path: str = "/") -> Tuple[int, int]:
        return (32 * 1024 ** 3, 24 * 1024 ** 3)

    def uptime(self) -> float:
        return max(time.time() - self.robot.booted, 0.0)

    def process_count(self) -> int:
        return len(self.robot.queue)

    def net_rate(self) -> Tuple[float, float]:
        return (0.0, 0.0)


class RobotAudio(Audio):
    """A robot speaks and listens; it has no volume slider."""

    def __init__(self, robot: "RobotPlatform"):
        self.robot = robot

    def available(self) -> bool:
        return True

    def speak(self, text: str, rate: float = 1.0) -> bool:
        self.robot.log(f"say {text!r}")
        return True

    def listen(self, timeout: float = 10.0) -> Optional[str]:
        raise _absent("microphone")

    def set_volume(self, level: float) -> bool:
        raise _absent("volume control")

    def get_volume(self) -> float:
        raise _absent("volume control")


class RobotDisplay(Display):
    """No screen. The interesting one.

    Every method refuses, and :meth:`available` says so up front, so the shell
    can tell the difference between *"that went wrong"* and *"this machine
    cannot do that at all"* — which are different sentences to a person.
    """

    def available(self) -> bool:
        return False

    def set_brightness(self, level: float) -> bool:
        raise _absent("screen")

    def get_brightness(self) -> float:
        raise _absent("screen")

    def set_resolution(self, width: int, height: int) -> bool:
        raise _absent("screen")

    def get_resolution(self) -> Tuple[int, int]:
        raise _absent("screen")


class RobotWindow(WindowControl):
    """No compositor, because no screen.

    Every method refuses. A robot is not a desktop that has lost its windows;
    it is a machine that never had the concept, and the honest reply to
    "switch to Firefox" on one is to say so.
    """

    def available(self) -> bool:
        return False

    def list_windows(self) -> List[WindowInfo]:
        return []

    def focus(self, handle: str) -> bool:
        raise _absent("windows")

    def close(self, handle: str) -> bool:
        raise _absent("windows")

    def fullscreen(self, handle: str, on: bool = True) -> bool:
        raise _absent("windows")

    def place(self, handle: str, x: int, y: int, w: int, h: int) -> bool:
        raise _absent("windows")

    def can_place(self) -> bool:
        return False


class RobotUser(User):
    """One operator, no login."""

    def __init__(self, robot: "RobotPlatform"):
        self.robot = robot

    def available(self) -> bool:
        return True

    def current_user(self) -> str:
        return "operator"

    def user_full_name(self, username: str) -> str:
        return self.robot.operator

    def user_home(self) -> str:
        return "/var/lib/cdos"

    def has_permission(self, action: str) -> bool:
        return True

    def list_sessions(self) -> List[str]:
        return ["console"]


class _Task(ProcessHandle):
    """A queued command, wearing the same interface a Unix process wears."""

    def __init__(self, pid: int, label: str):
        self.pid = pid
        self.label = label
        self.cancelled = False
        self.started = time.time()

    def poll(self) -> Optional[int]:
        return 1 if self.cancelled else None

    def wait(self, timeout: Optional[float] = None) -> Tuple[Optional[int], str]:
        return (1, "cancelled") if self.cancelled else (None, "")

    def release(self) -> None:
        return None


class RobotPlatform(Platform):
    """A robot with no hardware behind it."""

    name = "Robot"
    version = "0.1-stub"

    def __init__(self, unit: str = "stub-01", operator: str = "Operator"):
        self.unit = unit
        self.operator = operator
        self.charge = 0.82
        self.booted = time.time()
        self.queue: List[_Task] = []
        self.actions: List[str] = []
        self._next_pid = 1000

        self.system = RobotSystem(self)
        self.process = RobotProcess(self)
        self.filesystem = RobotFileSystem(self)
        self.network = RobotNetwork(self)
        self.hardware = RobotHardware(self)
        self.audio = RobotAudio(self)
        self.display = RobotDisplay()
        self.window = RobotWindow()
        self.user = RobotUser(self)
        self.bluetooth = RobotBluetooth()

    # ------------------------------------------------------------------
    def available(self) -> bool:
        """Only when asked for, so a desktop is never taken over by accident."""
        return bool(os.environ.get(MARKER))

    def initialize(self) -> bool:
        self.log("boot")
        return True

    def mechanisms(self) -> Dict[str, str]:
        """A robot serves the same questions from entirely different places.

        Nothing here is a Linux command, which is the point: the conformance
        report puts this beside the desktop's column, and every row differs
        while the answers above the boundary still line up.
        """
        return {
            "power": "motor-controller", "session": "none",
            "volume": "none", "speech": "onboard-tts",
            "brightness": "none", "resolution": "none",
            "network": "radio", "identity": "unit-id",
            "backlight": "none", "thermal": "controller-probe",
            "battery": "pack-monitor", "counters": "controller",
            "windows": "none", "init": "supervisor",
            "bluetooth": "none", "screenshot": "none",
        }

    # ------------------------------------------------------------------
    def log(self, action: str) -> None:
        """Record what would have been done, since nothing actually is."""
        self.actions.append(action)

    def enqueue(self, argv: List[str], label: str) -> _Task:
        self._next_pid += 1
        task = _Task(self._next_pid, label)
        self.queue.append(task)
        self.log(f"queue {label}")
        return task

    def cancel(self, pid: int) -> bool:
        for task in self.queue:
            if task.pid == pid:
                task.cancelled = True
                self.log(f"cancel {task.label}")
                return True
        return False
