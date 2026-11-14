"""The result envelope: what every answer carries, so it can be checked without reading its words.

    {"status": "answered | unsupported | conflict | invalid | unavailable",
     "truth": "TRUE | FALSE | UNKNOWN | CONFLICT | null",          the answer axis (computer/logic.py)
     "certainty": "ESTABLISHED | INFERRED | UNKNOWN | CONFLICT",   the support axis
     "value": ..., "text": "...", "kind": "knowledge | analysis | calc | ...",
     "evidence": [{"id", "subject", "relation", "value", "unit", "source", "conf", "derived", "corpus_version"}],
     "steps": [...], "rules": [...], "assumptions": [...], "errors": [...],
     "capabilities": [{"name", "version"}], "skipped": [{"name", "reason"}],
     "year": 2026, "corpus_version": "sha256:...", "canonical_hash": "sha256:..."}

Certainty (how the evidence supports the answer; a result takes the weakest of its steps):

    ESTABLISHED  only facts stored as written, plus exact unit conversions (listed in rules as unit.x_to_y)
    INFERRED     at least one derivation: an IS_A path, a knowledge rule, a lexicon mapping, a formula that combines
                 different relations (charge x voltage), or an estimate from stated quantities
    UNKNOWN      the evidence doesn't establish an answer (a gap, a wrong unit, a clarification still needed)
    CONFLICT     the evidence supports incompatible claims

`status` is "invalid" when an error can't be fixed by asking again (a wrong unit), "unavailable" when a required
capability is off.  `canonical_hash` covers everything except timings, metering and the trace, over a canonical
form: keys sorted, numbers as normalised decimals (52, 52.0 and 5.2e1 are one number), no file paths.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal

CERTAINTY = ("ESTABLISHED", "INFERRED", "UNKNOWN", "CONFLICT")       # weakest last
STATUSES = ("answered", "unsupported", "conflict", "invalid", "unavailable")
VOLATILE = {"canonical_hash", "ms", "compute", "trace"}
FINAL_ERRORS = {"UNIT_INCOMPATIBLE", "DIMENSION_ERROR", "INVALID_INPUT"}   # asking again won't fix these


def weakest(*labels: str) -> str:
    return max(labels, key=CERTAINTY.index) if labels else "ESTABLISHED"


def _num(x) -> str:
    if isinstance(x, float) and not math.isfinite(x):
        return str(x)
    d = Decimal(repr(x)) if isinstance(x, float) else Decimal(x)
    if d == 0:
        return "0"
    d = d.normalize()
    return format(d, "f") if -24 < d.adjusted() < 24 else format(d, "E")


def canonical(obj) -> object:
    """The envelope as the hash sees it."""
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, (int, float)):
        return {"#": _num(obj)}
    if isinstance(obj, dict):
        return {str(k): canonical(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, (list, tuple)):
        return [canonical(v) for v in obj]
    return str(obj)


def canonical_hash(env: dict) -> str:
    body = json.dumps(canonical(env), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


INTEGRITY_SAYS = {
    "mismatch": "the corpus doesn't match the content hash recorded when it was built: it was edited or replaced since",
    "changed": "a corpus file has changed on disk since the content hash was checked at load",
}


def _declared(name: str) -> str:
    from .capabilities import DECLARATIONS
    return next(f"{d['name']}@{d['version']}" for d in DECLARATIONS if d["name"] == name)


def seal(env: dict) -> dict:
    assert env["status"] in STATUSES and env["certainty"] in CERTAINTY, env
    ci = env.get("corpus_integrity") or {}
    if ci.get("status") in INTEGRITY_SAYS and not any(e.get("code") == "CORPUS_INTEGRITY" for e in env["errors"]):
        # The answer may still be right, but its provenance can't be claimed: the version names what was read at
        # load, and this says the build record (or the files since) disagree.
        env["errors"] = env["errors"] + [{"code": "CORPUS_INTEGRITY", "capability": _declared("corpus.lookup"),
                                          "message": INTEGRITY_SAYS[ci["status"]], "recoverable": False,
                                          "details": {k: ci.get(k) for k in ("stored", "actual", "changed_files")}}]
    env["canonical_hash"] = canonical_hash(env)
    return env


def _fact(f, version: str, derived=None) -> dict:
    get = (lambda k: f.get(k)) if isinstance(f, dict) else (lambda k: getattr(f, k))
    return {"id": get("id"), "subject": get("subject"), "relation": get("relation"), "value": get("value"),
            "unit": get("unit") or "", "source": get("source"), "conf": get("conf"),
            "derived": list(derived or []), "corpus_version": version}


def _status(internal: str, errors: list) -> str:
    codes = {e["code"] for e in errors}
    if internal == "answered":
        return "answered"
    if internal == "conflict":
        return "conflict"
    if "UNAVAILABLE" in codes or internal == "unavailable":
        return "unavailable"
    if codes & FINAL_ERRORS or internal == "invalid":
        return "invalid"
    return "unsupported"


def base(kind: str, text: str, corpus, usage, year: int) -> dict:
    return {"kind": kind, "status": "answered", "truth": None, "value": None, "certainty": "UNKNOWN", "text": text,
            "evidence": [], "steps": [], "rules": [], "assumptions": [], "errors": [],
            "capabilities": list(usage.used), "skipped": list(usage.skipped), "year": year,
            "corpus_version": corpus.version if corpus is not None and getattr(corpus, "ready", False) else "none",
            "corpus_integrity": corpus.integrity_record() if corpus is not None and getattr(corpus, "ready", False)
            and hasattr(corpus, "integrity_record") else {"status": "none"}}


# ---------------------------------------------------------------------- from the navigator

def from_result(res, corpus, usage, knowledge=None, text: str | None = None) -> dict:
    """A navigator Result (computer/navigator.py) as an envelope."""
    env = base("knowledge", text if text is not None else res.text, corpus, usage, res.year)
    version = env["corpus_version"]
    env["status"] = _status(res.status, res.errors)
    fin = res.final or {}
    if fin.get("kind") == "select":
        env["value"] = {"selected": [k for k, t in fin["verdicts"].items() if t == "TRUE"], "verdicts": fin["verdicts"]}
        truth = fin.get("truth")
        env["truth"] = None
    else:
        truth = res.truth or None
        env["truth"] = None if env["status"] == "invalid" else truth
        if fin.get("kind") == "test":
            env["value"] = fin.get("value")
        elif fin.get("kind") in ("values", "count", "calc"):
            env["value"] = fin.get("values")
        elif fin.get("kind") == "compare":
            env["value"] = {"winner": fin.get("winner"), "loser": fin.get("loser")}
    if env["status"] == "conflict" or truth == "CONFLICT":
        env["certainty"] = "CONFLICT"
    elif env["status"] != "answered" or truth == "UNKNOWN":
        env["certainty"] = "UNKNOWN"
    else:
        env["certainty"] = "INFERRED" if res.inferences else "ESTABLISHED"
    derived = getattr(res, "fact_derived", {}) or {}
    env["evidence"] = [_fact(f, version, list(via) or derived.get(f.id, [])) for f, via in zip(res.evidence, res.inherited)]
    env["steps"] = list(res.steps) + [d["expr"] for d in res.derived]
    env["rules"] = list(res.rules)
    env["errors"] = list(res.errors)
    why = {}
    if knowledge is not None:
        why.update({d.rule_id: d.why for d in knowledge.disjoint})
        why.update({c.rule_id: c.why for c in knowledge.complete.values()})
    env["assumptions"] = list(res.notes) + [f"{r}: {why[r]}" for r in res.rules if r in why] + \
        list(getattr(res, "assumptions", []) or [])
    env["inferences"] = list(res.inferences)
    return seal(env)


# ---------------------------------------------------------------------- from the analysis engine

def from_answer(ans, corpus, usage, year: int) -> dict:
    """An analysis Answer (computer/analysis.py) as an envelope.  Estimates are INFERRED: a formula over quantities,
    with every stated input and assumption listed."""
    from .analysis import conversion_rule
    from .quantity import BASE_UNITS, Quantity
    env = base("analysis", ans.text, corpus, usage, year)
    f = ans.finding
    version = env["corpus_version"]
    if ans.kind == "clarify":
        env["status"], env["certainty"] = "unsupported", "UNKNOWN"
        env["errors"] = [{"code": "MISSING_FACT", "capability": _ref(usage, "analysis.estimate"),
                          "message": ans.text, "recoverable": True,
                          "details": {"missing": list(f.missing) if f else [], "choose_between": list(f.conflicts) if f else []}}]
        return seal(env)
    if ans.kind == "refuse":
        env["status"], env["certainty"] = "invalid", "UNKNOWN"
        env["errors"] = [{"code": "INVALID_INPUT", "capability": _ref(usage, "analysis.estimate"), "message": ans.text,
                          "recoverable": False, "details": {}}]
        return seal(env)
    env["certainty"] = "INFERRED"
    inputs = {k: Quantity.from_json(v) for k, v in (f.inputs or {}).items()}
    facts, rules, said = [], [], []

    def walk(name, q):
        if q.fact:
            facts.append(_fact(q.fact, version, [f"input {name} of {f.formula}"]))
            r = conversion_rule(q)
            if r:
                rules.append(r)
        if q.source in ("stated", "answer"):
            said.append(f"{name} as stated: {q.text}")
        for p in q.parts:
            walk(name, p)
    for k, q in inputs.items():
        walk(k, q)
    out = f.formula and {"runtime": "time", "daily": "energy", "energy": "energy", "power": "power"}.get(f.formula)
    env["value"] = {"result": getattr(ans, "result", None), "unit": BASE_UNITS.get(out, "")} if out else None
    env["evidence"] = facts
    env["steps"] = list(f.steps)
    env["rules"] = list(dict.fromkeys(rules))
    env["assumptions"] = list(f.assumptions) + said + ([f"what-if: {f.conditional}"] if f.conditional else [])
    return seal(env)


def _ref(usage, name: str) -> str:
    for c in usage.used:
        if c["name"] == name:
            return f"{c['name']}@{c['version']}"
    return name


# ---------------------------------------------------------------------- everything else

def simple(kind: str, text: str, corpus, usage, year: int, certainty: str = "UNKNOWN", status: str = "answered",
           evidence=None, errors=None, value=None) -> dict:
    env = base(kind, text, corpus, usage, year)
    env.update(status=status, certainty=certainty, value=value, errors=list(errors or []))
    if evidence:
        env["evidence"] = [_fact(f, env["corpus_version"]) for f in evidence]
    return seal(env)
