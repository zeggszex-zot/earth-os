"""The conventions of the second book, #50992 (Gibbs), on a small fixture written to them: what the generic parser
and the pg-50992 profile must do, checked against the fixture's own text, with LF and with CRLF line endings."""

import json
import unittest

from helpers import ROOT, line_of

from edie.export.markdown import to_markdown
from edie.export.report import to_report
from edie.latex import signatures as sg
from edie.latex.parser import Parser, parse_file
from edie.model import dumps
from edie.source import Source

FIX = ROOT / "tests" / "fixtures" / "gibbs-mini" / "mini.tex"
SRC = FIX.read_text(encoding="utf-8")
DOC = parse_file(FIX)


def crlf_doc():
    src = Source.from_bytes(SRC.replace("\n", "\r\n").encode("utf-8"), FIX)
    return Parser(src, sg.Profile.auto(src.text), FIX.parent).parse()


def heads(doc, kind=None):
    return [b for b in doc["blocks"] if b["type"] == "heading" and (kind is None or b["kind"] == kind)]


class ProfileSelection(unittest.TestCase):
    def test_the_books_own_profile_is_chosen_from_its_ebook_number(self):
        p = DOC["generator"]["profile"]
        self.assertEqual((p["name"], p["extends"]), ("pg-50992", ["pg-latex"]))
        self.assertIn("ebook 50992", p["selected"])

    def test_the_same_parse_under_crlf(self):
        d = crlf_doc()
        self.assertEqual(d["source"]["newline"], "CRLF")
        shape = lambda doc: [(b["type"], b["lines"], b.get("number"), b["pages"]) for b in doc["blocks"]]
        self.assertEqual(shape(d), shape(DOC))
        self.assertEqual(d["corrections"][0]["lines"], DOC["corrections"][0]["lines"])


class Headings(unittest.TestCase):
    def test_optional_argument_is_an_alternate_title_not_a_running_head(self):
        h = next(b for b in heads(DOC, "chapter") if b["number"] == "II.")
        self.assertIsNone(h["running_head"])
        self.assertEqual(h["alt_title"]["text"], "The Function phi and the Canonical Distribution.")
        self.assertEqual(h["title"]["latex"], "The Function $\\phi$ and the\nCanonical Distribution.")
        self.assertEqual([m["latex"] for m in h["title"]["inline_math"]], ["$\\phi$"])

    def test_chapters_with_trailing_period_numbers_and_multiline_titles(self):
        h = next(b for b in heads(DOC, "chapter") if b["number"] == "I.")
        self.assertEqual(h["title"]["text"], "General Notions. The Principle of Conservation of Extension-in-Phase.")
        start = line_of(SRC, "\\Chapter{I.}")
        self.assertEqual(h["lines"], [start, start + 1])

    def test_exactly_the_real_chapters(self):
        self.assertEqual([(b["macro"], b["number"]) for b in heads(DOC, "chapter")],
                         [("Chap", None), ("Chapter", "I."), ("Chapter", "II.")])

    def test_unnumbered_chapter(self):
        h = heads(DOC, "chapter")[0]
        self.assertEqual((h["title"]["text"], h["number"], h["alt_title"], h["running_head"]),
                         ("Preface.", None, None, None))

    def test_theorems_are_numbered_run_in_headings_under_their_chapter(self):
        th = heads(DOC, "theorem")
        self.assertEqual([t["number"] for t in th], ["I.", "II."])
        chap = next(b for b in heads(DOC, "chapter") if b["number"] == "II.")
        self.assertTrue(all(t["parent"] == chap["id"] and t["title"] is None for t in th))
        after = DOC["blocks"][DOC["blocks"].index(th[0]) + 1]
        self.assertTrue(after["text"].startswith("If an ensemble of systems"))

    def test_a_title_made_of_a_text_macro_is_readable(self):
        h = heads(DOC, "subsection")[0]
        self.assertEqual(h["title"]["text"], "Transcriber's Note")
        self.assertIn("\\TransNote", h["title"]["latex"])

    def test_no_warnings(self):
        self.assertEqual(DOC["warnings"], [])


