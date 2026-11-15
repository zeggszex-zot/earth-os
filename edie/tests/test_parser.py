"""The fixture book, checked against its own source text rather than against numbers copied from EDIE's output."""

import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import FIXTURE, FIXTURE_DIR, line_of

from edie.latex import signatures as sg
from edie.latex.parser import Parser, parse_file
from edie.source import Source

SRC = FIXTURE.read_text(encoding="utf-8")
DOC = parse_file(FIXTURE)


def headings(doc, kind=None):
    return [b for b in doc["blocks"] if b["type"] == "heading" and (kind is None or b["kind"] == kind)]


def parse_text(text: str, root: Path | None = None) -> dict:
    src = Source.from_bytes(text.encode("utf-8"), (root or FIXTURE_DIR) / "t.tex")
    return Parser(src, sg.Profile.load("pg-latex"), root or FIXTURE_DIR).parse()


PREAMBLE = SRC[:SRC.index("\\begin{document}")]


def body(s: str) -> str:
    return PREAMBLE + "\\begin{document}\n" + s + "\n\\end{document}\n"


class ChapterPatterns(unittest.TestCase):
    def test_pattern_1_number_and_title(self):
        h = next(b for b in headings(DOC, "chapter") if b["number"] == "I")
        self.assertEqual(h["title"]["text"], "To Deliver You from the Preliminary Terrors.")
        self.assertIsNone(h["running_head"])
        self.assertEqual(h["lines"][0], line_of(SRC, r"\Chapter{I}"))

    def test_pattern_2_running_head_number_title(self):
        h = next(b for b in headings(DOC, "chapter") if b["number"] == "II")
        self.assertEqual(h["running_head"]["text"], "On Different Degrees of Smallness")
        self.assertEqual(h["title"]["text"], "On Different Degrees of Smallness.")
        self.assertNotEqual(h["running_head"]["latex"], h["title"]["latex"])

    def test_pattern_3_arguments_on_the_next_line(self):
        h = next(b for b in headings(DOC, "chapter") if b["number"] == "III")
        self.assertEqual(h["running_head"]["text"], "Relative Growing")
        self.assertEqual(h["title"]["text"], "On Relative Growings.")
        start = line_of(SRC, r"\Chapter[Relative Growing]")
        self.assertEqual(h["lines"], [start, start + 1])
        self.assertEqual(h["title"]["lines"], [start + 1, start + 1])

    def test_multiline_title_keeps_its_source_and_lines(self):
        h = next(b for b in headings(DOC, "chapter") if b["number"] == "II")
        self.assertIn("\\\\\nSmallness.", h["title"]["latex"])
        first = line_of(SRC, r"{II}{On Different")
        self.assertEqual(h["title"]["lines"], [first, first + 1])

    def test_exactly_the_real_chapters(self):
        """The \\Chapter in a comment and the one inside PGtext are not chapters."""
        self.assertEqual([b["number"] for b in headings(DOC, "chapter")], [None, "I", "II", "III", "XV"])
        self.assertEqual([b["macro"] for b in headings(DOC, "chapter")],
                         ["ChapterStar", "Chapter", "Chapter", "Chapter", "AltChapter"])

    def test_unnumbered_chapter_gets_no_number(self):
        h = headings(DOC, "chapter")[0]
        self.assertEqual((h["number"], h["title"]["text"], h["running_head"]["text"]), (None, "Prologue", "Prologue"))

    def test_a_missing_argument_is_reported_not_invented(self):
        d = parse_text(body("\\Chapter{Only}\n\nText."))
        h = headings(d, "chapter")[0]
        self.assertIsNone(h["title"])
        self.assertIn("ARG_MISSING", [w["code"] for w in d["warnings"]])

    def test_a_title_only_chapter_macro_is_not_warned_about(self):
        """#33283 defines \\AltChapter[head]{title}: Prologue, Table of Standard Forms and Answers have no number by
        design.  v0.1 warned three times on the real book; it must not."""
        d = parse_text(PREAMBLE.replace("\\newcommand{\\AltChapter}[3][]{\\chapter*{#2. #3}}",
                                        "\\newcommand{\\AltChapter}[2][]{\\chapter*{#2}}")
                       + "\\begin{document}\n\\AltChapter{Answers}\n\nText.\n\\end{document}\n")
        h = headings(d, "chapter")[0]
        self.assertEqual((h["number"], h["title"]["text"]), (None, "Answers"))
        self.assertEqual(d["warnings"], [])

    def test_an_empty_number_argument_is_reported(self):
        d = parse_text(body("\\Chapter{}{A Title}\n\nText."))
        h = headings(d, "chapter")[0]
        self.assertEqual((h["number"], h["title"]["text"]), (None, "A Title"))
        self.assertEqual([w["code"] for w in d["warnings"]], ["CHAPTER_UNNUMBERED"])

    def test_the_signature_came_from_the_preamble_dispatch(self):
        rec = next(s for s in DOC["signatures"] if s["name"] == "Chapter")
        self.assertEqual((rec["args"], rec["origin"]), ("omm", "preamble"))
        self.assertEqual(rec["line"], line_of(SRC, "\\newcommand{\\Chapter}"))


