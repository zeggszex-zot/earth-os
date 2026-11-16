# Downloads needed

What to fetch on a good connection. Nothing in this repository downloads these
by itself: the book-facts bench and `tools/books.py` use only models Ollama
already has, and the corpus pipeline fetches only approved, pinned sources.
When an item is on the machine, tick it off and date it.

| What | Why | Size | How | Check after |
|---|---|---|---|---|
| `llama3.1:8b` (Ollama) | turns the two books into corpus facts (`cdos-computer/tools/books.py`, steps in `docs/BOOKS_TO_CORPUS.md`); its trial on the bench's 50 paragraphs compares directly with `llama3.2:1b` | about 4.9 GB on disk (Ollama's default 4-bit build, Q4_K_M); about 5.5 GiB of RAM to run at a 4,096-token context (about 9.7 GiB is free on this desktop) | `ollama pull llama3.1:8b` | `ollama list` shows it; `ollama show llama3.1:8b` gives its digest, which every run records in `run.json` |

Already here: `llama3.2:1b` (1.3 GB), the bench's first model and the tiny
connection check.

Free disk on 2026-11-16: 80 GB.

## After `llama3.1:8b` is on the machine

Follow `docs/BOOKS_TO_CORPUS.md`. In short:

```bash
cd cdos-computer
python3 tools/books.py trial           # the fixed 50 paragraphs; prints the comparison with llama3.2:1b
# mark bench/book_facts/results/llama3.1-8b/<run>/scorecard.tsv, then
python3 bench/book_facts/bench.py summary bench/book_facts/results/llama3.1-8b/<run>
python3 tools/books.py plan            # the full run's hours, from the trial's timings
python3 tools/books.py extract         # days; resumable
python3 tools/books.py review
python3 tools/books.py export          # then approve it in sources.toml and integrate
```

The sample, the prompt and the options stay the same, so the two models'
trials compare directly. `bench.py compare` refuses runs made with a
different sample or prompt.
