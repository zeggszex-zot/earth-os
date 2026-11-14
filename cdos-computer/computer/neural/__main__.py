"""python3 -m computer.neural <command>: train, evaluate, promote, and inspect the neural core.

    train       explore the corpus, train into <registry>/staging/<id>          (the active model is untouched)
    evaluate    rule-only vs untrained vs trained (vs active) on the same held-out fixtures; writes eval.json + gates
    promote     make a staged, evaluated model active: needs every gate passed AND --approve --reason, run by an
                operator (computer/governor.py), who is recorded
    rollback    re-activate a model promoted before (--approve)
    deactivate  no active model: the computer answers with the rules (--approve)
    status      the active model, staged and promoted models, and their gates
    bench       load time, per-question latency and memory on this machine
    log         verify | stats: the experience log's hash chain, and what it recorded
    schema      the feature schema and its fingerprint

--preset mini uses the small test corpus (tests/mini_corpus.py, built in a temporary folder) and the hand-written
fixtures in tests/, for reproducible development without the production corpus.  The full sequence:

    python3 -m computer.neural train    --preset mini --registry /tmp/reg
    python3 -m computer.neural evaluate --preset mini --registry /tmp/reg --model <id printed by train>
    python3 -m computer.neural promote  --registry /tmp/reg --model <id>              # shows what would happen
    python3 -m computer.neural promote  --registry /tmp/reg --model <id> --approve    # does it
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MINI_HELDOUT = ["tests/controller_heldout.jsonl", "tests/lexicon_heldout.jsonl"]
MINI_REGRESSION = ["tests/navigator_questions.jsonl", "tests/lexical_questions.jsonl", "tests/lexical_paraphrases.jsonl"]


def mini_corpus() -> Path:
    sys.path.insert(0, str(ROOT / "tests"))
    import mini_corpus as mc
    return mc.build(Path(tempfile.mkdtemp(prefix="cdos-mini-")) / "data")


def corpus_and_fixtures(a) -> tuple[Path, list, list]:
    if getattr(a, "preset", None) == "mini":
        return mini_corpus(), a.heldout or [str(ROOT / p) for p in MINI_HELDOUT], \
            a.regression or [str(ROOT / p) for p in MINI_REGRESSION]
    if not a.corpus:
        raise SystemExit("--corpus DIR (your data/ folder) or --preset mini")
    return Path(a.corpus), a.heldout, a.regression


def registry_of(a) -> Path:
    p = Path(a.registry)
    return p if p.is_absolute() else Path.cwd() / p


def say(obj) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m computer.neural", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, corpus=True, fixtures=True):
        p.add_argument("--registry", default="models/neural", help="the model registry folder (default models/neural)")
        if corpus:
            p.add_argument("--corpus", help="the corpus data/ folder (read only)")
            p.add_argument("--preset", choices=["mini"], help="the test corpus and fixtures in tests/")
            p.add_argument("--lexicon", help="relation lexicon (tools/learn_lexicon.py) to use, as the computer would")
        if fixtures:
            p.add_argument("--heldout", action="append", default=[], help="JSONL of hand-written held-out questions")
            p.add_argument("--regression", action="append", default=[], help="JSONL the rules must still get right")

    t = sub.add_parser("train", help="train into staging")
    common(t)
    t.add_argument("--seed", type=int, default=0, help="training seed (and data seed unless --data-seed)")
    t.add_argument("--data-seed", type=int)
    t.add_argument("--subjects", type=int, default=3000, help="corpus subjects to generate questions from")
    t.add_argument("--things", action="store_true", help="only subjects with non-dictionary facts")
    t.add_argument("--rewordings", type=int, default=3)
    t.add_argument("--mine", action="append", default=[], help="JSONL of your own questions with answers")
    t.add_argument("--epochs", type=int, default=40)
    t.add_argument("--hidden", type=int, default=32)
    t.add_argument("--lr", type=float, default=0.01)
    t.add_argument("--l2", type=float, default=1e-4)
    t.add_argument("--batch", type=int, default=32)
    t.add_argument("--threads", type=int, default=1, help="math-library threads (default 1)")
    t.add_argument("--nice", type=int, default=0, help="lower this process's CPU priority by N")
    t.add_argument("--max-seconds", type=float, help="stop training after this much wall time")
    t.add_argument("--max-cpu-seconds", type=float, help="stop training after this much CPU time")
    t.add_argument("--summary-records", action="store_true", help="keep only the winning candidates in the records")
    t.add_argument("--replace", action="store_true", help="re-stage a model id that is already staged")

    e = sub.add_parser("evaluate", help="evaluate a staged model; writes eval.json")
    common(e)
    e.add_argument("--model", required=True)
    e.add_argument("--json", action="store_true", help="print the full report as JSON")

    p = sub.add_parser("promote", help="activate a staged, evaluated model (needs --approve)")
    common(p, corpus=False, fixtures=False)
    p.add_argument("--model", required=True)
    p.add_argument("--approve", action="store_true")
    p.add_argument("--reason", default="", help="why (required with --approve; recorded by the Governor)")
    p.add_argument("--config", help="config.toml naming governor_policy and governor_dir (default: the computer's)")

    r = sub.add_parser("rollback", help="re-activate a previously promoted model (needs --approve)")
    common(r, corpus=False, fixtures=False)
    r.add_argument("--to", required=True)
    r.add_argument("--approve", action="store_true")
    r.add_argument("--reason", default="", help="why (required with --approve; recorded by the Governor)")
    r.add_argument("--config", help="config.toml naming governor_policy and governor_dir (default: the computer's)")

    dz = sub.add_parser("deactivate", help="no active model (needs --approve)")
    common(dz, corpus=False, fixtures=False)
    dz.add_argument("--approve", action="store_true")
    dz.add_argument("--reason", default="", help="why (required with --approve; recorded by the Governor)")
    dz.add_argument("--config", help="config.toml naming governor_policy and governor_dir (default: the computer's)")

    s = sub.add_parser("status", help="active, staged and promoted models")
    common(s, corpus=False, fixtures=False)

    b = sub.add_parser("bench", help="latency and memory on this machine")
    common(b, fixtures=False)
    b.add_argument("--model", help="a staged or promoted id (default: the active model)")
    b.add_argument("--questions", action="append", default=[], help="JSONL with a question field (repeatable)")
    b.add_argument("--repeat", type=int, default=3)
    b.add_argument("--precision", choices=["float", "int8", "int4"], default="float")

    lg = sub.add_parser("log", help="the experience log")
    common(lg, corpus=False, fixtures=False)
    lg.add_argument("what", choices=["verify", "stats"])

    sub.add_parser("schema", help="the feature schema and its fingerprint")

    a = ap.parse_args(argv)
    if a.cmd == "schema":
        from computer.neural.schema import describe, fingerprint
        say({"fingerprint": fingerprint(), **{k: v for k, v in describe().items() if k != "templates"}})
        return 0
    reg = registry_of(a)

    if a.cmd == "train":
        from computer.neural.train import TrainingError, limit_cpu
        limits = limit_cpu(a.threads, a.nice)              # before numpy is imported
        from computer.neural.train import train_to_staging
        corpus, heldout, regression = corpus_and_fixtures(a)
        try:
            d = train_to_staging(reg, corpus, seed=a.seed, data_seed=a.data_seed, subjects=a.subjects,
                                 rewordings=a.rewordings, epochs=a.epochs, hidden=a.hidden, lr=a.lr, l2=a.l2,
                                 batch=a.batch, mine=a.mine, heldout=heldout, regression=regression, lexicon=a.lexicon,
                                 things=a.things, max_seconds=a.max_seconds, max_cpu_seconds=a.max_cpu_seconds,
                                 limits=limits, detail=not a.summary_records, replace=a.replace)
        except TrainingError as err:
            print(f"training refused: {err}", file=sys.stderr)
            return 2
        m = json.loads((d / "manifest.json").read_text())
        print(d.name)
        print(f"staged in {d}; evaluate it next:\n  python3 -m computer.neural evaluate --registry {a.registry} "
              f"--model {d.name} " + ("--preset mini" if a.preset else f"--corpus {a.corpus}"), file=sys.stderr)
        print(json.dumps({"model_id": d.name, "metrics": m["metrics"], "resources": m["resources"],
                          "training_data_sha256": m["training_data"]["sha256"]}, indent=1), file=sys.stderr)
        return 0

    if a.cmd == "evaluate":
        from computer.neural.evaluate import evaluate, table
        corpus, heldout, regression = corpus_and_fixtures(a)
        rep = evaluate(reg, a.model, corpus, heldout, regression, a.lexicon)
        print(json.dumps(rep, indent=1) if a.json else table(rep))
        return 0 if rep["passed"] else 1

    if a.cmd in ("promote", "rollback", "deactivate"):
        from computer.neural import registry as R
        gov = decision = None
        target = f"reasoning.selector:{getattr(a, 'model', None) or getattr(a, 'to', None) or '(none)'}"
        if a.approve:                      # the dry run changes nothing and needs no authority
            from computer.governor import Forbidden, cli_decision
            try:
                gov, decision = cli_decision(a.config, f"model.{a.cmd}", target, a.reason, {"registry": str(reg)})
            except Forbidden as err:
                print(f"refused by the Governor: {err.why}", file=sys.stderr)
                return 4
        approver = decision.ref if decision else None
        try:
            out = (R.promote(reg, a.model, a.approve, approver=approver) if a.cmd == "promote" else
                   R.rollback(reg, a.to, a.approve, approver=approver) if a.cmd == "rollback" else
                   R.deactivate(reg, a.approve, approver=approver))
        except R.PromotionRefused as err:
            if gov:
                gov.result(decision, f"refused: {err}")
            print(f"refused: {err}", file=sys.stderr)
            return 2
        if gov:
            gov.result(decision, out.get("status", "done"),
                       {k: out[k] for k in ("model_id", "previous", "model_sha256", "eval_sha256", "manifest_sha256")
                        if k in out})
        say(out)
        return 0 if a.approve else 3

    if a.cmd == "status":
        from computer.neural.registry import status
        say(status(reg))
        return 0

    if a.cmd == "bench":
        from computer.neural.bench import bench, questions_from
        corpus, _, _ = corpus_and_fixtures(argparse.Namespace(**{**vars(a), "heldout": [], "regression": []}))
        qs = questions_from(a.questions or [str(ROOT / p) for p in MINI_HELDOUT])
        say(bench(reg, corpus, qs, a.model, a.repeat, a.precision))
        return 0

    if a.cmd == "log":
        from computer.neural import experience as X
        path = reg / "experience" / "experience.jsonl"
        if not path.exists():
            print(f"no experience log at {path}", file=sys.stderr)
            return 1
        out = X.verify(path) if a.what == "verify" else X.stats(path)
        say(out)
        return 0 if out.get("ok", True) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
