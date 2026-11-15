"""Download one source's file: resume, limits, verification.  The verified file is the only thing a run reads.

    downloads/<source>/<file>.part              in progress; resumed with Range: bytes=<size>-
    downloads/<source>/<sha8>/<file>            verified (size and sha256), renamed into place only after the check
    downloads/<source>/fetch.json               the last verified download: url, sha256, size, ETag, Last-Modified
    quarantine/<source>/<file>.<UTC>            a download that failed its checksum or size limit; never parsed

  * A server that ignores Range (200 instead of 206, or a 206 that starts elsewhere) restarts the file from 0:
    bytes are never appended to the wrong offset.
  * An interrupted transfer keeps the .part file and raises FetchInterrupted; the next run resumes it.
  * Unchanged upstream (304 to If-None-Match / If-Modified-Since, or a registry sha256 already verified on disk) is
    not downloaded again.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from computer.governor import sha256_file, utc, write_atomic

from . import AcquireError
from .registry import Source

CHUNK = 1 << 16
CHECKSUM_MAX = 1 << 16
TIMEOUT = 60
USER_AGENT = "cdos-acquire/0.1"
NET_ERRORS = (urllib.error.URLError, http.client.HTTPException, ConnectionError, socket.timeout, TimeoutError)


class FetchInterrupted(AcquireError):
    """The transfer stopped part-way; the .part file is kept for the next run."""


class FetchRejected(AcquireError):
    """The download failed verification (checksum, size) and was quarantined."""


@dataclass
class Fetched:
    path: Path
    sha256: str
    size: int
    changed: bool          # False: the verified copy on disk is still current
    record: dict


class Bucket:
    """Token bucket: at most `rate` bytes per second, on average, from the first byte."""

    def __init__(self, rate: int, clock=time.monotonic, sleep=time.sleep):
        self.rate, self.clock, self.sleep = rate, clock, sleep
        self.start, self.sent = None, 0

    def take(self, n: int) -> None:
        if self.rate <= 0:
            return
        if self.start is None:
            self.start = self.clock()
        self.sent += n
        ahead = self.sent / self.rate - (self.clock() - self.start)
        if ahead > 0:
            self.sleep(ahead)


class Fetcher:
    def __init__(self, root: Path, journal, opener=None, clock=time.monotonic, sleep=time.sleep, wall=time.time):
        self.root = Path(root)
        self.journal = journal
        self.open = opener or (lambda req: urllib.request.urlopen(req, timeout=TIMEOUT))
        self.clock, self.sleep, self.wall = clock, sleep, wall

    def _dir(self, src: Source) -> Path:
        return self.root / "downloads" / src.id

    def last(self, src: Source) -> dict | None:
        p = self._dir(src) / "fetch.json"
        if not p.exists():
            return None
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return rec if Path(rec.get("path", "")).is_file() else None

    def _request(self, url: str, headers: dict) -> urllib.request.Request:
        return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})

    def expected_sha(self, src: Source) -> str:
        if src.sha256:
            return src.sha256
        try:
            with contextlib.closing(self.open(self._request(src.checksum_url, {}))) as r:
                text = r.read(CHECKSUM_MAX + 1)
        except NET_ERRORS as e:
            raise FetchInterrupted(f"{src.id}: the checksum file can't be fetched: {e}") from None
        if len(text) > CHECKSUM_MAX:
            raise FetchRejected(f"{src.id}: the checksum file is larger than {CHECKSUM_MAX} bytes")
        found = []
        for line in text.decode("utf-8", "replace").splitlines():
            m = re.match(r"^\s*([0-9a-fA-F]{64})(?:\s+\*?(\S+))?\s*$", line)
            if m and (m.group(2) in (None, src.filename) or Path(m.group(2)).name == src.filename):
                found.append(m.group(1).lower())
        if len(set(found)) != 1:
            raise FetchRejected(f"{src.id}: the checksum file has {len(set(found))} sha256 values for "
                                f"{src.filename}, not exactly one")
        return "sha256:" + found[0]

    def _quarantine(self, src: Source, part: Path, why: str) -> Path:
        q = self.root / "quarantine" / src.id
        q.mkdir(parents=True, exist_ok=True)
        dst = q / f"{src.filename}.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(self.wall()))}"
        os.replace(part, dst)
        (dst.with_name(dst.name + ".why")).write_text(why + "\n", encoding="utf-8")
        self.journal.append({"event": "quarantined", "source": src.id, "file": dst.name, "why": why})
        return dst

    def keep(self, src: Source, rel: str, data: bytes) -> Path:
        """An API collection's file (computer/acquire/wikidata.py), written whole under collect/<source>/ and nowhere
        else: the collector itself can't write files any other way."""
        base = (self.root / "collect" / src.id).resolve()
        path = (base / rel).resolve()
        if base not in path.parents:
            raise AcquireError(f"{src.id}: {rel!r} isn't inside {base}")
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, data)
        return path

    def fetch_all(self, src: Source) -> dict[str, Fetched]:
        """Every file of the source, verified: {"": Fetched} for a single url, else {input name: Fetched}."""
        if src.unpinned:
            raise FetchRejected(f"{src.id}: {', '.join(src.unpinned)} not pinned: run `python3 -m computer.acquire "
                                f"pin {src.id}`, check what it fetched, and copy the sha256 values into sources.toml")
        return {name: self.fetch(t) for name, t in src.targets().items()}

    def pin(self, src: Source) -> dict[str, dict]:
        """Download every file of the source without parsing it, and say what was fetched: size, sha256, date.
        A person approves a file by copying its sha256 into sources.toml; the run then uses exactly this file."""
        return {name: self.fetch(t, pin=True).record for name, t in src.targets().items()}

    def _pinned(self, src: Source, expected: str) -> Fetched | None:
        """A file fetched by `pin` whose sha256 a person has since approved: moved into place, not fetched again."""
        p = self._dir(src) / "pins.json"
        try:
            pin = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        path = Path(pin.get("path", ""))
        if pin.get("sha256") != expected or pin.get("url") != src.url or not path.is_file():
            return None
        if sha256_file(path) != expected:
            return None
        return self._accept(src, path, expected, pin.get("etag", ""), pin.get("last_modified", ""), "pinned")

    def _accept(self, src: Source, part: Path, sha: str, etag: str, modified: str, how: str) -> Fetched:
        d = self._dir(src)
        final_dir = d / sha[7:15]
        final_dir.mkdir(exist_ok=True)
        final = final_dir / src.filename
        os.replace(part, final)
        rec = {"url": src.url, "path": str(final), "sha256": sha, "size": final.stat().st_size, "etag": etag,
               "last_modified": modified, "at": utc(self.wall())}
        write_atomic(d / "fetch.json", (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode())
        self.journal.append({"event": "fetched", "source": src.id, "sha256": sha, "size": rec["size"], "how": how})
        return Fetched(final, sha, rec["size"], True, rec)

    def fetch(self, src: Source, pin: bool = False) -> Fetched:
        d = self._dir(src)
        d.mkdir(parents=True, exist_ok=True)
        last = None if pin else self.last(src)
        if not pin:
            if last and src.sha256 and last["sha256"] == src.sha256 and last.get("url") == src.url:
                return Fetched(Path(last["path"]), last["sha256"], last["size"], False, last)
            expected = self.expected_sha(src)
            if last and last["sha256"] == expected and last.get("url") == src.url:
                return Fetched(Path(last["path"]), last["sha256"], last["size"], False, last)
            pinned = self._pinned(src, expected)
            if pinned:
                return pinned

        part = d / (src.filename + (".pin.part" if pin else ".part"))
        have = part.stat().st_size if part.exists() else 0
        headers = {}
        if have:
            headers["Range"] = f"bytes={have}-"
        elif last and last.get("url") == src.url:
            if last.get("etag"):
                headers["If-None-Match"] = last["etag"]
            if last.get("last_modified"):
                headers["If-Modified-Since"] = last["last_modified"]
        try:
            resp = self.open(self._request(src.url, headers))
        except urllib.error.HTTPError as e:
            if e.code == 304 and last:
                return Fetched(Path(last["path"]), last["sha256"], last["size"], False, last)
            if e.code == 416 and have:
                part.unlink()                         # the .part doesn't fit what the server has: start again
                raise FetchInterrupted(f"{src.id}: the server refused the resume (416); the next run starts over")
            raise FetchInterrupted(f"{src.id}: HTTP {e.code} {e.reason}") from None
        except NET_ERRORS as e:
            raise FetchInterrupted(f"{src.id}: {e}") from None

        with contextlib.closing(resp):
            status = getattr(resp, "status", 200)
            mode, start = "wb", 0                      # a 200 is the whole file: start the .part again from 0
            if status == 206:
                m = re.match(r"bytes (\d+)-", resp.headers.get("Content-Range", ""))
                if not (have and m and int(m.group(1)) == have):
                    with contextlib.suppress(FileNotFoundError):
                        part.unlink()
                    raise FetchInterrupted(f"{src.id}: the server sent a part that doesn't continue the download; "
                                           "the next run starts over")
                mode, start = "ab", have
            length = resp.headers.get("Content-Length")
            total = start + int(length) if length and length.isdigit() else None
            if total is not None and total > src.max_bytes:
                raise FetchRejected(f"{src.id}: the server says {total:,} bytes, over max_bytes {src.max_bytes:,}")
            bucket, got = Bucket(src.bandwidth, self.clock, self.sleep), start
            try:
                with open(part, mode) as fh:
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        got += len(chunk)
                        if got > src.max_bytes:
                            fh.close()
                            self._quarantine(src, part, f"over max_bytes {src.max_bytes:,}")
                            raise FetchRejected(f"{src.id}: more than max_bytes {src.max_bytes:,}; quarantined")
                        fh.write(chunk)
                        bucket.take(len(chunk))
                    fh.flush()
                    os.fsync(fh.fileno())
            except NET_ERRORS as e:
                self.journal.append({"event": "interrupted", "source": src.id, "bytes": got, "why": str(e)[:200]})
                raise FetchInterrupted(f"{src.id}: stopped after {got:,} bytes ({e}); the next run resumes") from None
            if total is not None and got != total:
                raise FetchInterrupted(f"{src.id}: got {got:,} of {total:,} bytes; the next run resumes")
            etag, modified = resp.headers.get("ETag", ""), resp.headers.get("Last-Modified", "")

        sha = sha256_file(part)
        if pin:
            held = d / "unpinned" / sha[7:15]
            held.mkdir(parents=True, exist_ok=True)
            dst = held / src.filename
            os.replace(part, dst)
            rec = {"url": src.url, "path": str(dst), "sha256": sha, "size": dst.stat().st_size, "etag": etag,
                   "last_modified": modified, "at": utc(self.wall()), "approved": False}
            write_atomic(d / "pins.json", (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode())
            self.journal.append({"event": "pinned", "source": src.id, "sha256": sha, "size": rec["size"],
                                 "last_modified": modified})
            return Fetched(dst, sha, rec["size"], True, rec)
        if sha != expected:
            q = self._quarantine(src, part, f"sha256 {sha} != expected {expected}")
            raise FetchRejected(f"{src.id}: the download doesn't match its sha256; quarantined as {q.name}")
        return self._accept(src, part, sha, etag, modified, f"downloaded (resumed from {start})")
