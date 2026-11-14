"""Checkpoints and manifests: what a model directory holds, and every check it has to pass before it is used.

    <model dir>/
        model.json       the network, in the existing controller format (cdos-computer-controller/1)
        manifest.json    what it is and how it was made (MANIFEST_FORMAT, below)
        untrained.json   the same network at initialisation, before any training step (the evaluation's baseline)
        records.jsonl.gz the training records it was trained on (computer/neural/records.py)
        fixtures/        the held-out questions it was evaluated on, as they were materialised at training time
        eval.json        the evaluation report and its gates (computer/neural/evaluate.py), once evaluated

Manifest (cdos-neural-manifest/1):

    model_id, created, model_file, model_sha256, untrained_file, untrained_sha256
    schema            {name, version, fingerprint}: the loader refuses any other fingerprint
    actions           the output vocabulary, in order
    training_data     {sha256 of the records, counts by split and source, leak checks, corpus version, generator}
    hyperparameters   {hidden, epochs, lr, l2, batch, seed, data_seed, dim, limits}
    software          {neural_version, code_sha256, python, numpy, platform}
    metrics           {train_top1, heldout_top1, untrained_heldout_top1, ...}
    resources         {wall_seconds, cpu_seconds, peak_rss_mb, ...}

Everything that reads a model goes through load_model_dir() or check_network(), and they raise ModelError (a
ValueError) for anything wrong: missing files, a hash that doesn't match the file, a fingerprint for another schema,
an action this code doesn't have, shapes that don't fit, numbers that aren't finite.  Nothing here writes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from computer.controller import Network
from computer.neural.schema import SCHEMA_VERSION, describe, fingerprint

MODEL_FORMAT = "cdos-computer-controller/1"
MANIFEST_FORMAT = "cdos-neural-manifest/1"
MAX_MODEL_BYTES = 64 * 1024 * 1024        # a 32-hidden controller on the full corpus is well under 1 MB
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_HIDDEN = 1024
MAX_ABS_WEIGHT = 1e4                      # far outside anything training produces; keeps every sum finite
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{2,63}$")
FILE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class ModelError(ValueError):
    """Why a model can't be used.  kind: missing | corrupt | incompatible."""

    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind, self.message = kind, message


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _read_bounded(path: Path, limit: int, what: str) -> bytes:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise ModelError("missing", f"no {what} at {path}") from None
    except OSError as e:
        raise ModelError("missing", f"{what} unreadable: {e}") from None
    if size > limit:
        raise ModelError("corrupt", f"{what} is {size} bytes, over the {limit}-byte limit")
    try:
        return path.read_bytes()
    except OSError as e:
        raise ModelError("missing", f"{what} unreadable: {e}") from None


def _json(data: bytes, what: str) -> dict:
    try:
        d = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ModelError("corrupt", f"{what} isn't valid JSON ({e})") from None
    if not isinstance(d, dict):
        raise ModelError("corrupt", f"{what} isn't a JSON object")
    return d


def _finite(xs, n: int, what: str) -> list[float]:
    if not isinstance(xs, list) or len(xs) != n:
        raise ModelError("corrupt", f"{what} must be a list of {n} numbers")
    for x in xs:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or abs(x) > MAX_ABS_WEIGHT:
            raise ModelError("corrupt", f"{what} has a value that isn't a finite number within ±{MAX_ABS_WEIGHT:g}")
    return [float(x) for x in xs]


def check_network(d: dict, actions: list[str] | None = None, dim: int | None = None) -> Network:
    """A checkpoint dict -> a Network, or ModelError.  With `actions` / `dim`, they must match exactly (the schema)."""
    if d.get("format") != MODEL_FORMAT:
        raise ModelError("incompatible", f"model format {d.get('format')!r}, expected {MODEL_FORMAT!r}")
    acts = d.get("actions")
    if not isinstance(acts, list) or not acts or not all(isinstance(a, str) for a in acts) or len(set(acts)) != len(acts):
        raise ModelError("corrupt", "actions must be a list of distinct names")
    if actions is not None and acts != list(actions):
        unknown = sorted(set(acts) - set(actions))
        raise ModelError("incompatible", "the model's actions aren't this code's actions"
                         + (f" (unknown: {', '.join(unknown[:5])})" if unknown else " (order or set differs)"))
    ddim, hidden = d.get("dim"), d.get("hidden")
    for k, v in (("dim", ddim), ("hidden", hidden)):
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise ModelError("corrupt", f"{k} must be a positive integer")
    if dim is not None and ddim != dim:
        raise ModelError("incompatible", f"input dimension {ddim}, the schema has {dim}")
    if hidden > MAX_HIDDEN:
        raise ModelError("corrupt", f"hidden size {hidden} is over the limit of {MAX_HIDDEN}")
    rows = d.get("rows")
    if not isinstance(rows, dict):
        raise ModelError("corrupt", "rows must be an object of feature index -> weights")
    parsed = {}
    for k, v in rows.items():
        if not isinstance(k, str) or not re.fullmatch(r"\d{1,9}", k) or not 0 <= int(k) < ddim:
            raise ModelError("corrupt", f"row index {k!r} isn't an integer in [0, {ddim})")
        parsed[int(k)] = _finite(v, hidden, f"row {k}")
    b1 = _finite(d.get("b1"), hidden, "b1")
    w2 = d.get("w2")
    if not isinstance(w2, list) or len(w2) != len(acts):
        raise ModelError("corrupt", f"w2 must have one row per action ({len(acts)})")
    w2 = [_finite(r, hidden, f"w2[{i}]") for i, r in enumerate(w2)]
    b2 = _finite(d.get("b2"), len(acts), "b2")
    net = Network(acts, ddim, hidden, parsed, b1, w2, b2)
    probe(net, acts)
    return net


