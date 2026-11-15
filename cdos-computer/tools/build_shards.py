#!/usr/bin/env python3
"""Build the computer's data/ shards from a CDOS corpus file (corpus.pf: one fact per line, tab-separated).

    python3 tools/build_shards.py corpus.pf --out data
    python3 tools/build_shards.py corpus.pf --out data --fix-wordnet      # see below

corpus.pf columns: subject relation kind value unit valid_from valid_until source_type source_id confidence qualifiers
data/ is the format computer/corpus.py reads (see tests/mini_corpus.py): meta.json, subjects.bin, s/NNN.bin, v/NNN.bin.

What the build does on its own (the facts don't change, only how they're stored):
  - valid_from / valid_until of 0 mean "no date"
  - DEFINITION / EXAMPLE / DESCRIPTION values are text (kind T), not names
  - OEWN rows keep their sense (the synset in source_id) as a SENSE= qualifier, so the definition and the
    synonyms of one sense can be told from another's

--fix-wordnet repairs the WordNet import, which in the Phase 1.5 corpus has two labels swapped and points at
synset ids instead of words.  Checked by hand on DOG, OWL, FOOD, HAPPY, BIG, HOT:
    ANTONYM            -> HYPERNYM       DOG "ANTONYM" canine, domestic animal  (what it's a kind of)
    HYPERNYM           -> HYPONYM        DOG "HYPERNYM" puppy, pooch, mutt      (kinds of it)
    WORDNET_POINTER_!  -> WN_ANTONYM     HAPPY -> unhappy, BIG -> small         (the real opposites)
    WORDNET_POINTER_&  -> WN_SIMILAR_TO  HAPPY -> felicitous, fortunate
    other WORDNET_POINTER_x keep their label;  every WN_x_nnnnnnnn value becomes that synset's first word
    (rows whose synset has no words are dropped and counted).
    Only OEWN rows are repaired: another source's ANTONYM or HYPERNYM means what it says (Wikidata's "opposite of"
    is an ANTONYM), and relabelling it served 425 opposites as kinds (D1, docs/CORPUS_GROWTH_AUDIT.md §4).
--fix-wikidata resolves the Wikidata import's ids.  In the Phase 1.5 corpus 69,125 values are bare ids
("France CAPITAL Q90"), which the computer would read out as "Q90".  Every Wikidata row names its own subject's id in
source_id ("Paris ... Q90/P2046"), so the corpus already holds an id -> name table for everything it imported:
    values:    Q90 -> Paris                     (54,193 of the 69,125 resolve this way)
    units:     WD_Q613726 -> yottagram, and four more checked by hand (WD_UNITS); others stay hidden
    a value naming a *different* thing with the subject's name is kept ("Mars NAMED_AFTER Mars", the god);
    only a row whose value is its own subject's id is dropped as pointing at itself
    subjects:  Q937 -> Albert Einstein          (a subject stored as a bare id, when its name is known)
    the rest   point at things that were never imported (Q4, "death"): dropped and counted, since the computer
               could only ever say the id
--fix-keys stores every subject under the key the computer looks it up by (computer/corpus.py key_of: "Yemen" -> YEMEN).
Without it, the 72,800 subjects written in mixed or lower case (nearly all of the Wikidata and DBpedia rows) are in
the shards but can never be found: the computer asks for YEMEN and the shard holds "Yemen".  A thing that is in the corpus
twice ("FRANCE" curated, "France" from Wikidata) becomes one subject with both sets of facts.

corpus.pf itself is never written.  The report says how many rows each repair touched.  A guarded corpus.pf (one with
a corpus.pf.lock beside it: computer/corpus_guard.py) is read under the guard's shared lock, so a build never mixes
two versions of it; while its writer holds the lock, the build is refused at once.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from computer.corpus import content_hash, fnv1a, key_of  # noqa: E402
from computer.corpus_guard import CorpusGuard  # noqa: E402

TEXT_RELS = {"DEFINITION", "EXAMPLE", "DESCRIPTION"}
RELABEL = {"ANTONYM": "HYPERNYM", "HYPERNYM": "HYPONYM", "WORDNET_POINTER_!": "WN_ANTONYM",
           "WORDNET_POINTER_&": "WN_SIMILAR_TO"}
SYNSET_ID = re.compile(r"^WN_[a-z]_\d{8}$")
QID = re.compile(r"^Q\d+$")
QID_SOURCE = re.compile(r"^(Q\d+)/")
# Wikidata quantity units the import left as ids.  Only ones checked against Wikidata; the rest stay hidden (phrase.py).
WD_UNITS = {"WD_Q613726": "YOTTAGRAM", "WD_Q180892": "SOLAR_MASS", "WD_Q651336": "JUPITER_MASS",
            "WD_Q531": "LIGHT_YEAR", "WD_Q232291": "SQUARE_MILE"}
# The other pointers, named after the meaning of their WordNet symbol.  Unlike the four above, these labels were NOT
# checked by hand: the names say what the symbol means in WordNet, not what the rows were confirmed to hold.
POINTER_NAMES = {"-U": "WN_USAGE_MEMBER", ";U": "WN_USAGE_DOMAIN", "-C": "WN_TOPIC_MEMBER", ";C": "WN_TOPIC_DOMAIN",
                 "-R": "WN_REGION_MEMBER", ";R": "WN_REGION_DOMAIN", "\\": "WN_PERTAINYM", "^": "WN_ALSO_SEE",
                 "$": "WN_VERB_GROUP", "<": "WN_PARTICIPLE"}


def rows(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            c = line.rstrip("\n").split("\t")
            yield c + [""] * (11 - len(c))


def wikidata_names(src) -> dict[str, str]:
    """Wikidata id -> the corpus's name for it, from each row's source_id ("Q90/P2046" on a row about Paris).
    When an id appears under several subjects, a real name beats a bare id, then the most rows win."""
    seen = defaultdict(Counter)
    for c in rows(src):
        m = QID_SOURCE.match(c[8])
        if m:
            seen[m.group(1)][c[0]] += 1
    return {q: max(names, key=lambda n: (not QID.match(n), names[n])) for q, names in seen.items()
            if any(not QID.match(n) for n in names)}


def build(src, out, s_shards=256, v_shards=256, fix_wordnet=False, fix_wikidata=False, fix_keys=False, log=print):
    """Build data/ from src.  src is read three times, so a guarded corpus is read under its shared lock."""
    guard = CorpusGuard(src)
    if not guard.guarded:
        return _build(src, out, s_shards, v_shards, fix_wordnet, fix_wikidata, fix_keys, log)
    with guard.reading():
        return _build(src, out, s_shards, v_shards, fix_wordnet, fix_wikidata, fix_keys, log)


def _build(src, out, s_shards=256, v_shards=256, fix_wordnet=False, fix_wikidata=False, fix_keys=False, log=print):
    out = Path(out)
    report = Counter()
    qnames = wikidata_names(src) if fix_wikidata else {}
    lemmas = defaultdict(list)            # synset id -> its words, in file order
    if fix_wordnet:
        for c in rows(src):
            if c[1] == "SYNSET" and SYNSET_ID.match(c[3]):
                lemmas[c[3]].append(c[0])
    s_rows = [[] for _ in range(s_shards)]
    v_rows = [[] for _ in range(v_shards)]
    subjects, rels, seen = set(), set(), set()
    n = 0
    for c in rows(src):
        subj, rel, kind, value, unit, vf, vu, stype, sid, conf, qual = c[:11]
        if rel == "SYNSET":
            report["synset rows kept out of the shards (used to resolve ids)"] += 1
            continue
        if fix_wordnet and stype == "OEWN":     # the swapped labels are the OEWN import's; other sources mean what they say
            if rel in RELABEL:
                report[f"relabelled {rel} -> {RELABEL[rel]}"] += 1
                rel = RELABEL[rel]
            elif rel.startswith("WORDNET_POINTER_"):
                sym = rel[len("WORDNET_POINTER_"):]
                new = POINTER_NAMES.get(sym) or "WN_POINTER_" + re.sub(r"[^A-Z0-9]", "X", sym.upper())
                report[f"renamed {rel} -> {new} (by symbol, not checked)"] += 1
                rel = new
            if SYNSET_ID.match(value):
                words = lemmas.get(value)
                if not words:
                    report["dropped: synset with no words"] += 1
                    continue
                value = words[0].lower().replace("_", " ")      # a word, written like the synonyms are
                report["synset ids resolved to a word"] += 1
                if key_of(value) == subj:
                    report["dropped: points at itself"] += 1
                    continue
        if fix_wikidata:
            if QID.match(subj):
                if subj not in qnames:
                    report["dropped: Wikidata subject with no name in the corpus"] += 1
                    continue
                subj = qnames[subj]
                report["Wikidata subject ids resolved to a name"] += 1
            if QID.match(value) and kind == "S":
                if value not in qnames:
                    report["dropped: Wikidata value with no name in the corpus"] += 1
                    continue
                own = QID_SOURCE.match(sid)
                if own and own.group(1) == value:
                    report["dropped: points at itself"] += 1
                    continue
                if key_of(qnames[value]) == key_of(subj):
                    # a different thing with the same name: "Mars (the planet) NAMED_AFTER Mars (the god)" is true
                    report["kept: points at a different thing with the same name"] += 1
                value = qnames[value]
                report["Wikidata value ids resolved to a name"] += 1
            if unit in WD_UNITS:
                unit = WD_UNITS[unit]
                report["Wikidata unit ids named"] += 1
        if fix_keys and subj != key_of(subj):
            report["subjects stored under their lookup key"] += 1
            subj = key_of(subj)
        if rel in TEXT_RELS:
            kind = "T"
        start = int(vf) if vf not in ("", "0") else None
        until = int(vu) if vu not in ("", "0") else None
        if stype == "OEWN" and SYNSET_ID.match(sid) and not qual:
            qual = f"SENSE={sid}"
        sense = re.search(r"SENSE=\S+", qual or "")
        key = (subj, rel, key_of(value) if fix_keys and kind == "S" else value, unit, start, until,
               sense.group(0) if sense else "")      # one word, two senses: two facts
        if key in seen:
            report["dropped: duplicate after repair"] += 1
            continue
        seen.add(key)
        cell = lambda x: "" if x is None else str(x)
        s_rows[fnv1a(subj) % s_shards].append("\t".join(map(cell, (subj, rel, kind, value, unit, start, until, stype,
                                                                    conf or "1.0", qual))))
        if kind == "S":
            vk = key_of(value)
            v_rows[fnv1a(vk) % v_shards].append("\t".join(map(cell, (vk, subj, rel, start, until, conf))))
        subjects.add(subj)
        rels.add(rel)
        n += 1
    (out / "s").mkdir(parents=True, exist_ok=True)
    (out / "v").mkdir(parents=True, exist_ok=True)
    for i, r in enumerate(s_rows):
        (out / "s" / f"{i:03d}.bin").write_bytes(gzip.compress(("\n".join(r) + "\n").encode()))
    for i, r in enumerate(v_rows):
        (out / "v" / f"{i:03d}.bin").write_bytes(gzip.compress(("\n".join(r) + "\n").encode()))
    (out / "subjects.bin").write_bytes(gzip.compress(("\n".join(sorted(subjects)) + "\n").encode()))
    meta = {"facts": n, "subjects": len(subjects), "s_shards": s_shards, "v_shards": v_shards,
            "relation_names": sorted(rels), "built_from": str(src), "fix_wordnet": fix_wordnet,
            "fix_wikidata": fix_wikidata, "fix_keys": fix_keys}
    (out / "meta.json").write_text(json.dumps(meta))
    meta["content_sha256"] = content_hash(out)      # the corpus version every answer cites (computer/corpus.py)
    (out / "meta.json").write_text(json.dumps(meta))
    log(f"{n} facts, {len(subjects)} subjects, {len(rels)} relations -> {out}")
    for k, v in sorted(report.items()):
        log(f"  {k}: {v}")
    return meta, report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src")
    ap.add_argument("--out", default="data")
    ap.add_argument("--fix-wordnet", action="store_true", help="repair the swapped WordNet labels and synset-id values")
    ap.add_argument("--fix-wikidata", action="store_true", help="resolve bare Wikidata ids (Q90) to the corpus's names")
    ap.add_argument("--fix-keys", action="store_true", help="store subjects under the computer's lookup key (Yemen -> YEMEN)")
    ap.add_argument("--shards", type=int, default=256)
    a = ap.parse_args(argv)
    build(a.src, a.out, a.shards, a.shards, a.fix_wordnet, a.fix_wikidata, a.fix_keys)


if __name__ == "__main__":
    main()
