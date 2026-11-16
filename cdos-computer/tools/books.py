#!/usr/bin/env python3
"""Books -> corpus facts: a local model reads each paragraph of the EDIE books, a person accepts or rejects every fact
it proposes, and only accepted facts reach corpus.pf, as an approved source integrated through the guard with verify.

    python3 tools/books.py plan                 # the paragraphs a full run covers, and (after a trial) how long it takes
    python3 tools/books.py trial                # the fixed 50 paragraphs (bench/book_facts): scorecard, summary, compare
    python3 tools/books.py extract [--book calculus|gibbs] [--limit N]
                                                # every prose paragraph; resumes where it stopped; stages at the end
    python3 tools/books.py stage                # outputs -> staged.jsonl: well-formed, not already known, with source
    python3 tools/books.py review               # one fact at a time beside its sentence: a / r / s; remembers its place
    python3 tools/books.py status
    python3 tools/books.py export               # accepted facts -> books-<sha8>.pf, and the sources.toml entry for it

    --model    the Ollama model (default: <root>/settings.toml [books] model, else llama3.1:8b)
    --root     where runs live (default ~/cdos/books): <root>/<model>/ holds one model's run
    --master   the corpus facts are deduplicated against, read only (default ~/cdos/corpus/corpus.pf)

One definition of the extraction: the prompt, the options (temperature 0, seed 42), the Ollama client (installed models
only, never a pull, loopback only, each paragraph asked cold) and the fact-line check are the bench's
(bench/book_facts/bench.py), so the trial measures exactly what a full run does, and a trial paragraph gives the same
facts in both.

Nothing here writes corpus.pf.  Model output is staged, never integrated: a fact reaches the corpus only after a person
accepts it here, approves the exported file in ~/cdos/acquire/sources.toml (its sha256), and integrates it with
tools/integrate.py, which verifies the candidate corpus before replacing the master.  Every exported row is source_type
DOCUMENT with source_id book/<ebook>@<tex sha256 prefix>/<block>/L<first>-<last>; the source replaces that generation,
so each integration carries every accepted fact and a fact rejected later leaves the corpus.  docs/BOOKS_TO_CORPUS.md
has the steps.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import statistics
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench" / "book_facts"))

import bench  # noqa: E402
from computer import pfrow  # noqa: E402
from computer.acquire import DEFAULT_MASTER, AcquireError  # noqa: E402
from computer.acquire.index import MasterIndex, key_hash  # noqa: E402

DEFAULT_ROOT = Path("~/cdos/books").expanduser()
DEFAULT_MODEL = "llama3.1:8b"
SETTINGS_KEYS = {"model", "url"}
#: a text block with fewer words of prose than this, once its maths is taken out, is maths or a joining fragment
#: ("and", "Therefore,", "Try $y = x^5$."): it states nothing to extract
MIN_WORDS = 4
SOURCE_TYPE = "DOCUMENT"                 # ranked with MANUAL (computer/corpus.py SOURCE_RANK): a person accepted each
ID_PREFIX = "book/"
REPLACES = {"source_type": SOURCE_TYPE, "source_id": "^book/"}
CONFIDENCE = "0.9"
DECISIONS = ("accept", "reject", "skip")
KEYS = {"a": "accept", "r": "reject", "s": "skip"}
STOP = set("the a an of to in and or is are was be by for with as on at it its this that from which".split())


class BooksError(RuntimeError):
    pass


def sha256_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.part")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def append_line(path: Path, rec: dict) -> None:
    """One JSON line, on disk before this returns: a stop at any moment loses at most the line being written."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_lines(path: Path) -> list[dict]:
    """The JSON lines of a file written by append_line.  A last line cut short by a stop is dropped from the file (it
    is redone), never read as half a record; damage anywhere else is an error."""
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    lines = raw.split("\n")
    out = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            if i == len(lines) - 1:                       # the last, unterminated line: a write was interrupted
                write_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
                break
            raise BooksError(f"{path}: line {i + 1} isn't JSON; nothing is guessed") from None
    return out


# ---- settings -----------------------------------------------------------------------------------------------------

