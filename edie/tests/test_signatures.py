import unittest

import helpers  # noqa: F401

from edie.latex import signatures as sg


def sigs(preamble: str, profile_macros=None):
    prof = sg.Profile({"format": "edie-profile/1", "name": "t", "macros": profile_macros or {
        "Chapter": {"role": "heading", "kind": "chapter", "level": 1, "args": "omm", "verified": True}}})
    defs = sg.scan_preamble(preamble, len(preamble), lambda i: preamble.count("\n", 0, i) + 1)
    return sg.resolve(prof, defs)


class FromThePreamble(unittest.TestCase):
    def test_newcommand_with_default(self):
        s, notes = sigs(r"\newcommand{\Chapter}[3][]{#2 #3}")
        self.assertEqual((s["Chapter"].args, s["Chapter"].origin, notes), ("omm", "preamble", []))

    def test_newcommand_without_braces_around_the_name(self):
        s, _ = sigs(r"\newcommand\Chapter[2]{#1 #2}")
        self.assertEqual(s["Chapter"].args, "mm")

    def test_xparse(self):
        s, _ = sigs(r"\NewDocumentCommand{\Chapter}{o m m}{#2}")
        self.assertEqual(s["Chapter"].args, "omm")

    def test_def_with_bracket_delimited_parameter(self):
        s, _ = sigs(r"\makeatletter\def\Chapter[#1]#2#3{x}")
        self.assertEqual(s["Chapter"].args, "omm")

    def test_dispatch_follows_the_bracket_branch(self):
        pre = ("\\newcommand{\\Chapter}{\\@ifnextchar[{\\@Chapter}{\\@@Chapter}}\n"
               "\\def\\@Chapter[#1]#2#3{x}\n\\def\\@@Chapter#1#2{y}\n")
        s, notes = sigs(pre)
        self.assertEqual(s["Chapter"].args, "omm")
        self.assertIn("dispatches on [", s["Chapter"].note)
        self.assertEqual(s["Chapter"].line, 1)

    def test_unreadable_definition_falls_back_to_the_profile_and_says_so(self):
        s, notes = sigs(r"\def\Chapter#1.#2.{x}")
        self.assertEqual((s["Chapter"].args, s["Chapter"].origin), ("omm", "profile"))
        self.assertTrue(any(n["macro"] == "Chapter" for n in notes))

    def test_disagreeing_with_a_verified_pattern_is_a_warning(self):
        s, notes = sigs(r"\newcommand{\Chapter}[2]{#1}")
        self.assertEqual(s["Chapter"].args, "mm")
        self.assertEqual([n.get("code") for n in notes], ["SIGNATURE_MISMATCH"])

    def test_disagreeing_with_an_unverified_fallback_is_only_a_note(self):
        s, notes = sigs(r"\newcommand{\Section}[1]{#1}",
                        {"Section": {"role": "heading", "kind": "section", "level": 2, "args": "om"}})
        self.assertEqual((s["Section"].args, notes), ("m", []))
        self.assertIn("profile expected om", s["Section"].note)

    def test_comments_are_not_definitions(self):
        s, _ = sigs("% \\newcommand{\\Chapter}[1]{x}\n")
        self.assertEqual(s["Chapter"].origin, "profile")


class Profiles(unittest.TestCase):
    def test_shipped_profile_loads(self):
        p = sg.Profile.load("pg-latex")
        for m in ("Chapter", "ChapterStar", "AltChapter", "Section", "Subsection", "Paragraph", "Case", "Example",
                  "Figure", "Graphic"):
            self.assertIn(m, p.macros)
        self.assertIn("PGtext", p.verbatim_envs)

    def test_wrong_format_is_refused(self):
        with self.assertRaises(ValueError):
            sg.Profile({"format": "something/2", "name": "x"})


if __name__ == "__main__":
    unittest.main()
