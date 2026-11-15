# EDIE — Earth Document Intelligence Engine

*v0.1. Part 1 (§1–§3) was written before any EDIE code existed; part 2
(§4 onward) describes what was built and what testing found.*

EDIE turns an original document into a **structured document**: its
hierarchy, text, mathematics and illustrations, each piece traceable to the
source lines it came from. It does not extract facts and it does not touch
the corpus.

```
 Original documents ─► EDIE ─► structured documents ─► knowledge extraction (a model)
                                                    ─► validation ─► CDOS staging ─► approved corpus insertion
                       ▲ this document covers only this step
```

---

## 1. Audit: what Earth OS already has

Searched the whole tree (`earth_os/`, `cdos-computer/`, `tools/`, `tests/`,
`docs/`) for document ingestion, LaTeX, PDF, Markdown and parsing code.

| Concern | What exists | Reuse? |
|---|---|---|
| Document ingestion / parsing | **Nothing.** The only `.tex`/markdown mentions are an LLM style prompt (`computer/core.py`) and a review tool | — nothing to duplicate |
| Corpus | `corpus.pf` (11 tab columns) → `cdos-computer/tools/build_shards.py` → `data/` read by `computer/corpus.py`. `source_type` already has `DOCUMENT` for facts taken from documents, with a reference name in `source_id` | EDIE stays upstream of it. A future extractor would cite EDIE block ids in `source_id` |
| JSON conventions | Every persisted JSON carries `"format": "<name>/<major>"` (`cdos-governor-policy/1`, `cdos-growth-task/1`, `cdos-growth-journal/1`); hashes are sha256 hex; writes are atomic (`tmp` + `os.replace`) | **Yes** — `"format": "edie-document/1"`, sha256, atomic writes |
| Content addressing | `corpus.py` hashes content, not paths, so a rebuild of the same facts has the same version | **Yes** — EDIE output contains no timestamps or absolute paths, so the same source gives byte-identical output |
| Staging and approval | `computer/growth` stages and never promotes; `governor.py` gates promotion to an operator | Future extraction step, not EDIE |
| Package layout | `earth_os/` is the desktop shell, bound by the boundary, boot-time and Earth Mind import rules; `cdos-computer/` is a separate tree with its own package (`computer/`), tests and docs | EDIE follows the `cdos-computer` pattern |

## 2. Where EDIE lives, and why

**A new top-level tree, `edie/`, with its own package, tests and docs —
standard library only.**

* Not under `earth_os/`: nothing there may grow imports the boot path pays
  for, and `tests/test_boundary.py` and `tests/test_mind.py` police what that
  package imports. A document parser has no business near the window or the
  capability catalogue.
* Not under `cdos-computer/computer/`: that is the answering computer, which
  reads a built corpus and must keep running on a Pi. EDIE runs before the
  corpus exists.
* **Isolation is checked**: a test reads EDIE's imports and fails if it ever
  imports `earth_os`, `computer`, a network module or a model client, and
  nothing outside `edie/` imports EDIE.

## 3. File layout

```
edie/
  README.md                     how to run it
  edie/
    __init__.py                 version, schema id
    __main__.py                 CLI: python3 -m edie parse …
    source.py                   read-only load, sha256, encoding, line index
    model.py                    the edie-document/1 builder and serializer
    latex/
      scanner.py                braces, escapes, comments, optional args, math and verbatim ends
      signatures.py             macro signatures: the document's own preamble, then the profile
      parser.py                 body walk → headings, text, math, figures, verbatim
      render.py                 LaTeX fragment → readable text (for Markdown only)
    export/
      markdown.py               readable Markdown
      report.py                 the parsing report
    profiles/
      pg-latex.json             roles of the Project Gutenberg LaTeX macros (\Chapter, \Figure, …)
    schema/
      edie-document-1.schema.json
  tests/
    fixtures/thompson-mini/     a small document using every pattern, with images
    test_*.py
docs/EDIE.md                    this file
```

No existing file is modified.

---

## 4. How it works

```
 source.py ──► signatures.py ──────────────► parser.py (one pass over the body) ──► model.py ──► JSON
 bytes, sha256  preamble definitions                │                                        ├─► export/markdown.py
 encoding,      + profile roles                     ├─ scanner.py  braces, escapes,          └─► export/report.py
 line index     = how many args each macro takes    │              comments, [..], math, envs
                                                    └─ render.py   readable text (Markdown only)
```

