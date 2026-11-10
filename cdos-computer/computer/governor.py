"""The Governor: who may make CDOS do what, the emergency-stop latch, and the record of every privileged decision.

The neural core and the language model propose; the deterministic engine checks claims; the robot's safety envelope
checks motions.  None of them decides *who is allowed to act*.  That is this module, and only this module:

    Policy        who holds which role (a JSON file an administrator owns, e.g. /etc/cdos-computer/governor.json)
    Identity      who is asking, and how we know: a Unix-socket peer's uid (SO_PEERCRED, from the kernel), the uid of
                  a local command, or nothing at all (TCP on loopback: unverified)
    Governor      authorize(identity, permission, ...) -> Decision; every privileged decision and every denial is
                  written to the audit log before anything happens
    AuditLog      append-only, hash-chained JSONL with a separate head record; verified at start-up
    Latch         the emergency stop, persisted; engaged unless a verified record says an operator cleared it

Permissions and roles
---------------------
    read      api.read, estop.engage
    ask       read + api.ask
    robot     ask + robot.observe, robot.command
    service   ask + growth.train                       (the growth timer: stage and evaluate, never promote)
    teacher   ask + growth.answer                      (a person's answer becomes training data)
    operator  everything: estop.clear, model.promote, model.rollback, model.deactivate, growth.*, audit.reseal

    estop.engage is open to every caller, verified or not: stopping can't make things worse.

Fail closed
-----------
  * A privileged permission (anything beyond read/ask) needs a verified identity, a policy that loaded, a role that
    grants it, and an audit log that verified at start-up and can be written now.  Any of those missing: denied, with
    the reason.  With no policy configured, nobody is an operator.
  * The latch is engaged when its file is missing, unreadable, edited, of another format, or says "cleared" without
    a matching estop.clear entry in a verified audit log.  Editing estop.json alone can't release the robot.
  * A broken audit log (an edited, removed or reordered line, or a head record that doesn't match) puts the Governor
    in a degraded state: read and ask still work, every privileged action and every motion is refused, and the latch
    stays engaged, until an operator reseals the log (python3 -m computer.governor audit reseal --reason ...).

What this is not
----------------
  * Not hardware safety.  The latch makes the controller command STOP; it can't remove motor power.  A physical robot
    needs an independent interlock (an e-stop circuit, a microcontroller with its own limits) that no software here
    can override, and nothing here may ever be wired to override it.
  * Not proof against the service account.  The audit log and latch live in the state folder the service writes; the
    chain, the head record and the latch-audit cross-check catch accidents, truncation and single-file edits, not a
    process that rewrites all of them consistently.  The policy file is the strong part: it belongs to root.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:                       # not Linux: no cross-process lock (single-process use only)
    fcntl = None

POLICY_FORMAT = "cdos-governor-policy/1"
AUDIT_FORMAT = "cdos-governor-audit/1"
HEAD_FORMAT = "cdos-governor-audit-head/1"
LATCH_FORMAT = "cdos-estop-latch/1"
GENESIS = "sha256:" + "0" * 64

PERMISSIONS = {
    "api.read": "health, capabilities, components, robot state, growth tasks, governor status",
    "api.ask": "ask a question; call a component",
    "robot.observe": "submit robot observations",
    "robot.command": "request a robot motion (the safety envelope still checks it)",
    "estop.engage": "engage the emergency stop (open to every caller)",
    "estop.clear": "clear the emergency stop",
    "model.promote": "activate a staged, evaluated model",
    "model.rollback": "re-activate an earlier model",
    "model.deactivate": "run without a model",
    "growth.answer": "give a person's answer to a growth task (it becomes training data)",
    "growth.train": "train and evaluate a candidate into staging (never promotes)",
    "audit.reseal": "archive a broken audit log and start a new one",
}
_READ = {"api.read", "estop.engage"}
_ASK = _READ | {"api.ask"}
ROLES = {
    "read": frozenset(_READ),
    "ask": frozenset(_ASK),
    "robot": frozenset(_ASK | {"robot.observe", "robot.command"}),
    "service": frozenset(_ASK | {"growth.train"}),
    "teacher": frozenset(_ASK | {"growth.answer"}),
    "operator": frozenset(PERMISSIONS),
}
OPEN = frozenset({"estop.engage"})                    # anyone, verified or not
BASIC = frozenset({"api.read", "api.ask"})            # may come from the policy's default / unverified roles
DEFAULT_ROLES_ALLOWED = ("read", "ask")               # what an unlisted or unverified caller may be given, at most
MOTION = frozenset({"robot.observe", "robot.command"})


class GovernorError(RuntimeError):
    pass


class Forbidden(GovernorError):
    """Denied.  `why` says which check failed; `decision` is the audit record of the denial, if one was written."""

    def __init__(self, why: str, decision: dict | None = None):
        super().__init__(why)
        self.why, self.decision = why, decision


def utc(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def record_hash(rec: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical({k: v for k, v in rec.items() if k != "hash"}).encode()).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def match_owner(path: Path) -> None:
    """Root (an operator running a command) writes files in the service's folder: give them to the folder's owner,
    so the service can still append to the log, take the lock and replace the head and the latch."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    with contextlib.suppress(OSError):
        st = path.parent.stat()
        if st.st_uid != 0:
            os.chown(path, st.st_uid, st.st_gid)


