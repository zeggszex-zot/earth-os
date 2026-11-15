"""The character-level rules of LaTeX that every other part of EDIE relies on.

A brace counts only when it is not escaped (\\{) and not inside a comment (% to the end of the line).  A comment
does not end at an escaped percent sign (\\%).  An optional argument ends at the first ] that is not inside braces,
which is what LaTeX itself does.  Whitespace before an argument may hold one line break but never a blank line:
TeX turns a blank line into \\par, so an argument cannot start after one.

Nothing here uses a regular expression to find structure; each function walks characters from a position and
returns where it stopped, so the caller always knows the exact source span of what it read.
"""

from __future__ import annotations

from dataclasses import dataclass

LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ@")   # @ is a letter in preambles


@dataclass
class Group:
    """One argument: `inner` is the text between the delimiters; start/end span the delimiters too."""
    kind: str          # "{" or "["
    start: int
    end: int
    inner_start: int
    inner_end: int
    text: str          # the whole source's text, so inner can be sliced without copying up front

    @property
    def inner(self) -> str:
        return self.text[self.inner_start:self.inner_end]


class ScanError(Exception):
    def __init__(self, code: str, message: str, offset: int):
        super().__init__(message)
        self.code, self.message, self.offset = code, message, offset


def read_control(text: str, i: int, at_letter: bool = False) -> tuple[str, int]:
    """text[i] is a backslash.  Returns (name, end): a control word (letters) or a control symbol (one character).
    In a preamble @ is a letter (\\@ifnextchar); in the body it is not.  A backslash at the very end of the text is
    a control symbol with an empty name."""
    j = i + 1
    if j >= len(text):
        return "", j
    letter = (lambda c: c in LETTERS) if at_letter else (lambda c: c in LETTERS and c != "@")
    if letter(text[j]):
        while j < len(text) and letter(text[j]):
            j += 1
        return text[i + 1:j], j
    return text[j], j + 1


def skip_comment(text: str, i: int) -> int:
    """text[i] is %.  Returns the offset just past the end of the line (the newline is part of the comment)."""
    j = text.find("\n", i)
    return len(text) if j < 0 else j + 1


def skip_arg_space(text: str, i: int) -> int:
    """Skip what TeX skips before an argument: spaces, tabs, comments and at most one line break.
    Stops at a blank line, which would be a paragraph break."""
    newlines = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t":
            i += 1
        elif ch == "\n":
            if newlines:
                return i
            newlines += 1
            i += 1
        elif ch == "%":
            i = skip_comment(text, i)          # a comment's own line break does not count as a blank line
        else:
            return i
    return i


def find_group_end(text: str, i: int) -> int:
    """text[i] is {.  Returns the offset just past its matching }.  Raises ScanError when there is none."""
    depth, j, n = 0, i, len(text)
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2                              # \{ \} \% \\ are not delimiters
            continue
        if ch == "%":
            j = skip_comment(text, j)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ScanError("UNBALANCED_GROUP", "a { has no matching }", i)


def find_optional_end(text: str, i: int) -> int:
    """text[i] is [.  Returns the offset just past the ] that ends it: the first ] outside any braces."""
    j, n = i + 1, len(text)
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "%":
            j = skip_comment(text, j)
            continue
        if ch == "{":
            j = find_group_end(text, j)
            continue
        if ch == "]":
            return j + 1
        if ch == "\n" and text.startswith("\n", j + 1 + _spaces(text, j + 1)):
            break                               # a blank line: LaTeX would stop with "Paragraph ended"
        j += 1
    raise ScanError("UNCLOSED_OPTIONAL", "a [ starting an optional argument has no ]", i)


def _spaces(text: str, i: int) -> int:
    k = i
    while k < len(text) and text[k] in " \t":
        k += 1
    return k - i


def read_group(text: str, i: int) -> Group | None:
    """A mandatory argument starting at or after i (after skip_arg_space).  A single token without braces is an
    argument too, as in TeX: \\frac12.  Returns None when the next thing is not an argument."""
    j = skip_arg_space(text, i)
    if j >= len(text):
        return None
    ch = text[j]
    if ch == "{":
        end = find_group_end(text, j)
        return Group("{", j, end, j + 1, end - 1, text)
    if ch in "}[]\n" or ch == "%":
        return None
    if ch == "\\":
        _, end = read_control(text, j)
        return Group("{", j, end, j, end, text)
    return Group("{", j, j + 1, j, j + 1, text)


def read_optional(text: str, i: int) -> Group | None:
    j = skip_arg_space(text, i)
    if j < len(text) and text[j] == "[":
        end = find_optional_end(text, j)
        return Group("[", j, end, j + 1, end - 1, text)
    return None


def find_inline_math_end(text: str, i: int, closer: str) -> int:
    """Offset just past the closer ($ or \\)) of math starting at i (just after the opener), or -1 when the
    paragraph ends first: math cannot cross a blank line."""
    j, n = i, len(text)
    while j < n:
        ch = text[j]
        if ch == "\\":
            if closer == "\\)" and text.startswith("\\)", j):
                return j + 2
            j += 2
            continue
        if ch == "%":
            j = skip_comment(text, j)
            continue
        if ch == "{":
            try:
                j = find_group_end(text, j)
            except ScanError:
                return -1
            continue
        if closer == "$" and ch == "$":
            return j + 1
        if ch == "\n":
            k = j + 1 + _spaces(text, j + 1)
            if k < n and text[k] == "\n":
                return -1
        j += 1
    return -1


def find_display_end(text: str, i: int, closer: str) -> int:
    """Offset just past $$ or \\] closing display math that starts at i.  Display math may span lines but, like
    TeX, not a blank line."""
    j, n = i, len(text)
    while j < n:
        ch = text[j]
        if ch == "\\":
            if closer == "\\]" and text.startswith("\\]", j):
                return j + 2
            j += 2
            continue
        if ch == "%":
            j = skip_comment(text, j)
            continue
        if closer == "$$" and text.startswith("$$", j):
            return j + 2
        if ch == "\n":
            k = j + 1 + _spaces(text, j + 1)
            if k < n and text[k] == "\n":
                return -1
        j += 1
    return -1


def find_env_end(text: str, i: int, name: str, verbatim: bool) -> tuple[int, int]:
    """(offset of \\end{name}, offset just past it) for an environment whose body starts at i.  Nested
    environments of the same name are counted; in a verbatim environment nothing is interpreted, so only the
    literal \\end{name} closes it.  (-1, -1) when it never closes."""
    end_tag, begin_tag = "\\end{" + name + "}", "\\begin{" + name + "}"
    if verbatim:
        k = text.find(end_tag, i)
        return (k, k + len(end_tag)) if k >= 0 else (-1, -1)
    depth, j, n = 1, i, len(text)
    while j < n:
        ch = text[j]
        if ch == "%":
            j = skip_comment(text, j)
            continue
        if ch == "\\":
            if text.startswith(end_tag, j):
                depth -= 1
                if depth == 0:
                    return j, j + len(end_tag)
                j += len(end_tag)
                continue
            if text.startswith(begin_tag, j):
                depth += 1
                j += len(begin_tag)
                continue
            j += 2
            continue
        j += 1
    return -1, -1
