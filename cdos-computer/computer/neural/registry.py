"""The model registry: where models wait, and the one way a model becomes active.

    <registry>/
        staging/<id>/        what `train` writes (and `evaluate` adds eval.json to); never loaded by the computer
        models/<id>/         promoted copies, files read-only; the computer loads only from here
        ACTIVE.json          {"format", "model_id", ...}: which promoted model the computer uses (null: none)
        promotions.jsonl     append-only: every promotion, rollback and deactivation, with its evaluation
        experience/          the append-only experience log (computer/neural/experience.py)

promote(id, approve=True) is the only thing that changes ACTIVE.json to a new model, and it refuses unless:

  - the staged model and manifest load with every check the computer applies,
  - eval.json is for exactly these bytes (model and manifest hashes) and was made against the model that is active now,
  - every evaluation gate passed,
  - and approve is True (the CLI's --approve).  Without it, promote() only says what it would do.

Who approved: the CLIs ask the Governor (computer/governor.py) first, which needs an operator with a reason and
records the decision; its reference is passed here as `approver` and written into promotions.jsonl.  A call without
one (code using this module directly) is recorded as unattributed, never silently.

Training writes only to staging/, so the active model can't change while a model is being trained or evaluated.
ACTIVE.json is replaced atomically (temporary file, fsync, rename), like computer/state.py.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

from computer.neural.model import ModelError, load_model_dir, sha256_file
from computer.neural.runtime import ACTIVE_FORMAT, inside, read_active


class PromotionRefused(RuntimeError):
    pass


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def audit(root: Path, entry: dict) -> None:
    with open(root / "promotions.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": now(), **entry}, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def set_active(root: Path, model_id: str | None, previous: str | None, **extra) -> None:
    body = {"format": ACTIVE_FORMAT, "model_id": model_id, "previous": previous, "since": now(), **extra}
    write_atomic(root / "ACTIVE.json", (json.dumps(body, sort_keys=True, indent=1) + "\n").encode())


def load_eval(d: Path) -> dict:
    p = d / "eval.json"
    if not p.exists():
        raise PromotionRefused(f"{d.name} hasn't been evaluated: python3 -m computer.neural evaluate --model {d.name}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PromotionRefused(f"eval.json unreadable: {e}") from None


def load_selector(d: Path) -> dict:
    """The reasoning selector's checks: the model and its untrained baseline load, with their manifest."""
    _, manifest = load_model_dir(d)
    load_model_dir(d, "untrained")
    return manifest


def component_root(registry, component: str = "reasoning.selector") -> Path:
    """Where a component's staging/, models/ and ACTIVE.json live.  The reasoning selector keeps the 0.1 layout (the
    registry folder itself); every other component has <registry>/components/<name>/."""
    from computer.neural.components import NAME
    if component == "reasoning.selector":
        return Path(registry)
    if not NAME.match(component):
        raise PromotionRefused(f"{component!r} isn't a component name")
    return Path(registry) / "components" / component


def check_promotable(root: Path, model_id: str, load=None) -> tuple[Path, dict, dict]:
    load = load or load_selector
    d = root / "staging" / model_id
    if not d.is_dir() or not inside(d, root / "staging"):
        raise PromotionRefused(f"no staged model {model_id}")
    try:
        manifest = load(d)
    except ModelError as e:
        raise PromotionRefused(f"{model_id} doesn't load: {e}") from None
    ev = load_eval(d)
    if ev.get("model_sha256") != manifest["model_sha256"] or ev.get("manifest_sha256") != sha256_file(d / "manifest.json"):
        raise PromotionRefused("eval.json was made for different files: evaluate this model again")
    try:
        active = read_active(root)
    except ModelError as e:
        raise PromotionRefused(f"ACTIVE.json is damaged ({e}); fix or remove it first") from None
    if ev.get("active_at_eval") != active:
        raise PromotionRefused(f"the evaluation compared against {ev.get('active_at_eval')!r}, but {active!r} is active "
                               "now: evaluate again")
    if ev.get("strict") is False:
        raise PromotionRefused("this evaluation ran without the neural path's answer checks (strict=False), which isn't "
                               "how the model would run: evaluate it again with them")
    failing = [g for g, v in (ev.get("gates") or {}).items() if not v.get("pass")]
    if failing or not ev.get("gates") or not ev.get("passed"):
        raise PromotionRefused("evaluation gates failed: " + ", ".join(f"{g} ({ev['gates'][g]['why']})" for g in failing))
    return d, manifest, ev


UNATTRIBUTED = {"unattributed": "called without a Governor decision (library use, not the CLI)"}