def write_atomic(path: Path, data: bytes) -> None:
    """Write, fsync, rename, fsync the folder: after a power cut the file is the old one or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    match_owner(tmp)
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


# ---------------------------------------------------------------------- identity


@dataclass(frozen=True)
class Identity:
    uid: int | None
    name: str
    verified: bool
    via: str                              # unix-socket | local-process | tcp-loopback | ...
    pid: int | None = None
    hint: str = ""                        # e.g. SUDO_USER: recorded, never trusted

    def to_json(self) -> dict:
        d = {"name": self.name, "uid": self.uid, "verified": self.verified, "via": self.via}
        if self.pid is not None:
            d["pid"] = self.pid
        if self.hint:
            d["hint"] = self.hint
        return d


def user_name(uid: int) -> str:
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return f"uid:{uid}"


def from_uid(uid: int, via: str, pid: int | None = None, hint: str = "") -> Identity:
    return Identity(int(uid), user_name(int(uid)), True, via, pid, hint)


def unverified(via: str = "tcp-loopback") -> Identity:
    return Identity(None, "unverified", False, via)


def local_identity() -> Identity:
    """The user running this process, as the kernel says.  SUDO_USER is kept as a hint (root can set it to anything)."""
    hint = os.environ.get("SUDO_USER", "") if os.getuid() == 0 else ""
    return from_uid(os.getuid(), "local-process", os.getpid(), f"sudo by {hint}" if hint else "")


def peer_identity(sock) -> Identity:
    """A Unix-socket peer's uid and pid from the kernel (SO_PEERCRED).  Anything else (TCP): unverified."""
    import socket
    import struct
    try:
        if sock.family != socket.AF_UNIX:
            return unverified()
        pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                            struct.calcsize("3i")))
        return from_uid(uid, "unix-socket", pid)
    except (OSError, AttributeError, struct.error):
        return unverified("unix-socket (no peer credentials)")


# ---------------------------------------------------------------------- policy


@dataclass
class Policy:
    roles: dict                            # role -> set of principals ("alice", "uid:1000")
    default: tuple = ("read", "ask")        # roles of a verified caller the policy doesn't name
    unverified: tuple = ("read", "ask")     # roles of a caller whose identity isn't known (TCP)
    fingerprint: str = ""
    path: str = ""

    @classmethod
    def from_json(cls, d: dict, path: str = "") -> "Policy":
        if not isinstance(d, dict) or d.get("format") != POLICY_FORMAT:
            raise GovernorError(f"a policy file is {POLICY_FORMAT}")
        unknown = set(d) - {"format", "version", "roles", "default", "unverified", "comment"}
        if unknown:
            raise GovernorError(f"unknown keys: {', '.join(sorted(unknown))}")
        roles = d.get("roles")
        if not isinstance(roles, dict):
            raise GovernorError("roles must be an object of role -> list of users")
        out = {}
        for role, who in roles.items():
            if role not in ROLES:
                raise GovernorError(f"unknown role {role!r} (roles: {', '.join(ROLES)})")
            if not isinstance(who, list) or not all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in who):
                raise GovernorError(f"{role}: a list of user names or uids")
            out[role] = {principal(x) for x in who}
        lists = {}
        for k in ("default", "unverified"):
            v = d.get(k, ["read", "ask"])
            if not isinstance(v, list) or any(r not in DEFAULT_ROLES_ALLOWED for r in v):
                raise GovernorError(f"{k} may only hold {', '.join(DEFAULT_ROLES_ALLOWED)}: anything more needs a name")
            lists[k] = tuple(v)
        fp = "sha256:" + hashlib.sha256(canonical(d).encode()).hexdigest()
        return cls(out, lists["default"], lists["unverified"], fp, path)

    @classmethod
    def load(cls, path) -> "Policy":
        p = Path(path)
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise GovernorError(f"{p}: {e}") from None
        return cls.from_json(d, str(p))

    def roles_of(self, ident: Identity) -> set:
        if not ident.verified:
            return set(self.unverified)
        names = {ident.name, f"uid:{ident.uid}"}
        mine = {role for role, who in self.roles.items() if who & names}
        return mine or set(self.default)


