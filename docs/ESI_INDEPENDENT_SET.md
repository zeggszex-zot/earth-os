# ESI — collecting the independent sentences (phase 3C)

Every sentence ESI has been measured on so far was written by the person
who wrote its tables. That set can catch regressions; it cannot show
whether ESI understands language it was not built around. This file is how
to collect 30–50 sentences that can.

The rule that makes them worth having: **the writer has not seen ESI** —
not the code, not the phrasebook, not the other test sets, not this
repository's documents. Give them only the brief below.

---

## 1. The brief — hand this to the writer, and nothing else

> I'm testing a computer that answers questions about itself. Please write
> **30 to 50 things you might say or type to your own computer**, the way
> you'd actually say them, about:
>
> * how it's doing — speed, memory, temperature, storage, battery, network,
>   what's running;
> * problems you've had — it being slow, hot, full, frozen, offline;
> * and about **ten things you might say near a computer that aren't requests
>   to it at all** — about your day, other people, the weather.
>
> Write them however comes naturally: slang, typos, half-sentences and long
> rambling ones are all useful. Don't try to guess what it understands.
>
> Next to each, write a few words saying **what you wanted** ("to know if
> it's overheating", "nothing, I was talking to my brother").

Do not show them an example sentence from this repository: an example is a
template, and the point is sentences nobody here would have written.

## 2. Recording

One line per sentence in `tests/esi_independent.jsonl`, exactly as written —
do not correct spelling or grammar:

```json
{"text": "why is my laptop so laggy today", "want": "find out what is slowing it down", "written_by": "W1", "expect": "performance_report", "labelled_by": "K"}
```

| Field | Who fills it | What |
|---|---|---|
| `text` | the writer | the sentence, untouched |
| `want` | the writer | what they wanted, in their words |
| `written_by` | — | an initial or code, not a name, if they prefer |
| `expect` | the labeller | which answer is right — see below |
| `labelled_by` | the labeller | who decided `expect` |

## 3. Labelling

```bash
python3 tools/esi_measure.py --labels
```

lists every label there is, with what it means; it is generated from the
catalogue, so it cannot go stale. Choose the one that best answers the
writer's `want`, or `-` when the right response is to do nothing. The
measurement refuses a file containing a label that does not exist, so a
typo cannot quietly count as a miss.

* **Label from `want`, not from what ESI might do.** If no label fits what
  they wanted, use `-` and say so in a `note` field — that is a finding
  about the catalogue, not about ESI.
* **Ideally someone other than the ESI developer labels.** If the
  developer must, record it in `labelled_by`; the sentences are still
  independent, the labels less so.
* `concepts`, `attach` and `not_attach` (the three-outcome labels of
  `tests/esi_context.jsonl`) are optional here. Add them only if the
  labeller is comfortable doing so; interpretation alone is the measurement
  that matters most.

## 4. Measuring — once

```bash
python3 tools/esi_measure.py tests/esi_independent.jsonl --raw    # phrasebook alone
python3 tools/esi_measure.py tests/esi_independent.jsonl          # with ESI
```

1. **Commit the labelled file before running anything against it.** The
   commit is the record that the labels were not chosen after seeing the
   results.
2. **Run it once and record the figures** in `docs/ESI_ARCHITECTURE.md`, with
   the commit that produced them.
3. **Never tune against it.** Reading `--show` to understand the misses is
   fine; adding a word because one of these sentences used it is not. A miss
   worth fixing is fixed with a *new* sentence of the same kind added to
   `esi_dev.jsonl` or `esi_context.jsonl`, and the independent figure is left
   as it was.
4. **When it has been used to decide too much, retire it.** Move it into the
   development sets and collect a fresh one. An evaluation set wears out
   every time someone acts on what it showed.

`tests/test_esi.py` checks the file's shape whenever it exists — every line
has `text`, `expect`, `written_by` and `labelled_by`, and every label is
real — and sets no score for it. A floor would invite exactly the tuning
this set exists to prevent.
