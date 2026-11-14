"""Tests for the CDOS Platform Abstraction Layer.

The platform layer should be platform-neutral in its interface and concrete
in its implementations. Tests verify:

1. The abstract API is well-defined
2. The Linux adapter implements it correctly
3. Multiple adapters can coexist
4. The registry selects the right adapter
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earth_os.platform import (  # noqa: E402
    Audio, Display, FileSystem, Hardware, Network, Platform, PlatformError,
    Process, System, User, get_platform, reset_platform,
)
from earth_os.platform import registry                       # noqa: E402
from earth_os.platform.api import ProcessInfo, NetworkInterface, HardwareDevice
from earth_os.platform.linux_adapter import LinuxPlatform


class TestAbstractCapabilities(unittest.TestCase):
    """Verify that capability classes define required interfaces."""

    def test_system_capability_abstract(self):
        """System capability should not be instantiable directly."""
        self.assertTrue(hasattr(System, '__abstractmethods__'))

    def test_process_capability_abstract(self):
        """Process capability should not be instantiable directly."""
        self.assertTrue(hasattr(Process, '__abstractmethods__'))

    def test_all_capabilities_abstract(self):
        """All capability classes should be abstract."""
        for capability in [System, Process, FileSystem, Network, Hardware, Audio, Display, User]:
            self.assertTrue(hasattr(capability, '__abstractmethods__'),
                          f"{capability.__name__} should be abstract")


class TestLinuxPlatform(unittest.TestCase):
    """Test the Linux platform adapter."""

    def setUp(self):
        self.platform = LinuxPlatform()

    def test_platform_name(self):
        """Platform should identify itself."""
        self.assertEqual(self.platform.name, "Linux")
        self.assertEqual(self.platform.version, "1.0")

    def test_platform_available_on_linux(self):
        """Platform should report available on Linux."""
        if sys.platform.startswith("linux"):
            self.assertTrue(self.platform.available())

    def test_all_capabilities_present(self):
        """Platform should have all capability systems."""
        self.assertIsNotNone(self.platform.system)
        self.assertIsNotNone(self.platform.process)
        self.assertIsNotNone(self.platform.filesystem)
        self.assertIsNotNone(self.platform.network)
        self.assertIsNotNone(self.platform.hardware)
        self.assertIsNotNone(self.platform.audio)
        self.assertIsNotNone(self.platform.display)
        self.assertIsNotNone(self.platform.user)

    def test_capabilities_report(self):
        """Platform should report capability availability."""
        caps = self.platform.capabilities()
        self.assertIsInstance(caps, dict)
        # Filesystem should always be available
        self.assertTrue(caps.get("filesystem"))


class TestLinuxFileSystem(unittest.TestCase):
    """Test filesystem operations."""

    def setUp(self):
        self.platform = LinuxPlatform()
        self.fs = self.platform.filesystem
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_and_read(self):
        """Write and read a file."""
        path = self.tmp_path / "test.txt"
        data = b"hello world"

        self.assertTrue(self.fs.write(str(path), data))
        self.assertEqual(self.fs.read(str(path)), data)

    def test_exists(self):
        """Check file existence."""
        path = self.tmp_path / "exists.txt"
        self.assertFalse(self.fs.exists(str(path)))

        self.fs.write(str(path), b"data")
        self.assertTrue(self.fs.exists(str(path)))

    def test_is_directory(self):
        """Check if path is a directory."""
        dir_path = self.tmp_path / "subdir"
        self.fs.create_directory(str(dir_path))
        self.assertTrue(self.fs.is_directory(str(dir_path)))

        file_path = self.tmp_path / "file.txt"
        self.fs.write(str(file_path), b"content")
        self.assertFalse(self.fs.is_directory(str(file_path)))

    def test_list_directory(self):
        """List directory contents."""
        self.fs.write(str(self.tmp_path / "a.txt"), b"")
        self.fs.write(str(self.tmp_path / "b.txt"), b"")
        self.fs.create_directory(str(self.tmp_path / "subdir"))

        contents = self.fs.list_directory(str(self.tmp_path))
        self.assertIn("a.txt", contents)
        self.assertIn("b.txt", contents)
        self.assertIn("subdir", contents)

    def test_delete_file(self):
        """Delete a file."""
        path = self.tmp_path / "delete_me.txt"
        self.fs.write(str(path), b"data")
        self.assertTrue(self.fs.exists(str(path)))

        self.assertTrue(self.fs.delete(str(path)))
        self.assertFalse(self.fs.exists(str(path)))

    def test_create_directory_with_parents(self):
        """Create nested directories."""
        path = self.tmp_path / "a" / "b" / "c"
        self.assertTrue(self.fs.create_directory(str(path), parents=True))
        self.assertTrue(self.fs.is_directory(str(path)))


class TestLinuxProcess(unittest.TestCase):
    """Test process operations."""

    def setUp(self):
        self.platform = LinuxPlatform()
        self.process = self.platform.process

    def test_launch_returns_a_handle(self):
        """Launch returns something the caller can watch, not a bare pid."""
        handle = self.process.launch(["echo", "hello"], detached=False)
        self.assertIsInstance(handle.pid, int)
        self.assertGreater(handle.pid, 0)
        handle.wait(timeout=5)              # reap it

    def test_launch_not_found(self):
        """Launching non-existent command raises error."""
        with self.assertRaises(PlatformError):
            self.process.launch(["/nonexistent/command"])

    def test_handle_reports_a_clean_exit(self):
        handle = self.process.launch(["true"], detached=False)
        code, stderr = handle.wait(timeout=5)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")

    def test_handle_reports_failure_and_stderr(self):
        """The whole reason launch returns a handle: knowing *why* it died.

        A program that starts and exits immediately looks identical to a
        successful launch unless the exit code and stderr can be read back.
        """
        handle = self.process.launch(
            ["sh", "-c", "echo bad things >&2; exit 3"], detached=False)
        code, stderr = handle.wait(timeout=5)
        self.assertEqual(code, 3)
        self.assertIn("bad things", stderr)

    def test_handle_says_none_while_still_running(self):
        """Still running after the timeout is the good outcome for a program
        that was supposed to open a window."""
        handle = self.process.launch(["sleep", "5"], detached=False)
        try:
            code, _ = handle.wait(timeout=0.2)
            self.assertIsNone(code)
            self.assertIsNone(handle.poll())
        finally:
            self.process.terminate(handle.pid, "KILL")

    def test_terminate_takes_a_signal_name(self):
        """Signal numbers are a Unix detail the interface hides."""
        handle = self.process.launch(["sleep", "5"], detached=False)
        self.assertTrue(self.process.terminate(handle.pid, "TERM"))
        handle.wait(timeout=5)

    def test_release_frees_a_program_that_keeps_talking(self):
        """A captured pipe nobody empties eventually blocks the program.

        The buffer is about 64K, so a program that writes more than that to
        stderr and is never drained hangs forever on its next warning — and
        ordinary desktop programs chatter warnings for as long as they run.
        Releasing has to let it run on regardless.
        """
        # The chatter has to start *after* the watch window, because the watch
        # itself reads the pipe — it is the silence afterwards that is
        # dangerous. Then far more than a pipe holds (~64K), so an undrained
        # pipe genuinely wedges the writer instead of merely fitting in it.
        handle = self.process.launch(
            ["sh", "-c", "sleep 0.4; head -c 400000 /dev/zero | "
                         "tr '\\0' 'x' >&2"],
            detached=False)
        code, _ = handle.wait(timeout=0.2)
        self.assertIsNone(code, "should still be running")
        handle.release()
        try:
            for _ in range(80):
                if handle.poll() is not None:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(handle.poll(),
                                 "program blocked on an undrained pipe")
        finally:
            self.process.terminate(handle.pid, "KILL")

    def test_release_is_safe_to_repeat(self):
        handle = self.process.launch(["true"], detached=False)
        handle.wait(timeout=5)
        handle.release()
        handle.release()

    def test_uncaptured_launch_still_reports_its_exit(self):
        """Fire-and-forget still has to say whether it worked."""
        handle = self.process.launch(["false"], detached=False,
                                     capture_stderr=False)
        code, stderr = handle.wait(timeout=5)
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")

    def test_terminate_rejects_a_nonsense_signal(self):
        handle = self.process.launch(["sleep", "5"], detached=False)
        try:
            self.assertFalse(self.process.terminate(handle.pid, "NOT_A_SIGNAL"))
        finally:
            self.process.terminate(handle.pid, "KILL")
            handle.wait(timeout=5)

    def test_list_processes(self):
        """List running processes."""
        processes = self.process.list_processes()
        self.assertIsInstance(processes, list)
        # Should find at least this process
        self.assertGreater(len(processes), 0)
        self.assertTrue(all(isinstance(p, ProcessInfo) for p in processes))

    def test_process_info_fields(self):
        """ProcessInfo has expected fields."""
        pid = os.getpid()
        info = self.process.inspect(pid)
        if info:
            self.assertIsInstance(info.pid, int)
            self.assertIsInstance(info.name, str)
            self.assertIsInstance(info.state, str)


class TestLinuxHardware(unittest.TestCase):
    """Test hardware operations."""

    def setUp(self):
        self.platform = LinuxPlatform()
        self.hw = self.platform.hardware

    def test_cpu_count(self):
        """Get CPU core count."""
        count = self.hw.cpu_count()
        self.assertIsInstance(count, int)
        self.assertGreater(count, 0)

    def test_cpu_model(self):
        """Get CPU model name."""
        model = self.hw.cpu_model()
        self.assertIsInstance(model, str)

    def test_memory_bytes(self):
        """Get memory info."""
        total, available = self.hw.memory_bytes()
        self.assertIsInstance(total, int)
        self.assertIsInstance(available, int)
        self.assertGreater(total, 0)
        self.assertGreaterEqual(available, 0)

    def test_enumerate_devices(self):
        """Enumerate hardware devices."""
        devices = self.hw.enumerate()
        self.assertIsInstance(devices, list)
        # Should find CPU and memory
        device_types = {d.device_type for d in devices}
        self.assertIn("cpu", device_types)


class TestLinuxUser(unittest.TestCase):
    """Test user operations."""

    def setUp(self):
        self.platform = LinuxPlatform()
        self.user = self.platform.user

    def test_current_user(self):
        """Get current username."""
        username = self.user.current_user()
        self.assertIsInstance(username, str)
        self.assertGreater(len(username), 0)

    def test_user_home(self):
        """Get home directory."""
        home = self.user.user_home()
        self.assertIsInstance(home, str)
        self.assertTrue(Path(home).exists())

    def test_user_full_name(self):
        """Get user's full name."""
        username = self.user.current_user()
        full_name = self.user.user_full_name(username)
        self.assertIsInstance(full_name, str)
        # Should be at least the username
        self.assertGreater(len(full_name), 0)


