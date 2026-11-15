"""The edie-document/1 representation: format-independent, versioned, and traceable to the source.

Everything is a plain dict so the JSON export is the model itself (json.dumps, keys in a fixed order).  The
document never holds a timestamp, an absolute path or anything else that differs between two runs on the same
source: the same bytes in give the same bytes out (tests/test_reproducible.py).

    {
      "format":    "edie-document/1",
      "generator": {"name", "version", "profile": {"name", "sha256"}},
      "source":    {"filename", "sha256", "bytes", "encoding", "newline", "lines", "body_lines"},
      "metadata":  {...},                       every value says where it came from
      "signatures": [...],                      how many arguments each structural macro was read with, and why
      "environments": [...],                    environments the preamble defines that EDIE gives a role (0.2.1)
      "outline":   [{"id", "kind", "level", "number", "title", "children": [...]}],
      "blocks":    [...],                       in document order; see docs/EDIE.md §5 for each type
      "figures":   [...],                       every illustration reference, in or out of mathematics
      "warnings":  [{"code", "message", "line"}],
      "stats":     {...}
    }
"""

from __future__ import annotations

import json

from . import SCHEMA, __version__


class Builder:
    def __init__(self, source, profile):
        self.source = source
        self.profile = profile
        self.blocks: list = []
        self.figures: list = []
        self.warnings: list = []
        self.metadata: dict = {}
        self.signatures: list = []
        self._n = {"block": 0, "fig": 0}
        self.paragraph = 0
        self.body_lines = None                 # [first, last] line of the document body
        self.layout_only = 0                   # paragraphs holding only layout commands, not emitted
        self.page = None                       # the source's own page number, from its page markers
        self.page_markers = 0
        self.equation_tags = 0
        self.corrections: list = []
        self.definitions: list = []
        self.environments: list = []

    # ids are sequential in document order, so they are stable across runs and readable in a report
    def next_id(self, kind: str) -> str:
        self._n[kind] += 1
        return f"{'b' if kind == 'block' else 'fig'}-{self._n[kind]:05d}"

    def span(self, start: int, end: int) -> dict:
        return {"lines": self.source.lines(start, end), "offsets": [start, end]}

    def warn(self, code: str, message: str, offset: int | None = None) -> None:
        self.warnings.append({"code": code, "message": message,
                              "line": self.source.line(offset) if offset is not None else None})

    def add_block(self, block: dict) -> dict:
        first = block.pop("_page_start", self.page)
        block = {"id": self.next_id("block"), **block, "pages": [first, self.page] if (first or self.page) else None}
        self.blocks.append(block)
        return block

    def outline(self) -> list:
        nodes, roots = {}, []
        for b in self.blocks:
            if b["type"] != "heading":
                continue
            node = {"id": b["id"], "kind": b["kind"], "level": b["level"], "number": b["number"],
                    "title": b["title"]["text"] if b["title"] else None, "line": b["lines"][0], "children": []}
            nodes[b["id"]] = node
            (nodes[b["parent"]]["children"] if b["parent"] else roots).append(node)
        return roots

    def stats(self) -> dict:
        count = {}
        for b in self.blocks:
            key = b["type"] if b["type"] != "heading" else f"heading:{b['kind']}"
            count[key] = count.get(key, 0) + 1
        inline = sum(len(b.get("inline_math", [])) for b in self.blocks)
        inline += sum(len(f.get("inline_math", [])) for b in self.blocks if b["type"] == "heading"
                      for f in (b["title"], b["running_head"], b["alt_title"]) if f)
        return {"blocks": len(self.blocks), "by_type": dict(sorted(count.items())),
                "inline_math": inline, "display_math": count.get("math", 0),
                "figures": len(self.figures),
                "figures_in_math": sum(1 for f in self.figures if f["context"] == "math"),
                "images_missing": sum(1 for f in self.figures if not f["file_exists"]),
                "tables": count.get("table", 0),
                "table_rows": sum(b["n_rows"] for b in self.blocks if b["type"] == "table"),
                "lists": count.get("list", 0),
                "list_items": sum(b["n_items"] for b in self.blocks if b["type"] == "list"),
                "equation_tags": self.equation_tags, "corrections": len(self.corrections),
                "page_markers": self.page_markers,
                "layout_only_paragraphs": self.layout_only, "defined_math_environments": len(self.environments),
                "warnings": len(self.warnings)}

    def document(self) -> dict:
        s = self.source
        return {
            "format": SCHEMA,
            "generator": {"name": "edie", "version": __version__, "profile": self.profile.describe()},
            "source": {"filename": s.path.name, "sha256": s.sha256, "bytes": len(s.data), "encoding": s.encoding,
                       "newline": {"\n": "LF", "\r\n": "CRLF", "\r": "CR"}[s.newline], "lines": s.line_count,
                       "body_lines": self.body_lines},
            "metadata": self.metadata,
            "signatures": self.signatures,
            "environments": self.environments,
            "outline": self.outline(),
            "blocks": self.blocks,
            "figures": self.figures,
            "corrections": self.corrections,
            "definitions": self.definitions,
            "warnings": self.warnings,
            "stats": self.stats(),
        }


def dumps(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
