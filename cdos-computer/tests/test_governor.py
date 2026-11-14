"""The Governor (computer/governor.py; docs/GOVERNOR.md): one test class per requirement.

    1 the emergency stop survives restarts and fails safe       TestLatch, TestRestart (real processes, SIGKILL)
    2 one authorization policy, denied when unverifiable         TestPolicy, TestApiPermissions, TestPeerCredentials
    3 auditable approvals, tamper-evident log verified at start  TestAuditLog, TestDegraded, TestApprovals
    4 proposals are separate from the authority to act           TestApprovals (promote, rollback, growth)

The original defect (an engaged stop forgotten by a restart, so the robot moved) is reproduced against the 0.2 code
path in test_the_0_2_defect_is_fixed."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from test_platform import DATA, HAVE_NUMPY, TMP, ApiServer, cfg, good_obs, trained_registry   # noqa: E402

from computer.governor import (AuditLog, Forbidden, Governor, GovernorError, Identity, Latch, Policy,  # noqa: E402
                               from_uid, record_hash, unverified)
from computer.services.client import Client                                       # noqa: E402

ME = os.getuid()


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(dir=TMP.name))


def policy_file(roles: dict, **kw) -> str:
    p = tmpdir() / "governor.json"
    p.write_text(json.dumps({"format": "cdos-governor-policy/1", "roles": roles, **kw}))
    return str(p)


def who(name: str, uid: int) -> Identity:
    return Identity(uid, name, True, "test")


ALICE, ROBOT, GUEST = who("alice", 5001), who("robbie", 5002), who("guest", 5003)
POLICY = {"operator": ["alice"], "robot": ["robbie"], "teacher": ["uid:5004"]}


def gov(roles=POLICY, state=None, **kw) -> Governor:
    return Governor(policy_file(roles, **kw), state if state is not None else tmpdir())


def latch_file(state: Path) -> dict:
    return json.loads((state / "estop.json").read_text())


def rewrite_latch(state: Path, **changes) -> None:
    rec = {**latch_file(state), **changes}
    rec["hash"] = record_hash(rec)                          # a careful forger recomputes the hash
    (state / "estop.json").write_text(json.dumps(rec))


# ---------------------------------------------------------------------- 1 the latch


class TestLatch(unittest.TestCase):
    def test_engaged_until_an_operator_clears_it(self):
        g = gov()
        self.assertTrue(g.latch.engaged)
        self.assertIn("no latch record yet", g.latch.why)
        with self.assertRaises(Forbidden):
            g.estop_clear(ROBOT, "robot wants to go")
        with self.assertRaises(Forbidden) as cm:
            g.estop_clear(ALICE, "   ")
        self.assertIn("reason", cm.exception.why)
        self.assertTrue(g.latch.engaged)
        g.estop_clear(ALICE, "inspected the base")
        self.assertFalse(g.latch.engaged)

    def test_the_state_survives_a_new_governor(self):
        state = tmpdir()
        g = gov(state=state)
        g.estop_clear(ALICE, "ready")
        self.assertFalse(gov(state=state).latch.engaged)
        g.estop_engage(GUEST, "a person stepped in")          # anyone may engage it
        g2 = gov(state=state)
        self.assertTrue(g2.latch.engaged)
        self.assertIn("a person stepped in", g2.latch.why)

    def test_every_unverifiable_record_means_engaged(self):
        cases = {
            "missing": lambda s: (s / "estop.json").unlink(),
            "not json": lambda s: (s / "estop.json").write_text("{nope"),
            "edited without the hash": lambda s: (s / "estop.json").write_text(
                json.dumps({**latch_file(s), "reason": "changed"})),
            "another format": lambda s: rewrite_latch(s, format="cdos-estop-latch/0"),
            "cleared, but no audit entry": lambda s: rewrite_latch(s, audit_seq=999),
            "cleared, pointing at a different entry": lambda s: rewrite_latch(s, audit_seq=1),
            "cleared, wrong audit hash": lambda s: rewrite_latch(s, audit_hash="sha256:" + "1" * 64),
            "audit log broken": lambda s: (s / "audit.jsonl").write_text(
                (s / "audit.jsonl").read_text().replace("ready", "READY")),
            "audit log gone": lambda s: [(s / f).unlink() for f in ("audit.jsonl", "audit.head.json")],
        }
        for name, damage in cases.items():
            with self.subTest(name):
                state = tmpdir()
                g = gov(state=state)
                g.estop_engage(GUEST, "first")                  # audit seq 1: an engage, not a clear
                g.estop_clear(ALICE, "ready")
                self.assertFalse(gov(state=state).latch.engaged)
                damage(state)
                g2 = gov(state=state)
                self.assertTrue(g2.latch.engaged, name)
                self.assertTrue(g2.latch.why.startswith("engaged"), g2.latch.why)

    def test_a_forged_clear_record_doesnt_release_the_robot(self):
        """Flip an engaged record to cleared and recompute its hash: the audit log has no clear to match."""
        state = tmpdir()
        g = gov(state=state)
        g.estop_engage(GUEST, "stop")
        rewrite_latch(state, engaged=False)
        self.assertTrue(gov(state=state).latch.engaged)

    def test_in_memory_starts_engaged(self):
        g = Governor(policy_file(POLICY), None)
        self.assertTrue(g.latch.engaged)
        self.assertFalse(g.latch.persistent)
        g.estop_clear(ALICE, "dev bench")
        self.assertFalse(g.latch.engaged)
        self.assertTrue(Governor(policy_file(POLICY), None).latch.engaged)

    def test_the_controller_can_engage_but_never_clear(self):
        from test_platform import controller
        g = gov()
        ctl, clk = controller()
        ctl.latch = g.latch
        with self.assertRaises(PermissionError):
            ctl.set_e_stop(False)
        g.estop_clear(ALICE, "ok")
        ctl.submit(good_obs(obstacles=()))
        self.assertTrue(ctl.request_action([3.0, 0.0])["allowed"])
        ctl.set_e_stop(True)
        self.assertTrue(g.latch.engaged)
        ctl.submit(good_obs(obstacles=()))
        d = ctl.request_action([3.0, 0.0])
        self.assertFalse(d["allowed"])
        self.assertIn("e_stop", {v["rule_id"] for v in d["violations"]})

    def test_the_0_2_defect_is_fixed(self):
        """0.2: set_e_stop(True), restart the controller on the same journal, ask: the motion was allowed."""
        from computer.neural.components import ComponentRegistry
        from computer.neural.experience import ChainLog
        from computer.robotics.controller import JOURNAL_FORMAT, RobotController
        from computer.robotics.policy import ExpertPolicy
        from computer.robotics.safety import Envelope
        state, jp = tmpdir(), tmpdir() / "j.jsonl"

        def boot():
            g = gov(state=state)
            reg = ComponentRegistry()
            reg.register(ExpertPolicy())
            return g, RobotController(Envelope.load(), reg, lambda: 100.0, ChainLog(jp, fmt=JOURNAL_FORMAT),
                                      latch=g.latch)
        g, ctl = boot()
        g.estop_clear(ALICE, "ready")
        g.estop_engage(GUEST, "stop before the restart")
        g, ctl = boot()                                    # the restart
        ctl.submit(good_obs(t=100.0, obstacles=()))
        d = ctl.request_action([3.0, 0.0])
        self.assertFalse(d["allowed"])
        self.assertEqual(d["command"]["v"], 0.0)
        self.assertIn("e_stop", {v["rule_id"] for v in d["violations"]})
        events = [json.loads(l)["event"] for l in jp.read_text().splitlines()]
        self.assertEqual(events.count("e_stop_restored"), 2)


# ---------------------------------------------------------------------- 2 policy and permissions


class TestPolicy(unittest.TestCase):
    def test_roles_and_principals(self):
        p = Policy.from_json({"format": "cdos-governor-policy/1", "roles": {"operator": ["alice", 0, "uid:7"]}})
        self.assertEqual(p.roles["operator"], {"alice", "uid:0", "uid:7"})
        self.assertEqual(p.roles_of(who("alice", 1)), {"operator"})
        self.assertEqual(p.roles_of(who("x", 7)), {"operator"})
        self.assertEqual(p.roles_of(who("x", 8)), {"read", "ask"})
        self.assertEqual(p.roles_of(unverified()), {"read", "ask"})

    def test_bad_policies_are_refused(self):
        for bad in ({"roles": {}},
                    {"format": "cdos-governor-policy/1", "roles": {"admin": ["x"]}},
                    {"format": "cdos-governor-policy/1", "roles": {"operator": "root"}},
                    {"format": "cdos-governor-policy/1", "roles": {"operator": [True]}},
                    {"format": "cdos-governor-policy/1", "roles": {}, "default": ["robot"]},
                    {"format": "cdos-governor-policy/1", "roles": {}, "unverified": ["operator"]},
                    {"format": "cdos-governor-policy/1", "roles": {}, "extra": 1}):
            with self.subTest(bad), self.assertRaises(GovernorError):
                Policy.from_json(bad)

    def test_a_broken_or_missing_policy_means_no_privilege(self):
        bad = tmpdir() / "governor.json"
        bad.write_text("{")
        for g in (Governor(str(bad), tmpdir()), Governor(None, tmpdir())):
            self.assertIsNone(g.policy)
            self.assertTrue(g.check(ALICE, "api.ask")[0])
            for perm in ("estop.clear", "model.promote", "robot.command", "growth.answer"):
                self.assertFalse(g.check(ALICE, perm)[0], perm)
            self.assertTrue(g.check(ALICE, "estop.engage")[0])

    def test_the_example_policy_and_the_installers_are_valid(self):
        Policy.load(ROOT / "governor.example.json")
        r = subprocess.run([sys.executable, "-m", "computer.governor", "policy", "check",
                            str(ROOT / "governor.example.json")], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_who_may_do_what(self):
        g = gov()
        may = lambda i: set(g.whoami(i)["may"])                                     # noqa: E731
        self.assertEqual(may(GUEST), {"api.read", "api.ask", "estop.engage"})
        self.assertEqual(may(ROBOT), {"api.read", "api.ask", "estop.engage", "robot.observe", "robot.command"})
        self.assertIn("growth.answer", may(who("teach", 5004)))
        self.assertNotIn("model.promote", may(who("teach", 5004)))
        self.assertTrue({"estop.clear", "model.promote", "audit.reseal"} <= may(ALICE))
        self.assertEqual(may(unverified()), {"api.read", "api.ask", "estop.engage"})
        # an unverified caller can't be given a privileged role, even if the policy names "unverified"
        self.assertFalse(g.check(Identity(None, "alice", False, "tcp-loopback"), "estop.clear")[0])


def api(policy=POLICY, state=None, robotics=True, identify=None):
    c = cfg(robotics=robotics, governor_policy=policy_file(policy) if policy is not None else "",
            governor_dir=str(state if state is not None else tmpdir()))
    from computer.services.api import make_server
    from computer.services.platform import Platform
    import threading

    class Srv(ApiServer):
        def __init__(self):
            self.sock = str(tmpdir() / "api.sock")
            self.platform = Platform(c)
            self.who = [GUEST]
            self.srv = make_server(self.platform, self.sock, identify=identify or (lambda conn: self.who[0]))
            self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
            self.th.start()
    return Srv()


class TestApiPermissions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = api()
        cls.c = Client(cls.s.sock)

    @classmethod
    def tearDownClass(cls):
        cls.s.close()

    def as_(self, ident, method, path, body=None):
        self.s.who[0] = ident
        return self.c.request(method, path, body)

    def test_reading_and_asking_need_no_role(self):
        for ident in (GUEST, unverified()):
            self.assertEqual(self.as_(ident, "GET", "/v1/health")[0], 200)
            s, d = self.as_(ident, "POST", "/v1/ask", {"question": "what orbits the sun"})
            self.assertEqual(s, 200, d)

    def test_motion_needs_the_robot_role(self):
        for ident in (GUEST, unverified(), who("teach", 5004)):
            for path, body in (("/v1/robot/observations", {"observations": good_obs()}),
                               ("/v1/robot/action", {"goal": [1.0, 1.0]})):
                s, d = self.as_(ident, "POST", path, body)
                self.assertEqual((s, d["error"]["code"]), (403, "FORBIDDEN"), (ident.name, path))
        self.assertEqual(self.as_(ROBOT, "POST", "/v1/robot/observations", {"observations": good_obs()})[0], 200)
        self.assertEqual(self.as_(ROBOT, "POST", "/v1/robot/action", {"goal": [1.0, 1.0]})[0], 200)
        denied = [r for r in self.s.platform.governor.audit.records() if r["outcome"] == "denied"]
        self.assertTrue(any(r["action"] == "robot.command" and r["identity"]["name"] == "guest" for r in denied))
        self.assertTrue(any(r["action"] == "robot.observe" and r["identity"]["verified"] is False for r in denied))

    def test_anyone_engages_only_an_operator_clears(self):
        for ident in (GUEST, unverified(), ROBOT):
            s, d = self.as_(ident, "POST", "/v1/robot/e_stop", {"on": True, "reason": f"{ident.name} saw something"})
            self.assertEqual((s, d["e_stop"]), (200, True))
            s, d = self.as_(ident, "POST", "/v1/robot/e_stop", {"on": False, "reason": "let me go"})
            self.assertEqual((s, d["error"]["code"]), (403, "FORBIDDEN"), ident.name)
        s, d = self.as_(ALICE, "POST", "/v1/robot/e_stop", {"on": False})
        self.assertEqual(s, 403)
        self.assertIn("reason", d["error"]["message"])
        s, d = self.as_(ALICE, "POST", "/v1/robot/e_stop", {"on": False, "reason": "checked the room"})
        self.assertEqual((s, d["e_stop"]), (200, False))
        # the robot now moves, and the record says who allowed it and why
        self.as_(ROBOT, "POST", "/v1/robot/observations", {"observations": good_obs(t=time.monotonic(), obstacles=())})
        recs = self.s.platform.governor.audit.records()
        clear = [r for r in recs if r["action"] == "estop.clear" and r["outcome"] == "cleared"][-1]
        self.assertEqual((clear["identity"]["name"], clear["reason"]), ("alice", "checked the room"))
        denied = [r for r in recs if r["outcome"] == "denied"]
        self.assertTrue(any(r["action"] == "estop.clear" and r["identity"]["name"] == "robbie" for r in denied))
        self.assertTrue(self.s.platform.governor.audit.verify()["ok"])
        self.as_(GUEST, "POST", "/v1/robot/e_stop", {"on": True, "reason": "end of test"})

    def test_whoami_and_status(self):
        s, d = self.as_(ROBOT, "GET", "/v1/governor/whoami")
        self.assertEqual(d["roles"], ["robot"])
        s, d = self.as_(GUEST, "GET", "/v1/governor")
        self.assertEqual(s, 200)
        self.assertEqual(d["policy"]["operators"], ["alice"])
        self.assertIn("governor", self.as_(GUEST, "GET", "/v1/health")[1])

    def test_the_connection_carries_on_after_a_denial(self):
        """A 403 for a POST whose body was never read mustn't break the next request on the connection."""
        import http.client
        import socket

        class Conn(http.client.HTTPConnection):
            def connect(s2):
                s2.sock = socket.socket(socket.AF_UNIX)
                s2.sock.connect(self.s.sock)
        self.s.who[0] = GUEST
        c = Conn("localhost")
        for _ in range(3):
            c.request("POST", "/v1/robot/action", body=json.dumps({"goal": [1, 1]}),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            self.assertEqual(r.status, 403)
            r.read()
        c.request("GET", "/v1/health")
        self.assertEqual(c.getresponse().status, 200)
        c.close()

    def test_no_policy_means_no_operator(self):
        s = api(policy=None)
        try:
            s.who[0] = who("root", 0)
            st, d = Client(s.sock).request("POST", "/v1/robot/e_stop", {"on": False, "reason": "x"})
            self.assertEqual(st, 403)
            self.assertIn("governor_policy", d["error"]["message"])
            st, d = Client(s.sock).request("POST", "/v1/ask", {"question": "what orbits the sun"})
            self.assertEqual(st, 200)
        finally:
            s.close()


@unittest.skipUnless(os.getuid() == 0, "needs root, to connect as a second user")
class TestPeerCredentials(unittest.TestCase):
    def test_the_kernel_says_who_is_calling(self):
        """No identify override: the server reads SO_PEERCRED.  A process running as nobody (65534) is nobody."""
        s = api(policy={"operator": [f"uid:{ME}"]}, identify=None)
        try:
            from computer.governor import peer_identity
            s.srv.RequestHandlerClass.identify = staticmethod(peer_identity)
            os.chmod(s.sock, 0o666)
            os.chmod(Path(s.sock).parent, 0o755)
            os.chmod(TMP.name, 0o755)
            me = Client(s.sock).get("/v1/governor/whoami")
            self.assertEqual((me["identity"]["uid"], me["identity"]["via"], me["roles"]), (ME, "unix-socket", ["operator"]))
            code = ("import json,sys; sys.path.insert(0, %r); from computer.services.client import Client; "
                    "c = Client(%r); print(json.dumps([c.get('/v1/governor/whoami'), "
                    "c.request('POST', '/v1/robot/e_stop', {'on': False, 'reason': 'I am root, honest'})]))"
                    % (str(ROOT), s.sock))
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                               user=65534, group=65534, cwd="/")
            self.assertEqual(r.returncode, 0, r.stderr)
            whoami, (status, body) = json.loads(r.stdout)
            self.assertEqual(whoami["identity"]["uid"], 65534)
            self.assertEqual(whoami["roles"], ["ask", "read"])
            self.assertEqual(status, 403)
            self.assertTrue(s.platform.governor.latch.engaged)
        finally:
            s.close()