class TestLinuxNetwork(unittest.TestCase):
    """Test network operations."""

    def setUp(self):
        self.platform = LinuxPlatform()
        self.network = self.platform.network

    def test_list_interfaces(self):
        """List network interfaces."""
        interfaces = self.network.list_interfaces()
        self.assertIsInstance(interfaces, list)
        # Should find at least loopback
        self.assertTrue(any(i.name == "lo" for i in interfaces))

    def test_interface_info(self):
        """Interface info has expected fields."""
        interfaces = self.network.list_interfaces()
        if interfaces:
            iface = interfaces[0]
            self.assertIsInstance(iface, NetworkInterface)
            self.assertIsInstance(iface.name, str)
            self.assertIsInstance(iface.state, str)


class TestLinuxDisplay(unittest.TestCase):
    """Test display operations."""

    def setUp(self):
        self.platform = LinuxPlatform()
        self.display = self.platform.display

    def test_get_resolution(self):
        """A resolution, if one can be read, is a sane pair of pixel counts.

        A headless container has no framebuffer, no DRM card and no X server,
        so refusing is the correct answer here and is asserted as such —
        rather than the reading being forced to succeed, which is what the
        old hardcoded ``(1024, 768)`` fallback quietly did on every machine.
        """
        try:
            width, height = self.display.get_resolution()
        except PlatformError:
            return
        self.assertIsInstance(width, int)
        self.assertIsInstance(height, int)
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)

    def test_get_brightness(self):
        """A brightness, if one can be read, is a fraction."""
        try:
            brightness = self.display.get_brightness()
        except PlatformError:
            return
        self.assertIsInstance(brightness, float)
        self.assertGreaterEqual(brightness, 0.0)
        self.assertLessEqual(brightness, 1.0)


