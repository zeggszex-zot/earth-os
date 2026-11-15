# EDIE — Earth Document Intelligence Engine

*v0.2.1. Part 1 (§1–§3) was written before any EDIE code existed; part 2
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
      pg-50992.json             Gibbs (#50992): only what differs; extends pg-latex (0.2)
    schema/
      edie-document-1.schema.json
  tests/
    fixtures/thompson-mini/     a small document using every pattern, with images
    fixtures/gibbs-mini/        a small document in #50992's conventions (0.2)
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
| `table` | `environment`, `colspec`, `rows` (cells, each `{latex, text, lines, offsets}`), `n_rows`, `n_cols`, `inline_math` (0.2) |
| `list` | `environment`, `ordered`, `items` (each `{label, latex, text, lines, offsets}`; a nested list stays in its item), `n_items`, `inline_math` (0.2) |
| `verbatim` | `environment`, `body` |

Added in 0.2, still `edie-document/1`:
* every block has `pages`, its [first, last] printed page from the source's
  own page markers (null without them);
* headings have `alt_title`, an alternate title the source gives, which is
  never a running head;
* every expression has `tags`, its equation tags;
* the document has `corrections[]` (each with both readings) and
  `definitions[]` (the preamble macros the body uses, as written).

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
| `edie/tests` | **153 run (0.2.1)**: all pass here with both books present; 121 pass and 32 skip without them. On the books' machine: v0.1.0, 82 of 82; v0.1.1, 84 of 84, 0 warnings (§7a); v0.2.0, 145 of 147 (§10a) |
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

## 8. Known limitations (v0.2)

These replace the v0.1 list; the history of each fix is in §7a and §10.

1. **One front end: LaTeX** (UTF-8 or Latin-1, LF or CRLF). PDF and plain
   text are still to come, producing the same `edie-document/1`.
2. **No macro expansion beyond plain text.** A zero-argument macro whose
   definition is plain text is expanded in readable text (`\ie`); nothing else
   is. A heading produced by a wrapper macro the profile doesn't list isn't
   seen as a heading. Unknown macros keep their arguments' text, separated by
   spaces when there are several.
3. **Profiles state meaning, and only from evidence.** A macro's arguments are
   named (`fields`) only for the definition they were checked against
   (`fields_for`). #33283's `\DPPageSep` is a page marker (0.2.1); it has no
   corrections macro. The data is all in `latex` either way.
4. **Tables and lists: rows and items are checked on fixtures only.** #33283
   has 5 tables and 2 lists; the real-book tests check their mathematics and
   source slices, not how they were split into cells. A row of rules only is
   left out; `\multicolumn` keeps its text, not its span.
5. **Equation references are text.** `\Eq{(12)}` reads "(12)", and the tag
   `(12)` is recorded on its equation, but the two aren't linked yet.
6. **Page ranges at a marker's edge.** A block whose last line is a page
   marker is given that page as its last; a block that starts before the
   first marker has a null first page. A marker inside a display, table, list
   or figure environment turns the page there (0.2.1).
7. **Markdown is presentation.** Top-level `align`/`gather` are shown as
   `aligned`/`gathered` with their tags listed after them; `\displaybreak` is
   dropped; the book's mathematics macros are copied from its preamble, except
   those that branch or typeset (#50992: `\efrac`, `\Fact`), whose equations
   a renderer can't show. A book's own math environment is shown as the
   standard one its definition is built on (#33283's `DPalign*` and
   `DPgather*` both end with `\endalign`, so both show as `aligned`: a
   `DPgather*` line has no `&` and sits right-aligned rather than centred), and
   `\lintertext`/`\rintertext` as `\text`. The JSON keeps everything as
   written. Image links depend on where the output folder is.
8. **Captions and figure numbers** come only from a caption (`Fig. 4`) or an
   argument the profile names.
9. **Memory** is the finished document: JSON about 5–13× the source. Writing
   blocks as they are produced is the next step for very large sources.
10. **Defined environments are recognised only as mathematics**, and only when
   their own definition names a standard display-math environment (or one
   already recognised). An environment whose mathematics comes from a macro
   it calls under another name is not seen; its uses stay paragraph text, as
   before 0.2.1.
11. **Mathematics inside `\text{}` inside mathematics** stays part of the
   expression it is written in (`$\dfrac{\text{rate of $P$}}{…}$` is one
   expression); it is counted, not recorded separately.

## 9. The boundary with knowledge extraction

EDIE ends at the structured document. A later extraction step (Llama, then
validation, then CDOS staging) should cite the source as
`sha256 + block id + lines`, for example `pg33283:4040f0d8a4fc:b-00412:L340-352`,
in `corpus.pf`'s `source_id`, with its own `source_type`. Under the corpus
growth plan (`docs/CORPUS_GROWTH_AUDIT.md` §6.3), model-extracted statements
go to a proposal queue and never directly into `corpus.pf`. EDIE has no code
path to the corpus at all, and `test_isolation.py` keeps it that way.

## 10. v0.2: a second book, and EDIE in the repository

**Integration.** `edie/` sits at the root of the Earth OS repository, beside
`earth_os/` and `cdos-computer/`, and is not part of either. Its isolation
tests read the actual repository and fail if anything outside `edie/` imports
EDIE (including `importlib`/`__import__`), or if EDIE imports Earth OS, CDOS,
the network, a subprocess or a model client. A planted `import edie` and a
planted `__import__("edie")` were both caught. Copied anywhere else, those two
tests say *skipped*, never *passed*. `CLAUDE.md` and `AGENTS.md` now name
`/usr/bin/python3` (3.13.5), the interpreter the development machine has.

**The second book** is #50992, Gibbs, *Elementary Principles in Statistical
Mechanics*. Its structural audit and the compatibility report are in
[`EDIE_COMPAT_50992.md`](EDIE_COMPAT_50992.md). In short:
- EDIE 0.1.3 parsed it with 0 warnings and every expression accounted for;
- but it silently got meaning wrong, recording a bookmark title as a running
  head and merging misprints with their corrections;
- and it had no place for the book's equation tags, page numbers and
  theorems.

**What changed** (generic unless marked):
- profile inheritance and automatic selection (`--profile auto`: `pg-<ebook>`
  if EDIE has it, else `pg-latex`);
- named heading arguments that apply only to the definition they were checked
  against;
- `alt_title`;
- roles `correction`, `equation_tag` and `page`;
- plain-text macro expansion;
- `table` and `list` blocks;
- `definitions[]`;
- Markdown that carries the book's mathematics macros;
- readable-text fixes (`\\[12pt]`, `\rule`, environment arguments,
  multi-argument macros);
- *profile* `pg-50992` (inheriting `pg-latex`): `\Chapter[alt]{n}{title}`,
  `\Chap`, `\Theorem`, `\Typo`/`\Chg`/`\Add`, `\Tag`, `\PageSep`,
  `\BookMark`. Every entry names the preamble line it came from.

No #33283 test was changed except for two things. The independent count now
stops at `\end{document}`, where EDIE stops (#50992 names `\Chapter` in
tables after it). And the checks shared by both books moved into a common
class, with their assertions unchanged.

**Tests.** `edie/tests`: 147 run here (32 real-book tests skipped without the
books; 18 for #50992 pass when it is present). Each real book now also has:
- an independent count of every display and inline expression;
- independent counts of equation tags, corrections and page markers;
- a check that every figure and correction is its exact source slice;
- a comparison of two CLI runs **in separate processes**, byte for byte;
- a check that `--profile auto` picks its profile.

| | #33283 Calculus Made Easy | #50992 Gibbs |
|---|---:|---:|
| Source | 33283-t.tex, LF | 511,666 bytes, 11,386 lines, CRLF |
| Profile (auto) | `pg-latex` | `pg-50992` (extends `pg-latex`) |
| Blocks | 2,215 | 1,948 |
| Headings | 26 chapters (Preface, Prologue, I–XXI, 3 more), 15 sections, 12 subsections, 18 paragraphs, 3 cases, 15 examples | 17 chapters (Preface, Contents, I.–XV.), 9 theorems, 1 subsection |
| Display / inline expressions | 567 (110 in `DPalign*`/`DPgather*`) / 3,467 (+64 inside displays, +4 inside inline) | 725 / 2,349 (+12 inside displays) |
| Equation tags | 10 (`\tag*`) | 562 |
| Figures (missing) | 74 (0) | 0 (0) |
| Tables / lists | 5 / 2 | 0 / 0 |
| Corrections / page markers | none in the book / 279 (`\DPPageSep`, pages i–269) | 204 / 219 (pages v–207) |
| Warnings | 0 on the book's machine (here: 74 `MISSING_IMAGE`, the images not being in this workspace) | 0 |
| Time, peak memory | 0.44 s, 38 MB (0.2.0, the books' machine); 0.35–0.42 s, 41 MB (0.2.1, this workspace) | 0.70 s, 36 MB (0.2.0, the books' machine); 0.47 s, 38 MB (0.2.1, this workspace) |
| Deterministic | yes: 3 CLI runs byte-identical (0.2.1, JSON sha256 `346cfdfd…`) | yes: 3 CLI runs byte-identical (0.2.1, JSON sha256 `7e7fe0d4…`) |

Counts are 0.2.1's, from this workspace. Both sources here have the sha256
the books' machine printed for its own copies (`fb6a9195621a…`,
`fb7b303d1d0a…`).

## 10a. 0.2.1: what the first 0.2 run on Calculus Made Easy showed

On the books' machine, 0.2.0 passed every Gibbs test and failed two Calculus
tests, both new in 0.2:

* **Equation tags: 7 recorded, 10 in the source.** The other 3 were in
  `DPalign*` and `DPgather*`, environments the book defines (preamble lines
  825 and 832). Their definitions collect the body token by token and end
  with `\endalign`, so nothing in them says `\begin{align*}`, and EDIE 0.1–0.2.0
  read **all 110 of their uses as paragraph text**. The 0.1 tests could not
  see this: the display-math count was added in 0.2, and EDIE and the count
  both took the list of math environments from the profile. **Fix (generic):**
  EDIE reads `\newenvironment` (and the xparse and `environ` forms) and treats
  an environment as display mathematics when its own begin or end code opens
  or closes a known math environment (`\begin{align*}`, `\gather*`,
  `\endalign`, …), repeating so that one built on another is found too. Its
  arguments (`\begin{DPalign*}[m]`) are read and are not mathematics. The
  document lists these environments (`environments[]`, with `based_on` and the
  definition as written); the report has a table of them. The test now learns
  them separately, with a plain text search of each definition.
* **Inline expressions: 3,531 recorded, 3,535 in the source.** Line 10143 has
  `$\dfrac{\text{Rate of change of~$P$ when $t$~varies}}{…}$`: four
  expressions inside `\text{}` inside an inline one. EDIE keeps them as part of
  the outer expression, as it already did inside displays, and the test
  counted them inside displays but not inside inline expressions. The test now
  counts both the same way; the totals still have to agree exactly.

Two more things came up while checking the fix:

* **`\DPPageSep{026.png}{14}` leaked into the text** as "026.png 14" in 271
  places since 0.1. It is now a page marker in `pg-latex` (its page is the
  second argument: `fields`), so Calculus's blocks have printed pages, i–269.
* **A page marker inside a display or a list did not turn the page** (6 in
  Calculus). It now does, in displays, tables, lists and figure environments.

New regression tests (`test_defined_envs.py`, 6) cover each of these on small
documents, including an environment built on another defined one and one that
is not mathematics (`Problems`, built on `enumerate`). Gibbs's output is
unchanged except for the new, empty `environments` field. No assertion was
removed or loosened; the two counting changes above are the only test changes.
