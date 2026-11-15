# CDOS Platform Abstraction Layer

## Overview

The Platform Abstraction Layer divorces CDOS from Linux-specific implementation details by defining **capabilities** that any platform must provide, and letting adapters implement those capabilities.

Instead of CDOS knowing "how to reboot", it knows "I need `system.power_control`" and asks the platform adapter how to achieve that for **this** environment.

This is the foundation for CDOS running on:
- Multiple Linux distributions (Debian, Kali, Ubuntu, Fedora, etc.)
- Non-Linux platforms (embedded, robot, Windows via WSL)
- Future platforms not yet imagined

## Architecture

```
                    CDOS (intent-based)
                           │
                           ↓
                   Platform Adapter (capability-based)
                           │
                ┌──────────┼──────────┐
                │          │          │
            Linux       Robot    Embedded
              │
        ┌─────┼─────┐
      Debian Ubuntu Kali
```

## Key Concepts

### 1. **Capabilities** (API Contracts)

Each capability defines what CDOS can ask of the platform, independent of implementation.

| Capability | Purpose | Examples |
|------------|---------|----------|
| `System` | Power, reboot, shutdown | `reboot()`, `shutdown()`, `suspend()` |
| `Process` | Program launching and control | `launch()`, `terminate()`, `list_processes()` |
| `FileSystem` | File and directory operations | `read()`, `write()`, `list_directory()` |
| `Network` | Interfaces and wireless networks | `list_interfaces()`, `scan()`, `connect()`, `forget()` |
| `Hardware` | Hardware enumeration and sensors | `cpu_count()`, `memory_bytes()`, `temperature()` |
| `Audio` | Speech and sound | `speak()`, `listen()`, `set_volume()` |
| `Display` | Screen operations | `set_brightness()`, `set_resolution()`, `screenshot()` |
| `Bluetooth` | Short-range devices | `state()`, `scan()`, `connect()`, `forget()` |
| `User` | User identity and permissions | `current_user()`, `user_home()`, `has_permission()` |

### 2. **Adapters** (Implementations)

An adapter implements all capabilities for a specific platform. The Linux adapter handles distribution-specific details.

```python
from earth_os.platform import get_platform

# Get the platform adapter for this system
platform = get_platform()

# Use capabilities through the adapter
platform.system.reboot()
platform.process.launch(["firefox"])
platform.user.current_user()
```

### 3. **Platform Independence**

The corpus describes **intent**, not implementation:

```text
USER:
"reboot the computer"

        ↓

CORPUS:
reboot
→ requires(system.power_control)

        ↓

CDOS:
CAPABILITY = system.reboot
TARGET = current_platform

        ↓

PLATFORM ADAPTER:
How does THIS environment perform system.reboot?

┌─ Debian: systemctl reboot
├─ Kali:   systemctl reboot
├─ Ubuntu: systemctl reboot
└─ Robot:  send_command("stop motors, power off")

        ↓

RESULT
```

## How It Works

### Selecting a Platform

When CDOS starts, the registry tries adapters in order until one reports it's available:

```python
from earth_os.platform import get_platform

# Try each adapter in order
# 1. LinuxPlatform.available() → True on Linux → initialize and return
# 2. RobotPlatform.available() → Would be False on desktop
# 3. etc.

platform = get_platform()  # Returns the right adapter
```

### Using Capabilities

Once you have a platform, use capabilities through its interface:

```python
platform = get_platform()

# System operations
platform.system.reboot()
platform.system.shutdown()

# Launch programs
pid = platform.process.launch(["firefox"])
platform.process.terminate(pid)

# File operations
platform.filesystem.write("/tmp/test.txt", b"data")
data = platform.filesystem.read("/tmp/test.txt")

# User info
username = platform.user.current_user()
home = platform.user.user_home()
```

### Error Handling

All capabilities raise `PlatformError` when something goes wrong:

```python
from earth_os.platform import PlatformError

try:
    platform.process.launch(["/nonexistent/command"])
except PlatformError as exc:
    print(f"Failed: {exc}")
```

## The Linux Adapter

The Linux adapter is the reference implementation. It handles:

### System Operations
- Reboot via `systemctl reboot` (fallback: `sudo shutdown -r now`)
- Shutdown via `systemctl poweroff` (fallback: `sudo shutdown -h now`)
- Suspend via `systemctl suspend`
- Lock via `loginctl lock-session` (fallback: `slock`, `xlock`)

### Process Management
- Launches via `subprocess.Popen` with separate session (detached)
- Lists processes via `/proc/[pid]`
- Reads process info from `/proc/[pid]/stat` and `/proc/[pid]/status`
- Waits for processes via `os.waitpid()`

### File Operations
- Uses `pathlib.Path` for all file operations
- Atomic writes with temporary files
- Recursive directory creation with parents

### Network
- Lists interfaces from `/sys/class/net`
- Gets IP addresses via `ip addr show`
- Scans, joins, leaves and forgets wireless networks via `nmcli`
  (`platform/linux_radio.py`); a failure raises with NetworkManager's reason,
  so a wrong password and an out-of-range network are different answers
