"""The corpus guard: one writer at a time for corpus.pf, a backup before every change, and a record of each one.

corpus.pf is the master the served data/ is built from.  Nothing may change it except through this module, and this
module changes it in exactly one way: os.replace of a complete, fsynced, verified candidate.  Everything else here
exists to make that one step safe and accountable (docs/CORPUS_GROWTH_AUDIT.md, phase 1).

Beside corpus.pf, named after it:

    corpus.pf.lock            flock: shared for readers (a build), exclusive for the one writer; never deleted
    corpus.pf.manifest.json   size, sha256 and generation of the master; the backups; the last verified build;
                              a pending replace (written before the rename, so a crash between the two is recoverable)
    corpus.pf.journal.jsonl   every write, refusal, backup, restore and adoption, hash-chained (governor.AuditLog)
    backups/                  full copies, each checked against its sha256 after copying

Fail closed
-----------
  * Every write needs: fcntl (not on this platform: refused, never an unlocked write), the lock (held: refused at once,
    naming the holder), a manifest that loads, a journal that verifies, a master whose sha256 is the manifest's (else
    it changed outside the guard, and a person must `adopt` it), and room on the disk for the candidate and a backup.
  * A candidate that fails verify, or a `writing` block that raises, is deleted; the master is byte-identical.
  * A backup that doesn't match its recorded sha256 is never restored.

What this is not: protection against a person or program that ignores the lock and edits corpus.pf directly.  The
guard notices that (the master no longer matches the manifest) and refuses to write over it; it can't prevent it.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from pathlib import Path

from computer.governor import AuditLog, GovernorError, match_owner, record_hash, sha256_file, utc, write_atomic

try:
    import fcntl
except ImportError:                       # not Linux: no lock, so no writing at all
    fcntl = None

MANIFEST_FORMAT = "cdos-corpus-manifest/1"
KEEP = 5
SPACE_MARGIN = 1.10                       # free space needed: (candidate + one backup) x this


class GuardError(RuntimeError):
    """Base: the guard refused, and `str(e)` says why."""


class GuardBusy(GuardError):
    """The lock is held by someone else."""


class GuardRefused(GuardError):
    """A precondition for writing doesn't hold (the master changed outside the guard, the manifest or journal is
    damaged, not enough space, no fcntl, ...)."""


class VerifyFailed(GuardError):
    """The candidate failed verify; the master was not touched.  `result` is what verify returned."""

    def __init__(self, why: str, result: dict | None = None):
        super().__init__(why)
        self.result = result or {}


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _copy_synced(src: Path, dst: Path) -> None:
    with open(src, "rb") as a, open(dst, "wb") as b:
        shutil.copyfileobj(a, b, 1 << 20)
        b.flush()
        os.fsync(b.fileno())
    match_owner(dst)


class CorpusGuard:
    def __init__(self, pf, keep: int = KEEP, clock=time.time, free_bytes=None):
        self.pf = Path(pf).resolve()
        self.dir = self.pf.parent
        self.lock_path = self.dir / (self.pf.name + ".lock")
        self.manifest_path = self.dir / (self.pf.name + ".manifest.json")
        self.journal = AuditLog(self.dir / (self.pf.name + ".journal.jsonl"), clock)
        self.backup_dir = self.dir / "backups"
        self.keep = max(1, int(keep))
        self.clock = clock
        self.free_bytes = free_bytes or (lambda p: shutil.disk_usage(p).free)
        self._hook = lambda stage: None   # tests: simulate a crash at "after_pending" or "after_replace"

    # -- state ---------------------------------------------------------------------------------------------------
    @property
    def guarded(self) -> bool:
        return self.lock_path.exists()

    def _temp_prefix(self) -> str:
        return f".{self.pf.name}.new-"

    def _read_manifest(self) -> dict | None:
        if not self.manifest_path.exists():
            return None
        try:
            m = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise GuardRefused(f"the manifest is unreadable: {e}") from None
        if (not isinstance(m, dict) or m.get("format") != MANIFEST_FORMAT
                or m.get("hash_self") != record_hash({k: v for k, v in m.items() if k != "hash_self"})):
            raise GuardRefused("the manifest is damaged, edited or of another format; it was left as it is")
        return m

    def _write_manifest(self, m: dict) -> None:
        m = {k: v for k, v in m.items() if k != "hash_self"}
        m["format"] = MANIFEST_FORMAT
        m["hash_self"] = record_hash(m)
        write_atomic(self.manifest_path, (json.dumps(m, indent=1, sort_keys=True) + "\n").encode("utf-8"))

    def _note(self, event: str, by: str, reason: str, **details) -> dict:
        try:
            return self.journal.append({"event": event, "by": by, "reason": reason, "corpus": self.pf.name, **details})
        except GovernorError as e:
            raise GuardRefused(f"the journal can't be written: {e}") from None

    def status(self, check_backups: bool = True) -> dict:
        """Read-only, takes no lock.  ok is True when the master matches the manifest and nothing needs a person."""
        out = {"corpus": str(self.pf), "guarded": self.guarded, "exists": self.pf.exists(), "problems": [],
               "holder": None, "temp_files": sorted(p.name for p in self.dir.glob(self._temp_prefix() + "*"))}
        if out["exists"]:
            out["size"], out["sha256"] = self.pf.stat().st_size, sha256_file(self.pf)
        try:
            m = self._read_manifest()
        except GuardRefused as e:
            m = None
            out["problems"].append(str(e))
        else:
            if m is None:
                out["problems"].append("no manifest: the corpus is not guarded yet (init)")
        if m is not None:
            out.update(generation=m["generation"], manifest_sha256=m["sha256"], pending=m.get("pending"),
                       verified=(m.get("verified") or {}).get("sha256"))
            if out.get("sha256") != m["sha256"]:
                out["problems"].append("the master doesn't match the manifest: changed outside the guard"
                                       + (" (or a replace was interrupted: the next writer completes it)"
                                          if m.get("pending") else ""))
            backups = []
            for b in m.get("backups", []):
                p = self.backup_dir / b["id"]
                state = "missing" if not p.exists() else (
                    ("ok" if sha256_file(p) == b["sha256"] else "DAMAGED") if check_backups else "present")
                if state in ("missing", "DAMAGED"):
                    out["problems"].append(f"backup {b['id']} is {state.lower()}")
                backups.append({**b, "state": state})
            out["backups"] = backups
        jv = self.journal.verify()
        out["journal_ok"] = jv["ok"]
        if not jv["ok"]:
            out["problems"].append("the journal doesn't verify: " + "; ".join(b["why"] for b in jv["breaks"][:3]))
        if self.lock_path.exists():
            with contextlib.suppress(OSError, ValueError):
                txt = self.lock_path.read_text(encoding="utf-8").strip()
                out["holder"] = json.loads(txt) if txt else None
        if out["temp_files"]:
            out["problems"].append("leftover candidate files (an interrupted write); the next writer removes them")
        out["ok"] = not out["problems"]
        return out

    # -- locks ---------------------------------------------------------------------------------------------------
    def _need_fcntl(self) -> None:
        if fcntl is None:
            raise GuardRefused("no fcntl on this platform: the corpus can't be locked, so it isn't written")

    @contextlib.contextmanager
    def _flock(self, exclusive: bool, create: bool, by: str = "", reason: str = ""):
        self._need_fcntl()
        flags = os.O_RDWR | (os.O_CREAT if create else 0)
        try:
            fd = os.open(self.lock_path, flags, 0o660)
        except FileNotFoundError:
            raise GuardRefused("the corpus is not guarded (no lock file); run init first") from None
        try:
            match_owner(self.lock_path)
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError:
                holder = ""
                with contextlib.suppress(OSError):
                    holder = self.lock_path.read_text(encoding="utf-8").strip()
                raise GuardBusy(f"{self.pf.name} is locked by another "
                                f"{'process' if holder else 'reader (a build)'}"
                                + (f": {holder}" if holder else "") + "; try again when it finishes") from None
            if exclusive:
                info = json.dumps({"pid": os.getpid(), "by": by, "reason": reason, "since": utc(self.clock())})
                os.ftruncate(fd, 0)
                os.pwrite(fd, info.encode("utf-8"), 0)
            try:
                yield
            finally:
                if exclusive:
                    with contextlib.suppress(OSError):
                        os.ftruncate(fd, 0)
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def reading(self):
        """Shared lock for the length of a read (a build): readers share it, a writer can't take it meanwhile."""
        with self._flock(exclusive=False, create=False):
            yield self.pf

    # -- writing -------------------------------------------------------------------------------------------------
    def _need_space(self, nbytes: int, what: str) -> None:
        need = int(nbytes * SPACE_MARGIN)
        free = self.free_bytes(self.dir)
        if free < need:
            raise GuardRefused(f"not enough free space for {what}: {free:,} bytes free, {need:,} needed")

    def _recover(self, m: dict, sha: str) -> dict:
        """A replace interrupted between the rename and the manifest: finish it or drop it, by the master's hash."""
        p = m.get("pending")
        if not p:
            return m
        if sha == p["sha256"]:
            m.update(sha256=p["sha256"], size=p["size"], generation=p["generation"], updated=p["at"],
                     by=p["by"], reason=p["reason"], verified=p.get("verified") or m.get("verified"))
            m["pending"] = None
            self._write_manifest(m)
            self._note("recovered", p["by"], "a replace interrupted after the rename was completed",
                       generation=m["generation"], sha256=sha)
        elif sha == m["sha256"]:
            m["pending"] = None
            self._write_manifest(m)
            self._note("recovered", p["by"], "a replace interrupted before the rename was dropped",
                       generation=m["generation"], sha256=sha)
        return m

    @contextlib.contextmanager
    def writing(self, by: str, reason: str, _adopting: bool = False):
        """The one writer.  Yields a Writer; leaving the block without commit() discards the candidate."""
        if not by or not reason:
            raise GuardRefused("a write needs `by` (who) and `reason` (why)")
        with self._flock(exclusive=True, create=False, by=by, reason=reason):
            jv = self.journal.verify()
            if not jv["ok"]:
                raise GuardRefused("the journal doesn't verify, so nothing is written: "
                                   + "; ".join(b["why"] for b in jv["breaks"][:3]))
            m = self._read_manifest()
            if m is None:
                raise GuardRefused("no manifest: the corpus is not guarded yet (init)")
            sha = sha256_file(self.pf)
            m = self._recover(m, sha)
            if sha != m["sha256"] and not _adopting:
                raise GuardRefused(f"{self.pf.name} doesn't match the manifest (it is {sha[:19]}…, the manifest says "
                                   f"{m['sha256'][:19]}…): it changed outside the guard. A person must check it and "
                                   "run adopt before anything else is written")
            for stale in self.dir.glob(self._temp_prefix() + "*"):
                stale.unlink()
                self._note("removed", by, "a candidate left by an interrupted write", file=stale.name)
            self._need_space(2 * self.pf.stat().st_size, "a candidate and a backup")
            w = Writer(self, m, sha, by, reason)
            try:
                yield w
            except BaseException as e:
                if not w.committed and w.tmp.exists():
                    with contextlib.suppress(GuardError):
                        self._note("discarded", by, reason, why=f"{type(e).__name__}: {e}"[:300])
                raise
            finally:
                if not w.committed:
                    with contextlib.suppress(OSError):
                        w.tmp.unlink()

    # -- backups -------------------------------------------------------------------------------------------------
    def _backup(self, m: dict, sha: str, by: str, reason: str) -> dict:
        for b in m.get("backups", []):
            p = self.backup_dir / b["id"]
            if b["sha256"] == sha and p.exists() and p.stat().st_size == self.pf.stat().st_size:
                return b                              # this exact master is already backed up
        self._need_space(self.pf.stat().st_size, "a backup")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        bid = f"{self.pf.name}.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(self.clock()))}-{sha[7:15]}"
        dst = self.backup_dir / bid
        tmp = self.backup_dir / f".{bid}.part"
        try:
            _copy_synced(self.pf, tmp)
            if sha256_file(tmp) != sha:
                raise GuardRefused("the backup copy doesn't match the master; nothing was changed")
            os.replace(tmp, dst)
            _fsync_dir(self.backup_dir)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
        b = {"id": bid, "sha256": sha, "size": dst.stat().st_size, "made": utc(self.clock()),
             "generation": m["generation"]}
        m.setdefault("backups", []).append(b)
        self._write_manifest(m)
        self._note("backup", by, reason, backup=bid, sha256=sha, generation=m["generation"])
        return b

    def _prune(self, m: dict, by: str) -> list[str]:
        backups = m.get("backups", [])
        keep_ids = {b["id"] for b in backups[-self.keep:]}
        vsha = (m.get("verified") or {}).get("sha256")
        verified = [b for b in backups if b["sha256"] == vsha]
        if verified:
            keep_ids.add(verified[-1]["id"])
        gone = [b for b in backups if b["id"] not in keep_ids]
        if not gone:
            return []
        m["backups"] = [b for b in backups if b["id"] in keep_ids]
        self._write_manifest(m)                       # the manifest forgets them first, then the files go
        for b in gone:
            with contextlib.suppress(FileNotFoundError):
                (self.backup_dir / b["id"]).unlink()
        self._note("pruned", by, f"keep {self.keep}", backups=[b["id"] for b in gone])
        return [b["id"] for b in gone]

    # -- the person's commands -----------------------------------------------------------------------------------
    def init(self, by: str, reason: str) -> dict:
        """Start guarding: the lock file, the first manifest and the first backup.  corpus.pf itself is only read."""
        self._need_fcntl()
        if not by or not reason:
            raise GuardRefused("init needs `by` and `reason`")
        if not self.pf.is_file():
            raise GuardRefused(f"{self.pf} doesn't exist")
        if self.manifest_path.exists() or self.journal.path.exists():
            raise GuardRefused("this corpus is already guarded (a manifest or journal exists); nothing was changed")
        with self._flock(exclusive=True, create=True, by=by, reason=reason):
            sha = sha256_file(self.pf)
            m = {"file": self.pf.name, "generation": 1, "sha256": sha, "size": self.pf.stat().st_size,
                 "updated": utc(self.clock()), "by": by, "reason": reason, "backups": [], "verified": None,
                 "pending": None}
            self._note("init", by, reason, generation=1, sha256=sha)
            self._write_manifest(m)
            self._backup(m, sha, by, "the master as first guarded")
            return m

    def backup(self, by: str, reason: str) -> dict:
        with self.writing(by, reason) as w:
            return self._backup(w.manifest, w.master_sha, by, reason)

    def restore(self, backup_id: str, by: str, reason: str) -> dict:
        """Put a backup back: its hash is checked first, and the current master is backed up before it's replaced."""
        with self.writing(by, reason) as w:
            rec = next((b for b in w.manifest.get("backups", []) if b["id"] == backup_id), None)
            if rec is None:
                raise GuardRefused(f"no backup {backup_id!r} in the manifest")
            src = self.backup_dir / backup_id
            if not src.exists() or sha256_file(src) != rec["sha256"]:
                raise GuardRefused(f"backup {backup_id} is missing or doesn't match its recorded sha256; "
                                   "it is not restored")
            with w.new() as fh, open(src, "rb") as b:
                shutil.copyfileobj(b, fh, 1 << 20)
            return w.commit(event="restored", details={"from_backup": backup_id})

    def adopt(self, by: str, reason: str) -> dict:
        """A person accepts the master as it is now, after it changed outside the guard.  Recorded, never silent."""
        with self.writing(by, reason, _adopting=True) as w:
            m, sha = w.manifest, w.master_sha
            if sha == m["sha256"]:
                return m
            old = m["sha256"]
            m.update(sha256=sha, size=self.pf.stat().st_size, generation=m["generation"] + 1,
                     updated=utc(self.clock()), by=by, reason=reason)
            self._write_manifest(m)
            self._note("adopted", by, reason, generation=m["generation"], sha256=sha, was=old)
            self._backup(m, sha, by, "the adopted master")
            w.committed = True
            return m

    def record_verified(self, result: dict, by: str, reason: str) -> dict:
        """verify() passed on the current master: make it the baseline the next candidate is compared with."""
        with self.writing(by, reason) as w:
            if not result.get("ok"):
                raise VerifyFailed(result.get("why") or "verify failed", result)
            w.manifest["verified"] = {**_verified_record(result), "sha256": w.master_sha, "at": utc(self.clock()),
                                      "generation": w.manifest["generation"]}
            self._write_manifest(w.manifest)
            self._note("verified", by, reason, generation=w.manifest["generation"], sha256=w.master_sha,
                       version=result.get("version"))
            w.committed = True
            return w.manifest

    def prune(self, by: str) -> list[str]:
        with self.writing(by, "prune backups") as w:
            w.committed = True
            return self._prune(w.manifest, by)


