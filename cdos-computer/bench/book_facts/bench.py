#!/usr/bin/env python3
"""Book-facts bench: how well a local model turns a book paragraph into PF facts.  A measurement, not an importer:
nothing here reads or writes corpus.pf, and every fact it records is marked source_type LLM, which the reader never
treats as knowledge (computer/corpus.py NOT_KNOWLEDGE).  Findings go to review, like the Pi searcher's.

    python3 bench/book_facts/bench.py sample                     # once: the fixed 50 paragraphs -> sample.jsonl
    python3 bench/book_facts/bench.py run --model llama3.2:1b    # -> results/<model>/<run>/{run.json,outputs.jsonl}
    python3 bench/book_facts/bench.py scorecard RUN_DIR          # -> RUN_DIR/scorecard.tsv, for a person to mark
    python3 bench/book_facts/bench.py summary RUN_DIR            # -> RUN_DIR/summary.md (marked or not)
    python3 bench/book_facts/bench.py compare RUN_DIR RUN_DIR    # two models, side by side

What stays fixed, so a later run with another model (llama3.1:8b) is a direct comparison:
  * the sample: sample.jsonl (25 paragraphs of Calculus Made Easy, 25 of Gibbs), with the sha256 of each EDIE
    document it came from; `sample` refuses to overwrite it;
  * the prompt: prompt.txt, whose sha256 every run records;
  * the options: OPTIONS (temperature 0, seed 42, context, output cap).
Every run records the model's digest and Ollama's version, each paragraph's raw output, its timings and tokens per
second, and repeats the first paragraph at the end to check the output is reproducible.  Each request starts the
model cold (keep_alive 0): Ollama otherwise reuses the cached start of the previous prompt, which changes the
arithmetic, so an output would depend on the paragraph asked before it (measured 2026-11-15 with llama3.2:1b: the
same paragraph gave different facts warm and cold, and the same facts every time cold).  Load time is recorded
apart from generation, so tokens per second aren't affected.

Only a model already installed is used: the bench asks Ollama which models it has and refuses one it hasn't
(docs/DOWNLOADS_NEEDED.md lists what to fetch).  It never asks Ollama to pull, and talks only to a loopback address.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from computer import pfrow  # noqa: E402

SAMPLE = HERE / "sample.jsonl"
PROMPT = HERE / "prompt.txt"
RESULTS = HERE / "results"
LIBRARY = Path("~/earth-os-library/edie").expanduser()
BOOKS = {"calculus": LIBRARY / "33283" / "33283-t.edie.json", "gibbs": LIBRARY / "50992" / "50992-t.edie.json"}
PER_BOOK = 25
# Which paragraphs can be drawn: body text (inside a section), 250 to 1,500 characters, at most 3 inline formulas
MIN_CHARS, MAX_CHARS, MAX_INLINE_MATH = 250, 1500, 3
OLLAMA = "http://127.0.0.1:11434"
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 512}
TIMEOUT = 600
MARKS = ("correct", "wrong", "unsupported", "malformed")
SCORE_COLUMNS = ["n", "book", "block", "line", "repeats", "subject", "relation", "kind", "value", "unit", "auto_check",
                 "mark", "note"]


class BenchError(RuntimeError):
    pass


def sha256_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---- the sample -------------------------------------------------------------------------------------------------

def eligible(blocks: list) -> list:
    return [b for b in blocks if b.get("type") == "text" and b.get("section")
            and MIN_CHARS <= len(b["text"]) <= MAX_CHARS and len(b.get("inline_math") or []) <= MAX_INLINE_MATH]


def evenly(items: list, k: int) -> list:
    """k items spread evenly from first to last: no randomness, so the same documents give the same sample."""
    if len(items) < k:
        raise BenchError(f"only {len(items)} paragraphs qualify; {k} are needed")
    return [items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)]


def make_sample(books: dict = BOOKS, out: Path = SAMPLE, k: int = PER_BOOK) -> list:
    if out.exists():
        raise BenchError(f"{out} exists: the sample is fixed, so every model is measured on the same paragraphs")
    rows = []
    for book, path in books.items():
        raw = Path(path).read_bytes()
        doc = json.loads(raw)
        for b in evenly(eligible(doc["blocks"]), k):
            rows.append({"n": len(rows) + 1, "book": book, "document": Path(path).name, "document_sha256":
                         sha256_bytes(raw), "block": b["id"], "lines": b["lines"], "pages": b.get("pages"),
                         "text": b["text"]})
    write_text(out, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return rows


def load_sample(path: Path = SAMPLE) -> list:
    if not path.exists():
        raise BenchError(f"no sample at {path}: run `bench.py sample` once")
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---- talking to Ollama (loopback only, never a pull) -------------------------------------------------------------

def _loopback(url: str) -> str:
    host = urlparse(url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise BenchError(f"{url}: the bench talks only to a local Ollama (127.0.0.1)")
    return url.rstrip("/")


class Ollama:
    def __init__(self, url: str = OLLAMA, opener=None):
        self.url = _loopback(url)
        self.open = opener or (lambda req: urllib.request.urlopen(req, timeout=TIMEOUT))

    def _call(self, path: str, body: dict | None = None) -> dict:
        if path not in ("/api/version", "/api/tags", "/api/generate"):
            raise BenchError(f"{path}: not an endpoint the bench uses")           # only these: nothing that downloads
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        with self.open(req) as r:
            return json.loads(r.read())

    def version(self) -> str:
        return self._call("/api/version").get("version", "")

    def installed(self, model: str) -> dict:
        for m in self._call("/api/tags").get("models", []):
            if m.get("name") == model or m.get("model") == model:
                return m
        raise BenchError(f"{model} isn't installed in Ollama; the bench doesn't download models "
                         "(see docs/DOWNLOADS_NEEDED.md)")

    def generate(self, model: str, prompt: str) -> dict:
        return self._call("/api/generate", {"model": model, "prompt": prompt, "stream": False, "options": OPTIONS,
                                            "keep_alive": 0})          # cold every time: see the module docstring


# ---- the output, as facts ----------------------------------------------------------------------------------------

def facts_of(output: str) -> list[dict]:
    """Each line that tries to be a fact (has a "|"), split into fields and checked like a corpus row.  Lines without
    "|" (NONE, chatter) aren't facts; they stay in outputs.jsonl."""
    out = []
    for line in output.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        f = [x.strip() for x in line.split("|")]
        if len(f) == 4:
            f.append("")                            # "a | B | S | c" with the empty unit left off
        rec = {"line": line, "subject": "", "relation": "", "kind": "", "value": "", "unit": "", "auto_check": "ok"}
        if len(f) != 5:
            rec["auto_check"] = f"malformed: {len(f)} fields, not 5"
            out.append(rec)
            continue
        rec.update(subject=f[0], relation=f[1], kind=f[2], value=f[3], unit=f[4])
        why = []
        if not f[0]:
            why.append("no subject")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", f[1]):
            why.append("relation isn't UPPER_CASE")
        if not f[3]:
            why.append("no value")
        why += pfrow.field_problems(f[2], f[3], "", "", "1.0", "")
        if why:
            rec["auto_check"] = "malformed: " + "; ".join(why)
        out.append(rec)
    return out


