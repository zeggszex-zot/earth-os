"""Parsers: a downloaded file -> (record number, fields, why, raw) in source order.  `fields` is a list of the 11 PF
columns, or None with `why` when the record can't be read as one.  A parser keeps the source's own relation names;
the registry's map turns them into CDOS relations (and rejects the ones it doesn't list).  `why` starts with a short
code and a colon ("columns: 10 columns, not 11"); the report counts rejections by that code.
"""

from __future__ import annotations

import gzip
import io
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterator

from . import AcquireError

#: WordNet's order of parts of speech (by the synset id's last letter); 's' is an adjective satellite.
POS_ORDER = {"n": 0, "v": 1, "a": 2, "s": 2, "r": 3}
# OEWN has no sense frequencies; an entry's order of senses is WordNet's ranking where there is one ("big": "large"
# before "pregnant"), and otherwise its synsets in descending id order ("colour": the visual attribute 5th of 7).
# 3,963 entries with three or more senses run strictly downward and 200 upward, so downward is the default, not a
# ranking; those take ascending id order, the order the legacy import had.  Two senses can't be told apart (12,886
# downward, 2,835 upward: many real rankings among them) and keep the entry's order.
UNRANKED_MIN = 3
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
                    # Every earlier child of the parent has ended too, so all of them can go: memory stays bounded,
                    # and each step costs one child, not a search through all of them (remove() was quadratic).
                    del stack[-1][:]
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
    entries = []                                 # (lemma, part of speech, the entry's senses in its own order)
    for entry in _events(path, {"LexicalEntry"}, cap):
        lemma_el = entry.find("Lemma")
        lemma = _clean(lemma_el.get("writtenForm") if lemma_el is not None else "")
        lemma_of_entry[entry.get("id")] = lemma
        senses = []
        for sense in entry.findall("Sense"):
            sid, syn = sense.get("id"), sense.get("synset")
            lemma_of_sense[sid], synset_of_sense[sid] = lemma, syn
            senses_of_synset[syn].append(sid)
            senses.append(sid)
            for rel in sense.findall("SenseRelation"):
                sense_rels[sid].append((rel.get("relType"), rel.get("target")))
        ids = [synset_of_sense[x] or "" for x in senses]
        if len(ids) >= UNRANKED_MIN and all(x > y for x, y in zip(ids, ids[1:])):
            senses.sort(key=lambda x: synset_of_sense[x] or "")
        pos = lemma_el.get("partOfSpeech", "") if lemma_el is not None else ""
        if not pos and senses:                   # else the synset id's last letter
            pos = (synset_of_sense[senses[0]] or "").rsplit("-", 1)[-1]
        entries.append((lemma, pos, senses))

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
    synsets.sort(key=lambda x: POS_ORDER.get(x[0].rsplit("-", 1)[-1], len(POS_ORDER)))
    usable = {}                                  # synset -> (record number, words, text, relations that resolve)
    for sid, words, definition, examples, rels in synsets:
        n += 1
        if not words:
            yield n, None, "no-lemmas: a synset with no lemmas", sid
            continue
        good = []
        for rel, target in rels:
            if target in first:
                good.append((rel, target))
            else:
                yield n, None, f"dangling-synset-relation: {rel} points at {target}, a synset with no lemmas or none at all", \
                    f"{sid} {rel} -> {target}"
        text = "; ".join([definition] + [f'"{e}"' for e in examples]) if definition else ""
        usable[sid] = (n, words, text, good)

    def row(lemma, rel, kind, value, sid):
        return [lemma, rel, kind, value, "", "0", "0", "OEWN", sid, "1.0", f"SENSE={sid}"]

    done = set()

    def synset_rows(lemma, sid):
        if (lemma, sid) in done or lemma not in usable[sid][1]:
            return
        done.add((lemma, sid))
        rn, words, text, good = usable[sid]
        if text:
            yield rn, row(lemma, "definition", "T", text, sid), "", f"{sid} definition"
        for other in words:
            if other != lemma:
                yield rn, row(lemma, "synonym", "S", other, sid), "", f"{sid} synonym {other}"
        for rel, target in good:
            yield rn, row(lemma, rel, "S", first[target], sid), "", f"{sid} {rel} -> {target}"

    # A word's rows come in its meanings' order, which the reader follows when it lists a word's first meanings:
    # nouns, verbs, adjectives (with satellites), adverbs, as the legacy import had, and within each the order the
    # word's own entry gives its senses (WordNet's ranking; see UNRANKED_MIN), not the synsets' ids ("big" is "large"
    # before it is "pregnant").  Words come in the order the file first names them.
    by_word = defaultdict(list)
    for i, (lemma, pos, senses) in enumerate(entries):
        by_word[lemma.casefold()].append((POS_ORDER.get(pos, len(POS_ORDER)), i, lemma, senses))
    for group in by_word.values():
        for _, _, lemma, senses in sorted(group):
            for sense in senses:
                sid = synset_of_sense[sense]
                if sid not in usable:
                    continue
                yield from synset_rows(lemma, sid)
                rn = usable[sid][0]
                for rel, target in sense_rels.get(sense, []):
                    if target not in lemma_of_sense:
                        yield rn, None, f"dangling-sense-relation: {rel} points at {target}, a sense that doesn't exist", f"{sense} {rel} -> {target}"
                        continue
                    yield rn, row(lemma, rel, "S", lemma_of_sense[target], sid), "", f"{sense} {rel} -> {target}"
    for sid, (_, words, _, _) in usable.items():  # a member no entry's senses name: still its synset's rows
        for lemma in words:
            yield from synset_rows(lemma, sid)
    known = {x[0] for x in synsets}
    missing = sorted(s for s in senses_of_synset if s not in known)
    for syn in missing:
        n += 1
        yield n, None, f"missing-synset: senses point at {syn}, a synset that doesn't exist", syn


