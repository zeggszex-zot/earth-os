# EDIE compatibility report: Project Gutenberg #50992

*J. Willard Gibbs, **Elementary Principles in Statistical Mechanics** (1902),
LaTeX by Andrew D. Hwang. This report was written from the source before any
parser change. Each finding cites the source line it rests on (line numbers
with CRLF read as LF, as EDIE reports them). §6 records what was done about
each finding and what the result was.*

## 1. The source

| | |
|---|---|
| File | `50992-t.tex`: 511,666 bytes, 11,386 lines, UTF-8, **CRLF line endings** |
| sha256 | `fb7b303d1d0ab8f6b4f4bfc4953cb31b2af513adda9c4e87e14c904363e0d648` |
| Images | **none**: the archive holds only the `.tex` file |
| Body | `\begin{document}` at line 414, `\end{document}` at line 10,773; after that, a list of macro tables (lines 10,781–10,786) that name `\Chapter` and `\Signature` as strings |
| Packages | `amsmath`, `amssymb`, `alltt`, `footmisc`, `calc`, `fancyhdr`, `ifthen`, `geometry`, `hyperref` |

## 2. Custom commands in the preamble

| Group | Commands (definition line) | What they do |
|---|---|---|
| Structure | `\Chapter[3][]` (320), `\Chap[1]` (336), `\Theorem[1]` (344), `\SectTitle[2][\large]`, `\SubsectTitle[2][\normalsize]` (312, 315) | chapters, unnumbered chapter-like units, run-in theorems; the two `*Title` macros are only called by the others |
| Corrections | `\Typo[2]{#2}` (223), `\Chg[2]{#2}` (226), `\Add[1]{\Chg{}{#1}}` (228) | the transcriber's corrections: print the corrected reading, keep the printed one in the source |
| Pages and references | `\PageSep[1]` (357), `\Pageref`, `\Tag[1]` (406), `\Eq[1]` (409) | original page breaks; equation tags (`\tag*` plus a label); references to them |
| Text | `\ie` (`\emph{i.e.}`), `\TransNote` (`Transcriber's Note`), `\First[1]`, `\Signature[3]`, `\ToC[4]`, `\ToCChap[2]`, `\ToCPg[3]` | abbreviations, small caps, the preface's signature, contents lines |
| Mathematics | `\eps` → `\epsilon`, `\Bar`/`\bar` → `\overline`, `\Avg[2][\eps]`, `\efrac[2]`, `\Fact[2][*]`, `\Int[2]`, `\ints`, `\intac`/`\intap`/`\intav`, `\intsall`, `\intslim[2][]`, `\const`, `\constant`, `\Eta`, `\dsp`, `\Op`, `\Fsp`, and `\func` (a `\DeclareMathOperator`) | used throughout the mathematics (`\eps` alone ~1,900 times) |
| Layout | `\BookMark[2]`, running-head and margin macros, `PGtext` (217: an `alltt` environment) | no text of their own |

## 3. Chapter and section conventions

| Convention | Example (line) | Differs from #33283? |
|---|---|---|
| **`\Chapter[Alt title]{Number}{Title}`**: the comment on line 319 says so. The `[..]` replaces the title **only in the PDF bookmark** (`\BookMark{0}{#2 #1}`, line 326). Used twice: chapters III (2120) and IX (5529, where the title has `$\phi$` and the bookmark spells it `phi`) | `\Chapter[The Function phi and the Canonical Distribution.]{IX.}{The Function $\phi$ and ...}` | **Yes: same name, same `o m m` pattern, different meaning.** In #33283 the `[..]` is the running head |
| Numbers carry a period: `I.` … `XV.` | `\Chapter{I.}{General Notions. ...}` (1061) | number format only |
| Titles span lines | 1061–1062 | no |
| `\Chap{Preface.}`, `\Chap{Contents.}`: unnumbered | 502, 731 | different macro |
| `\Theorem{I\@.}`: nine run-in theorems in chapter XI, each followed on the same line by its statement | 6908–7277 | new |
| `\subsection*` in the front matter whose title is the macro `\TransNote` | 460 | new: the title exists only after expansion |
| A `\Chapter` inside a comment | 1059 | the same trap as #33283 |

## 4. Equations, figures, tables, lists

