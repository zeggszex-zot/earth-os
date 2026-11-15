"""LaTeX source -> edie-document/1.  One pass over the body, character by character, in document order.

What it recognises, and nothing else:
  headings     any macro the profile gives the role "heading" (\\Chapter, \\Section, ...; \\section* and friends)
  mathematics  $..$  \\(..\\)  inline;  $$..$$  \\[..\\]  and math environments (align, equation, ...) as display
  figures      macros with the role "figure" (\\Figure, \\Graphic, \\includegraphics), in text, in mathematics,
               or inside a figure environment (with its \\caption)
  verbatim     environments whose body must not be interpreted (verbatim, PGtext, ...)
  text         everything else, cut into blocks at blank lines and at the things above

Every block keeps the exact source slice it came from (`latex`), its line range and its character offsets.
A heading's arguments are read with the pattern the document's preamble gives (signatures.py), so a running head
is never taken for a title.  Nothing missing is filled in: an absent argument is null and a warning says where.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import model
from ..source import Source
from . import render as rd
from . import scanner as sc
from . import signatures as sg

GREEK = set("alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi "
            "rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma "
            "Upsilon Phi Psi Omega".split())
FEATURES = {"fraction": {"frac", "dfrac", "tfrac", "cfrac", "over"}, "integral": {"int", "iint", "iiint", "oint"},
            "root": {"sqrt"}, "sum": {"sum", "prod"}, "limit": {"lim"}, "derivative": {"partial"},
            "function": {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "exp", "sinh", "cosh", "tanh"}}
ALIGN_ENVS = {"align", "align*", "alignat", "alignat*", "eqnarray", "eqnarray*", "aligned", "array", "split",
              "gathered", "cases", "matrix", "pmatrix", "bmatrix", "flalign", "flalign*"}
DERIVATIVE = re.compile(r"\\[dt]?frac\s*\{\s*d|\\[dt]?frac\s*\{\s*\\partial")   # d/dx written as a fraction
FIG_NUMBER = re.compile(r"\bFig(?:ure)?\.?(?:\\ |~|\s)*(\d+[a-z]?)\b")
PG_FIELDS = {"title": "title", "author": "author", "release date": "release_date", "language": "language",
             "character set encoding": "declared_encoding"}


class Parser:
    def __init__(self, source: Source, profile: sg.Profile, image_root: Path | None = None):
        self.src = source
        self.text = source.text
        self.profile = profile
        self.root = Path(image_root) if image_root else source.path.parent
        self.b = model.Builder(source, profile)
        self.envs: list = []                   # open non-math environments, for the record only
        self.heading_stack: list = []          # (level, block id)
        self.para_start: int | None = None
        self.para_math: list = []
        self.para_figs: list = []
        self.verbatim = set(profile.verbatim_envs)
        self.math_envs = set(profile.math_envs)
        self.env_args: dict = {}
        self._para_used = False
        self.para_page = None                  # the page a text block starts on (page markers, e.g. \\PageSep)

    # ------------------------------------------------------------------ entry point

    def parse(self) -> dict:
        t = self.text
        begin = t.find("\\begin{document}")
        if begin < 0:
            body_start, pre_end = 0, 0
            self.b.warn("NO_DOCUMENT_ENV", "no \\begin{document}: the whole file is read as the body")
        else:
            pre_end, body_start = begin, begin + len("\\begin{document}")
        end = t.find("\\end{document}", body_start)
        body_end = end if end >= 0 else len(t)
        if begin >= 0 and end < 0:
            self.b.warn("NO_DOCUMENT_END", "no \\end{document}: the body runs to the end of the file", len(t) - 1)

        defs = sg.scan_preamble(t, pre_end, self.src.line)
        self.sigs, notes = sg.resolve(self.profile, defs)
        for n in notes:
            if n.get("code") == "SIGNATURE_MISMATCH" or n["macro"] in self.profile.raw.get("macros", {}):
                self.b.warnings.append({"code": n.get("code", "SIGNATURE_UNREAD"), "message": f"\\{n['macro']}: {n['note']}",
                                        "line": n["line"]})
        self.verbatim |= self._verbatim_envs_defined(pre_end)
        # Environments the preamble defines as display mathematics (#33283's DPalign*, which ends with \endalign).
        learned = sg.math_environments(sg.scan_environments(t, pre_end, self.src.line), self.profile.math_envs)
        self.math_envs = set(self.profile.math_envs) | set(learned)
        self.env_args = {n: d.args for n, d in learned.items() if d.args}
        self.b.environments = [{"name": n, "role": "math", "based_on": d.based_on, "args": d.args, "line": d.line,
                                "origin": "preamble", "latex": d.source} for n, d in sorted(learned.items())]
        self.b.signatures = sg.as_records(self.sigs, self.profile)
        self._pg_metadata()
        self._latex_metadata(0, pre_end)
        self.b.body_lines = [self.src.line(body_start), self.src.line(max(body_start, body_end - 1))]
        self.ctx = rd.Context(self.sigs, self.profile.drop, {}, self.profile.macros, text_macros(defs))

        self._walk(body_start, body_end)
        self._flush(body_end)
        self._unreferenced_images()
        self._corrections(body_start, body_end)
        self.b.definitions = used_definitions(defs, t[body_start:body_end])
        return self.b.document()

    # ------------------------------------------------------------------ metadata

    def _pg_metadata(self) -> None:
        """'Title: ...' lines of a Project Gutenberg header, with their indented continuation lines.  Read from the
        first 300 lines, commented or not; each value records the line it came from."""
        # Header lines are often drawn as a box: "%% Title: Calculus Made Easy        %%".  Both borders go.
        lines = [ln.lstrip("%").rstrip().rstrip("%").rstrip() for ln in self.text.split("\n", 300)[:300]]
        k = 0
        while k < len(lines):
            raw = lines[k].strip()
            low = raw.lower()
            for label, key in PG_FIELDS.items():
                if low.startswith(label + ":") and key not in self.b.metadata:
                    value = raw[len(label) + 1:].strip()
                    j = k + 1
                    while key == "title" and j < len(lines):
                        nxt = lines[j]
                        if nxt.strip() and nxt[:1] in " \t" and ":" not in nxt.split()[0]:
                            value += " " + nxt.strip()
                            j += 1
                        else:
                            break
                    if value:
                        self.b.metadata[key] = {"value": value, "origin": "gutenberg-header", "line": k + 1}
            m = re.search(r"\[E[Bb]ook #(\d+)\]", raw)
            if m and "ebook_number" not in self.b.metadata:
                self.b.metadata["ebook_number"] = {"value": m.group(1), "origin": "gutenberg-header", "line": k + 1}
            k += 1

    def _latex_metadata(self, start: int, end: int) -> None:
        for name, spec in self.profile.macros.items():
            if spec.get("role") != "metadata":
                continue
            at = self._find_macro(name, start, end)
            if at < 0:
                continue
            args, _ = self._read_args(at + 1 + len(name), self.sigs[name].args, at)
            g = next((a for a in args if a and a.kind == "{"), None)
            if g:
                self.b.metadata[f"latex_{spec['field']}"] = {
                    "value": rd.render(self.text, g.inner_start, g.inner_end, rd.Context(self.sigs, self.profile.drop)),
                    "latex": g.inner, "origin": "preamble", "line": self.src.line(at)}

    def _find_macro(self, name: str, start: int, end: int) -> int:
        i, t = start, self.text
        while i < end:
            if t[i] == "%":
                i = sc.skip_comment(t, i)
                continue
            if t[i] == "\\":
                nm, j = sc.read_control(t, i)
                if nm == name:
                    return i
                i = j
                continue
            i += 1
        return -1

    def _verbatim_envs_defined(self, end: int) -> set:
        """Environments the preamble makes verbatim (fancyvrb's \\DefineVerbatimEnvironment{PGtext}{Verbatim}...)."""
        out, i, t = set(), 0, self.text
        tag = "\\DefineVerbatimEnvironment"
        while True:
            i = t.find(tag, i, end)
            if i < 0:
                return out
            try:
                g = sc.read_group(t, i + len(tag))
            except sc.ScanError:
                g = None
            if g:
                out.add(g.inner.strip())
            i += len(tag)

    # ------------------------------------------------------------------ the walk

    def _walk(self, start: int, end: int) -> None:
        t = self.text
        i = start
        while i < end:
            ch = t[i]
            if ch == "%":
                i = sc.skip_comment(t, i)
                continue
            if ch == "\n":
                k = i + 1
                while k < end and t[k] in " \t":
                    k += 1
                if k < end and t[k] == "\n":
                    self._flush(i)
                    i = k
                    continue
                i += 1
                continue
            if ch in " \t":
                i += 1
                continue
            if ch == "$":
                i = self._dollar(i, end)
                continue
            if ch != "\\":
                self._open(i)
                i += 1
                continue
            name, j = sc.read_control(t, i)
            try:
                i = self._control(name, i, j, end)
            except sc.ScanError as e:
                # One malformed construct (a { never closed) costs a warning, not the document: its text stays in
                # the block it is in, and the walk goes on after the control word.
                self.b.warn(e.code, f"\\{name}: {e.message}", e.offset)
                self._open(i)
                i = j

    def _control(self, name: str, i: int, j: int, end: int) -> int:
        t = self.text
        if name == "(":
            return self._inline_math(i, j, "\\)")
        if name == "[":
            return self._display(i, j, "\\]", "\\[")
        if name == "begin":
            return self._begin(i, j, end)
        if name == "end":
            g = sc.read_group(t, j)
            if g and self.envs and self.envs[-1] == g.inner.strip():
                self.envs.pop()
            self._open(i)
            return g.end if g else j
        if name == "verb" and j < end:
            k = t.find(t[j], j + 1)
            self._open(i)
            return k + 1 if k > 0 else j
        role = self.profile.macros.get(name, {}).get("role")
        if role == "heading":
            return self._heading(name, i, j)
        if role == "figure":
            return self._figure_macro(name, i, j, "text")
        if role == "metadata":
            return self._read_args(j, self.sigs[name].args, i)[1]
        if role == "page":
            return self._page_marker(name, i, j)
        self._open(i)
        return j

    def _page_marker(self, name: str, i: int, j: int) -> int:
        """A page marker (\\PageSep{14}, \\DPPageSep{026.png}{14}): the printed page that starts here."""
        pattern = self.sigs[name].args
        args, k = self._read_args(j, pattern, i)
        spec = self.profile.macros[name]
        fields = spec.get("fields") if spec.get("fields_for") in (None, pattern) else None
        if fields and "page" in fields and fields.index("page") < len(args):
            g = args[fields.index("page")]                   # \DPPageSep{026.png}{14}: the page is the 2nd argument
        else:
            g = next((a for a in args if a is not None and a.kind == "{"), None)
        if g is not None:
            self.b.page = g.inner.strip()
            self.b.page_markers += 1
        return k

    def _pages_in(self, start: int, end: int):
        """Page markers inside a block the walk does not enter (a display, a table, a list, a figure environment):
        the page turns there too.  Returns the page the block starts on."""
        first, i, t = self.b.page, start, self.text
        while i < end:
            if t[i] == "%":
                i = sc.skip_comment(t, i)
                continue
            if t[i] != "\\":
                i += 1
                continue
            name, j = sc.read_control(t, i)
            if self.profile.macros.get(name, {}).get("role") == "page" and name in self.sigs:
                try:
                    i = max(self._page_marker(name, i, j), j)
                except sc.ScanError:
                    i = j
                continue
            i = max(j, i + 1)
        return first

    def _open(self, i: int) -> None:
        if self.para_start is None:
            self.para_start = i
            self.para_page = self.b.page

    def _section(self) -> str | None:
        return self.heading_stack[-1][1] if self.heading_stack else None

    def _flush(self, end: int, para_break: bool = True) -> None:
        """Close the current text block at `end` (exclusive).  A block whose text is only layout (\\newpage,
        comments) is not emitted; it is counted.  `para_break`: the TeX paragraph ends here too (a blank line, a
        heading); display mathematics does not end one, so the text after it keeps the paragraph number."""
        if self.para_start is None:
            if para_break:
                self._next_paragraph()
            return
        start = self.para_start
        while end > start and self.text[end - 1] in " \t\n":
            end -= 1
        text = rd.render(self.text, start, end, self.ctx)
        if text or self.para_math or self.para_figs:
            block = self.b.add_block({"type": "text", "section": self._section(), "paragraph": self.b.paragraph,
                                      "_page_start": self.para_page,
                                      **self.b.span(start, end), "latex": self.text[start:end], "text": text,
                                      "inline_math": self.para_math, "figures": [f["id"] for f in self.para_figs]})
            for f in self.para_figs:
                f["block"] = block["id"]
            self._para_used = True
        else:
            self.b.layout_only += 1
        self.para_start, self.para_math, self.para_figs = None, [], []
        if para_break:
            self._next_paragraph()

    def _next_paragraph(self) -> None:
        if self._para_used:
            self.b.paragraph += 1
            self._para_used = False

    # ------------------------------------------------------------------ arguments

    def _read_args(self, j: int, pattern: str, at: int) -> tuple[list, int]:
        """Arguments in `pattern` starting at j.  A missing mandatory argument is None, with a warning."""
        args = []
        for a in pattern:
            try:
                g = sc.read_optional(self.text, j) if a == "o" else sc.read_group(self.text, j)
            except sc.ScanError as e:
                self.b.warn(e.code, e.message, e.offset)
                g = None
            if g is None and a == "m":
                self.b.warn("ARG_MISSING", f"a mandatory argument of the macro at line {self.src.line(at)} is missing",
                            at)
            if g is not None:
                j = g.end
            args.append(g)
        return args, j

    def _field(self, g) -> dict | None:
        if g is None:
            return None
        return {"latex": g.inner, "text": rd.render(self.text, g.inner_start, g.inner_end, self.ctx),
                "inline_math": self._math_in(g.inner_start, g.inner_end), **self.b.span(g.inner_start, g.inner_end)}

    # ------------------------------------------------------------------ headings

    def _heading(self, name: str, i: int, j: int) -> int:
        spec = self.profile.macros[name]
        star = False
        if j < len(self.text) and self.text[j] == "*":
            star, j = True, j + 1
        pattern = self.sigs[name].args
        args, k = self._read_args(j, pattern, i)
        if any(a is None for a, p in zip(args, pattern) if p == "m") and not any(args):
            self._open(i)                       # not used as a heading here (\\Chapter mentioned in text)
            return j
        self._flush(i)
        opt = [a for a, p in zip(args, pattern) if p == "o"]
        mand = [a for a, p in zip(args, pattern) if p == "m"]
        # Named arguments apply only to the definition they were written for ("fields_for"): the same macro name in
        # another book may mean something else (\\Chapter's [..] is a running head in #33283, a bookmark title in
        # #50992).  Without names: [..] is the running head, the last {..} the title, the one before it the number.
        fields = spec.get("fields") if spec.get("fields_for") in (None, pattern) else None
        named = {}
        if fields:
            named = {f: self._field(a) for f, a in zip(fields, args) if f}
        title = named.get("title") if fields else self._field(mand[-1] if mand else None)
        number = named.get("number") if fields else (self._field(mand[-2]) if len(mand) >= 2 else None)
        running = named.get("running_head") if fields else (self._field(opt[0]) if opt else None)
        alt = named.get("alt_title") if fields else None
        extra = [self._field(a) for a in mand[:-2]] if not fields and len(mand) > 2 else []
        level = spec["level"]
        while self.heading_stack and self.heading_stack[-1][0] >= level:
            self.heading_stack.pop()
        parent = self._section()
        # A heading macro whose definition has no number argument (\AltChapter{Answers} in #33283) is unnumbered by
        # design: nothing to warn about.  Only a number argument that is there but empty is worth a line.
        if number is not None and not number["text"].strip():
            self.b.warn("CHAPTER_UNNUMBERED" if spec["kind"] == "chapter" else "HEADING_NUMBER_EMPTY",
                        f"\\{name}: its number argument is empty; none is supplied", i)
            number = None
        block = self.b.add_block({
            "type": "heading", "kind": spec["kind"], "level": level, "macro": name, "star": star,
            "number": number["text"] if number else None, "number_latex": number["latex"] if number else None,
            "title": title, "running_head": running, "alt_title": alt, "extra_args": extra, "parent": parent,
            **self.b.span(i, k), "latex": self.text[i:k]})
        self.heading_stack.append((level, block["id"]))
        return k

    # ------------------------------------------------------------------ mathematics

    def _dollar(self, i: int, end: int) -> int:
        if self.text.startswith("$$", i):
            return self._display(i, i + 2, "$$", "$$")
        return self._inline_math(i, i + 1, "$")

    def _inline_math(self, i: int, j: int, closer: str) -> int:
        close = sc.find_inline_math_end(self.text, j, closer)
        self._open(i)
        if close < 0:
            self.b.warn("UNCLOSED_MATH", f"inline mathematics opened with {self.text[i:j]} is not closed in its paragraph", i)
            return j
        body_end = close - len(closer)
        figs = self._figures_in(j, body_end, "math")
        self.para_math.append({"delimiter": self.text[i:j], "latex": self.text[i:close], "body": self.text[j:body_end],
                               "normalized": normalize(self.text, j, body_end),
                               "features": features(self.text[j:body_end], None),
                               "tags": self._tags(j, body_end),
                               "figures": [f["id"] for f in figs], **self.b.span(i, close)})
        self.para_figs.extend(figs)
        return close

    def _display(self, i: int, j: int, closer: str, opener: str, env: str | None = None, close: int | None = None) -> int:
        if close is None:
            close = sc.find_display_end(self.text, j, closer)
        if close < 0:
            self.b.warn("UNCLOSED_MATH", f"display mathematics opened with {opener} is not closed", i)
            self._open(i)
            return j
        self._flush(i, para_break=False)
        body_end = close - len(closer)
        figs = self._figures_in(j, body_end, "math")
        first = self._pages_in(j, body_end)
        block = self.b.add_block({"type": "math", "display": True, "delimiter": opener, "environment": env,
                                  "_page_start": first,
                                  "section": self._section(), "paragraph": self.b.paragraph,
                                  **self.b.span(i, close), "latex": self.text[i:close], "body": self.text[j:body_end],
                                  "normalized": normalize(self.text, j, body_end),
                                  "features": features(self.text[j:body_end], env),
                                  "tags": self._tags(j, body_end),
                                  "figures": [f["id"] for f in figs]})
        for f in figs:
            f["block"] = block["id"]
        self._para_used = True
        return close

    # ------------------------------------------------------------------ environments

    def _begin(self, i: int, j: int, end: int) -> int:
        g = sc.read_group(self.text, j)
        if not g:
            self._open(i)
            return j
        name = g.inner.strip()
        if name in self.verbatim:
            k, after = sc.find_env_end(self.text, g.end, name, verbatim=True)
            if k < 0:
                self.b.warn("UNCLOSED_ENV", f"\\begin{{{name}}} has no \\end{{{name}}}", i)
                k = after = end
            self._flush(i)
            self.b.add_block({"type": "verbatim", "environment": name, "section": self._section(),
                              **self.b.span(i, after), "latex": self.text[i:after], "body": self.text[g.end:k]})
            return after
        if name in self.math_envs:
            k, after = sc.find_env_end(self.text, g.end, name, verbatim=False)
            if k < 0:
                self.b.warn("UNCLOSED_MATH", f"\\begin{{{name}}} has no \\end{{{name}}}", i)
                self._open(i)
                return g.end
            start = self._env_args_end(g.end, self.env_args.get(name, ""), k)   # \begin{DPalign*}[m]: not mathematics
            return self._display(i, start, "\\end{" + name + "}", "\\begin{" + name + "}", env=name, close=after)
        if name in self.profile.table_envs or name in self.profile.list_envs:
            k, after = sc.find_env_end(self.text, g.end, name, verbatim=False)
            if k < 0:
                self.b.warn("UNCLOSED_ENV", f"\\begin{{{name}}} has no \\end{{{name}}}", i)
                self._open(i)
                return g.end
            self._flush(i)
            (self._table if name in self.profile.table_envs else self._list)(name, i, g.end, k, after)
            return after
        if name in self.profile.figure_envs:
            k, after = sc.find_env_end(self.text, g.end, name, verbatim=False)
            if k < 0:
                self.b.warn("UNCLOSED_ENV", f"\\begin{{{name}}} has no \\end{{{name}}}", i)
                k = after = end
            self._flush(i)
            self._figure_env(name, i, g.end, k, after)
            return after
        self.envs.append(name)
        self._open(i)
        return g.end

    def _env_args_end(self, j: int, pattern: str, limit: int) -> int:
        """Where an environment's own arguments end (pattern as for macros: o optional, m mandatory)."""
        for c in pattern:
            try:
                g = sc.read_optional(self.text, j) if c == "o" else sc.read_group(self.text, j)
            except sc.ScanError:
                g = None
            if g is None or g.end > limit:
                if c == "o":
                    continue
                break
            j = g.end
        return j

    # ------------------------------------------------------------------ tags, corrections, inline mathematics in a span

    def _tags(self, start: int, end: int) -> list:
        """Equation tags inside an expression (\\tag, \\tag*, or a book's own, e.g. #50992's \\Tag), as written and
        as text, in order.  The expression itself is not changed."""
        out, i, t = [], start, self.text
        while i < end:
            if t[i] == "%":
                i = sc.skip_comment(t, i)
                continue
            if t[i] != "\\":
                i += 1
                continue
            name, j = sc.read_control(t, i)
            if self.profile.macros.get(name, {}).get("role") == "equation_tag":
                if j < end and t[j] == "*":
                    j += 1
                try:
                    g = sc.read_group(t, j)
                except sc.ScanError:
                    g = None
                if g is not None and g.end <= end:
                    out.append({"macro": name, "latex": g.inner,
                                "text": rd.render(t, g.inner_start, g.inner_end, self.ctx), "line": self.src.line(i)})
                    self.b.equation_tags += 1
                    i = g.end
                    continue
            i = j
        return out

    def _math_in(self, start: int, end: int) -> list:
        """Inline mathematics ($..$, \\(..\\)) inside a table or list, recorded like a paragraph's."""
        out, i, t = [], start, self.text
        while i < end:
            ch = t[i]
            if ch == "%":
                i = sc.skip_comment(t, i)
                continue
            opener = closer = None
            if ch == "$" and not t.startswith("$$", i):
                opener, closer, j = "$", "$", i + 1
            elif t.startswith("\\(", i):
                opener, closer, j = "\\(", "\\)", i + 2
            elif ch == "\\":
                i += 2
                continue
            if opener is None:
                i += 1
                continue
            close = sc.find_inline_math_end(t, j, closer)
            if close < 0 or close > end:
                i = j
                continue
            body_end = close - len(closer)
            out.append({"delimiter": opener, "latex": t[i:close], "body": t[j:body_end],
                        "normalized": normalize(t, j, body_end), "features": features(t[j:body_end], None),
                        "tags": self._tags(j, body_end), "figures": [], **self.b.span(i, close)})
            i = close
        return out

    def _corrections(self, start: int, end: int) -> None:
        """Every transcriber's correction (\\Typo{original}{corrected} and the like) in the body, outside comments
        and verbatim blocks, with what was printed, what the text now says, and where."""
        names = {n for n, s in self.profile.macros.items() if s.get("role") == "correction"}
        if not names:
            return
        verbatim = [(b["offsets"][0], b["offsets"][1]) for b in self.b.blocks if b["type"] == "verbatim"]
        starts = [b["offsets"][0] for b in self.b.blocks]
        import bisect
        i, t, v = start, self.text, 0
        while i < end:
            while v < len(verbatim) and verbatim[v][1] <= i:
                v += 1
            if v < len(verbatim) and verbatim[v][0] <= i:
                i = verbatim[v][1]
                continue
            if t[i] == "%":
                i = sc.skip_comment(t, i)
                continue
            if t[i] != "\\":
                i += 1
                continue
            name, j = sc.read_control(t, i)
            if name not in names:
                i = j
                continue
            spec = self.profile.macros[name]
            pattern = self.sigs[name].args
            try:
                args, k = sc_args(t, j, pattern)
            except sc.ScanError:
                i = j
                continue
            fields = spec.get("fields") if spec.get("fields_for") in (None, pattern) else None
            fields = fields or (["original", "corrected"] if len(args) == 2 else ["corrected"] * len(args))
            val = {f: (a.inner if a is not None else None) for f, a in zip(fields, args)}
            n = bisect.bisect_right(starts, i) - 1
            self.b.corrections.append({"macro": name, "original": val.get("original"), "corrected": val.get("corrected"),
                                       "block": self.b.blocks[n]["id"] if n >= 0 else None,
                                       **self.b.span(i, k), "latex": t[i:k]})
            i = j                                # nested corrections are found too
        self.b.corrections.sort(key=lambda c: c["offsets"][0])

    # ------------------------------------------------------------------ tables and lists

    def _env_args(self, name: str, j: int) -> tuple[list, int]:
        pattern = {"tabular": "om", "tabular*": "mom", "tabularx": "mom", "longtable": "om",
                   "enumerate": "o", "itemize": "o", "description": "o"}.get(name, "")
        return sc_args(self.text, j, pattern)

    def _split_top(self, start: int, end: int, at_item: bool) -> list:
        """Cut start..end where it divides at the top level: at \\item (lists) or at \\\\ (table rows).  Braces and
        nested environments are stepped over, so a nested list or a {..} with \\\\ inside is not cut."""
        t, i, cuts = self.text, start, []
        while i < end:
            ch = t[i]
            if ch == "%":
                i = sc.skip_comment(t, i)
                continue
            if ch == "{":
                try:
                    i = sc.find_group_end(t, i)
                except sc.ScanError:
                    i += 1
                continue
            if ch == "$":
                close = (sc.find_display_end(t, i + 2, "$$") if t.startswith("$$", i)
                         else sc.find_inline_math_end(t, i + 1, "$"))
                i = close if 0 < close <= end else i + 1
                continue
            if ch != "\\":
                i += 1
                continue
            name, j = sc.read_control(t, i)
            if name == "begin":
                g = sc.read_group(t, j)
                if g:
                    _, after = sc.find_env_end(t, g.end, g.inner.strip(), g.inner.strip() in self.verbatim)
                    i = after if 0 < after <= end else g.end
                    continue
            if at_item and name == "item":
                cuts.append((i, j))
            elif not at_item and name in ("\\", "tabularnewline"):
                k = j
                if k < end and t[k] == "*":
                    k += 1
                o = sc.read_optional(t, k) if k < end else None
                cuts.append((i, o.end if o and o.end <= end else k))
                i = cuts[-1][1]
                continue
            i = j
        return cuts

    def _table(self, name: str, i: int, body: int, k: int, after: int) -> None:
        args, start = self._env_args(name, body)
        colspec = next((a.inner for a in reversed(args) if a is not None and a.kind == "{"), None)
        bounds, prev = [], start
        for a, z in self._split_top(start, k, at_item=False):
            bounds.append((prev, a))
            prev = z
        bounds.append((prev, k))
        rows = []
        for a, z in bounds:
            cells, c0 = [], a
            for c1 in self._cells(a, z) + [z]:
                cells.append((c0, c1))
                c0 = c1 + 1
            row = []
            for c0, c1 in cells:
                lat = self.text[c0:c1]
                row.append({"latex": lat.strip(), "text": rd.render(self.text, c0, c1, self.ctx),
                            **self.b.span(c0, max(c0 + 1, c1))})
            if any(c["text"] for c in row):
                rows.append(row)
        first = self._pages_in(body, k)
        self.b.add_block({"type": "table", "environment": name, "colspec": colspec, "section": self._section(),
                          "_page_start": first,
                          "rows": rows, "n_rows": len(rows), "n_cols": max((len(r) for r in rows), default=0),
                          "inline_math": self._math_in(start, k), **self.b.span(i, after),
                          "latex": self.text[i:after]})
        self._para_used = True
        self._next_paragraph()

    def _cells(self, start: int, end: int) -> list:
        """Offsets of the & that separate cells, at the top level of one row."""
        t, i, out = self.text, start, []
        while i < end:
            ch = t[i]
            if ch == "%":
                i = sc.skip_comment(t, i)
            elif ch == "\\":
                i += 2
            elif ch == "{":
                try:
                    i = sc.find_group_end(t, i)
                except sc.ScanError:
                    i += 1
            elif ch == "$":
                close = sc.find_inline_math_end(t, i + 1, "$")
                i = close if 0 < close <= end else i + 1
            elif ch == "&":
                out.append(i)
                i += 1
            else:
                i += 1
        return out

    def _list(self, name: str, i: int, body: int, k: int, after: int) -> None:
        args, start = self._env_args(name, body)
        cuts = self._split_top(start, k, at_item=True)
        items = []
        for n, (a, j) in enumerate(cuts):
            z = cuts[n + 1][0] if n + 1 < len(cuts) else k
            label = None
            try:
                o = sc.read_optional(self.text, j)
            except sc.ScanError:
                o = None
            if o and o.end <= z:
                label = self._field(o)
                j = o.end
            zz = z
            while zz > j and self.text[zz - 1] in " \t\n":
                zz -= 1
            items.append({"label": label, "latex": self.text[a:zz], "text": rd.render(self.text, j, zz, self.ctx),
                          **self.b.span(a, max(a + 1, zz))})
        if self.text[start:cuts[0][0] if cuts else k].strip() and not cuts:
            self.b.warn("LIST_WITHOUT_ITEMS", f"\\begin{{{name}}} holds text but no \\item", i)
        first = self._pages_in(body, k)
        self.b.add_block({"type": "list", "environment": name, "ordered": name == "enumerate",
                          "_page_start": first,
                          "section": self._section(), "items": items, "n_items": len(items),
                          "inline_math": self._math_in(start, k), **self.b.span(i, after),
                          "latex": self.text[i:after]})
        self._para_used = True
        self._next_paragraph()

    # ------------------------------------------------------------------ figures

    def _figure_macro(self, name: str, i: int, j: int, context: str, env: dict | None = None) -> int:
        spec = self.profile.macros[name]
        args, k = self._read_args(j, self.sigs[name].args, i)
        figs = self._make_figure(name, i, k, args, context, env)
        if spec.get("block") and context == "text":
            self._flush(i)
            block = self.b.add_block({"type": "figure", "figure": figs[0]["id"], "figures": [f["id"] for f in figs],
                                      "section": self._section(), **self.b.span(i, k), "latex": self.text[i:k]})
            for f in figs:
                f["block"] = block["id"]
        elif context == "text":
            self._open(i)
            self.ctx.figures[i] = [f["id"] for f in figs]
            self.para_figs.extend(figs)
        return k

    def _figures_in(self, start: int, end: int, context: str) -> list:
        """Figure macros inside a span (mathematics): recorded, never discarded, never cut out of the span."""
        out, i, t = [], start, self.text
        while i < end:
            if t[i] == "%":
                i = sc.skip_comment(t, i)
                continue
            if t[i] == "\\":
                name, j = sc.read_control(t, i)
                if self.profile.macros.get(name, {}).get("role") == "figure":
                    args, k = self._read_args(j, self.sigs[name].args, i)       # _read_args never raises
                    made = self._make_figure(name, i, k, args, context, None)
                    out.extend(made)
                    self.ctx.figures[i] = [f["id"] for f in made]
                    i = k
                    continue
                i = j
                continue
            i += 1
        return out

    def _figure_env(self, env: str, i: int, body: int, k: int, after: int) -> None:
        t = self.text
        rec = {"environment": env, "caption": None, "label": None}
        figs, x = [], body
        while x < k:
            if t[x] == "%":
                x = sc.skip_comment(t, x)
                continue
            if t[x] != "\\":
                x += 1
                continue
            name, j = sc.read_control(t, x)
            role = self.profile.macros.get(name, {}).get("role")
            if role == "figure":
                at = x
                args, x = self._read_args(j, self.sigs[name].args, at)
                figs.extend(self._make_figure(name, at, x, args, "figure-environment", rec))
            elif role == "caption":
                args, x2 = self._read_args(j, self.sigs[name].args, x)
                cap = args[-1] if args else None
                rec["caption"] = self._field(cap)
                x = x2
            elif role == "label":
                args, x = self._read_args(j, "m", x)
                rec["label"] = args[0].inner.strip() if args[0] else None
                x = max(x, j)
            else:
                x = j
        if not figs:
            self.b.warn("FIGURE_ENV_EMPTY", f"\\begin{{{env}}} holds no graphic EDIE recognises", i)
        number = None
        if rec["caption"]:
            m = FIG_NUMBER.search(rec["caption"]["latex"])
            number = m.group(1) if m else None
        for f in figs:
            f.update({"caption": rec["caption"], "label": rec["label"], "environment": env})
            if number and f["number"] is None:
                f["number"], f["number_origin"] = number, "caption"
        first = self._pages_in(body, k)
        block = self.b.add_block({"type": "figure", "figure": figs[0]["id"] if figs else None,
                                  "_page_start": first,
                                  "figures": [f["id"] for f in figs], "section": self._section(),
                                  **self.b.span(i, after), "latex": t[i:after]})
        for f in figs:
            f["block"] = block["id"]

    def _make_figure(self, name: str, i: int, k: int, args: list, context: str, env) -> list:
        """One record per image the macro shows: one for \\Figure, two for a side-by-side \\Figures pair.

        When the profile names each argument ("fields") for the pattern the book actually defines ("fields_for"),
        the file and number are read from those arguments.  Otherwise every argument is tried as an image name and
        no number is taken from an argument at all."""
        spec = self.profile.macros[name]
        pattern = self.sigs[name].args
        raw = [{"kind": "optional" if p == "o" else "mandatory", "latex": a.inner if a else None}
               for a, p in zip(args, pattern)]
        fields = spec.get("fields") if spec.get("fields_for") in (None, pattern) else None
        value = lambda n: args[n].inner.strip() if n < len(args) and args[n] is not None else None
        if fields:
            files = [value(n) for n, f in enumerate(fields) if f == "file"]
            numbers = [value(n) for n, f in enumerate(fields) if f == "number"]
            plan = [([f] if f else [], numbers[n] if n < len(numbers) else None) for n, f in enumerate(files)]
        else:
            plan = [([v for v in (value(n) for n in range(len(args))) if v], None)]
        out = []
        for candidates, number in plan:
            found = [h for h in (self._resolve_image(c) for c in candidates) if h]
            file = found[0] if found else None
            if len(set(found)) > 1:
                self.b.warn("FIGURE_AMBIGUOUS", f"\\{name}: more than one argument names an image "
                                                f"({', '.join(found)}); the first is used", i)
            if not file:
                self.b.warn("MISSING_IMAGE", f"\\{name}: no image file found for "
                                             f"{', '.join(repr(c) for c in candidates) or 'its arguments'} "
                                             f"under {', '.join(self.profile.image_dirs)}", i)
            number = number or None
            out.append({"id": self.b.next_id("fig"), "macro": name, "context": context, "in_math": context == "math",
                        "block_level": bool(spec.get("block")), "args": raw, "file": file, "file_exists": bool(file),
                        "candidates": [] if file else candidates,
                        "number": number, "number_origin": "argument" if number else None,
                        "caption": None, "label": None, "environment": None, "block": None, "group": None,
                        "section": self._section(), **self.b.span(i, k), "latex": self.text[i:k]})
        if len(out) > 1:
            for f in out:
                f["group"] = out[0]["id"]          # the images of one macro, side by side
        self.b.figures.extend(out)
        return out

    def _resolve_image(self, name: str) -> str | None:
        if not name or any(c in name for c in "\\{}$ \n") or name.startswith(("/", "..")):
            return None
        for d in self.profile.image_dirs:
            for ext in self.profile.image_exts:
                rel = Path(d) / (name + ext) if d != "." else Path(name + ext)
                p = self.root / rel
                if p.is_file():
                    return rel.as_posix()
        return None

    def _unreferenced_images(self) -> None:
        used = {f["file"] for f in self.b.figures if f["file"]}
        found = []
        for d in self.profile.image_dirs:
            if d == ".":
                continue
            folder = self.root / d
            if folder.is_dir():
                for p in sorted(folder.iterdir()):
                    if p.is_file() and p.suffix.lower() in {e for e in self.profile.image_exts if e}:
                        rel = (Path(d) / p.name).as_posix()
                        if rel not in used:
                            found.append(rel)
        self.b.metadata["unreferenced_images"] = found


def sc_args(text: str, j: int, pattern: str) -> tuple[list, int]:
    """Arguments in `pattern` from j, without warnings (for scans that only look)."""
    args = []
    for a in pattern:
        g = sc.read_optional(text, j) if a == "o" else sc.read_group(text, j)
        if g is not None:
            j = g.end
        args.append(g)
    return args, j


SAFE_TEXT = {"emph", "textit", "textbf", "textsc", "textrm", "textsl", "textup", "text", "mbox", "ldots", "dots",
             "@", " ", ",", "/", "-", "'", "`", '"', "^", "~", "c", "TeX", "LaTeX", "S", "P"}


def text_macros(defs: dict) -> dict:
    """Macros with no arguments whose definition is plain text (\\ie -> \\emph{i.e.}, \\TransNote -> Transcriber's
    Note): name -> body.  Only those are expanded when rendering readable text; anything that could typeset,
    measure or branch (a #, any other control word) is left alone."""
    out = {}
    for name, d in defs.items():
        if d.args != "" or "#" in d.body or not d.body.strip():
            continue
        words, i, ok = [], 0, True
        while i < len(d.body):
            if d.body[i] == "\\":
                w, i = sc.read_control(d.body, i, at_letter=True)
                if w not in SAFE_TEXT:
                    ok = False
                    break
            else:
                i += 1
        if ok:
            out[name] = d.body
    return out


def used_definitions(defs: dict, body: str) -> list:
    """The preamble definitions the body uses, so a later step can read \\eps as \\epsilon without TeX."""
    used: dict = {}
    i = 0
    while True:
        i = body.find("\\", i)
        if i < 0:
            break
        name, j = sc.read_control(body, i)
        if name in defs:
            used[name] = used.get(name, 0) + 1
        i = j
    # and what those definitions use in turn (\\intac is \\Int{all}{config.}): uses 0, reached through another
    via: dict = {}
    todo = list(used)
    while todo:
        n = todo.pop()
        k = 0
        while True:
            k = defs[n].body.find("\\", k)
            if k < 0:
                break
            m, k2 = sc.read_control(defs[n].body, k, at_letter=True)
            if m in defs and m not in used and m not in via and m != n:
                via[m] = n
                todo.append(m)
            k = k2
    return [{"name": n, "args": defs[n].args, "kind": defs[n].kind, "line": defs[n].line, "uses": used.get(n, 0),
             "via": via.get(n), "body": defs[n].body, "latex": defs[n].source} for n in sorted(set(used) | set(via))]


def normalize(text: str, start: int, end: int) -> str:
    """The expression with comments removed and runs of whitespace made one space.  Nothing else changes."""
    out, i = [], start
    while i < end:
        if text[i] == "\\" and i + 1 < end:
            out.append(text[i:i + 2])
            i += 2
            continue
        if text[i] == "%":
            i = min(sc.skip_comment(text, i), end)
            continue
        out.append(text[i])
        i += 1
    return " ".join("".join(out).split())


def features(body: str, env: str | None) -> list:
    """What kinds of mathematics an expression contains, by the macros it uses.  For search and reports only."""
    found, i = set(), 0
    while i < len(body):
        if body[i] == "\\":
            name, j = sc.read_control(body, i)
            if name in GREEK:
                found.add("greek")
            for key, names in FEATURES.items():
                if name in names:
                    found.add(key)
            if name == "begin":
                try:
                    g = sc.read_group(body, j)
                except sc.ScanError:
                    g = None
                if g and g.inner.strip() in ALIGN_ENVS:
                    found.add("alignment")
            i = j
            continue
        i += 1
    if DERIVATIVE.search(body):
        found.add("derivative")
    if env in ALIGN_ENVS or "&" in body.replace("\\&", ""):
        found.add("alignment")
    return sorted(found)


def parse_file(path, profile: str = "auto", image_root=None) -> dict:
    src = Source.load(path)
    prof = sg.Profile.auto(src.text) if profile == "auto" else sg.Profile.load(profile)
    return Parser(src, prof, image_root).parse()