def principal(x) -> str:
    if isinstance(x, int):
        return f"uid:{x}"
    x = x.strip()
    return f"uid:{int(x)}" if x.isdigit() else x


# ---------------------------------------------------------------------- audit log


@contextlib.contextmanager
def _locked(lock_path: Path):
    if fcntl is None:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o660)
    match_owner(lock_path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _last_line(path: Path) -> tuple[bytes, bool]:
    """(the last complete line, whether the file ends without a newline)."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        if end == 0:
            return b"", False
        size = min(end, 1 << 20)
        fh.seek(end - size)
        tail = fh.read(size)
    torn = not tail.endswith(b"\n")
    lines = tail.split(b"\n")
    complete = lines[:-1]                  # the piece after the last newline is empty, or torn
    complete = [l for l in complete if l.strip()]
    return (complete[-1] if complete else b""), torn


class AuditLog:
    """Append-only, hash-chained, fsynced, one writer at a time (flock).  A head record (audit.head.json) holds the
    last line's seq and hash, so removing lines from the end is detected too.  path=None: kept in memory."""

    def __init__(self, path: Path | None, clock=time.time):
        self.path = Path(path) if path else None
        self.clock = clock
        self.memory: list[dict] = []
        self.head_path = self.path.with_name(self.path.stem + ".head.json") if self.path else None
        self.lock_path = self.path.with_name("." + self.path.name + ".lock") if self.path else None

    @property
    def persistent(self) -> bool:
        return self.path is not None

    def _read_head(self) -> dict | None:
        if not self.head_path.exists():
            return None
        try:
            h = json.loads(self.head_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise GovernorError(f"the audit head record is unreadable: {e}") from None
        if not isinstance(h, dict) or h.get("format") != HEAD_FORMAT or h.get("hash_self") != record_hash(
                {k: v for k, v in h.items() if k != "hash_self"}):
            raise GovernorError("the audit head record is damaged or edited")
        return h

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0, GENESIS
        last, torn = _last_line(self.path)
        if torn:
            raise GovernorError("the audit log ends in a partly written line")
        try:
            rec = json.loads(last)
            return int(rec["seq"]), str(rec["hash"])
        except (ValueError, KeyError, TypeError):
            raise GovernorError("the audit log's last line is damaged") from None

    def append(self, entry: dict) -> dict:
        """Write one decision.  Raises GovernorError if it can't be written exactly where the chain says it goes."""
        t = self.clock()
        if self.path is None:
            prev = self.memory[-1]["hash"] if self.memory else GENESIS
            rec = {"format": AUDIT_FORMAT, "seq": len(self.memory) + 1, "prev": prev, "t": utc(t), **entry}
            rec["hash"] = record_hash(rec)
            self.memory.append(rec)
            return rec
        with _locked(self.lock_path):
            seq, prev = self._tail()
            head = self._read_head()
            if head is None and seq != 0:
                raise GovernorError("the audit log has lines but no head record")
            if head is not None and (head["seq"], head["hash"]) != (seq, prev):
                raise GovernorError(f"the audit log ends at {seq} but its head record says {head['seq']}: "
                                    "lines were removed or added outside the Governor")
            rec = {"format": AUDIT_FORMAT, "seq": seq + 1, "prev": prev, "t": utc(t), **entry}
            rec["hash"] = record_hash(rec)
            line = (canonical(rec) + "\n").encode("utf-8")
            with open(self.path, "ab") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            match_owner(self.path)
            self._write_head(rec["seq"], rec["hash"])
        return rec

    def _write_head(self, seq: int, h: str) -> None:
        head = {"format": HEAD_FORMAT, "seq": seq, "hash": h, "log": self.path.name}
        head["hash_self"] = record_hash(head)
        write_atomic(self.head_path, (json.dumps(head, sort_keys=True) + "\n").encode())

    def records(self) -> list[dict]:
        if self.path is None:
            return list(self.memory)
        out = []
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    with contextlib.suppress(ValueError):
                        out.append(json.loads(raw))
        return out

    def find(self, seq: int) -> dict | None:
        for rec in self.records():
            if rec.get("seq") == seq:
                return rec
        return None

    def verify(self) -> dict:
        """Walk the chain and compare its end with the head record.  ok is False at the first problem."""
        out = {"path": str(self.path) if self.path else None, "lines": 0, "ok": True, "breaks": [], "head": None,
               "notes": []}
        if self.path is None:
            prev = GENESIS
            for i, rec in enumerate(self.memory, 1):
                if rec.get("hash") != record_hash(rec) or rec.get("prev") != prev or rec.get("seq") != i:
                    out["breaks"].append({"line": i, "why": "edited"})
                prev = rec.get("hash")
            out["lines"] = len(self.memory)
            out["ok"] = not out["breaks"]
            return out
        prev, seq = GENESIS, 0
        if self.path.exists():
            with open(self.path, "rb") as fh:
                data = fh.read()
            if data and not data.endswith(b"\n"):
                out["breaks"].append({"line": data.count(b"\n") + 1, "why": "the last line is only partly written"})
                data = data[: data.rfind(b"\n") + 1]
            for n, raw in enumerate(data.decode("utf-8", "replace").split("\n"), 1):
                if not raw.strip():
                    continue
                out["lines"] += 1
                try:
                    rec = json.loads(raw)
                except ValueError:
                    out["breaks"].append({"line": n, "why": "not JSON (damaged)"})
                    prev, seq = "sha256:broken", -1
                    continue
                why = ""
                if rec.get("format") != AUDIT_FORMAT:
                    why = f"format {rec.get('format')!r}"
                elif rec.get("hash") != record_hash(rec):
                    why = "content doesn't match its hash (edited)"
                elif rec.get("prev") != prev:
                    why = "doesn't follow the line before (a line was removed, reordered or edited)"
                elif rec.get("seq") != seq + 1:
                    why = f"sequence {rec.get('seq')} after {seq}"
                if why:
                    out["breaks"].append({"line": n, "why": why})
                prev = rec.get("hash", "")
                seq = rec.get("seq") if isinstance(rec.get("seq"), int) else -1
        try:
            head = self._read_head()
        except GovernorError as e:
            out["breaks"].append({"line": 0, "why": str(e)})
            head = None
        out["head"] = {"seq": seq, "hash": prev} if out["lines"] else None
        if head is None and out["lines"] and not any(b["line"] == 0 for b in out["breaks"]):
            out["breaks"].append({"line": 0, "why": "the log has lines but no head record"})
        elif head is not None:
            if (head["seq"], head["hash"]) == (seq, prev):
                pass
            elif head["seq"] == seq - 1 and self._line_hash_at(head["seq"]) == head["hash"]:
                # the last line was written and fsynced, then the power went before the head was: consistent
                out["notes"].append(f"the head record lagged one line (seq {seq}); a crash between the two writes")
            else:
                out["breaks"].append({"line": 0, "why": f"the log ends at seq {seq}, its head record says "
                                                        f"{head['seq']}: lines were removed or added"})
        out["ok"] = not out["breaks"]
        return out

    def _line_hash_at(self, seq: int) -> str | None:
        rec = self.find(seq)
        return rec.get("hash") if rec else None

    def reseal(self, entry: dict) -> tuple[dict, str]:
        """Move a broken log aside (it is kept, never deleted) and start a new chain whose first line names it."""
        with _locked(self.lock_path):
            archived = ""
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock()))
            if self.path.exists():
                dest = self.path.with_name(f"{self.path.stem}.{stamp}.broken{self.path.suffix}")
                n = 1
                while dest.exists():
                    dest = self.path.with_name(f"{self.path.stem}.{stamp}-{n}.broken{self.path.suffix}")
                    n += 1
                os.replace(self.path, dest)
                archived = str(dest)
            if self.head_path.exists():
                os.replace(self.head_path, self.head_path.with_name(self.head_path.name + f".{stamp}.broken"))
            prev_sha = sha256_file(archived) if archived else None
            rec = {"format": AUDIT_FORMAT, "seq": 1, "prev": GENESIS, "t": utc(self.clock()),
                   **entry, "previous_log": archived or None, "previous_sha256": prev_sha}
            rec["hash"] = record_hash(rec)
            with open(self.path, "ab") as fh:
                fh.write((canonical(rec) + "\n").encode())
                fh.flush()
                os.fsync(fh.fileno())
            match_owner(self.path)
            self._write_head(1, rec["hash"])
        return rec, archived


