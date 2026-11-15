"""A LaTeX fragment as readable text, for people and for the Markdown export.

This is a reading aid, not a translation: the block's `latex` field always keeps the source exactly.  Mathematics
is copied through untouched ($...$ stays $...$), so no expression is simplified or reinterpreted.  Illustrations
become markers, [[figure:fig-00012]], that the Markdown export turns into image links.
"""

from __future__ import annotations

import re
import unicodedata

from . import scanner as sc

ACCENTS = {"'": "́", "`": "̀", "^": "̂", '"': "̈", "~": "̃", "=": "̄", ".": "̇",
           "c": "̧", "u": "̆", "v": "̌", "H": "̋", "k": "̨"}
SYMBOLS = {"%": "%", "$": "$", "&": "&", "#": "#", "_": "_", "{": "{", "}": "}", " ": " ", ",": " ",
           ";": " ", ":": " ", "!": "", "-": "", "/": "", "@": "", "\\": " ", "\n": " ", "\t": " "}
WORDS = {"ldots": "…", "dots": "…", "textellipsis": "…", "S": "§", "P": "¶", "pounds": "£", "copyright": "©",
         "dag": "†", "ddag": "‡", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ", "ss": "ß", "o": "ø", "O": "Ø",
         "l": "ł", "L": "Ł", "i": "ı", "aa": "å", "AA": "Å", "TeX": "TeX", "LaTeX": "LaTeX", "quad": " ",
         "qquad": "  ", "textbackslash": "\\", "textendash": "–", "textemdash": "—", "textdegree": "°",
         "degree": "°", "times": "×", "newline": " ", "linebreak": " ", "thinspace": " ", "enspace": " ",
         "textquoteleft": "‘", "textquoteright": "’", "textquotedblleft": "“", "textquotedblright": "”"}
# Formatting that changes how text looks, not what it says: the name goes, the argument's text stays.
DECLARATIONS = {"itshape", "bfseries", "scshape", "upshape", "mdseries", "rmfamily", "sffamily", "ttfamily",
                "normalfont", "em", "it", "bf", "sc", "rm", "sf", "tt", "sl", "slshape", "tiny", "scriptsize",
                "footnotesize", "small", "normalsize", "large", "Large", "LARGE", "huge", "Huge", "centering",
                "raggedleft", "raggedright", "selectfont"}
SPACE = re.compile(r"[ \t ]+")


class Context:
    def __init__(self, sigs: dict, drop: dict, figures: dict | None = None, roles: dict | None = None):
        self.sigs = sigs                # name -> Signature
        self.drop = drop                # name -> args pattern
        self.figures = figures or {}    # offset of a figure macro -> its figure ids
        self.roles = roles or {}        # name -> profile spec


def _skip_args(text: str, j: int, pattern: str) -> int:
    for a in pattern:
        g = sc.read_optional(text, j) if a == "o" else sc.read_group(text, j)
        if g is None:
            if a == "m":
                break
            continue
        j = g.end
    return j


def render(text: str, start: int, end: int, ctx: Context) -> str:
    """Never raises: a construct that can't be read (a { never closed) is copied through as written."""
    try:
        return _render(text, start, end, ctx)
    except sc.ScanError:
        return " ".join(text[start:end].split())