def settings(root: Path) -> dict:
    """<root>/settings.toml: [books] model = "...", url = "http://127.0.0.1:11434".  Anything else is refused."""
    p = Path(root) / "settings.toml"
    if not p.exists():
        return {}
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise BooksError(f"{p} can't be read: {e}") from None
    if set(data) - {"books"} or set(data.get("books", {})) - SETTINGS_KEYS:
        raise BooksError(f"{p} may hold only [books] with {', '.join(sorted(SETTINGS_KEYS))}")
    out = data.get("books", {})
    if not all(isinstance(v, str) and v for v in out.values()):
        raise BooksError(f"{p}: each setting is a non-empty string")
    return out


def run_dir(root: Path, model: str) -> Path:
    return Path(root) / re.sub(r"[^A-Za-z0-9.]+", "-", model)


# ---- the paragraphs -----------------------------------------------------------------------------------------------

_MATH = re.compile(r"\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)|\\\[.*?\\\]|\[\[figure:[^\]]*\]\]", re.S)


def prose_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]{2,}", _MATH.sub(" ", text))


def load_book(name: str, path: Path) -> dict:
    raw = Path(path).read_bytes()
    doc = json.loads(raw)
    src = doc.get("source") or {}
    meta = doc.get("metadata") or {}
    if not re.fullmatch(r"[0-9a-f]{64}", str(src.get("sha256", ""))):
        raise BooksError(f"{path}: EDIE's source has no sha256 of the book")
    return {"book": name, "ebook": Path(path).parent.name, "document": Path(path).name,
            "document_sha256": sha256_bytes(raw), "book_sha256": "sha256:" + src["sha256"],
            "book_file": src.get("filename", ""), "title": (meta.get("title") or {}).get("value", ""),
            "author": (meta.get("author") or {}).get("value", ""), "blocks": doc["blocks"]}


def paragraphs(books: dict = bench.BOOKS, only: str | None = None) -> tuple[list[dict], dict]:
    """(the paragraphs a run asks about, in book order; what was left out and why, by book)."""
    out, skipped = [], {}
    for name, path in books.items():
        if only and name != only:
            continue
        b = load_book(name, path)
        why = Counter()
        for blk in b["blocks"]:
            if blk.get("type") != "text":
                why[f"{blk.get('type')} block"] += 1
            elif not blk.get("section"):
                why["text before the first section (title page)"] += 1
            elif len(prose_words(blk["text"])) < MIN_WORDS:
                why[f"maths or a fragment (under {MIN_WORDS} words of prose)"] += 1
            else:
                out.append({"book": name, "ebook": b["ebook"], "book_sha256": b["book_sha256"],
                            "document_sha256": b["document_sha256"], "block": blk["id"], "lines": blk["lines"],
                            "pages": blk.get("pages"), "text": blk["text"]})
        skipped[name] = dict(sorted(why.items()))
    if only and only not in books:
        raise BooksError(f"no book {only!r} (books: {', '.join(books)})")
    return out, skipped


# ---- extraction ---------------------------------------------------------------------------------------------------

def _meta(model: str, ollama: bench.Ollama, books: dict) -> dict:
    info = ollama.installed(model)                        # refuses a model that isn't installed: never a pull
    template = bench.PROMPT.read_text(encoding="utf-8")
    docs = {name: load_book(name, p) for name, p in books.items()}
    return {"format": "cdos-books-run/1", "model": model, "model_digest": info.get("digest", ""),
            "model_details": info.get("details", {}), "model_size": info.get("size"), "ollama": ollama.version(),
            "options": bench.OPTIONS, "keep_alive": 0, "prompt_sha256": sha256_bytes(template.encode()),
            "min_words": MIN_WORDS,
            "books": {n: {k: d[k] for k in ("ebook", "document", "document_sha256", "book_sha256", "book_file",
                                            "title", "author")} for n, d in docs.items()}}


SAME = ("model", "model_digest", "options", "keep_alive", "prompt_sha256", "min_words", "books")