# ---------------------------------------------------------------------- the emergency-stop latch


class Latch:
    """The emergency stop, persisted.  Engaged unless proven cleared: see the module docstring."""

    def __init__(self, path: Path | None, audit: AuditLog, audit_ok: bool, clock=time.time):
        self.path = Path(path) if path else None
        self.audit, self.clock = audit, clock
        self.error = ""
        self.reload(audit_ok)

    def reload(self, audit_ok: bool) -> None:
        """Read the record again (after an audit reseal).  The same object, so the robot controller holding it sees
        the result."""
        self.engaged, self.why, self.record = True, "", None
        self._load(audit_ok)

    @property
    def persistent(self) -> bool:
        return self.path is not None

    def _load(self, audit_ok: bool) -> None:
        if self.path is None:
            self.why = "engaged at start: the latch is kept in memory only (set governor_dir to persist it)"
            return
        if not self.path.exists():
            self.why = "engaged: no latch record yet; an operator clears it once the robot is ready"
            return
        try:
            rec = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.why = f"engaged: the latch record is unreadable ({e})"
            return
        if not isinstance(rec, dict) or rec.get("format") != LATCH_FORMAT:
            self.why = f"engaged: the latch record isn't {LATCH_FORMAT}"
            return
        if rec.get("hash") != record_hash(rec):
            self.why = "engaged: the latch record was edited (its hash doesn't match)"
            return
        if not isinstance(rec.get("engaged"), bool):
            self.why = "engaged: the latch record has no engaged flag"
            return
        self.record = rec
        if rec["engaged"]:
            self.why = f"engaged since {rec.get('changed_at')} by {rec.get('by', {}).get('name', '?')}: " \
                       f"{rec.get('reason', '')}"
            return
        # "cleared" must be backed by the audit log: an estop.clear decision with this seq and hash.
        if not audit_ok:
            self.why = "engaged: the latch says cleared, but the audit log didn't verify"
            return
        entry = self.audit.find(rec.get("audit_seq")) if isinstance(rec.get("audit_seq"), int) else None
        if entry is None or entry.get("hash") != rec.get("audit_hash"):
            self.why = "engaged: the latch says cleared, but the audit log has no matching estop.clear decision"
            return
        if entry.get("action") != "estop.clear" or entry.get("outcome") != "cleared":
            self.why = "engaged: the audit entry the latch points to isn't an operator's clear"
            return
        self.engaged = False
        self.why = f"cleared at {rec.get('changed_at')} by {rec.get('by', {}).get('name', '?')} " \
                   f"(audit seq {entry['seq']}): {rec.get('reason', '')}"

    def _write(self, engaged: bool, by: dict, reason: str, audit_rec: dict | None) -> None:
        if self.path is None:
            return
        rec = {"format": LATCH_FORMAT, "engaged": engaged, "changed_at": utc(self.clock()), "by": by, "reason": reason,
               "audit_seq": audit_rec["seq"] if audit_rec else None,
               "audit_hash": audit_rec["hash"] if audit_rec else None}
        rec["hash"] = record_hash(rec)
        write_atomic(self.path, (json.dumps(rec, sort_keys=True, indent=1) + "\n").encode())
        self.record = rec

    def engage(self, by: dict, reason: str, audit_rec: dict | None = None) -> None:
        """In memory first (it takes effect now, whatever happens to the disk), then on disk, best effort."""
        self.engaged = True
        self.why = f"engaged by {by.get('name', '?')}: {reason}"
        try:
            self._write(True, by, reason, audit_rec)
            self.error = ""
        except OSError as e:
            self.error = f"engaged in memory, but the latch file wasn't written: {e}"

    def clear(self, by: dict, reason: str, audit_rec: dict) -> None:
        """On disk first: if the record can't be written, the stop stays engaged."""
        self._write(False, by, reason, audit_rec)
        self.engaged = False
        self.error = ""
        self.why = f"cleared by {by.get('name', '?')} (audit seq {audit_rec['seq']}): {reason}"

    def to_json(self) -> dict:
        return {"engaged": self.engaged, "why": self.why, "persistent": self.persistent,
                "path": str(self.path) if self.path else None, "error": self.error or None}


