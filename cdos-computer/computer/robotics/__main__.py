"""python3 -m computer.robotics <command>: the simulated robot, the safety envelope, the learned policy.

    simrobot          a simulated robot as a separate process: it sends its sensors to the API, asks for a motion,
                      and moves only on an approved command (systemd/cdos-robot-sim.service)
    sim               the same in one process, over many rooms, for a quick look (no API)
    envelope [file]   check a safety file and print its limits
    train-policy      train the learned policy in simulation into <registry>/components/robot.policy/staging
    evaluate-policy   held-out rooms: trained vs untrained vs expert vs naive (vs active) -> eval.json and gates
    promote-policy    activate it: every gate passed AND --approve --reason, by an operator (the Governor)
    status            the learned policy's active, staged and promoted models
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter


def simrobot(a) -> int:
    from computer.config import load
    from computer.robotics.safety import STOP
    from computer.robotics.sim import SimWorld
    from computer.services.client import Client
    cfg = load(a.config)
    cl = Client(a.socket or cfg.api_socket, a.port or cfg.api_tcp_port)
    w = SimWorld(a.seed, raw_temperature=a.raw_temperature)
    allowed = rejected = 0
    rules = Counter()
    for _ in range(a.steps):
        if w.status()["status"] != "running":
            break
        sub = cl.post("/v1/robot/observations", {"observations": w.observe()})
        if sub["refused"]:
            print(f"observations refused: {sub['refused']}", file=sys.stderr)
        d = cl.post("/v1/robot/action", {"goal": list(w.goal)})
        if d["allowed"]:
            allowed += 1
            w.apply(d["command"])
        else:
            rejected += 1
            rules.update(v["rule_id"] for v in d["violations"])
            w.apply(STOP)                     # only an approved command ever moves the robot
        if a.realtime:
            time.sleep(max(0.0, float(d["command"].get("duration", 0.0)) or 0.1))
    print(json.dumps({**w.status(), "seed": a.seed, "allowed": allowed, "rejected": rejected,
                      "rejections_by_rule": dict(rules)}, indent=1))
    return 0


def sim(a) -> int:
    from computer.robotics.learn import load_policy, run_episode
    from computer.robotics.policy import ExpertPolicy, NaivePolicy
    from computer.robotics.safety import Envelope
    env = Envelope.load(a.envelope)
    pol = {"expert": ExpertPolicy, "naive": NaivePolicy}.get(a.policy)
    pol = pol() if pol else load_policy(a.model_dir)[0]
    outs = [run_episode(s, pol, env) for s in range(a.seed, a.seed + a.rooms)]
    c = Counter(o["status"] for o in outs)
    dec = sum(o["steps"] for o in outs)
    print(json.dumps({"policy": pol.spec.ref, "rooms": a.rooms, **dict(c),
                      "rejection_rate": round(sum(o["rejected"] for o in outs) / max(1, dec), 4)}, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m computer.robotics", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("simrobot")
    r.add_argument("--config")
    r.add_argument("--socket")
    r.add_argument("--port", type=int)
    r.add_argument("--seed", type=int, default=10000)
    r.add_argument("--steps", type=int, default=200)
    r.add_argument("--raw-temperature", action="store_true", help="send the motor temperature as a raw sensor register")
    r.add_argument("--realtime", action="store_true", help="wait out each motion (as a real robot would)")
    s = sub.add_parser("sim")
    s.add_argument("--policy", choices=["expert", "naive", "learned"], default="expert")
    s.add_argument("--model-dir", help="a robot.policy model folder (for --policy learned)")
    s.add_argument("--seed", type=int, default=10000)
    s.add_argument("--rooms", type=int, default=20)
    s.add_argument("--envelope")
    e = sub.add_parser("envelope")
    e.add_argument("file", nargs="?")
    for name in ("train-policy", "evaluate-policy", "promote-policy", "status"):
        p = sub.add_parser(name)
        p.add_argument("--registry", default="models/neural")
        if name == "train-policy":
            p.add_argument("--seed", type=int, default=0)
            p.add_argument("--rooms", type=int, default=60)
            p.add_argument("--epochs", type=int, default=400)
        if name in ("evaluate-policy", "promote-policy"):
            p.add_argument("--model", required=True)
        if name == "promote-policy":
            p.add_argument("--approve", action="store_true")
            p.add_argument("--reason", default="", help="why (required with --approve; recorded by the Governor)")
            p.add_argument("--config", help="config.toml naming governor_policy and governor_dir")
    a = ap.parse_args(argv)

    if a.cmd == "simrobot":
        return simrobot(a)
    if a.cmd == "sim":
        return sim(a)
    if a.cmd == "envelope":
        from computer.robotics.safety import Envelope, EnvelopeError
        try:
            env = Envelope.load(a.file)
        except EnvelopeError as err:
            print(f"refused: {err}", file=sys.stderr)
            return 2
        print(json.dumps({"fingerprint": env.fingerprint, "limits": env.lim, "robot_radius_m": env.radius}, indent=1))
        return 0
    from computer.neural.registry import PromotionRefused, component_root, status
    from computer.robotics import learn
    if a.cmd == "train-policy":
        d = learn.train_policy(a.registry, a.seed, a.rooms, epochs=a.epochs, log=lambda *x: print(*x, file=sys.stderr))
        print(d.name)
        return 0
    if a.cmd == "evaluate-policy":
        rep = learn.evaluate_policy(a.registry, a.model)
        print(json.dumps({k: rep[k] for k in ("summary", "results", "gates", "passed")}, indent=1))
        return 0 if rep["passed"] else 1
    if a.cmd == "promote-policy":
        gov = decision = None
        if a.approve:
            from computer.governor import Forbidden, cli_decision
            try:
                gov, decision = cli_decision(a.config, "model.promote", f"{learn.COMPONENT}:{a.model}", a.reason,
                                             {"registry": str(a.registry)})
            except Forbidden as err:
                print(f"refused by the Governor: {err.why}", file=sys.stderr)
                return 4
        try:
            out = learn.promote_policy(a.registry, a.model, a.approve, approver=decision.ref if decision else None)
        except PromotionRefused as err:
            if gov:
                gov.result(decision, f"refused: {err}")
            print(f"refused: {err}", file=sys.stderr)
            return 2
        if gov:
            gov.result(decision, out.get("status", "done"),
                       {k: out[k] for k in ("model_id", "previous", "model_sha256", "eval_sha256") if k in out})
        print(json.dumps(out, indent=1, default=str))
        return 0 if a.approve else 3
    if a.cmd == "status":
        print(json.dumps(status(component_root(a.registry, learn.COMPONENT), load=learn.load_manifest), indent=1))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
