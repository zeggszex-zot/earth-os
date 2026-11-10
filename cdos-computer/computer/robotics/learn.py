"""The learned robot policy: trained in simulation, staged, evaluated, promoted only with approval.  Same lifecycle
and registry as the reasoning selector (computer/neural/registry.py), in <registry>/components/robot.policy/.

    train_policy(registry, seed)      expert demonstrations in seeded rooms (training seeds < 10000) -> a small network
                                      (11 features -> 24 tanh -> 2) -> staging/<id>/{model.json, untrained.json, manifest.json}
    evaluate_policy(registry, id)     held-out rooms (seeds 10000+): the trained network, its initial weights, the expert
                                      and the naive policy, each through the same controller and envelope -> eval.json
    load_policy(model dir)            every check before use: manifest, file hashes, interface versions, shapes, numbers

Gates: loads, no_collisions (and never out of bounds), beats_untrained (rooms reached), fewer_rejections_than_naive,
not_worse_than_active, deterministic.  Training needs numpy; using a policy doesn't.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import time
from collections import Counter
from pathlib import Path

from computer.neural.components import ComponentRegistry, major
from computer.neural.model import FILE_NAME, MODEL_ID, ModelError, sha256_bytes, sha256_file
from computer.neural.registry import component_root
from computer.neural.runtime import read_active
from computer.robotics.policy import (N_FEATURES, ROBOT_STATE, V_SCALE, W_SCALE, ExpertPolicy, LearnedPolicy, NaivePolicy,
                                      features, policy_input)
from computer.robotics.safety import ACTION, Envelope

COMPONENT = "robot.policy"
MANIFEST_FORMAT = "cdos-component-manifest/1"
WEIGHTS_FORMAT = "cdos-robot-policy/1"
EVAL_FORMAT = "cdos-component-eval/1"
HELDOUT_SEED = 10000
MAX_HIDDEN = 256
GATES = ("loads", "no_collisions", "beats_untrained", "fewer_rejections_than_naive", "not_worse_than_active",
         "deterministic")


def canonical(obj, indent=None) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=indent, separators=(",", ":") if indent is None else (",", ": "))
            + "\n").encode("utf-8")


# ---------------------------------------------------------------------- checks

def check_weights(d: dict) -> dict:
    if not isinstance(d, dict) or d.get("format") != WEIGHTS_FORMAT:
        raise ModelError("incompatible", f"weights format must be {WEIGHTS_FORMAT}")
    w1, b1, w2, b2 = d.get("w1"), d.get("b1"), d.get("w2"), d.get("b2")
    ok = lambda xs: isinstance(xs, list) and all(isinstance(x, (int, float)) and not isinstance(x, bool)   # noqa: E731
                                                 and math.isfinite(x) and abs(x) <= 1e4 for x in xs)
    if not (isinstance(w1, list) and 1 <= len(w1) <= MAX_HIDDEN and all(isinstance(r, list) and len(r) == N_FEATURES
                                                                        and ok(r) for r in w1)):
        raise ModelError("corrupt", f"w1 must be 1-{MAX_HIDDEN} rows of {N_FEATURES} finite numbers")
    h = len(w1)
    if not (ok(b1) and len(b1) == h and isinstance(w2, list) and len(w2) == 2 and all(isinstance(r, list) and len(r) == h
                                                                                      and ok(r) for r in w2)
            and ok(b2) and len(b2) == 2):
        raise ModelError("corrupt", "b1, w2 or b2 has the wrong shape or a value that isn't a finite number")
    return {"w1": w1, "b1": b1, "w2": w2, "b2": b2}


def load_policy(d, which: str = "model") -> tuple[LearnedPolicy, dict]:
    d = Path(d)
    try:
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ModelError("missing", f"manifest unreadable: {e}") from None
    if not isinstance(man, dict) or man.get("format") != MANIFEST_FORMAT or man.get("component") != COMPONENT:
        raise ModelError("incompatible", f"not a {COMPONENT} manifest")
    if not MODEL_ID.match(str(man.get("model_id", ""))):
        raise ModelError("corrupt", "bad model_id")
    io = man.get("interface") or {}
    for key, spec in (("input", ROBOT_STATE), ("output", ACTION)):
        name, ver = str(io.get(key, "@")).split("@", 1) if "@" in str(io.get(key, "")) else ("", "")
        try:
            same = name == spec.name and major(ver) == major(spec.version)
        except ValueError:
            same = False
        if not same:
            raise ModelError("incompatible", f"built for {key} {io.get(key)!r}; this code has {spec.ref}")
    fname, sha = (man.get("model_file"), man.get("model_sha256")) if which == "model" else \
        (man.get("untrained_file"), man.get("untrained_sha256"))
    if not isinstance(fname, str) or not FILE_NAME.match(fname):
        raise ModelError("corrupt", "bad file name in the manifest")
    try:
        data = (d / fname).read_bytes()
    except OSError as e:
        raise ModelError("missing", f"{fname}: {e}") from None
    if sha256_bytes(data) != sha:
        raise ModelError("corrupt", f"{fname} doesn't match the hash in its manifest")
    try:
        w = check_weights(json.loads(data))
    except ValueError as e:
        if isinstance(e, ModelError):
            raise
        raise ModelError("corrupt", f"{fname} isn't valid JSON") from None
    pol = LearnedPolicy(w, man)
    probe = {"x": 0.0, "y": 0.0, "heading": 0.0, "speed": 0.0, "yaw_rate": 0.0, "obstacles": [[1.0, 1.0, 0.3]],
             "goal_x": 2.0, "goal_y": 0.0}
    try:
        pol(probe)
    except Exception as e:  # noqa: BLE001
        raise ModelError("corrupt", f"the policy fails on a test input: {e}") from None
    return pol, man


def load_manifest(d) -> dict:
    """For registry.promote / rollback / status: the checks, and the manifest."""
    load_policy(d)
    load_policy(d, "untrained")
    return json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8"))


def load_active_policy(registry) -> tuple[LearnedPolicy | None, str]:
    """(policy, "") or (None, why not).  Never raises."""
    try:
        root = component_root(registry, COMPONENT)
        mid = read_active(root)
        if mid is None:
            return None, "no active robot.policy"
        return load_policy(root / "models" / mid)[0], ""
    except Exception as e:  # noqa: BLE001
        return None, str(e)


# ---------------------------------------------------------------------- episodes

def run_episode(seed: int, policy, envelope: Envelope, active_name: str = "p", max_steps: int = 200,
                record=None) -> dict:
    from computer.robotics.controller import RobotController
    from computer.robotics.sim import SimWorld
    reg = ComponentRegistry()
    reg.register(policy)
    w = SimWorld(seed)
    ctl = RobotController(envelope, reg, clock=lambda: w.t, policies=(policy.spec.name,))
    steps = rejected = 0
    rules = Counter()
    while w.status()["status"] == "running" and steps < max_steps:
        ctl.submit(w.observe())
        dcs = ctl.request_action(w.goal)
        steps += 1
        if not dcs["allowed"]:
            rejected += 1
            rules.update(v["rule_id"] for v in dcs["violations"])
        if record is not None and dcs["candidate"] is not None:
            record(policy_input(ctl.state(), w.goal), dcs["candidate"])
        w.apply(dcs["command"])
    st = w.status()
    if st["status"] == "running":
        st["status"] = "timeout"
    return {**st, "steps": steps, "rejected": rejected, "rules": dict(rules)}


def demonstrations(seeds, envelope: Envelope) -> tuple[list, list]:
    X, Y = [], []
    expert = ExpertPolicy()

    def rec(s, a):
        X.append(features(s))
        Y.append([max(-0.999, min(0.999, a["v"] / V_SCALE)), max(-0.999, min(0.999, a["omega"] / W_SCALE))])
    for seed in seeds:
        run_episode(seed, expert, envelope, record=rec)
    return X, Y


# ---------------------------------------------------------------------- training

def fit(X, Y, hidden: int = 24, epochs: int = 400, lr: float = 0.01, seed: int = 0):
    import numpy as np
    rng = np.random.default_rng(seed)
    X, Y = np.array(X), np.array(Y)
    W1, b1 = rng.normal(0, 0.3, (hidden, X.shape[1])), np.zeros(hidden)
    W2, b2 = rng.normal(0, 0.3, (2, hidden)), np.zeros(2)
    init = {"w1": W1.tolist(), "b1": b1.tolist(), "w2": W2.tolist(), "b2": b2.tolist()}
    params = [W1, b1, W2, b2]
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    losses = []
    for t in range(1, epochs + 1):
        H = np.tanh(X @ W1.T + b1)
        O = H @ W2.T + b2
        P = np.tanh(O)
        err = P - Y
        losses.append(float((err ** 2).mean()))
        dO = 2 * err * (1 - P ** 2) / len(X)
        gW2, gb2 = dO.T @ H, dO.sum(0)
        dH = (dO @ W2) * (1 - H ** 2)
        gW1, gb1 = dH.T @ X, dH.sum(0)
        for k, (p, g) in enumerate(zip(params, (gW1, gb1, gW2, gb2))):
            m[k] = 0.9 * m[k] + 0.1 * g
            v[k] = 0.999 * v[k] + 0.001 * g * g
            p -= lr * (m[k] / (1 - 0.9 ** t)) / (np.sqrt(v[k] / (1 - 0.999 ** t)) + 1e-8)
    rnd = lambda a: [round(float(x), 6) for x in a]   # noqa: E731
    trained = {"w1": [rnd(r) for r in W1], "b1": rnd(b1), "w2": [rnd(r) for r in W2], "b2": rnd(b2)}
    init = {"w1": [rnd(r) for r in init["w1"]], "b1": rnd(init["b1"]), "w2": [rnd(r) for r in init["w2"]],
            "b2": rnd(init["b2"])}
    return trained, init, losses


def train_policy(registry, seed: int = 0, rooms: int = 60, hidden: int = 24, epochs: int = 400, lr: float = 0.01,
                 envelope_path=None, log=None) -> Path:
    from computer.neural.train import peak_rss_mb, software
    log = log or (lambda *a: None)
    w0, c0 = time.monotonic(), time.process_time()
    env = Envelope.load(envelope_path)
    seeds = list(range(seed * 1000, seed * 1000 + rooms))
    if max(seeds) >= HELDOUT_SEED:
        raise ValueError("training rooms must use seeds below 10000 (the held-out rooms)")
    X, Y = demonstrations(seeds, env)
    trained, init, losses = fit(X, Y, hidden, epochs, lr, seed)
    mb = canonical({"format": WEIGHTS_FORMAT, **trained})
    ub = canonical({"format": WEIGHTS_FORMAT, **init})
    msha, usha = sha256_bytes(mb), sha256_bytes(ub)
    mid = f"rp1-{msha.split(':')[1][:12]}"
    data_sha = sha256_bytes(canonical({"X": X, "Y": Y}))
    root = component_root(registry, COMPONENT)
    dest = root / "staging" / mid
    if dest.exists():
        raise FileExistsError(f"{dest} already exists (same weights)")
    tmp = root / "staging" / f".work-{os.getpid()}-{time.monotonic_ns()}"
    tmp.mkdir(parents=True)
    try:
        (tmp / "model.json").write_bytes(mb)
        (tmp / "untrained.json").write_bytes(ub)
        man = {"format": MANIFEST_FORMAT, "component": COMPONENT, "model_id": mid,
               "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model_file": "model.json",
               "model_sha256": msha, "untrained_file": "untrained.json", "untrained_sha256": usha,
               "interface": {"input": ROBOT_STATE.ref, "output": ACTION.ref},
               "training_data": {"sha256": data_sha, "samples": len(X), "rooms": seeds[:1] + seeds[-1:],
                                 "demonstrator": ExpertPolicy.spec.ref, "envelope": env.fingerprint,
                                 "heldout_rooms_from_seed": HELDOUT_SEED},
               "hyperparameters": {"hidden": hidden, "epochs": epochs, "lr": lr, "seed": seed, "features": N_FEATURES,
                                   "output_scale": {"v": V_SCALE, "omega": W_SCALE}},
               "training": {"loss_first": round(losses[0], 6), "loss_last": round(losses[-1], 6)},
               "software": software(),
               "resources": {"wall_seconds": round(time.monotonic() - w0, 3),
                             "cpu_seconds": round(time.process_time() - c0, 3), "peak_rss_mb": peak_rss_mb()}}
        (tmp / "manifest.json").write_bytes(canonical(man, indent=1))
        load_policy(tmp)
        os.replace(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    log(f"staged {mid}: {len(X)} samples, loss {losses[0]:.4f} -> {losses[-1]:.4f}")
    return dest


# ---------------------------------------------------------------------- evaluation

def evaluate_policy(registry, model_id: str, rooms: int = 40, envelope_path=None, log=None) -> dict:
    log = log or (lambda *a: None)
    root = component_root(registry, COMPONENT)
    d = root / "staging" / model_id
    if not d.is_dir():
        d = root / "models" / model_id
    trained, man = load_policy(d)
    untrained, _ = load_policy(d, "untrained")
    env = Envelope.load(envelope_path)
    active_id = read_active(root)
    systems = {"expert": ExpertPolicy(), "naive": NaivePolicy(), "untrained": untrained, "trained": trained}
    if active_id and active_id != model_id:
        systems["active"] = load_policy(root / "models" / active_id)[0]
    seeds = list(range(HELDOUT_SEED, HELDOUT_SEED + rooms))
    results = {}
    for name, pol in systems.items():
        outs = [run_episode(s, pol, env) for s in seeds]
        c = Counter(o["status"] for o in outs)
        dec = sum(o["steps"] for o in outs)
        rules = Counter()
        for o in outs:
            rules.update(o["rules"])
        results[name] = {"rooms": rooms, "reached": c["reached"], "timeout": c["timeout"], "collided": c["collided"],
                         "out_of_bounds": c["out_of_bounds"], "decisions": dec,
                         "rejection_rate": round(sum(o["rejected"] for o in outs) / max(1, dec), 4),
                         "rejections_by_rule": dict(rules),
                         "mean_time_reached": round(sum(o["t"] for o in outs if o["status"] == "reached")
                                                    / max(1, c["reached"]), 2)}
        log(f"  {name}: {results[name]}")
    t, u, n = results["trained"], results["untrained"], results["naive"]
    again, _ = load_policy(d)
    probe = [run_episode(s, again, env, max_steps=20) for s in seeds[:3]]
    first = [run_episode(s, trained, env, max_steps=20) for s in seeds[:3]]
    gates = {"loads": (True, "model, untrained baseline and manifest pass every check"),
             "no_collisions": (t["collided"] == 0 and t["out_of_bounds"] == 0,
                               f"{t['collided']} collisions, {t['out_of_bounds']} out of bounds in {rooms} rooms"),
             "beats_untrained": (t["reached"] > u["reached"], f"reached {t['reached']} vs untrained {u['reached']}"),
             "fewer_rejections_than_naive": (t["rejection_rate"] < n["rejection_rate"],
                                             f"rejection rate {t['rejection_rate']:.1%} vs naive {n['rejection_rate']:.1%}"),
             "not_worse_than_active": ((t["reached"] >= results["active"]["reached"],
                                        f"reached {t['reached']} vs active {results['active']['reached']}")
                                       if "active" in results else (True, "no other active model to compare with")),
             "deterministic": (probe == first, "the same decisions from a second load" if probe == first else "differ")}
    rep = {"format": EVAL_FORMAT, "component": COMPONENT, "model_id": model_id, "model_sha256": man["model_sha256"],
           "manifest_sha256": sha256_file(d / "manifest.json"), "active_at_eval": active_id,
           "evaluated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "envelope": env.fingerprint,
           "heldout_rooms": [seeds[0], seeds[-1]], "results": results,
           "primary": {k: v["reached"] for k, v in results.items()},
           "summary": f"reached {t['reached']}/{rooms} held-out rooms (expert {results['expert']['reached']}, "
                      f"untrained {u['reached']}); rejection rate {t['rejection_rate']:.1%}",
           "gates": {g: {"pass": bool(gates[g][0]), "why": gates[g][1]} for g in GATES},
           "passed": all(gates[g][0] for g in GATES)}
    if d.parent.name == "staging":
        (d / "eval.json").write_bytes(canonical(rep, indent=1))
    return rep


def promote_policy(registry, model_id: str, approve: bool = False, approver: dict | None = None) -> dict:
    from computer.neural.registry import promote
    return promote(component_root(registry, COMPONENT), model_id, approve, load=load_manifest, approver=approver)
