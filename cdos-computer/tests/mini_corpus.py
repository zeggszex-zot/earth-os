"""A tiny CDOS corpus for tests, written in the same sharded format as cdos-web's data/.

    meta.json        {"facts", "subjects", "s_shards", "v_shards", "relation_names"}
    subjects.bin     gzip, one subject key per line
    s/NNN.bin        gzip, subject \t relation \t kind \t value \t unit \t start \t until \t source \t conf \t qual
    v/NNN.bin        gzip, value_key \t subject \t relation \t start \t until \t conf     (S/T values only)

`build(path)` writes it; nothing here is the real corpus.  The facts are rounded and
only meant to exercise the navigator (multi-hop, comparison, reverse, inheritance, time).
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from computer.corpus import fnv1a, key_of  # noqa: E402

# subject, relation, kind, value, unit, start, until, source, conf, qual
F = [
    ("SUN", "IS_A", "S", "STAR", "", None, None, "WIKIDATA", 1.0, ""),
    ("SUN", "MASS", "N", "1.989e30", "KG", None, None, "WIKIDATA", 1.0, ""),
    ("SUN", "RADIUS", "N", "696340", "KM", None, None, "WIKIDATA", 1.0, ""),
    ("STAR", "DEFINITION", "T", "a celestial body of hot gases that radiates energy", "", None, None, "OEWN", 1.0, ""),
    ("JUPITER", "IS_A", "S", "GAS_GIANT", "", None, None, "WIKIDATA", 1.0, ""),
    ("JUPITER", "IS_A", "S", "PLANET", "", None, None, "WIKIDATA", 0.95, ""),
    ("JUPITER", "ORBITS", "S", "SUN", "", None, None, "WIKIDATA", 1.0, ""),
    ("JUPITER", "MASS", "N", "1.898e27", "KG", None, None, "WIKIDATA", 1.0, ""),
    ("JUPITER", "RADIUS", "N", "69911", "KM", None, None, "WIKIDATA", 1.0, ""),
    ("JUPITER", "NUMBER_OF_MOONS", "N", "95", "", 2023, None, "WIKIDATA", 1.0, ""),
    ("JUPITER", "NUMBER_OF_MOONS", "N", "79", "", 2018, 2023, "WIKIDATA", 1.0, ""),
    ("JUPITER", "HAS_SATELLITE", "S", "IO", "", None, None, "WIKIDATA", 1.0, ""),
    ("JUPITER", "HAS_SATELLITE", "S", "EUROPA", "", None, None, "WIKIDATA", 1.0, ""),
    ("JUPITER", "HAS_SATELLITE", "S", "GANYMEDE", "", None, None, "WIKIDATA", 1.0, ""),
    ("JUPITER", "HAS_SATELLITE", "S", "CALLISTO", "", None, None, "WIKIDATA", 1.0, ""),
    ("SATURN", "IS_A", "S", "GAS_GIANT", "", None, None, "WIKIDATA", 1.0, ""),
    ("SATURN", "ORBITS", "S", "SUN", "", None, None, "WIKIDATA", 1.0, ""),
    ("SATURN", "MASS", "N", "5.683e26", "KG", None, None, "WIKIDATA", 1.0, ""),
    ("SATURN", "RADIUS", "N", "58232", "KM", None, None, "WIKIDATA", 1.0, ""),
    ("SATURN", "HAS_SATELLITE", "S", "TITAN", "", None, None, "WIKIDATA", 1.0, ""),
    ("SATURN", "HAS_SATELLITE", "S", "ENCELADUS", "", None, None, "WIKIDATA", 1.0, ""),
    ("EARTH", "IS_A", "S", "TERRESTRIAL_PLANET", "", None, None, "WIKIDATA", 1.0, ""),
    ("EARTH", "ORBITS", "S", "SUN", "", None, None, "WIKIDATA", 1.0, ""),
    ("EARTH", "MASS", "N", "5.972e24", "KG", None, None, "WIKIDATA", 1.0, ""),
    ("EARTH", "RADIUS", "N", "6371", "KM", None, None, "WIKIDATA", 1.0, ""),
    ("EARTH", "HAS_SATELLITE", "S", "MOON", "", None, None, "WIKIDATA", 1.0, ""),
    ("MOON", "IS_A", "S", "NATURAL_SATELLITE", "", None, None, "WIKIDATA", 1.0, ""),
    ("MOON", "ORBITS", "S", "EARTH", "", None, None, "WIKIDATA", 1.0, ""),
    ("MOON", "RADIUS", "N", "1737", "KM", None, None, "WIKIDATA", 1.0, ""),
    ("TITAN", "IS_A", "S", "NATURAL_SATELLITE", "", None, None, "WIKIDATA", 1.0, ""),
    ("TITAN", "ORBITS", "S", "SATURN", "", None, None, "WIKIDATA", 1.0, ""),
    ("TITAN", "RADIUS", "N", "2575", "KM", None, None, "WIKIDATA", 1.0, ""),
    ("GANYMEDE", "IS_A", "S", "NATURAL_SATELLITE", "", None, None, "WIKIDATA", 1.0, ""),
    ("GANYMEDE", "ORBITS", "S", "JUPITER", "", None, None, "WIKIDATA", 1.0, ""),
    ("GANYMEDE", "RADIUS", "N", "2634", "KM", None, None, "WIKIDATA", 1.0, ""),
    ("TERRESTRIAL_PLANET", "IS_A", "S", "PLANET", "", None, None, "WIKIDATA", 1.0, ""),
    ("GAS_GIANT", "IS_A", "S", "PLANET", "", None, None, "WIKIDATA", 1.0, ""),
    ("PLANET", "IS_A", "S", "CELESTIAL_BODY", "", None, None, "WIKIDATA", 1.0, ""),
    ("PLANET", "ORBITS", "S", "STAR", "", None, None, "WIKIDATA", 0.9, ""),
    ("FRANCE", "IS_A", "S", "COUNTRY", "", None, None, "WIKIDATA", 1.0, ""),
    ("FRANCE", "CAPITAL", "S", "PARIS", "", None, None, "WIKIDATA", 1.0, ""),
    ("FRANCE", "CONTINENT", "S", "EUROPE", "", None, None, "WIKIDATA", 1.0, ""),
    ("FRANCE", "CURRENCY", "S", "EURO", "", 2002, None, "WIKIDATA", 1.0, ""),
    ("FRANCE", "CURRENCY", "S", "FRENCH_FRANC", "", 1795, 2002, "WIKIDATA", 1.0, ""),
    ("FRANCE", "POPULATION", "N", "68000000", "", 2023, None, "WIKIDATA", 1.0, ""),
    ("FRANCE", "POPULATION", "N", "65000000", "", 2010, 2023, "WIKIDATA", 1.0, ""),
    ("FRANCE", "OFFICIAL_LANGUAGE", "S", "FRENCH", "", None, None, "WIKIDATA", 1.0, ""),
    ("FRANCE", "BORDERS", "S", "GERMANY", "", None, None, "WIKIDATA", 1.0, ""),
    ("FRANCE", "BORDERS", "S", "SPAIN", "", None, None, "WIKIDATA", 1.0, ""),
    ("GERMANY", "IS_A", "S", "COUNTRY", "", None, None, "WIKIDATA", 1.0, ""),
    ("GERMANY", "CAPITAL", "S", "BERLIN", "", None, None, "WIKIDATA", 1.0, ""),
    ("GERMANY", "CONTINENT", "S", "EUROPE", "", None, None, "WIKIDATA", 1.0, ""),
    ("GERMANY", "POPULATION", "N", "84000000", "", 2023, None, "WIKIDATA", 1.0, ""),
    ("GERMANY", "CURRENCY", "S", "EURO", "", 2002, None, "WIKIDATA", 1.0, ""),
    ("GERMANY", "BORDERS", "S", "FRANCE", "", None, None, "WIKIDATA", 1.0, ""),
    ("SPAIN", "IS_A", "S", "COUNTRY", "", None, None, "WIKIDATA", 1.0, ""),
    ("SPAIN", "CAPITAL", "S", "MADRID", "", None, None, "WIKIDATA", 1.0, ""),
    ("SPAIN", "POPULATION", "N", "48000000", "", 2023, None, "WIKIDATA", 1.0, ""),
    ("PARIS", "IS_A", "S", "CITY", "", None, None, "WIKIDATA", 1.0, ""),
    ("PARIS", "LOCATED_IN", "S", "FRANCE", "", None, None, "WIKIDATA", 1.0, ""),
    ("PARIS", "POPULATION", "N", "2100000", "", 2023, None, "WIKIDATA", 1.0, ""),
    ("BERLIN", "IS_A", "S", "CITY", "", None, None, "WIKIDATA", 1.0, ""),
    ("BERLIN", "LOCATED_IN", "S", "GERMANY", "", None, None, "WIKIDATA", 1.0, ""),
    ("BERLIN", "POPULATION", "N", "3800000", "", 2023, None, "WIKIDATA", 1.0, ""),
    ("MADRID", "IS_A", "S", "CITY", "", None, None, "WIKIDATA", 1.0, ""),
    ("MADRID", "LOCATED_IN", "S", "SPAIN", "", None, None, "WIKIDATA", 1.0, ""),
    ("EIFFEL_TOWER", "IS_A", "S", "TOWER", "", None, None, "WIKIDATA", 1.0, ""),
    ("EIFFEL_TOWER", "LOCATED_IN", "S", "PARIS", "", None, None, "WIKIDATA", 1.0, ""),
    ("EIFFEL_TOWER", "HEIGHT", "N", "330", "M", None, None, "WIKIDATA", 1.0, ""),
    ("EIFFEL_TOWER", "INCEPTION", "D", "1889-03-31", "", None, None, "WIKIDATA", 1.0, ""),
    ("BRANDENBURG_GATE", "IS_A", "S", "MONUMENT", "", None, None, "WIKIDATA", 1.0, ""),
    ("BRANDENBURG_GATE", "LOCATED_IN", "S", "BERLIN", "", None, None, "WIKIDATA", 1.0, ""),
    ("BRANDENBURG_GATE", "HEIGHT", "N", "26", "M", None, None, "WIKIDATA", 1.0, ""),
    ("BRANDENBURG_GATE", "INCEPTION", "D", "1791", "", None, None, "WIKIDATA", 1.0, ""),
    ("WHALE", "IS_A", "S", "MARINE_MAMMAL", "", None, None, "MANUAL", 1.0, ""),
    ("WHALE", "HAS_PART", "S", "BLOWHOLE", "", None, None, "MANUAL", 1.0, ""),
    ("WHALE", "DEFINITION", "T", "a very large marine mammal that breathes air through a blowhole", "", None, None, "OEWN", 1.0, ""),
    ("WHALE", "SURFACES_TO", "S", "BREATHE_AIR", "", None, None, "MANUAL", 0.9, ""),
    ("MARINE_MAMMAL", "IS_A", "S", "MAMMAL", "", None, None, "MANUAL", 1.0, ""),
    ("MARINE_MAMMAL", "HABITAT", "S", "OCEAN", "", None, None, "MANUAL", 1.0, ""),
    ("MAMMAL", "IS_A", "S", "ANIMAL", "", None, None, "MANUAL", 1.0, ""),
    ("MAMMAL", "BREATHES_WITH", "S", "LUNGS", "", None, None, "MANUAL", 1.0, ""),
    ("FISH", "IS_A", "S", "ANIMAL", "", None, None, "MANUAL", 1.0, ""),
    ("FISH", "BREATHES_WITH", "S", "GILLS", "", None, None, "MANUAL", 1.0, ""),
    ("SHARK", "IS_A", "S", "FISH", "", None, None, "MANUAL", 1.0, ""),
    ("BLUE_WHALE", "IS_A", "S", "WHALE", "", None, None, "MANUAL", 1.0, ""),
    ("BLUE_WHALE", "MASS", "N", "150000", "KG", None, None, "WIKIDATA", 1.0, ""),
    ("BLUE_WHALE", "LENGTH", "N", "30", "M", None, None, "WIKIDATA", 1.0, ""),
    ("BLUE_WHALE", "DIET", "S", "KRILL", "", None, None, "MANUAL", 1.0, ""),
    ("LUNGS", "DEFINITION", "T", "either of two saclike respiratory organs in the chest of vertebrates", "", None, None, "OEWN", 1.0, ""),
    ("LUNGS", "PART_OF", "S", "RESPIRATORY_SYSTEM", "", None, None, "MANUAL", 1.0, ""),
    ("BLOWHOLE", "DEFINITION", "T", "the nostril on top of the head of a whale", "", None, None, "OEWN", 1.0, ""),
    ("BLOWHOLE", "USED_FOR", "S", "BREATHING", "", None, None, "MANUAL", 1.0, ""),
    ("OWL", "IS_A", "S", "BIRD", "", None, None, "MANUAL", 1.0, ""),
    ("OWL", "DIET", "S", "MICE", "", None, None, "MANUAL", 1.0, ""),
    ("BIRD", "IS_A", "S", "ANIMAL", "", None, None, "MANUAL", 1.0, ""),
    ("BIRD", "BREATHES_WITH", "S", "LUNGS", "", None, None, "MANUAL", 1.0, ""),
    ("BIRD", "HAS_PART", "S", "FEATHERS", "", None, None, "MANUAL", 1.0, ""),
]

# Dictionary words, shaped like the Open English WordNet rows in the real corpus (source OEWN):
# several senses per word, usage examples after ';' in quotes, and word-to-word relations.
_W = lambda s, r, v, kind="S": (s, r, kind, v, "", None, None, "OEWN", 1.0, "")
LEXICAL = [
    _W("HAPPY", "DEFINITION", 'enjoying or showing or marked by joy or pleasure; "a happy smile"', "T"),
    _W("HAPPY", "DEFINITION", 'marked by good fortune; "a happy outcome"', "T"),
    _W("HAPPY", "SYNONYM", "GLAD"),
    _W("HAPPY", "SYNONYM", "JOYFUL"),             # JOYFUL has no entry of its own
    _W("HAPPY", "WN_SIMILAR_TO", "CHEERFUL"),
    _W("HAPPY", "WN_ANTONYM", "UNHAPPY"),
    _W("HAPPY", "DERIVATION", "HAPPINESS"),
    _W("HAPPY", "EXAMPLE", "a happy smile", "T"),
    _W("GLAD", "DEFINITION", "showing or causing joy and pleasure", "T"),
    _W("GLAD", "SYNONYM", "HAPPY"),
    _W("CHEERFUL", "DEFINITION", "being full of or promoting cheer; having or showing good spirits", "T"),
    _W("UNHAPPY", "DEFINITION", "experiencing or marked by or causing sadness or sorrow or discontent", "T"),
    _W("UNHAPPY", "SYNONYM", "SAD"),
    _W("UNHAPPY", "WN_ANTONYM", "HAPPY"),
    _W("SAD", "DEFINITION", "experiencing or showing sorrow or unhappiness", "T"),
    _W("SAD", "SYNONYM", "UNHAPPY"),
    _W("SAD", "WN_ANTONYM", "GLAD"),
    _W("HAPPINESS", "DEFINITION", "state of well-being characterized by emotions ranging from contentment to intense joy", "T"),
    _W("HAPPINESS", "DERIVATION", "HAPPY"),
    _W("BANK", "DEFINITION", 'sloping land (especially the slope beside a body of water); "they pulled the canoe up on '
                             'the bank"; "he sat on the bank of the river and watched the currents"', "T"),
    _W("BANK", "DEFINITION", 'a financial institution that accepts deposits and channels the money into lending '
                             'activities; "he cashed a check at the bank"', "T"),
    _W("BANK", "HYPERNYM", "SLOPE"),
    _W("BANK", "HYPERNYM", "FINANCIAL_INSTITUTION"),
    _W("DOG", "DEFINITION", "a member of the genus Canis that has been domesticated by man since prehistoric times", "T"),
    _W("DOG", "HYPERNYM", "CANINE"),
    _W("DOG", "HYPERNYM", "DOMESTIC_ANIMAL"),
    _W("DOG", "HYPONYM", "PUPPY"),
    _W("DOG", "HYPONYM", "POODLE"),
    _W("DOG", "HYPONYM", "DALMATIAN"),
    _W("DOG", "SYNONYM", "DOMESTIC_DOG"),
    _W("CANINE", "DEFINITION", "any of various fissiped mammals with nonretractile claws and typically long muzzles", "T"),
    _W("CANINE", "HYPERNYM", "CARNIVORE"),
    _W("CARNIVORE", "DEFINITION", "a terrestrial or aquatic flesh-eating mammal", "T"),
    _W("CARNIVORE", "HYPERNYM", "MAMMAL"),
    _W("PUPPY", "DEFINITION", "a young dog", "T"),
    _W("PUPPY", "HYPERNYM", "DOG"),
    _W("POODLE", "DEFINITION", "an intelligent dog with a heavy curly solid-colored coat", "T"),
    _W("POODLE", "HYPERNYM", "DOG"),
    _W("BIG", "DEFINITION", "above average in size or number or quantity or magnitude or extent", "T"),
    _W("BIG", "SYNONYM", "LARGE"),
    _W("BIG", "WN_ANTONYM", "SMALL"),
    _W("LARGE", "DEFINITION", "above average in size or number or quantity or magnitude or extent", "T"),
    _W("LARGE", "SYNONYM", "BIG"),
    _W("LARGE", "WN_ANTONYM", "SMALL"),
    _W("SMALL", "DEFINITION", "limited or below average in number or quantity or magnitude or extent", "T"),
    _W("SMALL", "SYNONYM", "LITTLE"),
    _W("SMALL", "WN_ANTONYM", "BIG"),
    _W("SMALL", "WN_ANTONYM", "LARGE"),
    _W("COLOR", "DEFINITION", "a visual attribute of things that results from the light they emit or transmit or reflect", "T"),
    _W("COLOR", "HYPONYM", "RED"),
    _W("COLOR", "HYPONYM", "BLUE"),
    _W("RED", "DEFINITION", "the color of blood or of a ruby", "T"),
    _W("RED", "HYPERNYM", "COLOR"),
    _W("BLUE", "DEFINITION", "the color of the clear sky in the daytime", "T"),
    _W("BLUE", "HYPERNYM", "COLOR"),
    _W("RUN", "DEFINITION", "move fast by using one's feet, with one foot off the ground at any given time", "T"),
    _W("RUN", "HYPERNYM", "TRAVEL_RAPIDLY"),
    _W("RUN", "DERIVATION", "RUNNER"),
]

# The dictionary's entries for the words relations are named with (the real corpus has these from OEWN).
# The relation lexicon (computer/lexicon.py) learns "food" -> DIET from them; nothing here names a relation directly.
RELATION_WORDS = [
    _W("DIET", "DEFINITION", "a prescribed selection of foods", "T"),
    _W("DIET", "DEFINITION", "the usual food and drink consumed by an organism (person or animal)", "T"),
    _W("FOOD", "DEFINITION", "any substance that can be metabolized by an animal to give energy and build tissue", "T"),
    _W("FOOD", "SYNONYM", "NUTRIENT"),
    _W("NOURISHMENT", "DEFINITION", "a source of materials to nourish the body", "T"),
    _W("NOURISHMENT", "SYNONYM", "SUSTENANCE"),
    _W("HABITAT", "DEFINITION", "the type of environment in which an organism or group normally lives or occurs", "T"),
    _W("HABITAT", "SYNONYM", "HOME_GROUND"),
    _W("CAPITAL", "DEFINITION", "a seat of government", "T"),
    _W("CAPITAL", "DEFINITION", "wealth in the form of money or property owned by a person or business", "T"),
    _W("CURRENCY", "DEFINITION", "the metal or paper medium of exchange that is presently used", "T"),
    _W("LANGUAGE", "DEFINITION", "a systematic means of communicating by the use of sounds or conventional symbols", "T"),
    _W("LANGUAGE", "SYNONYM", "TONGUE"),
    _W("HEIGHT", "DEFINITION", "the vertical dimension of extension; distance from the base of something to the top", "T"),
    _W("HEIGHT", "SYNONYM", "TALLNESS"),
    _W("POPULATION", "DEFINITION", "the people who inhabit a territory or state", "T"),
    _W("ANIMAL", "HYPERNYM", "ORGANISM"),
    _W("COUNTRY", "DEFINITION", "the territory occupied by a nation", "T"),
    _W("COUNTRY", "SYNONYM", "STATE"),
]
F = F + LEXICAL + RELATION_WORDS

# Devices with electrical figures, for the analysis engine (computer/analysis.py).  Rounded test values, not
# specifications.  Kept out of F so the navigator's, lexicon's and controller's tests see the corpus they
# were written against: build(path, F + DEVICES) to include them.
_D = lambda s, r, k, v, u="", q="", conf=1.0: (s, r, k, v, u, None, None, "MANUAL", conf, q)
DEVICES = [
    _D("RASPBERRY_PI_ZERO_2_W", "IS_A", "S", "SINGLE_BOARD_COMPUTER"),
    _D("RASPBERRY_PI_ZERO_2_W", "POWER_DRAW", "N", "2.5", "W", conf=0.9),
    _D("RASPBERRY_PI_5", "IS_A", "S", "SINGLE_BOARD_COMPUTER"),
    _D("RASPBERRY_PI_5", "POWER_DRAW", "N", "12", "W", conf=0.9),
    _D("RASPBERRY_PI_4", "IS_A", "S", "SINGLE_BOARD_COMPUTER"),
    _D("RASPBERRY_PI_4", "POWER_DRAW", "N", "3", "W", "STATE=IDLE", 0.9),        # two figures: the engine must ask
    _D("RASPBERRY_PI_4", "POWER_DRAW", "N", "6.4", "W", "STATE=LOAD", 0.9),
    _D("OWL_BATTERY", "IS_A", "S", "BATTERY"),
    _D("OWL_BATTERY", "BATTERY_CAPACITY", "N", "10000", "MAH"),
    _D("OWL_BATTERY", "NOMINAL_VOLTAGE", "N", "3.7", "V"),
]


def build(path: str | Path, facts=F, s_shards: int = 4, v_shards: int = 4) -> Path:
    base = Path(path)
    (base / "s").mkdir(parents=True, exist_ok=True)
    (base / "v").mkdir(parents=True, exist_ok=True)
    s_rows = [[] for _ in range(s_shards)]
    v_rows = [[] for _ in range(v_shards)]
    subjects, rels = set(), set()
    cell = lambda x: "" if x is None else str(x)
    for f in facts:
        subj, rel, kind, value, unit, start, until, source, conf, qual = f
        subjects.add(subj)
        rels.add(rel)
        s_rows[fnv1a(subj) % s_shards].append("\t".join(map(cell, f)))
        if kind == "S":
            vk = key_of(value)
            v_rows[fnv1a(vk) % v_shards].append("\t".join(map(cell, (vk, subj, rel, start, until, conf))))
    for i, rows in enumerate(s_rows):
        (base / "s" / f"{i:03d}.bin").write_bytes(gzip.compress(("\n".join(rows) + "\n").encode()))
    for i, rows in enumerate(v_rows):
        (base / "v" / f"{i:03d}.bin").write_bytes(gzip.compress(("\n".join(rows) + "\n").encode()))
    (base / "subjects.bin").write_bytes(gzip.compress(("\n".join(sorted(subjects)) + "\n").encode()))
    (base / "meta.json").write_text(json.dumps({"facts": len(facts), "subjects": len(subjects), "s_shards": s_shards,
                                                "v_shards": v_shards, "relation_names": sorted(rels)}))
    return base


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "mini_data"))
