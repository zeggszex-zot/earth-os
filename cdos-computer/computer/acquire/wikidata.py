"""Collect selected Wikidata entities from the API into one snapshot file, which is then approved like any download.

    python3 -m computer.acquire collect wikidata        (resumable: run it again after an interruption)
    python3 -m computer.acquire pin wikidata            (prints the snapshot's sha256 for a person to approve)
    python3 -m computer.acquire run wikidata            (parses only the approved snapshot; no network)

Which entities (plan.json, fixed when a collection starts):
  * legacy: every Q-id the master's legacy Wikidata rows were taken from (source_id "Q<n>/...");
  * linked: the items whose GeoNames id (P1566) is one of the places the latest staged GeoNames run has, found by
    Wikidata Query Service lookups, 500 GeoNames ids a query.

Requests, one at a time, never in parallel, at least MIN_INTERVAL apart, User-Agent from sources.toml:
  1. links      query.wikidata.org/sparql, POST, 500 GeoNames ids each           collect/<source>/links/NNNNN.json
  2. entities   wbgetentities, 50 ids each, maxlag=5, English labels/descriptions, claims, sitelinks
                                                                                   collect/<source>/entities/NNNNN.json.gz
  3. labels     wbgetentities props=labels, 50 ids each: every item a kept statement points at (values, units)
                that isn't itself collected                                       collect/<source>/labels/NNNNN.json.gz
  4. snapshot   collect/<source>/snapshot.jsonl.gz: labels, then each entity trimmed to the mapped properties, with
                its revision id (lastrevid) and its ENTITY=; gzip with no timestamp, so the same answers give the
                same bytes.

Every batch is written whole (temporary file, then rename) after its answer has been checked: every id asked for
is answered (an entity, a redirect, or "missing").  A batch on disk is never asked again, so an interrupted
collection resumes at the first missing batch.  maxlag errors wait as long as the server says (Retry-After);
429/5xx and network errors back off 10 s, 20 s, ... up to 10 minutes, MAX_TRIES times, and then stop the
collection (FetchInterrupted: run it again later).  Any other error stops it (FetchRejected).

Wikimedia publishes no checksum for API answers.  The snapshot's sha256 is what a person approves; each entity's
revision id says exactly which version was read (Special:EntityData/Q<n>.json?revision=<id> fetches it again).
"""

from __future__ import annotations

import contextlib
import gzip
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from computer.governor import sha256_file, utc

from . import AcquireError
from .fetch import NET_ERRORS, TIMEOUT, FetchInterrupted, FetchRejected
from .registry import Source

MIN_INTERVAL = 1.0                 # seconds between the starts of two requests
MAX_TRIES = 8                      # 429/5xx/network failures of one request before the collection stops
MAX_LAG_WAITS = 30                 # maxlag answers to one request before the collection stops
MAX_ANSWER = 64 << 20              # bytes of one decompressed answer
LINKS_PER_QUERY = 500
QID = re.compile(r"^Q[1-9][0-9]*$")
LEGACY_ID = re.compile(r"^(Q[1-9][0-9]*)/")
GN_ENTITY = re.compile(r"ENTITY=gn:([0-9]+)")
KEPT_EXTRA = ("P1566",)            # read for the link to GeoNames; never a row


def _qnum(q: str) -> int:
    return int(q[1:])