**1. The source is read once, read-only.** Its sha256 is taken over the bytes
as stored. Text is UTF-8, or Latin-1 when it isn't, and the encoding used is
recorded. Line endings are normalised so that offsets and line numbers match
what an editor shows, whatever the file uses.

**2. Each macro's arguments come from the document itself.** The profile
(`profiles/pg-latex.json`) says what a macro *means*: `\Chapter` is a chapter
heading, `\Graphic` is an inline illustration. The number of arguments, and
which are optional, comes from the book's preamble whenever it can be read
without running TeX:

| The preamble says | EDIE reads |
|---|---|
| `\newcommand{\Chapter}[3][]{…}` | `o m m` |
| `\NewDocumentCommand{\Chapter}{o m m}{…}` | `o m m` |
| `\def\@Chapter[#1]#2#3{…}` | `o m m` |
| `\newcommand{\Chapter}{\@ifnextchar[\@Chapter\@@Chapter}` | follows the `[` branch to `\@Chapter`: `o m m` |
| anything else | the profile's pattern, with a warning naming the line |

This is what keeps a running head from being taken for a title. With
`o m m`, `\Chapter[Running Head]{II}{Title}` gives running head, number,
title. `\Chapter{I}{Title}` gives no running head (null), number, title.
`\Chapter[Running Head]` followed by a line break and then `{II}{Title}` is
read the same way, because, as in TeX, one line break may come before an
argument but a blank line may not. The report lists every structural macro,
the pattern used, and the preamble line it came from.

**3. One walk over the body.** At each position the parser does exactly one
of the following:

* `%`: skips the comment;
* a blank line: ends the paragraph;
* `$`, `$$`, `\(`, `\[`, or `\begin{align}` and the other maths
  environments: records mathematics exactly as written;
* a heading macro: reads its arguments and closes the current text block;
* a figure macro: records an illustration, block-level or inline;
* a verbatim environment (`PGtext`, `verbatim`, and any the preamble declares
  with `\DefineVerbatimEnvironment`): copies it uninterpreted;
* anything else: becomes part of the current text block.

Braces are matched by `scanner.py`, which skips escaped braces and braces
inside comments. Regular expressions are used only to read small values that
have already been located: `Fig. 4` in a caption, `Title:` in a Project
Gutenberg header.

**4. Nothing is supplied.** A missing argument stays null, with an
`ARG_MISSING` warning on its line. A figure number is recorded only when the
source states it (in a caption, or in an argument the profile names). An image
that can't be found is `file: null`, with the names that were tried.

**5. Every malformed construct becomes a warning.** An unclosed `{`, `$` or
environment produces a warning with its line number, and parsing carries on.
`tests/test_parser.py` cuts the fixture at every 7th byte and parses each
prefix: none crashes, and every block is still exactly its source slice. Before
this was fixed, 20 of 532 cuts crashed.

## 5. The document (`edie-document/1`)

The full schema is in `edie/schema/edie-document-1.schema.json`. Every block
carries `id`, `type`, `latex` (the exact source slice), `lines` and `offsets`,
and `section`, the innermost heading it falls under.

| type | adds |
|---|---|
| `heading` | `kind` (chapter, section, subsection, paragraph, case, example), `level`, `macro`, `number` (as written, e.g. `II`, or null), `title` and `running_head` (each `{latex, text, lines, offsets}`), `parent` |
| `text` | `text` (readable), `inline_math` (each with `latex`, `body`, `normalized`, `features`, `figures`, `lines`), `figures`, `paragraph` |
| `math` | display mathematics: `delimiter`, `environment`, `latex`, `body`, `normalized`, `features`, `figures`, `paragraph` |
| `figure` | a block-level illustration or figure environment; the details are in `figures[]` |
| `verbatim` | `environment`, `body` |

* **`figures[]`** holds every illustration: `macro`, `context` (`text`,
  `math` or `figure-environment`), `in_math`, `args` as written, `file`,
  `file_exists`, `number` and `number_origin`, `caption`, `label`, `block`.
  A diagram inside an expression stays in the expression and is also listed
  here.