def _verified_record(result: dict) -> dict:
    return {k: result.get(k) for k in ("version", "facts", "review") if k in result}


class Writer:
    """What `writing()` yields: write the candidate with new(), then commit(verify)."""

    def __init__(self, guard: CorpusGuard, manifest: dict, master_sha: str, by: str, reason: str):
        self.guard, self.manifest, self.master_sha, self.by, self.reason = guard, manifest, master_sha, by, reason
        self.tmp = guard.dir / f"{guard._temp_prefix()}{os.getpid()}"
        self.committed = False

    @contextlib.contextmanager
    def new(self):
        with open(self.tmp, "wb") as fh:
            yield fh
            fh.flush()
            os.fsync(fh.fileno())
        match_owner(self.tmp)

    def commit(self, verify=None, event: str = "replaced", details: dict | None = None) -> dict:
        """Verify the candidate, back up the master, replace it, record it.  The master changes only at the rename."""
        g, m = self.guard, self.manifest
        if not self.tmp.exists():
            raise GuardRefused("there is no candidate to commit (write it with new() first)")
        sha, size = sha256_file(self.tmp), self.tmp.stat().st_size
        if sha == self.master_sha:
            g._note("unchanged", self.by, self.reason, sha256=sha)
            return {"changed": False, "sha256": sha, "generation": m["generation"]}
        result = None
        if verify is not None:
            result = verify(self.tmp, m.get("verified"))
            if not result.get("ok"):
                g._note("rejected", self.by, self.reason, sha256=sha, why=str(result.get("why", ""))[:500])
                raise VerifyFailed(result.get("why") or "the candidate failed verify; the master is unchanged", result)
        g._need_space(size, "the backup before replacing")
        g._backup(m, self.master_sha, self.by, f"before: {self.reason}")
        gen = m["generation"] + 1
        verified = ({**_verified_record(result), "sha256": sha, "at": utc(g.clock()), "generation": gen}
                    if result is not None else None)
        m["pending"] = {"sha256": sha, "size": size, "generation": gen, "by": self.by, "reason": self.reason,
                        "at": utc(g.clock()), "verified": verified}
        g._write_manifest(m)
        g._hook("after_pending")
        os.replace(self.tmp, g.pf)
        _fsync_dir(g.dir)
        self.committed = True
        g._hook("after_replace")
        m.update(sha256=sha, size=size, generation=gen, updated=m["pending"]["at"], by=self.by, reason=self.reason,
                 verified=verified or m.get("verified"), pending=None)
        g._write_manifest(m)
        g._note(event, self.by, self.reason, generation=gen, sha256=sha, was=self.master_sha, **(details or {}))
        g._prune(m, self.by)
        return {"changed": True, "sha256": sha, "generation": gen, "verified": verified}