class TestPlatformRegistry(unittest.TestCase):
    """Test platform adapter selection."""

    def tearDown(self):
        reset_platform()

    def test_get_platform_returns_platform(self):
        """get_platform returns a Platform instance."""
        platform = get_platform()
        self.assertIsInstance(platform, Platform)

    def test_get_platform_caches_result(self):
        """get_platform returns the same instance."""
        p1 = get_platform()
        p2 = get_platform()
        self.assertIs(p1, p2)

    def test_reset_platform(self):
        """reset_platform clears the cache."""
        p1 = get_platform()
        reset_platform()
        p2 = get_platform()
        self.assertIsNot(p1, p2)

    def test_linux_platform_available_on_linux(self):
        """LinuxPlatform is available on Linux."""
        if sys.platform.startswith("linux"):
            platform = get_platform()
            self.assertEqual(platform.name, "Linux")


class TestCapabilityConsistency(unittest.TestCase):
    """Verify that capabilities are consistent across implementations."""

    def setUp(self):
        self.platform = get_platform()

    def test_most_capabilities_available(self):
        """Most core capabilities should be available.

        Audio may not be available in containerized environments.
        Filesystem, process, user should always be available.
        """
        caps = self.platform.capabilities()
        self.assertTrue(caps.get("filesystem"))
        self.assertTrue(caps.get("process"))
        self.assertTrue(caps.get("user"))

    def test_filesystem_available(self):
        """Filesystem capability should be available."""
        self.assertTrue(self.platform.filesystem.available())

    def test_process_available(self):
        """Process capability should be available."""
        self.assertTrue(self.platform.process.available())

    def test_user_available(self):
        """User capability should be available."""
        self.assertTrue(self.platform.user.available())


