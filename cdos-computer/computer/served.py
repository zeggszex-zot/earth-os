"""The build's pass over corpus.pf, row by row: the repairs (--fix-wordnet, --fix-wikidata, --fix-keys) and the last
steps (computer/pfrow.py), exactly as tools/build_shards.py serves each row, before deduplication.

It lives here so that everything that needs to see the master as the computer will (the build, and the acquisition
pipeline's dedupe and conflict index) runs the same code: a legacy Wikidata row whose value is a bare id (Q90) is
"Paris" to both.  tools/build_shards.py documents what each repair does and why.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from computer import pfrow
from computer.corpus import key_of
from computer.pfrow import SYNSET_ID

RELABEL = {"ANTONYM": "HYPERNYM", "HYPERNYM": "HYPONYM", "WORDNET_POINTER_!": "WN_ANTONYM",
           "WORDNET_POINTER_&": "WN_SIMILAR_TO"}
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




def lemma_table(src) -> dict[str, list[str]]:
    """Legacy synset id -> its words, in file order (the SYNSET rows)."""
    lemmas = defaultdict(list)
    for c in rows(src):
        if c[1] == "SYNSET" and SYNSET_ID.match(c[3]):
            lemmas[c[3]].append(c[0])
    return lemmas


def served_rows(src, fix_wordnet=False, fix_wikidata=False, fix_keys=False, report: Counter | None = None):
    """Every fact row of `src` as the build serves it, in file order, before deduplication:
    (subject, relation, kind, value, unit, start, until, source_type, source_id, confidence, qualifiers).
    `report` counts each repair, as the build reports them."""
    report = report if report is not None else Counter()
    qnames = wikidata_names(src) if fix_wikidata else {}
    lemmas = lemma_table(src) if fix_wordnet else {}
    for c in rows(src):
        subj, rel, kind, value, unit, vf, vu, stype, sid, conf, qual = c[:11]
        if rel == "SYNSET":
            report["synset rows kept out of the shards (used to resolve ids)"] += 1
            continue
        # The swapped labels and synset-id values are the legacy OEWN import's, whose rows carry a WN_x_nnnnnnnn
        # source_id; other sources, and OEWN rows from a new import (Q2), mean what they say.
        if fix_wordnet and stype == "OEWN" and SYNSET_ID.match(sid):
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
        # The last steps and the fact key are shared with the validator and computer/acquire (computer/pfrow.py)
        subj, kind, start, until, qual = pfrow.finish(subj, rel, kind, value, vf, vu, stype, sid, qual, fix_keys)
        yield subj, rel, kind, value, unit, start, until, stype, sid, conf, qual