def _render(text: str, start: int, end: int, ctx: Context) -> str:
    out: list = []
    i = start
    while i < end:
        ch = text[i]
        if ch == "%":
            i = min(sc.skip_comment(text, i), end)
            while i < end and text[i] in " \t":     # TeX drops the next line's leading spaces too
                i += 1
            continue
        if ch == "$":
            disp = text.startswith("$$", i)
            close = sc.find_display_end(text, i + 2, "$$") if disp else sc.find_inline_math_end(text, i + 1, "$")
            if close < 0 or close > end:
                out.append("$")
                i += 1
                continue
            out.append(text[i:close])
            i = close
            continue
        if ch == "\\":
            name, j = sc.read_control(text, i)
            if name in ("(", "["):
                close = (sc.find_inline_math_end(text, j, "\\)") if name == "("
                         else sc.find_display_end(text, j, "\\]"))
                if 0 <= close <= end:
                    out.append(text[i:close])
                    i = close
                    continue
            if i in ctx.figures:
                spec = ctx.sigs.get(name)
                ids = ctx.figures[i]
                out.append(" ".join(f"[[figure:{x}]]" for x in ([ids] if isinstance(ids, str) else ids)))
                i = _safe_skip(text, j, spec.args if spec else "", end)
                continue
            if len(name) == 1 and not name.isalpha():
                if name in ACCENTS and j < end:
                    i = _accent(text, j, ACCENTS[name], out, end)
                    continue
                out.append(SYMBOLS.get(name, name))
                i = j
                continue
            if name in ACCENTS and len(name) == 1 and j < end and text[j] in "{ ":
                i = _accent(text, j, ACCENTS[name], out, end)
                continue
            if name in ctx.drop:
                i = _safe_skip(text, j, ctx.drop[name], end)
                continue
            if name == "verb" and j < end:
                d = text[j]
                k = text.find(d, j + 1)
                if 0 <= k < end:
                    out.append(text[j + 1:k])
                    i = k + 1
                    continue
            if name in ("begin", "end"):
                g = sc.read_group(text, j)
                i = g.end if g and g.end <= end else j
                continue
            if name == "item":
                out.append("\n• ")
                g = sc.read_optional(text, j)
                if g and g.end <= end:
                    out.append(_render(text, g.inner_start, g.inner_end, ctx) + " ")
                    j = g.end
                i = j
                continue
            if name == "footnote":
                g = sc.read_group(text, j)
                if g and g.end <= end:
                    out.append(f" [footnote: {_render(text, g.inner_start, g.inner_end, ctx)}]")
                    i = g.end
                    continue
            if name in WORDS:
                out.append(WORDS[name])
                i = _eat_spaces(text, j, end)
                continue
            if name in DECLARATIONS:
                i = _eat_spaces(text, j, end)
                continue
            spec = ctx.sigs.get(name)
            if spec and spec.args.startswith("o"):
                g = sc.read_optional(text, j)
                if g and g.end <= end:
                    j = g.end
            i = _eat_spaces(text, j, end) if j < end and text[j] in " \t" else j
            continue
        if ch in "{}":
            i += 1
            continue
        if ch == "~":
            out.append(" ")
        elif ch == "-" and text.startswith("---", i):
            out.append("—")
            i += 3
            continue
        elif ch == "-" and text.startswith("--", i):
            out.append("–")
            i += 2
            continue
        elif ch == "`" and text.startswith("``", i):
            out.append("“")
            i += 2
            continue
        elif ch == "`":
            out.append("‘")
        elif ch == "'" and text.startswith("''", i):
            out.append("”")
            i += 2
            continue
        elif ch == "\n":
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    joined = "".join(out)
    lines = [SPACE.sub(" ", ln).strip() for ln in joined.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def _safe_skip(text: str, j: int, pattern: str, end: int) -> int:
    try:
        k = _skip_args(text, j, pattern)
    except sc.ScanError:
        return j
    return k if k <= end else j


def _eat_spaces(text: str, j: int, end: int) -> int:
    while j < end and text[j] in " \t":
        j += 1
    return j


def _accent(text: str, j: int, mark: str, out: list, end: int) -> int:
    """\\'e, \\'{e}, \\c{c}, \\c c: the letter with its combining mark, composed where Unicode has one character."""
    k = _eat_spaces(text, j, end)
    if k < end and text[k] == "{":
        try:
            g_end = sc.find_group_end(text, k)
        except sc.ScanError:
            return j
        inner = text[k + 1:g_end - 1].strip()
        if inner.startswith("\\"):
            nm, _ = sc.read_control(inner, 0)
            inner = WORDS.get(nm, nm)
        out.append(unicodedata.normalize("NFC", inner + mark) if inner else mark)
        return g_end
    if k < end:
        out.append(unicodedata.normalize("NFC", text[k] + mark))
        return k + 1
    return k
