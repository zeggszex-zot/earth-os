"""Tests for Claude Code built into CDOS (``earth_os/agent``).

**Human-write-only** (listed in ``earth_os/agent/policy.py``). These are the
tests that hold the agent to its gate; an agent that could edit them could
decide what its gate checks.

What they establish, in the order it matters:

1. The gate keeps only what the suite passes, sets everything else aside, and
   refuses protected files and boundary-moving changes *before* the suite can
   launder them.
2. Nothing the agent asks for reaches the machine without the shell's gated
   path: effectful capabilities wait for CONFIRM, unlisted commands wait for
   ALLOW, and CANCEL/DENY mean nothing ran.
3. The plumbing is real: a fake ``claude`` is driven end to end through the
   controller, its edit is judged, and the result is committed.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os.agent import policy                              # noqa: E402
from earth_os.agent.bridge import ControlBridge                # noqa: E402
from earth_os.agent.controller import AgentController          # noqa: E402
from earth_os.agent.gate import COUNT_FILE, Gate               # noqa: E402
from earth_os.agent.mcp_server import TOOLS, BridgeClient, Server  # noqa: E402
from earth_os.agent.session import ClaudeSession, describe_tool  # noqa: E402
from earth_os.agent.workspace import Workspace, provision      # noqa: E402
from earth_os.config import Config                             # noqa: E402
from earth_os.core import Shell                                # noqa: E402
from earth_os.screens import ALL_SCREENS                       # noqa: E402

PY = sys.executable


def temp_config() -> Config:
    config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
    config.values["voice"]["enabled"] = False
    return config


def make_shell() -> Shell:
    shell = Shell(temp_config())
    for screen_class in ALL_SCREENS:
        shell.register_screen(screen_class(shell))
    return shell


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                           *args], cwd=root, capture_output=True, text=True,
                          check=True).stdout


PASSING_TEST = textwrap.dedent('''
    import unittest
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import app

    class T(unittest.TestCase):
        def test_answer(self):
            self.assertEqual(app.ANSWER, 42)
        def test_two(self):
            self.assertTrue(True)
''')


def make_repo() -> Path:
    """A tiny project with a suite, committed: what a workspace looks like."""
    root = Path(tempfile.mkdtemp(prefix="cdos-gate-"))
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text(PASSING_TEST)
    (root / "app.py").write_text("ANSWER = 42\n")
    (root / "AGENTS.md").write_text("rules\n")
    (root / "earth_os" / "platform").mkdir(parents=True)
    (root / "earth_os" / "platform" / "linux_adapter.py").write_text("x = 1\n")
    (root / "earth_os" / "capability_corpus.py").write_text("y = 1\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


# ----------------------------------------------------------------------
# 1. The gate
# ----------------------------------------------------------------------
class TestTheGate(unittest.TestCase):
    def setUp(self):
        self.root = make_repo()
        self.ws = Workspace(self.root)
        self.gate = Gate(self.ws, python=PY)
        self.base = self.ws.head()

    def stashes(self) -> str:
        return git(self.root, "stash", "list")

    def test_a_passing_change_is_committed_once_with_its_evidence(self):
        (self.root / "app.py").write_text("ANSWER = 42\nNAME = 'lcars'\n")
        result = self.gate.run(self.base, "name the app")
        self.assertTrue(result.accepted, result.reason)
        self.assertEqual(result.tests_run, 2)
        self.assertEqual(git(self.root, "rev-parse", "HEAD~1").strip(), self.base)
        body = git(self.root, "log", "-1", "--pretty=%B")
        self.assertIn("Verified by the CDOS gate", body)
        self.assertIn("2 tests, OK", body)
        self.assertIn("app.py", body)
        self.assertFalse(self.ws.dirty())
        self.assertEqual((self.root / ".git" / COUNT_FILE).read_text().strip(), "2")

    def test_a_failing_change_is_set_aside_not_kept(self):
        (self.root / "app.py").write_text("ANSWER = 41\n")
        result = self.gate.run(self.base, "break it")
        self.assertFalse(result.accepted)
        self.assertEqual(self.ws.head(), self.base)
        self.assertFalse(self.ws.dirty(), "the failed change is still on disk")
        self.assertIn("cdos-gate rejected", self.stashes())
        self.assertEqual((self.root / "app.py").read_text(), "ANSWER = 42\n")

    def test_protected_files_are_refused_before_the_suite_runs(self):
        (self.root / "AGENTS.md").write_text("rules, relaxed\n")
        self.gate.run_suite = lambda: self.fail("the suite ran on a protected change")
        result = self.gate.run(self.base, "relax the rules")
        self.assertFalse(result.accepted)
        self.assertIn("human-write-only", result.reason)
        self.assertEqual((self.root / "AGENTS.md").read_text(), "rules\n")

    def test_every_rung_four_file_is_protected(self):
        for path in ("earth_os/platform/capabilities.py", "tests/test_boundary.py",
                     "tests/test_capabilities.py", "AGENTS.md",
                     "earth_os/agent/policy.py", "earth_os/agent/gate.py",
                     "tests/test_agent.py"):
            self.assertIn(path, policy.PROTECTED, path)

    def test_both_sides_of_the_boundary_at_once_is_refused(self):
        (self.root / "earth_os/platform/linux_adapter.py").write_text("x = 2\n")
        (self.root / "earth_os/capability_corpus.py").write_text("y = 2\n")
        result = self.gate.run(self.base, "new capability")
        self.assertFalse(result.accepted)
        self.assertIn("a person grants those", result.reason)

    def test_one_side_of_the_boundary_is_judged_normally(self):
        (self.root / "earth_os/platform/linux_adapter.py").write_text("x = 2\n")
        self.assertTrue(self.gate.run(self.base, "new mechanism").accepted)

    def test_commits_made_mid_turn_are_judged_and_squashed(self):
        (self.root / "app.py").write_text("ANSWER = 41\n")
        git(self.root, "commit", "-qam", "sneaky")
        result = self.gate.run(self.base, "sneak")
        self.assertFalse(result.accepted)
        self.assertEqual(self.ws.head(), self.base, "the agent's commit survived")

        (self.root / "app.py").write_text("ANSWER = 42\nA = 1\n")
        git(self.root, "commit", "-qam", "fine")
        (self.root / "app.py").write_text("ANSWER = 42\nA = 2\n")
        result = self.gate.run(self.base, "two steps")
        self.assertTrue(result.accepted)
        self.assertEqual(git(self.root, "rev-parse", "HEAD~1").strip(), self.base)

    def test_the_suite_may_not_shrink_to_pass(self):
        (self.root / "app.py").write_text("ANSWER = 42\n# ok\n")
        self.assertTrue(self.gate.run(self.base, "first").accepted)
        base = self.ws.head()
        (self.root / "tests" / "test_app.py").write_text(
            PASSING_TEST.replace("    def test_two(self):\n        self.assertTrue(True)\n", ""))
        result = self.gate.run(base, "delete a test")
        self.assertFalse(result.accepted)
        self.assertIn("shrank from 2 to 1", result.reason)

    def test_no_interpreter_means_nothing_is_kept(self):
        (self.root / "app.py").write_text("ANSWER = 42\nB = 1\n")
        result = Gate(self.ws, python="/nonexistent/python3.12").run(self.base, "x")
        self.assertFalse(result.accepted)
        self.assertIn("cannot verify", result.reason)

    def test_nothing_changed_is_nothing(self):
        self.assertEqual(self.gate.run(self.base, "noop").verdict, "nothing")


# ----------------------------------------------------------------------
# 2. Policy and the bridge: the gated paths
# ----------------------------------------------------------------------
class TestPolicy(unittest.TestCase):
    def test_protected_edits_are_denied_without_asking(self):
        root = Path("/work")
        decision, why = policy.review("Edit", {"file_path": "/work/AGENTS.md"}, root)
        self.assertEqual(decision, "deny")
        self.assertIn("human-write-only", why)

    def test_the_gates_git_verbs_are_denied(self):
        for command in ("git commit -am x", "git reset --hard", "git stash"):
            self.assertEqual(policy.review("Bash", {"command": command},
                                           Path("/w"))[0], "deny", command)

    def test_everything_else_goes_to_a_person(self):
        decision, detail = policy.review("Bash", {"command": "rm -rf build"}, Path("/w"))
        self.assertIsNone(decision)
        self.assertEqual(detail, "rm -rf build")

    def test_settings_deny_protected_files_and_default_to_accept_edits(self):
        s = policy.settings(Path("/w"))
        self.assertIn("Edit(./AGENTS.md)", s["permissions"]["deny"])
        self.assertIn("Bash(git commit:*)", s["permissions"]["deny"])
        self.assertEqual(s["permissions"]["defaultMode"], "acceptEdits")
        self.assertNotIn("Bash", s["permissions"]["allow"],
                         "unrestricted Bash would bypass every press")


class RecordingRegistry:
    """Wraps the real registry and records what actually ran."""

    def __init__(self, real):
        self.real = real
        self.ran = []

    def __getattr__(self, name):
        return getattr(self.real, name)

    def invoke(self, name, **kwargs):
        self.ran.append(name)
        if self.real.spec(name) is not None and self.real.spec(name).effectful:
            # Recorded, never performed: a test must not suspend the machine
            # it runs on.
            from earth_os.platform.capabilities import CapabilityResult
            return CapabilityResult(ok=True, value=None)
        return self.real.invoke(name, **kwargs)


def press_when_offered(shell, label, timeout=5.0):
    """A person, on another thread, pressing *label* once it appears."""
    def person():
        deadline = time.time() + timeout
        while time.time() < deadline:
            for text, callback in list(shell.pending_options):
                if text == label:
                    callback()
                    return
            time.sleep(0.01)
    thread = threading.Thread(target=person, daemon=True)
    thread.start()
    return thread


class TestTheBridge(unittest.TestCase):
    def setUp(self):
        self.shell = make_shell()
        if self.shell.capabilities is None:
            self.skipTest("no platform adapter here")
        self.registry = RecordingRegistry(self.shell.capabilities)
        self.shell.capabilities = self.registry
        self.bridge = ControlBridge(self.shell, workspace=ROOT,
                                    shots_dir=Path(tempfile.mkdtemp()),
                                    decision_timeout=5.0)

    def test_status_and_navigation(self):
        status = self.bridge.handle("status", {})
        self.assertTrue(status["ok"])
        self.assertIn("claude", status["screens"])
        self.assertTrue(self.bridge.handle("go", {"screen": "engineering"})["ok"])
        self.assertEqual(self.shell.current, "engineering")
        self.assertFalse(self.bridge.handle("go", {"screen": "bridge"})["ok"])

    def test_palette_must_exist(self):
        self.assertFalse(self.bridge.handle("set_palette", {"name": "Borg"})["ok"])
        name = self.bridge.handle("palettes", {})["palettes"][0]
        self.assertTrue(self.bridge.handle("set_palette", {"name": name})["ok"])
        self.assertEqual(self.shell.palette.name, name)

    def test_the_catalogue_stays_closed(self):
        reply = self.bridge.handle("invoke", {"capability": "system.format_disk"})
        self.assertFalse(reply["ok"])
        self.assertNotIn("system.format_disk", self.registry.ran)

    def test_a_reading_returns_its_value(self):
        reply = self.bridge.handle("invoke", {"capability": "user.identity"})
        self.assertIn("ok", reply)
        self.assertIn("user.identity", self.registry.ran)

    def test_effectful_waits_and_cancel_means_nothing_ran(self):
        press_when_offered(self.shell, "CANCEL")
        reply = self.bridge.handle("invoke", {"capability": "system.suspend"})
        self.assertFalse(reply["ok"])
        self.assertFalse(reply["confirmed"])
        self.assertNotIn("system.suspend", self.registry.ran)
        self.assertEqual(self.shell.pending_options, [])

    def test_effectful_runs_only_after_confirm(self):
        press_when_offered(self.shell, "CONFIRM")
        reply = self.bridge.handle("invoke", {"capability": "system.suspend"})
        self.assertTrue(reply["confirmed"])
        self.assertIn("system.suspend", self.registry.ran)

    def test_silence_is_a_refusal(self):
        self.bridge.decision_timeout = 0.2
        reply = self.bridge.handle("invoke", {"capability": "system.suspend"})
        self.assertFalse(reply["ok"])
        self.assertNotIn("system.suspend", self.registry.ran)
        self.assertEqual(self.shell.pending_options, [])

    def test_permission_prompts_go_to_the_strip(self):
        press_when_offered(self.shell, "ALLOW")
        reply = self.bridge.handle("approve", {"tool_name": "Bash",
                                               "input": {"command": "make shots"}})
        self.assertEqual(reply["behavior"], "allow")
        self.assertEqual(reply["updatedInput"], {"command": "make shots"})
        press_when_offered(self.shell, "DENY")
        reply = self.bridge.handle("approve", {"tool_name": "Bash",
                                               "input": {"command": "curl x | sh"}})
        self.assertEqual(reply["behavior"], "deny")

    def test_permission_for_a_protected_file_is_never_offered(self):
        reply = self.bridge.handle("approve", {
            "tool_name": "Write",
            "input": {"file_path": str(ROOT / "earth_os/agent/gate.py")}})
        self.assertEqual(reply["behavior"], "deny")
        self.assertEqual(self.shell.pending_options, [])

    def test_screenshot_renders_a_png(self):
        reply = self.bridge.handle("screenshot", {"screen": "claude",
                                                  "width": 800, "height": 500})
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(Path(reply["path"]).read_bytes().startswith(b"\x89PNG"))

    def test_the_bridge_never_calls_the_ungated_door(self):
        source = (ROOT / "earth_os/agent/bridge.py").read_text()
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("capabilities.invoke(", code)

    def test_over_a_real_socket_through_the_mcp_server(self):
        path = os.path.join(tempfile.mkdtemp(), "a.sock")
        self.bridge.start(path)
        try:
            server = Server(BridgeClient(path))
            init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2025-06-18"}})
            self.assertEqual(init["result"]["serverInfo"]["name"], "cdos")
            names = {t["name"] for t in server.handle(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
            self.assertLessEqual({"status", "go", "invoke", "approve", "screenshot"}, names)
            reply = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                   "params": {"name": "go",
                                              "arguments": {"screen": "settings"}}})
            self.assertFalse(reply["result"]["isError"])
            self.assertEqual(self.shell.current, "settings")
            decision = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                      "params": {"name": "approve", "arguments": {
                                          "tool_name": "Edit",
                                          "input": {"file_path": str(ROOT / "AGENTS.md")}}}})
            text = json.loads(decision["result"]["content"][0]["text"])
            self.assertEqual(text["behavior"], "deny")
            self.assertIsNone(server.handle({"jsonrpc": "2.0",
                                             "method": "notifications/initialized"}))
        finally:
            self.bridge.stop()

    def test_every_mcp_tool_is_a_bridge_operation(self):
        for tool in TOOLS:
            self.assertTrue(hasattr(ControlBridge, f"op_{tool['name']}"), tool["name"])

    def test_the_mcp_server_starts_without_the_toolkit(self):
        code = ("import sys, earth_os.agent.mcp_server; "
                "print(any(m.startswith('gi') for m in sys.modules))")
        out = subprocess.run([PY, "-c", code], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
        self.assertEqual(out, "False")


# ----------------------------------------------------------------------
# 3. The session, the controller and the voice path
# ----------------------------------------------------------------------
FAKE_CLAUDE = textwrap.dedent('''\
    #!{python}
    import json, os, sys, pathlib
    log = pathlib.Path(os.environ["FAKE_CLAUDE_LOG"])
    with log.open("a") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\\n")
    prompt = sys.argv[sys.argv.index("-p") + 1]
    def out(event):
        print(json.dumps(event), flush=True)
    out({{"type": "system", "subtype": "init", "session_id": "sess-1234abcd",
          "model": "fake", "mcp_servers": [{{"name": "cdos", "status": "connected"}}]}})
    if "edit" in prompt:
        pathlib.Path("app.py").write_text("ANSWER = 42\\nEDITED = True\\n")
        out({{"type": "assistant", "message": {{"content": [
            {{"type": "tool_use", "name": "Edit", "input": {{"file_path": os.path.abspath("app.py")}}}}]}}}})
    if "break" in prompt:
        pathlib.Path("app.py").write_text("ANSWER = 0\\n")
    out({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "Done."}}]}}}})
    out({{"type": "result", "subtype": "success", "is_error": False, "result": "Done.",
          "total_cost_usd": 0.01, "num_turns": 2, "session_id": "sess-1234abcd"}})
''')


def make_fake_claude() -> Path:
    path = Path(tempfile.mkdtemp()) / "claude"
    path.write_text(FAKE_CLAUDE.format(python=PY))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def wait_for(predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestTheSession(unittest.TestCase):
    def test_argv_carries_the_rules(self):
        s = ClaudeSession(Path("/w"), command=PY, state_dir=Path(tempfile.mkdtemp()),
                          socket_path="/tmp/x.sock")
        argv = s.argv("hello")
        self.assertIn("--permission-prompt-tool", argv)
        self.assertIn(policy.PERMISSION_TOOL, argv)
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertNotIn("--dangerously-skip-permissions", argv)
        mcp = json.loads((s.state_dir / "mcp.json").read_text())
        self.assertEqual(mcp["mcpServers"]["cdos"]["args"][-1], "/tmp/x.sock")

    def test_describe_tool(self):
        root = Path("/w")
        self.assertEqual(describe_tool("Edit", {"file_path": "/w/a/b.py"}, root), "EDIT a/b.py")
        self.assertEqual(describe_tool("Bash", {"command": "ls"}, root), "RUN ls")
        self.assertTrue(describe_tool("mcp__cdos__go", {"screen": "main"}, root)
                        .startswith("CDOS GO"))

    def test_a_missing_cli_says_so(self):
        events = []
        s = ClaudeSession(Path("/w"), command="/nonexistent/claude",
                          state_dir=Path(tempfile.mkdtemp()),
                          on_event=lambda k, t: events.append((k, t)))
        self.assertFalse(s.ask("hi"))
        self.assertTrue(any("not installed" in t for _, t in events))


class TestTheControllerEndToEnd(unittest.TestCase):
    def setUp(self):
        self.root = make_repo()
        self.fake = make_fake_claude()
        self.log = Path(tempfile.mkdtemp()) / "argv.log"
        os.environ["FAKE_CLAUDE_LOG"] = str(self.log)
        self.shell = make_shell()
        cfg = self.shell.config.values.setdefault("claude", {})
        cfg.update({"workspace": str(self.root), "command": str(self.fake),
                    "python": PY})
        self.agent = AgentController(
            self.shell, state_dir=Path(tempfile.mkdtemp()),
            socket_path=os.path.join(tempfile.mkdtemp(), "b.sock"))

    def tearDown(self):
        self.agent.shutdown()

    def settle(self):
        self.assertTrue(wait_for(lambda: self.agent.state == "READY"),
                        self.agent.snapshot())

    def test_an_edit_is_verified_and_committed(self):
        self.assertTrue(self.agent.ask("edit the app"))
        self.settle()
        self.assertTrue(self.agent.last_gate.accepted, self.agent.last_gate.reason)
        self.assertTrue(self.agent.reload_ready)
        self.assertIn("EDITED", (self.root / "app.py").read_text())
        self.assertIn("edit the app", git(self.root, "log", "-1", "--pretty=%s"))
        kinds = {k for k, _ in self.agent.snapshot()}
        self.assertLessEqual({"user", "tool", "assistant", "gate-ok"}, kinds)

    def test_a_breaking_edit_is_set_aside(self):
        self.agent.ask("break the app")
        self.settle()
        self.assertFalse(self.agent.last_gate.accepted)
        self.assertFalse(self.agent.reload_ready)
        self.assertEqual((self.root / "app.py").read_text(), "ANSWER = 42\n")

    def test_the_conversation_resumes(self):
        self.agent.ask("hello")
        self.settle()
        self.agent.ask("again")
        self.settle()
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertNotIn("--resume", calls[0])
        self.assertEqual(calls[1][calls[1].index("--resume") + 1], "sess-1234abcd")

    def test_unjudged_work_in_the_workspace_is_never_touched(self):
        (self.root / "app.py").write_text("ANSWER = 42\n# mine\n")
        self.assertFalse(self.agent.ask("edit the app"))
        self.assertEqual((self.root / "app.py").read_text(), "ANSWER = 42\n# mine\n")

    def test_undo_needs_a_press_and_reverts_only_gate_commits(self):
        self.agent.ask("edit the app")
        self.settle()
        self.agent.offer_undo()
        self.assertEqual([l for l, _ in self.shell.pending_options],
                         ["CONFIRM", "CANCEL"])
        dict(self.shell.pending_options)["CONFIRM"]()
        self.assertNotIn("EDITED", (self.root / "app.py").read_text())
        self.agent.offer_undo()                 # a revert is not a gate commit
        self.assertEqual(self.shell.pending_options, [])


class FakeAgent:
    def __init__(self):
        self.asked = []

    def ask(self, text):
        self.asked.append(text)
        return True


class TestTheVoicePath(unittest.TestCase):
    def setUp(self):
        self.shell = make_shell()
        self.fake = FakeAgent()
        self.shell.agent = self.fake

    def test_claude_by_name_is_a_request(self):
        self.shell.handle_utterance("Claude, make the home screen orange")
        self.assertEqual(self.fake.asked, ["make the home screen orange"])
        self.assertEqual(self.shell.current, "claude")

    def test_the_bare_name_opens_the_screen(self):
        for phrase in ("claude", "claude code"):
            self.shell.current = "main"
            self.shell.handle_utterance(phrase)
            self.assertEqual(self.shell.current, "claude", phrase)
        self.assertEqual(self.fake.asked, [])

    def test_other_utterances_are_untouched(self):
        self.shell.handle_utterance("engineering")
        self.assertEqual(self.shell.current, "engineering")
        self.assertEqual(self.fake.asked, [])

    def test_the_screen_registers_the_agent(self):
        shell = make_shell()
        self.assertIsNotNone(shell.agent)
        self.assertIs(shell.agent, shell.screens["claude"].agent)


class TestProvisioning(unittest.TestCase):
    def test_a_workspace_is_a_committed_copy_with_its_tests(self):
        target = Path(tempfile.mkdtemp()) / "src"
        ws = provision([ROOT], target)
        self.assertTrue((target / "tests" / "test_agent.py").exists())
        self.assertTrue((target / "AGENTS.md").exists())
        self.assertFalse(ws.dirty())
        self.assertIn("baseline", ws.log(1)[0])


if __name__ == "__main__":
    unittest.main()