# ---------------------------------------------------------------------- the Governor


@dataclass
class Decision:
    allowed: bool
    permission: str
    identity: Identity
    roles: tuple
    why: str
    target: str = ""
    reason: str = ""
    record: dict | None = None             # the audit line, when one was written

    @property
    def ref(self) -> dict:
        """What another log (the model registry's audit, a growth answer) keeps to point at this decision."""
        r = {"approver": self.identity.to_json(), "permission": self.permission, "reason": self.reason}
        if self.record:
            r.update(governor_seq=self.record["seq"], governor_hash=self.record["hash"])
        return r


class Governor:
    def __init__(self, policy_path: str | os.PathLike | None = None, state_dir: str | os.PathLike | None = None,
                 clock=time.time):
        self.clock = clock
        self.policy: Policy | None = None
        self.policy_error = ""
        if policy_path:
            try:
                self.policy = Policy.load(policy_path)
            except GovernorError as e:
                self.policy_error = f"policy not loaded: {e}"
        else:
            self.policy_error = "no governor_policy configured: nobody holds a privileged role"
        self.state_dir = Path(state_dir) if state_dir else None
        self.audit = AuditLog(self.state_dir / "audit.jsonl" if self.state_dir else None, clock)
        self.verified = self.audit.verify()
        self.audit_error = "" if self.verified["ok"] else \
            "audit log broken: " + "; ".join(f"line {b['line']}: {b['why']}" for b in self.verified["breaks"][:3])
        self.latch = Latch(self.state_dir / "estop.json" if self.state_dir else None, self.audit,
                           self.verified["ok"], clock)

    @classmethod
    def from_config(cls, cfg, clock=time.time) -> "Governor":
        from computer.config import HERE

        def res(p):
            if not p:
                return None
            q = Path(p)
            return q if q.is_absolute() else HERE / q
        return cls(res(getattr(cfg, "governor_policy", "")), res(getattr(cfg, "governor_dir", "")), clock)

    @property
    def degraded(self) -> bool:
        return bool(self.audit_error)

    # -------------------------------------------------------------- deciding

    def roles(self, ident: Identity) -> set:
        if self.policy is None:                 # unconfigured or unreadable: the basic roles, nothing privileged
            return set(DEFAULT_ROLES_ALLOWED)
        return self.policy.roles_of(ident)

    def check(self, ident: Identity, permission: str) -> tuple[bool, str, tuple]:
        """(allowed, why, roles).  No side effects."""
        if permission not in PERMISSIONS:
            return False, f"unknown permission {permission!r}", ()
        roles = tuple(sorted(self.roles(ident)))
        if permission in OPEN:
            return True, "open to every caller", roles
        granted = any(permission in ROLES[r] for r in roles)
        if permission in BASIC:
            return (granted, "granted" if granted else f"{ident.name} has no role that grants {permission}", roles)
        # privileged, or a motion
        if not ident.verified:
            return False, f"{permission} needs a verified identity (connect over the Unix socket)", roles
        if self.policy is None:
            return False, self.policy_error, roles
        if not granted:
            return False, f"{ident.name} (roles: {', '.join(roles) or 'none'}) is not granted {permission}", roles
        if self.degraded and permission != "audit.reseal":
            return False, f"the Governor is degraded ({self.audit_error}); an operator must reseal the audit log", roles
        return True, "granted", roles

    def reverify(self) -> bool:
        """Walk the whole chain again (before a privileged decision; motions only check the end of the log, on every
        append).  A break found now degrades the Governor exactly as one found at start-up."""
        self.verified = self.audit.verify()
        if not self.verified["ok"]:
            self.audit_error = "audit log broken: " + "; ".join(f"line {b['line']}: {b['why']}"
                                                               for b in self.verified["breaks"][:3])
        return self.verified["ok"]

    def authorize(self, ident: Identity, permission: str, target: str = "", reason: str = "",
                  details: dict | None = None, audit_allowed: bool = True, require_reason: bool = False) -> Decision:
        """Decide, and write the decision down.  A denial is always recorded (best effort) and raised as Forbidden.
        An allowed privileged decision that can't be recorded is a denial: nothing happens without its record."""
        if permission not in BASIC and permission not in MOTION and permission not in OPEN \
                and permission != "audit.reseal" and not self.degraded:
            self.reverify()
        ok, why, roles = self.check(ident, permission)
        reason = (reason or "").strip()
        if ok and require_reason and not reason:
            ok, why = False, f"{permission} needs a reason (--reason)"
        d = Decision(ok, permission, ident, roles, why, target, reason)
        entry = {"action": permission, "outcome": "allowed" if ok else "denied", "why": why, "target": target,
                 "reason": reason, "identity": ident.to_json(), "roles": list(roles),
                 "policy": self.policy.fingerprint if self.policy else None}
        if details:
            entry["details"] = details
        if not ok:
            with contextlib.suppress(GovernorError, OSError):
                d.record = self.audit.append(entry)
            raise Forbidden(why, d.record)
        if audit_allowed:
            try:
                d.record = self.audit.append(entry)
            except (GovernorError, OSError) as e:
                self.audit_error = self.audit_error or f"audit log not writable: {e}"
                raise Forbidden(f"{permission} can't be recorded, so it isn't allowed: {e}") from None
        return d

    def result(self, decision: Decision, outcome: str, details: dict | None = None) -> dict | None:
        """The outcome of an authorized action (activated, refused: ..., failed: ...), linked to its decision."""
        entry = {"action": decision.permission, "outcome": outcome, "target": decision.target,
                 "identity": decision.identity.to_json(),
                 "decision_seq": decision.record["seq"] if decision.record else None,
                 "decision_hash": decision.record["hash"] if decision.record else None}
        if details:
            entry["details"] = details
        try:
            return self.audit.append(entry)
        except (GovernorError, OSError) as e:
            self.audit_error = self.audit_error or f"audit log not writable: {e}"
            return None

    # -------------------------------------------------------------- the emergency stop

    def estop_engage(self, ident: Identity, reason: str = "", source: str = "") -> dict:
        """Always allowed.  Takes effect in memory before anything is written."""
        reason = (reason or "").strip() or "no reason given"
        self.latch.engage(ident.to_json(), reason)
        rec = None
        with contextlib.suppress(GovernorError, OSError):
            rec = self.audit.append({"action": "estop.engage", "outcome": "engaged", "target": source or "robot",
                                     "reason": reason, "identity": ident.to_json(),
                                     "roles": sorted(self.roles(ident))})
        if rec is not None:
            self.latch.engage(ident.to_json(), reason, rec)     # rewrite the file with the audit reference
        return self.latch.to_json()

    def estop_clear(self, ident: Identity, reason: str) -> dict:
        """Operator only, with a reason, with a working audit log.  The record is written before the latch moves."""
        d = self.authorize(ident, "estop.clear", target="robot", reason=reason, require_reason=True,
                           audit_allowed=False)
        try:
            rec = self.audit.append({"action": "estop.clear", "outcome": "cleared", "why": d.why, "target": "robot",
                                     "reason": d.reason, "identity": ident.to_json(), "roles": list(d.roles),
                                     "policy": self.policy.fingerprint if self.policy else None})
        except (GovernorError, OSError) as e:
            self.audit_error = self.audit_error or f"audit log not writable: {e}"
            raise Forbidden(f"the clear can't be recorded, so the stop stays engaged: {e}") from None
        try:
            self.latch.clear(ident.to_json(), d.reason, rec)
        except OSError as e:
            self.result(Decision(True, "estop.clear", ident, d.roles, d.why, "robot", d.reason, rec),
                        f"failed: latch file not written ({e})")
            raise Forbidden(f"the latch file couldn't be written, so the stop stays engaged: {e}") from None
        return self.latch.to_json()

    # -------------------------------------------------------------- operator maintenance

    def reseal(self, ident: Identity, reason: str) -> dict:
        d = self.authorize(ident, "audit.reseal", target=str(self.audit.path), reason=reason, require_reason=True,
                           audit_allowed=False)
        if not self.audit.persistent:
            raise GovernorError("the audit log is in memory: nothing to reseal")
        before = self.audit.verify()
        rec, archived = self.audit.reseal({"action": "audit.reseal", "outcome": "resealed", "reason": d.reason,
                                           "identity": ident.to_json(), "roles": list(d.roles),
                                           "previous_verify": {"ok": before["ok"], "lines": before["lines"],
                                                               "breaks": before["breaks"][:20]}})
        self.verified = self.audit.verify()
        self.audit_error = "" if self.verified["ok"] else "audit log still broken after reseal"
        # A cleared latch pointed into the old log: it can't be verified any more, so the stop is engaged again.
        self.latch.reload(self.verified["ok"])
        return {"resealed": True, "archived": archived, "first": rec["hash"], "latch": self.latch.to_json()}

    def status(self) -> dict:
        return {"policy": {"loaded": self.policy is not None, "path": self.policy.path if self.policy else None,
                           "fingerprint": self.policy.fingerprint if self.policy else None,
                           "error": self.policy_error or None,
                           "operators": sorted(self.policy.roles.get("operator", ())) if self.policy else []},
                "audit": {"persistent": self.audit.persistent, "path": str(self.audit.path) if self.audit.path else None,
                          "ok": not self.degraded, "error": self.audit_error or None,
                          "lines": self.verified["lines"], "head": self.verified["head"],
                          "notes": self.verified.get("notes") or None},
                "degraded": self.degraded,
                "latch": self.latch.to_json()}

    def whoami(self, ident: Identity) -> dict:
        roles = sorted(self.roles(ident))
        return {"identity": ident.to_json(), "roles": roles,
                "may": sorted(p for p in PERMISSIONS if self.check(ident, p)[0])}