def promote(root, model_id: str, approve: bool = False, load=None, approver: dict | None = None) -> dict:
    """Make a staged, evaluated model the active one.  approve=False: report what would happen, change nothing.
    `load` checks a model folder and returns its manifest (default: the reasoning selector's checks)."""
    root = Path(root)
    load = load or load_selector
    d, manifest, ev = check_promotable(root, model_id, load)
    previous = ev.get("active_at_eval")
    cmp = (ev.get("comparisons") or {}).get("rules+trained") or {}
    summary = {"model_id": model_id, "previous": previous, "primary": ev.get("primary"), "selection": ev.get("selection"),
               "summary": ev.get("summary")}
    if cmp:
        summary.update(new_wrong=len(cmp.get("new_wrong", [])), regressions=len(cmp.get("regressions", [])))
    if not approve:
        return {"status": "not activated: approval required (--approve)", **summary}
    dest = root / "models" / model_id
    if dest.exists():
        raise PromotionRefused(f"{dest} already exists: use rollback --to {model_id} to activate it again")
    tmp = root / "models" / f".{model_id}.tmp-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(d, tmp)
    try:
        load(tmp)                                      # the copy, checked as the computer will check it
    except ModelError as e:
        shutil.rmtree(tmp)
        raise PromotionRefused(f"the copy doesn't load: {e}") from None
    for p in tmp.rglob("*"):
        if p.is_file():
            p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    os.replace(tmp, dest)
    eval_sha = sha256_file(dest / "eval.json")
    set_active(root, model_id, previous, eval_sha256=eval_sha, manifest_sha256=sha256_file(dest / "manifest.json"))
    audit(root, {"action": "promote", "model_id": model_id, "previous": previous, "eval_sha256": eval_sha,
                 "model_sha256": manifest["model_sha256"], "gates": {g: v["pass"] for g, v in ev["gates"].items()},
                 "primary": ev.get("primary"), "approved_by": approver or UNATTRIBUTED})
    return {"status": "activated", **summary, "model_sha256": manifest["model_sha256"], "eval_sha256": eval_sha}


def rollback(root, model_id: str, approve: bool = False, load=None, approver: dict | None = None) -> dict:
    """Activate a model that was promoted before (models/<id>), after checking it still loads."""
    root = Path(root)
    d = root / "models" / model_id
    if not d.is_dir() or not inside(d, root / "models"):
        raise PromotionRefused(f"{model_id} was never promoted")
    try:
        (load or load_selector)(d)
    except ModelError as e:
        raise PromotionRefused(f"{model_id} doesn't load any more: {e}") from None
    previous = read_active(root)
    if not approve:
        return {"status": "not activated: approval required (--approve)", "model_id": model_id, "previous": previous}
    msha = sha256_file(d / "manifest.json")
    set_active(root, model_id, previous, manifest_sha256=msha)
    audit(root, {"action": "rollback", "model_id": model_id, "previous": previous, "manifest_sha256": msha,
                 "approved_by": approver or UNATTRIBUTED})
    return {"status": "activated", "model_id": model_id, "previous": previous, "manifest_sha256": msha}


def deactivate(root, approve: bool = False, approver: dict | None = None) -> dict:
    root = Path(root)
    previous = read_active(root)
    if not approve:
        return {"status": "not deactivated: approval required (--approve)", "previous": previous}
    set_active(root, None, previous)
    audit(root, {"action": "deactivate", "model_id": None, "previous": previous,
                 "approved_by": approver or UNATTRIBUTED})
    return {"status": "deactivated", "previous": previous}


def status(root, load=None) -> dict:
    root = Path(root)
    load = load or load_selector
    out = {"registry": str(root), "active": None, "active_error": "", "staged": [], "promoted": []}
    try:
        out["active"] = read_active(root)
    except ModelError as e:
        out["active_error"] = str(e)
    for sub, key in (("staging", "staged"), ("models", "promoted")):
        for d in sorted((root / sub).glob("*")) if (root / sub).is_dir() else []:
            if d.name.startswith(".") or not d.is_dir():
                continue
            row = {"model_id": d.name}
            try:
                m = load(d)
                row.update(created=m.get("created"), heldout_top1=(m.get("metrics") or {}).get("heldout_top1"),
                           corpus=str((m.get("training_data") or {}).get("corpus_version", ""))[:19] or None)
            except ModelError as e:
                row["error"] = str(e)
            ev = d / "eval.json"
            if ev.exists():
                try:
                    e = json.loads(ev.read_text())
                    row.update(evaluated=e.get("evaluated"), passed=e.get("passed"), primary=e.get("primary"))
                except ValueError:
                    row["eval"] = "unreadable"
            out[key].append(row)
    return out
