"""CDOS Platform Abstraction Layer.

The platform layer divorces CDOS from Linux-specific implementation details
by defining capabilities that any platform must provide, and letting adapters
implement those capabilities.

Instead of CDOS knowing "how to reboot", it knows "I need system.power_control"
and asks the platform adapter how to achieve that for this environment.

This enables CDOS to run on:
- Multiple Linux distributions (Debian, Kali, Ubuntu, Fedora, etc.)
- Non-Linux platforms (embedded, robot, Windows via WSL)
- Future platforms not yet imagined

The architecture is:

    CDOS (intent-based)
         │
         ↓
    Platform Adapter (capability-based)
         │
         ├── Linux Adapter
         │    ├── Debian
         │    ├── Ubuntu
         │    ├── Kali
         │    └── ...
         │
         ├── Robot Adapter
         │    ├── motors
         │    ├── sensors
         │    └── camera
         │
         └── Embedded Adapter

Key design principle:
    CDOS asks for capabilities.
    The adapter provides implementations.
    The corpus maps intent to capability.
"""

from .api import (
    Audio, Capability, Display, FileSystem, Hardware, Network, Platform,
    PlatformError, Process, ProcessHandle, System, User,
)
from .capabilities import (
    CATALOGUE, CapabilityRegistry, CapabilityResult, CapabilitySpec,
)
from .registry import get_platform, reset_platform

__all__ = [
    "Platform",
    "Capability",
    "System",
    "Process",
    "ProcessHandle",
    "FileSystem",
    "Network",
    "Hardware",
    "Audio",
    "Display",
    "User",
    "WindowControl",
    "WindowInfo",
    "PlatformError",
    "get_platform",
    "reset_platform",
    # The capability layer: what the corpus names instead of commands.
    "CATALOGUE",
    "CapabilityRegistry",
    "CapabilityResult",
    "CapabilitySpec",
]
