"""Generic structures added in 0.2: tables, lists, profile inheritance, and named heading fields that apply only
to the definition they were written for."""

import json
import tempfile
import unittest
from pathlib import Path

from helpers import FIXTURE_DIR

from edie.latex import signatures as sg
from edie.latex.parser import Parser, parse_file
from edie.source import Source

PRE = "\\documentclass{book}\n\\usepackage{amsmath}\n"


def parse(body: str, preamble: str = PRE, profile: str = "pg-latex") -> tuple:
    text = preamble + "\\begin{document}\n" + body + "\n\\end{document}\n"
    src = Source.from_bytes(text.encode("utf-8"), FIXTURE_DIR / "s.tex")
    return Parser(src, sg.Profile.load(profile), FIXTURE_DIR).parse(), text


class Tables(unittest.TestCase):
    BODY = r"""Before.

\begin{tabular}{|l|c|}
\hline
Integral & Result \\ \hline
$\int x\,dx$ & $\frac{x^2}{2}$ \\
\multicolumn{2}{c}{a {nested \\ group} \& more} \\[2pt]
\hline
\end{tabular}

After."""

    def test_rows_and_cells(self):
        d, text = parse(self.BODY)
        t = next(b for b in d["blocks"] if b["type"] == "table")
        self.assertEqual((t["environment"], t["colspec"], t["n_rows"], t["n_cols"]), ("tabular", "|l|c|", 3, 2))
        self.assertEqual([[c["text"] for c in r] for r in t["rows"]],
                         [["Integral", "Result"], ["$\\int x\\,dx$", "$\\frac{x^2}{2}$"], ["a nested group & more"]])
        self.assertEqual([m["latex"] for m in t["inline_math"]], ["$\\int x\\,dx$", "$\\frac{x^2}{2}$"])
        self.assertEqual(text[t["offsets"][0]:t["offsets"][1]], t["latex"])
        for row in t["rows"]:
            for c in row:
                self.assertIn(c["latex"], text[c["offsets"][0]:c["offsets"][1] + 1])

    def test_counts_and_text_around(self):
        d, _ = parse(self.BODY)
        self.assertEqual((d["stats"]["tables"], d["stats"]["table_rows"]), (1, 3))
        self.assertEqual([b["text"] for b in d["blocks"] if b["type"] == "text"], ["Before.", "After."])


class Lists(unittest.TestCase):
    BODY = r"""\begin{enumerate}
\item First, with $x^2$.
\item[(b)] Second, which holds a list:
  \begin{itemize}
  \item inner one
  \item inner two
  \end{itemize}
\item Third.
\end{enumerate}

\begin{description}
\item[Term] its meaning
\end{description}"""

    def test_items_labels_and_nesting(self):
        d, text = parse(self.BODY)
        en, de = [b for b in d["blocks"] if b["type"] == "list"]
        self.assertEqual((en["environment"], en["ordered"], en["n_items"]), ("enumerate", True, 3))
        self.assertEqual(en["items"][1]["label"]["text"], "(b)")
        self.assertIn("\\begin{itemize}", en["items"][1]["latex"])          # the nested list stays inside its item
        self.assertIn("• inner one", en["items"][1]["text"])
        self.assertEqual(en["items"][2]["text"], "Third.")
        self.assertEqual([m["latex"] for m in en["inline_math"]], ["$x^2$"])
        self.assertEqual((de["items"][0]["label"]["text"], de["items"][0]["text"]), ("Term", "its meaning"))
        for it in en["items"]:
            self.assertEqual(text[it["offsets"][0]:it["offsets"][1]], it["latex"])
        self.assertEqual((d["stats"]["lists"], d["stats"]["list_items"]), (2, 4))


class Inheritance(unittest.TestCase):
    def test_child_overrides_parent_key_by_key(self):
        p = sg.Profile.load("pg-50992")
        self.assertEqual(p.macros["Chapter"]["fields"], ["alt_title", "number", "title"])
        self.assertEqual(p.macros["Figure"]["fields"], ["width", "file", "number"])     # inherited unchanged
        self.assertIn("PGtext", p.verbatim_envs)
        self.assertIn("Pagelabel", p.drop)
        self.assertEqual(p.chain, ("pg-latex", "pg-50992"))

    def test_a_loop_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.json", Path(d) / "b.json"
            old = sg.PROFILE_DIR
            try:
                sg.PROFILE_DIR = Path(d)
                a.write_text(json.dumps({"format": "edie-profile/1", "name": "a", "extends": "b"}))
                b.write_text(json.dumps({"format": "edie-profile/1", "name": "b", "extends": "a"}))
                with self.assertRaisesRegex(ValueError, "loops"):
                    sg.Profile.load("a")
            finally:
                sg.PROFILE_DIR = old

    def test_generic_profile_when_no_book_profile(self):
        p = sg.Profile.auto("\\def\\ebook{12345}\n")
        self.assertEqual((p.name, p.selected), ("pg-latex", "auto: no profile for ebook 12345; the generic one"))


class FieldsApplyOnlyToTheirDefinition(unittest.TestCase):
    def test_gibbs_names_do_not_apply_to_a_differently_defined_chapter(self):
        """pg-50992 names \\Chapter's [..] an alternate title for the omm definition; a book defining \\Chapter{..}{..}
        under that profile gets the generic reading, not the names."""
        d, _ = parse("\\Chapter{I.}{Title}\n\nText.", PRE + "\\newcommand{\\Chapter}[2]{#1 #2}\n", "pg-50992")
        h = next(b for b in d["blocks"] if b["type"] == "heading")
        self.assertEqual((h["number"], h["title"]["text"], h["alt_title"], h["running_head"]), ("I.", "Title", None, None))

    def test_calculus_reading_is_unchanged(self):
        d = parse_file(FIXTURE_DIR / "mini.tex")
        h = next(b for b in d["blocks"] if b["type"] == "heading" and b.get("number") == "II")
        self.assertEqual((h["running_head"]["text"], h["alt_title"]), ("On Different Degrees of Smallness", None))
        self.assertEqual(d["generator"]["profile"]["name"], "pg-latex")


if __name__ == "__main__":
    unittest.main()
