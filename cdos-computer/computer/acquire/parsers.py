"""Parsers: a downloaded file -> (record number, fields, why, raw) in source order.  `fields` is a list of the 11 PF
columns, or None with `why` when the record can't be read as one.  A parser keeps the source's own relation names;
the registry's map turns them into CDOS relations (and rejects the ones it doesn't list).  `why` starts with a short
code and a colon ("columns: 10 columns, not 11"); the report counts rejections by that code.
"""

from __future__ import annotations

import gzip
import io
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Iterator

from . import AcquireError

#: The most a compressed XML source may expand to before it is refused as a decompression bomb.
MAX_XML_BYTES = 500_000_000


def parse_pf(path: Path) -> Iterator[tuple[int, list | None, str, str]]:
    """A source that already ships PF rows (11 tab-separated columns), e.g. a curated table."""
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh, 1):
            try:
                line = raw.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError:
                yield n, None, "not-utf8: the line isn't UTF-8", raw[:500].decode("utf-8", "replace")
                continue
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) != 11:
                yield n, None, f"columns: {len(cols)} columns, not 11", line
                continue
            yield n, cols, "", line


class _Capped(io.RawIOBase):
    """A read-only stream that refuses to give more than `cap` bytes."""

    def __init__(self, inner, cap: int):
        self.inner, self.cap, self.read_so_far = inner, cap, 0

    def readable(self):
        return True

    def readinto(self, b):
        data = self.inner.read(len(b))
        self.read_so_far += len(data)
        if self.read_so_far > self.cap:
            raise AcquireError(f"the XML expands past {self.cap:,} bytes; refused as a decompression bomb")
        b[:len(data)] = data
        return len(data)


def _open_xml(path: Path, cap: int):
    with open(path, "rb") as probe:
        gz = probe.read(2) == b"\x1f\x8b"
    raw = gzip.open(path, "rb") if gz else open(path, "rb")
    return raw, io.BufferedReader(_Capped(raw, cap))


def _events(path: Path, tags: set[str], cap: int):
    """(element) for each finished element in `tags`, cleared after use so memory stays bounded.  Python's expat
    doesn't fetch external entities or the DTD and refuses entity-expansion bombs; a parse error fails the run."""
    raw, stream = _open_xml(path, cap)
    stack = []
    try:
        for event, elem in ET.iterparse(stream, events=("start", "end")):
            if event == "start":
                stack.append(elem)
                continue
            stack.pop()
            if elem.tag in tags:
                yield elem
                elem.clear()
                if stack:
                    stack[-1].remove(elem)          # detached, so the tree doesn't keep every finished element
    except ET.ParseError as e:
        raise AcquireError(f"{path.name} isn't well-formed XML: {e}") from None
    finally:
        stream.close()
        raw.close()


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def parse_gwa_xml(path: Path, cap: int = MAX_XML_BYTES) -> Iterator[tuple[int, list | None, str, str]]:
    """A Global WordNet Association LMF file (Open English WordNet).  One row per sense and relation, in the shape
    the legacy OEWN import had, so the two can be compared:

        <lemma> definition  T  <definition>; "<example>"…   for every lemma of the synset
        <lemma> synonym     S  <another lemma of the synset>
        <lemma> <synset relation type>  S  <first lemma of the target synset>
        <lemma> <sense relation type>   S  <lemma of the target sense>

    source_id and the SENSE= qualifier are the synset id.  Values are words, never synset ids.  Relation names are
    GWA's (hypernym, antonym, ...); the registry maps them.  Pass 1 reads the lexical entries, pass 2 the synsets."""
    lemma_of_entry, lemma_of_sense, synset_of_sense = {}, {}, {}
    senses_of_synset = defaultdict(list)
    sense_rels = defaultdict(list)
    for entry in _events(path, {"LexicalEntry"}, cap):
        lemma_el = entry.find("Lemma")
        lemma = _clean(lemma_el.get("writtenForm") if lemma_el is not None else "")
        lemma_of_entry[entry.get("id")] = lemma
        for sense in entry.findall("Sense"):
            sid, syn = sense.get("id"), sense.get("synset")
            lemma_of_sense[sid], synset_of_sense[sid] = lemma, syn
            senses_of_synset[syn].append(sid)
            for rel in sense.findall("SenseRelation"):
                sense_rels[sid].append((rel.get("relType"), rel.get("target")))

    def lemmas(syn: str, members: list[str] | None = None) -> list[str]:
        """The synset's words, in its own order (members) when it gives one, else the file's; without repeats."""
        order = [lemma_of_entry.get(m, "") for m in members] if members else \
                [lemma_of_sense[s] for s in senses_of_synset.get(syn, [])]
        return list(dict.fromkeys(w for w in order if w))

    first = {}                                   # synset -> its first word, for the targets of synset relations
    n = 0
    synsets = []
    for syn in _events(path, {"Synset"}, cap):
        sid = syn.get("id")
        members = (syn.get("members") or "").split() or None
        words = lemmas(sid, members)
        if words:
            first[sid] = words[0]
        definition = _clean(syn.findtext("Definition"))
        examples = [_clean(e.text) for e in syn.findall("Example") if _clean(e.text)]
        rels = [(r.get("relType"), r.get("target")) for r in syn.findall("SynsetRelation")]
        synsets.append((sid, words, definition, examples, rels))
    # Synset relations can point forward in the file, so rows are made once every synset's first word is known.
    for sid, words, definition, examples, rels in synsets:
        n += 1
        if not words:
            yield n, None, "no-lemmas: a synset with no lemmas", sid
            continue
        text = "; ".join([definition] + [f'"{e}"' for e in examples]) if definition else ""

        def row(lemma, rel, kind, value):
            return [lemma, rel, kind, value, "", "0", "0", "OEWN", sid, "1.0", f"SENSE={sid}"]

        good = []
        for rel, target in rels:
            if target in first:
                good.append((rel, target))
            else:
                yield n, None, f"dangling-synset-relation: {rel} points at {target}, a synset with no lemmas or none at all", \
                    f"{sid} {rel} -> {target}"
        for lemma in words:
            if text:
                yield n, row(lemma, "definition", "T", text), "", f"{sid} definition"
            for other in words:
                if other != lemma:
                    yield n, row(lemma, "synonym", "S", other), "", f"{sid} synonym {other}"
            for rel, target in good:
                yield n, row(lemma, rel, "S", first[target]), "", f"{sid} {rel} -> {target}"
        for sense in senses_of_synset.get(sid, []):
            lemma = lemma_of_sense[sense]
            for rel, target in sense_rels.get(sense, []):
                if target not in lemma_of_sense:
                    yield n, None, f"dangling-sense-relation: {rel} points at {target}, a sense that doesn't exist", f"{sense} {rel} -> {target}"
                    continue
                yield n, row(lemma, rel, "S", lemma_of_sense[target]), "", f"{sense} {rel} -> {target}"
    known = {x[0] for x in synsets}
    missing = sorted(s for s in senses_of_synset if s not in known)
    for syn in missing:
        n += 1
        yield n, None, f"missing-synset: senses point at {syn}, a synset that doesn't exist", syn


PARSERS = {"pf": parse_pf, "gwa_xml": parse_gwa_xml}