def extract(model: str, ollama: bench.Ollama, root: Path = DEFAULT_ROOT, books: dict = bench.BOOKS,
            only: str | None = None, limit: int | None = None, log=print, clock=time.perf_counter) -> dict:
    """Ask the model about every paragraph not yet asked, appending each answer to outputs.jsonl as it comes.  A run
    stopped at any point (Ctrl-C, power) resumes at the first paragraph without an answer.  Resuming with another
    model digest, prompt, options, rule or book file is refused: a run's answers all come from one setup."""
    d = run_dir(root, model)
    d.mkdir(parents=True, exist_ok=True)
    meta = _meta(model, ollama, books)
    rj = d / "run.json"
    if rj.exists():
        old = json.loads(rj.read_text(encoding="utf-8"))
        changed = [k for k in SAME if old.get(k) != meta.get(k)]
        if changed:
            raise BooksError(f"{d} was started with a different {', '.join(changed)}; a run's answers all come from "
                             "one setup. Move that folder away (or use another --root) to start over")
        meta = old
    else:
        meta["started"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_text(rj, json.dumps(meta, indent=1) + "\n")
    out_path = d / "outputs.jsonl"
    done = {(o["book"], o["block"]) for o in read_lines(out_path)}
    todo_all, _ = paragraphs(books, only)
    todo = [p for p in todo_all if (p["book"], p["block"]) not in done]
    if limit is not None:
        todo = todo[:limit]
    template = bench.PROMPT.read_text(encoding="utf-8")
    already = sum(1 for p in todo_all if (p["book"], p["block"]) in done)
    log(f"{model}: {already} of {len(todo_all)} paragraphs done; asking {len(todo)} now")
    walls = []
    for i, p in enumerate(todo, 1):
        t = clock()
        r = ollama.generate(model, template.replace("{paragraph}", p["text"]))
        wall = clock() - t
        walls.append(wall)
        rec = {**p, "output": r.get("response", ""), "done_reason": r.get("done_reason"), "wall_s": round(wall, 3),
               "prompt_tokens": r.get("prompt_eval_count"), "output_tokens": r.get("eval_count"),
               "prompt_s": (r.get("prompt_eval_duration") or 0) / 1e9,
               "output_s": (r.get("eval_duration") or 0) / 1e9, "load_s": (r.get("load_duration") or 0) / 1e9,
               "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        append_line(out_path, rec)
        left = (len(todo) - i) * statistics.mean(walls)
        log(f"{i:>4}/{len(todo)} {p['book']:<8} {p['block']}  {rec['output_tokens']} tokens  {wall:.1f} s  "
            f"about {left / 3600:.1f} h left")
    return {"run": str(d), "asked": len(todo), "done": len(done) + len(todo), "paragraphs": len(todo_all)}


# ---- staging ------------------------------------------------------------------------------------------------------

def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;:])\s+(?=[A-Z$(“\"])", text) if s.strip()]


def source_sentence(text: str, fact: dict) -> str:
    """The sentence of the paragraph that shares most words with the fact's subject and value (the model doesn't
    say which it read); "" when none shares any, and the reviewer reads the whole paragraph."""
    want = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", f"{fact['subject']} {fact['value']}")} - STOP
    best, score = "", 0
    for s in sentences(text):
        have = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", s)}
        n = len(want & have)
        if n > score:
            best, score = s, n
    return best


def fact_id(p: dict, f: dict) -> str:
    """Stable across re-staging: the book file, the block, and the fact's fields."""
    key = "\t".join([p["book_sha256"], p["block"], f["subject"], f["relation"], f["kind"].upper(), f["value"],
                     f["unit"]])
    return hashlib.blake2b(key.encode("utf-8"), digest_size=8).hexdigest()


def source_id(p: dict) -> str:
    lo, hi = p["lines"]
    return f"{ID_PREFIX}{p['ebook']}@{p['book_sha256'][7:19]}/{p['block']}/L{lo}-{hi}"


def pf_row(f: dict, p: dict) -> list[str]:
    return [f["subject"], f["relation"], f["kind"].upper(), f["value"], f["unit"], "0", "0", SOURCE_TYPE,
            source_id(p), CONFIDENCE, ""]


def stage(run: Path, master: Path = DEFAULT_MASTER, index: MasterIndex | None = None, log=print) -> dict:
    """outputs.jsonl -> staged.jsonl (for review) and rejects.jsonl, rebuilt whole every time from the outputs and
    the master as it is now.  A fact line is staged only if it is well-formed (bench.facts_of: 5 fields, an UPPER_CASE
    relation, a known kind, a number for N, a date for D), is not a fact the corpus has already (the build's own key,
    computer/pfrow.py; the books' own earlier generation doesn't count), and was not staged already from an earlier
    paragraph or line.  Each staged fact carries its source: the book's sha256, the block id and the lines."""
    run = Path(run)
    outs = read_lines(run / "outputs.jsonl")
    if not outs:
        raise BooksError(f"{run}: nothing extracted yet")
    if not Path(master).is_file():
        raise BooksError(f"no corpus at {master}: facts can't be checked against it")
    index = index or MasterIndex(Path(run).parent / "index.sqlite")
    meta, rebuilt = index.ensure(Path(master), REPLACES)
    if rebuilt:
        log(f"dedupe index built: {meta['keys']} facts in {master}")
    counts = Counter()
    staged, rejects, seen = [], [], set()
    try:
        for o in outs:
            counts["paragraphs"] += 1
            facts = bench.facts_of(o["output"])
            if not facts:
                counts["paragraphs_without_facts"] += 1
            for f in facts:
                counts["fact_lines"] += 1
                base = {"book": o["book"], "block": o["block"], "line": f["line"]}
                if f["auto_check"] != "ok":
                    counts["malformed"] += 1
                    rejects.append({**base, "why": f["auto_check"]})
                    continue
                if not f["subject"] or not f["value"]:
                    counts["malformed"] += 1
                    rejects.append({**base, "why": "malformed: an empty subject or value"})
                    continue
                if "\t" in f["line"]:
                    counts["malformed"] += 1
                    rejects.append({**base, "why": "malformed: a tab inside a field"})
                    continue
                row = pf_row(f, o)
                fsubj, fkind, start, until, fqual = pfrow.finish(row[0], row[1], row[2], row[3], row[5], row[6],
                                                                 row[7], row[8], row[10], True)
                h = key_hash(pfrow.fact_key(fsubj, row[1], fkind, row[3], row[4], start, until, fqual, True))
                if index.has(h):
                    counts["in_corpus"] += 1
                    continue
                if h in seen:
                    counts["repeated"] += 1
                    continue
                seen.add(h)
                staged.append({"id": fact_id(o, f), "book": o["book"], "ebook": o["ebook"],
                               "book_sha256": o["book_sha256"], "document_sha256": o["document_sha256"],
                               "block": o["block"], "lines": o["lines"], "pages": o.get("pages"),
                               "subject": f["subject"], "relation": f["relation"], "kind": f["kind"].upper(),
                               "value": f["value"], "unit": f["unit"], "row": row,
                               "sentence": source_sentence(o["text"], f), "paragraph": o["text"]})
    finally:
        index.close()
    counts["staged"] = len(staged)
    write_text(run / "staged.jsonl", "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in staged))
    write_text(run / "rejects.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rejects))
    report = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "master": str(master),
              "master_sha256": meta["master_sha256"], "counts": dict(counts),
              "reconciled": counts["fact_lines"] == counts["malformed"] + counts["in_corpus"] + counts["repeated"]
              + counts["staged"]}
    write_text(run / "stage.json", json.dumps(report, indent=1) + "\n")
    return report


# ---- review -------------------------------------------------------------------------------------------------------

def load_staged(run: Path) -> list[dict]:
    p = Path(run) / "staged.jsonl"
    if not p.exists():
        raise BooksError(f"{run}: nothing staged yet (tools/books.py stage)")
    return read_lines(p)


def decisions(run: Path) -> dict[str, str]:
    """fact id -> accept / reject / skip; the last word on each fact wins, and an undo takes it back."""
    out = {}
    for r in read_lines(Path(run) / "decisions.jsonl"):
        if r.get("decision") in DECISIONS:
            out[r["id"]] = r["decision"]
        else:
            out.pop(r["id"], None)
    return out


def next_fact(staged: list[dict], dec: dict) -> int | None:
    """Where review resumes: the first fact without a decision; once there are none, the first one skipped."""
    for want in (None, "skip"):
        for i, s in enumerate(staged):
            if dec.get(s["id"]) == want:
                return i
    return None


def _read_key(stdin) -> str:
    """One key, without Enter, on a terminal; one line's first character otherwise (a pipe, a test).  "" at the end."""
    if stdin.isatty():
        import termios
        import tty
        fd = stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            return os.read(fd, 1).decode("utf-8", "replace")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    line = stdin.readline()
    return line.strip()[:1] if line else ""


def review(run: Path, stdin=sys.stdin, out=sys.stdout, clock=time.time) -> dict:
    run = Path(run)
    staged = load_staged(run)
    path = run / "decisions.jsonl"
    bold = (lambda s: f"\033[1m{s}\033[0m") if out.isatty() else (lambda s: s)
    history: list[str] = []
    show_paragraph = False
    while True:
        dec = decisions(run)
        i = next_fact(staged, dec)
        c = Counter(dec.values())
        if i is None:
            out.write(f"\nEvery staged fact is decided: {c['accept']} accepted, {c['reject']} rejected. "
                      "Next: tools/books.py export\n")
            break
        s = staged[i]
        lo, hi = s["lines"]
        pages = s.get("pages") or []
        out.write(f"\n── fact {i + 1} of {len(staged)} · {c['accept']} accepted · {c['reject']} rejected · "
                  f"{c['skip']} skipped " + "─" * 20 + "\n")
        out.write(f"{s['book']}  {s['block']}  lines {lo}–{hi}" + (f"  page {pages[0]}" if pages else "") +
                  ("   (skipped before)" if dec.get(s["id"]) == "skip" else "") + "\n")
        if show_paragraph or not s["sentence"]:
            out.write(f"paragraph: {s['paragraph']}\n")
        else:
            out.write(f"sentence:  {s['sentence']}\n")
        out.write(bold(f"fact:      {s['subject']} | {s['relation']} | {s['kind']} | {s['value']} | {s['unit']}") +
                  "\n[a] accept  [r] reject  [s] skip  [p] paragraph  [u] undo  [q] quit  > ")
        out.flush()
        k = _read_key(stdin).lower()
        out.write(k + "\n")
        if k in ("", "q"):
            out.write("Your place is kept; tools/books.py review carries on from here.\n")
            break
        if k == "p":
            show_paragraph = not show_paragraph
            continue
        show_paragraph = False
        if k == "u":
            if history:
                append_line(path, {"id": history.pop(), "decision": None, "at": clock()})
            else:
                out.write("nothing to undo in this session\n")
            continue
        if k in KEYS:
            append_line(path, {"id": s["id"], "decision": KEYS[k], "at": clock(), "row": s["row"]})
            history.append(s["id"])
        else:
            out.write("keys: a, r, s, p, u, q\n")
    dec = decisions(run)
    c = Counter(dec.values())
    return {"staged": len(staged), "accept": c["accept"], "reject": c["reject"], "skip": c["skip"],
            "undecided": sum(1 for s in staged if s["id"] not in dec)}


# ---- export: the accepted facts as an approvable source ---------------------------------------------------------

def export(run: Path, today: str | None = None) -> dict:
    """The accepted facts, as one PF file named by its sha256 (books-<sha8>.pf, so an approved file is never
    overwritten), and the sources.toml entry that approves exactly that file.  Every accepted fact still staged is in
    it: the source replaces its earlier generation (source_id ^book/), so each integration carries the whole set."""
    run = Path(run)
    staged = load_staged(run)
    dec = decisions(run)
    meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
    rows = [s["row"] for s in staged if dec.get(s["id"]) == "accept"]
    if not rows:
        raise BooksError("no accepted facts to export (tools/books.py review)")
    books = meta["books"]
    head = (f"# books -> corpus: {len(rows)} facts extracted by {meta['model']} ({meta['model_digest'][:12]}) and "
            "accepted one by one by a person (tools/books.py review)\n" +
            "".join(f"# {b['ebook']}: {b['book_file']} {b['book_sha256']} ({b['title'][:60]}, {b['author']})\n"
                    for b in books.values()) + "# " + "\t".join(pfrow.COLUMNS) + "\n")
    body = head + "".join("\t".join(r) + "\n" for r in rows)
    data = body.encode("utf-8")
    sha = sha256_bytes(data)
    path = run / "export" / f"books-{sha[7:15]}.pf"
    write_text(path, body)
    rels = sorted({r[1] for r in rows})
    ebooks = ", ".join(f"#{b['ebook']}" for b in books.values())
    titles = "; ".join(f"{b['author']}, {b['title'][:60]}" for b in books.values())
    entry = "\n".join([
        "[source.books]",
        f'source_type = "{SOURCE_TYPE}"',
        f'licence     = "Public domain in the USA (Project Gutenberg eBooks {ebooks}); facts restated"',
        f'attribution = "Facts read from Project Gutenberg eBooks {ebooks} ({titles}); proposed by '
        f'{meta["model"]}, each accepted by a person"',
        f'url         = "{path.resolve().as_uri()}"',
        f'sha256      = "{sha}"',
        'parser      = "pf"',
        "relations   = { " + ", ".join(f'{r} = "{r}"' for r in rels) + " }",
        f'replaces    = {{ source_type = "{REPLACES["source_type"]}", source_id = "{REPLACES["source_id"]}" }}',
        f"max_bytes   = {max(1_000_000, 2 * len(data))}",
        'approved_by = ""            # your name: approving is a person\'s act, and an empty field is refused',
        f'approved_at = "{today or time.strftime("%Y-%m-%d")}"',
    ]) + "\n"
    rec = {"file": str(path), "sha256": sha, "facts": len(rows), "relations": rels, "model": meta["model"],
           "model_digest": meta["model_digest"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    write_text(run / "export" / f"books-{sha[7:15]}.json", json.dumps(rec, indent=1) + "\n")
    write_text(run / "export" / "sources-entry.toml", entry)
    return {**rec, "entry": entry}


# ---- trial, plan, status ------------------------------------------------------------------------------------------

def trial(model: str, ollama: bench.Ollama, results: Path = bench.RESULTS, log=print) -> dict:
    """The bench on its fixed 50 paragraphs: run, a blank scorecard to mark, a summary, and a comparison with every
    earlier run of the same sample and prompt (other models, or this one before)."""
    out = bench.run(model, ollama, results=results, log=log)
    bench.make_scorecard(out)
    bench.summary(out)
    compared = {}
    for other in sorted(Path(results).glob("*/*/run.json")):
        o = other.parent
        if o == out or not (o / "outputs.jsonl").exists():
            continue
        with contextlib.suppress(bench.BenchError, KeyError, ValueError):
            compared[str(o)] = bench.compare(o, out)
    return {"run": str(out), "summary": (out / "summary.md").read_text(encoding="utf-8"), "compared": compared}


def estimate(trial_dir: Path, paras: list[dict]) -> dict:
    """Hours for a full run, from a trial's own timings: prompt tokens fitted to paragraph length, the trial's prompt
    and output speeds, its mean output and its load time.  Cold every paragraph, as the run does."""
    outs = [json.loads(x) for x in (Path(trial_dir) / "outputs.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()]
    sample = {r["n"]: r for r in bench.load_sample()}
    xs = [len(sample[o["n"]]["text"]) for o in outs]
    ys = [o["prompt_tokens"] for o in outs]
    slope, intercept = statistics.linear_regression(xs, ys)
    prompt_rate = sum(ys) / sum(o["prompt_s"] for o in outs)
    out_rate = sum(o["output_tokens"] for o in outs) / sum(o["output_s"] for o in outs)
    out_tokens = statistics.mean(o["output_tokens"] for o in outs)
    load = statistics.median(o["load_s"] for o in outs)
    secs = sum((intercept + slope * len(p["text"])) / prompt_rate + out_tokens / out_rate + load for p in paras)
    return {"trial": str(trial_dir), "prompt_tokens_per_s": round(prompt_rate, 2),
            "output_tokens_per_s": round(out_rate, 2), "mean_output_tokens": round(out_tokens, 1),
            "load_s": round(load, 1), "hours": round(secs / 3600, 1)}


def plan(model: str, books: dict = bench.BOOKS, results: Path = bench.RESULTS) -> dict:
    paras, skipped = paragraphs(books)
    by = Counter(p["book"] for p in paras)
    out = {"model": model, "paragraphs": dict(by), "total": len(paras), "left_out": skipped,
           "characters": sum(len(p["text"]) for p in paras)}
    trials = sorted((Path(results) / re.sub(r"[^A-Za-z0-9.]+", "-", model)).glob("*/outputs.jsonl"))
    out["estimate"] = estimate(trials[-1].parent, paras) if trials else (
        f"no trial of {model} yet: `tools/books.py trial --model {model}` measures its speed on this machine")
    return out


def status(root: Path, model: str) -> dict:
    d = run_dir(root, model)
    if not (d / "run.json").exists():
        return {"run": str(d), "started": False}
    outs = read_lines(d / "outputs.jsonl")
    paras, _ = paragraphs(bench.BOOKS)
    out = {"run": str(d), "asked": len(outs), "paragraphs": len(paras),
           "hours_so_far": round(sum(o["wall_s"] for o in outs) / 3600, 1)}
    if (d / "stage.json").exists():
        out["stage"] = json.loads((d / "stage.json").read_text(encoding="utf-8"))["counts"]
        staged = read_lines(d / "staged.jsonl")
        dec = decisions(d)
        c = Counter(dec.get(s["id"], "undecided") for s in staged)
        out["review"] = dict(c)
    out["exports"] = sorted(p.name for p in (d / "export").glob("books-*.pf")) if (d / "export").exists() else []
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["plan", "trial", "extract", "stage", "review", "status", "export"])
    ap.add_argument("--model")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--master", default=str(DEFAULT_MASTER))
    ap.add_argument("--url")
    ap.add_argument("--book", choices=sorted(bench.BOOKS))
    ap.add_argument("--limit", type=int, help="extract: ask at most this many paragraphs, then stop")
    ap.add_argument("--no-stage", action="store_true", help="extract: don't stage at the end")
    a = ap.parse_args(argv)
    root = Path(a.root).expanduser()
    log = lambda m: print(m, flush=True)                                                     # noqa: E731
    try:
        s = settings(root)
        model = a.model or s.get("model") or DEFAULT_MODEL
        url = a.url or s.get("url") or bench.OLLAMA
        d = run_dir(root, model)
        if a.command == "plan":
            res = plan(model)
        elif a.command == "trial":
            res = trial(model, bench.Ollama(url), log=log)
            print(res.pop("summary"))
            for k, v in res.pop("compared").items():
                print(f"\nagainst {k}:\n{v}")
        elif a.command == "extract":
            try:
                res = extract(model, bench.Ollama(url), root, only=a.book, limit=a.limit, log=log)
            except KeyboardInterrupt:
                print(f"\nstopped; every answer so far is kept in {d / 'outputs.jsonl'}. "
                      "Run extract again to carry on.", file=sys.stderr)
                return 130
            if not a.no_stage:
                res["stage"] = stage(d, Path(a.master), log=log)
        elif a.command == "stage":
            res = stage(d, Path(a.master), log=log)
        elif a.command == "review":
            res = review(d)
        elif a.command == "status":
            res = status(root, model)
        else:
            res = export(d)
            print(res.pop("entry"))
            res["next"] = ("copy the entry above into ~/cdos/acquire/sources.toml (replacing an earlier [source.books]),"
                           " put your name in approved_by, then: python3 -m computer.acquire run books")
    except (BooksError, bench.BenchError, AcquireError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