def pf_row(fact: dict, sample_row: dict, model: str) -> str:
    """The fact as a PF row for review: source_type LLM (never knowledge), source_id book:block, confidence 0."""
    return "\t".join([fact["subject"], fact["relation"], fact["kind"], fact["value"], fact["unit"], "0", "0", "LLM",
                      f"{sample_row['book']}:{sample_row['block']}", "0.0", f"MODEL={re.sub(r'[^A-Za-z0-9.]', '_', model)}"])


# ---- a run --------------------------------------------------------------------------------------------------------

def run(model: str, ollama: Ollama | None = None, sample_path: Path = SAMPLE, results: Path = RESULTS,
        limit: int | None = None, log=print) -> Path:
    ollama = ollama or Ollama()
    sample = load_sample(sample_path)[:limit] if limit else load_sample(sample_path)
    template = PROMPT.read_text(encoding="utf-8")
    info = ollama.installed(model)
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = results / re.sub(r"[^A-Za-z0-9.]+", "-", model) / run_id
    out.mkdir(parents=True)
    meta = {"format": "cdos-book-bench/1", "model": model, "model_digest": info.get("digest", ""),
            "model_details": info.get("details", {}), "model_size": info.get("size"), "ollama": ollama.version(),
            "options": OPTIONS, "prompt_sha256": sha256_bytes(template.encode()),
            "sample_sha256": sha256_bytes(sample_path.read_bytes()), "paragraphs": len(sample),
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cpus": os.cpu_count()}
    write_text(out / "run.json", json.dumps(meta, indent=1) + "\n")
    lines = []
    for row in sample:
        t = time.perf_counter()
        r = ollama.generate(model, template.replace("{paragraph}", row["text"]))
        wall = time.perf_counter() - t
        rec = {"n": row["n"], "book": row["book"], "block": row["block"], "output": r.get("response", ""),
               "done_reason": r.get("done_reason"), "wall_s": round(wall, 3),
               "prompt_tokens": r.get("prompt_eval_count"), "output_tokens": r.get("eval_count"),
               "prompt_s": (r.get("prompt_eval_duration") or 0) / 1e9, "output_s": (r.get("eval_duration") or 0) / 1e9,
               "load_s": (r.get("load_duration") or 0) / 1e9, "total_s": (r.get("total_duration") or 0) / 1e9}
        rec["tokens_per_s"] = round(rec["output_tokens"] / rec["output_s"], 2) if rec["output_s"] and rec[
            "output_tokens"] else None
        lines.append(json.dumps(rec, ensure_ascii=False))
        write_text(out / "outputs.jsonl", "\n".join(lines) + "\n")          # whole after every paragraph
        log(f"{row['n']:>2}/{len(sample)} {row['book']:<8} {row['block']}  {rec['output_tokens']} tokens "
            f"{rec['tokens_per_s']} tok/s  {wall:.1f} s")
    again = ollama.generate(model, template.replace("{paragraph}", sample[0]["text"])).get("response", "")
    first = json.loads(lines[0])["output"]
    meta.update(finished=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                reproducible={"paragraph": sample[0]["n"], "same_output": again == first})
    write_text(out / "run.json", json.dumps(meta, indent=1) + "\n")
    review = [pf_row(f, row, model) for row, l in zip(sample, lines) for f in facts_of(json.loads(l)["output"])]
    write_text(out / "review.pf", "# model output for review only: source_type LLM is never knowledge; never "
               "integrated into corpus.pf\n" + "".join(x + "\n" for x in review))
    return out


# ---- scoring ------------------------------------------------------------------------------------------------------

def make_scorecard(run_dir: Path) -> Path:
    path = run_dir / "scorecard.tsv"
    if path.exists():
        raise BenchError(f"{path} exists: it may hold your marks; move it away first to make a new one")
    rows = []
    for line in (run_dir / "outputs.jsonl").read_text(encoding="utf-8").splitlines():
        o = json.loads(line)
        seen: dict = {}
        for f in facts_of(o["output"]):                # a line repeated in one output is one fact, marked once
            if f["line"] in seen:
                seen[f["line"]]["repeats"] += 1
                continue
            seen[f["line"]] = {"n": o["n"], "book": o["book"], "block": o["block"], **f, "repeats": 1, "mark": "",
                               "note": ""}
        rows.extend(seen.values())
    tmp = path.with_name(".scorecard.tsv.part")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, SCORE_COLUMNS, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    return path


def read_scorecard(run_dir: Path) -> list[dict]:
    path = run_dir / "scorecard.tsv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    bad = sorted({r["mark"] for r in rows if r["mark"].strip() and r["mark"].strip().lower() not in MARKS})
    if bad:
        raise BenchError(f"{path}: marks must be one of {', '.join(MARKS)} (or empty); found {bad}")
    return rows


def stats(run_dir: Path) -> dict:
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    outs = [json.loads(x) for x in (run_dir / "outputs.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    card = read_scorecard(run_dir)
    facts = [f for o in outs for f in facts_of(o["output"])]
    distinct = sum(len({f["line"] for f in facts_of(o["output"])}) for o in outs)
    tps = [o["tokens_per_s"] for o in outs if o.get("tokens_per_s")]
    s = {"model": meta["model"], "digest": meta.get("model_digest", "")[:12], "paragraphs": len(outs),
         "facts": len(facts), "facts_per_paragraph": round(len(facts) / len(outs), 2) if outs else 0,
         "distinct_facts": distinct,
         "auto_malformed": sum(f["auto_check"] != "ok" for f in facts),
         "paragraphs_without_facts": sum(not facts_of(o["output"]) for o in outs),
         "cut_off": sum(o.get("done_reason") == "length" for o in outs),
         "tokens_per_s_median": round(statistics.median(tps), 1) if tps else None,
         "seconds_total": round(sum(o["wall_s"] for o in outs), 1),
         "seconds_per_paragraph_median": round(statistics.median(o["wall_s"] for o in outs), 1) if outs else None,
         "reproducible": (meta.get("reproducible") or {}).get("same_output"),
         "prompt_sha256": meta["prompt_sha256"], "sample_sha256": meta["sample_sha256"]}
    marks = {m: 0 for m in MARKS}
    by_book: dict = {}
    for r in card:
        m = r["mark"].strip().lower()
        if m:
            marks[m] += 1
            by_book.setdefault(r["book"], {x: 0 for x in MARKS})[m] += 1
    marked = sum(marks.values())
    s.update(scorecard=bool(card), marked=marked, unmarked=len(card) - marked, marks=marks, marks_by_book=by_book,
             precision=round(marks["correct"] / marked, 3) if marked else None)
    return s


def summary(run_dir: Path) -> Path:
    s = stats(run_dir)
    m = s["marks"]
    lines = [f"# Book-facts bench: {s['model']} ({s['digest']})", "",
             f"Sample `{s['sample_sha256'][:19]}…`, prompt `{s['prompt_sha256'][:19]}…`, options {json.dumps(OPTIONS)}.",
             "", "| | |", "|---|---|",
             f"| paragraphs | {s['paragraphs']} |", f"| fact lines | {s['facts']} ({s['facts_per_paragraph']} a paragraph) |",
             f"| distinct within their paragraph (the scorecard's rows) | {s['distinct_facts']} |",
             f"| malformed (automatic check) | {s['auto_malformed']} |",
             f"| paragraphs with no fact lines | {s['paragraphs_without_facts']} |",
             f"| outputs cut off at the length cap | {s['cut_off']} |",
             f"| speed | {s['tokens_per_s_median']} tokens/s median; {s['seconds_per_paragraph_median']} s a paragraph; "
             f"{s['seconds_total']} s in all |",
             f"| same output when paragraph 1 is asked again | {s['reproducible']} |", ""]
    if not s["scorecard"]:
        lines.append("No scorecard yet: `bench.py scorecard RUN_DIR`, then mark each fact.")
    elif not s["marked"]:
        lines.append(f"Nothing marked yet: {s['unmarked']} facts in scorecard.tsv.")
    else:
        lines += [f"Marked {s['marked']} of {s['marked'] + s['unmarked']} facts.", "",
                  "| mark | all | " + " | ".join(sorted(s["marks_by_book"])) + " |",
                  "|---|---|" + "---|" * len(s["marks_by_book"])]
        for k in MARKS:
            lines.append(f"| {k} | {m[k]} | " + " | ".join(str(s["marks_by_book"][b][k])
                                                          for b in sorted(s["marks_by_book"])) + " |")
        lines += ["", f"Precision (correct / marked): {s['precision']}"]
    lines += ["", "Model output only: nothing here is in corpus.pf; review.pf is for the review queue."]
    path = run_dir / "summary.md"
    write_text(path, "\n".join(lines) + "\n")
    return path


def compare(a: Path, b: Path) -> str:
    sa, sb = stats(a), stats(b)
    if (sa["sample_sha256"], sa["prompt_sha256"]) != (sb["sample_sha256"], sb["prompt_sha256"]):
        raise BenchError("the two runs used different samples or prompts: not a direct comparison")
    keys = ["facts", "distinct_facts", "facts_per_paragraph", "auto_malformed", "paragraphs_without_facts", "cut_off", "marked",
            "precision", "tokens_per_s_median", "seconds_per_paragraph_median", "reproducible"]
    rows = [f"| | {sa['model']} | {sb['model']} |", "|---|---|---|"]
    rows += [f"| {k} | {sa[k]} | {sb[k]} |" for k in keys]
    rows += [f"| {m} | {sa['marks'][m]} | {sb['marks'][m]} |" for m in MARKS]
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    r = sub.add_parser("run")
    r.add_argument("--model", required=True)
    r.add_argument("--url", default=OLLAMA)
    r.add_argument("--limit", type=int)
    for name in ("scorecard", "summary"):
        sub.add_parser(name).add_argument("run_dir")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "sample":
            rows = make_sample()
            print(f"{SAMPLE}: {len(rows)} paragraphs")
        elif a.cmd == "run":
            print(run(a.model, Ollama(a.url), limit=a.limit, log=lambda m: print(m, flush=True)))
        elif a.cmd == "scorecard":
            print(make_scorecard(Path(a.run_dir)))
        elif a.cmd == "summary":
            print(summary(Path(a.run_dir)).read_text(encoding="utf-8"))
        else:
            print(compare(Path(a.a), Path(a.b)))
    except BenchError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
