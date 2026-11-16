# Downloads needed

What to fetch on a good connection. Nothing in this repository downloads these
by itself: the book-facts bench uses only models Ollama already has, and the
corpus pipeline fetches only approved, pinned sources. Tick an item off (and
date it) when it is on the machine.

| What | Why | Size | How | Check after |
|---|---|---|---|---|
| `llama3.1:8b` (Ollama) | the second model for the book-facts bench (`cdos-computer/bench/book_facts/`), compared directly with `llama3.2:1b` | about 4.9 GB on disk (Ollama's default 4-bit build); needs about 6–8 GB of free RAM to run (this desktop has 15 GB) | `ollama pull llama3.1:8b` | `ollama list` shows it; `ollama show llama3.1:8b` gives its digest, which the bench records in `run.json` |

Free disk on 2026-11-15: 81 GB.

After `llama3.1:8b` is on the machine, the comparison is:

```bash
cd cdos-computer
python3 bench/book_facts/bench.py run --model llama3.1:8b
python3 bench/book_facts/bench.py scorecard bench/book_facts/results/llama3.1-8b/<run>
# mark the scorecard, then
python3 bench/book_facts/bench.py summary bench/book_facts/results/llama3.1-8b/<run>
python3 bench/book_facts/bench.py compare bench/book_facts/results/llama3.2-1b/<run> bench/book_facts/results/llama3.1-8b/<run>
```

The sample, the prompt and the options stay the same, so the two runs compare
directly; `compare` refuses runs made with a different sample or prompt.