* **`normalized`** removes comments and collapses runs of whitespace. Nothing
  else changes.
* **`features`** (`fraction`, `integral`, `derivative`, `greek`,
  `alignment`, …) labels an expression for search and reports. It never
  rewrites it.
* **`paragraph`**: text and display mathematics from one TeX paragraph share
  a number, so the paragraph can be put back together.
* **No timestamps or absolute paths**, so the same source always gives
  byte-identical JSON.

## 6. Test results

| Suite | Result |
|---|---|
| `edie/tests` | **91 run: 82 pass, 9 skipped** here (the real-book tests; the book is not in this sandbox). On the book's machine: v0.1.0, 82 of 82; v0.1.1, 84 of 84, 0 warnings (§7a) |
| `cdos-computer/tests` | 512 run, OK, 95 skipped: unchanged |
| `tests/` (Earth OS) | 553 run, 7 failures and 32 errors: the same as before EDIE, all from this sandbox lacking GTK, Pango, cairo and sway |

All of Phase 7's cases are covered by the fixture `tests/fixtures/thompson-mini/mini.tex`:

| Case | Tests |
|---|---|
| Three `\Chapter` patterns; optional arguments; multiline headings | `ChapterPatterns`: number, title, running head and line ranges, each checked against the line found by searching the source |
| Nested braces; escaped characters | `test_nested_braces_in_a_title`, `test_escaped_characters`, `Groups`, `Arguments` |
| Mathematics | `Mathematics`: inline, `\[..\]`, `$$..$$`, `align*` and `equation`, kept byte for byte; paragraph continuity across display maths |
| Figures and inline graphics | `Figures`: block, inline in text, inside maths, a figure environment with its caption number |
| Missing images | a nonexistent name, and an image deleted from a copy of the fixture |
| Source line tracking | every block's `latex` equals `source[offsets]`, and its lines equal an independent newline count; the same structure from a CRLF copy |
| Reproducible output | two parses are byte-identical; two CLI runs give identical files; the source's hash, mtime and folder are unchanged |
| Traps | a `\Chapter` in a comment and one inside `PGtext` are not chapters; a lone `$` gives `UNCLOSED_MATH` |
| Isolation | EDIE imports only the standard library (no network, no subprocess, no model clients), and nothing in Earth OS or CDOS imports EDIE |

**The real book.** `tests/test_real_book.py` compares EDIE's headings and
figures with a second, independent count of the source, made line by line
outside comments and verbatim blocks. It also checks that the chapter numbers
are the consecutive Roman numerals the source states, so a missed or invented
chapter fails. It writes down no expected numbers. The harness was dry-run on
the fixture: it reported the fixture's deliberate jump from III to XV and
passed everything else.

## 7. Memory and CPU

Measured in this sandbox on the fixture body repeated to book size. The
fixture is denser in mathematics and figures than a real book.

| Source | Blocks | Time | Peak RSS | JSON |
|---|---:|---:|---:|---:|
| 3.7 kB (fixture) | 27 | 0.01 s | 19 MB | 21 kB |
| 403 kB (the size of `33283-t.tex`) | 6,939 | 0.43 s | 46 MB | 5.3 MB |
| 4.0 MB | 69,201 | 4.5 s | 284 MB | 53 MB |

* **Time is linear.** Every scanner function moves forward only, and the
  parser makes one pass.
* **Memory is dominated by the finished document**, not by parsing: each
  block keeps its LaTeX, its readable text and its spans, so the JSON is about
  13× the source. For a book that's tens of MB. The next step for
  encyclopedia-sized sources is to write blocks to JSON as they are produced
  instead of building the whole dict first; `Builder.add_block` is the place
  to do it.
* **No model, no network, one process, single-threaded.**

## 7a. First run on Calculus Made Easy (#33283), 2026-11-14

On the book's own machine with Python 3.13.5, v0.1.0: **82 tests, all pass**,
including the 8 real-book tests (independent count agrees, chapters I–XXI
consecutive, running heads apart from titles, reproducible, source untouched).