- The wireless methods are not abstract: a platform with no radio inherits a
  refusal (`wireless is not supported on this platform`) rather than having to
  invent an empty answer

### Bluetooth
- Through `bluetoothctl` (`platform/linux_radio.py`); no adapter in
  `/sys/class/bluetooth` is reported at once, without waiting on bluetoothd
- `connect()` pairs and trusts first when needed, in one live `bluetoothctl`
  session with a *NoInputNoOutput* agent, so pairing needs no dialog
- Like the wireless half, every method refuses by default on other platforms

### Display — screenshots
- `screenshot(path)` tries the tools a session can actually use, in order:
  Wayland `grim`, `gnome-screenshot`, `spectacle`; X11 `maim`, `scrot`,
  `import`, `gnome-screenshot`, `spectacle`. The chains are separate because
  an X11 tool under Wayland returns a black image and exit status 0.
- Success is a non-empty file, not an exit status

### Hardware
- CPU info from `/proc/cpuinfo`
- Memory from `/proc/meminfo`
- Temperature from `/sys/class/thermal`
- Battery status from `/sys/class/power_supply`

### User
- Current user via `getpass.getuser()`
- Full name from `/etc/passwd` (pwd database)
- Home directory via `pathlib.Path.home()`
- Sessions via `loginctl list-sessions`

## Future Adapters

Additional adapters follow the same pattern:

### Robot Adapter

```python
class RobotPlatform(Platform):
    def available(self) -> bool:
        return os.path.exists("/dev/robot")
    
    def initialize(self) -> bool:
        # Connect to robot hardware
        return True
```

Would provide:

```python
platform.system.power_off()  # Stop motors, cut power
platform.hardware.enumerate()  # Servos, camera, gripper, etc.
platform.hardware.motors.move(angle=45)  # Robot-specific
```

### Embedded Adapter

For Raspberry Pi, BeagleBone, Arduino-like systems:

```python
class EmbeddedPlatform(Platform):
    def available(self) -> bool:
        return os.path.exists("/proc/device-tree")
```

Would provide:

```python
platform.hardware.gpio.set(pin=17, value=True)
platform.hardware.i2c.read(address=0x68)
platform.audio.play_sound(path)  # ← no TTS in embedded
```

## Design Principles

### 1. **Capabilities, Not Commands**

CDOS asks for capabilities, not commands. The adapter decides how to achieve them.

❌ BAD:
```python
os.system("systemctl reboot")
```

✅ GOOD:
```python
platform.system.reboot()
```

### 2. **Graceful Degradation**

If a capability is unavailable, report it clearly:

```python
if platform.audio.available():
    platform.audio.speak("Hello")
else:
    platform.user.say("Audio offline")
```

### 3. **No Direct Shell**

Adapters never call the shell. Everything uses direct APIs or subprocess with parsed argv.

❌ BAD:
```python
os.system("reboot")  # Shell injection risk
subprocess.run("reboot", shell=True)
```

✅ GOOD:
```python
subprocess.run(["systemctl", "reboot"])
```

### 4. **Distribution Independence**

Linux adapters don't check `/etc/os-release`. They try commands in order:

❌ BAD:
```python
if distro == "debian":
    cmd = "apt install"
elif distro == "fedora":
    cmd = "dnf install"
```

✅ GOOD:
```python
for cmd in [["apt", "install"], ["dnf", "install"], ["pacman", "-S"]]:
    if shutil.which(cmd[0]):
        # Use this command
```

## Testing

All capabilities are tested in `tests/test_platform.py`:

```bash
python -m unittest tests.test_platform -v
```

Tests verify:
- ✓ All 8 capabilities are abstract and well-defined
- ✓ Linux adapter implements all capabilities
- ✓ Each capability method works correctly
- ✓ Platform registry selects the right adapter
- ✓ Error handling is consistent

Current coverage: **59 platform tests**, within a suite of **288, all passing**.

Run with the system interpreter (`/usr/bin/python3 -m unittest discover -s tests -q`);
PyGObject's extension is built for it, so under any other interpreter the
GTK-touching modules fail to import. See AGENTS.md and `docs/PLATFORM_AUDIT.md`.

## Integration with CDOS Core

The platform layer will integrate with `earth_os.core.Shell`:

```python
from earth_os.platform import get_platform

class Shell:
    def __init__(self, config=None):
        # ... existing init ...
        self.platform = get_platform()
    
    def reboot(self) -> None:
        self.platform.system.reboot()
    
    def launch(self, command: str, name: str = "") -> bool:
        argv = shlex.split(command)
        pid = self.platform.process.launch(argv)
        return pid > 0
    
    def operator_name(self) -> str:
        return self.platform.user.user_full_name(
            self.platform.user.current_user()
        )
```

## Migration Path

Phase 1 (complete): Define platform abstraction layer ✓
Phase 2 (next): Integrate platform layer into Shell
Phase 3: Refactor Shell methods to use platform layer
Phase 4: Test against multiple Linux distributions
Phase 5: Build robot adapter (as proof of portability)

---

**The platform abstraction layer is the foundation for CDOS becoming a truly portable operating system substrate.**
