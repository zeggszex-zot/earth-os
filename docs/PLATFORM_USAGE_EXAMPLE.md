# Platform Abstraction: Usage Examples

## The Same Code Runs Everywhere

This is the power of the platform abstraction layer. The same CDOS code can run on:
- Desktop Linux (Debian, Kali, Ubuntu)
- Embedded Linux (Raspberry Pi)
- Robots (with motor/sensor adapters)
- Future platforms not yet written

## Example 1: Launching a Program

### Current Code (Platform-Specific)

```python
# earth_os/core.py - OLD WAY
import subprocess
import shlex

def launch(self, command: str, name: str = "") -> bool:
    if not command:
        return False
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        self.status_message = f"cannot parse command: {exc}"
        return False
    try:
        process = subprocess.Popen(
            argv, start_new_session=True, 
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE)
    except FileNotFoundError:
        self.status_message = f"not installed: {argv[0]}"
        return False
    except (OSError, ValueError) as exc:
        self.status_message = f"launch failed: {exc}"
        return False
    # ... rest of method
```

**Problem:** This is hardcoded for Linux. On a robot, `start_new_session=True` makes no sense.

### New Code (Platform-Agnostic)

```python
# earth_os/core.py - NEW WAY
from earth_os.platform import get_platform, PlatformError

def launch(self, command: str, name: str = "") -> bool:
    if not command:
        return False
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        self.status_message = f"cannot parse command: {exc}"
        return False
    try:
        pid = self.platform.process.launch(argv, name=name)
    except PlatformError as exc:
        self.status_message = str(exc)
        return False
    # ... rest of method
```

**Benefit:** Same code works on Linux, robot, embedded. Platform handles the details.

### How It Works on Different Platforms

#### Linux
```python
# platform/linux_adapter.py
class LinuxProcess(Process):
    def launch(self, argv, name="", detached=True):
        process = subprocess.Popen(
            argv, 
            start_new_session=True,  # Linux-specific
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE)
        return process.pid
```

#### Robot (Future)
```python
# platform/robot_adapter.py
class RobotProcess(Process):
    def launch(self, argv, name="", detached=True):
        # Robot doesn't have traditional processes
        # Instead, queue the command for the robot controller
        cmd = RobotCommand(
            action="execute",
            program=argv[0],
            args=argv[1:],
            name=name
        )
        self.robot_queue.put(cmd)
        return cmd.id  # Return command ID instead of PID
```

#### Embedded (Future)
```python
# platform/embedded_adapter.py
class EmbeddedProcess(Process):
    def launch(self, argv, name="", detached=True):
        # Embedded system might run everything sequentially
        # or queue tasks for a scheduler
        task = Task(argv, name=name)
        self.task_scheduler.enqueue(task)
        return task.id
```

**Same caller code, three different implementations!**

---

## Example 2: System Reboot

### Current Code (Linux-Hardcoded)

```python
# earth_os/core.py - OLD
def reboot(self) -> None:
    self._session_command(
        ["systemctl", "reboot"], 
        fallback=self.quit)
```

**Hardcoded:** Assumes systemd exists. Won't work on non-Linux.

### New Code (Platform-Agnostic)

```python
# earth_os/core.py - NEW
def reboot(self) -> None:
    try:
        self.platform.system.reboot()
    except PlatformError as exc:
        self.say(f"Cannot reboot: {exc}")
```

### How It Works on Different Platforms

#### Linux
```python
# platform/linux_adapter.py
class LinuxSystem(System):
    def reboot(self) -> None:
        self._run_command(
            ["systemctl", "reboot"],
            fallback=["sudo", "shutdown", "-r", "now"])
```

#### Embedded (Raspberry Pi)
```python
# platform/embedded_adapter.py
class EmbeddedSystem(System):
    def reboot(self) -> None:
        # On embedded, might need to wait for tasks to finish
        self.task_scheduler.wait_for_idle(timeout=5)
        self._run_command(["systemctl", "reboot"])
```

#### Robot
```python
# platform/robot_adapter.py
class RobotSystem(System):
    def reboot(self) -> None:
        # Stop motors and power down
        self.robot.motors.stop()
        self.robot.power.shutdown()
```

---

## Example 3: User Information

### Current Code (Linux-Hardcoded)

```python
# earth_os/core.py - OLD
def operator_name(self) -> str:
    configured = str(self.config.get("shell", "operator", "") or "").strip()
    if configured:
        return configured
    try:
        import getpass
        import pwd
        login = getpass.getuser()
        full = pwd.getpwnam(login).pw_gecos.split(",")[0].strip()
        return full or login
    except Exception:
        return os.environ.get("USER") or os.environ.get("USERNAME") or ""
```

