# CDOS Platform Abstraction Layer

This module implements the **CDOS Platform Abstraction Layer** — the foundation for making CDOS run on any operating system or hardware platform.

## Quick Start

```python
from earth_os.platform import get_platform

# Get the platform adapter for this system
platform = get_platform()

# Use capabilities
username = platform.user.current_user()
platform.system.reboot()
pid = platform.process.launch(["firefox"])
data = platform.filesystem.read("/etc/hostname")
```

## Files

| File | Purpose |
|------|---------|
| `api.py` | Abstract capability interfaces (8 capabilities) |
| `linux_adapter.py` | Linux reference implementation |
| `registry.py` | Platform selection and caching |
| `__init__.py` | Public API exports |

## Capabilities

The platform abstraction defines **8 capabilities**:

1. **System** — Power, reboot, shutdown, suspend, lock
2. **Process** — Launch programs, manage processes
3. **FileSystem** — Read/write files and directories
4. **Network** — List interfaces, connect to networks
5. **Hardware** — CPU, memory, temperature, battery info
6. **Audio** — Speech synthesis and recognition
7. **Display** — Brightness and resolution control
8. **User** — User identity and permissions

Each capability is:
- **Abstract**: Defined by a Python ABC with required methods
- **Platform-specific**: Implemented differently for each platform
- **Composable**: All capabilities work together through Platform

## Platform Selection

The registry tries adapters in order and returns the first available one:

```python
# Automatically tries:
# 1. LinuxPlatform (currently only option)
# 2. Future: RobotPlatform, EmbeddedPlatform, etc.

platform = get_platform()  # Returns LinuxPlatform() on Linux

# Same instance on subsequent calls (cached)
p1 = get_platform()
p2 = get_platform()
assert p1 is p2
```

## Linux Adapter

The Linux adapter is the reference implementation. It:

- Uses distribution-agnostic methods wherever possible
- Reads `/proc`, `/sys` for hardware info
- Tries commands in preference order (systemctl → shutdown → etc.)
- Never calls shell, always uses subprocess with parsed argv
- Fails gracefully if a capability isn't available

### Examples

**System reboot (with fallback):**
```python
# Tries systemctl first, falls back to sudo shutdown
platform.system.reboot()
```

**Process listing (from /proc):**
```python
processes = platform.process.list_processes()
# Returns ProcessInfo objects with PID, name, memory, CPU
```

**Hardware info:**
```python
cpus = platform.hardware.cpu_count()        # /proc/cpuinfo
memory = platform.hardware.memory_bytes()   # /proc/meminfo
temp = platform.hardware.temperature()      # /sys/class/thermal
```

## Testing

All 36 tests pass:

```bash
cd earth-os
python -m unittest tests.test_platform -v
```

Tests cover:
- Abstract API validation
- Linux adapter functionality
- Error handling
- Platform registry
- Capability availability

## Adding a New Adapter

To add support for a new platform (e.g., robots):

```python
# platform/robot_adapter.py
from .api import Platform, System, Process, etc.

class RobotSystem(System):
    def available(self) -> bool:
        return os.path.exists("/dev/robot")
    
    def reboot(self) -> None:
        self.robot.power_off()

class RobotPlatform(Platform):
    name = "Robot"
    version = "1.0"
    
    def __init__(self):
        self.system = RobotSystem()
        # ... etc for all 8 capabilities
    
    def available(self) -> bool:
        return os.path.exists("/dev/robot")
```

Then register it in `registry.py`:

```python
from .robot_adapter import RobotPlatform

_ADAPTERS: List[type[Platform]] = [
    LinuxPlatform,
    RobotPlatform,  # Tried second
    # ...
]
```

## Design Principles

### 1. Capabilities, Not Commands

CDOS asks "what capabilities do I need?" not "what command should I run?"

```python
# ✓ GOOD: Abstract
platform.system.reboot()

# ❌ BAD: Platform-specific
os.system("systemctl reboot")
subprocess.run(["sudo", "shutdown", "-r", "now"])
```

### 2. Graceful Degradation

Every capability reports `available()`. Adapters don't fail silently.

```python
if platform.audio.available():
    platform.audio.speak("Hello")
else:
    print("Audio unavailable")
```

### 3. Distribution Independence

Linux adapter doesn't check `/etc/os-release`. It tries commands in order:

```python
# ✓ GOOD: Works on Debian, Ubuntu, Fedora, Arch, etc.
for cmd in [["apt", "install"], ["dnf", "install"], ["pacman", "-S"]]:
    if shutil.which(cmd[0]):
        subprocess.run([*cmd, "package"])
        break

# ❌ BAD: Only works on one distro
if distro == "debian":
    subprocess.run(["apt", "install", "package"])
```

### 4. No Shell Execution

All subprocess calls use parsed argv, never shell=True:

```python
# ✓ GOOD: Safe
subprocess.run(["systemctl", "reboot"])

# ❌ BAD: Shell injection risk
subprocess.run("systemctl reboot", shell=True)
os.system("systemctl reboot")
```

## Integration with CDOS Core

The Shell class will be updated to use the platform layer:

```python
from earth_os.platform import get_platform

class Shell:
    def __init__(self, config=None):
        self.config = config or Config.load()
        self.platform = get_platform()  # ← NEW
        # ... rest of init
    
    def launch(self, command: str) -> bool:
        try:
            pid = self.platform.process.launch(argv)  # ← Uses platform
            return True
        except PlatformError as exc:
            self.status_message = str(exc)
            return False
    
    def operator_name(self) -> str:
        username = self.platform.user.current_user()  # ← Uses platform
        return self.platform.user.user_full_name(username)
```

## Performance

- Platform detection: ~1ms (one-time, cached)
- Capability calls: Native speed (no overhead)
- No subprocess overhead for inquiries like `cpu_count()` (reads files directly)

## Future Work

- [ ] Robot adapter (servo, motor, camera capabilities)
- [ ] Embedded adapter (GPIO, I2C, SPI for Pi/BeagleBone)
- [ ] Windows WSL adapter
- [ ] Performance profiling across platforms
- [ ] Distribution-specific performance tuning

## See Also

- `docs/PLATFORM_ABSTRACTION.md` — Architecture overview
- `docs/PLATFORM_USAGE_EXAMPLE.md` — Concrete before/after examples
- `tests/test_platform.py` — Comprehensive test suite

---

**The platform abstraction layer transforms CDOS from a Linux shell into a portable operating system substrate.**
