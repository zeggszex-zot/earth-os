"""The real books, when they are on this machine: Calculus Made Easy (Project Gutenberg #33283) and Elementary
Principles in Statistical Mechanics (#50992, Gibbs).

    python3 -m unittest test_real_book -v
    EDIE_BOOK_33283=/path/33283-t.tex EDIE_BOOK_50992=/path/50992-t.tex python3 -m unittest test_real_book -v

Default locations: ~/earth-os-library/gutenberg/books-original/<number>/<number>-t.tex (EDIE_BOOK still names the
first book, as in 0.1).  Each book's tests are skipped when it is absent.

Every check in RealBook runs on both books; each book adds what only it has.

The structure EDIE extracts is compared with the source by a second, independent method: a line-by-line count of
each structural macro outside comments and verbatim blocks.  The two have to agree.  Nothing here was copied from
EDIE's own output, and no expected chapter count is written down: chapter numbers are checked for being the
consecutive Roman numerals the source states, so a skipped or invented chapter fails.
"""

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401

from edie.latex import signatures as sg
from edie.latex.parser import Parser
from edie.model import dumps
from edie.source import Source

LIBRARY = Path.home() / "earth-os-library/gutenberg/books-original"
BOOK = Path(os.environ.get("EDIE_BOOK_33283") or os.environ.get("EDIE_BOOK") or LIBRARY / "33283/33283-t.tex")
GIBBS = Path(os.environ.get("EDIE_BOOK_50992") or LIBRARY / "50992/50992-t.tex")
ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def roman(s: str):
    s = s.strip().rstrip(".").upper()
    if not s or any(c not in ROMAN for c in s):
        return None
    total = 0
    for a, b in zip(s, s[1:] + " "):
        v = ROMAN[a]
        total += -v if b != " " and ROMAN[b] > v else v
    return total


def independent_counts(text: str, names, verbatim_envs) -> dict:
    """Occurrences of each macro in the body, outside comments and verbatim environments, counted per line."""
    body = code_lines(text, verbatim_envs)
    counts = {n: 0 for n in names}
    for code in body:
        for n in names:
            counts[n] += len(re.findall(r"\\" + n + r"(?![A-Za-z])", code))
    return counts


def code_lines(text: str, verbatim_envs) -> list:
    """The body's lines, from \\begin{document} to \\end{document} as EDIE reads it, comments cut off and
    verbatim environments left out."""
    text = text.replace("\r\n", "\n")
    start = text.index("\\begin{document}")
    end = text.find("\\end{document}", start)
    body = text[start:end if end >= 0 else len(text)]
    out = []
    inside = None
    for line in body.split("\n"):
        if inside:
            if f"\\end{{{inside}}}" in line:
                inside = None
            continue
        code = re.split(r"(?<!\\)%", line, maxsplit=1)[0]
        for env in verbatim_envs:
            if f"\\begin{{{env}}}" in code:
                inside = env
                code = code.split(f"\\begin{{{env}}}")[0]
        out.append(code)
    return out


def independent_math(text: str, verbatim_envs, math_envs) -> tuple:
    """(display, inline) expressions counted from the source's characters alone: \\[, $$ and math environments;
    single $ pairs and \\(.  An escaped \\$ and the line break \\\\[2pt] are not mathematics."""
    code = "\n".join(code_lines(text, verbatim_envs)).replace("\\\\", "  ").replace("\\$", "  ")
    envs = sum(code.count(f"\\begin{{{e}}}") for e in math_envs)
    display = code.count("\\[") + code.count("$$") // 2 + envs
    inline = (code.count("$") - 2 * code.count("$$")) // 2 + code.count("\\(")
    return display, inline


MATH_BASES = r"(?:align|alignat|gather|multline|flalign|eqnarray|equation|displaymath)"
DEFINES_MATH = re.compile(r"\\(?:begin\{|end\{|end)?" + MATH_BASES + r"\*?(?![A-Za-z@])")


def defined_math_envs(text: str) -> set:
    """Environments the preamble defines with \\newenvironment (or a relative) whose definition, up to the next
    definition of anything, mentions a standard display-math environment: \\begin{align*}, \\endalign, \\gather ...
    A plain search of the text, not EDIE's reading of the definition."""
    pre = text[:text.index("\\begin{document}")]
    pre = "\n".join(re.split(r"(?<!\\)%", ln, maxsplit=1)[0] for ln in pre.split("\n"))
    found = set()
    ends = [m.start() for m in re.finditer(r"\\(?:new|renew|provide|New|Renew|Declare|Provide)[A-Za-z]*"
                                           r"(?:command|environment|Command|Environment)|\\def\\|\\makeatother",
                                           pre)]
    for m in re.finditer(r"\\(?:new|renew|provide)environment\*?\{([^}]*)\}|\\(?:New|Renew|Declare|Provide)"
                         r"DocumentEnvironment\{([^}]*)\}", pre):
        name = m.group(1) or m.group(2)
        stop = min([e for e in ends if e > m.start()] + [len(pre)])
        if DEFINES_MATH.search(pre[m.end():stop]):
            found.add(name)
    return found