CONTINENTS = {"AF": "Africa", "AN": "Antarctica", "AS": "Asia", "EU": "Europe", "NA": "North America",
              "OC": "Oceania", "SA": "South America"}
CITY_COLUMNS = 19        # geonameid name asciiname alternatenames lat lon fclass fcode cc cc2 admin1..4 population
                         # elevation dem timezone modified (GeoNames readme, "geoname" table)


def _text_lines(path: Path, cap: int):
    """The lines of a text file, or of the one .txt inside a .zip, with the same decompression cap as the XML."""
    if zipfile.is_zipfile(path):
        zf = zipfile.ZipFile(path)
        names = [n for n in zf.namelist() if n.endswith(".txt") and "/" not in n]
        if len(names) != 1:
            zf.close()
            raise AcquireError(f"{path.name} should hold exactly one .txt file, not {names}")
        if zf.getinfo(names[0]).file_size > cap:
            zf.close()
            raise AcquireError(f"{path.name} expands past {cap:,} bytes; refused as a decompression bomb")
        raw = zf.open(names[0])
        closers = (raw, zf)
    else:
        raw = open(path, "rb")
        closers = (raw,)
    stream = io.BufferedReader(_Capped(raw, cap))
    try:
        for n, line in enumerate(io.TextIOWrapper(stream, encoding="utf-8", newline="\n"), 1):
            yield n, line.rstrip("\r\n")
    finally:
        stream.close()
        for c in closers:
            c.close()


