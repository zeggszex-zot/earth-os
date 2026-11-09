"""Platform adapter registry and selection.

At runtime, CDOS asks "which platform am I running on?" and gets back the
appropriate adapter. This registry handles that lookup.

Future adapters can register themselves here without touching CDOS itself.
"""

from __future__ import annotations

from itertools import count
from typing import List, Optional, Tuple

from .api import Platform, PlatformError
from .linux_adapter import LinuxPlatform
from .robot_stub import RobotPlatform


#: Priority of the built-in Linux adapter.
#:
#: Deliberately a large number, because Linux is the *general* answer rather
#: than the specific one.  A robot and a Raspberry Pi both run Linux, so
#: :meth:`LinuxPlatform.available` is true on them too; if the general adapter
#: were tried first it would always win and the specific adapter would never
#: be reached.  Specific adapters therefore register below this number.
GENERAL = 1000

#: Registered adapters as ``(priority, sequence, class)``.  The sequence keeps
#: registration order stable among equal priorities, and keeps the tuples
#: sortable without ever comparing two classes.
#: Adapters that ship with CDOS, most specific first.
#:
#: The robot sits ahead of Linux deliberately: a robot runs Linux too, so the
#: general adapter would answer for it and the specific one would never be
#: reached. It claims nothing unless its marker is set, so a desktop is never
#: taken over by a robot that is not there.
_ADAPTERS: List[Tuple[int, int, type[Platform]]] = [
    (100, 0, RobotPlatform),
    (GENERAL, 1, LinuxPlatform),
]

_SEQUENCE = count(1)

_PLATFORM: Optional[Platform] = None


def register_adapter(adapter_class: type[Platform],
                     priority: int = GENERAL - 1) -> None:
    """Register a platform adapter.

    *priority* orders the search: lower is tried first.  The default puts a
    newly registered adapter ahead of the built-in Linux one, because an
    adapter someone went to the trouble of writing for this machine describes
    it better than the generic fallback does — a robot running Linux wants the
    robot adapter, and would otherwise never get it.

    Registering the same class twice replaces the earlier entry rather than
    queueing it up again, so re-importing a module cannot change the order.
    """
    global _ADAPTERS
    _ADAPTERS = [entry for entry in _ADAPTERS if entry[2] is not adapter_class]
    _ADAPTERS.append((int(priority), next(_SEQUENCE), adapter_class))
    _ADAPTERS.sort(key=lambda entry: (entry[0], entry[1]))


def adapters() -> List[type[Platform]]:
    """The registered adapters, in the order they will be tried."""
    return [entry[2] for entry in _ADAPTERS]


def get_platform() -> Platform:
    """Get the platform adapter for this system.

    Tries adapters in order until one reports it's available, then
    initializes it. Returns the same instance on subsequent calls.
    """
    global _PLATFORM

    if _PLATFORM is not None:
        return _PLATFORM

    refused: List[str] = []
    for adapter_class in adapters():
        name = getattr(adapter_class, "name", adapter_class.__name__)
        try:
            adapter = adapter_class()
            if not adapter.available():
                refused.append(f"{name}: not this machine")
                continue
            if not adapter.initialize():
                refused.append(f"{name}: would not start")
                continue
        except Exception as exc:
            refused.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        _PLATFORM = adapter
        return _PLATFORM

    # Say which adapters were tried and what each one said. A bare "no adapter
    # available" leaves whoever is porting CDOS to a new machine with nothing
    # to go on, and this is exactly the moment they need it.
    detail = "; ".join(refused) if refused else "none registered"
    raise PlatformError(f"no platform adapter for this system ({detail})")


def reset_platform() -> None:
    """Clear the cached platform. Used in tests."""
    global _PLATFORM
    _PLATFORM = None