def edie_math(doc: dict) -> tuple:
    """(display, inline) as EDIE recorded them: math blocks; every inline expression wherever it was recorded,
    plus those written inside another expression, display or inline (\\text{... $x$ ...}), which stay part of
    the expression they are written in."""
    inline = 0

    def walk(x):
        nonlocal inline
        if isinstance(x, dict):
            if x.get("delimiter") in ("$", "\\(") and "body" in x:
                inline += 1 + x["body"].replace("\\$", "").count("$") // 2
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(doc["blocks"])
    walk(doc["figures"])
    maths = [b for b in doc["blocks"] if b["type"] == "math"]
    inside = sum((b["latex"].replace("\\$", "").count("$") - (4 if b["delimiter"] == "$$" else 0)) // 2
                 for b in maths)
    return len(maths), inline + inside
class RealBook:
    """Checks every real book must pass.  BOOK and PROFILE are set by each book's class."""
    BOOK: Path
    PROFILE: str

    @classmethod
    def setUpClass(cls):
        cls.before = hashlib.sha256(cls.BOOK.read_bytes()).hexdigest()
        cls.src = Source.load(cls.BOOK)
        cls.prof = sg.Profile.load(cls.PROFILE)
        cls.doc = Parser(cls.src, cls.prof, cls.BOOK.parent).parse()

    def test_auto_selects_this_books_profile(self):
        self.assertEqual(sg.Profile.auto(self.src.text).name, self.PROFILE)

    def test_source_unchanged(self):
        self.assertEqual(hashlib.sha256(self.BOOK.read_bytes()).hexdigest(), self.before)
        self.assertEqual(self.doc["source"]["sha256"], self.before)

    def test_structure_agrees_with_an_independent_count(self):
        heads = [m for m, s in self.prof.raw["macros"].items() if s["role"] == "heading"]
        figs = [m for m, s in self.prof.raw["macros"].items() if s["role"] == "figure"]
        verb = sorted({b["environment"] for b in self.doc["blocks"] if b["type"] == "verbatim"} | {"verbatim"})
        want = independent_counts(self.src.text, heads + figs, verb)
        got = {n: 0 for n in heads + figs}
        for b in self.doc["blocks"]:
            if b["type"] == "heading" and b["macro"] in got:
                got[b["macro"]] += 1
        for f in self.doc["figures"]:
            if f["macro"] in got and f["group"] in (None, f["id"]):      # a \\Figures pair is one use
                got[f["macro"]] += 1
        self.assertEqual(got, want)

    def test_chapter_numbers_are_the_sources_own_and_consecutive(self):
        numbered = [b for b in self.doc["blocks"] if b["type"] == "heading" and b["kind"] == "chapter" and b["number"]]
        values = [roman(b["number"]) for b in numbered]
        self.assertTrue(numbered, "no numbered chapters found")
        self.assertNotIn(None, values, [b["number"] for b in numbered])
        self.assertEqual(values, list(range(1, len(values) + 1)),
                         [(b["number"], b["lines"][0]) for b in numbered])
        for b in numbered:
            self.assertIn(b["number_latex"], b["latex"])          # stated in the source, not supplied

    def test_every_chapter_has_a_title_distinct_from_its_number(self):
        for b in self.doc["blocks"]:
            if b["type"] == "heading" and b["kind"] == "chapter":
                self.assertTrue(b["title"] and b["title"]["text"], b["lines"])
                self.assertNotEqual(b["title"]["text"], b["number"])

    def test_running_heads_are_not_titles(self):
        for b in self.doc["blocks"]:
            if b["type"] == "heading" and b["running_head"]:
                ra, rz = b["running_head"]["offsets"]
                ta, tz = b["title"]["offsets"]
                self.assertTrue(rz <= ta or tz <= ra, b["lines"])  # different spans of the source

    def test_mathematics_agrees_with_an_independent_count(self):
        verb = sorted({b["environment"] for b in self.doc["blocks"] if b["type"] == "verbatim"} | {"verbatim"})
        envs = set(self.prof.math_envs) | defined_math_envs(self.src.text)
        self.assertEqual(edie_math(self.doc), independent_math(self.src.text, verb, envs))

    def test_tags_corrections_and_page_markers_agree_with_an_independent_count(self):
        verb = sorted({b["environment"] for b in self.doc["blocks"] if b["type"] == "verbatim"} | {"verbatim"})
        by_role = lambda role: [m for m, s in self.prof.macros.items() if s.get("role") == role]
        count = lambda names: sum(independent_counts(self.src.text, names, verb).values()) if names else 0
        self.assertEqual(self.doc["stats"]["equation_tags"], count(by_role("equation_tag")))
        self.assertEqual(len(self.doc["corrections"]), count(by_role("correction")))
        self.assertEqual(self.doc["stats"]["page_markers"], count(by_role("page")))

    def test_every_figure_and_correction_is_its_source_slice(self):
        for x in self.doc["figures"] + self.doc["corrections"]:
            a, z = x["offsets"]
            self.assertEqual(self.src.text[a:z], x["latex"], x.get("id", x["lines"]))

    def test_cli_twice_in_separate_processes_is_byte_identical(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for out in (a, b):
                r = subprocess.run([sys.executable, "-m", "edie", "parse", str(self.BOOK), "--out", out,
                                    "--image-base", "images"], cwd=root, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
            for f in sorted(Path(a).iterdir()):
                self.assertEqual(f.read_bytes(), (Path(b) / f.name).read_bytes(), f.name)

    def test_every_block_is_its_source_slice(self):
        for b in self.doc["blocks"]:
            a, z = b["offsets"]
            self.assertEqual(self.src.text[a:z], b["latex"], b["id"])

    def test_reproducible(self):
        again = Parser(Source.load(self.BOOK), self.prof, self.BOOK.parent).parse()
        self.assertEqual(dumps(again), dumps(self.doc))

    def test_no_crash_level_problems(self):
        codes = {w["code"] for w in self.doc["warnings"]}
        self.assertFalse(codes & {"UNBALANCED_GROUP", "NO_DOCUMENT_ENV", "NO_DOCUMENT_END", "SIGNATURE_MISMATCH"},
                         [w for w in self.doc["warnings"] if w["code"] in
                          {"UNBALANCED_GROUP", "NO_DOCUMENT_ENV", "NO_DOCUMENT_END", "SIGNATURE_MISMATCH"}][:10])



@unittest.skipUnless(BOOK.is_file(), f"{BOOK} is not on this machine")
class CalculusMadeEasy(RealBook, unittest.TestCase):
    BOOK, PROFILE = BOOK, "pg-latex"

    def test_figure_numbers_come_from_the_source(self):
        """\\Figure[width]{file}{number} and \\Figures[width]{f1}{f2}{n1}{n2} state their numbers: every one has
        a number, it is in the macro's own text, and no number is used twice."""
        figs = [f for f in self.doc["figures"] if f["macro"] in ("Figure", "Figures")]
        for f in figs:
            self.assertTrue(f["number"], f["lines"])
            self.assertEqual(f["number_origin"], "argument")
            self.assertIn("{" + f["number"] + "}", f["latex"])
        numbers = [f["number"] for f in figs]
        self.assertEqual(len(numbers), len(set(numbers)), sorted(n for n in numbers if numbers.count(n) > 1))



@unittest.skipUnless(GIBBS.is_file(), f"{GIBBS} is not on this machine")
class StatisticalMechanics(RealBook, unittest.TestCase):
    """#50992: no figures; numbered chapters I.-XV. with an optional [..] that is an alternate (bookmark) title;
    unnumbered \\Chap units; run-in \\Theorem{I\\@.}; transcriber's corrections; equation tags; page markers."""
    BOOK, PROFILE = GIBBS, "pg-50992"

    def heads(self, kind):
        return [b for b in self.doc["blocks"] if b["type"] == "heading" and b["kind"] == kind]

    def test_alternate_titles_are_exactly_the_chapters_that_give_one(self):
        verb = sorted({b["environment"] for b in self.doc["blocks"] if b["type"] == "verbatim"} | {"verbatim"})
        given = sum(len(re.findall(r"\\Chapter\s*\[", c)) for c in code_lines(self.src.text, verb))
        alts = [b for b in self.heads("chapter") if b["alt_title"]]
        self.assertEqual(len(alts), given)
        for b in self.heads("chapter"):
            self.assertIsNone(b["running_head"], b["lines"])        # this book's [..] is never a running head
        for b in alts:
            self.assertNotEqual(b["alt_title"]["offsets"], b["title"]["offsets"])

    def test_theorems_are_consecutive_and_inside_chapters(self):
        th = self.heads("theorem")
        self.assertTrue(th)
        self.assertEqual([roman(t["number"]) for t in th], list(range(1, len(th) + 1)))
        chapters = {b["id"] for b in self.heads("chapter")}
        self.assertTrue(all(t["parent"] in chapters for t in th))

    def test_unnumbered_chapters_have_titles_and_no_number(self):
        chap = [b for b in self.heads("chapter") if b["macro"] == "Chap"]
        self.assertTrue(chap)
        for b in chap:
            self.assertIsNone(b["number"])
            self.assertTrue(b["title"]["text"])

    def test_no_correction_macro_is_left_in_readable_text(self):
        names = [m for m, s in self.prof.macros.items() if s.get("role") == "correction"]
        for b in self.doc["blocks"]:
            if b["type"] == "text":
                plain = re.sub(r"\$[^$]*\$", "", b["text"])          # mathematics is kept as written
                for n in names:
                    self.assertNotIn("\\" + n + "{", plain, b["lines"])

    def test_every_block_after_the_first_page_marker_has_its_page(self):
        """The block holding the first marker may start before it (its first page is then unknown, null, not
        guessed); every block after that one starts on a known page."""
        seen = False
        for b in self.doc["blocks"]:
            if seen:
                self.assertTrue(b["pages"] and b["pages"][0], b["lines"])
            seen = seen or bool(b["pages"])


if __name__ == "__main__":
    unittest.main()
