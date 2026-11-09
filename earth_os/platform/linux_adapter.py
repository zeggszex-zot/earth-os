"""Linux Platform Adapter.

Reference implementation of the CDOS Platform API for Linux systems
(Debian, Ubuntu, Kali, Fedora, etc.).

This adapter abstracts away distribution-specific details. CDOS itself doesn't
care whether it's running on Debian or Kali; only this adapter cares.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .api import (
    AccessPoint, Audio, Display, FileSystem, Hardware, HardwareDevice, Network,
    NetworkInterface, Platform, PlatformError, Process, ProcessHandle,
    ProcessInfo, System, User, WindowControl, WindowInfo, WirelessState,
)
from .linux_radio import LinuxBluetooth, LinuxWireless


class LinuxProcessHandle(ProcessHandle):
    """A :class:`subprocess.Popen` behind the portable handle interface."""

    def __init__(self, popen: "subprocess.Popen", label: str):
        self._popen = popen
        self._released = False
        self.pid = popen.pid
        self.label = label

    def poll(self) -> Optional[int]:
        return self._popen.poll()

    def wait(self, timeout: Optional[float] = None) -> Tuple[Optional[int], str]:
        if self._popen.stderr is None:          # nothing captured to read
            try:
                self._popen.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return (None, "")
            return (self._popen.returncode, "")
        try:
            _out, stderr = self._popen.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return (None, "")          # still running: it worked
        text = (stderr or b"").decode("utf-8", "replace").strip()
        return (self._popen.returncode, text)

    def release(self) -> None:
        """Drain and discard anything still arriving on stderr.

        Draining on a thread rather than closing the pipe: closing it would
        hand the program a broken pipe on its next warning, and on Linux that
        is a signal that kills it by default.  Killing a program because the
        shell stopped listening to it would be an absurd way to lose work.
        The thread blocks on a read that ends when the program does.
        """
        if self._released:
            return
        self._released = True
        stream = self._popen.stderr
        if stream is None or stream.closed:
            return

        def drain() -> None:
            try:
                while stream.read(8192):
                    pass
            except (OSError, ValueError):
                pass
            finally:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

        threading.Thread(target=drain, daemon=True,
                         name=f"drain-{self.pid}").start()


class LinuxSystem(System):
    """System operations on Linux."""

    def available(self) -> bool:
        return True

    #: How long a command gets to fail before it is assumed to have worked.
    #:
    #: A power command that succeeds either returns zero immediately and takes
    #: the machine down behind it, or never returns at all.  A power command
    #: that fails does so at once — "Failed to connect to bus" costs
    #: milliseconds.  So a short wait separates the two cleanly, and the
    #: timeout is the *success* case rather than the failure one.
    GRACE = 1.5

    def reboot(self) -> None:
        """Reboot via systemd or fallback."""
        self._run_command(["systemctl", "reboot"], fallback=["sudo", "shutdown", "-r", "now"])

    def shutdown(self) -> None:
        """Power off via systemd or fallback."""
        self._run_command(["systemctl", "poweroff"], fallback=["sudo", "shutdown", "-h", "now"])

    def suspend(self) -> None:
        """Suspend to RAM via systemd."""
        self._run_command(["systemctl", "suspend"])

    def lock(self) -> None:
        """Lock the session using common lock commands.

        A screen locker like ``slock`` blocks until the screen is unlocked, so
        it is still running when the grace period ends — which is the success
        case.  The grace is kept short here because the shell is waiting on it
        with the screen already covered, where a reboot has nothing left to
        wait for.
        """
        self._try_each([["loginctl", "lock-session"], ["slock"], ["xlock"],
                        ["gnome-screensaver-command", "-l"]],
                       nothing="no lock command available", grace=0.4)

    def logout(self) -> None:
        """End the session, leaving the machine up."""
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
        if shutil.which("loginctl") and user:
            self._run_command(["loginctl", "terminate-user", user])
            return
        raise PlatformError("no logout command available")

    def get_hostname(self) -> str:
        """Get system hostname."""
        try:
            return subprocess.check_output(["hostname"], text=True,
                                           stderr=subprocess.DEVNULL).strip()
        except Exception:
            return "localhost"

    def kernel(self) -> str:
        """The running kernel version."""
        try:
            return Path("/proc/sys/kernel/osrelease").read_text().strip()
        except OSError:
            return "unknown"

    def distro(self) -> str:
        """What this environment calls itself.

        The only place a distribution name is read, and it is here — below the
        boundary — so that it can never become a branch. CDOS never asks *are
        we on Debian* to decide what to do; the adapter tries mechanisms in
        order. This exists to answer a person who asks, and nothing else.
        """
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return "unknown"

    def _try_each(self, chain: List[List[str]], nothing: str,
                  grace: Optional[float] = None) -> None:
        """Work down a list of commands until one actually succeeds.

        The distinction from a plain loop is the one this adapter keeps
        getting wrong.  Locking used to be written as::

            for cmd in candidates:
                if shutil.which(cmd[0]):
                    self._run_command(cmd)
                    return

        which moves on only when a binary is *missing*.  When ``loginctl``
        exists but fails — a container, a session with no bus, a machine
        where the user lacks permission — the error propagates out of the
        loop and ``slock`` is never tried, though it would have worked.

        This is the same defect the conformance harness found in
        ``_run_command`` itself, in a second place, which is a good argument
        for there being exactly one implementation of "try these in order".
        """
        problems: List[str] = []
        for argv in chain:
            if shutil.which(argv[0]) is None:
                problems.append(f"{argv[0]} is not installed")
                continue
            ok, detail = self._attempt(argv, grace)
            if ok:
                return
            problems.append(detail)
        raise PlatformError("; ".join(problems) if problems else nothing)

    def _run_command(self, argv: List[str], fallback: Optional[List[str]] = None,
                     grace: Optional[float] = None) -> None:
        """Run a command and find out whether it actually did anything.

        Starting a process successfully only means the binary existed.
        ``systemctl reboot`` on a machine that was not booted with systemd
        exits non-zero having done nothing at all, and the shell above has no
        way to tell that apart from a reboot in progress.  Reporting it as
        success is how a computer comes to answer "Acknowledged" to a power
        command that never happened — and it is also why the *fallback* below
        used to be unreachable, since it only ran when the binary was missing,
        never when the binary ran and failed.

        So each attempt is waited on and judged:

        * exits zero, or is still running when the grace expires — it worked,
          and in the reboot case the machine is on its way down;
        * exits non-zero — it failed, and says why.

        Every attempt that fails is collected, so the error names the whole
        chain rather than only the last thing tried.
        """
        attempts = [argv] + ([fallback] if fallback else [])
        problems: List[str] = []
        for attempt in attempts:
            ok, detail = self._attempt(attempt, grace)
            if ok:
                return
            problems.append(detail)
        raise PlatformError("; ".join(problems))

    def _attempt(self, argv: List[str],
                 grace: Optional[float] = None) -> Tuple[bool, str]:
        """One try at a command. Returns ``(worked, why_not)``."""
        window = self.GRACE if grace is None else grace
        try:
            process = subprocess.Popen(
                argv, start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except FileNotFoundError:
            return (False, f"{argv[0]} is not installed")
        except (OSError, ValueError) as exc:
            return (False, f"could not run {argv[0]}: {exc}")

        try:
            _out, stderr = process.communicate(timeout=window)
        except subprocess.TimeoutExpired:
            return (True, "")          # still going: taking effect
        if process.returncode == 0:
            return (True, "")

        detail = (stderr or b"").decode("utf-8", "replace").strip()
        detail = detail.splitlines()[-1] if detail else f"exit {process.returncode}"
        return (False, f"{argv[0]}: {detail}")


class LinuxProcess(Process):
    """Process management on Linux."""

    def available(self) -> bool:
        return True

    def launch(self, argv: List[str], name: str = "", detached: bool = True,
               capture_stderr: bool = True) -> LinuxProcessHandle:
        """Launch a program and return a handle on it.

        stderr is kept as a pipe by default rather than discarded, so a caller
        that wants to know *why* something died a second later can find out.
        """
        if not argv:
            raise PlatformError("empty command")
        try:
            process = subprocess.Popen(
                argv,
                start_new_session=detached,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE if capture_stderr else subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise PlatformError(f"not installed: {argv[0]}") from exc
        except (OSError, ValueError) as exc:
            raise PlatformError(f"launch failed: {exc}") from exc
        return LinuxProcessHandle(process, name or " ".join(argv))

    def terminate(self, pid: int, signal: str = "TERM") -> bool:
        """Send a named signal to a process.

        The name is translated here rather than by the caller: signal numbers
        are a Unix detail that the platform interface deliberately hides.
        """
        import signal as signals
        try:
            number = getattr(signals, f"SIG{signal.upper().removeprefix('SIG')}")
        except AttributeError:
            return False
        try:
            os.kill(pid, number)
            return True
        except (OSError, ProcessLookupError):
            return False

    def list_processes(self) -> List[ProcessInfo]:
        """List running processes using /proc."""
        processes = []
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    pid = int(entry)
                    info = self._read_process_info(pid)
                    if info:
                        processes.append(info)
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
        return processes

    def inspect(self, pid: int) -> Optional[ProcessInfo]:
        """Get info on a specific process."""
        return self._read_process_info(pid)

    def wait_for_exit(self, pid: int, timeout: Optional[float] = None) -> int:
        """Wait for a process to exit."""
        try:
            _, status = os.waitpid(pid, 0)
            return status
        except (OSError, ChildProcessError):
            return -1

    def _read_process_info(self, pid: int) -> Optional[ProcessInfo]:
        """Read process info from /proc/[pid]/stat and /proc/[pid]/status."""
        try:
            stat_path = Path(f"/proc/{pid}/stat")
            status_path = Path(f"/proc/{pid}/status")

            if not stat_path.exists():
                return None

            # Parse /proc/[pid]/stat
            stat_line = stat_path.read_text().split()
            name = stat_line[1].strip("()")
            state = stat_line[2]

            # Parse /proc/[pid]/status for memory
            memory_bytes = 0
            if status_path.exists():
                for line in status_path.read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        try:
                            memory_bytes = int(line.split()[1]) * 1024  # KB to bytes
                        except (ValueError, IndexError):
                            pass
                        break

            # Estimate CPU percent (simplified; real implementation needs history)
            cpu_percent = 0.0

            return ProcessInfo(
                pid=pid,
                name=name,
                command=self._read_command_line(pid),
                state=state,
                cpu_percent=cpu_percent,
                memory_bytes=memory_bytes,
            )
        except (OSError, ValueError, IndexError):
            return None

    def _read_command_line(self, pid: int) -> str:
        """Read the command line from /proc/[pid]/cmdline."""
        try:
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if cmdline_path.exists():
                content = cmdline_path.read_bytes()
                # cmdline is null-separated
                parts = content.split(b'\x00')
                return ' '.join(p.decode('utf-8', 'replace') for p in parts if p)
        except OSError:
            pass
        return ""


class LinuxFileSystem(FileSystem):
    """File operations on Linux."""

    def available(self) -> bool:
        return True

    def read(self, path: str) -> bytes:
        try:
            return Path(path).read_bytes()
        except OSError as exc:
            raise PlatformError(f"cannot read {path}: {exc}") from exc

    def write(self, path: str, data: bytes, append: bool = False) -> bool:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            if append:
                with p.open('ab') as f:
                    f.write(data)
            else:
                p.write_bytes(data)
            return True
        except OSError:
            return False

    def list_directory(self, path: str) -> List[str]:
        try:
            return [e.name for e in Path(path).iterdir()]
        except OSError:
            return []

    def create_directory(self, path: str, parents: bool = True) -> bool:
        try:
            Path(path).mkdir(parents=parents, exist_ok=True)
            return True
        except OSError:
            return False

    def delete(self, path: str, recursive: bool = False) -> bool:
        try:
            p = Path(path)
            if p.is_dir():
                if recursive:
                    import shutil as sh
                    sh.rmtree(p)
                else:
                    p.rmdir()
            else:
                p.unlink()
            return True
        except OSError:
            return False

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_directory(self, path: str) -> bool:
        return Path(path).is_dir()


class LinuxNetwork(Network):
    """Network operations on Linux."""

    def available(self) -> bool:
        return os.path.exists("/sys/class/net")

    def list_interfaces(self) -> List[NetworkInterface]:
        """List network interfaces from /sys/class/net."""
        interfaces = []
        try:
            net_path = Path("/sys/class/net")
            for iface_dir in net_path.iterdir():
                name = iface_dir.name
                # Check if interface is up
                operstate_file = iface_dir / "operstate"
                if operstate_file.exists():
                    state = operstate_file.read_text().strip()
                else:
                    state = "unknown"

                interfaces.append(NetworkInterface(
                    name=name,
                    address=self._get_ip_address(name),
                    state=state,
                    rx_bytes=self._read_counter(name, "statistics/rx_bytes"),
                    tx_bytes=self._read_counter(name, "statistics/tx_bytes"),
                ))
        except OSError:
            pass
        return interfaces

    def interface_status(self, name: str) -> Optional[NetworkInterface]:
        """Get status of a specific interface."""
        for iface in self.list_interfaces():
            if iface.name == name:
                return iface
        return None

    # -- wireless, through NetworkManager (platform/linux_radio.py) ----------
    @property
    def wireless(self) -> LinuxWireless:
        if getattr(self, "_wireless", None) is None:
            self._wireless = LinuxWireless(self._get_ip_address)
        return self._wireless

    def connect(self, ssid: str, password: str = "") -> bool:
        """Join a wireless network.

        Used to return False for every failure, so a wrong password and a
        network out of range were the same answer. Raises with the reason now.
        """
        return self.wireless.connect(ssid, password)

    def disconnect(self, name: str = "") -> bool:
        """Leave a network; with no name, the wireless network joined now.

        This used to switch the whole radio off when given no name, which is
        a different act with its own capability now (network.wifi_off).
        """
        return self.wireless.disconnect(name)

    def wireless_state(self) -> WirelessState:
        return self.wireless.state()

    def scan(self, rescan: bool = True) -> List[AccessPoint]:
        return self.wireless.scan(rescan)

    def set_wireless(self, on: bool) -> bool:
        return self.wireless.set_enabled(on)

    def forget(self, ssid: str) -> bool:
        return self.wireless.forget(ssid)

    def _get_ip_address(self, interface: str) -> str:
        """Get IP address for an interface."""
        try:
            output = subprocess.check_output(
                ["ip", "addr", "show", interface],
                text=True, stderr=subprocess.DEVNULL
            )
            for line in output.splitlines():
                if "inet " in line:
                    parts = line.split()
                    return parts[1].split("/")[0]
        except (OSError, subprocess.CalledProcessError):
            pass
        return ""

    def _read_counter(self, interface: str, counter_file: str) -> int:
        """Read a counter from /sys/class/net."""
        try:
            path = Path(f"/sys/class/net/{interface}/{counter_file}")
            return int(path.read_text().strip())
        except (OSError, ValueError):
            return 0


class LinuxHardware(Hardware):
    """Hardware operations on Linux."""

    def __init__(self):
        #: Counter sampling, which is stateful and stays below the boundary.
        self.live = LinuxLiveReadings()

    def available(self) -> bool:
        return True

    def enumerate(self) -> List[HardwareDevice]:
        """Enumerate hardware devices."""
        devices = []
        # CPU
        cpu_model = self.cpu_model()
        devices.append(HardwareDevice(
            name="CPU",
            device_type="cpu",
            status="active",
            data={"cores": self.cpu_count(), "model": cpu_model}
        ))
        # Memory
        total, available = self.memory_bytes()
        devices.append(HardwareDevice(
            name="Memory",
            device_type="memory",
            status="active",
            data={"total": total, "available": available}
        ))
        return devices

    def cpu_count(self) -> int:
        try:
            return len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            return os.cpu_count() or 1

    def cpu_model(self) -> str:
        """Get CPU model from /proc/cpuinfo."""
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
        return "Unknown"

    def memory_bytes(self) -> Tuple[int, int]:
        """Get memory info from /proc/meminfo."""
        try:
            with open("/proc/meminfo") as f:
                meminfo = {}
                for line in f:
                    key, value = line.split(":")
                    meminfo[key.strip()] = int(value.split()[0]) * 1024  # KB to bytes
                total = meminfo.get("MemTotal", 0)
                available = meminfo.get("MemAvailable", 0)
                return (total, available)
        except OSError:
            return (0, 0)

    def temperature(self) -> Optional[float]:
        """Get system temperature from /sys/class/thermal."""
        try:
            for zone_dir in Path("/sys/class/thermal").glob("thermal_zone*"):
                temp_file = zone_dir / "temp"
                if temp_file.exists():
                    # Temperature is in millidegrees Celsius
                    temp_m = int(temp_file.read_text().strip())
                    return temp_m / 1000.0
        except (OSError, ValueError):
            pass
        return None

    def battery(self) -> Optional[Tuple[float, str]]:
        """Get battery status from /sys/class/power_supply."""
        try:
            power_supply_path = Path("/sys/class/power_supply")
            for device_dir in power_supply_path.glob("BAT*"):
                capacity_file = device_dir / "capacity"
                status_file = device_dir / "status"
                if capacity_file.exists() and status_file.exists():
                    percentage = int(capacity_file.read_text().strip()) / 100.0
                    status = status_file.read_text().strip().lower()
                    return (percentage, status)
        except (OSError, ValueError):
            pass
        return None


    # -- live readings --------------------------------------------------
    #
    # Moved down from ``sysinfo.SystemMonitor``, which read the same files.
    # The shell now asks for these instead of taking its own reading, so
    # there is one answer to "how hot is it" rather than two that drift.

    def cpu_load(self) -> float:
        return self.live.cpu_load

    def cpu_load_per_core(self) -> List[float]:
        return self.live.per_core

    def load_average(self) -> Tuple[float, float, float]:
        try:
            return os.getloadavg()
        except OSError:
            return (0.0, 0.0, 0.0)

    def swap_bytes(self) -> Tuple[int, int]:
        info = self._meminfo()
        return (info.get("SwapTotal", 0), info.get("SwapFree", 0))

    def disk_bytes(self, path: str = "/") -> Tuple[int, int]:
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            raise PlatformError(f"cannot read {path}: {exc}") from exc
        return (usage.total, usage.free)

    def uptime(self) -> float:
        try:
            return float(Path("/proc/uptime").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return 0.0

    def process_count(self) -> int:
        try:
            return sum(1 for entry in Path("/proc").iterdir()
                       if entry.name.isdigit())
        except OSError:
            return 0

    def net_rate(self) -> Tuple[float, float]:
        return self.live.net_rate

    def _meminfo(self) -> Dict[str, int]:
        """``/proc/meminfo`` as bytes. One parser, used by memory and swap."""
        info: Dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, _, rest = line.partition(":")
                try:
                    info[key] = int(rest.split()[0]) * 1024
                except (ValueError, IndexError):
                    continue
        except OSError:
            pass
        return info


class _Sample:
    """One reading of a monotonic kernel counter, with when it was taken."""

    __slots__ = ("idle", "total", "when", "rx", "tx")

    def __init__(self, idle=0.0, total=0.0, when=0.0, rx=0, tx=0):
        self.idle, self.total, self.when, self.rx, self.tx = idle, total, when, rx, tx


class LinuxLiveReadings:
    """The sampling half of :class:`LinuxHardware`.

    ``/proc/stat`` and ``/proc/net/dev`` hold monotonic totals, not rates, so
    a load figure only exists as a difference between two readings.  That
    makes these stateful in a way the rest of the adapter is not, and the
    state is kept here rather than above the boundary because jiffies are a
    Linux detail — a robot's controller reports load some other way entirely,
    and the shell should never learn the difference.

    The shell used to do this sampling itself, in ``sysinfo.SystemMonitor``,
    against the same two files this reads.
    """

    def __init__(self):
        self._cpu = _Sample()
        self._cores: Dict[int, _Sample] = {}
        self._per_core: List[float] = []
        self._cpu_load = 0.0
        self._net = _Sample()
        self._net_rate: Tuple[float, float] = (0.0, 0.0)

    # ------------------------------------------------------------------
    def sample_cpu(self) -> None:
        try:
            lines = Path("/proc/stat").read_text().splitlines()
        except OSError:
            return
        for line in lines:
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            label = parts[0]
            try:
                values = [float(v) for v in parts[1:]]
            except ValueError:
                continue
            if len(values) < 4:
                continue
            idle = values[3] + (values[4] if len(values) > 4 else 0.0)
            total = sum(values)
            if label == "cpu":
                previous = self._cpu
                span = total - previous.total
                if span > 0:
                    self._cpu_load = max(0.0, min(
                        1.0, 1.0 - (idle - previous.idle) / span))
                self._cpu = _Sample(idle, total)
            else:
                try:
                    index = int(label[3:])
                except ValueError:
                    continue
                previous = self._cores.get(index, _Sample())
                span = total - previous.total
                usage = (max(0.0, min(1.0, 1.0 - (idle - previous.idle) / span))
                         if span > 0 else 0.0)
                while len(self._per_core) <= index:
                    self._per_core.append(0.0)
                self._per_core[index] = usage
                self._cores[index] = _Sample(idle, total)

    def sample_net(self) -> None:
        rx = tx = 0
        try:
            for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
                name, _, rest = line.partition(":")
                if name.strip() == "lo":
                    continue                # loopback is not traffic
                fields = rest.split()
                if len(fields) >= 9:
                    rx += int(fields[0])
                    tx += int(fields[8])
        except (OSError, ValueError):
            return
        now = time.monotonic()
        if self._net.when and now > self._net.when:
            span = now - self._net.when
            self._net_rate = ((rx - self._net.rx) / span,
                              (tx - self._net.tx) / span)
        self._net = _Sample(when=now, rx=rx, tx=tx)

    # ------------------------------------------------------------------
    @property
    def cpu_load(self) -> float:
        self.sample_cpu()
        return self._cpu_load

    @property
    def per_core(self) -> List[float]:
        self.sample_cpu()
        return list(self._per_core)

    @property
    def net_rate(self) -> Tuple[float, float]:
        self.sample_net()
        return self._net_rate


class LinuxAudio(Audio):
    """Audio operations on Linux."""

    def available(self) -> bool:
        # PulseAudio, PipeWire or bare ALSA
        return any(shutil.which(t) for t in ("pactl", "wpctl", "amixer"))

    def speak(self, text: str, rate: float = 1.0) -> bool:
        """Synthesize speech using espeak or festival."""
        for cmd in [["espeak", text], ["festival", "--tts"]]:
            if shutil.which(cmd[0]):
                try:
                    subprocess.run(cmd, input=text.encode(), check=True, capture_output=True)
                    return True
                except subprocess.CalledProcessError:
                    continue
        return False

    def listen(self, timeout: float = 10.0) -> Optional[str]:
        """Capture speech and recognise it.

        Not wired up here yet: recognition lives in the voice stack, which
        still sits above the boundary and is the next thing to move down.

        It *refuses* rather than returning ``None``. Returning None made the
        capability layer report ``ok`` with an empty answer — indistinguishable
        from having listened carefully and heard silence. That is the same
        fabricated success the platform audit found in four other places, and
        a conformance run duly recorded this one as working.
        """
        raise PlatformError(
            "speech recognition is not wired into the platform yet")

    #: Output volume, in the order each is tried. pactl speaks to PulseAudio
    #: and to PipeWire's pulse shim; wpctl is PipeWire's own tool, there on
    #: machines that never installed the shim; amixer is bare ALSA.
    def _volume_tools(self) -> List[str]:
        return [t for t in ("pactl", "wpctl", "amixer") if shutil.which(t)]

    def set_volume(self, level: float) -> bool:
        """Set the output volume, 0.0-1.0.

        Raises rather than returning False: False came back from the
        capability layer as ``ok`` with a value of False, which every caller
        read as the volume having changed.
        """
        level = max(0.0, min(1.0, float(level)))
        percent = int(round(level * 100))
        commands = {
            "pactl": ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"],
            "wpctl": ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level:.2f}"],
            "amixer": ["amixer", "-q", "set", "Master", f"{percent}%"],
        }
        tools = self._volume_tools()
        if not tools:
            raise PlatformError("no volume control: install pulseaudio-utils "
                                "(pactl), wireplumber (wpctl) or alsa-utils")
        problems = []
        for tool in tools:
            try:
                subprocess.run(commands[tool], check=True, capture_output=True,
                               timeout=3)
                return True
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
                problems.append(f"{tool}: {detail.splitlines()[-1] if detail else exc.returncode}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                problems.append(f"{tool}: {exc}")
        raise PlatformError("; ".join(problems))

    def get_volume(self) -> float:
        """The output volume, 0.0-1.0.

        Raises when it cannot be read. This used to return 0.5 on any
        failure — a plausible number for a reading that was never taken, and
        the amixer branch could never succeed at all, since amixer brackets
        its percentages and the parser wanted them bare.
        """
        import re
        commands = {
            "pactl": ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
            "wpctl": ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
            "amixer": ["amixer", "get", "Master"],
        }
        tools = self._volume_tools()
        if not tools:
            raise PlatformError("no volume control: install pulseaudio-utils "
                                "(pactl), wireplumber (wpctl) or alsa-utils")
        problems = []
        for tool in tools:
            try:
                output = subprocess.check_output(
                    commands[tool], text=True, stderr=subprocess.PIPE,
                    timeout=3)
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or "").strip()
                problems.append(f"{tool}: {detail.splitlines()[-1] if detail else exc.returncode}")
                continue
            except (OSError, subprocess.TimeoutExpired) as exc:
                problems.append(f"{tool}: {exc}")
                continue
            if tool == "wpctl":
                # "Volume: 0.40" — a fraction, possibly followed by [MUTED].
                match = re.search(r"Volume:\s*([0-9.]+)", output)
                if match:
                    return max(0.0, min(1.0, float(match.group(1))))
            else:
                # pactl: "... / 40% / ..."; amixer: "... [40%] ...".
                match = re.search(r"(\d+)%", output)
                if match:
                    return max(0.0, min(1.0, int(match.group(1)) / 100.0))
            problems.append(f"{tool}: no volume in its output")
        raise PlatformError("; ".join(problems))

    def play_sound(self, path: str) -> bool:
        """Play an audio file."""
        for cmd in [["paplay"], ["aplay"], ["ffplay", "-nodisp", "-autoexit"]]:
            if shutil.which(cmd[0]):
                try:
                    subprocess.run([*cmd, path], check=True, capture_output=True)
                    return True
                except subprocess.CalledProcessError:
                    continue
        return False


class LinuxDisplay(Display):
    """Display operations on Linux."""

    def available(self) -> bool:
        return True

    def set_brightness(self, level: float) -> bool:
        """Set screen brightness.

        Writing ``/sys/class/backlight/*/brightness`` directly needs root, so
        on an ordinary desktop session that path always fails.  The helpers
        exist precisely to bridge that gap, so they are tried first and the
        raw write is kept as the last resort rather than the only attempt.
        """
        level = max(0.0, min(1.0, float(level)))
        percent = int(round(level * 100))
        for argv in (["brightnessctl", "--quiet", "set", f"{percent}%"],
                     ["light", "-S", str(percent)],
                     ["xbacklight", "-set", str(percent)]):
            if shutil.which(argv[0]) is None:
                continue
            try:
                completed = subprocess.run(argv, capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                continue
            if completed.returncode == 0:
                return True

        for brightness_file in Path("/sys/class/backlight").glob("*/brightness"):
            try:
                ceiling = int((brightness_file.parent / "max_brightness").read_text())
                if ceiling <= 0:
                    continue
                brightness_file.write_text(str(int(ceiling * level)))
                return True
            except (OSError, ValueError):
                continue
        return False

    def get_brightness(self) -> float:
        """Get current screen brightness, as a fraction.

        Raises rather than inventing a number: a caller that is told 0.5 when
        nothing could be read has no way to know it was guessed at, and the
        capability layer turns the exception into an honest failure anyway.
        """
        for brightness_file in Path("/sys/class/backlight").glob("*/brightness"):
            try:
                ceiling = int((brightness_file.parent / "max_brightness").read_text())
                if ceiling <= 0:
                    continue
                return int(brightness_file.read_text()) / ceiling
            except (OSError, ValueError, ZeroDivisionError):
                continue
        raise PlatformError("no backlight device to read brightness from")

    #: Whole-screen capture tools, by the kind of session they can see.
    #: A Wayland compositor shows nothing to an X11 tool — `import` on
    #: XWayland returns a black rectangle and exit status 0 — so the two
    #: chains are kept apart rather than tried in one list.
    SCREENSHOT_WAYLAND: Tuple[Tuple[str, ...], ...] = (
        ("grim", "{path}"),
    )
    SCREENSHOT_X11: Tuple[Tuple[str, ...], ...] = (
        ("maim", "{path}"),
        ("scrot", "{path}"),
        ("import", "-window", "root", "{path}"),
    )
    #: Tools that belong to one desktop, and only work quietly inside it.
    #: Outside GNOME, gnome-screenshot cannot reach GNOME Shell and opens its
    #: own interactive window instead of writing the file — which the shell
    #: then framed as a program the person had opened, and pulled them back
    #: to the main screen to show it. So each is offered only on its own
    #: desktop, as reported by XDG_CURRENT_DESKTOP.
    SCREENSHOT_DESKTOP: Dict[str, Tuple[str, ...]] = {
        "GNOME": ("gnome-screenshot", "-f", "{path}"),
        "KDE": ("spectacle", "-b", "-n", "-f", "-o", "{path}"),
    }

    def screenshot_chain(self) -> Tuple[Tuple[str, ...], ...]:
        desktops = os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")
        native = tuple(tool for name, tool in self.SCREENSHOT_DESKTOP.items()
                       if name in desktops)
        if os.environ.get("WAYLAND_DISPLAY"):
            return self.SCREENSHOT_WAYLAND + native
        if os.environ.get("DISPLAY"):
            return self.SCREENSHOT_X11 + native
        return ()

    def screenshot(self, path: str = "") -> str:
        """Capture the whole screen with whatever this session can use.

        Every tool is tried in turn and a failure moves on to the next — the
        fallback has to fire when a tool is present but refuses (grim under
        GNOME, which lacks the protocol it needs), not only when it is
        missing. Success is a non-empty file, not an exit status.
        """
        if not path:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = str(Path.home() / "Pictures" / f"Screenshot-{stamp}.png")
        target = Path(path).expanduser()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PlatformError(f"cannot write to {target.parent}: {exc}")

        chain = self.screenshot_chain()
        if not chain:
            raise PlatformError("this machine has no display session to capture")
        tried: List[str] = []
        for template in chain:
            tool = template[0]
            if shutil.which(tool) is None:
                tried.append(f"{tool} (not installed)")
                continue
            argv = [part.replace("{path}", str(target)) for part in template]
            try:
                target.unlink(missing_ok=True)
                completed = subprocess.run(argv, capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError) as exc:
                tried.append(f"{tool} ({type(exc).__name__})")
                continue
            if completed.returncode == 0 and target.exists() \
                    and target.stat().st_size > 0:
                return str(target)
            reason = (completed.stderr or b"").decode("utf-8", "replace").strip()
            tried.append(f"{tool} ({reason.splitlines()[-1] if reason else 'failed'})")
        raise PlatformError("could not capture the screen: " + "; ".join(tried))

    def set_resolution(self, width: int, height: int) -> bool:
        """Set screen resolution using xrandr."""
        if not shutil.which("xrandr"):
            return False
        try:
            subprocess.run(
                ["xrandr", "-s", f"{width}x{height}"],
                check=True, capture_output=True
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def get_resolution(self) -> Tuple[int, int]:
        """Get the current screen resolution.

        The kernel is asked before any display server is, because
        ``/sys/class/graphics/fb0/virtual_size`` is right on X, on Wayland and
        on a bare console alike, while ``xrandr`` needs an X server and an
        ``$DISPLAY`` the shell may not have.

        Nothing is invented if all of them fail.  This used to end in a bare
        ``return (1024, 768)``, which meant a caller could not tell a real
        reading from a placeholder — and since the ``xrandr`` invocation
        underneath it passed a flag xrandr does not accept, the placeholder
        was what every machine actually got.
        """
        try:
            raw = Path("/sys/class/graphics/fb0/virtual_size").read_text()
            width, height = (int(n) for n in raw.strip().split(",", 1))
            if width > 0 and height > 0:
                return (width, height)
        except (OSError, ValueError):
            pass

        for modes in sorted(Path("/sys/class/drm").glob("*/modes")):
            try:
                first = modes.read_text().split("\n", 1)[0].strip()
                width, height = (int(n) for n in first.split("x", 1))
                if width > 0 and height > 0:
                    return (width, height)
            except (OSError, ValueError):
                continue

        if shutil.which("xrandr"):
            try:
                output = subprocess.check_output(
                    ["xrandr", "--current"], text=True, stderr=subprocess.DEVNULL,
                    timeout=5)
                for line in output.splitlines():
                    if " connected" not in line:
                        continue
                    for part in line.split():
                        if "x" in part and "+" in part:
                            width, height = (
                                int(n) for n in part.split("+", 1)[0].split("x", 1))
                            return (width, height)
            except (OSError, subprocess.SubprocessError, ValueError):
                pass

        raise PlatformError("could not determine screen resolution")


class LinuxWindowControl(WindowControl):
    """Windows, through whichever compositor protocol this session offers.

    A thin cover over :mod:`earth_os.platform.linux_windows`, which already
    picks between sway IPC, wlroots, wmctrl and nothing at all.  That
    selection was written long before this boundary existed and needed no
    changing to sit behind it — which is the whole argument for mounting a
    working subsystem rather than rewriting one.

    Handles cross the boundary as opaque strings.  A sway ``con_id`` and an
    X11 window id mean different things, and neither is the shell's business.
    """

    def __init__(self, preferred: str = "auto"):
        self._preferred = preferred
        self._backend = None

    @property
    def backend(self):
        if self._backend is None:
            from .linux_windows import detect
            self._backend = detect(self._preferred)
        return self._backend

    def available(self) -> bool:
        return bool(self.backend.available)

    def _find(self, handle: str):
        for window in self.backend.list_windows():
            if window.handle == handle:
                return window
        raise PlatformError(f"no such window: {handle}")

    def list_windows(self) -> List[WindowInfo]:
        return [WindowInfo(handle=w.handle, title=w.title, app=w.app,
                           focused=bool(getattr(w, "focused", False)))
                for w in self.backend.list_windows()]

    def focus(self, handle: str) -> bool:
        return bool(self.backend.focus(self._find(handle)))

    def close(self, handle: str) -> bool:
        return bool(self.backend.close(self._find(handle)))

    def fullscreen(self, handle: str, on: bool = True) -> bool:
        return bool(self.backend.fullscreen(self._find(handle), on))

    def place(self, handle: str, x: int, y: int, w: int, h: int) -> bool:
        if not self.backend.can_place:
            raise PlatformError(
                f"{self.backend.name} cannot place windows on this session")
        return bool(self.backend.place(self._find(handle), x, y, w, h))

    def can_place(self) -> bool:
        return bool(self.backend.can_place)


class LinuxUser(User):
    """User operations on Linux."""

    def available(self) -> bool:
        return True

    def current_user(self) -> str:
        try:
            import getpass
            return getpass.getuser()
        except Exception:
            return os.environ.get("USER", os.environ.get("USERNAME", "unknown"))

    def user_full_name(self, username: str) -> str:
        """The account's real name, falling back to the login name.

        An empty GECOS field is common and means "nobody filled this in",
        not "this person has no name", so it falls back too.
        """
        try:
            import pwd
            full = pwd.getpwnam(username).pw_gecos.split(",")[0].strip()
            return full or username
        except Exception:
            return username

    def user_home(self) -> str:
        return str(Path.home())

    def has_permission(self, action: str) -> bool:
        """Whether the current user may do something.

        On Linux this is mostly not knowable in advance, and pretending
        otherwise is worse than admitting it. Powering the machine down is
        authorised by polkit at the moment of the call, against the session
        making it: an ordinary desktop user with no sudo rights at all is
        normally allowed to, and a ``sudo -n`` probe would report that they
        are not. Refusing on the strength of that guess would break the
        common case in order to guard against the rare one.

        So the answer here is optimistic, and the attempt is the real test —
        a denial comes back from the call itself, where it is accurate.
        Platforms that *can* answer cheaply and correctly — an embedded board
        with a fixed policy, a robot with a physical key switch — should
        answer properly rather than inherit this.
        """
        return True

    def list_sessions(self) -> List[str]:
        """List active sessions using loginctl."""
        try:
            output = subprocess.check_output(["loginctl", "list-sessions"], text=True,
                                             stderr=subprocess.DEVNULL)
            sessions = []
            for line in output.splitlines()[1:]:  # skip header
                parts = line.split()
                if parts:
                    sessions.append(parts[0])
            return sessions
        except (OSError, subprocess.CalledProcessError):
            return []


class LinuxPlatform(Platform):
    """Complete Linux platform implementation."""

    name = "Linux"
    version = "1.0"

    def __init__(self):
        self.system = LinuxSystem()
        self.process = LinuxProcess()
        self.filesystem = LinuxFileSystem()
        self.network = LinuxNetwork()
        self.hardware = LinuxHardware()
        self.audio = LinuxAudio()
        self.display = LinuxDisplay()
        self.window = LinuxWindowControl()
        self.user = LinuxUser()
        self.bluetooth = LinuxBluetooth()

    #: The order each area is tried in, mirroring what the methods above do.
    #:
    #: Kept beside the methods rather than derived from them, which is a
    #: deliberate small duplication: a generated list would silently agree
    #: with whatever the code does, including when the code is wrong, and the
    #: point of a conformance report is to be checkable against the source by
    #: a person.
    CHAINS: Dict[str, Tuple[str, ...]] = {
        "power": ("systemctl", "shutdown"),
        "session": ("loginctl", "slock", "xlock",
                    "gnome-screensaver-command"),
        "volume": ("pactl", "wpctl", "amixer"),
        "speech": ("espeak", "festival"),
        "brightness": ("brightnessctl", "light", "xbacklight"),
        "resolution": ("xrandr",),
        "network": ("nmcli",),
        "identity": ("hostname",),
        "bluetooth": ("bluetoothctl",),
    }

    def mechanisms(self) -> Dict[str, str]:
        """Which mechanism would serve each area on *this* machine.

        This is where two Linux distributions visibly stop being the same
        computer: one has ``brightnessctl``, another only sysfs; one runs
        sway, another X11 with ``wmctrl``; a container has no ``systemctl``
        that works at all. The capability answers should match anyway, and
        that gap between *same answer* and *different mechanism* is the whole
        claim.
        """
        found: Dict[str, str] = {}
        for area, chain in self.CHAINS.items():
            found[area] = next(
                (name for name in chain if shutil.which(name)), "none")
        # Kernel interfaces are not on PATH, so they are looked for directly.
        found["backlight"] = ("sysfs" if any(Path("/sys/class/backlight").glob("*"))
                              else "none")
        found["thermal"] = ("sysfs" if any(Path("/sys/class/thermal").glob("thermal_zone*"))
                            else "none")
        found["battery"] = ("sysfs" if any(Path("/sys/class/power_supply").glob("BAT*"))
                            else "none")
        found["counters"] = "procfs" if Path("/proc/stat").exists() else "none"
        found["screenshot"] = next(
            (template[0] for template in self.display.screenshot_chain()
             if shutil.which(template[0])), "none")
        found["windows"] = self.window.backend.name
        found["init"] = "systemd" if Path("/run/systemd/system").is_dir() else "other"
        return found

    def available(self) -> bool:
        """Linux adapter is available on Linux systems."""
        import platform as plat
        return plat.system() == "Linux"

    def initialize(self) -> bool:
        """Initialize the Linux platform."""
        # Nothing special needed for now
        return True
