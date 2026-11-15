"""Calculus Made Easy (Project Gutenberg #33283), when it is on this machine.

    EDIE_BOOK=/path/to/33283-t.tex python3 -m unittest test_real_book -v

Default location: ~/earth-os-library/gutenberg/books-original/33283/33283-t.tex.  Skipped when absent.

The structure EDIE extracts is compared with the source by a second, independent method: a line-by-line count of
each structural macro outside comments and verbatim blocks.  The two have to agree.  Nothing here was copied from
EDIE's own output, and no expected chapter count is written down: chapter numbers are checked for being the
consecutive Roman numerals the source states, so a skipped or invented chapter fails.
"""

import hashlib
import os
import re
import unittest
from pathlib import Path

import helpers  # noqa: F401

from edie.latex import signatures as sg
from edie.latex.parser import Parser
from edie.model import dumps
from edie.source import Source

BOOK = Path(os.environ.get("EDIE_BOOK",
                           Path.home() / "earth-os-library/gutenberg/books-original/33283/33283-t.tex"))
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
    body = text[text.index("\\begin{document}"):]
    counts = {n: 0 for n in names}
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
        for n in names:
            counts[n] += len(re.findall(r"\\" + n + r"(?![A-Za-z])", code))
    return counts


@unittest.skipUnless(BOOK.is_file(), f"{BOOK} is not on this machine")
class CalculusMadeEasy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = hashlib.sha256(BOOK.read_bytes()).hexdigest()
        cls.src = Source.load(BOOK)
        cls.prof = sg.Profile.load("pg-latex")
        cls.doc = Parser(cls.src, cls.prof, BOOK.parent).parse()

    def test_source_unchanged(self):
        self.assertEqual(hashlib.sha256(BOOK.read_bytes()).hexdigest(), self.before)
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

    def test_every_block_is_its_source_slice(self):
        for b in self.doc["blocks"]:
            a, z = b["offsets"]
            self.assertEqual(self.src.text[a:z], b["latex"], b["id"])

    def test_reproducible(self):
        again = Parser(Source.load(BOOK), self.prof, BOOK.parent).parse()
        self.assertEqual(dumps(again), dumps(self.doc))

    def test_no_crash_level_problems(self):
        codes = {w["code"] for w in self.doc["warnings"]}
        self.assertFalse(codes & {"UNBALANCED_GROUP", "NO_DOCUMENT_ENV", "NO_DOCUMENT_END", "SIGNATURE_MISMATCH"},
                         [w for w in self.doc["warnings"] if w["code"] in
                          {"UNBALANCED_GROUP", "NO_DOCUMENT_ENV", "NO_DOCUMENT_END", "SIGNATURE_MISMATCH"}][:10])


if __name__ == "__main__":
    unittest.main()
