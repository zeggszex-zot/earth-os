"""Knowledge: the CDOS Corpus reader, ported from cdos-web/js/corpus.js.

Reads the same sharded data/ folder that tools/build_corpus.py writes, so the
computer and the web companion share one corpus.  It finds facts; it does not
reason about them.  Every fact it returns came out of the store.

On a Pi Zero 2 W: the subject list (~140k keys) is loaded once (~15 MB of
RAM); fact shards (~70 KB each, gzip) are read on demand and the most recent
ones are kept in a small LRU cache.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from . import qualifiers as Q
from .clock import current_year

PRONOUNS = {"it", "its", "it's", "they", "them", "their", "that", "this", "he", "she",
            "him", "her", "his", "one", "there"}
STOP = set("""the a an of is are was were does do did what which who whom tell me about please
can you how many much and or to in on for have has with there any some know describe explain
give show i my your be by at from as where when why would could should will its it's it that
this these those than then so if else not no yes computer hey ok okay also really very just get
got say said s we us our owl""".split())
META = set("""word words mean means meaning define definition synonym synonyms antonym antonyms
opposite another called term spell spelling fact facts thing things something""".split())

HINT_ALIASES = {
    "moon": ["satellite", "moon"], "moons": ["satellite", "moon"],
    "big": ["radius", "area", "mass", "size", "length", "height"],
    "large": ["radius", "area", "mass", "size"], "size": ["radius", "area", "size"],
    "heavy": ["mass"], "weigh": ["mass"], "weight": ["mass"],
    "far": ["distance"], "distance": ["distance"], "tall": ["height", "elevation"],
    "high": ["height", "elevation"], "people": ["population"], "live": ["population", "located"],
    "where": ["located", "continent", "country", "location"], "located": ["located"],
    "when": ["date", "occurred", "inception"],
    "born": ["born"], "die": ["died"], "died": ["died"], "start": ["founded", "inception"],
    "mean": ["definition"], "means": ["definition"], "meaning": ["definition"],
    "define": ["definition"], "definition": ["definition"],
    "opposite": ["antonym"], "opposites": ["antonym"], "similar": ["synonym"], "another": ["synonym"],
    "synonym": ["synonym", "similar"], "synonyms": ["synonym", "similar"], "antonyms": ["antonym"],
    "meanings": ["definition"], "sense": ["definition"], "senses": ["definition"],
    "money": ["currency"], "speak": ["language"], "spoken": ["language"],
    "long": ["length", "period", "day"], "year": ["period"], "day": ["day"],
    "hot": ["temperature", "boiling", "melting"], "cold": ["temperature", "melting"],
    "fast": ["speed"], "eat": ["diet"], "eats": ["diet"], "founded": ["founded", "inception"],
    "old": ["founded", "inception", "age"], "kind": ["is", "category", "class"],
    "type": ["is", "category", "class"],
}
# Open English WordNet's word-to-word relations (and the ones cdos-web names after them).
LEXICAL_RELS = {"DEFINITION", "SYNONYM", "WN_SIMILAR_TO", "ANTONYM", "WN_ANTONYM", "HYPERNYM", "HYPONYM",
                "DERIVATION", "EXAMPLE", "CATEGORY"}
SOURCE_RANK = {"MANUAL": 0, "DOCUMENT": 0, "TABLE": 1, "INFERENCE": 1, "WIKIDATA": 2,
               "DBPEDIA": 2, "GEONAMES": 2.5, "LLM": 3, "?": 3, "OEWN": 4}
# GEONAMES ranks just below Wikidata: where both give a figure (France's area), Wikidata's curated, dated one is used,
# and GeoNames fills what Wikidata lacks.
REL_RANK = {"IS_A": 0, "DEFINITION": 1, "SYNONYM": 2, "HYPERNYM": 3, "CATEGORY": 4}


def fnv1a(text: str) -> int:
    h = 0x811C9DC5
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def key_of(s: str) -> str:
    return re.sub(r"[\s\-]+", "_", s.strip()).upper()


def rel_words(rel: str) -> set[str]:
    out = set()
    for w in rel.lower().split("_"):
        if w in STOP or len(w) < 3:
            continue
        out.update((w, w + "s"))
        if w.endswith("s"):
            out.add(w[:-1])
    return out


def expand_hints(words) -> set[str]:
    out = set()
    for w in words:
        out.update(HINT_ALIASES.get(w, []))
        if w in STOP:
            continue
        out.add(w)
        if w.endswith("s"):
            out.add(w[:-1])
    return out


def stem(w: str) -> str:
    """A crude stem for matching words between texts: rivers -> river, lending -> lend."""
    for suf in ("ing", "ies", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def content_stems(text: str) -> set[str]:
    return {stem(w) for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in STOP}


def rank_by_overlap(texts: list[str], context: str) -> tuple[list[int], list[int]]:
    """Indexes of `texts` sorted by how many content words they share with `context` (best first,
    ties keep the corpus order, which for WordNet is most-common sense first).  Used to pick the
    sense of a word that fits a question ("bank, as in a river") or a hexagram's theme."""
    ctx = content_stems(context)
    scores = [len(content_stems(t) & ctx) for t in texts]
    return sorted(range(len(texts)), key=lambda i: (-scores[i], i)), scores