class HeadingsAndText(unittest.TestCase):
    def test_hierarchy(self):
        kinds = {b["id"]: b for b in DOC["blocks"]}
        case = headings(DOC, "case")[0]
        para = kinds[case["parent"]]
        sub = kinds[para["parent"]]
        chap = kinds[sub["parent"]]
        self.assertEqual((para["kind"], sub["kind"], chap["number"]), ("paragraph", "subsection", "III"))
        self.assertEqual(headings(DOC, "example")[0]["parent"], para["id"])

    def test_nested_braces_in_a_title(self):
        s = headings(DOC, "section")[0]
        self.assertEqual(s["title"]["latex"], r"Small quantities of the {\itshape very} {first {order}}")
        self.assertEqual(s["title"]["text"], "Small quantities of the very first order")

    def test_escaped_characters(self):
        t = next(b for b in DOC["blocks"] if b["type"] == "text" and "fool" in b["text"])
        self.assertEqual(t["text"], "What one fool can do, another can. Costs 5% more, with {braces} and $1.")
        self.assertEqual(t["inline_math"], [])          # \$ opens no mathematics

    def test_verbatim_is_kept_and_not_interpreted(self):
        v = next(b for b in DOC["blocks"] if b["type"] == "verbatim")
        self.assertEqual(v["environment"], "PGtext")
        self.assertIn("\\Chapter{IX}{Not a Chapter}", v["body"])

    def test_metadata_from_the_gutenberg_header(self):
        m = DOC["metadata"]
        self.assertEqual(m["title"]["value"], "A Test Book Shaped Like Calculus Made Easy Being a fixture for EDIE")
        self.assertEqual((m["author"]["value"], m["ebook_number"]["value"]), ("A. Fixture", "99999"))
        self.assertEqual(m["title"]["line"], line_of(SRC, "Title:"))


class Mathematics(unittest.TestCase):
    def test_inline_math_is_kept_exactly(self):
        t = next(b for b in DOC["blocks"] if b["type"] == "text" and "preliminary terror" in b["text"])
        self.assertEqual([m["latex"] for m in t["inline_math"]], ["$dx$", "$\\int$", "$S$", "$\\int dx$", "$x$"])
        for m in t["inline_math"]:
            self.assertEqual(SRC[m["offsets"][0]:m["offsets"][1]], m["latex"])

    def test_display_math_keeps_the_paragraph(self):
        maths = [b for b in DOC["blocks"] if b["type"] == "math"]
        bracket = next(b for b in maths if b["delimiter"] == "\\[")
        before = DOC["blocks"][DOC["blocks"].index(bracket) - 1]
        after = DOC["blocks"][DOC["blocks"].index(bracket) + 1]
        self.assertEqual(before["paragraph"], bracket["paragraph"])
        self.assertEqual(after["paragraph"], bracket["paragraph"])
        self.assertEqual(bracket["features"], ["derivative", "fraction", "greek"])

    def test_aligned_environment_preserved(self):
        al = next(b for b in DOC["blocks"] if b["type"] == "math" and b["environment"] == "align*")
        start = SRC.index("\\begin{align*}")
        end = SRC.index("\\end{align*}") + len("\\end{align*}")
        self.assertEqual(al["latex"], SRC[start:end])
        self.assertIn("alignment", al["features"])
        self.assertEqual(al["normalized"], r"y + dy &= (x + dx)^2 \\ dy &= 2x\,dx + (dx)^2.")

    def test_integral_and_fraction_features(self):
        dd = next(b for b in DOC["blocks"] if b["type"] == "math" and b["delimiter"] == "$$")
        self.assertEqual(dd["body"], "\\int_0^1 x\\,dx = \\tfrac{1}{2}")
        self.assertEqual(dd["features"], ["fraction", "integral"])

    def test_unclosed_inline_math_is_a_warning(self):
        d = parse_text(body("Price $5 and nothing closes it.\n\nNext."))
        self.assertIn("UNCLOSED_MATH", [w["code"] for w in d["warnings"]])