class TestCommandsReportWhatHappened(unittest.TestCase):
    """A command that failed must not come back looking like one that worked.

    Starting a process successfully only means the binary existed.
    ``systemctl reboot`` on a machine with no systemd exits non-zero having
    done nothing, and if that reads as success the shell says "Acknowledged"
    to a reboot that never happened.  These tests pin the distinction.
    """

    def setUp(self):
        self.system = LinuxPlatform().system

    def test_failing_command_raises(self):
        """A non-zero exit is a failure, not a success."""
        with self.assertRaises(PlatformError):
            self.system._run_command(["false"])

    def test_failure_carries_the_reason(self):
        """The error says what went wrong, so the shell can show it."""
        with self.assertRaises(PlatformError) as caught:
            self.system._run_command(
                ["sh", "-c", "echo no bus available >&2; exit 1"])
        self.assertIn("no bus available", str(caught.exception))

    def test_missing_binary_is_named(self):
        with self.assertRaises(PlatformError) as caught:
            self.system._run_command(["cdos-definitely-not-installed"])
        self.assertIn("not installed", str(caught.exception))

    def test_zero_exit_is_success(self):
        self.system._run_command(["true"])           # must not raise

    def test_still_running_counts_as_success(self):
        """A reboot in progress does not return; the timeout is the success."""
        self.system._run_command(["sleep", "5"], grace=0.2)

    def test_fallback_runs_when_the_first_choice_fails(self):
        """The bug this guards: the fallback used to be reachable only when
        the binary was missing, never when it ran and failed — which is the
        one situation it was written for."""
        self.system._run_command(["false"], fallback=["true"])

    def test_error_names_every_attempt(self):
        with self.assertRaises(PlatformError) as caught:
            self.system._run_command(
                ["sh", "-c", "echo first >&2; exit 1"],
                fallback=["sh", "-c", "echo second >&2; exit 1"])
        message = str(caught.exception)
        self.assertIn("first", message)
        self.assertIn("second", message)