HASH_FIELDS = ("content_sha256", "version", "built_from")   # meta.json fields that describe or locate the content


def content_hash(base: str | Path, stats: dict | None = None) -> str:
    """sha256 over what the corpus holds: subjects.bin and every s/ and v/ shard, decompressed (so gzip's
    timestamps don't change it), in path order, plus meta.json without its own hash fields.  Changing one fact's
    value changes it; rebuilding the same facts doesn't.  `stats`, if given, is filled with each file's
    (size, mtime_ns) as it was read, so a later read can tell the file has changed since."""
    base = Path(base)
    h = hashlib.sha256()
    meta = json.loads((base / "meta.json").read_text())
    h.update(json.dumps({k: v for k, v in meta.items() if k not in HASH_FIELDS}, sort_keys=True).encode())
    files = [base / "subjects.bin"] + sorted(base.glob("s/*.bin")) + sorted(base.glob("v/*.bin"))
    for p in files:
        if stats is not None:
            st = p.stat()
            stats[str(p.relative_to(base))] = (st.st_size, st.st_mtime_ns)
        h.update(b"\0" + str(p.relative_to(base)).encode() + b"\0")
        h.update(_read_gz(p).encode("utf-8"))
    return h.hexdigest()


INTEGRITY_MODES = ("verify", "strict", "trust")
# What Corpus.integrity can be, and what it means for an answer's provenance:
#   verified   meta.json's stored hash was recomputed from the shards at load, and matched
#   computed   no stored hash: the version was computed from the shards at load (nothing to check it against)
#   trusted    integrity = "trust": the stored hash was used without reading the shards.  Never called verified.
#   mismatch   the stored hash doesn't match the shards: they were edited or replaced after the build
#   changed    verified at load, but a shard read since has a different size or modification time


def _read_gz(path: Path) -> str:
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data.decode("utf-8", errors="replace")


@dataclass
class Fact:
    subject: str
    relation: str
    kind: str
    value: str
    unit: str = ""
    start: int | None = None
    until: int | None = None
    source: str = "?"
    conf: float = 1.0
    qual: str = ""

    @classmethod
    def from_cols(cls, c: list[str]) -> "Fact":
        c = c + [""] * (10 - len(c))
        return cls(c[0], c[1], c[2], c[3], c[4], int(c[5]) if c[5] else None,
                   int(c[6]) if c[6] else None, c[7] or "?", float(c[8]) if c[8] else 1.0, c[9])

    @property
    def id(self) -> str:
        """A stable identifier for this fact: a hash of its content (not its position in a shard), so the
        same fact has the same id after a rebuild, and evidence can cite it.  16 hex digits."""
        row = "\t".join("" if x is None else str(x) for x in (self.subject, self.relation, self.kind, self.value,
                                                              self.unit, self.start, self.until, self.source, self.qual))
        return hashlib.blake2b(row.encode("utf-8"), digest_size=8).hexdigest()

    def holds(self, year: int, min_conf: float) -> bool:
        if self.conf < min_conf:
            return False
        if self.start is not None and year < self.start:
            return False
        if self.until is not None and year >= self.until:
            return False
        return True

    def sentence(self) -> str:
        rel = self.relation.lower().replace("_", " ")
        if self.kind in ("N", "D"):
            s = f"{self.subject} {rel}: {self.value}{' ' + self.unit if self.unit else ''}"
        elif rel in ("is a", "is an", "isa"):
            s = f"{self.subject} is {self.value}"
        else:
            s = f"{self.subject} {rel} {self.value}"
        extra = []
        # SENSE= and ENTITY= say which fact this is, not anything about it (computer/qualifiers.py): never said
        extra += [f"{k} {v}".lower() for k, v in Q.qualifiers(Q.shown(self.qual))]
        if self.start is not None or self.until is not None:
            extra.append(" ".join(x for x in (f"from {self.start}" if self.start is not None else "",
                                              f"until {self.until}" if self.until is not None else "") if x))
        return s + (f" ({'; '.join(extra)})" if extra else "") + "."