class Figures(unittest.TestCase):
    def fig(self, file):
        return next(f for f in DOC["figures"] if f["file"] == file)

    def test_block_figure(self):
        f = self.fig("images/fig1.png")
        self.assertEqual((f["macro"], f["context"], f["block_level"]), ("Figure", "text", True))
        self.assertEqual(f["lines"][0], line_of(SRC, r"\Figure[2in]{fig1}"))
        self.assertEqual([a["latex"] for a in f["args"]], ["2in", "fig1"])

    def test_inline_graphic_in_text(self):
        f = self.fig("images/fig2.png")
        t = next(b for b in DOC["blocks"] if b["id"] == f["block"])
        self.assertIn(f"[[figure:{f['id']}]]", t["text"])

    def test_graphic_inside_mathematics_is_kept(self):
        f = self.fig("images/fig3.png")
        self.assertTrue(f["in_math"])
        t = next(b for b in DOC["blocks"] if b["id"] == f["block"])
        m = next(m for m in t["inline_math"] if f["id"] in m["figures"])
        self.assertIn("\\Graphic{0.3in}{fig3}", m["latex"])      # still inside the expression

    def test_figure_environment_with_caption_number(self):
        f = self.fig("images/fig4.png")
        self.assertEqual((f["number"], f["number_origin"], f["label"]), ("4", "caption", "fig:curve"))
        self.assertEqual(f["caption"]["text"], "Fig. 4. A curve and its slope.")

    def test_missing_image_detected(self):
        f = next(f for f in DOC["figures"] if not f["file_exists"])
        self.assertEqual((f["file"], f["candidates"]), (None, ["fig99"]))
        w = next(w for w in DOC["warnings"] if w["code"] == "MISSING_IMAGE")
        self.assertEqual(w["line"], line_of(SRC, r"\Figure{fig99}"))

    def test_unreferenced_images_listed(self):
        self.assertEqual(DOC["metadata"]["unreferenced_images"], ["images/unused.png"])

    def test_deleted_image_is_noticed(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(FIXTURE_DIR, Path(d) / "book")
            (Path(d) / "book" / "images" / "fig2.png").unlink()
            doc = parse_file(Path(d) / "book" / "mini.tex")
            lines = [w["line"] for w in doc["warnings"] if w["code"] == "MISSING_IMAGE"]
            self.assertIn(line_of(SRC, r"\Graphic{0.5in}{fig2}"), lines)


# The definitions as they are in 33283-t.tex (lines 651-675), added after the fixture's own: a later definition wins.
BOOK_FIGURES = r"""
\renewcommand{\Graphic}[2][]{%
  \ifthenelse{\equal{#1}{}}{\includegraphics{./images/#2.pdf}}{\includegraphics[width=#1]{./images/#2.pdf}}%
}
% Usage: \Figure[optional width]{File name}{Fig number}
\renewcommand{\Figure}[3][2.25in]{%
  \begin{figure}[hbt]\centering\Graphic[#1]{#2}\phantomsection\label{fig:#3}\end{figure}%
}
\newcommand{\Figures}[5][2.25in]{%
  \begin{figure}[hbt]\centering\Graphic[#1]{#2}\qquad\Graphic[#1]{#3}\label{fig:#4}\label{fig:#5}\end{figure}%
}
"""


def book_figures(text: str) -> dict:
    return parse_text(PREAMBLE + BOOK_FIGURES + "\\begin{document}\n" + text + "\n\\end{document}\n")


class BookFigureMacros(unittest.TestCase):
    def test_figure_file_and_number_from_their_arguments(self):
        d = book_figures("Text.\n\\Figure[1.5in]{fig1}{1}\n\nMore.")
        f = d["figures"][0]
        self.assertEqual((f["file"], f["number"], f["number_origin"]), ("images/fig1.png", "1", "argument"))
        self.assertEqual([a["latex"] for a in f["args"]], ["1.5in", "fig1", "1"])

    def test_figure_number_is_never_mistaken_for_a_file(self):
        """{2} names the figure, not an image, even if an image called 2 existed: the definition says so."""
        d = book_figures("\\Figure{fig4}{2}")
        self.assertEqual((d["figures"][0]["file"], d["figures"][0]["number"]), ("images/fig4.png", "2"))

    def test_side_by_side_pair_is_two_figures_in_one_block(self):
        d = book_figures("\\Figures{fig1}{fig2}{5}{6}")
        a, b = d["figures"]
        self.assertEqual([(a["file"], a["number"]), (b["file"], b["number"])],
                         [("images/fig1.png", "5"), ("images/fig2.png", "6")])
        self.assertEqual((a["group"], b["group"]), (a["id"], a["id"]))
        blk = next(x for x in d["blocks"] if x["type"] == "figure")
        self.assertEqual(blk["figures"], [a["id"], b["id"]])
        self.assertEqual(a["offsets"], b["offsets"])

    def test_two_graphics_in_one_figure_environment_keep_their_own_positions(self):
        """0.1.2 gave both the environment's span, so the real book's independent count saw 11 \\Graphic, not 12."""
        text = "\\begin{figure}[hbt]\n\\centering\n\\Graphic{fig1}\\qquad\\Graphic{fig2}\n\\end{figure}"
        d = book_figures(text)
        a, b = d["figures"]
        self.assertNotEqual(a["offsets"], b["offsets"])
        src = (PREAMBLE + BOOK_FIGURES + "\\begin{document}\n" + text + "\n\\end{document}\n")
        for f in (a, b):
            self.assertEqual(src[f["offsets"][0]:f["offsets"][1]], f["latex"])
        blk = next(x for x in d["blocks"] if x["type"] == "figure")
        self.assertEqual(blk["figures"], [a["id"], b["id"]])
        self.assertTrue(blk["latex"].startswith("\\begin{figure}"))

    def test_pair_with_a_missing_image(self):
        d = book_figures("\\Figures{fig1}{nothere}{7}{8}")
        self.assertEqual([f["file_exists"] for f in d["figures"]], [True, False])
        self.assertEqual([w["code"] for w in d["warnings"]], ["MISSING_IMAGE"])

    def test_graphic_with_and_without_width(self):
        d = book_figures("A \\Graphic{fig2} and a \\Graphic[1in]{fig3}.")
        self.assertEqual([(f["file"], f["number"]) for f in d["figures"]],
                         [("images/fig2.png", None), ("images/fig3.png", None)])

    def test_fields_are_not_applied_to_a_different_definition(self):
        """The fixture's own \\Figure is [..]{file} with no number: its arguments are tried as files, and no
        number is read from them."""
        f = next(f for f in DOC["figures"] if f["file"] == "images/fig1.png")
        self.assertIsNone(f["number"])


class Traceability(unittest.TestCase):
    def test_every_block_is_its_source_slice_and_lines(self):
        for b in DOC["blocks"]:
            a, z = b["offsets"]
            self.assertEqual(SRC[a:z], b["latex"], b["id"])
            self.assertEqual(b["lines"], [SRC.count("\n", 0, a) + 1, SRC.count("\n", 0, z - 1) + 1], b["id"])

    def test_every_figure_is_its_own_source_slice_and_lines(self):
        for f in DOC["figures"]:
            a, z = f["offsets"]
            self.assertEqual(SRC[a:z], f["latex"], f["id"])
            self.assertEqual(f["lines"], [SRC.count("\n", 0, a) + 1, SRC.count("\n", 0, z - 1) + 1], f["id"])

    def test_crlf_source_has_the_same_structure_and_lines(self):
        crlf = Source.from_bytes(SRC.replace("\n", "\r\n").encode(), FIXTURE)
        d = Parser(crlf, sg.Profile.load("pg-latex"), FIXTURE_DIR).parse()
        strip = lambda doc: [(b["type"], b["lines"]) for b in doc["blocks"]]
        self.assertEqual(strip(d), strip(DOC))
        self.assertEqual(d["source"]["newline"], "CRLF")

    def test_latin1_source_is_decoded_and_says_so(self):
        raw = (PREAMBLE + "\\begin{document}\nCaf\\'e and ").encode("ascii") + b"\xe9t\xe9.\n\\end{document}\n"
        d = Parser(Source.from_bytes(raw, FIXTURE_DIR / "t.tex"), sg.Profile.load("pg-latex"), FIXTURE_DIR).parse()
        self.assertEqual(d["source"]["encoding"], "latin-1")
        t = next(b for b in d["blocks"] if b["type"] == "text")
        self.assertEqual(t["text"], "Café and été.")


if __name__ == "__main__":
    unittest.main()


class Robustness(unittest.TestCase):
    def test_every_truncation_parses_and_stays_traceable(self):
        """Cut the fixture every 7 bytes: an unclosed brace, math or environment is a warning, never a crash,
        and every block is still exactly its source slice."""
        data = FIXTURE.read_bytes()
        prof = sg.Profile.load("pg-latex")
        for cut in range(0, len(data), 7):
            src = Source.from_bytes(data[:cut], FIXTURE_DIR / "t.tex")
            d = Parser(src, prof, FIXTURE_DIR).parse()
            for b in d["blocks"]:
                a, z = b["offsets"]
                self.assertEqual(src.text[a:z], b["latex"], f"cut {cut}, {b['id']}")
