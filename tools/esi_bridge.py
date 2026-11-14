"""The CDOS Dictionary Bridge, for a person to run: ESI phase 2.

    python3 tools/esi_bridge.py propose --corpus DATA     # needs a built CDOS corpus
    python3 tools/esi_bridge.py list [--concept memory] [--blocked]
    python3 tools/esi_bridge.py show WORD CONCEPT
    python3 tools/esi_bridge.py approve WORD CONCEPT --by NAME [--override REASON]
    python3 tools/esi_bridge.py reject WORD CONCEPT --by NAME --reason TEXT
    python3 tools/esi_bridge.py status

DATA is a CDOS data/ folder, built from corpus.pf with
``python3 cdos-computer/tools/build_shards.py corpus.pf --out DATA --fix-wordnet``.

Proposals go to reviews/esi_bridge.json; nothing there is in use. Approving
writes earth_os/assets/lexicon/approved.json, which Earth OS reads on start.
Commit that file like any other change: approval is a change to the machine's
meaning, and it should be as visible in git as one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os import dictionary_bridge as bridge          # noqa: E402
from earth_os.lexical import APPROVED, load_approved      # noqa: E402

PROPOSALS = ROOT / "reviews" / "esi_bridge.json"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"No proposals at {path}. Run: {Path(__file__).name} propose --corpus DATA")


def cmd_propose(args) -> int:
    proposals = bridge.build_proposals(Path(args.corpus), Path(args.approved))
    bridge.write_json(Path(args.file), proposals)
    cands = proposals["candidates"]
    passed = [c for c in cands if c["status"] == "passed"]
    print(f"{len(cands)} candidates from {proposals['corpus']['status']}: "
          f"{len(passed)} passed every check, {len(cands) - len(passed)} blocked.")
    if proposals["without_anchor"]:
        print("No sense found for: " + ", ".join(proposals["without_anchor"]))
    print(f"Written to {args.file}. Nothing is in use until approved.")
    return 0


def cmd_list(args) -> int:
    cands = _load(Path(args.file))["candidates"]
    for c in cands:
        if args.concept and c["concept"] != args.concept:
            continue
        if (c["status"] == "blocked") != args.blocked:
            continue
        note = ""
        if c["status"] == "blocked":
            note = next(f"{k}: {v}" for k, v in c["checks"].items() if v != "ok")
        print(f"{c['confidence']:.2f}  {c['concept']:<11} {c['word']:<28} "
              f"{c['relation']:<8} via {c['anchor']}  {note}")
    return 0


def cmd_show(args) -> int:
    cand = bridge._find(_load(Path(args.file)), args.word, args.concept)
    if cand is None:
        print(f"{args.word!r} was not proposed for {args.concept!r}.")
        return 1
    print(json.dumps(cand, indent=2, ensure_ascii=False))
    return 0


def cmd_approve(args) -> int:
    ok, message = bridge.approve(args.word, args.concept, args.by, _load(Path(args.file)),
                                 Path(args.approved), override=args.override or "")
    print(message)
    return 0 if ok else 1


def cmd_reject(args) -> int:
    ok, message = bridge.reject(args.word, args.concept, args.by, args.reason,
                                Path(args.approved))
    print(message)
    return 0 if ok else 1


def cmd_status(args) -> int:
    store = bridge.read_store(Path(args.approved))
    loaded = load_approved(Path(args.approved))
    print(f"approved {len(store['approved'])}, rejected {len(store['rejected'])}, "
          f"in use {len(loaded.words) + len(loaded.phrases)}")
    for problem in loaded.problems:
        print(f"  not in use: {problem}")
    path = Path(args.file)
    if path.is_file():
        cands = json.loads(path.read_text(encoding="utf-8"))["candidates"]
        passed = sum(c["status"] == "passed" for c in cands)
        print(f"proposals: {len(cands)} ({passed} passed, {len(cands) - passed} blocked)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", default=str(PROPOSALS), help="the proposals file")
    parser.add_argument("--approved", default=str(APPROVED), help="the approved file")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("propose"); p.add_argument("--corpus", required=True)
    p.set_defaults(run=cmd_propose)
    p = sub.add_parser("list"); p.add_argument("--concept", default="")
    p.add_argument("--blocked", action="store_true"); p.set_defaults(run=cmd_list)
    for name, run in (("show", cmd_show), ("approve", cmd_approve), ("reject", cmd_reject)):
        p = sub.add_parser(name); p.add_argument("word"); p.add_argument("concept")
        if name in ("approve", "reject"):
            p.add_argument("--by", default="")
        if name == "approve":
            p.add_argument("--override", default="")
        if name == "reject":
            p.add_argument("--reason", default="")
        p.set_defaults(run=run)
    p = sub.add_parser("status"); p.set_defaults(run=cmd_status)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