# Rows a model wrote ("speculative_model_output") are kept in the corpus, and never said as facts: house rule 4,
# a model is a voice, not a source.  Rule-planned answers, the lookup and the estimates all read through applicable().
NOT_KNOWLEDGE = {"LLM"}


# ---------------------------------------------------------------------- one name, several things (D4)
#
# A name can stand for several things: rows carry ENTITY=<id> (computer/qualifiers.py) to say which.  When the facts
# for one key name two or more entities, the reader answers about one of them and says which:
#   - the one the question names, by type ("Georgia the state") or by place ("Springfield, Illinois");
#   - otherwise the most prominent: sitelinks when a source gives them, then population, then country > region >
#     city, then the entity id (so the choice never wobbles).
# Rows without ENTITY (written before entities existed) join the most prominent reading only.  They can mix several
# things under one name, and can't be linked to a new source's entities without an id both share (Wikidata's GeoNames
# id, P1566, which the legacy import lacks), so the acquisition pipeline holds back a new source's rows for a name the
# master already holds without entities (computer/acquire/pipeline.py).  A key with one entity or none answers exactly
# as before.

TYPE_WORDS = {"country": "country", "nation": "country", "state": "region", "province": "region",
              "region": "region", "territory": "region", "county": "region", "city": "city", "town": "city",
              "village": "city"}
TYPE_RANK = {"country": 0, "region": 1, "city": 2}
# What a class name says a thing is, for classes written out (Wikidata's "sovereign state", "U.S. state", "commune
# of France"): the first rule a name matches.  Only to tell homonyms apart and describe them; never said as a fact.
KIND_RULES = (("country", ("country", "sovereign state")),
              ("region", ("u.s. state", "state of", "federated state", "province", "region", "oblast", "prefecture",
                          "canton", "territory")),
              ("city", ("city", "town", "commune", "municipality", "capital", "village", "metropolis")))


def kind_of(value: str) -> str:
    v = value.lower().replace("_", " ").strip()
    if v in TYPE_WORDS:
        return TYPE_WORDS[v]
    for kind, marks in KIND_RULES:
        if any(m in v for m in marks):
            return kind
    return ""


@dataclass
class Choice:
    """Which of a name's entities an answer is about, and the others it could have meant."""
    key: str
    entity: str
    by: str                     # "hint": the question named it; "prominence": the default reading
    described: str              # "Springfield, Missouri", "Georgia, the country"
    others: list                # descriptions of the others, most prominent first

    def note(self) -> str:
        """"(That's Paris in Île-de-France; there's also Paris in Texas.)" -- said only for the default reading."""
        if self.by == "hint" or not self.others:
            return ""
        named = self.others[:2]
        more = len(self.others) - len(named)
        also = (" and ".join(named) if not more else ", ".join(named) + f" and {more} more")
        return f"(That's {self.described}; there {'is' if len(self.others) == 1 else 'are'} also {also}.)"


def entity_of(f) -> str:
    return Q.get(f.qual, "ENTITY")


def _number(v: str) -> float:
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return 0.0