def parse_geonames(files: dict, cap: int = MAX_XML_BYTES) -> Iterator[tuple[int, list | None, str, str]]:
    """GeoNames dump files: `countries` (countryInfo.txt), `admin1` (admin1CodesASCII.txt) and `cities`
    (citiesNNNNN.zip).  Countries first, then first-level regions, then cities; codes are resolved to names through the other files, and a
    code that resolves to nothing is rejected, never guessed.  Every row carries ENTITY=gn:<geonameid>, so two places
    with one name stay two facts (Q3).  GeoNames doesn't say when a population was counted, so none is dated."""
    missing = {"countries", "admin1", "cities"} - set(files)
    if missing:
        raise AcquireError(f"geonames needs the inputs countries, admin1 and cities; missing {sorted(missing)}")

    def row(subj, rel, kind, value, unit, sid, gid):
        return [subj, rel, kind, value, unit, "0", "0", "GEONAMES", sid, "1.0", f"ENTITY=gn:{gid}" if gid else ""]

    countries, gids, lines = {}, {}, []
    for n, line in _text_lines(Path(files["countries"]), cap):
        if not line.strip() or line.startswith("#"):
            continue
        c = line.split("\t")
        if len(c) < 18 or not c[0] or not c[4]:
            yield n, None, f"columns: a country line with {len(c)} columns or no code/name", line[:200]
            continue
        countries[c[0]], gids[c[0]] = c[4], c[16]
        lines.append((n, c))
    regions, region_lines = {}, []
    for n, line in _text_lines(Path(files["admin1"]), cap):
        c = line.split("\t")
        if len(c) >= 4 and c[0] and c[1]:
            regions[c[0]] = c[1]
            region_lines.append((n, c))
        elif line.strip():
            region_lines.append((n, None))

    record = 0
    for n, c in lines:
        record += 1
        iso, name, capital, area, pop, cont, cur_name, neighbours = c[0], c[4], c[5], c[6], c[7], c[8], c[11], c[17]
        gid = gids[iso]
        yield record, row(name, "is_a", "S", "country", "", iso, gid), "", f"{iso} country"
        if capital:
            yield record, row(name, "capital", "S", capital, "", iso, gid), "", f"{iso} capital"
        if _known(area):
            yield record, row(name, "area", "N", area, "KM2", iso, gid), "", f"{iso} area"
        if _known(pop):
            yield record, row(name, "population", "N", pop, "", iso, gid), "", f"{iso} population"
        if cont:
            if cont in CONTINENTS:
                yield record, row(name, "continent", "S", CONTINENTS[cont], "", iso, gid), "", f"{iso} continent"
            else:
                yield record, None, f"unknown-continent: {cont!r} isn't a GeoNames continent code", f"{iso} {cont}"
        if cur_name:
            yield record, row(name, "currency", "S", cur_name, "", iso, gid), "", f"{iso} currency"
        for nb in filter(None, neighbours.split(",")):
            if nb in countries:
                yield record, row(name, "shares_border_with", "S", countries[nb], "", iso, gid), "", f"{iso} -> {nb}"
            else:
                yield record, None, f"unknown-country: neighbour {nb!r} of {iso} isn't in countryInfo", f"{iso} {nb}"

    # First-level regions (states, provinces) are things of their own: "Georgia the state" is one (D4)
    for n, c in region_lines:
        record += 1
        if c is None:
            yield record, None, "columns: a region line without code, name and geonameid", f"admin1 line {n}"
            continue
        code, name, gid = c[0], c[1], c[3]
        yield record, row(name, "is_a", "S", "region", "", code, gid), "", f"{code} region"
        cc = code.split(".", 1)[0]
        if cc in countries:
            yield record, row(name, "located_in", "S", countries[cc], "", code, gid), "", f"{code} in {cc}"
        else:
            yield record, None, f"unknown-country: {cc!r} (region {code}) isn't in countryInfo", f"{code} {name}"

    for n, line in _text_lines(Path(files["cities"]), cap):
        if not line.strip():
            continue
        record += 1
        c = line.split("\t")
        if len(c) != CITY_COLUMNS:
            yield record, None, f"columns: {len(c)} columns, not {CITY_COLUMNS}", line[:200]
            continue
        gid, name, lat, lon, fclass, cc, adm1, pop, elev, tz = c[0], c[1], c[4], c[5], c[6], c[8], c[10], c[14], \
            c[15], c[17]
        if not gid or not name:
            yield record, None, "columns: no geonameid or name", line[:200]
            continue
        if fclass != "P":
            yield record, None, f"not-a-place: feature class {fclass!r}, not P (a populated place)", f"{gid} {name}"
            continue
        yield record, row(name, "is_a", "S", "city", "", gid, gid), "", f"{gid} city"
        if cc in countries:
            yield record, row(name, "located_in", "S", countries[cc], "", gid, gid), "", f"{gid} in {cc}"
        else:
            yield record, None, f"unknown-country: {cc!r} isn't in countryInfo", f"{gid} {name} {cc}"
        if adm1 and adm1 != "00":
            region = regions.get(f"{cc}.{adm1}")
            if region:
                yield record, row(name, "located_in", "S", region, "", gid, gid), "", f"{gid} in {cc}.{adm1}"
            else:
                yield record, None, f"unknown-admin1: {cc}.{adm1} isn't in admin1CodesASCII", f"{gid} {name}"
        if _known(pop):
            yield record, row(name, "population", "N", pop, "", gid, gid), "", f"{gid} population"
        if elev:
            yield record, row(name, "elevation", "N", elev, "M", gid, gid), "", f"{gid} elevation"
        if lat and lon:
            yield record, row(name, "located_at", "S", f"{lat},{lon}", "", gid, gid), "", f"{gid} position"
        if tz:
            yield record, row(name, "timezone", "S", tz, "", gid, gid), "", f"{gid} timezone"


def _known(x: str) -> bool:
    """GeoNames writes an unknown number as "" or 0; anything else goes on to be validated (a non-number is rejected
    there, with its reason, rather than dropped here)."""
    try:
        return bool(x) and float(x) != 0
    except ValueError:
        return True


PARSERS = {"pf": parse_pf, "gwa_xml": parse_gwa_xml, "geonames": parse_geonames}