def cli_decision(config_path, permission: str, target: str, reason: str, details: dict | None = None):
    """For a command a person runs: (governor, decision) for the user running it, recorded, or Forbidden."""
    from computer.config import load
    gov = Governor.from_config(load(config_path))
    return gov, gov.authorize(local_identity(), permission, target=target, reason=reason, details=details,
                              require_reason=True)


EXAMPLE_POLICY = {
    "format": POLICY_FORMAT,
    "comment": "Who may do what (computer/governor.py).  Names are login names or uid:N.  Keep this file root-owned.",
    "roles": {"operator": ["root"], "robot": ["cdos"], "service": ["cdos"], "teacher": []},
    "default": ["read", "ask"],
    "unverified": ["read", "ask"],
}


# ---------------------------------------------------------------------- python3 -m computer.governor

USAGE = """python3 -m computer.governor <command>: the Governor (computer/governor.py)

    status                      the policy, the audit log and the emergency-stop latch, from the files; and the
                                running service's view, if its socket is reachable
    whoami                      who you are to the Governor, your roles, what you may do
    estop engage [--reason R]   engage the emergency stop on the running service (anyone)
    estop clear --reason R      clear it (an operator; recorded)
    audit verify                walk the audit log's chain and compare its end with the head record
    audit reseal --reason R     an operator archives a broken audit log and starts a new one (restart cdos-api after)
    policy check FILE           check a policy file
    policy example              print an example policy

Paths come from config.toml (governor_policy, governor_dir, api_socket) unless --config names another.
"""


