# Book-facts bench

How well a local model turns one paragraph of a book into PF facts. A bench
test only: model output is never knowledge. Every fact it records carries
`source_type LLM`, which the reader never treats as a fact, and nothing here
reads or writes `corpus.pf`. Findings go to review, like the Pi searcher's.

| File | What it is |
|---|---|
| `sample.jsonl` | the fixed 50 paragraphs: 25 from *Calculus Made Easy* (33283), 25 from Gibbs (50992), from EDIE's output in `~/earth-os-library/edie/`. Each has its block id, source lines, pages and the sha256 of the EDIE document. Rule: body text, 250–1,500 characters, at most 3 inline formulas, evenly spaced through the book (no randomness) |
| `prompt.txt` | the one extraction prompt; `{paragraph}` is replaced by the paragraph |
| `bench.py` | `sample`, `run`, `scorecard`, `summary`, `compare` |
| `results/<model>/<run>/` | `run.json` (model, digest, Ollama version, options, prompt and sample sha256, reproducibility check), `outputs.jsonl` (each raw output, tokens, times, tokens/s), `review.pf` (the facts as LLM rows, for review), `scorecard.tsv`, `summary.md` |

Options for every run: temperature 0, seed 42, a 4,096-token context, at
most 512 output tokens. The first paragraph is asked again at the end of a
run: `run.json` says whether the output was the same.

## Marking

`bench.py scorecard RUN_DIR` writes `scorecard.tsv`, one row per fact line
(a line with `|`). Fill the `mark` column with one of:

- `correct`: the paragraph states it, and the row says it right;
- `wrong`: the row says something the paragraph contradicts or misstates;
- `unsupported`: maybe true, but the paragraph doesn't state it;
- `malformed`: not a usable fact row (the `auto_check` column flags the ones a
  machine can see, but you decide).

`note` is free text. Then `bench.py summary RUN_DIR`. `scorecard` refuses to
overwrite a scorecard, so your marks are never lost.

## Another model

See `docs/DOWNLOADS_NEEDED.md` for what to fetch. `tools/books.py trial` runs
another model on this sample and compares it with every earlier run; the same
code then reads the whole of both books (`docs/BOOKS_TO_CORPUS.md`).