class Text(unittest.TestCase):
    def block(self, needle):
        return next(b for b in DOC["blocks"] if b["type"] == "text" and needle in b["text"])

    def test_corrections_read_as_corrected_and_both_are_kept(self):
        t = self.block("usual point of view")
        self.assertIn("the study of a system", t["text"])
        self.assertNotIn("stuy", t["text"])
        c = DOC["corrections"][0]
        self.assertEqual((c["macro"], c["original"], c["corrected"], c["block"]), ("Typo", "stuy", "study", t["id"]))
        self.assertEqual(c["lines"][0], line_of(SRC, "\\Typo{stuy}"))
        self.assertEqual(SRC[c["offsets"][0]:c["offsets"][1]], c["latex"])

    def test_every_correction_in_the_body_is_recorded_once(self):
        self.assertEqual([(c["macro"], c["original"], c["corrected"]) for c in DOC["corrections"]],
                         [("Typo", "stuy", "study"), ("Add", None, "+"), ("Chg", "\\eps", "e"), ("Add", None, ",")])

    def test_text_macros_and_multi_argument_macros(self):
        t = self.block("usual point of view")
        self.assertIn("i.e.,", t["text"])
        self.assertIn("J. W. G. New Haven, December, 1901.", t["text"])

    def test_equation_reference_reads_as_its_number(self):
        self.assertIn("by (1)", self.block("moment of the forces")["text"])


class Mathematics(unittest.TestCase):
    def maths(self):
        return [b for b in DOC["blocks"] if b["type"] == "math"]

    def test_equation_tags(self):
        a, b = self.maths()
        self.assertEqual([t["text"] for t in a["tags"]], ["(1)"])
        self.assertEqual([t["text"] for t in b["tags"]], ["(2)", "(3)"])
        self.assertEqual(b["environment"], "gather*")
        self.assertEqual(DOC["stats"]["equation_tags"], 3)
        self.assertEqual(b["tags"][1]["line"], line_of(SRC, "\\Tag{(3)}"))

    def test_mathematics_keeps_its_source_exactly(self):
        for m in self.maths():
            self.assertEqual(SRC[m["offsets"][0]:m["offsets"][1]], m["latex"])
        self.assertIn("\\Chg{\\eps}{e}", self.maths()[1]["body"])

    def test_definitions_the_body_uses_including_indirect_ones(self):
        defs = {d["name"]: d for d in DOC["definitions"]}
        self.assertEqual(defs["eps"]["body"], "\\epsilon")
        self.assertEqual((defs["Int"]["uses"], defs["Int"]["via"]), (0, "intap"))
        self.assertEqual(defs["Tag"]["line"], line_of(SRC, "\\newcommand{\\Tag}"))
        self.assertNotIn("Signature", [d for d in defs if defs[d]["uses"] == 0])


class Pages(unittest.TestCase):
    def test_blocks_carry_the_printed_page(self):
        pref = next(b for b in heads(DOC, "chapter") if b["number"] is None)
        self.assertEqual(pref["pages"], ["vii", "vii"])
        self.assertEqual(heads(DOC, "chapter")[1]["pages"], ["1", "1"])
        self.assertEqual(heads(DOC, "theorem")[0]["pages"], ["2", "2"])
        body = SRC[SRC.index("\\begin{document}"):]
        self.assertEqual(DOC["stats"]["page_markers"], body.count("\\PageSep{"))
        self.assertIsNone(DOC["blocks"][0]["pages"])          # before the first marker: unknown, not invented


class Exports(unittest.TestCase):
    md = to_markdown(DOC)

    def test_markdown_defines_the_books_math_macros_from_its_preamble(self):
        self.assertIn("\\newcommand{\\eps}{\\epsilon}", self.md)
        self.assertIn("\\newcommand{\\Int}[2]{\\int\\!\\! \\overset", self.md)
        self.assertIn("\\newcommand{\\Tag}[1]{\\tag*{#1}}", self.md)
        self.assertIn("not copied (they do more than mathematics): \\efrac", self.md)

    def test_markdown_tagged_gather_lists_its_tags_and_drops_page_breaks(self):
        self.assertIn("$$\n\\begin{gathered}", self.md)
        self.assertIn("*Equations (2), (3)*", self.md)
        self.assertNotIn("displaybreak", self.md)
        self.assertIn("$$\nF_{1}\\, dq_{1} + F_{2}\\, dq_{2} \\Add{+} \\cdots + F_{n}\\, dq_{n}\n\\Tag{(1)}\n$$",
                      self.md)

    def test_markdown_headings(self):
        self.assertIn("# I. General Notions.", self.md)
        self.assertIn("#### Theorem I.", self.md)
        self.assertIn("<!-- alternate title: The Function phi and the Canonical Distribution. -->", self.md)

    def test_report(self):
        r = to_report(DOC)
        self.assertIn("profile `pg-50992` extends `pg-latex`", r)
        self.assertIn("## Alternate titles", r)
        self.assertIn("| 57 | `\\Typo` | stuy | study |", r)
        self.assertIn("Printed pages, from the source's own page markers: vii to 2.", r)

    def test_reproducible(self):
        self.assertEqual(dumps(parse_file(FIX)), dumps(DOC))
        self.assertEqual(json.loads(dumps(DOC))["source"]["sha256"], DOC["source"]["sha256"])


if __name__ == "__main__":
    unittest.main()
