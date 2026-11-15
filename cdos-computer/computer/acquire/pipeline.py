"""One run: an approved source's verified download -> staging/<source>/<run>/.

    parse -> map -> normalise -> validate -> dedupe -> conflicts -> stage

    staging/<source>/<run>/delta.pf          the rows that passed, in the source's order, PF format
                          /rejects.jsonl     one line per rejected record: line, source_id, stage, code, why, raw
                          /conflicts.jsonl   a functional relation given a different value than the master has
                          /held.pf           with hold_homonyms: rows of a name that stands for several things in
                                             this run (ENTITY=), not staged
                          /report.json       counts (read = staged + held + rejected + duplicate), inputs, hashes
    staging/<source>/.<run>.part/            a run in progress; renamed into place only after report.json

Failure is data: a record that can't be parsed, mapped or validated is rejected with its reason, never repaired by
guessing.  Nothing here writes corpus.pf (corpus.integrate is off, Q1).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path

from computer.corpus import key_of
from computer.governor import AuditLog, sha256_file, utc

from .. import pfrow
from .. import qualifiers as Q
from . import DEFAULT_MASTER, DEFAULT_ROOT, INTEGRATE_OFF, AcquireError, registry
from .fetch import Fetcher
from .index import MasterIndex, key_hash
from .parsers import PARSERS

HELD_HEADER = ("# held back by computer.acquire: these names stand for more than one thing in this run (ENTITY=), and "
               "the reader can't yet tell them apart (D4)\n# " + "\t".join(pfrow.COLUMNS) + "\n")
DELTA_HEADER = ("# staged by computer.acquire; not part of the corpus until integrated (off: Q1)\n"
                "# " + "\t".join(pfrow.COLUMNS) + "\n")


def code_fingerprint() -> str:
    here = Path(__file__).parent
    h = hashlib.sha256()
    for p in sorted(here.glob("*.py")) + [Path(pfrow.__file__)]:
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    return "sha256:" + h.hexdigest()


def _decimals(s: str) -> int:
    s = s.strip()
    return len(s.split(".", 1)[1]) if "." in s else 0


def same_value(kind: str, a: str, b: str) -> bool:
    """Equal after normalising: case and spacing for names; numbers to the precision of the less precise one
    (4.0026 and 4.002602 agree; 4.0026 and 4.0031 don't)."""
    if kind == "N":
        try:
            fa, fb = float(a.replace(",", "")), float(b.replace(",", ""))
        except ValueError:
            return key_of(a) == key_of(b)
        d = min(_decimals(a), _decimals(b))
        return round(fa, d) == round(fb, d)
    return key_of(a) == key_of(b)


class Acquirer:
    def __init__(self, root=DEFAULT_ROOT, master=DEFAULT_MASTER, registry_path=None, opener=None,
                 clock=time.monotonic, sleep=time.sleep, wall=time.time):
        self.root = Path(root)
        self.master = Path(master)
        self.registry_path = Path(registry_path) if registry_path else self.root / "sources.toml"
        self.journal = AuditLog(self.root / "journal.jsonl", wall)
        self.fetcher = Fetcher(self.root, self.journal, opener, clock, sleep, wall)
        self.index = MasterIndex(self.root / "index.sqlite")
        self.wall = wall
        self._hook = lambda stage: None          # tests: simulate a crash at "staging"

    # -- reading state -------------------------------------------------------------------------------------------
    def runs(self, sid: str) -> list[dict]:
        d = self.root / "staging" / sid
        out = []
        for p in sorted(d.glob("[0-9]*")) if d.exists() else []:
            with contextlib.suppress(OSError, ValueError):
                out.append(json.loads((p / "report.json").read_text(encoding="utf-8")))
        return out

    def plan(self) -> dict:
        ok, refused = registry.load(self.registry_path)
        out = {"registry": str(self.registry_path), "refused": refused, "sources": {}}
        for sid, src in sorted(ok.items()):
            runs = self.runs(sid)
            files = {}
            for name, t in src.targets().items():
                last = self.fetcher.last(t)
                files[name or "file"] = {"url": t.url, "verified_download": last["sha256"] if last else None,
                                         "pinned": bool(t.sha256 or t.checksum_url)}
            would = ("pin first: `python3 -m computer.acquire pin " + sid + "`, then approve the sha256 values"
                     if src.unpinned else
                     "check upstream for newer files, then stage if anything changed"
                     if all(f["verified_download"] for f in files.values()) else "fetch and verify, then stage")
            out["sources"][sid] = {"licence": src.licence, "approved_by": src.approved_by, "files": files,
                                   "would": would, "last_run": runs[-1]["run"] if runs else None}
        return out

    def status(self) -> dict:
        jv = self.journal.verify()
        out = {"root": str(self.root), "journal_ok": jv["ok"], "index": self.index.meta(), "sources": {}}
        with contextlib.suppress(AcquireError):
            ok, _ = registry.load(self.registry_path)
            for sid in sorted(ok):
                runs = self.runs(sid)
                q = self.root / "quarantine" / sid
                out["sources"][sid] = {"runs": len(runs), "last": (runs[-1]["counts"] if runs else None),
                                       "quarantined": len([p for p in q.glob("*") if not p.name.endswith(".why")])
                                       if q.exists() else 0}
        return out

    def integrate(self, *a, **k):
        raise AcquireError(INTEGRATE_OFF)

    # -- a run ---------------------------------------------------------------------------------------------------
    def run(self, sid: str, force: bool = False) -> dict:
        jv = self.journal.verify()
        if not jv["ok"]:
            raise AcquireError("the acquire journal doesn't verify; nothing runs until it is looked at: "
                               + "; ".join(b["why"] for b in jv["breaks"][:3]))
        src = registry.get(self.registry_path, sid)
        if not self.master.is_file():
            raise AcquireError(f"no master corpus at {self.master}")
        got = self.fetcher.fetch_all(src)
        input_sha = got[""].sha256 if "" in got else "sha256:" + hashlib.sha256(
            "\n".join(f"{n}={f.sha256}" for n, f in sorted(got.items())).encode()).hexdigest()
        master_sha = sha256_file(self.master)
        code = code_fingerprint()
        runs = self.runs(sid)
        if runs and not force:
            last = runs[-1]
            if (last["input"]["sha256"], last["master"]["sha256"], last["source_fingerprint"], last["code"]) == \
                    (input_sha, master_sha, src.fingerprint(), code):
                return {"unchanged": True, "run": last["run"], "counts": last["counts"]}
        meta, rebuilt = self.index.ensure(self.master, src.replaces or None)
        if rebuilt:
            self.journal.append({"event": "indexed", "master_sha256": master_sha, "keys": meta["keys"]})

        base = self.root / "staging" / sid
        base.mkdir(parents=True, exist_ok=True)
        for stale in base.glob(".*.part"):
            shutil.rmtree(stale)
            self.journal.append({"event": "removed", "source": sid, "run": stale.name,
                                 "why": "a run left by an interruption"})
        run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(self.wall()))}-{len(runs) + 1:04d}"
        part = base / f".{run_id}.part"
        part.mkdir()
        try:
            report = self._stage(src, got, input_sha, part, run_id, master_sha, code)
            self._hook("staging")
            final = base / run_id
            os.replace(part, final)
            with contextlib.suppress(OSError):
                fd = os.open(base, os.O_RDONLY)
                os.fsync(fd)
                os.close(fd)
        except BaseException:
            shutil.rmtree(part, ignore_errors=True)
            raise
        finally:
            self.index.close()
        self.journal.append({"event": "staged", "source": sid, "run": run_id, "counts": report["counts"],
                             "delta_sha256": report["delta_sha256"]})
        return report

    def _stage(self, src, got, input_sha: str, part: Path, run_id: str, master_sha: str, code: str) -> dict:
        counts = Counter()
        rejected_by = Counter()
        functional = self.index.functional()
        seen: set[bytes] = set()
        run_vals = defaultdict(list)
        entities = defaultdict(set)              # name -> the things it names, for the homonym count (D4)
        candidates = []                          # possible conflicts; labelled once every name's entities are known

        # Rows that pass every check go to passed.tmp first: whether a name stands for several things is only known
        # at the end, and those rows are held back (held.pf), not staged, until the reader can tell them apart (D4).
        with open(part / "passed.tmp", "w", encoding="utf-8") as passed, \
                open(part / "rejects.jsonl", "w", encoding="utf-8") as rej:

            def reject(n, sid_, stage, code_, why, raw):
                counts["rejected"] += 1
                rejected_by[f"{stage}: {code_}"] += 1
                rej.write(json.dumps({"line": n, "source_id": sid_, "stage": stage, "code": code_, "why": why,
                                      "raw": raw[:500]}, ensure_ascii=False, sort_keys=True) + "\n")

            source_files = got[""].path if "" in got else {name: f.path for name, f in got.items()}
            for n, cols, why, raw in PARSERS[src.parser](source_files):
                counts["read"] += 1
                if cols is None:
                    code_ = why.split(":", 1)[0] if ":" in why else "unreadable"
                    reject(n, "", "parse", code_, why, raw)
                    continue
                subj, rel, kind, value, unit, vf, vu, _stype, sid_, conf, qual = (c.strip() for c in cols)
                # map: the allowlist, the source's own type, the original id, the entity (Q3)
                if rel not in src.relations:
                    reject(n, sid_, "map", "relation", f"relation {rel!r} is not in this source's allowlist", raw)
                    continue
                if not sid_:
                    reject(n, sid_, "map", "source-id", "no source_id: every row keeps its original id", raw)
                    continue
                rel, stype = src.relations[rel], src.source_type
                if src.entity and not Q.get(qual, "ENTITY"):
                    qual = (qual + Q.SEPARATOR if qual else "") + f"ENTITY={src.entity}:{sid_.split('/')[0]}"
                # normalise: trimmed above; no bound is written 0; nothing else is changed
                kind, vf, vu = kind.upper(), vf or "0", vu or "0"
                if not subj or not value:
                    reject(n, sid_, "normalise", "empty", "the subject or the value is empty", raw)
                    continue
                # validate: the validator's own checks
                problems = pfrow.field_problems(kind, value, vf, vu, conf, qual)
                if problems:
                    reject(n, sid_, "validate", problems[0], "; ".join(problems), raw)
                    continue
                # dedupe: the build's own key
                fsubj, fkind, start, until, fqual = pfrow.finish(subj, rel, kind, value, vf, vu, stype, sid_, qual,
                                                                 True)
                key = pfrow.fact_key(fsubj, rel, fkind, value, unit, start, until, fqual, True)
                h = key_hash(key)
                if h in seen or self.index.has(h):
                    counts["duplicate"] += 1
                    continue
                # The master has this fact with no ENTITY= (a legacy row, until Q2 re-imports its source).  A duplicate,
                # unless the name stands for several things in this run: then this row is what says which thing it is
                # about (D4), and is kept.  Decided once every name's things are known.
                legacy_copy = bool(key[-1]) and self.index.has(key_hash(key[:-1] + ("",)))
                seen.add(h)
                # conflicts: recorded, never resolved here.  Within the run, only the same thing is compared with
                # itself (name, relation and ENTITY); the master's rows carry no ENTITY, so they're compared by name.
                entity = Q.get(fqual, "ENTITY")
                if rel in functional and start is None and until is None:
                    existing = [("master", k, v) for k, v in self.index.values(fsubj, rel)] + \
                               [("this run", k, v) for k, v in run_vals[(fsubj, rel, entity)]]
                    if existing and not any(same_value(fkind, value, v) for _, _, v in existing):
                        candidates.append({"line": n, "source_id": sid_, "subject": fsubj, "relation": rel,
                                           "value": value, "entity": entity,
                                           "existing": [{"in": w, "value": v} for w, _, v in existing]})
                    run_vals[(fsubj, rel, entity)].append((fkind, value))
                passed.write(fsubj + "\t" + ("L" if legacy_copy else "") + "\t" +
                             "\t".join((subj, rel, kind, value, unit, vf, vu, stype, sid_, conf, qual)) + "\n")
                if entity:
                    entities[fsubj].add(entity)
            for fh in (passed, rej):
                fh.flush()
                os.fsync(fh.fileno())

        ambiguous = {k for k, v in entities.items() if len(v) > 1}      # the reader tells them apart (D4) ...
        # ... except from rows that don't say which thing they're about: a name the master holds without entities
        # (legacy rows can mix several things, and nothing links them to this source's ids) waits, as does every
        # ambiguous name of a source that asks to hold them (hold_homonyms).
        held_names = {k for k in ambiguous if src.hold_homonyms or self.index.held_plain(k)}
        with open(part / "passed.tmp", encoding="utf-8") as passed, \
                open(part / "delta.pf", "w", encoding="utf-8") as delta, \
                open(part / "held.pf", "w", encoding="utf-8") as held:
            delta.write(DELTA_HEADER)
            held.write(HELD_HEADER)
            for line in passed:
                fsubj, flag, row = line.split("\t", 2)
                if flag == "L" and fsubj not in ambiguous:
                    counts["duplicate"] += 1
                    counts["duplicate_of_a_row_without_entity"] += 1
                elif fsubj in held_names:
                    held.write(row)
                    counts["held"] += 1
                else:
                    delta.write(row)
                    counts["staged"] += 1
            for fh in (delta, held):
                fh.flush()
                os.fsync(fh.fileno())
        (part / "passed.tmp").unlink()
        kinds = Counter()
        with open(part / "conflicts.jsonl", "w", encoding="utf-8") as con:
            for c in candidates:
                c["kind"] = "ambiguous" if c["subject"] in ambiguous else "conflict"
                kinds[c["kind"]] += 1
                con.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
            con.flush()
            os.fsync(con.fileno())

        without_entity = counts["duplicate_of_a_row_without_entity"]
        counts = {k: counts[k] for k in ("read", "staged", "held", "rejected", "duplicate")}
        counts["conflicts"] = kinds["conflict"]
        counts["ambiguous_comparisons"] = kinds["ambiguous"]
        report = {
            "format": "cdos-acquire-run/1", "run": run_id, "source": src.id, "at": utc(self.wall()),
            "source_type": src.source_type, "licence": src.licence, "attribution": src.attribution,
            "approved_by": src.approved_by, "approved_at": src.approved_at,
            "source_fingerprint": src.fingerprint(), "code": code,
            "input": ({"url": src.url, "path": str(got[""].path), "sha256": input_sha, "size": got[""].size,
                       "etag": got[""].record.get("etag", "")} if "" in got else
                      {"sha256": input_sha, "files": {n: {"url": f.record["url"], "path": str(f.path),
                                                           "sha256": f.sha256, "size": f.size,
                                                           "last_modified": f.record.get("last_modified", "")}
                                                       for n, f in sorted(got.items())}}),
            "master": {"path": str(self.master), "sha256": master_sha},
            "replaces": src.replaces or None,
            "counts": counts, "rejected_by": dict(sorted(rejected_by.items())),
            "duplicates_of_rows_without_entity": without_entity,
            "names_with_several_entities": sum(1 for v in entities.values() if len(v) > 1),
            "names_with_several_entities_examples": sorted(
                ([k, len(v)] for k, v in entities.items() if len(v) > 1), key=lambda x: (-x[1], x[0]))[:10],
            "held_names": len(held_names),
            "held_names_examples": sorted(held_names)[:10],
            "reconciled": counts["read"] == counts["staged"] + counts["held"] + counts["rejected"] + counts["duplicate"],
            "delta_sha256": sha256_file(part / "delta.pf"),
            "integrated": False,
        }
        if not report["reconciled"]:
            raise AcquireError(f"counts don't reconcile: {counts}")
        with open(part / "report.json", "w", encoding="utf-8") as fh:
            fh.write(json.dumps(report, indent=1, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return report