class TestReadingsAreNotInvented(unittest.TestCase):
    """A reading that could not be taken must not look like one that was."""

    def setUp(self):
        self.display = LinuxPlatform().display

    def test_resolution_is_real_or_refused(self):
        """Never the old silent ``return (1024, 768)``.

        That placeholder was indistinguishable from a real answer, and
        because the xrandr call beneath it passed a flag xrandr rejects, the
        placeholder was what every machine actually got.
        """
        try:
            width, height = self.display.get_resolution()
        except PlatformError:
            return                       # honest refusal is the other valid answer
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)
        self.assertNotEqual(
            (width, height), (1024, 768),
            "1024x768 here is the old hardcoded placeholder, not a reading")

    def test_brightness_is_real_or_refused(self):
        try:
            level = self.display.get_brightness()
        except PlatformError:
            return
        self.assertGreaterEqual(level, 0.0)
        self.assertLessEqual(level, 1.0)


class TestAdapterPriority(unittest.TestCase):
    """Phase 3 depends on this: a robot running Linux must get the robot
    adapter.  Linux is the general answer, so it has to yield to a specific
    one or the specific one can never win."""

    def setUp(self):
        self._saved = list(registry._ADAPTERS)
        reset_platform()

    def tearDown(self):
        registry._ADAPTERS = self._saved
        reset_platform()

    def _stub(self, label: str, works: bool = True):
        class Stub(LinuxPlatform):
            name = label

            def available(self) -> bool:
                return works
        Stub.__name__ = f"Stub{label}"
        return Stub

    def test_registered_adapter_is_tried_before_linux(self):
        registry.register_adapter(self._stub("Robot"))
        self.assertEqual(registry.adapters()[0].name, "Robot")
        self.assertIs(registry.adapters()[-1], LinuxPlatform)

    def test_a_specific_adapter_wins_on_a_linux_machine(self):
        registry.register_adapter(self._stub("Robot"))
        self.assertEqual(get_platform().name, "Robot")

    def test_priority_orders_the_search(self):
        registry.register_adapter(self._stub("Late"), priority=500)
        registry.register_adapter(self._stub("Early"), priority=10)
        order = [a.name for a in registry.adapters()]
        self.assertLess(order.index("Early"), order.index("Late"))

    def test_registering_twice_does_not_queue_twice(self):
        stub = self._stub("Robot")
        registry.register_adapter(stub)
        registry.register_adapter(stub, priority=5)
        self.assertEqual(sum(1 for a in registry.adapters() if a is stub), 1)

    def test_unavailable_adapter_is_skipped(self):
        registry.register_adapter(self._stub("Absent", works=False))
        self.assertEqual(get_platform().name, "Linux")

    def test_no_adapter_says_what_was_tried(self):
        registry._ADAPTERS = [(0, 0, self._stub("Absent", works=False))]
        with self.assertRaises(PlatformError) as caught:
            get_platform()
        self.assertIn("Absent", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
