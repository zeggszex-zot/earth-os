"""The capability catalogue: what this computer can be asked to do.

This is the layer the corpus talks to.  The corpus deals in intent — *restart
the computer* — and resolves that to a capability name like ``system.reboot``.
It never deals in ``systemctl reboot``, because that is a fact about one
platform rather than a fact about computers.

    "restart the computer"
             |
          CORPUS      restart -> synonym_of -> reboot
             |        reboot  -> requires   -> system.reboot
             |
           CDOS       intent: system.reboot
             |
        CAPABILITY    registry.invoke("system.reboot")
             |
         PLATFORM     platform.system.reboot()
             |
          ADAPTER     systemctl reboot

The catalogue is also a **gate**, and that is the more important half.  A
model may be asked to interpret a request it has never seen, and a model will
cheerfully invent a plausible-sounding answer.  ``system.format_disk`` reads
like something that ought to exist.  It does not exist here, so it cannot be
invoked here: :meth:`CapabilityRegistry.invoke` refuses anything that is not
in :data:`CATALOGUE`, and the catalogue is written by hand.

That is the architectural rule this module exists to enforce:

    The corpus and CDOS establish what the computer is capable of.
    A model may help interpret a request.  It may not extend the machine.

Adding a capability is a deliberate act — an entry here, and a binding in
:meth:`CapabilityRegistry._bind`.  Nothing else can widen the surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .api import Platform, PlatformError


@dataclass(frozen=True)
class CapabilitySpec:
    """One thing the computer can be asked to do."""

    name: str                              # "system.reboot"
    summary: str                           # what it does, in a line
    params: Tuple[str, ...] = ()           # required argument names
    optional: Tuple[str, ...] = ()
    effectful: bool = False                # changes the machine -> always ask
    permission: str = ""                   # checked against user.has_permission

    @property
    def domain(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def action(self) -> str:
        return self.name.split(".", 1)[1]


@dataclass
class CapabilityResult:
    """What came back.  Never raises at the caller; failure is data."""

    ok: bool
    capability: str = ""
    value: object = None
    error: str = ""

    def __bool__(self) -> bool:
        return self.ok


def _spec(name: str, summary: str, **kwargs) -> Tuple[str, CapabilitySpec]:
    return (name, CapabilitySpec(name=name, summary=summary, **kwargs))


#: Every capability this operating system has.  Nothing outside this table can
#: be invoked, whatever names a model produces.
#:
#: ``effectful`` marks the ones that change the machine in a way that is not
#: trivially undone, or that interrupt whatever the person was doing.  Those
#: always require a deliberate human press, however confident the reading was.
#: Setting the volume is not one of those; rebooting is.
CATALOGUE: Dict[str, CapabilitySpec] = dict([
    # -- system ---------------------------------------------------------
    _spec("system.reboot", "restart the computer",
          effectful=True, permission="reboot"),
    _spec("system.shutdown", "power the computer off",
          effectful=True, permission="shutdown"),
    _spec("system.suspend", "suspend the computer to memory", effectful=True),
    _spec("system.logout", "end this session", effectful=True),
    # Locking is deliberate and instantly undone by typing a password, so it
    # does not earn a confirmation step — being asked "are you sure?" every
    # time would defeat the point of saying it.
    _spec("system.lock", "lock the session"),
    _spec("system.kernel", "what kernel is running"),
    _spec("system.distro", "what this environment calls itself"),
    _spec("system.hostname", "what this machine is called"),

    # -- process --------------------------------------------------------
    # Launching is the ordinary business of a shell; every button does it.
    _spec("process.launch", "start a program",
          params=("argv",), optional=("name", "detached", "capture_stderr")),
    _spec("process.terminate", "stop a running program",
          params=("pid",), optional=("signal",), effectful=True),
    _spec("process.list", "what is running"),
    _spec("process.inspect", "details of one running program", params=("pid",)),

    # -- filesystem -----------------------------------------------------
    _spec("filesystem.read", "read a file", params=("path",)),
    _spec("filesystem.write", "write a file",
          params=("path", "data"), optional=("append",), effectful=True),
    _spec("filesystem.list", "list a directory", params=("path",)),
    _spec("filesystem.create_directory", "make a directory",
          params=("path",), optional=("parents",)),
    _spec("filesystem.delete", "delete a file or directory",
          params=("path",), optional=("recursive",), effectful=True),
    _spec("filesystem.exists", "whether something is there", params=("path",)),

    # -- network --------------------------------------------------------
    _spec("network.interfaces", "list network interfaces"),
    _spec("network.status", "state of one interface", params=("name",)),
    _spec("network.connect", "join a wireless network",
          params=("ssid",), optional=("password",), effectful=True),
    _spec("network.disconnect", "leave the wireless network",
          optional=("name",), effectful=True),
    # The communications screen's half of the domain. Reading the radio and
    # listing what is in range change nothing. Switching the radio off cuts
    # every connection at once, and forgetting a network throws away a
    # password a person may not have written down, so those two ask first;
    # switching it on is undone by switching it off.
    _spec("network.wireless", "whether wireless is on, and what it is joined to"),
    _spec("network.scan", "wireless networks in range", optional=("rescan",)),
    _spec("network.wifi_on", "switch the wireless radio on"),
    _spec("network.wifi_off", "switch the wireless radio off", effectful=True),
    _spec("network.forget", "forget a saved wireless network",
          params=("ssid",), effectful=True),

    # -- hardware -------------------------------------------------------
    _spec("hardware.enumerate", "list attached hardware"),
    _spec("hardware.cpu_count", "how many processor cores"),
    _spec("hardware.cpu_model", "what processor this is"),
    _spec("hardware.memory", "total and available memory"),
    _spec("hardware.temperature", "how hot the machine is"),
    _spec("hardware.battery", "battery level and charging state"),

    # -- audio ----------------------------------------------------------
    _spec("audio.speak", "say something out loud",
          params=("text",), optional=("rate",)),
    _spec("audio.listen", "capture speech", optional=("timeout",)),
    _spec("audio.set_volume", "set the volume", params=("level",)),
    _spec("audio.get_volume", "what the volume is"),

    # -- display --------------------------------------------------------
    _spec("display.set_brightness", "set screen brightness", params=("level",)),
    _spec("display.get_brightness", "what the brightness is"),
    # A wrong resolution can leave a screen that cannot be read well enough to
    # put it back, so this one asks first.
    _spec("display.set_resolution", "change screen resolution",
          params=("width", "height"), effectful=True),
    _spec("display.get_resolution", "what the resolution is"),
    # Writes a new file and touches nothing else; asking first would put a
    # press between a person and the moment they wanted to keep.
    _spec("display.screenshot", "capture the whole screen to a picture",
          optional=("path",)),

    _spec("hardware.cpu_load", "how busy the processor is"),
    _spec("hardware.cpu_load_per_core", "how busy each core is"),
    _spec("hardware.load_average", "the one, five and fifteen minute load"),
    _spec("hardware.swap", "how much swap is in use"),
    _spec("hardware.disk", "how full the disk is", optional=("path",)),
    _spec("hardware.uptime", "how long this machine has been up"),
    _spec("hardware.processes", "how many processes are running"),
    _spec("hardware.net_rate", "how fast the network is moving"),
    # -- window ---------------------------------------------------------
    #
    # Placing, closing and fullscreening are effectful: they move or remove
    # something the person is looking at, and a misheard phrase should not
    # rearrange their screen.  Listing and focusing are not — focusing is
    # undone by focusing something else.
    _spec("window.list", "what windows are open"),
    _spec("window.focus", "bring a window to the front", params=("handle",)),
    _spec("window.close", "close a window", params=("handle",), effectful=True),
    _spec("window.fullscreen", "put a window full screen",
          params=("handle",), optional=("on",), effectful=True),
    _spec("window.place", "put a window at an exact rectangle",
          params=("handle", "x", "y", "w", "h"), effectful=True),
    _spec("window.can_place", "whether windows can be placed here"),
    # -- user -----------------------------------------------------------
    _spec("user.identity", "who is logged in"),
    _spec("user.full_name", "a user's full name", params=("username",)),
    _spec("user.home", "the current user's home directory"),
    _spec("user.sessions", "which sessions are active"),

    # -- bluetooth ------------------------------------------------------
    #
    # Connecting pairs first when it has to: to a person those are one act,
    # "use my headphones", and both are undone by disconnecting or forgetting.
    # Switching the adapter off can take the keyboard and mouse with it, which
    # is not something to do on a misheard word, so that asks first. So does
    # forgetting, because pairing again means finding the device's pairing
    # button.
    _spec("bluetooth.state", "whether bluetooth is on"),
    _spec("bluetooth.on", "switch bluetooth on"),
    _spec("bluetooth.off", "switch bluetooth off", effectful=True),
    _spec("bluetooth.devices", "bluetooth devices this computer knows"),
    _spec("bluetooth.scan", "look for bluetooth devices nearby",
          optional=("seconds",)),
    _spec("bluetooth.connect", "connect a bluetooth device, pairing if needed",
          params=("address",)),
    _spec("bluetooth.disconnect", "disconnect bluetooth devices",
          optional=("address",)),
    _spec("bluetooth.forget", "forget a paired bluetooth device",
          params=("address",), effectful=True),
])


class CapabilityRegistry:
    """Resolves capability names against a platform, and invokes them.

    Holds no policy about *when* something should run — that is the shell's
    business, and the shell reads :attr:`CapabilitySpec.effectful` to decide.
    This only answers two questions honestly: does this capability exist, and
    can this platform actually do it?
    """

    def __init__(self, platform: Platform):
        self.platform = platform
        self._bindings: Dict[str, Callable] = self._bind(platform)

    # ------------------------------------------------------------------
    @staticmethod
    def _bind(p: Platform) -> Dict[str, Callable]:
        """Map each catalogue name onto the platform method behind it.

        Written out rather than derived from the name by ``getattr``, because
        a table that builds itself from strings would let a typo — or a
        cleverly shaped capability name — reach a method that was never meant
        to be reachable.  The whole point of this module is that the reachable
        surface is written down.
        """
        return {
            "system.reboot": p.system.reboot,
            "system.shutdown": p.system.shutdown,
            "system.suspend": p.system.suspend,
            "system.logout": p.system.logout,
            "system.lock": p.system.lock,
            "system.hostname": p.system.get_hostname,

            "process.launch": p.process.launch,
            "process.terminate": p.process.terminate,
            "process.list": p.process.list_processes,
            "process.inspect": p.process.inspect,

            "filesystem.read": p.filesystem.read,
            "filesystem.write": p.filesystem.write,
            "filesystem.list": p.filesystem.list_directory,
            "filesystem.create_directory": p.filesystem.create_directory,
            "filesystem.delete": p.filesystem.delete,
            "filesystem.exists": p.filesystem.exists,

            "network.interfaces": p.network.list_interfaces,
            "network.status": p.network.interface_status,
            "network.connect": p.network.connect,
            "network.disconnect": p.network.disconnect,
            "network.wireless": p.network.wireless_state,
            "network.scan": p.network.scan,
            "network.wifi_on": lambda: p.network.set_wireless(True),
            "network.wifi_off": lambda: p.network.set_wireless(False),
            "network.forget": p.network.forget,

            "hardware.enumerate": p.hardware.enumerate,
            "hardware.cpu_count": p.hardware.cpu_count,
            "hardware.cpu_model": p.hardware.cpu_model,
            "hardware.memory": p.hardware.memory_bytes,
            "hardware.temperature": p.hardware.temperature,
            "hardware.battery": p.hardware.battery,

            "audio.speak": p.audio.speak,
            "audio.listen": p.audio.listen,
            "audio.set_volume": p.audio.set_volume,
            "audio.get_volume": p.audio.get_volume,

            "display.set_brightness": p.display.set_brightness,
            "display.get_brightness": p.display.get_brightness,
            "display.set_resolution": p.display.set_resolution,
            "display.get_resolution": p.display.get_resolution,
            "display.screenshot": p.display.screenshot,

            "user.identity": p.user.current_user,
            "user.full_name": p.user.user_full_name,
            "user.home": p.user.user_home,
            "hardware.cpu_load": p.hardware.cpu_load,
            "hardware.cpu_load_per_core": p.hardware.cpu_load_per_core,
            "hardware.load_average": p.hardware.load_average,
            "hardware.swap": p.hardware.swap_bytes,
            "hardware.disk": p.hardware.disk_bytes,
            "hardware.uptime": p.hardware.uptime,
            "hardware.processes": p.hardware.process_count,
            "hardware.net_rate": p.hardware.net_rate,
            "system.kernel": p.system.kernel,
            "system.distro": p.system.distro,
            "window.list": p.window.list_windows,
            "window.focus": p.window.focus,
            "window.close": p.window.close,
            "window.fullscreen": p.window.fullscreen,
            "window.place": p.window.place,
            "window.can_place": p.window.can_place,
            "user.sessions": p.user.list_sessions,

            "bluetooth.state": p.bluetooth.state,
            "bluetooth.on": lambda: p.bluetooth.set_power(True),
            "bluetooth.off": lambda: p.bluetooth.set_power(False),
            "bluetooth.devices": p.bluetooth.devices,
            "bluetooth.scan": p.bluetooth.scan,
            "bluetooth.connect": p.bluetooth.connect,
            "bluetooth.disconnect": p.bluetooth.disconnect,
            "bluetooth.forget": p.bluetooth.forget,
        }

    # ------------------------------------------------------------------
    def names(self) -> List[str]:
        """Every capability name, in catalogue order."""
        return list(CATALOGUE)

    def spec(self, name: str) -> Optional[CapabilitySpec]:
        return CATALOGUE.get((name or "").strip().lower())

    def known(self, name: str) -> bool:
        """Whether this is a capability at all.  The gate, in one call."""
        return self.spec(name) is not None

    def domain_available(self, domain: str) -> bool:
        capability = getattr(self.platform, domain, None)
        if capability is None:
            return False
        try:
            return bool(capability.available())
        except Exception:
            return False

    def supported(self, name: str) -> bool:
        """Known, bound, and provided by *this* platform.

        A robot has no ``display.set_resolution``; saying so plainly is more
        useful than failing halfway through.
        """
        spec = self.spec(name)
        if spec is None or spec.name not in self._bindings:
            return False
        return self.domain_available(spec.domain)

    def available(self) -> List[str]:
        """The capabilities this machine actually has, right now."""
        return [name for name in CATALOGUE if self.supported(name)]

    # ------------------------------------------------------------------
    def validate(self, capability: str, /, **kwargs) -> str:
        """Why this call would be refused, or ``""`` if it would not be.

        Separate from :meth:`invoke` so the shell can check before it offers
        a person a button that cannot work.

        The capability is positional-only for the reason given on
        :meth:`invoke`.
        """
        name = capability
        spec = self.spec(name)
        if spec is None:
            return f"no such capability: {name}"
        if spec.name not in self._bindings:
            return f"{spec.name} is not wired to this platform"
        if not self.domain_available(spec.domain):
            return f"{spec.domain} is not available on {self.platform.name}"
        missing = [p for p in spec.params if p not in kwargs]
        if missing:
            return f"{spec.name} needs {', '.join(missing)}"
        allowed = set(spec.params) | set(spec.optional)
        extra = [k for k in kwargs if k not in allowed]
        if extra:
            return f"{spec.name} does not take {', '.join(sorted(extra))}"
        if spec.permission and not self._permitted(spec.permission):
            return f"not permitted: {spec.permission}"
        return ""

    def _permitted(self, permission: str) -> bool:
        try:
            return bool(self.platform.user.has_permission(permission))
        except Exception:
            return False

    # ------------------------------------------------------------------
    def invoke(self, capability: str, /, **kwargs) -> CapabilityResult:
        """Run a capability, if it is one.

        The capability name is positional-only so that it cannot collide with
        an argument of the capability itself — ``process.launch`` takes a
        ``name``, and without this ``invoke("process.launch", name="htop")``
        would be two values for one parameter.

        Failure comes back as a :class:`CapabilityResult`, never as an
        exception: this sits under a voice loop and a button, and neither of
        those has anywhere sensible to put a traceback.
        """
        key = (capability or "").strip().lower()
        problem = self.validate(key, **kwargs)
        if problem:
            return CapabilityResult(ok=False, capability=key, error=problem)
        try:
            value = self._bindings[key](**kwargs)
        except PlatformError as exc:
            return CapabilityResult(ok=False, capability=key, error=str(exc))
        except Exception as exc:                     # a broken adapter
            return CapabilityResult(
                ok=False, capability=key,
                error=f"{type(exc).__name__}: {exc}")
        return CapabilityResult(ok=True, capability=key, value=value)

    # ------------------------------------------------------------------
    def describe(self, available_only: bool = True) -> str:
        """The catalogue as text, for putting in front of a model.

        This is how a model finds out what the machine can do: it is told,
        rather than asked to remember or guess.  Effectful entries are marked
        so an answer can say so without having to know which ones they are.
        """
        names = self.available() if available_only else self.names()
        lines: List[str] = []
        domain = ""
        for name in names:
            spec = CATALOGUE[name]
            if spec.domain != domain:
                domain = spec.domain
                lines.append(f"{domain.upper()}")
            args = "".join(f" <{p}>" for p in spec.params)
            mark = "  [needs confirmation]" if spec.effectful else ""
            lines.append(f"  {spec.name}{args} — {spec.summary}{mark}")
        return "\n".join(lines)

    def summary(self) -> str:
        have = len(self.available())
        return (f"{have} of {len(CATALOGUE)} capabilities on "
                f"{self.platform.name} {self.platform.version}")