def _profile(name: str, facts: list, place_kind=None) -> dict:
    """What tells one entity from another: its type, its places, and how prominent it is.  A place that has ended
    ("Kingdom of England", until 1707) isn't one of its places now.  It's described by the region it's in when
    `place_kind` (a place name -> its kind, from the corpus) says which of its places is a region: "Springfield in
    Illinois", not "in Sangamon County"; else by a place that isn't a country, else by its last place."""
    year = current_year()
    kinds = [kind_of(f.value) for f in facts if f.relation == "IS_A"]
    kind = min((k for k in kinds if k in TYPE_RANK), key=TYPE_RANK.get, default="")
    places = list(dict.fromkeys(f.value for f in facts if f.relation == "LOCATED_IN"
                                and (f.until is None or f.until > year)))
    where = places[-1] if places else ""
    if place_kind and places:
        pk = {p: place_kind(p) for p in places}
        where = next((p for p in reversed(places) if pk[p] == "region"), "") or \
            next((p for p in reversed(places) if pk[p] != "country"), "") or places[-1]
    pops = [_number(f.value) for f in facts if f.relation == "POPULATION"]
    links = [_number(f.value) for f in facts if f.relation == "SITELINKS"]
    shown = name_of_key(name)
    if kind == "country":
        described = f"{shown}, the country"
    elif kind == "region":
        described = f"{shown}, a region of {with_article(places[-1])}" if places else f"{shown}, a region"
    elif places:
        described = f"{shown} in {where}"
    else:
        described = f"{shown} ({kind})" if kind else f"another {shown}"
    return {"kind": kind, "places": places, "population": max(pops) if pops else None,
            "sitelinks": max(links) if links else None, "described": described}


def _prominence(entity: str, p: dict) -> tuple:
    return (p["sitelinks"] is None, -(p["sitelinks"] or 0), p["population"] is None, -(p["population"] or 0),
            TYPE_RANK.get(p["kind"], len(TYPE_RANK)), entity)


def _hint_score(p: dict, hints: set) -> int:
    """How many of the question's other words pick this entity out: its type, and each of its places."""
    score = int(any(TYPE_WORDS.get(h) == p["kind"] for h in hints))
    for place in p["places"]:
        words = set(re.findall(r"[a-z0-9]+", place.lower()))
        if words and words <= hints:
            score += 1
    return score


def choose_entity(key: str, facts: list, hints=(), place_kind=None) -> tuple[list, Choice | None]:
    """(the facts to answer from, the choice made).  With one entity or none: (facts, None), unchanged.
    `place_kind` (optional): a place name -> "country", "region", "city" or "", to describe each by its region."""
    groups: dict = {}
    for f in facts:
        e = entity_of(f)
        if e:
            groups.setdefault(e, []).append(f)
    if len(groups) < 2:
        return facts, None
    prof = {e: _profile(key, fs, place_kind) for e, fs in groups.items()}
    ranked = sorted(groups, key=lambda e: _prominence(e, prof[e]))
    chosen, by = ranked[0], "prominence"
    hints = {h.lower() for h in hints}
    if hints:
        scores = {e: _hint_score(prof[e], hints) for e in ranked}
        best = max(scores.values())
        if best:
            chosen, by = next(e for e in ranked if scores[e] == best), "hint"
    out = [f for f in facts if entity_of(f) == chosen or (chosen == ranked[0] and not entity_of(f))]
    return out, Choice(key, chosen, by, prof[chosen]["described"],
                       [prof[e]["described"] for e in ranked if e != chosen])


THE_NAMES = {"Netherlands", "Philippines", "Bahamas", "Gambia", "Maldives", "Seychelles", "Comoros", "Vatican"}


def with_article(place: str) -> str:
    """"United States" -> "the United States"; most names take no article."""
    if place in THE_NAMES or place.startswith(("United ", "Republic of ")) or place.endswith(("Islands", "Republic")):
        return "the " + place
    return place


def name_of_key(key: str) -> str:
    """A subject key as words: SPRINGFIELD -> Springfield, NEW_YORK -> New York."""
    return " ".join(w.capitalize() for w in key.split("_"))


