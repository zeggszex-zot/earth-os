# EDIE — Earth Document Intelligence Engine (v0.2)

Original documents in, structured documents out: hierarchy, text, mathematics and illustrations, every piece
traceable to its source lines.  Deterministic, standard library only, no model, no network.  Design and test
results: [`../docs/EDIE.md`](../docs/EDIE.md).

## Run it on Calculus Made Easy

```bash
cd earth-os/edie                # the edie/ folder at the root of the Earth OS repository
BOOK=~/earth-os-library/gutenberg/books-original/33283
python3 -m edie parse $BOOK/33283-t.tex --out ~/earth-os-library/edie/33283
```

It prints one summary line and the three files it wrote:

| File | What it is |
|---|---|
| `33283-t.edie.json` | the structured document, format `edie-document/1` (schema: `edie/schema/edie-document-1.schema.json`) |
| `33283-t.md` | readable Markdown: headings, `$...$` / `$$...$$` mathematics, image links, a source-line comment before every block |
| `33283-t.report.md` | the parsing report: how each macro was read and from which preamble line, the outline, running heads, illustrations, every warning with its line |

Read the report first.  Then run the real-book tests, which compare EDIE's structure with an independent count
of the source:

```bash
cd earth-os/edie/tests
python3 -m unittest discover -s . -v           # all tests; the real-book ones run when the book is present
EDIE_BOOK_33283=/elsewhere/33283-t.tex EDIE_BOOK_50992=/elsewhere/50992-t.tex python3 -m unittest test_real_book -v
```

Gibbs, *Elementary Principles in Statistical Mechanics* (#50992), the same way: its profile is chosen
automatically.

```bash
python3 -m edie parse ~/earth-os-library/gutenberg/books-original/50992/50992-t.tex --out ~/earth-os-library/edie/50992
```

## Options

| | |
|---|---|
| `--profile auto\|NAME\|PATH` | macro roles, in `edie/profiles/`. `auto` (the default): `pg-<ebook number>` when EDIE has one (`pg-50992`), else `pg-latex` |
| `--images DIR` | the folder image names are resolved against (default: the source's folder) |
| `--image-base PREFIX` | prefix for image links in the Markdown (default: the path from `--out` to the image folder) |
| `--no-anchors` | leave the `<!-- b-00012 L340-352 -->` comments out of the Markdown |
| `--strict` | exit 2 if there is any warning |

Exit status: 0 parsed; 1 the source or the profile could not be read, or an output would overwrite the source;
2 parsed with warnings under `--strict`.

The source and its folder are only read.  Outputs are written atomically (temporary file, fsync, rename).
