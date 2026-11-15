"""edie-document/1 -> readable Markdown.

Headings become #..######, text keeps its inline mathematics as $...$, display mathematics is printed as $$ ... $$
around the original LaTeX, every illustration is an image link, tables are pipe tables and lists are lists.  Each
block is preceded by an HTML comment naming its source lines, invisible when rendered, so any passage can be traced
back.

Markdown renderers (MathJax, KaTeX) know standard LaTeX only.  When the book defines its own mathematics macros
(#50992's \\eps, \\efrac, \\Tag), the Markdown opens with those definitions, copied exactly from the preamble, so the
equations render; a definition that does more than mathematics (\\ifthenelse, \\label, \\hyperref, ...) is left out
and named in a comment.  All of this is presentation only: the JSON keeps every expression as written.
"""

from __future__ import annotations

import posixpath
import re

MARK = re.compile(r"\[\[figure:(fig-\d+)\]\]")
# Top-level LaTeX environments can't sit inside $$ in Markdown renderers (MathJax, KaTeX).  For display only, each
# is shown as the inner environment those renderers accept; the expression's characters are not changed.
MD_ENVS = {"equation": None, "equation*": None, "displaymath": None, "math": None,
           "align": ("aligned", ""), "align*": ("aligned", ""), "flalign": ("aligned", ""), "flalign*": ("aligned", ""),
           "gather": ("gathered", ""), "gather*": ("gathered", ""), "multline": ("gathered", ""),
           "multline*": ("gathered", ""), "eqnarray": ("array", "{rcl}"), "eqnarray*": ("array", "{rcl}")}
# What a Markdown renderer can't do inside mathematics.  A definition using any of these is not copied.
NOT_MATH = {"ifthenelse", "equal", "hyperref", "phantomsection", "label", "ref", "pageref", "Pageref", "BookMark",
            "pdfbookmark", "fancyhead", "fancyhf", "markboth", "markright", "section", "subsection", "chapter", "par",
            "medskip", "smallskip", "bigskip", "noindent", "centering", "cleardoublepage", "clearpage", "newpage",
            "ensuremath", "def", "let", "makeatletter", "makeatother", "MakeUppercase", "MakeLowercase",
            "setlength", "addtolength", "spaceskip", "ignorespaces", "FlushRunningHeads", "InitRunningHeads",
            "SetRunningHeads", "SectTitle", "SubsectTitle", "null", "hfill", "vfill", "textsc", "scshape", "raggedright"}
CS = re.compile(r"\\([A-Za-z@]+)")
PAGE_BREAKS = re.compile(r"\\(?:displaybreak|allowdisplaybreaks)(?:\[[^\]]*\])?")
# Text set between the lines of a display: \intertext, and the left/right variants that Distributed Proofreaders'
# DPalign*/DPgather* code (common in Project Gutenberg LaTeX) defines.  Renderers know \text.
INTERTEXT = re.compile(r"\\(?:intertext|shortintertext|lintertext|rintertext)(?![A-Za-z@])\s*\{")


def _image(fig: dict, base: str) -> str:
    label = f"Figure {fig['number']}" if fig.get("number") else f"Illustration ({fig['id']})"
    if fig.get("caption") and fig["caption"].get("text"):
        label += f": {fig['caption']['text']}"
    if not fig["file"]:
        return f"**[missing image: {label}; {fig['latex']}]**"
    return f"![{label}]({posixpath.join(base, fig['file']) if base else fig['file']})"


def _heading(b: dict) -> str:
    title = b["title"]["text"] if b["title"] else ""
    number = b["number"] or ""
    if number and title:
        text = f"{number}. {title}" if not number.endswith(".") else f"{number} {title}"
    elif number and not title:
        text = f"{b['kind'].capitalize()} {number}"       # a run-in unit known only by its number (#50992's Theorem I.)
    else:
        text = title or f"({b['macro']})"
    return "#" * min(6, max(1, b["level"] + (1 if b["level"] == 0 else 0))) + " " + text.replace("\n", " ")


