"""EDIE command line.

    python3 -m edie parse BOOK.tex --out OUTDIR [--profile auto|NAME|PATH] [--images DIR] [--strict]

Writes OUTDIR/<name>.edie.json, OUTDIR/<name>.md and OUTDIR/<name>.report.md, each atomically.  The source and
its folder are only ever read; an output path that would land on the source is refused.

Exit status: 0 parsed; 1 the source or profile could not be read; 2 parsed, but --strict and there were warnings.
"""

from __future__ import annotations

import argparse
import os
import resource
import sys
import time
from pathlib import Path

from . import SCHEMA, __version__
from .export.markdown import to_markdown
from .export.report import to_report
from .latex import signatures as sg
from .latex.parser import Parser
from .model import dumps
from .source import Source, SourceError


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def cmd_parse(a) -> int:
    t0 = time.perf_counter()
    try:
        src = Source.load(a.source)
        prof = sg.Profile.auto(src.text) if a.profile == "auto" else sg.Profile.load(a.profile)
    except (SourceError, ValueError) as e:
        print(f"edie: {e}", file=sys.stderr)
        return 1
    images = Path(a.images) if a.images else src.path.parent
    doc = Parser(src, prof, images).parse()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = src.path.name.rsplit(".", 1)[0]
    paths = {"json": out / f"{stem}.edie.json", "md": out / f"{stem}.md", "report": out / f"{stem}.report.md"}
    real_src = src.path.resolve()
    for p in paths.values():
        if p.resolve() == real_src:
            print(f"edie: refusing to write {p}: it is the source", file=sys.stderr)
            return 1
    base = a.image_base if a.image_base is not None else os.path.relpath(images.resolve(), out.resolve())
    base = "" if base == "." else Path(base).as_posix()
    write_atomic(paths["json"], dumps(doc))
    write_atomic(paths["md"], to_markdown(doc, base, anchors=not a.no_anchors))
    write_atomic(paths["report"], to_report(doc))
    st = doc["stats"]
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"edie {__version__} ({SCHEMA}): {src.path.name} sha256:{src.sha256[:12]}… "
          f"{st['blocks']} blocks, {st['inline_math']} inline + {st['display_math']} display math, "
          f"{st['figures']} figures ({st['images_missing']} missing), {st['tables']} tables, {st['lists']} lists, "
          f"{st['equation_tags']} equation tags, {st['warnings']} warnings, profile {prof.name} "
          f"in {time.perf_counter() - t0:.2f} s, peak RSS {rss:.0f} MB", file=sys.stderr)
    for p in paths.values():
        print(p)
    return 2 if a.strict and doc["warnings"] else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="edie", description="Earth Document Intelligence Engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse", help="parse a LaTeX document into edie-document/1, Markdown and a report")
    p.add_argument("source")
    p.add_argument("--out", required=True, help="output folder (created if needed)")
    p.add_argument("--profile", default="auto",
                   help="profile name or path to a profile .json; auto (default): pg-<ebook number> if EDIE has one, "
                        "else pg-latex")
    p.add_argument("--images", help="folder image paths are relative to (default: the source's folder)")
    p.add_argument("--image-base", help="prefix for image links in the Markdown (default: relative to --out)")
    p.add_argument("--no-anchors", action="store_true", help="leave source-line comments out of the Markdown")
    p.add_argument("--strict", action="store_true", help="exit 2 when there are any warnings")
    a = ap.parse_args(argv)
    return cmd_parse(a)


if __name__ == "__main__":
    raise SystemExit(main())
