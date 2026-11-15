"""A source document, read once and never written: its bytes' sha256, its encoding, and a line index.

Offsets everywhere in EDIE are character offsets into `Source.text`; `Source.line(offset)` turns one into the
1-based line a person sees in an editor.
"""

from __future__ import annotations

import bisect
import hashlib
from dataclasses import dataclass, field
from pathlib import Path


class SourceError(Exception):
    """The source cannot be read.  Raised, never papered over with an empty document."""


@dataclass
class Source:
    path: Path
    data: bytes
    text: str
    encoding: str
    sha256: str
    newline: str
    _starts: list = field(default_factory=list, repr=False)

    @classmethod
    def load(cls, path) -> "Source":
        path = Path(path)
        try:
            with open(path, "rb") as fh:          # read-only, always
                data = fh.read()
        except OSError as e:
            raise SourceError(f"cannot read {path}: {e.strerror or e}") from e
        return cls.from_bytes(data, path)

    @classmethod
    def from_bytes(cls, data: bytes, path="<memory>") -> "Source":
        if data.startswith(b"\xef\xbb\xbf"):
            enc, body = "utf-8-sig", data
        else:
            enc, body = "utf-8", data
        try:
            text = body.decode(enc)
        except UnicodeDecodeError:
            # Older Project Gutenberg LaTeX files are ISO-8859-1 (\usepackage[latin1]{inputenc}).  Latin-1 decodes
            # every byte, so this cannot fail; the encoding is recorded so nobody mistakes it for UTF-8.
            enc, text = "latin-1", data.decode("latin-1")
        newline = "\r\n" if "\r\n" in text else ("\r" if "\r" in text else "\n")
        if newline != "\n":
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        src = cls(Path(path), data, text, enc, hashlib.sha256(data).hexdigest(), newline)
        src._starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
        return src

    @property
    def line_count(self) -> int:
        n = len(self._starts)
        return n - 1 if self.text.endswith("\n") else n

    def line(self, offset: int) -> int:
        """The 1-based line holding character `offset`."""
        return bisect.bisect_right(self._starts, max(0, offset)) - 1 + 1

    def lines(self, start: int, end: int) -> list:
        """[first line, last line] of the span start..end (end exclusive)."""
        return [self.line(start), self.line(max(start, end - 1))]
