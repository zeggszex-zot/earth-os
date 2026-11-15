import unittest

import helpers  # noqa: F401

from edie.latex import scanner as sc


class Groups(unittest.TestCase):
    def test_nested_braces(self):
        t = r"\X{a {b {c}} d}rest"
        g = sc.read_group(t, 2)
        self.assertEqual(g.inner, "a {b {c}} d")
        self.assertEqual(t[g.end:], "rest")

    def test_escaped_braces_do_not_count(self):
        t = r"{a \} b \{ c}after"
        self.assertEqual(sc.read_group(t, 0).inner, r"a \} b \{ c")

    def test_brace_inside_a_comment_does_not_count(self):
        t = "{a % a stray } in a comment\n b}after"
        self.assertEqual(t[sc.find_group_end(t, 0):], "after")

    def test_escaped_percent_is_not_a_comment(self):
        t = r"{5\% of {x}}z"
        self.assertEqual(sc.read_group(t, 0).inner, r"5\% of {x}")

    def test_double_backslash_then_brace(self):
        t = r"{line\\}x"
        self.assertEqual(sc.read_group(t, 0).inner, "line\\\\")

    def test_unbalanced_is_an_error_with_its_offset(self):
        with self.assertRaises(sc.ScanError) as e:
            sc.find_group_end("ab{c{d}", 2)
        self.assertEqual((e.exception.code, e.exception.offset), ("UNBALANCED_GROUP", 2))


class Arguments(unittest.TestCase):
    def test_optional_ends_at_first_bracket_outside_braces(self):
        t = r"[a {b]c} d]{m}"
        g = sc.read_optional(t, 0)
        self.assertEqual(g.inner, "a {b]c} d")

    def test_one_line_break_before_an_argument_is_allowed(self):
        t = "[Head]\n{II}{Title}"
        o = sc.read_optional(t, 0)
        m = sc.read_group(t, o.end)
        self.assertEqual(m.inner, "II")

    def test_comment_between_arguments(self):
        t = "[Head]% note\n{II}"
        self.assertEqual(sc.read_group(t, sc.read_optional(t, 0).end).inner, "II")

    def test_a_blank_line_ends_the_arguments(self):
        t = "[Head]\n\n{II}"
        self.assertIsNone(sc.read_group(t, sc.read_optional(t, 0).end))

    def test_no_optional_when_none_follows(self):
        self.assertIsNone(sc.read_optional("{x}", 0))

    def test_single_token_argument(self):
        self.assertEqual(sc.read_group(r"\frac12", 5).inner, "1")


class Math(unittest.TestCase):
    def test_inline_end_skips_escaped_dollar(self):
        t = r"$a \$ b$ rest"
        self.assertEqual(t[:sc.find_inline_math_end(t, 1, "$")], r"$a \$ b$")

    def test_inline_math_cannot_cross_a_blank_line(self):
        self.assertEqual(sc.find_inline_math_end("$a\n\nb$", 1, "$"), -1)

    def test_display_end(self):
        t = "\\[\n x^2 \n\\] after"
        self.assertEqual(t[sc.find_display_end(t, 2, "\\]"):], " after")

    def test_nested_environment_end(self):
        t = r"\begin{a}x\begin{a}y\end{a}z\end{a}!"
        k, after = sc.find_env_end(t, len(r"\begin{a}"), "a", verbatim=False)
        self.assertEqual(t[after:], "!")

    def test_verbatim_end_ignores_braces(self):
        t = r"\begin{v}{ \end{x} }\end{v}!"
        k, after = sc.find_env_end(t, len(r"\begin{v}"), "v", verbatim=True)
        self.assertEqual(t[after:], "!")


class ControlSequences(unittest.TestCase):
    def test_word_symbol_and_at(self):
        self.assertEqual(sc.read_control(r"\Chapter[", 0), ("Chapter", 8))
        self.assertEqual(sc.read_control(r"\%x", 0), ("%", 2))
        self.assertEqual(sc.read_control(r"\@Chapter", 0)[0], "@")
        self.assertEqual(sc.read_control(r"\@Chapter", 0, at_letter=True)[0], "@Chapter")


if __name__ == "__main__":
    unittest.main()