def main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="python3 -m computer.governor", description=USAGE,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config.toml (default: the computer's own)")
    ap.add_argument("--socket", help="the API socket (default: api_socket from the config)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("whoami")
    e = sub.add_parser("estop")
    e.add_argument("what", choices=["engage", "clear"])
    e.add_argument("--reason", default="")
    au = sub.add_parser("audit")
    au.add_argument("what", choices=["verify", "reseal"])
    au.add_argument("--reason", default="")
    po = sub.add_parser("policy")
    po.add_argument("what", choices=["check", "example"])
    po.add_argument("file", nargs="?")
    a = ap.parse_args(argv)

    def say(obj) -> None:
        print(json.dumps(obj, indent=1, sort_keys=True, default=str))

    if a.cmd == "policy":
        if a.what == "example":
            say(EXAMPLE_POLICY)
            return 0
        if not a.file:
            print("policy check FILE", file=sys.stderr)
            return 2
        try:
            p = Policy.load(a.file)
        except GovernorError as err:
            print(f"refused: {err}", file=sys.stderr)
            return 2
        say({"ok": True, "fingerprint": p.fingerprint, "roles": {k: sorted(v) for k, v in p.roles.items()},
             "default": list(p.default), "unverified": list(p.unverified)})
        return 0

    from computer.config import load
    cfg = load(a.config)
    sock = a.socket or getattr(cfg, "api_socket", "")

    def client():
        from computer.services.client import Client
        if not sock:
            raise SystemExit("no api_socket configured (or pass --socket): the stop lives in the running service")
        return Client(sock, timeout=10)

    if a.cmd == "estop":
        try:
            s, d = client().request("POST", "/v1/robot/e_stop", {"on": a.what == "engage", "reason": a.reason})
        except OSError as err:
            print(f"the service isn't reachable at {sock}: {err}", file=sys.stderr)
            return 2
        say(d)
        return 0 if s == 200 else (4 if s == 403 else 2)

    gov = Governor.from_config(cfg)
    if a.cmd == "status":
        out = {"files": gov.status()}
        if sock:
            try:
                s, d = client().request("GET", "/v1/governor")
                out["service"] = d if s == 200 else {"status": s, **d}
            except OSError as err:
                out["service"] = f"not reachable at {sock}: {err}"
        say(out)
        return 0
    if a.cmd == "whoami":
        say(gov.whoami(local_identity()))
        return 0
    if a.cmd == "audit":
        if a.what == "verify":
            v = gov.audit.verify()
            say(v)
            return 0 if v["ok"] else 1
        try:
            say(gov.reseal(local_identity(), a.reason))
        except Forbidden as err:
            print(f"refused by the Governor: {err.why}", file=sys.stderr)
            return 4
        except GovernorError as err:
            print(f"refused: {err}", file=sys.stderr)
            return 2
        return 0
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