def _math_preamble(doc: dict) -> list:
    """The book's own mathematics macros that its mathematics uses, as $$ \\newcommand...$$ for Markdown renderers."""
    defs = {d["name"]: d for d in doc.get("definitions", [])}
    if not defs:
        return []
    used = set()
    for b in doc["blocks"]:
        maths = ([b["latex"]] if b["type"] == "math" else []) + [m["latex"] for m in b.get("inline_math", [])]
        for f in ("title", "running_head", "alt_title"):
            if b.get(f):
                maths += [m["latex"] for m in b[f].get("inline_math", [])]
        for m in maths:
            used |= set(CS.findall(m)) & defs.keys()
    # a macro used inside mathematics may be defined in terms of others: take those too
    todo = list(used)
    while todo:
        d = defs[todo.pop()]
        for n in set(CS.findall(d["body"])) & defs.keys():
            if n not in used:
                used.add(n)
                todo.append(n)
    used -= {"intertext", "shortintertext", "lintertext", "rintertext"}     # printed as \\text (INTERTEXT)
    tag_macros = {t["macro"] for b in doc["blocks"] for t in b.get("tags", [])} - {"tag"}
    keep, skipped = [], []
    for n in sorted(used):
        d = defs[n]
        if n in tag_macros:
            keep.append(f"\\newcommand{{\\{n}}}[1]{{\\tag*{{#1}}}}")     # an equation tag, whatever else it does
        elif d["kind"] == "newcommand" and not (set(CS.findall(d["body"])) & NOT_MATH):
            keep.append(" ".join(d["latex"].replace("%\n", "\n").split()))
        else:
            skipped.append(n)
    out = ["<!-- the book's own mathematics macros, copied from its preamble so Markdown renderers can show its "
           "equations; the JSON keeps every expression as written -->", "$$\n" + "\n".join(keep) + "\n$$"]
    if skipped:
        out.append(f"<!-- not copied (they do more than mathematics): {', '.join(chr(92) + n for n in skipped)} -->")
    return out if keep else out[2:]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(doc: dict, image_base: str = "", anchors: bool = True) -> str:
    figs = {f["id"]: f for f in doc["figures"]}
    defined = {e["name"]: e["based_on"] for e in doc.get("environments", []) if e.get("role") == "math"}
    out = []
    meta = doc["metadata"]
    title = (meta.get("title") or meta.get("latex_title") or {}).get("value")
    author = (meta.get("author") or meta.get("latex_author") or {}).get("value")
    out.append(f"<!-- edie {doc['generator']['version']} | {doc['format']} | source {doc['source']['filename']} "
               f"sha256:{doc['source']['sha256']} -->")
    if title:
        out.append(f"**{title}**" + (f"  \n*{author}*" if author else ""))
    out += _math_preamble(doc)
    for b in doc["blocks"]:
        if anchors:
            pages = f" p.{b['pages'][0]}" if b.get("pages") and b["pages"][0] else ""
            out.append(f"<!-- {b['id']} L{b['lines'][0]}-{b['lines'][1]}{pages} -->")
        t = b["type"]
        if t == "heading":
            out.append(_heading(b))
            if b["running_head"]:
                out.append(f"<!-- running head: {b['running_head']['text']} -->")
            if b.get("alt_title"):
                out.append(f"<!-- alternate title: {b['alt_title']['text']} -->")
        elif t == "text":
            text = b["text"]
            for m in b["inline_math"]:
                if m["figures"]:                    # a diagram inside mathematics: show it, keep the LaTeX
                    imgs = " ".join(_image(figs[f], image_base) for f in m["figures"])
                    text = text.replace(m["latex"], f"{imgs} `{m['latex']}`", 1)
            text = MARK.sub(lambda mm: _image(figs[mm.group(1)], image_base), text)
            out.append(text)
        elif t == "math":
            env = b["environment"]
            seen = set()
            while env not in MD_ENVS and env in defined and env not in seen:   # a book's own math environment:
                seen.add(env)                                                   # the standard one it is built on
                env = defined[env]
            body = b["body"].strip("\n")
            tags = b.get("tags") or []
            if env in MD_ENVS:
                wrap = MD_ENVS[env]
                if wrap:
                    if tags:                         # \tag is not allowed inside aligned/gathered: listed after
                        for tg in tags:
                            body = re.sub(r"\\" + re.escape(tg["macro"]) + r"\*?\s*\{" + re.escape(tg["latex"]) + r"\}",
                                          "", body)
                    body = f"\\begin{{{wrap[0]}}}{wrap[1]}\n{body}\n\\end{{{wrap[0]}}}"
                out.append(f"<!-- LaTeX environment {b['environment']}; the JSON keeps it as written -->")
            elif env:
                body = b["latex"]
            body = INTERTEXT.sub(r"\\text{", PAGE_BREAKS.sub("", body))   # print layout, not mathematics
            out.append("$$\n" + body.strip("\n") + "\n$$")
            if env in MD_ENVS and MD_ENVS[env] and tags:
                out.append("*Equations " + ", ".join(tg["text"] for tg in tags) + "*")
            for f in b["figures"]:
                out.append(_image(figs[f], image_base))
        elif t == "figure":
            for fid in b.get("figures") or ([b["figure"]] if b.get("figure") else []):
                out.append(_image(figs[fid], image_base))
        elif t == "table":
            rows = b["rows"] or [[]]
            n = max(1, b["n_cols"])
            pad = lambda r: [_cell(c["text"]) for c in r] + [""] * (n - len(r))
            lines = ["| " + " | ".join(pad(rows[0])) + " |", "|" + "---|" * n]
            lines += ["| " + " | ".join(pad(r)) + " |" for r in rows[1:]]
            out.append("\n".join(lines))
        elif t == "list":
            lines = []
            for k, it in enumerate(b["items"], 1):
                lead = f"{k}." if b["ordered"] else "-"
                label = f"**{it['label']['text']}** " if it.get("label") else ""
                lines.append(f"{lead} {label}" + it["text"].replace("\n", "\n   "))
            out.append("\n".join(lines))
        elif t == "verbatim":
            out.append("```text\n" + b["body"].strip("\n") + "\n```")
    return "\n\n".join(out) + "\n"