def applicable(facts: list, year: int, min_conf: float) -> list:
    """The facts that apply in `year`: one temporal policy for the lookup and the navigator alike.

      - a model's output (source LLM) is never a fact (NOT_KNOWLEDGE);
      - a fact whose period has ended, or not yet begun, doesn't apply (Fact.holds); nor does an undated copy of a
        value that has dates (the dated one says when it held);
      - of a number with a dated series (POPULATION since 1954, since 1956, ...), only the latest that has begun
        applies: the series is open-ended, each figure replacing the one before;
      - and an undated number for that relation doesn't apply alongside it: it doesn't say when it held (Earth's
        undated populations of 7 million and 50 million are prehistoric estimates, not today's figure).
    Undated numbers are kept when the relation has no dated figure that applies."""
    facts = [f for f in facts if f.source not in NOT_KNOWLEDGE]
    dated = {(f.relation, f.value) for f in facts
             if (f.until is not None and year >= f.until) or (f.start is not None and year < f.start)}
    out = [f for f in facts if f.holds(year, min_conf)
           and not (f.start is None and f.until is None and (f.relation, f.value) in dated)]
    latest: dict = {}
    for f in out:
        if f.kind == "N" and f.start is not None:
            if f.relation not in latest or f.start > latest[f.relation].start:
                latest[f.relation] = f
    return [f for f in out if not (f.kind == "N" and f.relation in latest and latest[f.relation] is not f)]


