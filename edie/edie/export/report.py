"""The parsing report: what EDIE found, how it read each structural macro, and everything it was unsure of.

Written for a person checking the extraction against the source, so every heading and every warning carries its
source line.  Like the document, it holds nothing that changes between two runs on the same input.
"""

from __future__ import annotations

from collections import Counter


def to_report(doc: dict) -> str:
    s, src, st = [], doc["source"], doc["stats"]
    s.append(f"# EDIE parsing report: {src['filename']}\n")
    s.append(f"- schema `{doc['format']}`, edie {doc['generator']['version']}, profile "
             f"`{doc['generator']['profile']['name']}`")
    s.append(f"- source sha256 `{src['sha256']}`, {src['bytes']:,} bytes, {src['lines']:,} lines, "
             f"{src['encoding']}, {src['newline']} line endings; body lines {src['body_lines']}")
    meta = {k: v for k, v in doc["metadata"].items() if isinstance(v, dict)}
    if meta:
        s.append("\n## Metadata\n")
        s.append("| field | value | from |\n|---|---|---|")
        for k, v in meta.items():
            s.append(f"| {k} | {v['value']} | {v['origin']}, line {v['line']} |")

    s.append("\n## Counts\n")
    s.append("| | |\n|---|---:|")
    for k, v in st["by_type"].items():
        s.append(f"| {k} | {v:,} |")
    for k in ("inline_math", "display_math", "figures", "figures_in_math", "images_missing",
              "layout_only_paragraphs", "warnings"):
        s.append(f"| {k.replace('_', ' ')} | {st[k]:,} |")

    s.append("\n## How each structural macro was read\n")
    s.append("Arguments: `o` optional `[..]`, `m` mandatory `{..}`.\n")
    s.append("| macro | role | arguments | from | note |\n|---|---|---|---|---|")
    used = Counter(b.get("macro") for b in doc["blocks"] if b["type"] == "heading")
    used.update(f["macro"] for f in doc["figures"])
    for g in doc["signatures"]:
        if g["role"] in ("heading", "figure") and (used[g["name"]] or g["origin"] != "base"):
            where = f"preamble line {g['line']}" if g["line"] else g["origin"]
            s.append(f"| `\\{g['name']}` ({used[g['name']]}×) | {g['role']} | `{g['args'] or '-'}` | {where} | "
                     f"{g['note']} |")

    s.append("\n## Outline\n")

    def walk(nodes, depth):
        for n in nodes:
            label = " ".join(x for x in (n["number"], n["title"]) if x) or "(untitled)"
            s.append(f"{'  ' * depth}- {n['kind']}: {label} (line {n['line']})")
            walk(n["children"], depth + 1)
    walk(doc["outline"], 0)

    heads = [b for b in doc["blocks"] if b["type"] == "heading" and b["running_head"]]
    if heads:
        s.append("\n## Running heads (kept apart from titles)\n")
        s.append("| line | number | title | running head |\n|---:|---|---|---|")
        for b in heads:
            s.append(f"| {b['lines'][0]} | {b['number'] or ''} | {b['title']['text'] if b['title'] else ''} | "
                     f"{b['running_head']['text']} |")

    s.append("\n## Illustrations\n")
    missing = [f for f in doc["figures"] if not f["file_exists"]]
    s.append(f"{len(doc['figures'])} references; {len(missing)} without an image file; "
             f"{sum(1 for f in doc['figures'] if f['in_math'])} inside mathematics.")
    if missing:
        s.append("\n| line | macro | tried |\n|---:|---|---|")
        for f in missing:
            s.append(f"| {f['lines'][0]} | `\\{f['macro']}` | {', '.join(f['candidates']) or '-'} |")
    unref = doc["metadata"].get("unreferenced_images") or []
    if unref:
        s.append(f"\nImage files no reference uses ({len(unref)}): " + ", ".join(f"`{u}`" for u in unref))

    s.append("\n## Warnings\n")
    if not doc["warnings"]:
        s.append("None.")
    else:
        by = Counter(w["code"] for w in doc["warnings"])
        s.append(", ".join(f"{k} ×{v}" for k, v in sorted(by.items())) + "\n")
        s.append("| line | code | message |\n|---:|---|---|")
        for w in doc["warnings"]:
            s.append(f"| {w['line'] if w['line'] is not None else ''} | {w['code']} | {w['message']} |")
    return "\n".join(s) + "\n"