def probe(model, actions: list[str]) -> None:
    """Run the model on no features and on every feature row it has: the scores must be a probability for each
    action.  Anything a bad file would break at answer time (a row of the wrong length) breaks here instead."""
    rows = sorted(getattr(model, "rows", None) or getattr(model, "index", None) or [])
    for idx in ([], rows):
        try:
            p = model.predict(idx)
        except Exception as e:  # noqa: BLE001 - any failure here is a broken model, said as one
            raise ModelError("corrupt", f"the model fails on a test input ({type(e).__name__}: {e})") from None
        if not isinstance(p, dict) or list(p) != list(actions):
            raise ModelError("corrupt", "the model's scores don't name its actions")
        if not all(isinstance(v, float) and math.isfinite(v) and 0.0 <= v <= 1.0 for v in p.values()) \
                or abs(sum(p.values()) - 1.0) > 1e-6:
            raise ModelError("corrupt", "the model's scores aren't a probability distribution")


def load_network_file(path, expect_sha256: str | None = None, actions=None, dim=None) -> tuple[Network, str]:
    """(network, sha256 of the file).  The hash is checked before the file is parsed."""
    p = Path(path)
    data = _read_bounded(p, MAX_MODEL_BYTES, "model file")
    sha = sha256_bytes(data)
    if expect_sha256 is not None and sha != expect_sha256:
        raise ModelError("corrupt", f"{p.name} doesn't match the hash in its manifest (changed or damaged since it was made)")
    return check_network(_json(data, p.name), actions, dim), sha


def check_manifest(m: dict) -> dict:
    """The manifest's own fields, and that it was made for this schema."""
    if m.get("format") != MANIFEST_FORMAT:
        raise ModelError("incompatible", f"manifest format {m.get('format')!r}, expected {MANIFEST_FORMAT!r}")
    mid = m.get("model_id")
    if not isinstance(mid, str) or not MODEL_ID.match(mid):
        raise ModelError("corrupt", "model_id must be 3-64 characters of a-z, 0-9 and -")
    for k in ("model_file", "untrained_file"):
        v = m.get(k)
        if v is not None and (not isinstance(v, str) or not FILE_NAME.match(v) or v in (".", "..")):
            raise ModelError("corrupt", f"{k} must be a plain file name in the model directory")
    if not isinstance(m.get("model_file"), str):
        raise ModelError("corrupt", "manifest has no model_file")
    if not isinstance(m.get("model_sha256"), str) or not m["model_sha256"].startswith("sha256:"):
        raise ModelError("corrupt", "manifest has no model_sha256")
    sch = m.get("schema")
    if not isinstance(sch, dict):
        raise ModelError("corrupt", "manifest has no schema")
    if sch.get("version") != SCHEMA_VERSION or sch.get("fingerprint") != fingerprint():
        raise ModelError("incompatible", f"built for feature schema v{sch.get('version')} "
                         f"({str(sch.get('fingerprint'))[:19]}…); this code has v{SCHEMA_VERSION} ({fingerprint()[:19]}…)")
    if m.get("actions") != describe()["actions"]:
        raise ModelError("incompatible", "the manifest's actions aren't this code's actions")
    return m


def load_manifest(model_dir) -> dict:
    d = Path(model_dir)
    return check_manifest(_json(_read_bounded(d / "manifest.json", MAX_MANIFEST_BYTES, "manifest"), "manifest.json"))


def load_model_dir(model_dir, which: str = "model") -> tuple[Network, dict]:
    """(network, manifest) for a model directory, every check passed.  which: "model" or "untrained"."""
    d = Path(model_dir)
    if not d.is_dir():
        raise ModelError("missing", f"no model directory at {d}")
    m = load_manifest(d)
    sch = describe()
    fname, sha = (m["model_file"], m["model_sha256"]) if which == "model" else (m.get("untrained_file"), m.get("untrained_sha256"))
    if not fname or not isinstance(sha, str):
        raise ModelError("missing", f"the manifest names no {which} file")
    net, _ = load_network_file(d / fname, sha, sch["actions"], sch["dim"])
    return net, m
