"""edie-document/1 -> readable Markdown.

Headings become #..######, text keeps its inline mathematics as $...$, display mathematics is printed as $$ ... $$
around the original LaTeX (nothing rewritten), and every illustration is an image link.  Each block is preceded by
an HTML comment naming its source lines, invisible when rendered, so any passage can be traced back.
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
    text = f"{number}. {title}" if number and title else (number or title or f"({b['macro']})")
    return "#" * min(6, max(1, b["level"] + (1 if b["level"] == 0 else 0))) + " " + text.replace("\n", " ")


def to_markdown(doc: dict, image_base: str = "", anchors: bool = True) -> str:
    figs = {f["id"]: f for f in doc["figures"]}
    out = []
    meta = doc["metadata"]
    title = (meta.get("title") or meta.get("latex_title") or {}).get("value")
    author = (meta.get("author") or meta.get("latex_author") or {}).get("value")
    out.append(f"<!-- edie {doc['generator']['version']} | {doc['format']} | source {doc['source']['filename']} "
               f"sha256:{doc['source']['sha256']} -->")
    if title:
        out.append(f"**{title}**" + (f"  \n*{author}*" if author else ""))
    for b in doc["blocks"]:
        if anchors:
            out.append(f"<!-- {b['id']} L{b['lines'][0]}-{b['lines'][1]} -->")
        t = b["type"]
        if t == "heading":
            out.append(_heading(b))
            if b["running_head"]:
                out.append(f"<!-- running head: {b['running_head']['text']} -->")
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
            body = b["body"].strip("\n")
            if env in MD_ENVS:
                wrap = MD_ENVS[env]
                if wrap:
                    body = f"\\begin{{{wrap[0]}}}{wrap[1]}\n{body}\n\\end{{{wrap[0]}}}"
                out.append(f"<!-- LaTeX environment {env}; the JSON keeps it as written -->")
            elif env:
                body = b["latex"]
            out.append("$$\n" + body.strip("\n") + "\n$$")
            for f in b["figures"]:
                out.append(_image(figs[f], image_base))
        elif t == "figure":
            for fid in b.get("figures") or ([b["figure"]] if b.get("figure") else []):
                out.append(_image(figs[fid], image_base))
        elif t == "verbatim":
            out.append("```text\n" + b["body"].strip("\n") + "\n```")
    return "\n\n".join(out) + "\n"
