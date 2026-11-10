"""The experience log: every prediction the neural core makes, and what the deterministic checks made of it.

    <registry>/experience/experience.jsonl     one JSON object per line, appended, never rewritten

Each line (cdos-neural-experience/1):

    seq, prev, hash   a hash chain: hash = sha256 of the line's canonical JSON without "hash"; prev = the line before's
    t                 UTC time
    model_id          the active model that made the prediction
    question          what was asked (the turn log, computer.log.jsonl, holds the same text)
    top               the five best-scored actions and their scores
    plans             each plan the model proposed: whether it ran, the executor's status, accept()'s verdict
    outcome           what the computer said, from the result envelope: who answered (rules / neural / ...), status,
                      truth, certainty, the evidence fact ids, the corpus version
    rules_action      when the rules answered: the action their plan is an instance of, so the log measures how often
                      the model agrees with the rules without anyone labelling anything

"Verified" here means checked by the computer's own deterministic machinery (validator, executor, accept(), the
envelope), not by a person.  Nothing reads this file on the answer path, and nothing in it is a fact: training from
it would need a person's answer for each question (see docs/NEURAL_CORE.md).

`verify(path)` walks the chain: an edited, removed or reordered line breaks it.  The writer opens the file in append
mode only; on a file that is already over max_bytes it stops logging and says so, rather than rotate or truncate.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path

FORMAT = "cdos-neural-experience/1"
GENESIS = "sha256:" + "0" * 64
MAX_BYTES = 256 * 1024 * 1024


def line_hash(rec: dict) -> str:
    body = json.dumps({k: v for k, v in rec.items() if k != "hash"}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _last_line(path: Path) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        size = min(end, 1 << 20)
        fh.seek(end - size)
        tail = fh.read(size)
    lines = [l for l in tail.split(b"\n") if l.strip()]
    return lines[-1] if lines else b""


class ExperienceLog:
    """An append-only, hash-chained JSONL file.  `fmt` names what the lines are: the experience log is
    cdos-neural-experience/1; the Growth Engine's journal and answers and the robot's decisions use the same class."""

    def __init__(self, path, max_bytes: int = MAX_BYTES, fmt: str = FORMAT):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.fmt = fmt
        self.error = ""
        self.written = 0
        self.seq, self.prev = 0, GENESIS
        self.newline = False                 # the file ends mid-line (a write cut short): start a fresh line first
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size:
            with open(self.path, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                self.newline = fh.read(1) != b"\n"
            last = _last_line(self.path)
            try:
                rec = json.loads(last)
                self.seq, self.prev = int(rec["seq"]), str(rec["hash"])
            except (ValueError, KeyError, TypeError):
                # A damaged last line (a power cut mid-write): the chain says so from here on; nothing is rewritten.
                self.seq, self.prev = -1, "sha256:broken"

    def append(self, record: dict) -> bool:
        try:
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                self.error = f"experience log over {self.max_bytes // (1024 * 1024)} MB: archive {self.path} to resume"
                return False
            rec = {"format": self.fmt, "seq": self.seq + 1, "prev": self.prev,
                   "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record}
            rec["hash"] = line_hash(rec)
            line = json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(("\n" if self.newline else "") + line)
                fh.flush()
            self.newline = False
            self.seq, self.prev = rec["seq"], rec["hash"]
            self.written += 1
            return True
        except (OSError, TypeError, ValueError) as e:
            self.error = f"experience log not written: {e}"
            return False


ChainLog = ExperienceLog


def read_chain(path) -> list[dict]:
    """Every line that parses (damaged lines are skipped: verify() reports them)."""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return out


def verify(path) -> dict:
    """Walk the chain.  ok is False at the first line that doesn't parse, doesn't match its hash, or doesn't follow."""
    out = {"path": str(path), "lines": 0, "ok": True, "breaks": []}
    prev, seq = GENESIS, 0
    try:
        fh = open(path, "r", encoding="utf-8")
    except OSError as e:
        return {**out, "ok": False, "breaks": [{"line": 0, "why": str(e)}]}
    with fh:
        for n, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            out["lines"] += 1
            try:
                rec = json.loads(raw)
            except ValueError:
                out["breaks"].append({"line": n, "why": "not JSON (damaged or partly written)"})
                prev, seq = "sha256:broken", -1
                continue
            why = ""
            if rec.get("hash") != line_hash(rec):
                why = "content doesn't match its hash (edited)"
            elif rec.get("prev") != prev:
                why = "doesn't follow the line before (a line was removed, reordered or edited)"
            elif rec.get("seq") != seq + 1:
                why = f"sequence {rec.get('seq')} after {seq}"
            if why:
                out["breaks"].append({"line": n, "why": why})
            prev, seq = rec.get("hash", ""), rec.get("seq", -1) if isinstance(rec.get("seq"), int) else -1
    out["ok"] = not out["breaks"]
    return out


