"""The parsing report: what EDIE found, how it read each structural macro, and everything it was unsure of.

Written for a person checking the extraction against the source, so every heading and every warning carries its
source line.  Like the document, it holds nothing that changes between two runs on the same input.
"""

from __future__ import annotations

from collections import Counter


def to_report(doc: dict) -> str:
    s, src, st = [], doc["source"], doc["stats"]
    s.append(f"# EDIE parsing report: {src['filename']}\n")
    prof = doc["generator"]["profile"]
    chain = " extends " + " > ".join(f"`{x}`" for x in prof.get("extends", [])) if prof.get("extends") else ""
    s.append(f"- schema `{doc['format']}`, edie {doc['generator']['version']}, profile `{prof['name']}`{chain} "
             f"({prof.get('selected', 'named')})")
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
    for k in ("inline_math", "display_math", "equation_tags", "figures", "figures_in_math", "images_missing",
              "tables", "table_rows", "lists", "list_items", "corrections", "page_markers",
              "layout_only_paragraphs", "warnings"):
        s.append(f"| {k.replace('_', ' ')} | {st.get(k, 0):,} |")
    pages = [b["pages"] for b in doc["blocks"] if b.get("pages")]
    if pages:
        s.append(f"\nPrinted pages, from the source's own page markers: {pages[0][0]} to {pages[-1][1]}.")

    s.append("\n## How each structural macro was read\n")
    s.append("Arguments: `o` optional `[..]`, `m` mandatory `{..}`.\n")
    s.append("| macro | role | arguments | from | note |\n|---|---|---|---|---|")
    used = Counter(b.get("macro") for b in doc["blocks"] if b["type"] == "heading")
    used.update(f["macro"] for f in doc["figures"] if f.get("group") in (None, f["id"]))
    used.update(d["name"] for d in doc.get("definitions", []) for _ in range(d["uses"])
                if d["name"] not in used)
    unused = 0
    for g in doc["signatures"]:
        if g["role"] in ("heading", "figure", "correction", "equation_tag", "page") and not used[g["name"]]:
            unused += g["origin"] != "base"
            continue
        if g["role"] in ("heading", "figure", "correction", "equation_tag", "page"):
            where = f"preamble line {g['line']}" if g["line"] else g["origin"]
            s.append(f"| `\\{g['name']}` ({used[g['name']]}×) | {g['role']} | `{g['args'] or '-'}` | {where} | "
                     f"{g['note']} |")

    if unused:
        s.append(f"\n{unused} macros the profile knows are not used in this document.")

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

    alts = [b for b in doc["blocks"] if b["type"] == "heading" and b.get("alt_title")]
    if alts:
        s.append("\n## Alternate titles (kept apart from titles and running heads)\n")
        s.append("| line | number | title | alternate title |\n|---:|---|---|---|")
        for b in alts:
            s.append(f"| {b['lines'][0]} | {b['number'] or ''} | {b['title']['text'] if b['title'] else ''} | "
                     f"{b['alt_title']['text']} |")

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

    tabs = [b for b in doc["blocks"] if b["type"] in ("table", "list")]
    if tabs:
        s.append("\n## Tables and lists\n")
        s.append("| line | kind | environment | size |\n|---:|---|---|---|")
        for b in tabs:
            size = f"{b['n_rows']} rows × {b['n_cols']} columns" if b["type"] == "table" else f"{b['n_items']} items"
            s.append(f"| {b['lines'][0]} | {b['type']} | {b['environment']} | {size} |")

    cor = doc.get("corrections") or []
    if cor:
        by = Counter(c["macro"] for c in cor)
        s.append("\n## Transcriber's corrections\n")
        s.append(f"{len(cor)} in all ({', '.join(f'`\\{k}` ×{v}' for k, v in sorted(by.items()))}); "
                 "the text uses the correction, the JSON keeps both. The first ten:\n")
        s.append("| line | macro | printed | corrected |\n|---:|---|---|---|")
        for c in cor[:10]:
            s.append(f"| {c['lines'][0]} | `\\{c['macro']}` | {c['original'] if c['original'] is not None else '(nothing)'} "
                     f"| {c['corrected']} |")

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