class Corpus:
    def __init__(self, base: str | Path, cache_shards: int = 24, integrity: str = "verify"):
        """integrity: "verify" (default) recomputes the content hash at load and reports a mismatch with every
        answer; "strict" is the same, and the computer then refuses to answer from a corpus that fails; "trust" skips
        the recomputation for a build you know is intact, and answers say "trusted", not "verified"."""
        if integrity not in INTEGRITY_MODES:
            raise ValueError(f"corpus integrity must be one of {', '.join(INTEGRITY_MODES)}, not {integrity!r}")
        self.base = Path(base)
        self.integrity_mode = integrity
        self.integrity = "none"            # verified | computed | trusted | mismatch | changed (see INTEGRITY_MODES)
        self.stored_hash = ""              # what meta.json claims
        self.actual_hash = ""              # what the shards hashed to at load (empty when trusted)
        self.changed_files: list[str] = []
        self._stats: dict = {}
        self.meta: dict | None = None
        self.subjects: set[str] = set()
        self.cache: OrderedDict[str, dict] = OrderedDict()
        self.cache_shards = cache_shards
        self._text: dict[str, str] = {}              # one shard's text, for reverse-lookup resolution
        self.last_subject = ""
        self.error = ""
        self._rel_vocab: set[str] | None = None
        self._version: str | None = None

    def load(self) -> "Corpus":
        try:
            self.meta = json.loads((self.base / "meta.json").read_text())
            self.subjects = set(k for k in _read_gz(self.base / "subjects.bin").split("\n") if k)
        except OSError as e:
            self.error = f"no corpus index at {self.base} ({e})"
            self.meta = None
            return self
        self._check_integrity()
        return self

    def _check_integrity(self) -> None:
        """At load: is this corpus what its build recorded?  Never writes anything to data/."""
        self.stored_hash = (self.meta or {}).get("content_sha256", "") or ""
        self.changed_files, self._stats = [], {}
        if self.integrity_mode == "trust" and self.stored_hash:
            self.integrity, self.actual_hash = "trusted", ""
            self._version = f"sha256:{self.stored_hash}"
            return
        try:
            self.actual_hash = content_hash(self.base, self._stats)
        except (OSError, ValueError) as e:
            self.integrity, self.actual_hash = "mismatch", ""
            self.error = f"corpus files unreadable while checking integrity ({e})"
            self._version = "sha256:unreadable"
            return
        self._version = f"sha256:{self.actual_hash}"       # always what the shards actually hold
        if not self.stored_hash:
            self.integrity = "computed"
        elif self.stored_hash == self.actual_hash:
            self.integrity = "verified"
        else:
            self.integrity = "mismatch"
            self.error = (f"corpus integrity: meta.json records sha256:{self.stored_hash[:12]}…, but the shards hash to "
                          f"sha256:{self.actual_hash[:12]}…: edited or replaced since the build")

    @property
    def integrity_ok(self) -> bool:
        return self.integrity in ("verified", "computed", "trusted")

    def integrity_record(self) -> dict:
        """For the result envelope: the check's outcome and what it compared."""
        return {"status": self.integrity, "mode": self.integrity_mode, "stored": self.stored_hash,
                "actual": self.actual_hash, "changed_files": list(self.changed_files)}

    @property
    def ready(self) -> bool:
        return bool(self.subjects)

    def status(self) -> str:
        if not self.meta:
            return self.error or "no corpus loaded"
        return f"{self.meta['facts']:,} facts, {self.meta['subjects']:,} subjects"

    @property
    def version(self) -> str:
        """The corpus version every answer cites: "sha256:<content hash>" of the shards as they were read at load,
        never just the hash meta.json claims (unless integrity = "trust", which every answer then says).  Whether
        the claim and the shards agree is `integrity`.  The data/ folder is never written."""
        if self._version is None:
            if not self.meta:
                self._version = "none"
            else:
                self._check_integrity()
        return self._version

    def verify_version(self) -> tuple[bool, str]:
        """Does the hash in meta.json still match the shards?  (False: rebuilt or edited since.)"""
        if not self.meta or not self.meta.get("content_sha256"):
            return True, "no stored hash; the version is computed from the shards"
        now = content_hash(self.base)
        return now == self.meta["content_sha256"], now

    def has(self, key: str) -> bool:
        return key in self.subjects

    def rel_vocab(self) -> set[str]:
        if self._rel_vocab is None:
            self._rel_vocab = set()
            for r in (self.meta or {}).get("relation_names", []):
                self._rel_vocab |= rel_words(r)
        return self._rel_vocab

    def _shard(self, kind: str, n: int) -> dict:
        sid = f"{kind}/{n:03d}"
        if sid in self.cache:
            self.cache.move_to_end(sid)
            return self.cache[sid]
        rows: dict[str, list] = {}
        path = self.base / f"{sid}.bin"
        self._shard_stat(sid)
        try:
            for line in _read_gz(path).split("\n"):
                if line:
                    c = line.split("\t")
                    rows.setdefault(c[0], []).append(c)
        except OSError:
            return {}
        self.cache[sid] = rows
        while len(self.cache) > self.cache_shards:
            self.cache.popitem(last=False)
        return rows

    def _shard_stat(self, sid: str) -> None:
        if self._stats:
            try:
                st = (self.base / f"{sid}.bin").stat()
                if self._stats.get(f"{sid}.bin") != (st.st_size, st.st_mtime_ns) and f"{sid}.bin" not in self.changed_files:
                    # Read after the load-time check, and it isn't the file that was hashed: the answer can't claim
                    # the verified version any more.
                    self.changed_files.append(f"{sid}.bin")
                    if self.integrity in ("verified", "computed"):
                        self.integrity = "changed"
            except OSError:
                pass

    def place_kind(self, name: str) -> str:
        """What kind of place a place name is, by its own IS_A rows: "region" when any says so (Georgia, the US
        state, is a region to Columbus, Georgia), else the highest of country > city, else ""."""
        cache = self.__dict__.setdefault("_place_kinds", {})
        if name not in cache:
            kinds = {kind_of(f.value) for f in self.facts_for(key_of(name)) if f.relation == "IS_A"}
            cache[name] = "region" if "region" in kinds else min((k for k in kinds if k in TYPE_RANK),
                                                                 key=TYPE_RANK.get, default="")
            if len(cache) > 4096:
                cache.clear()
        return cache[name]

    def facts_for(self, key: str) -> list[Fact]:
        if not self.meta or key not in self.subjects:
            return []
        return [Fact.from_cols(c) for c in self._shard("s", fnv1a(key) % self.meta["s_shards"]).get(key, [])]

    def reverse(self, value_key: str) -> list[dict]:
        if not self.meta:
            return []
        out = []
        for c in self._shard("v", fnv1a(value_key) % self.meta["v_shards"]).get(value_key, []):
            out.append({"subject": c[1], "relation": c[2], "start": int(c[3]) if c[3] else None,
                        "until": int(c[4]) if c[4] else None, "conf": float(c[5]) if c[5] else 1.0})
        return out

    def reverse_facts(self, value_key: str, year: int, min_conf: float, relations=None) -> list[Fact]:
        """The stored facts that point at value_key, as they are in the subject's own rows.  A v row carries no
        source or unit, so each is resolved to its forward row: evidence then cites a fact that exists (its real
        id and source), and the same temporal and source policy as a forward lookup applies (applicable())."""
        wanted: dict[int, set] = {}
        for r in self.reverse(value_key):
            if relations is None or r["relation"] in relations:
                wanted.setdefault(fnv1a(r["subject"]) % self.meta["s_shards"], set()).add((r["subject"], r["relation"]))
        out = []
        for n in sorted(wanted):                       # each subject shard read once, in a fixed order
            for subject, relation in sorted(wanted[n]):
                own = [f for f in map(Fact.from_cols, self._rows_of("s", n, subject, relation))
                       if f.kind in ("S", "T") and key_of(f.value) == value_key]
                out += applicable(own, year, min_conf)
        return out

    def _rows_of(self, kind: str, n: int, subject: str, relation: str) -> list[list]:
        """One subject's rows for one relation, from shard n.  From the cache when the shard is there; otherwise
        found in the shard's text without decoding (and caching) all of it: a reverse lookup can touch hundreds
        of subjects in as many shards, and decoding each in full is what made that slow."""
        sid = f"{kind}/{n:03d}"
        if sid in self.cache:
            return [c for c in self.cache[sid].get(subject, []) if c[1] == relation]
        text = self._text.get(sid)
        if text is None:
            self._shard_stat(sid)
            try:
                text = _read_gz(self.base / f"{sid}.bin")
            except OSError:
                return []
            self._text = {sid: text}                   # the shard being worked through; one held at a time
        head, out, i = f"\n{subject}\t{relation}\t", [], -1
        if text.startswith(head[1:]):
            i = 0
        else:
            i = text.find(head)
            i = -1 if i < 0 else i + 1
        while i >= 0:
            j = text.find("\n", i)
            out.append(text[i:j if j >= 0 else len(text)].split("\t"))
            k = text.find(head, i)
            i = -1 if k < 0 else k + 1
        return out

    def names_in(self, words: list[str]) -> list[dict]:
        found, used = [], [False] * len(words)
        for n in (4, 3, 2, 1):
            for i in range(0, len(words) - n + 1):
                if any(used[i:i + n]):
                    continue
                if n == 1 and (words[i] in STOP or words[i] in META):
                    continue
                cand = "_".join(words[i:i + n]).upper()
                for key in (cand, cand[:-1] if cand.endswith("S") else None, cand[:-2] if cand.endswith("ES") else None):
                    if key and self.has(key) and not any(f["key"] == key for f in found):
                        ws = words[i:i + n]
                        found.append({"key": key, "n": n, "pos": i, "words": ws})
                        if not (n > 1 and any(w in STOP for w in ws)):
                            for k in range(i, i + n):
                                used[k] = True
                        break
        return found

    def definition(self, word: str) -> str:
        """First dictionary definition of a word, or ''.  (Used by Wisdom.)"""
        for f in self.facts_for(key_of(word)):
            if f.relation == "DEFINITION":
                return re.sub(r';\s*"[^"]*"', "", f.value).strip()
        return ""

    def ask(self, question: str, year: int = 0, min_conf: float = 0.5, limit: int = 24) -> dict:
        notes = []
        words = [re.sub(r"'s$", "", w).replace("'", "")
                 for w in re.findall(r"[a-z0-9][a-z0-9_'\-]*", question.lower())]
        m = re.search(r"\b(?:in|during|of|around)\s+(1[0-9]{3}|20[0-9]{2})\b", question)
        if m and not year:
            year = int(m.group(1))
            notes.append(f"(as of {year}, from the question)")
        year = year or current_year()

        cands = self.names_in(words)
        pronoun = any(w in PRONOUNS and w not in ("one", "this", "that") for w in words)
        if self.last_subject and pronoun and not any(c["key"] == self.last_subject for c in cands):
            cands.insert(0, {"key": self.last_subject, "n": 1, "pos": 0, "words": [], "pronoun": True})

        scored = []
        for c in cands[:8]:
            own = set(c["words"])
            hints = expand_hints([w for w in words if w not in own])
            raw, choice = choose_entity(c["key"], self.facts_for(c["key"]), {w for w in words if w not in own},
                                        self.place_kind)
            facts = applicable(raw, year, min_conf)
            if not facts:
                continue
            wanted = [f for f in facts if rel_words(f.relation) & hints
                      or (f.kind in ("S", "T") and len(f.value) <= 40
                          and any(len(w) > 2 and w in hints for w in key_of(f.value).lower().split("_")))]
            curated = any(f.source != "OEWN" for f in facts)
            rel_word = c["n"] == 1 and len(c["words"]) == 1 and (
                c["words"][0] in self.rel_vocab() or c["words"][0] in HINT_ALIASES)
            score = (10 if wanted else 0) + (4 if curated else 0) + c["n"] * 2 - (6 if rel_word else 0) - c["pos"] * 0.01
            scored.append({**c, "facts": facts, "wanted": wanted, "curated": curated, "rel_word": rel_word, "score": score,
                           "choice": choice})
        named = any(not s.get("pronoun") and s["curated"] and not s["rel_word"] and s["wanted"] for s in scored)
        for s in scored:
            if s.get("pronoun"):
                s["score"] += -3 if named else 5
        scored.sort(key=lambda s: -s["score"])
        if scored and scored[0].get("pronoun"):
            notes.append(f'(taking "it" to mean {scored[0]["key"]})')
        if scored and scored[0].get("choice") and scored[0]["choice"].note():
            notes.append(scored[0]["choice"].note())             # which of the name's things (D4)

        rank = lambda f: SOURCE_RANK.get(f.source, 3) * 10 + REL_RANK.get(f.relation, 6)
        blocks = []
        for s in scored[:3]:
            wanted = sorted(s["wanted"], key=rank)
            rest = sorted([f for f in s["facts"] if f not in s["wanted"]], key=rank)
            room = limit if not blocks else -(-limit // 3)
            seen, chosen = set(), []
            for f in wanted + rest:
                k = f.sentence()
                if k not in seen:
                    seen.add(k)
                    chosen.append(f)
            chosen = chosen[:room]
            blocks.append({"subject": s["key"], "facts": chosen, "matched": bool(s["wanted"]),
                           "nwanted": min(len(s["wanted"]), len(chosen)), "total": len(s["facts"]),
                           "note": s["choice"].note() if s.get("choice") else ""})    # which of its things (D4)

        # "what orbits the sun": a named value plus a relation word.
        for v in cands[:6]:
            if blocks and blocks[0]["matched"]:
                break
            own = set(v["words"])
            hints = expand_hints([w for w in words if w not in own])
            if not hints:
                continue
            rels = {r["relation"] for r in self.reverse(v["key"]) if rel_words(r["relation"]) & hints}
            rows = self.reverse_facts(v["key"], year, min_conf, rels) if rels else []
            if not rows:
                continue
            rel = rows[0].relation
            names = sorted({f.subject for f in rows if f.relation == rel})
            blocks.insert(0, {"subject": v["key"], "reverse": rel, "names": names, "matched": True,
                              "conf": min(f.conf for f in rows)})
            break

        if not blocks:
            return {"found": False, "text": "", "facts": 0, "confidence": None, "notes": notes, "blocks": [], "year": year}
        lines, nfacts, confs = [], 0, []
        for b in blocks:
            if b.get("reverse"):
                lines.append(f"Subjects where {b['reverse']} {b['subject']}:")
                lines += [f"  - {n}" for n in b["names"][:40]]
                nfacts += len(b["names"])
                confs.append(b["conf"])
            else:
                lines += [f.sentence() for f in b["facts"]]
                nfacts += len(b["facts"])
                confs += [f.conf for f in b["facts"]]
        first = next((b for b in blocks if not b.get("reverse")), None)
        if first:
            self.last_subject = first["subject"]
        return {"found": True, "text": "\n".join(lines), "facts": nfacts,
                "confidence": min(confs) if confs else None, "notes": notes, "blocks": blocks, "year": year}


def as_context(ans: dict | None) -> str:
    if not ans or not ans.get("found"):
        return "corpus: no facts found for this question"
    conf = f", confidence {ans['confidence']:.2f}" if ans.get("confidence") is not None else ""
    out = [f"corpus ({ans['facts']} facts{conf}):"] + [f"  {n}" for n in ans["notes"]]
    out += [f"  {l}" for l in ans["text"].split("\n") if l.strip()]
    return "\n".join(out)