def stats(path) -> dict:
    c, agree, models, reasons = Counter(), Counter(), Counter(), Counter()
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            try:
                rec = json.loads(raw)
            except ValueError:
                c["damaged"] += 1
                continue
            c["predictions"] += 1
            models[rec.get("model_id", "?")] += 1
            out = rec.get("outcome") or {}
            c[f"said_by:{out.get('said_by', '?')}"] += 1
            for p in rec.get("plans", []):
                c["plans_proposed"] += 1
                if p.get("ran"):
                    c["plans_run"] += 1
                if p.get("accept"):
                    reasons[p["accept"].split(":")[0][:60]] += 1
            ra, top = rec.get("rules_action"), (rec.get("top") or [[None]])[0][0]
            if ra:
                agree["rules_answered"] += 1
                agree["model_top1_agrees"] += ra == top
    a = agree["rules_answered"]
    return {"counts": dict(c), "models": dict(models), "accept_rejections": dict(reasons.most_common(10)),
            "agreement_with_rules": {"questions": a, "top1_agrees": agree["model_top1_agrees"],
                                     "rate": round(agree["model_top1_agrees"] / a, 4) if a else None}}


def neural_runs(trace: list[str], label: str = "neural") -> list[dict]:
    """The neural planner's plans that navigate() ran this turn, from its trace: plan, status, rejection."""
    runs, cur = [], None
    for line in trace:
        if line.startswith(f"planner {label} ["):
            cur = {"plan": line.split("]: ", 1)[1].replace(" | ", "\n") if "]: " in line else "", "status": "",
                   "rejected": ""}
            runs.append(cur)
        elif cur is not None and line.startswith("  => rejected: "):
            cur["rejected"] = line[len("  => rejected: "):]
        elif cur is not None and line.startswith("  => ") and not cur["status"]:
            cur["status"] = line[len("  => "):].split(",")[0]
        elif line.startswith("planner "):
            cur = None
    return runs


def turn_record(planner, trace: list[str], plan, res, envelope: dict, said_by: str) -> dict:
    """One experience line for a turn the neural planner was asked about."""
    from computer.controller import action_of
    last = planner.last or {}
    ran = {r["plan"]: r for r in neural_runs(trace, planner.label)}
    plans = []
    for p in last.get("plans", []):
        r = ran.get(p["plan"])
        plans.append({"plan": p["plan"], "source": p["source"], "ran": r is not None,
                      "status": r["status"] if r else None,
                      # accept()'s verdict ("" = passed) for a plan that answered; None if it never got that far
                      "accept": last.get("accept", {}).get(p["plan"])})
    src = getattr(plan, "source", "") or ""
    spoke = said_by == "navigator" and plan is not None
    answered_by = (src.split(":", 1)[0] if src.startswith(("neural:", "learned:")) else src) if spoke else ""
    rules_action = None
    if spoke and answered_by == "rules" and res is not None and res.answered:
        try:
            rules_action = action_of(plan)
        except Exception:  # noqa: BLE001 - a rule plan that isn't an action shape just isn't compared
            rules_action = None
    env = envelope or {}
    return {"model_id": planner.model_id, "question": last.get("question", ""), "top": last.get("top", []),
            "plans": plans, "rules_action": rules_action,
            "outcome": {"said_by": said_by or "none", "planner": answered_by or None, "plan_source": (src if spoke else None),
                        "status": env.get("status"), "truth": env.get("truth"), "certainty": env.get("certainty"),
                        "evidence": [e.get("id") for e in env.get("evidence", [])][:24],
                        "corpus_version": env.get("corpus_version")}}