| | |
|---|---|
| Source | `33283-t.tex`, sha256 `fb6a9195621a…` |
| Result | 2,089 blocks; 3,482 inline and 457 display equations; 52 figures (40 `\Figure`, 12 `\Graphic`), **0 missing**; 0.42 s, 37 MB |
| Chapters | Preface, Prologue, I–XXI, Epilogue and Apologue, Table of Standard Forms, Answers: 26, matching the contents |
| Signatures | every structural macro read from the book's preamble (`\Chapter` line 388, `\AltChapter` 407, `\ChapterStar` 432, `\Section` 451, `\Subsection` 459, `\Paragraph` 464, `\Case` 472, `\Example` 475, `\Graphic` 651, `\Figure` 660); none fell back to the profile |
| Warnings (v0.1.0) | 3 × `CHAPTER_UNNUMBERED` on `\AltChapter`: a false alarm. The book defines `\AltChapter[head]{title}`, unnumbered by design. **Fixed in 0.1.1**: the warning now fires only for a number argument that exists and is empty; regression test added. The profile's fallback patterns now match this preamble. v0.1.1 on the book: 84 tests pass, **0 warnings**, 0.38 s. |

## 8. Known limitations

1. **Figure numbers for #33283 come from the definitions** (0.1.2). The
   book's own usage line (659) is `\Figure[optional width]{File name}{Fig number}`,
   and line 669 defines a side-by-side pair,
   `\Figures[width]{file 1}{file 2}{number 1}{number 2}`, which v0.1.0–0.1.1
   did not know about. Any pairs in the book were therefore missed by both
   EDIE and the independent count, which only counts profile macros. The
   profile now names each argument (`fields`), applied only when the book
   defines the macro with exactly that pattern (`fields_for`). A pair gives
   two figure records sharing a `group` and one block. A new real-book test
   requires every `\Figure`/`\Figures` to carry the number stated in its own
   text, with no number used twice. On the book (0.1.2): **74 figures**, 22 of them from 11 `\Figures` pairs that earlier versions missed; every number found.
   0.1.2 also showed a bug: inside a figure environment, each image's position was replaced by the environment's
   span, so two `\Graphic`s in one environment looked like one use (independent count 12, EDIE 11). **Fixed in 0.1.3**:
   every figure keeps its own macro's span, and a test now checks each figure, not only each block, against its exact
   source text and lines.
2. **Fallback patterns** in `pg-latex.json` are now the ones #33283's
   preamble defines. They only matter for a book that doesn't define a macro
   readably, and the report flags that case.
3. **Figure numbers.** `\Figure`/`\Graphic` arguments are kept as written. A
   number is recorded only from a caption (`Fig. 4`) or from an argument the
   profile names (`"fields": [null, "number", "file"]`).
4. **No macro expansion.** A heading produced by a wrapper macro the profile
   doesn't list isn't seen as a heading. Unknown macros keep their arguments'
   text in the readable rendering (`\textit{x}` → `x`); the drop list removes
   layout-only ones (`\Pagelabel{162}`).
5. **Tables and lists aren't structured yet.** `tabular` becomes text, and
   `\item` becomes `•`. The raw LaTeX is in the block.
6. **Optional arguments of every heading are stored as `running_head`.** For
   `\Section[short]{…}` that's a short title, used the same way. Case and
   Example are recorded as level-5 headings under the preceding heading; the
   raw LaTeX allows another reading later.
7. **Markdown only.** Top-level `align`/`equation` environments are shown as
   `aligned`/plain display maths, because Markdown renderers reject them
   inside `$$`. A comment names the original environment, and the JSON keeps
   it untouched. Image links depend on where the output folder is; the JSON
   doesn't.
8. **Inputs: LaTeX in UTF-8 or Latin-1.** PDF and plain text will be further
   front ends producing the same `edie-document/1`.

## 9. The boundary with knowledge extraction

EDIE ends at the structured document. A later extraction step (Llama, then
validation, then CDOS staging) should cite the source as
`sha256 + block id + lines`, for example `pg33283:4040f0d8a4fc:b-00412:L340-352`,
in `corpus.pf`'s `source_id`, with its own `source_type`. Under the corpus
growth plan (`docs/CORPUS_GROWTH_AUDIT.md` §6.3), model-extracted statements
go to a proposal queue and never directly into `corpus.pf`. EDIE has no code
path to the corpus at all, and `test_isolation.py` keeps it that way.
