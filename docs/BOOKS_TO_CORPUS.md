# Books to corpus facts

The steps for the day `llama3.1:8b` is on this machine. A local model reads
every prose paragraph of *Calculus Made Easy* (Gutenberg #33283) and Gibbs's
*Elementary Principles in Statistical Mechanics* (#50992) from EDIE's output in
`~/earth-os-library/edie/`. It proposes facts, you accept or reject each one,
and only accepted facts reach `corpus.pf`, as the `books` source. That source
is integrated through the corpus guard with verify, like OEWN, GeoNames and
Wikidata.

Everything is `cdos-computer/tools/books.py`; run the commands from
`cdos-computer/`. Nothing in it downloads: it uses only models Ollama already
has, talks only to `127.0.0.1`, and never writes `corpus.pf`.

## What the run does

| Step | Command | Writes |
|---|---|---|
| plan | `books.py plan` | nothing: what is covered and, after a trial, how long it takes |
| trial | `books.py trial` | `bench/book_facts/results/<model>/<run>/`: the fixed 50 paragraphs |
| extract | `books.py extract` | `~/cdos/books/<model>/outputs.jsonl`, one answer per paragraph, kept as it comes |
| stage | `books.py stage` (`extract` does it at the end) | `staged.jsonl`, `rejects.jsonl`, `stage.json` |
| review | `books.py review` | `decisions.jsonl` |
| export | `books.py export` | `export/books-<sha8>.pf` and the `sources.toml` entry for it |
| integrate | `python3 -m computer.acquire run books`, then `tools/integrate.py books` | `corpus.pf`, through the guard |

- **Paragraphs:** every text block inside a section that has at least 4 words
  of prose once its maths is taken out (2,066: 1,060 + 1,006). Maths blocks,
  headings, figures, the title page, and fragments between equations
  ("and", "Therefore,", "If we set") are left out. `plan` counts each reason.
- **Asking:** the bench's prompt (`bench/book_facts/prompt.txt`) and options
  (temperature 0, seed 42, a 4,096-token context, at most 512 output tokens).
  Each paragraph is asked cold (`keep_alive 0`), so its answer doesn't depend
  on the paragraph before it. The run is the trial's code, so a trial
  paragraph gives the same facts in both.
- **Resuming:** stop it any way you like (Ctrl-C, closing the lid, a power
  cut). Every finished answer is on disk, and `extract` carries on at the first
  paragraph without one. It refuses to resume if the model's digest, the
  prompt, the options or a book file has changed, so one run's answers all
  come from one setup.
- **Staging:** a fact line is staged only if it is well-formed (5 fields, an
  UPPER_CASE relation, kind S/N/D/T, a number for N, a date for D). It must
  also be new: not already in the corpus (by the build's own fact key; the
  books' own earlier generation doesn't count), and not staged already from an
  earlier line. Each staged fact carries its source: the book's sha256 (the
  `.tex` EDIE read), the block id and the lines. In the corpus it becomes
  `source_type DOCUMENT`, `source_id book/33283@fb6a9195621a/b-00063/L1203-1212`,
  confidence 0.9.

## The day the model arrives

### 1. Download (on a good connection)

```bash
ollama pull llama3.1:8b
ollama list                      # llama3.1:8b, about 4.9 GB
ollama show llama3.1:8b          # Q4_K_M; runs record the digest
```

### 2. Trial: the fixed 50 paragraphs

```bash
cd ~/projects/earth-os/cdos-computer
python3 tools/books.py trial
```

This takes about 2½–6½ hours (estimate below). It writes `run.json`,
`outputs.jsonl`, `review.pf`, a blank `scorecard.tsv` and `summary.md` under
`bench/book_facts/results/llama3.1-8b/<run>/`. It then prints the comparison
with the `llama3.2:1b` run on the same 50 paragraphs.

Mark `scorecard.tsv`: one of `correct`, `wrong`, `unsupported` or `malformed`
per row (`bench/book_facts/README.md` says what each means). Then:

```bash
python3 bench/book_facts/bench.py summary bench/book_facts/results/llama3.1-8b/<run>
python3 bench/book_facts/bench.py compare bench/book_facts/results/llama3.2-1b/20261116T012433Z \
                                          bench/book_facts/results/llama3.1-8b/<run>
python3 tools/books.py plan      # the full run's hours, now from the 8B's own timings
```

Decide from the precision whether the full run is worth its days. Any later
model is compared the same way: `trial --model <name>`.

### 3. The full books

The run takes days, so keep the machine awake and run it in the background:

```bash
systemd-inhibit --what=idle:sleep --why="book facts" \
  nohup python3 tools/books.py extract > ~/cdos/books/extract.log 2>&1 &
tail -f ~/cdos/books/extract.log                       # paragraph n/N, tokens, seconds, hours left
python3 tools/books.py status                          # any time: asked, staged, reviewed
```

Smaller pieces work too: `--book calculus` or `--book gibbs`, and `--limit N`
stops after N paragraphs. Running the same command again carries on. To
start over, move `~/cdos/books/llama3.1-8b/` away.

To review while it runs, stage what it has so far. The dedupe index takes
about 40 seconds the first time and again after each integration:

```bash
python3 tools/books.py stage
```

Staging again keeps your decisions: a fact's id comes from its book, its block
and its fields.

The model is a setting: `--model`, or `~/cdos/books/settings.toml`:

```toml
[books]
model = "llama3.1:8b"
```

### 4. Review

```bash
python3 tools/books.py review
```

You see one fact at a time, with the sentence of the paragraph it most likely
came from. When no sentence shares a word with the fact, you see the whole
paragraph instead.

| Key | |
|---|---|
| `a` | accept |
| `r` | reject |
| `s` | skip: it comes back after every undecided fact |
| `p` | the whole paragraph (again: back to the sentence) |
| `u` | undo the last decision of this session |
| `q` | quit; the next `review` starts where you stopped |

Accept only what the paragraph states and the row says right. The 1B's trial
answers show why every fact needs reading: it wrote facts about the Earth for
the book's preface.

### 5. Export, approve, integrate

```bash
python3 tools/books.py export
```

This writes `~/cdos/books/llama3.1-8b/export/books-<sha8>.pf` (every accepted
fact) and prints a `[source.books]` entry with that file's sha256 and the
relations it uses. The entry is also saved as `export/sources-entry.toml`.

1. Read the file, then paste the entry into `~/cdos/acquire/sources.toml`. If
   an earlier `[source.books]` is there, replace it. Put your name in
   `approved_by`: the registry refuses the entry until you do.
2. Stage it against the corpus as it is now, and look at the counts and
   `conflicts.jsonl` (a functional relation given a value other than the
   corpus has):

   ```bash
   python3 -m computer.acquire run books
   python3 -m computer.acquire show books
   ```
3. Optionally, check what it does to the review answers. This builds the
   corpus twice:

   ```bash
   python3 tools/candidate_check.py books
   ```
4. Integrate. `corpus.integrate` is already on in `~/cdos/acquire/settings.toml`:

   ```bash
   python3 tools/integrate.py books --by richie --reason "book facts: llama3.1:8b, reviewed"
   ```

   The candidate is built, validated and asked the review questions. If any
   case that passed before fails now, it is refused and `corpus.pf` stays
   byte-identical. Otherwise the master is backed up, replaced and recorded.
   Rebuild the served `data/` the usual way afterwards.

Doing it again later (more reviewed, or a fact you now reject): export,
replace the entry, `run books`, integrate. The source replaces its own
generation (`source_id ^book/`), so the corpus holds exactly the accepted set
each time.

## Will it fit, and how long

Measured 2026-11-16 on this machine: i7-5557U (2 cores, 4 threads, AVX2, no
GPU Ollama can use), 15 GiB RAM with about 9.7 GiB available, 10 GiB swap,
80 GB free disk, Ollama 0.35.0.

- **Download:** about 4.9 GB (Ollama's default `llama3.1:8b`, 4-bit Q4_K_M).
  This is from Ollama's library listing, not checked here: `ollama pull`
  shows the real figure.
- **Memory:** about 4.6 GiB of weights, 0.5 GiB of context cache at 4,096
  tokens, and a few hundred MiB of working space: about 5.5 GiB in all. That
  fits in the 9.7 GiB available with room to spare; a browser with many tabs
  open is what would squeeze it.
- **Speed:** estimated, not measured. The 1B ran at 15.7 prompt tokens/s and
  5.9 output tokens/s here. Scaling by size (6.5× the arithmetic, 3.7× the
  bytes) gives the 8B about 2.4 prompt tokens/s and 0.9–1.6 output tokens/s,
  plus about 10 s to load each paragraph.
- **The work:** about 314 prompt tokens a paragraph, 265 of them the
  instructions. Output depends on how much the 8B writes: the 1B averaged 276
  tokens, much of it repeated lines; a model that follows the format should
  write 100–150.

| | per paragraph | trial (50) | full books (2,066) |
|---|---|---|---|
| optimistic | 2.8 min | 2.5 h | 98 h (4 days) |
| middle | 3.9 min | 3.5 h | 134 h (5½ days) |
| pessimistic | 7.5 min | 6.4 h | 257 h (11 days) |

The trial replaces these guesses with measurements: `books.py plan` after it.
The tiny real check with `llama3.2:1b` (3 paragraphs, 2026-11-16) took 16–58 s
a paragraph and staged its facts against the real 1.69 million-fact corpus,
which it only read.