**Hardcoded:** Uses `/etc/passwd` directly. Doesn't work on systems without it.

### New Code (Platform-Agnostic)

```python
# earth_os/core.py - NEW
def operator_name(self) -> str:
    configured = str(self.config.get("shell", "operator", "") or "").strip()
    if configured:
        return configured
    username = self.platform.user.current_user()
    return self.platform.user.user_full_name(username)
```

### How It Works on Different Platforms

#### Linux
```python
# platform/linux_adapter.py
class LinuxUser(User):
    def current_user(self) -> str:
        import getpass
        return getpass.getuser()
    
    def user_full_name(self, username: str) -> str:
        import pwd
        return pwd.getpwnam(username).pw_gecos.split(",")[0].strip()
```

#### Embedded (Logged in via SSH)
```python
# platform/embedded_adapter.py
class EmbeddedUser(User):
    def current_user(self) -> str:
        # Embedded might not have getpass
        return os.environ.get("USER", "embedded-user")
    
    def user_full_name(self, username: str) -> str:
        # No /etc/passwd; use a simple config file instead
        config = self.user_config.get(username, {})
        return config.get("full_name", username)
```

#### Robot (Single-operator device)
```python
# platform/robot_adapter.py
class RobotUser(User):
    def current_user(self) -> str:
        return "operator"  # Only one user per robot
    
    def user_full_name(self, username: str) -> str:
        # Robots typically have a configured name
        return self.robot_config.get("operator_name", "Robot")
```

---

## Example 4: Checking Capabilities

You can query what the platform supports at runtime:

```python
# CDOS code that adapts based on capabilities
platform = get_platform()

# Display info
if platform.display.available():
    brightness = platform.display.get_brightness()
    self.status_message = f"Brightness: {int(brightness * 100)}%"

# Audio
if platform.audio.available():
    platform.audio.speak("Hello, world!")
else:
    self.say("Audio unavailable", speak=False)

# Hardware sensors
devices = platform.hardware.enumerate()
print(f"Found {len(devices)} hardware devices")

# Network
interfaces = platform.network.list_interfaces()
print(f"Connected via: {[i.name for i in interfaces if i.state == 'up']}")
```

---

## The Corpus Flows Through

Here's where the corpus layer sits in the flow:

```
USER:
"reboot the computer"

        ↓

VOICE RECOGNIZER:
"reboot the computer" (raw text)

        ↓

DISPATCHER:
Matches "reboot" in vocabulary

        ↓

SEMANTICS:
Looks up "reboot" → "system.power_control"
Confidence: high (built-in knowledge)

        ↓

CDOS CORE:
run_action("reboot")

        ↓

SHELL:
self.reboot()

        ↓

PLATFORM:
platform.system.reboot()

        ↓

ADAPTER:
Linux:    systemctl reboot
Robot:    robot.power_off()
Embedded: /sbin/reboot
```

The corpus doesn't know **how** to reboot. It knows:
- "reboot" means "system.power_control"
- That concept maps to a CDOS action
- The action calls a platform method
- The platform knows the specifics

---

## Migration Strategy

### Phase 1: Platform Layer (DONE ✓)
- Define abstract API
- Implement Linux adapter
- Write 36 comprehensive tests
- 100% test pass rate

### Phase 2: Integrate Into Shell
Core.py will use `self.platform` for:
- `launch()` → `platform.process.launch()`
- `reboot()` → `platform.system.reboot()`
- `shutdown()` → `platform.system.shutdown()`
- `operator_name()` → `platform.user.*`
- File operations → `platform.filesystem.*`

### Phase 3: Robot Adapter
Prove portability by building a robot adapter that:
- Implements same API
- Works with same CDOS code
- Adds robot-specific capabilities

### Phase 4: Multi-Distro Testing
Verify Linux adapter works across:
- Ubuntu 22.04 LTS
- Debian 12
- Kali Linux
- Fedora 39
- Arch Linux

### Phase 5: Documentation
- Usage guide for custom adapters
- Architecture decision records
- Performance profiling across platforms

---

## Why This Matters

**Before:** CDOS is "Linux shell" — pigeonholed to one OS family.

**After:** CDOS is "operating system substrate" — runs anywhere you write an adapter.

This is the architectural move that transforms CDOS from a GUI for Linux into a true portable operating system.

The corpus becomes the **lingua franca**: the language any platform speaks.