| | Count (independent, from the source) | Notes |
|---|---:|---|
| Display `\[ … \]` | 641 | `\\[2pt]` line breaks are not displays |
| Display environments | 84 | `gather*` 44, `multline*` 24, `align*` 16; inside them `aligned` 14, `alignedat` 2, `array` 3, plus `\intertext` and `\displaybreak` |
| Inline `$ … $` | 2,361 | 12 of them inside `\text{…}` within displays |
| Equation tags `\Tag{(n)}` | 562 | on displayed lines |
| References `\Eq{(n)}` | 282 | in text |
| Figures | **0** | no `\includegraphics`, no images folder |
| Tables | **0** | the 3 `array`s are inside mathematics |
| Lists | **0** | no `itemize`/`enumerate`/`description` |
| Footnotes | 85 `\footnote`, 10 `\footnotemark` + `\footnotetext` | |
| Corrections | 160 `\Add`, 33 `\Typo`, 11 `\Chg` in the body | |
| Page markers `\PageSep{…}` | 219 | pages v to 207 |

## 5. Against EDIE 0.1.3, unchanged

Run with the `pg-latex` profile, before any change: 1,939 blocks, 2,348
inline and 725 display expressions, **0 warnings**, 0.24 s, 36 MB.

| | Result |
|---|---|
| Worked | CRLF; all 725 displays, matching the independent count exactly; every inline expression (2,348 in text + 1 in the chapter IX title + 12 inside displays = 2,361); 15 numbered chapters with the source's numbers and multi-line titles; both traps ignored; the PGtext boilerplate; the Project Gutenberg metadata |
| **Silently wrong** | Chapters III and IX: the bookmark title recorded as a **running head**. `\Typo{function}{functions}` would read "functionfunctions". `\ie` vanished from the text (30 times). `\Signature`'s three arguments ran together. `\\[12pt]` and `\rule{2in}{1pt}` left "[12pt]" and "2in1pt" in the text |
| Missing | `\Chap` units (Preface, Contents) not headings; the 9 theorems not structured; the line-460 heading untitled (`\TransNote`); equation tags, corrections and page numbers not recorded; the book's mathematics macros unknown to Markdown renderers |

The structural parse needed no change. What was wrong or missing was
*meaning*, so most of the work is generic machinery a profile can switch on.

## 6. What was done, and where

| Finding | Change | Generic or profile |
|---|---|---|
| `[..]` means different things in different books | named heading arguments (`fields`) apply only to the definition they were written for (`fields_for: "omm"`); new heading field `alt_title` | generic; `pg-50992` names `[..]` `alt_title` |
| A book profile should not repeat pg-latex | profile inheritance (`"extends": "pg-latex"`); `--profile auto` (the default) picks `pg-<ebook number>` when EDIE has one | generic |
| `\Chap`, `\Theorem` | heading kinds `chapter` (unnumbered) and `theorem` | profile |
| Corrections | role `correction`: readable text uses the corrected reading; every correction listed (`corrections[]`) with both readings, line and block | generic role; `pg-50992` assigns it to `\Typo`/`\Chg`/`\Add` |
| Equation tags | role `equation_tag` (standard `\tag` too): `tags[]` on every expression | generic; `pg-50992` adds `\Tag` |
| Page markers | role `page`: every block has `pages` [first, last] from the source's own markers | generic; `pg-50992` assigns `\PageSep` (not yet `pg-latex`: #33283's `\PageSep` definition is unchecked) |
| `\ie`, `\TransNote` | zero-argument macros whose definition is plain text are expanded in readable text | generic |
| `\Signature{..}{..}{..}` | a macro with several arguments reads as its arguments separated by spaces | generic |
| `\\[12pt]`, `\rule`, environment widths, `\BookMark` | line-break spacing, rules and environment arguments are layout; `\BookMark` dropped | generic; `\BookMark` in `pg-50992` |
| Mathematics macros | `definitions[]`: every preamble definition the body uses, directly or through another, as written. The Markdown opens with the ones a renderer can run (`\newcommand{\eps}{\epsilon}` …), and names the two it can't (`\efrac`, `\Fact` use `\ifthenelse`) | generic |
| `\displaybreak`, `\intertext` in Markdown | dropped and `\text` (Markdown only; JSON unchanged) | generic |
| Tables and lists | none in this book; generic `table` and `list` blocks added anyway, tested on fixtures | generic |

**Result (EDIE 0.2.0, profile `pg-50992`, chosen automatically):** 1,948
blocks; 17 chapters (Preface, Contents, I.–XV.); 9 theorems; 725 display and
2,349 inline expressions (+12 inside displays), matching the independent
count; 562 equation tags; 204 corrections; 219 page markers (pages v–207); 0
figures, tables and lists; **0 warnings**; 0.34 s, 38 MB. The output is
byte-identical across three runs in separate processes.