def _gz(obj) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as g:
        g.write((json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n").encode())
    return buf.getvalue()


def _read_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


class Collector:
    def __init__(self, root: Path, src: Source, master: Path, runs_of, journal, keep, opener=None,
                 clock=time.monotonic, sleep=time.sleep, wall=time.time, log=print):
        """keep(src, relative path, bytes): Fetcher.keep, the one way this writes a file (under collect/<source>/)."""
        if not src.collect:
            raise AcquireError(f"source {src.id!r} has no collect table: it isn't collected from an API")
        self.src, self.c = src, src.collect
        self.root = Path(root)
        self.dir = self.root / "collect" / src.id
        self.master, self.runs_of, self.journal, self.keep = Path(master), runs_of, journal, keep
        self.open = opener or (lambda req: urllib.request.urlopen(req, timeout=TIMEOUT))
        self.clock, self.sleep, self.wall, self.log = clock, sleep, wall, log
        self.props = sorted({k.split("_", 1)[0] for k in src.relations if re.match(r"^P[0-9]+", k)} | set(KEPT_EXTRA),
                            key=lambda p: int(p[1:]))
        self.last_start = None
        self.requests = 0

    # ---- the plan ------------------------------------------------------------------------------------------------

    def _inputs(self) -> dict:
        runs = self.runs_of(self.c["geonames"])
        if not runs:
            raise AcquireError(f"no staged run of {self.c['geonames']!r}: the GeoNames places to link aren't known")
        run = runs[-1]
        d = self._run_dir(run)
        return {"master_sha256": sha256_file(self.master), "geonames_run": run["run"],
                "geonames_delta_sha256": sha256_file(d / "delta.pf")}

    def _run_dir(self, run: dict) -> Path:
        return self.root / "staging" / self.c["geonames"] / run["run"]

    def plan(self, new: bool = False) -> dict:
        p = self.dir / "plan.json"
        inputs = self._inputs()
        if p.exists() and not new:
            plan = json.loads(p.read_text(encoding="utf-8"))
            if plan["inputs"] != inputs:
                raise AcquireError(f"the collection in {self.dir} was planned from other inputs ({plan['inputs']}; "
                                   f"now {inputs}); finish it as it is by restoring them, or start again with --new")
            return plan
        if new and self.dir.exists():
            old = self.dir.with_name(f"{self.src.id}.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(self.wall()))}")
            os.replace(self.dir, old)
            self.log(f"the previous collection is kept as {old}")
        legacy = set()
        stype, sid_re = self.c["legacy"]["source_type"], re.compile(self.c["legacy"]["source_id"])
        with open(self.master, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                c = line.rstrip("\n").split("\t")
                if len(c) > 8 and c[7] == stype and sid_re.match(c[8]):
                    m = LEGACY_ID.match(c[8])
                    if m:
                        legacy.add(m.group(1))
        run_dir = self._run_dir(self.runs_of(self.c["geonames"])[-1])
        gn = set()
        for name in ("delta.pf", "held.pf"):
            if (run_dir / name).exists():
                with open(run_dir / name, encoding="utf-8") as fh:
                    for line in fh:
                        gn.update(GN_ENTITY.findall(line))
        plan = {"format": "cdos-wikidata-plan/1", "made_at": utc(self.wall()), "inputs": inputs,
                "properties": self.props, "legacy": sorted(legacy, key=_qnum), "geonames": sorted(gn, key=int)}
        self.keep(self.src, "plan.json", (json.dumps(plan, indent=0) + "\n").encode())
        self.journal.append({"event": "planned", "source": self.src.id, "legacy": len(legacy), "geonames": len(gn),
                             **inputs})
        return plan

    # ---- one request -------------------------------------------------------------------------------------------

    def _request(self, url: str, data: bytes | None = None, accept: str = "application/json") -> bytes:
        tries = lag_waits = 0
        while True:
            if self.last_start is not None:
                wait = self.last_start + MIN_INTERVAL - self.clock()
                if wait > 0:
                    self.sleep(wait)
            self.last_start = self.clock()
            self.requests += 1
            headers = {"User-Agent": self.c["user_agent"], "Accept-Encoding": "gzip", "Accept": accept}
            if data is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            retry_after = None
            try:
                with contextlib.closing(self.open(urllib.request.Request(url, data=data, headers=headers))) as r:
                    raw = r.read(MAX_ANSWER + 1)
                    if len(raw) > MAX_ANSWER:
                        raise FetchRejected(f"an answer over {MAX_ANSWER:,} bytes from {url[:80]}")
                    if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as g:
                            raw = g.read(MAX_ANSWER + 1)
                        if len(raw) > MAX_ANSWER:
                            raise FetchRejected(f"an answer over {MAX_ANSWER:,} bytes, decompressed, from {url[:80]}")
                    retry_after = r.headers.get("Retry-After")
                    if accept == "application/json":
                        err = (json.loads(raw).get("error") or {}) if raw[:1] == b"{" else {}
                        if err.get("code") == "maxlag":
                            lag_waits += 1
                            if lag_waits > MAX_LAG_WAITS:
                                raise FetchInterrupted(f"the database lagged {MAX_LAG_WAITS} times in a row; "
                                                       "run collect again later")
                            self.sleep(self._seconds(retry_after, 5))
                            continue
                        if err:
                            raise FetchRejected(f"the API refused: {err.get('code')}: {str(err.get('info'))[:200]}")
                    return raw
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504):
                    raise FetchRejected(f"HTTP {e.code} {e.reason} from {url[:80]}") from None
                retry_after, why = e.headers.get("Retry-After") if e.headers else None, f"HTTP {e.code}"
            except (*NET_ERRORS, EOFError, gzip.BadGzipFile) as e:       # a cut-off answer is tried again
                why = str(e)[:200] or type(e).__name__
            except ValueError as e:                     # an answer that isn't JSON
                raise FetchRejected(f"an answer that isn't JSON from {url[:80]}: {e}") from None
            tries += 1
            if tries >= MAX_TRIES:
                self.journal.append({"event": "interrupted", "source": self.src.id, "why": why})
                raise FetchInterrupted(f"{why} {tries} times in a row; run collect again later (finished batches "
                                       "are kept)")
            self.sleep(self._seconds(retry_after, min(600, 10 * 2 ** (tries - 1))))

    @staticmethod
    def _seconds(header, default: float) -> float:
        try:
            return max(1.0, min(3600.0, float(header)))
        except (TypeError, ValueError):
            return float(default)

    # ---- the steps ---------------------------------------------------------------------------------------------

    def _batches(self, kind: str, ids: list, size: int, fetch, check) -> None:
        d = self.dir / kind
        total = (len(ids) + size - 1) // size
        for i in range(total):
            out = d / f"{i:05d}.json.gz"
            part = ids[i * size:(i + 1) * size]
            if out.exists():
                got = _read_gz(out)
                if got.get("asked") == part:
                    continue
                raise AcquireError(f"{out} answers other ids than the plan asks; the plan changed under it")
            answer = fetch(part)
            check(part, answer)
            self.keep(self.src, f"{kind}/{out.name}", _gz({"asked": part, "answer": answer, "at": utc(self.wall())}))
            if i % 50 == 0 or i == total - 1:
                self.log(f"{kind}: {i + 1}/{total} batches")

    def _sparql(self, gn_ids: list[str]) -> dict:
        values = " ".join(f'"{g}"' for g in gn_ids)
        q = f"SELECT ?item ?gn WHERE {{ VALUES ?gn {{ {values} }} ?item wdt:P1566 ?gn . }}"
        raw = self._request(self.c["sparql"], urllib.parse.urlencode({"query": q}).encode(),
                            accept="application/sparql-results+json")
        return json.loads(raw)

    @staticmethod
    def _check_sparql(asked, answer):
        if "results" not in answer or "bindings" not in answer["results"]:
            raise FetchRejected("a query answer without results")

    def _wbget(self, ids: list[str], props: str) -> dict:
        q = {"action": "wbgetentities", "format": "json", "ids": "|".join(ids), "props": props, "languages": "en",
             "maxlag": str(self.c["maxlag"])}
        if "sitelinks" in props:
            q["sitefilter"] = ""
        return json.loads(self._request(self.c["api"] + "?" + urllib.parse.urlencode(q)))

    @staticmethod
    def _check_entities(asked, answer):
        ents = answer.get("entities")
        if not isinstance(ents, dict):
            raise FetchRejected("an answer without entities")
        answered = set(ents) | {(e.get("redirects") or {}).get("from") for e in ents.values()}
        missing = [q for q in asked if q not in answered]
        if missing:
            raise FetchRejected(f"ids asked for and not answered: {missing[:5]}")

    def links(self, plan) -> dict[str, list[str]]:
        self._batches("links", plan["geonames"], LINKS_PER_QUERY, self._sparql, self._check_sparql)
        by_gn: dict[str, set] = {}
        for f in sorted((self.dir / "links").glob("*.json.gz")):
            for b in _read_gz(f)["answer"]["results"]["bindings"]:
                q = b["item"]["value"].rsplit("/", 1)[-1]
                if QID.match(q):
                    by_gn.setdefault(b["gn"]["value"], set()).add(q)
        return {g: sorted(qs, key=_qnum) for g, qs in by_gn.items()}

    def entity_ids(self, plan, links) -> list[str]:
        return sorted(set(plan["legacy"]) | {qs[0] for qs in links.values() if len(qs) == 1}, key=_qnum)

    def entities(self, ids) -> None:
        self._batches("entities", ids, self.c["batch"],
                      lambda part: self._wbget(part, "info|labels|descriptions|claims|sitelinks"),
                      self._check_entities)

    def _collected(self):
        """Every collected entity (redirects followed: an entity is kept once, under the id it has now)."""
        seen = set()
        for f in sorted((self.dir / "entities").glob("*.json.gz")):
            for key, e in sorted(_read_gz(f)["answer"]["entities"].items()):
                q = e.get("id", key)
                if "missing" in e or q in seen:
                    continue
                seen.add(q)
                yield q, e

    def _trim(self, e) -> dict:
        claims = {}
        for p in self.props:
            kept = []
            for s in e.get("claims", {}).get(p, []):
                snak = s.get("mainsnak", {})
                kept.append({"rank": s.get("rank"), "type": snak.get("datatype"),
                             "value": snak.get("datavalue", {}).get("value") if snak.get("snaktype") == "value" else None,
                             "qualifiers": {k: [x.get("datavalue", {}).get("value") for x in v
                                                if x.get("snaktype") == "value"]
                                            for k, v in (s.get("qualifiers") or {}).items()
                                            if k in ("P580", "P582", "P585")}})
            if kept:
                claims[p] = kept
        return {"id": e["id"], "rev": e.get("lastrevid"), "modified": e.get("modified"),
                "label": (e.get("labels", {}).get("en") or {}).get("value", ""),
                "description": (e.get("descriptions", {}).get("en") or {}).get("value", ""),
                "sitelinks": len(e.get("sitelinks") or {}), "claims": claims}

    @staticmethod
    def _targets(trimmed) -> set[str]:
        out = set()
        for stmts in trimmed["claims"].values():
            for s in stmts:
                v = s["value"]
                if isinstance(v, dict):
                    if v.get("id") and QID.match(v["id"]):
                        out.add(v["id"])
                    unit = str(v.get("unit", "")).rsplit("/", 1)[-1]
                    if QID.match(unit):
                        out.add(unit)
        return out

    def labels(self) -> list[str]:
        have, want = set(), set()
        for q, e in self._collected():
            t = self._trim(e)
            have.add(q)
            want |= self._targets(t)
        ids = sorted(want - have, key=_qnum)
        self._batches("labels", ids, self.c["batch"], lambda part: self._wbget(part, "labels"), self._check_entities)
        return ids

    def snapshot(self, plan, links) -> dict:
        labels = {}
        for f in sorted((self.dir / "labels").glob("*.json.gz")):
            for q, e in _read_gz(f)["answer"]["entities"].items():
                lab = (e.get("labels", {}).get("en") or {}).get("value")
                if lab:
                    labels[q] = lab
        planned_gn = set(plan["geonames"])
        one_item = {g: qs[0] for g, qs in links.items() if len(qs) == 1}
        entities, redirects, counts = [], [], {"entities": 0, "linked_to_geonames": 0, "geonames_ids_with_several_items":
                                               sum(1 for qs in links.values() if len(qs) > 1)}
        for f in sorted((self.dir / "entities").glob("*.json.gz")):
            for q, e in _read_gz(f)["answer"]["entities"].items():
                if (e.get("redirects") or {}).get("from"):
                    redirects.append({"type": "redirect", "from": e["redirects"]["from"], "to": q})
        for q, e in self._collected():
            t = self._trim(e)
            if t["label"]:
                labels.setdefault(q, t["label"])
            # the one GeoNames id of this item that is a staged place and names only this item (Paris has two
            # GeoNames ids, one of them a staged place)
            gns = {s["value"] for s in t["claims"].get("P1566", []) if s["rank"] != "deprecated" and s["value"]}
            mine = [g for g in gns if g in planned_gn and one_item.get(g) == q]
            t["entity"] = f"gn:{mine[0]}" if len(mine) == 1 else f"wd:{q}"
            counts["linked_to_geonames"] += t["entity"].startswith("gn:")
            entities.append(t)
        entities.sort(key=lambda t: _qnum(t["id"]))
        counts["entities"], counts["labels"], counts["redirects"] = len(entities), len(labels), len(redirects)
        out = self.dir / "snapshot.jsonl.gz"
        tmp = out.with_suffix(".part")
        with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as g:
            def put(obj):
                g.write((json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n").encode())
            put({"type": "snapshot", "format": "cdos-wikidata-snapshot/1", "plan_inputs": plan["inputs"],
                 "properties": plan["properties"], "counts": counts})
            for q in sorted(labels, key=_qnum):
                put({"type": "label", "id": q, "label": labels[q]})
            for r in sorted(redirects, key=lambda r: _qnum(r["from"])):
                put(r)
            for t in entities:
                put({"type": "entity", **t})
        os.replace(tmp, out)
        sha = sha256_file(out)
        self.journal.append({"event": "collected", "source": self.src.id, "sha256": sha, **counts})
        return {"snapshot": str(out), "sha256": sha, "size": out.stat().st_size, "counts": counts,
                "requests_this_run": self.requests}

    def collect(self, new: bool = False) -> dict:
        plan = self.plan(new)
        links = self.links(plan)
        ids = self.entity_ids(plan, links)
        self.log(f"entities to collect: {len(ids):,} ({len(plan['legacy']):,} legacy, "
                 f"{sum(1 for qs in links.values() if len(qs) == 1):,} linked to GeoNames)")
        self.entities(ids)
        self.labels()
        return self.snapshot(plan, links)
