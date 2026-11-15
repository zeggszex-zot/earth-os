"""python3 -m computer.acquire {status, plan, pin SOURCE, run SOURCE [--force], show SOURCE [RUN], integrate}

    --root    the acquisition folder (default ~/cdos/acquire): sources.toml, downloads/, staging/, index, journal
    --master  the corpus it dedupes against, read only (default ~/cdos/corpus/corpus.pf)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import DEFAULT_MASTER, DEFAULT_ROOT, INTEGRATE_OFF, AcquireError
from .pipeline import Acquirer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m computer.acquire", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["status", "plan", "pin", "run", "show", "integrate"])
    ap.add_argument("source", nargs="?")
    ap.add_argument("run_id", nargs="?")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--master", default=str(DEFAULT_MASTER))
    ap.add_argument("--force", action="store_true", help="stage again even when nothing changed")
    a = ap.parse_args(argv)
    acq = Acquirer(Path(a.root), Path(a.master))
    try:
        if a.command == "integrate":
            raise AcquireError(INTEGRATE_OFF)
        if a.command == "status":
            out = acq.status()
        elif a.command == "plan":
            out = acq.plan()
        elif a.command == "pin":
            if not a.source:
                ap.error("pin needs SOURCE")
            from . import registry
            out = acq.fetcher.pin(registry.get(acq.registry_path, a.source))
            out = {"pinned": out, "next": "check these, then copy each sha256 into sources.toml to approve it; "
                                          "nothing has been parsed"}
        elif a.command == "run":
            if not a.source:
                ap.error("run needs SOURCE")
            out = acq.run(a.source, force=a.force)
        else:
            if not a.source:
                ap.error("show needs SOURCE")
            runs = acq.runs(a.source)
            out = next((r for r in runs if r["run"] == a.run_id), None) if a.run_id else (runs[-1] if runs else None)
            if out is None:
                raise AcquireError(f"no staged run {a.run_id or ''} for {a.source}")
    except AcquireError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
