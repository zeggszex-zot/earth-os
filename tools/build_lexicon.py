"""Build earth_os/assets/lexicon/english.words.gz from Princeton WordNet 3.0.

    python3 tools/build_lexicon.py <wordnet-3.0-dict-dir> earth_os/assets/lexicon/english.words.gz

The dict directory is the one holding index.noun, verb.exc and the rest
(NLTK ships it as corpora/wordnet). Deterministic: the same input gives the
same bytes, because the gzip timestamp is fixed. See docs/ESI_ARCHITECTURE.md.
"""
import gzip, re, sys
from pathlib import Path
src, out = Path(sys.argv[1]), Path(sys.argv[2])
WORD = re.compile(r"^[a-z]+$")
words = set()
for pos in ("noun", "verb", "adj", "adv"):
    for line in (src / f"index.{pos}").read_text(encoding="latin-1").splitlines():
        if line.startswith(" "):
            continue
        lemma = line.split(" ", 1)[0]
        if WORD.match(lemma):
            words.add(lemma)
exc = {}
for pos in ("noun", "verb", "adj", "adv"):
    for line in (src / f"{pos}.exc").read_text(encoding="latin-1").splitlines():
        parts = line.split()
        if len(parts) >= 2 and WORD.match(parts[0]) and WORD.match(parts[1]):
            exc.setdefault(parts[0], parts[1])
lines = ["# English words: single-word lemmas from Princeton WordNet 3.0,",
         "# then 'inflected base' pairs from its exception lists.",
         "# Built by tools/build_lexicon.py. Licence: WordNet-LICENSE.txt.",
         "[words]"] + sorted(words) + ["[irregular]"] + [f"{k} {v}" for k, v in sorted(exc.items())]
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb",
                                          mtime=0) as fh:
    fh.write(("\n".join(lines) + "\n").encode("utf-8"))
print(len(words), "words", len(exc), "irregular", out.stat().st_size, "bytes")