# ---------------------------------------------------------------------- 3 the audit log


class TestAuditLog(unittest.TestCase):
    def fresh(self, n=4) -> AuditLog:
        a = AuditLog(tmpdir() / "audit.jsonl")
        for i in range(n):
            a.append({"action": "test", "outcome": "allowed", "i": i})
        return a

    def test_a_clean_log_verifies(self):
        v = self.fresh().verify()
        self.assertTrue(v["ok"], v)
        self.assertEqual((v["lines"], v["head"]["seq"]), (4, 4))

    def test_tampering_is_detected(self):
        def edit(a):
            a.path.write_text(a.path.read_text().replace('"i":1', '"i":9'))

        def drop_middle(a):
            lines = a.path.read_text().splitlines(True)
            a.path.write_text("".join(lines[:1] + lines[2:]))

        def drop_last(a):                                # the chain alone can't see this; the head record can
            a.path.write_text("".join(a.path.read_text().splitlines(True)[:-1]))

        def reorder(a):
            lines = a.path.read_text().splitlines(True)
            a.path.write_text("".join([lines[1], lines[0]] + lines[2:]))

        def drop_head(a):
            a.head_path.unlink()

        def edit_head(a):
            h = json.loads(a.head_path.read_text())
            h["seq"] = 2
            a.head_path.write_text(json.dumps(h))

        def torn(a):
            with open(a.path, "a") as fh:
                fh.write('{"format": "cdos-gov')

        def empty(a):
            a.path.write_text("")
        for f in (edit, drop_middle, drop_last, reorder, drop_head, edit_head, torn, empty):
            with self.subTest(f.__name__):
                a = self.fresh()
                f(a)
                self.assertFalse(a.verify()["ok"], f.__name__)
                if f in (drop_last, drop_head, edit_head, torn, empty):
                    # damage at the end: even an append (a motion's denial, an engage) refuses to write after it
                    with self.assertRaises(GovernorError):
                        a.append({"action": "test", "outcome": "allowed"})

    def test_an_edit_while_running_stops_the_next_privileged_decision(self):
        state = tmpdir()
        g = gov(state=state)
        g.estop_engage(GUEST, "one")
        g.estop_engage(GUEST, "two")
        self.assertFalse(g.degraded)
        (state / "audit.jsonl").write_text((state / "audit.jsonl").read_text().replace('"one"', '"0ne"'))
        with self.assertRaises(Forbidden) as cm:
            g.estop_clear(ALICE, "ready")                # re-verified first: the edit is found
        self.assertIn("degraded", cm.exception.why)
        self.assertTrue(g.degraded)
        self.assertTrue(g.latch.engaged)

    def test_a_crash_between_the_line_and_the_head_is_not_tampering(self):
        a = self.fresh(3)
        old_head = a.head_path.read_bytes()
        a.append({"action": "test", "outcome": "allowed"})
        a.head_path.write_bytes(old_head)               # the power went after the line's fsync
        v = a.verify()
        self.assertTrue(v["ok"], v)
        self.assertTrue(v["notes"])

    def test_two_processes_append_without_breaking_the_chain(self):
        path = tmpdir() / "audit.jsonl"
        code = ("import sys; sys.path.insert(0, %r); from computer.governor import AuditLog\n"
                "a = AuditLog(%r)\nfor i in range(40): a.append({'action': 'test', 'outcome': 'allowed', 'w': sys.argv[1]})"
                % (str(ROOT), str(path)))
        ps = [subprocess.Popen([sys.executable, "-c", code, str(w)]) for w in range(3)]
        for p in ps:
            self.assertEqual(p.wait(timeout=120), 0)
        v = AuditLog(path).verify()
        self.assertTrue(v["ok"], v["breaks"][:3])
        self.assertEqual(v["lines"], 120)


    @unittest.skipUnless(os.getuid() == 0, "needs root")
    def test_an_operator_running_as_root_leaves_the_files_to_the_service(self):
        """sudo ... promote writes the audit as root: the service user must still be able to append afterwards."""
        state = tmpdir()
        os.chown(state, 65534, 65534)
        os.chmod(state, 0o750)
        os.chmod(TMP.name, 0o755)
        os.chmod(state.parent, 0o755)
        g = gov(state=state)
        g.estop_engage(ALICE, "root writes first")
        for f in ("audit.jsonl", "audit.head.json", ".audit.jsonl.lock", "estop.json"):
            self.assertEqual((state / f).stat().st_uid, 65534, f)
        code = ("import sys; sys.path.insert(0, %r); from computer.governor import AuditLog\n"
                "AuditLog(%r).append({'action': 'test', 'outcome': 'allowed'})" % (str(ROOT), str(state / "audit.jsonl")))
        r = subprocess.run([sys.executable, "-c", code], user=65534, group=65534, cwd="/", capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(AuditLog(state / "audit.jsonl").verify()["ok"])


class TestDegraded(unittest.TestCase):
    def test_a_broken_log_stops_privilege_and_motion_until_resealed(self):
        state = tmpdir()
        g = gov(state=state)
        g.estop_clear(ALICE, "ready")
        (state / "audit.jsonl").write_text((state / "audit.jsonl").read_text().replace("ready", "go!!!"))
        s = api(state=state)
        try:
            g2 = s.platform.governor
            self.assertTrue(g2.degraded)
            self.assertTrue(g2.latch.engaged)                       # the clear can't be verified any more
            c = Client(s.sock)
            self.assertEqual(c.request("POST", "/v1/ask", {"question": "what orbits the sun"})[0], 200)
            h = c.get("/v1/health")
            self.assertTrue(h["governor"]["degraded"])
            for ident, path, body in ((ROBOT, "/v1/robot/action", {"goal": [1, 1]}),
                                      (ALICE, "/v1/robot/e_stop", {"on": False, "reason": "please"})):
                s.who[0] = ident
                st, d = c.request("POST", path, body)
                self.assertEqual(st, 403, d)
                self.assertIn("degraded", d["error"]["message"])
            s.who[0] = GUEST
            self.assertEqual(c.request("POST", "/v1/robot/e_stop", {"on": True})[0], 200)   # stopping still works
            with self.assertRaises(Forbidden):
                g2.reseal(ROBOT, "not mine to do")
            out = g2.reseal(ALICE, "line 2 was edited by hand during testing")
            self.assertTrue(Path(out["archived"]).exists())                 # the broken log is kept
            self.assertFalse(g2.degraded)
            self.assertTrue(g2.latch.engaged)
            first = g2.audit.records()[0]
            self.assertEqual((first["action"], first["previous_verify"]["ok"]), ("audit.reseal", False))
            s.who[0] = ALICE
            st, d = c.request("POST", "/v1/robot/e_stop", {"on": False, "reason": "resealed and checked"})
            self.assertEqual(st, 200, d)
            self.assertFalse(gov(state=state).latch.engaged)
        finally:
            s.close()


# ---------------------------------------------------------------------- 1 again, with real processes


class TestRestart(unittest.TestCase):
    def test_an_engaged_stop_survives_sigkill(self):
        base = tmpdir()
        conf = base / "config.toml"
        sock = str(base / "api.sock")
        conf.write_text("\n".join([f'corpus = "{DATA}"', 'llm_mode = "off"', 'llm_endpoint = ""', 'log_file = ""',
                                   "robotics = true", f'robotics_journal = "{base / "journal.jsonl"}"',
                                   f'governor_policy = "{policy_file({"operator": [f"uid:{ME}"], "robot": [f"uid:{ME}"]})}"',
                                   f'governor_dir = "{base / "governor"}"']) + "\n")

        def start():
            p = subprocess.Popen([sys.executable, "-m", "computer.services.api", "--config", str(conf), "--socket", sock],
                                 cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            cl = Client(sock, timeout=2)
            for _ in range(120):
                try:
                    cl.get("/v1/health")
                    return p, cl
                except (OSError, RuntimeError, ValueError):
                    time.sleep(0.25)
            p.kill()
            self.fail(p.stderr.read().decode())

        def kill(p):
            p.send_signal(signal.SIGKILL)
            p.wait(timeout=10)
            p.stderr.close()

        def move(cl):
            cl.post("/v1/robot/observations", {"observations": good_obs(t=1.0, obstacles=())})
            return cl.post("/v1/robot/action", {"goal": [3.0, 0.0]})

        p, cl = start()
        try:
            self.assertFalse(move(cl)["allowed"])                         # first start: engaged
            cl.post("/v1/robot/e_stop", {"on": False, "reason": "bench test"})
            self.assertTrue(move(cl)["allowed"])
            cl.post("/v1/robot/e_stop", {"on": True, "reason": "hand in the way"})
            kill(p)
            p, cl = start()
            d = move(cl)                                                  # 0.2 allowed this
            self.assertFalse(d["allowed"])
            self.assertIn("e_stop", {v["rule_id"] for v in d["violations"]})
            self.assertIn("hand in the way", cl.get("/v1/governor")["latch"]["why"])
            cl.post("/v1/robot/e_stop", {"on": False, "reason": "clear again"})
            kill(p)
            p, cl = start()
            self.assertTrue(move(cl)["allowed"])                          # and a recorded clear persists too
        finally:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=10)
                p.stderr.close()
        self.assertTrue(AuditLog(base / "governor" / "audit.jsonl").verify()["ok"])


# ---------------------------------------------------------------------- 3 + 4 approvals through the commands


@unittest.skipUnless(HAVE_NUMPY, "needs numpy (a trained model)")
class TestApprovals(unittest.TestCase):
    def setUp(self):
        self.reg = tmpdir() / "reg"
        shutil.copytree(trained_registry(), self.reg)
        self.state = tmpdir()
        self.active = json.loads((self.reg / "ACTIVE.json").read_text())["model_id"]

    def run_cli(self, module, policy, *args):
        conf = tmpdir() / "config.toml"
        lines = [f'governor_dir = "{self.state}"']
        if policy is not None:
            lines.append(f'governor_policy = "{policy_file(policy)}"')
        conf.write_text("\n".join(lines) + "\n")
        cmd = ([sys.executable, "-m", module, "--config", str(conf), *args] if module == "computer.growth" else
               [sys.executable, "-m", module, *args, "--config", str(conf)])     # growth: --config before the command
        r = subprocess.run(cmd, cwd=str(ROOT),
                           capture_output=True, text=True, timeout=300)
        return r

    def active_now(self):
        return json.loads((self.reg / "ACTIVE.json").read_text())["model_id"]

    def test_only_a_named_operator_with_a_reason_changes_the_active_model(self):
        args = ["deactivate", "--registry", str(self.reg), "--approve"]
        for policy, reason, why in ((None, "r", "governor_policy"),
                                    ({"operator": ["someone-else"]}, "r", "not granted"),
                                    ({"teacher": [f"uid:{ME}"]}, "r", "not granted"),
                                    ({"operator": [f"uid:{ME}"]}, "", "reason")):
            with self.subTest(policy=policy, reason=reason):
                r = self.run_cli("computer.neural", policy, *args, "--reason", reason)
                self.assertEqual(r.returncode, 4, r.stderr)
                self.assertIn(why, r.stderr)
                self.assertEqual(self.active_now(), self.active)
        dry = self.run_cli("computer.neural", None, "deactivate", "--registry", str(self.reg))
        self.assertEqual(dry.returncode, 3, dry.stderr)                     # the dry run needs no authority
        op = {"operator": [f"uid:{ME}"]}
        r = self.run_cli("computer.neural", op, *args, "--reason", "testing a rollback path")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(self.active_now())
        r = self.run_cli("computer.neural", op, "rollback", "--registry", str(self.reg), "--to", self.active,
                         "--approve", "--reason", "restore after the test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.active_now(), self.active)

        # the registry's own audit names who approved, and points into the Governor's log
        promos = [json.loads(l) for l in (self.reg / "promotions.jsonl").read_text().splitlines()]
        self.assertEqual(promos[0]["approved_by"], {"unattributed": "called without a Governor decision "
                                                                    "(library use, not the CLI)"})
        for p in promos[1:]:
            self.assertEqual(p["approved_by"]["approver"]["uid"], ME)
            self.assertTrue(p["approved_by"]["reason"])
        audit = AuditLog(self.state / "audit.jsonl")
        self.assertTrue(audit.verify()["ok"])
        recs = audit.records()
        by_seq = {r["seq"]: r for r in recs}
        self.assertEqual(promos[-1]["approved_by"]["governor_hash"], by_seq[promos[-1]["approved_by"]["governor_seq"]]["hash"])
        results = [r for r in recs if r.get("decision_seq")]
        self.assertEqual([r["outcome"] for r in results], ["deactivated", "activated"])
        for r in results:
            dec = by_seq[r["decision_seq"]]
            self.assertEqual((dec["hash"], dec["outcome"]), (r["decision_hash"], "allowed"))
        self.assertTrue(results[-1]["details"]["manifest_sha256"].startswith("sha256:"))
        denied = [r for r in recs if r["outcome"] == "denied"]
        self.assertEqual(len(denied), 4)                         # every refusal above is on record
        self.assertTrue(all(r["identity"]["uid"] == ME for r in recs))

    def test_a_refused_promotion_is_recorded_as_refused(self):
        r = self.run_cli("computer.neural", {"operator": [f"uid:{ME}"]}, "promote", "--registry", str(self.reg),
                         "--model", "nc1-does-not-exist", "--approve", "--reason", "try it")
        self.assertEqual(r.returncode, 2, r.stderr)
        last = AuditLog(self.state / "audit.jsonl").records()[-1]
        self.assertTrue(last["outcome"].startswith("refused: no staged model"), last)

    def test_growth_answers_need_a_teacher(self):
        from computer.growth.engine import Growth
        g = Growth(self.reg, DATA)
        g.create_tasks(g.discover(replay=False) or [])
        args = ["--registry", str(self.reg), "--corpus", str(DATA), "answer", "t-none", "--question", "q",
                "--gold", "a", "--by", "Me"]
        r = self.run_cli("computer.growth", {"robot": [f"uid:{ME}"]}, *args)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("growth.answer", r.stderr)
        r = self.run_cli("computer.growth", {"teacher": [f"uid:{ME}"]}, *args)
        self.assertEqual(r.returncode, 2, r.stderr)                          # allowed, then refused: no such task
        last = AuditLog(self.state / "audit.jsonl").records()[-1]
        self.assertTrue(last["outcome"].startswith("refused:"), last)
        r = self.run_cli("computer.growth", {"teacher": [f"uid:{ME}"]}, "--registry", str(self.reg), "run", "t-none")
        self.assertEqual(r.returncode, 4, r.stderr)                          # a teacher doesn't train


if __name__ == "__main__":
    unittest.main()
