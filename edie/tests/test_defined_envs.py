"""0.2.1: what Calculus Made Easy (#33283) showed when the 0.2 tests first ran on it.

* An environment the preamble defines as display mathematics is display mathematics, however it is built:
  #33283's DPalign* and DPgather* collect their body token by token and end with \\endalign, so nothing in them
  says \\begin{align*}.  0.1-0.2.0 read their 110 uses as paragraph text.
* A page marker inside a display, a table or a list still turns the page.
* \\DPPageSep{026.png}{14} is a page marker whose page is its second argument; it is never text.
"""

import unittest

from test_structures import parse

from edie.export.markdown import to_markdown

DP = r"""\makeatletter
\def\lintertext{lint}
\newenvironment{DPalign*}[1][a]{%
  \global\DP@lign@no\z@\parseb@dy
}{%
  \endalign
}
\newenvironment{Eqn}{\begin{gather*}}{\end{gather*}}
\newenvironment{Both}{\begin{Eqn}}{\end{Eqn}}
\newenvironment{Problems}[1][1]{\begin{enumerate}}{\end{enumerate}}
\makeatother
\newcommand{\DPPageSep}[2]{\label{page:#2}}
"""
PRE = "\\documentclass{book}\n\\usepackage{amsmath}\n" + DP


class DefinedMathEnvironments(unittest.TestCase):
    BODY = r"""Before the display.
\begin{DPalign*}[m]
y &= x^2 \tag*{(i)} \\
\lintertext{where $x$ is small}
z &= 2x.
\end{DPalign*}
After it.

\begin{Eqn}
a = b
\end{Eqn}

\begin{Both}
c = d
\end{Both}

\begin{Problems}
Not mathematics: $e$.
\end{Problems}"""

    def setUp(self):
        self.d, self.text = parse(self.BODY, PRE)
        self.maths = [b for b in self.d["blocks"] if b["type"] == "math"]

    def test_learned_from_the_preamble_with_their_base(self):
        envs = {e["name"]: (e["based_on"], e["args"]) for e in self.d["environments"]}
        self.assertEqual(envs, {"DPalign*": ("align", "o"), "Eqn": ("gather*", ""), "Both": ("Eqn", "")})
        self.assertEqual(self.d["stats"]["defined_math_environments"], 3)
        for e in self.d["environments"]:
            self.assertIn(e["latex"], self.text)
            self.assertIn("\\newenvironment{" + e["name"] + "}", e["latex"])

    def test_each_use_is_one_display_with_its_tag_and_nested_math(self):
        self.assertEqual([b["environment"] for b in self.maths], ["DPalign*", "Eqn", "Both"])
        dp = self.maths[0]
        self.assertTrue(dp["latex"].startswith("\\begin{DPalign*}[m]"))
        self.assertFalse(dp["body"].lstrip().startswith("[m]"))           # its own argument is not mathematics
        self.assertEqual([t["latex"] for t in dp["tags"]], ["(i)"])
        self.assertIn("$x$", dp["latex"])
        text = " ".join(b["text"] for b in self.d["blocks"] if b["type"] == "text")
        self.assertNotIn("x^2", text)

    def test_an_environment_that_is_not_mathematics_stays_text(self):
        self.assertNotIn("Problems", {e["name"] for e in self.d["environments"]})
        self.assertTrue(any("Not mathematics" in b.get("text", "") for b in self.d["blocks"]))

    def test_markdown_uses_the_base_environment_and_text_for_intertext(self):
        md = to_markdown(self.d)
        self.assertIn("\\begin{aligned}", md)
        self.assertNotIn("\\begin{DPalign*}", md)
        self.assertIn("\\text{where $x$ is small}", md)
        self.assertNotIn("\\lintertext", md)
        self.assertIn("\\begin{gathered}", md)
        self.assertNotIn("\\begin{Both}", md)                          # Both -> Eqn -> gather*
        self.assertNotIn("\\begin{Eqn}", md)
        self.assertIn("<!-- LaTeX environment DPalign*;", md)


class PageMarkers(unittest.TestCase):
    BODY = r"""\DPPageSep{001.png}{1}%
First page text.
\begin{align*}
a &= b \\
\DPPageSep{002.png}{2}%
c &= d
\end{align*}
Still on page two, and the
\DPPageSep{003.png}{3}%
sentence goes on.

\begin{itemize}
\item one
\DPPageSep{004.png}{4}%
\item two
\end{itemize}
Last."""

    def setUp(self):
        self.d, _ = parse(self.BODY, PRE)
        self.blocks = [b for b in self.d["blocks"] if b["type"] in ("text", "math", "list")]

    def test_dppagesep_is_a_page_marker_read_from_its_second_argument(self):
        self.assertEqual(self.d["stats"]["page_markers"], 4)
        text = " ".join(b.get("text", "") for b in self.d["blocks"])
        self.assertNotIn(".png", text)
        self.assertIn("the sentence goes on", " ".join(text.split()))

    def test_a_marker_inside_a_display_or_list_turns_the_page(self):
        by_type = [(b["type"], b["pages"]) for b in self.blocks]
        self.assertEqual(by_type, [("text", ["1", "1"]), ("math", ["1", "2"]), ("text", ["2", "3"]),
                                   ("list", ["3", "4"]), ("text", ["4", "4"])])


if __name__ == "__main__":
    unittest.main()
