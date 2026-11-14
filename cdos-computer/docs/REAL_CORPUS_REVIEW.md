# M1.1 full-corpus integration review

This review ran the computer against the production corpus (`corpus.pf`, 1,459,260 rows) through the normal public
path: `Computer(Config(corpus=...))`, with the LLM off and no network. Every case in it says what was asked, what the
corpus supports, what the computer said and which stored rows it cited. Each failure is classified as one of:
**engine defect**, **corpus data** or **unsupported query**.

**What this review does not show.** It doesn't show that the computer is correct over the whole corpus. The corpus
loads, verifies and passes the suite, and 73 hand-checked cases plus 40 held-out questions behave as recorded
below. That's a sample of about 1.2 million facts, chosen to cover each source and each kind of question. It is
not an exhaustive check.

## Summary

| | |
|---|---|
| Corpus | 1,210,591 facts, 140,428 subjects, 129 relations, 256 + 256 shards |
| Integrity | `verified` on every load (3 of 3 in the validator, plus every review run) |
| Version | stable: `sha256:935d454723620f13faa589c277ded07b7f411dcdc812e001e9df05cf96015d97` |
| Validator (`tools/validate_corpus.py`) | no problems: shards decode, columns, kinds, numbers, dates, placement, totals, reverse rows, duplicates |
| Review cases (73) | 59 pass, 0 fail, 5 known (classified), 9 observed (no single right answer; checked by hand) |
| Held-out real questions (40) | original 30 right / 3 wrong / 7 abstained; M1.1 30 / 3 / 7; now **32 / 1 / 7** |
| Engine defects found and fixed | 8 (E1 to E8), plus one source policy change flagged for your decision |
| Test-corpus replies changed vs M1.1 | 0 of 487 |
| Test suite | 350 passed, 4 skipped (25 regression tests in `test_real_corpus_fixes.py`, 5 in `test_validate_corpus.py`, 1 data-gated in `test_real_corpus.py`) |
| Production files | unchanged: `corpus.pf` sha256 and all 514 shard/meta checksums of both builds verified after the review |

## 1. How the corpus was loaded, and what was left alone

- **What was supplied.** The upload contained `corpus.pf` only, with no shards. The shards were built once,
  before any test ran, using the command the README documents:
  `tools/build_shards.py corpus.pf --out data --fix-wordnet --fix-wikidata --fix-keys`. The output went to a
  folder outside the repo. A second build without the `--fix` flags (`data_plain`, 1,274,090 facts) was made
  for comparison only.
- **Freezing.** `corpus.pf` was then made read-only (`chmod 444`) and its sha256 recorded (`f17a638f…a792f425`).
  So were the sha256 of every file in both builds (514 each).
- **Checking.** At the end of the review: `sha256sum -c` passes for `corpus.pf` and for every file in `data/`
  and `data_plain/`. No file under the production folder is newer than its checksum list. Nothing in the
  review regenerated shards, edited facts or rewrote `meta.json`. The fixes are all in the engine.
- **Why dialogue can't write the corpus.** The capability registry's `WRITERS` allowlist doesn't include the
  corpus. The integrity check reads only. With `corpus_integrity = "verify"`, a stored hash that doesn't match is
  reported (`CORPUS_INTEGRITY`) rather than rewritten. Separately from that guarantee, the checksum comparison
  above shows that this review's 113 + 40 + 487 questions wrote nothing.
- **One copy of the corpus.** The review tools create one `Computer(cfg)`, which loads and verifies the corpus, then
  give each case a fresh `Computer(cfg, corpus)` on the same object. `gc` counts exactly one live `Corpus` after
  113 questions. The data-dependent `test_computer` tests use the same pattern.

## 2. Performance

All figures were measured in this container (x86, Python 3, warm page cache) with `scratchpad/perf.py`, over
113 questions: the 73 review cases plus the 40 held-out questions.

| | verify (default) | trust (explicit fast path) |
|---|---|---|
| Startup (`Computer(cfg)`, including the integrity check) | 0.88 to 1.07 s | 0.13 to 0.19 s |
| RSS before / after startup | 24 / 47 MB | 24 / 40 MB |
| RSS peak over 113 questions (24-shard cache) | 110 MB | 106 MB |
| Latency per question, first pass: median / p90 / max | 13.6 / 93 / 728 ms | 12.3 / 80 / 699 ms |
| Second pass (warm cache), median | 13.1 ms | 12.6 ms |

- **Integrity cost.** Verification costs about 0.75 s at startup and nothing afterwards. For comparison, the
  original code loaded in 0.04 s because it hashed nothing.
- **Memory.** Memory is dominated by the decoded-shard cache, at about 2.4 MB per subject shard. With
  `cache_shards = 8`, peak RSS falls to 71 MB at the same median latency, which suits the Pi.
- **Slowest questions.** These are yes/no IS_A tests that walk many ancestors across shards: "is oxygen a
  metal" 0.7 s, "is gold a substance" 0.5 s, "is mercury a planet" 0.5 s.
- **Reverse fan-out cost.** Large reverse fan-outs ("what is located in france", 302 rows) now cost about
  0.3 s, against 0.02 s before. This is the price of the E7 fix (§4): each pointing row is resolved to its stored
  row. Each shard is read once and scanned as text, not decoded and cached. The first version of the fix took
  1.7 s.

## 3. Results by source and kind

Every kind of question requirement 5 asks for is covered where the corpus supports it:
- **Direct retrieval:** W1–W9, D1–D13, B1, M9.
- **IS_A inheritance:** W6, M7, M10–M15. M15, for example, walks whale → cetacean → aquatic mammal →
  placental → mammal.
- **Numbers:** D2, D6, M6, N1, N17, N18.
- **Missing facts:** W7, W8, D14, X1–X6.
- **Conflicts:** D7, D15–D17, N6, N13.
- **Units:** N9–N13, A1.
- **Calculations:** N3–N8.
- **Time:** D5, D8, D9.

| Source | Cases | How names reach it |
|---|---|---|
| WordNet (OEWN) | 10 | 94% of OEWN subjects are reachable by typing their name |
| Wikidata | 22 | 86%. The rest have punctuation or accents |
| DBpedia | 2 | **2 of 1,129** subjects (all rows are CATEGORY; names like `$AVVY`, `(I_CAN'T_GET_NO)_SATISFACTION`) |
| MANUAL / TABLE / DOCUMENT / INFERENCE | 29 | 100% |
| LLM | 1 | the corpus's only LLM row; see §5 |
| none (calculation, status) | 9 | — |

The full case table is in §7.

## 4. Engine defects: found, fixed, regression-tested

Each defect was found on the real corpus and reproduced on a small fixture in `tests/test_real_corpus_fixes.py`,
so its test runs without the production data. **Run against M1.1, 19 of the 25 fail; on this tree all
25 pass.** Of the 19, 17 fail on M1.1's replies or values. The other 2 call the new `applicable()` and
`reverse_facts()` directly, so on M1.1 they fail because those functions don't exist yet. The 6 that pass on M1.1 check behaviour the fixes must keep (the forward question still answers,
undated figures stay when nothing is dated, and so on). That shows they catch the defects rather than just matching text.

| # | Defect (real case) | Before | Fix | Tests |
|---|---|---|---|---|
| E1 | Geological dates ("how old is the moon"; MOON FOUNDED -4527000000) | "4527 BC" (date regex took 4 digits); compare ordered wrongly | `to_number` reads any number of year digits; `date_text` says "about 4.53 billion years ago" / "10,000 BC" | TestE1 (2) |
| E2 | "what orbits venus": nothing orbits Venus | said Venus orbits the Sun, as if answering | after the planner's direction check rejects, says "My corpus doesn't say what orbit Venus" | TestE2 (2) |
| E3 | "what language does a wolf speak" | answered with facts about the *word* "language" | the navigator's "doesn't say its language" wins when the lookup's subject is a relation word | TestE3 (2) |
| E4 | Requirements in length / mass / area ("is the nile at least 6000 km long") | units not parsed; MEASURE mixed HEIGHT with WIDTH; "run 100 m" taken as a battery question | km, m, ft, kg, lb, km² and so on; MEASURE uses relations named by the adjective (`~long`, `~tall`) and says the reading as an assumption; electrical dims only for the analyst | TestE4 (4) |
| E5 | Several figures for one relation in CALC/COMPARE (Everest's four elevations; Jupiter's mass ×2) | silently took the first | figures that agree after conversion count as one; otherwise the better-ranked source is used and *said* as an assumption; equal-ranked disagreement is a CONFLICT and the computer declines | TestE5 (3) |
| E6 | Dated and undated figures mixed ("population of germany in 1960", "population of earth") | Earth's population "7,000,000" (an undated prehistoric estimate) | `applicable()`: the latest dated figure applying to the year wins over undated ones; undated figures stay when nothing is dated; lookup and navigator use the same policy | TestE6 (4) |
| E7 | **Reverse lookups cited made-up facts** ("what orbits saturn") | evidence `TITAN ORBITS SATURN (source ?, id 42db…)`: no stored row has that id; LLM and expired rows named as answers | `Corpus.reverse_facts()` resolves each pointing row to its stored row (real id, source, unit); `applicable()` applies; used by WHO, the lookup and soft resolution | TestE7 (4) |
| E8 | Accented keys said raw ("what is located in france") | "AIGUILLE_DE_LA_GRANDE_SASSIÈRE" | `is_name` accepts upper case in any script | TestE8 (1) |
| — | One-letter word taken as a verb ("does proxima centauri b have liquid water") | "B" read as a relation | `_logic` skips verbs shorter than 2 letters | TestOneLetter (1) |

- **Evidence check on the real corpus (E7).** For all 113 questions, every non-derived evidence row was matched
  against the stored rows for its subject. Result: 437 cited, **0 not stored**. On M1.1, "what orbits saturn" and
  "what orbits mars" cited 4 rows that don't exist.
- **Mutation check.** The 12 deliberate breakages from M1.1 were rerun on this tree: all 12 are still caught.
- **Answers on the test corpus.** All 487 test-corpus questions give the same reply text as on M1.1.

## 5. Source policy, for your decision: LLM rows are not stated as facts

The corpus has exactly one LLM row: `PROXIMA_CENTAURI_B HAS_LIQUID_WATER TRUE`, `speculative_model_output`,
conf 0.72. Before this review it was said as a fact. On M1.1, a reverse lookup also named an LLM-sourced
subject as an answer, which TestE7 shows.

The computer now leaves out every LLM-sourced row (`NOT_KNOWLEDGE = {"LLM"}`) from answers, measurements and
reverse lookups. The row stays in the corpus and is counted in its statistics. This puts into effect the
brief's "don't invent results". It is a policy change, though, not a bug fix. If you would rather have such
rows said with a "speculative" qualifier, the change is one set in `computer/corpus.py`.

## 6. Corpus data issues (reported, not changed)

| Issue | Evidence | Effect |
|---|---|---|
| Everest HEIGHT 3,650 m and 4,650 m | `MOUNT_EVEREST HEIGHT 3650 M`, `4650 M` (Wikidata) | "how tall is everest" lists them beside the four elevations; "at least 8000 m" is UNKNOWN (N13) |
| Four equally ranked Everest elevations | 8848.86, 8844.43, 8848, 8850 m | difference with K2 is declined as a conflict (N6), correctly |
| Mercury: mass 0 kg, and the planet and the element under one key | `MERCURY MASS 0 KG`; `MERCURY CHEMICAL_SYMBOL Hg` | mass answer includes "0 kilograms" and the atomic mass (D17); symbol right for a merged reason (M5) |
| Earth's undated prehistoric populations | `EARTH POPULATION 7000000` (undated) | handled by E6; worth dating or removing at source |
| Neptune HAS_SATELLITE lists only Triton | `MOON_COUNT 16` also present | "how many satellites does neptune have" says "lists 1: Triton" (the one wrong held-out answer) |
| "Founded" for the Moon and Earth | Wikidata inception, imported as FOUNDED | answer says "Moon was founded about 4.53 billion years ago" |
| Dated and undated copies of one value | e.g. FRANCE CAPITAL Paris (1789) and PARIS (undated) | handled by `applicable()` |
| IS_FUNCTIONAL / HAS_UNIT schema rows not consumed | corpus declares e.g. AREA functional; Germany has 3 areas | the engine uses `rules.json` instead; the corpus's declaration is contradicted by its own data |
| Boolean property rows | `OXYGEN IS_METAL FALSE` | not read as a "no" (see §8) |
| INTEGER_n keys | `INTEGER_97 IS_PRIME TRUE` | unreachable from "97" (see §8) |

## 7. Every case: input, expectation, result, evidence, classification

`expected` is one of:
- `answer: x`: the computer commits, and `x` appears in the reply;
- `admit`: the computer says the corpus lacks it;
- `yes` / `no`: the reply starts with that word;
- `any`: no single right answer from this corpus, so the reply is checked by hand and the note says what was
  found.

`known` marks a classified failure. Expectations corrected after checking the corpus are marked "gold
corrected": in each one the computer was right and the first expectation was wrong.

| id | kind | source | input | expected | actual (abridged) | evidence cited | result | classification / note |
|---|---|---|---|---|---|---|---|---|
| W1 | retrieval | OEWN | what does happy mean | answer: good fortune | Happy can mean: well expressed and to the point; marked by good fortune; enjoying or showing or marked by joy or pleasure. | HAPPY DEFINITION well expressed and to the point; "a happy turn of phrase"; "a few well-chosen words" (OEWN, 8bef5fd2c2326d90); HAPPY DEFINITION marked by good fortune; "a felicitous life"; "a happy outcome" (OEWN, 735043d7ef5631a8) | pass |  |
| W2 | retrieval | OEWN | what is the opposite of happy | answer: unhappy | The opposite of happy is unhappy. | HAPPY WN_ANTONYM unhappy (OEWN, cba643b6d0be2929) | pass |  |
| W3 | retrieval | OEWN | another word for big | answer: large | Other words for big: large, prominent, conspicuous, bighearted, bounteous, bountiful, freehanded and handsome. | BIG SYNONYM large (OEWN, 67d0c93fd3df4409); BIG SYNONYM prominent (OEWN, 920cea845d084b81) | pass |  |
| W4 | retrieval | OEWN | what kind of thing is a dog | answer: canine | Dog is a kind of canine, domestic animal, support, catch, sausage and villain. | DOG HYPERNYM canine (OEWN, ae402e5ce21fc498); DOG HYPERNYM domestic animal (OEWN, 48c599835543b60e) | pass |  |
| W5 | retrieval | OEWN | what are some kinds of dog | answer: puppy | Kinds of dog include puppy, pooch, cur, lapdog, toy dog, hunting dog, working dog, dalmatian, basenji and pug. | DOG HYPONYM puppy (OEWN, d0768f9ecbbe0c60); DOG HYPONYM pooch (OEWN, bc60f66a3d2c9b36) | pass |  |
| W6 | isa | OEWN | is a dog an animal | yes | Yes. Dog is a kind of canine. Canine is a kind of carnivore. Carnivore is a kind of predator. Predator is a kind of animal. | DOG HYPERNYM canine (OEWN, ae402e5ce21fc498); DOG HYPERNYM domestic animal (OEWN, 48c599835543b60e) | pass |  |
| W7 | retrieval | OEWN | use happy in a sentence | admit | I know the word happy, but my corpus doesn't have an example sentence with happy. | — | pass | gold corrected: the corpus has no EXAMPLE rows at all; admitting it is right |
| W8 | missing | OEWN | what does flurbishness mean | admit | Hoo. I don't know that one yet; it isn't in my corpus. I won't guess at facts. | — | pass |  |
| W9 | retrieval | OEWN | what does colour mean | answer: visual | Colour can mean: the appearance of objects (or light sources) described in terms of a person's perception of their hue and lightness (or brightness) and saturation; an... | COLOUR DEFINITION the appearance of objects (or light sources) described in terms of a person's perception of their hue and lightness (or brightness) and saturation (OEWN, 7deb292f467254f1); COLOUR DEFINITION an outward or token appearance or form that is deliberately misleading; "he hoped his claims would have a semblance of authenticity"; "he tried to give his falsehood the gloss of moral sanction"; "the situation soon took on a different color" (OEWN, 397a94284a796e6b) | pass |  |
| D1 | retrieval | TABLE | what is the capital of france | answer: Paris | France's capital is Paris (since 1789). | FRANCE CAPITAL PARIS (TABLE, 96b8a3ecd1f9e2dd); FRANCE CAPITAL Paris (WIKIDATA, c152f0006f3a0b18) | pass |  |
| D2 | numeric | WIKIDATA | what is the area of france | answer: 643,801 | France is part of European Economic Area. France's area is 643,801 square kilometres. | FRANCE PART_OF European Economic Area (WIKIDATA, 7f9867187b23a733); FRANCE AREA 643801 KM2 (WIKIDATA, 2d61ef270e7da452) | pass |  |
| D3 | retrieval | WIKIDATA | when was albert einstein born | answer: 1879 | Albert Einstein was born on 14 March 1879. | ALBERT_EINSTEIN BORN 1879-03-14 (WIKIDATA, 26f0a57ee9eb83f3) | pass |  |
| D4 | retrieval | WIKIDATA | what is the occupation of albert einstein | answer: theoretical physicist | Albert Einstein's occupations are theoretical physicist, philosopher of science, inventor, science writer and pedagogue. | ALBERT_EINSTEIN OCCUPATION theoretical physicist (WIKIDATA, 6102bb528bd6d891); ALBERT_EINSTEIN OCCUPATION philosopher of science (WIKIDATA, d2dd677ae4d9534e) | pass |  |
| D5 | temporal | WIKIDATA | what was the citizenship of albert einstein in 1920 | answer: Weimar Republic | Albert Einstein's citizenships are Switzerland (since 1901) and Weimar Republic (from 1918 until 1933). | ALBERT_EINSTEIN CITIZENSHIP Switzerland (WIKIDATA, 46d80a0f2e82a691); ALBERT_EINSTEIN CITIZENSHIP Weimar Republic (WIKIDATA, 3965c15edc4e22f7) | pass |  |
| D6 | numeric | WIKIDATA | what is the elevation of mount everest | answer: 8848.86 | Mount Everest's elevation is 8848.86 metres, or 8844.43 metres, or 8848 metres. | MOUNT_EVEREST ELEVATION 8848.86 M (WIKIDATA, a8126dece69ef58f); MOUNT_EVEREST ELEVATION 8844.43 M (WIKIDATA, dd856a2a31eb13de) | pass |  |
| D7 | conflict | WIKIDATA | how tall is mount everest | any | Mount Everest's height is 3650 metres, or 4650 metres. Mount Everest's elevation is 8848.86 metres, or 8844.43 metres, or 8848 metres. | MOUNT_EVEREST HEIGHT 3650 M (WIKIDATA, 819e60152e1dec8f); MOUNT_EVEREST HEIGHT 4650 M (WIKIDATA, 7cd81a25e6f8c43c) | observed | corpus data: Wikidata HEIGHT 3650 / 4650 m isn't Everest's elevation; the computer lists both relations faithfully |
| D8 | temporal | WIKIDATA | what is the population of south africa | answer: 62,027,503 | South Africa's population is 62,027,503 (since 2022). | SOUTH_AFRICA POPULATION 62027503 (WIKIDATA, d98c1ba2eaeaf50e) | pass | gold corrected: first expectation (57,725,600, 2018) missed the corpus's 2020-2022 figures; the computer's answer was right |
| D9 | temporal | WIKIDATA | what was the population of germany in 1960 | answer: 73,147,000 | Germany's population is 73,147,000 (since 1960). | GERMANY POPULATION 73147000 (WIKIDATA, 22e90a071f65cf4a) | pass |  |
| D10 | retrieval | WIKIDATA | what is the highest point of bolivia | answer: Nevado Sajama | Bolivia's highest point is Nevado Sajama. | BOLIVIA HIGHEST_POINT Nevado Sajama (WIKIDATA, 9d5999a07fab49bc) | pass |  |
| D11 | retrieval | WIKIDATA | what is the capital of yemen | answer: Sanaa | Yemen's capitals are Sanaa (since 1990) and Aden. | YEMEN CAPITAL Sanaa (WIKIDATA, c92acd8c3f74553c); YEMEN CAPITAL Aden (WIKIDATA, 5a4b88dcdcdaef75) | pass |  |
| D12 | retrieval | TABLE | what currency does japan use | answer: Yen | Japan's currency is Yen. | JAPAN CURRENCY YEN (TABLE, e9e510d112538239) | pass |  |
| D13 | retrieval | TABLE | what is the capital of south africa | answer: Pretoria | South Africa's capital is Pretoria. | SOUTH_AFRICA CAPITAL PRETORIA (TABLE, ea86a3a666c9585d) | pass |  |
| D14 | missing | WIKIDATA | which countries border france | admit | My corpus doesn't say what border France. | — | pass |  |
| D15 | conflict | WIKIDATA | what is the area of germany | answer: 357 | Germany's area is 357587.77 square kilometres, or 357,170 square kilometres, or 349,390 square kilometres. | GERMANY AREA 357587.77 KM2 (WIKIDATA, d993e59381be6754); GERMANY AREA 357170 KM2 (WIKIDATA, ee76b42c0deac840) | pass |  |
| D16 | conflict | WIKIDATA | what is the population of earth | any | Earth's population is 8,117,059,282 (since 2025). | EARTH POPULATION 8117059282 (WIKIDATA, 7926aca97b23d3a9) | observed | fixed (E6): was 'Earth's population is 7,000,000' (undated prehistoric estimates); now the latest dated figure |
| D17 | conflict | DOCUMENT | what is the mass of mercury | answer: 3.301e+23 | Mercury's mass is 3.301e+23 kilograms, or 330 yottagram, or 0 kilograms. Mercury's atomic mass is 200.592 u. | MERCURY MASS 3.301e+23 KG (DOCUMENT, 6e84c8b9cec6c81e); MERCURY MASS 330 YOTTAGRAM (WIKIDATA, 4beb7d99bb632776) | known | **corpus data**: Wikidata gives Mercury a mass of 0 kg, and its planet and element facts share the key MERCURY (homonyms merged by label), so 'atomic mass' joins the answer |
| B1 | retrieval | DBPEDIA | what category is girl is on my mind | answer: 2004 songs | Girl Is On My Mind's categorys are 2004 songs, songs written by dan auerbach, songs written by patrick carney, split singles and the black keys songs. | GIRL_IS_ON_MY_MIND CATEGORY 2004_SONGS (DBPEDIA, fa8416ae0bc7061e); GIRL_IS_ON_MY_MIND CATEGORY SONGS_WRITTEN_BY_DAN_AUERBACH (DBPEDIA, 16261ab4f635cbb8) | pass |  |
| B2 | retrieval | DBPEDIA | what is $avvy | answer: film | Hoo. I don't know that one yet; it isn't in my corpus. I won't guess at facts. | — | known | **unsupported query**: names with punctuation or accents can't be typed into the plan language or the name resolver (DBpedia: 2 of 1,129 subjects reachable by name; Wikidata 86%) |
| M1 | retrieval | MANUAL | is 97 prime | yes | Hoo. I don't know that one yet; it isn't in my corpus. I won't guess at facts. | — | known | **unsupported query**: integer facts are keyed INTEGER_97; the resolver finds the WordNet entry '97' and nothing links the two |
| M2 | retrieval | MANUAL | what is 12 in roman numerals | answer: XII | Hoo. I don't know that one yet; it isn't in my corpus. I won't guess at facts. | — | known | **unsupported query**: as M1 (INTEGER_12 ROMAN_NUMERAL XII) |
| M3 | calc | - | what is 97 squared | answer: 9,409 | 97 to the power of 2 is 9,409. | — | pass |  |
| M4 | isa | DOCUMENT | is mercury a planet | yes | Yes. Mercury is a planet. | MERCURY IS_A PLANET (DOCUMENT, 40121489085ae543); MERCURY IS_A TERRESTRIAL_PLANET (DOCUMENT, 34bc0b81bea1306f) | pass |  |
| M5 | ambiguous | TABLE | what is the chemical symbol of mercury | any | Mercury is a chemical element. Mercury's chemical symbol is Hg. Mercury's chemical formula is Hg. | MERCURY IS_A chemical element (WIKIDATA, bba212fe5848b18e); MERCURY CHEMICAL_SYMBOL Hg (WIKIDATA, 62c1246a47dbe116) | observed | corpus data: Hg comes from the element's Wikidata facts merged under MERCURY; the right symbol, for a merged reason |
| M6 | numeric | TABLE | what is the atomic number of gold | answer: 79 | Gold's atomic number is 79. Gold's atomic mass is 196.97 u, or 196.96657 u. Other words for gold: atomic number 79. | GOLD ATOMIC_NUMBER 79 (TABLE, b9c2c5dddf7b3915); GOLD ATOMIC_MASS 196.97 U (TABLE, a8ff53756c50f391) | pass |  |
| M7 | isa | WIKIDATA | is gold a metal | yes | Yes. Gold is a precious metal. Precious metal is a metal. | GOLD IS_A CHEMICAL_ELEMENT (TABLE, 44577c51a0752414); GOLD IS_A metallic material (WIKIDATA, 4fd60659364ebc64) | pass |  |
| M8 | retrieval | INFERENCE | is oxygen a metal | no | I can't find that in my corpus. I know Oxygen is a chemical element and a lithophile element, but nothing there links it to metal. | OXYGEN IS_A CHEMICAL_ELEMENT (TABLE, 6c3a21638f92cc5e); OXYGEN IS_A lithophile element (WIKIDATA, a859808e05232585) | known | **unsupported query**: a yes/no TEST uses IS_A links; boolean property rows (IS_METAL FALSE) aren't read as a 'no'.  The computer admits it can't tell rather than answering wrongly |
| M9 | retrieval | DOCUMENT | what does a lion eat | answer: carnivore | Lion's diet is carnivore. | LION DIET CARNIVORE (DOCUMENT, 5537c2872291c9a8) | pass |  |
| M10 | isa | DOCUMENT | is a lion a mammal | yes | Yes. Lion is a mammal. | LION IS_A MAMMAL (DOCUMENT, 8d66f85f850ca6f1) | pass |  |
| M11 | isa | DOCUMENT | is a lion a fish | no | No. Lion is a mammal. Nothing is both a mammal and a fish. | LION IS_A MAMMAL (DOCUMENT, 8d66f85f850ca6f1) | pass |  |
| M12 | isa | MANUAL | is a lion a living thing | yes | Yes. Lion is a mammal. Mammal is an animal. Animal is a living thing. | LION IS_A MAMMAL (DOCUMENT, 8d66f85f850ca6f1) | pass |  |
| M13 | isa | MANUAL | is jupiter a celestial body | yes | Yes. Jupiter is a planet. Planet is a celestial body. | JUPITER IS_A PLANET (DOCUMENT, 47f1785fb97c5a38); JUPITER IS_A GAS_GIANT (DOCUMENT, 27dd8a85d7f76ee3) | pass |  |
| M14 | isa | MANUAL | is gold a substance | yes | Yes. Gold is a chemical element. Chemical element is a substance. | GOLD IS_A CHEMICAL_ELEMENT (TABLE, 44577c51a0752414); GOLD IS_A metallic material (WIKIDATA, 4fd60659364ebc64) | pass |  |
| M15 | isa | OEWN | is a whale a mammal | yes | Yes. Whale is a kind of cetacean. Cetacean is a kind of aquatic mammal. Aquatic Mammal is a kind of placental. Placental is a kind of mammal. | WHALE HYPERNYM cetacean (OEWN, c18eeab913b4ca8d); WHALE HYPERNYM large person (OEWN, ff8e5df7431a8763) | pass |  |
| M16 | missing | LLM | does proxima centauri b have liquid water | any | I can't find that in my corpus. I know Proxima Centauri B is an exoplanet, but nothing there links it to liquid water. | PROXIMA_CENTAURI_B IS_A EXOPLANET (DOCUMENT, fc0fdd858f46e093); EXOPLANET HYPERNYM planet (OEWN, dac4f216dbb8031e) | observed | fixed (one-letter relation words, LLM rows not facts): admits it; the corpus's only LLM row is never said as fact |
| N1 | numeric | DOCUMENT | how many moons does jupiter have | answer: 95 | Jupiter's moon count is 95. Jupiter has satellite Io, Europa, Ganymede and Callisto. | JUPITER MOON_COUNT 95 (DOCUMENT, 49e65dcd2acc0589); JUPITER HAS_SATELLITE IO (DOCUMENT, d2530977f07887c2) | pass |  |
| N2 | compare | DOCUMENT | which is heavier, jupiter or saturn | answer: Jupiter is heavier | Jupiter's mass is 1.898e+27 kilograms, and Saturn's is 5.683e+26 kilograms, so Jupiter is heavier. | JUPITER MASS 1.898e+27 KG (DOCUMENT, b791cec10dfe5e2e); JUPITER MASS 1898130 YOTTAGRAM (WIKIDATA, 5fb873213ee0060d) | pass |  |
| N3 | calc | DOCUMENT | how many times heavier is jupiter than saturn | answer: 3.34 times | Jupiter's mass is 1.898e+27 kilograms, and Saturn's is 5.683e+26 kilograms, so Jupiter's mass is 3.34 times Saturn's. | JUPITER MASS 1.898e+27 KG (DOCUMENT, b791cec10dfe5e2e); JUPITER MASS 1898130 YOTTAGRAM (WIKIDATA, 5fb873213ee0060d) | pass |  |
| N4 | calc | DOCUMENT | how many times bigger is jupiter than earth | answer: 11.2 times | Jupiter's equatorial radius is 71492.0 kilometres, and Earth's is 6378.1 kilometres, so Jupiter's equatorial radius is 11.2 times Earth's. | JUPITER EQUATORIAL_RADIUS 71492.0 KM (DOCUMENT, cc82dd3fec04e098); EARTH EQUATORIAL_RADIUS 6378.1 KM (DOCUMENT, fa34f2fe2d0ae0db) | pass |  |
| N5 | calc | TABLE | how much longer is the nile than the amazon | answer: 250 kilometres | Nile's length is 6650.0 kilometres, and Amazon's is 6400.0 kilometres, so Nile is 250 kilometres longer. | NILE LENGTH 6650.0 KM (TABLE, 6bd6b1bc131de77a); AMAZON LENGTH 6400.0 KM (TABLE, d636ff0efedb783c) | pass |  |
| N6 | calc | WIKIDATA | what is the difference in elevation between mount everest and k2 | admit | My corpus gives Mount Everest more than one elevation (8848.86 metres, 8844.43 metres, 8848 metres and 8850 metres), from equally ranked sources, so I won't pick one f... | MOUNT_EVEREST ELEVATION 8848.86 M (WIKIDATA, a8126dece69ef58f); MOUNT_EVEREST ELEVATION 8844.43 M (WIKIDATA, dd856a2a31eb13de) | pass | gold corrected: Everest has four equally ranked Wikidata elevations (8848.86, 8844.43, 8848, 8850); refusing to pick is right |
| N7 | calc | WIKIDATA | what is the combined population of france and germany | answer: together | France's population is 68,605,616 (since 2025), and Germany's is 83,577,140 (since 2024), so together that's 152,182,756. | FRANCE POPULATION 68605616 (WIKIDATA, 7ec66e744a327299); GERMANY POPULATION 83577140 (WIKIDATA, ab09d06d25598268) | pass |  |
| N8 | calc | WIKIDATA | what is the population density of france | answer: per square kilometre | France's population is 68,605,616 (since 2025), and its area is 643,801 square kilometres, so its population density is about 107 per square kilometre. | FRANCE POPULATION 68605616 (WIKIDATA, 7ec66e744a327299); FRANCE AREA 643801 KM2 (WIKIDATA, 2d61ef270e7da452) | pass |  |
| N9 | units | TABLE | is the nile at least 6000 km long | yes | Yes. Nile's length is 6650.0 kilometres, so it meets 6,000 kilometres. | NILE LENGTH 6650.0 KM (TABLE, 6bd6b1bc131de77a) | pass |  |
| N10 | units | TABLE | is the nile at least 7000 km long | no | No. Nile's length is 6650.0 kilometres. That's less than 7,000 kilometres. | NILE LENGTH 6650.0 KM (TABLE, 6bd6b1bc131de77a) | pass |  |
| N11 | units | TABLE | is the nile at least 6000000 m long | yes | Yes. Nile's length is 6650.0 kilometres, so it meets 6,000 kilometres. | NILE LENGTH 6650.0 KM (TABLE, 6bd6b1bc131de77a) | pass |  |
| N12 | units | WIKIDATA | is k2 at least 8000 m | any | Yes. K2's height is 8611.0 metres (also 8611 metres, the same figure), so it meets 8,000 metres. | K2 HEIGHT 8611.0 M (TABLE, 31ea58dd668110df); K2 ELEVATION 8611 M (WIKIDATA, 61324ec89a970281) | observed | fixed (E4): two length relations with the same value, so a verdict |
| N13 | units | WIKIDATA | is mount everest at least 8000 m | any | I can't tell. My corpus gives Mount Everest more than one length: 3,650 metres, 4,650 metres, 8,849 metres, 8,844 metres, 8,848 metres, 8,850 metres and 8,848 metres, ... | MOUNT_EVEREST HEIGHT 3650 M (WIKIDATA, 819e60152e1dec8f); MOUNT_EVEREST HEIGHT 4650 M (WIKIDATA, 7cd81a25e6f8c43c) | observed | corpus data: Everest's length figures (HEIGHT 3650/4650, four elevations) disagree on the answer, so UNKNOWN, said with the figures |
| N14 | numeric | WIKIDATA | how old is the moon | any | Moon was founded about 4.53 billion years ago. | MOON FOUNDED -4527000000 (WIKIDATA, 186a41d710fb36ce) | observed | fixed (E1): was '4527 BC'.  'founded' is the corpus's own relation name (Wikidata inception) for the Moon |
| N15 | numeric | WIKIDATA | when did the earth form | any | Hoo. I don't know that one yet; it isn't in my corpus. I won't guess at facts. | — | observed | unsupported query: 'form' isn't linked to FOUNDED without the optional lexicon |
| N16 | compare | WIKIDATA | which is older, the earth or the moon | answer: Earth | Earth was founded about 4.54 billion years ago, and Moon's is about 4.53 billion years ago, so Earth is older. | EARTH FOUNDED -4540000000 (WIKIDATA, b19a787d3543a06d); MOON FOUNDED -4527000000 (WIKIDATA, 186a41d710fb36ce) | pass |  |
| N17 | numeric | DOCUMENT | how long is a day on mars | answer: 24.7 | Mars's orbital period is 687.0 days, or 686.98 days. Mars's day length is 24.7 hours, or 24.622963 hours. | MARS ORBITAL_PERIOD 687.0 DAYS (DOCUMENT, 07b93862a2d80207); MARS DAY_LENGTH 24.7 HOURS (DOCUMENT, 618760797da0064f) | pass |  |
| N18 | numeric | TABLE | what is the boiling point of water | answer: 100 | Water's boiling point is 100.0 degrees Celsius, or 99.9839 degrees Celsius. Water's melting point is 0.0 degrees Celsius, or 0.002519 degrees Celsius. | WATER BOILING_POINT 100.0 C (TABLE, e8a322da8b71fd5f); WATER MELTING_POINT 0.0 C (TABLE, 2d385646571936e7) | pass |  |
| X1 | missing | - | what is the capital of jupiter | admit | Hoo. I don't know that one yet; it isn't in my corpus. I won't guess at facts. | — | pass |  |
| X2 | missing | - | what is the population of the moon | admit | Hoo. I don't know that one yet; it isn't in my corpus. I won't guess at facts. | — | pass |  |
| X3 | missing | - | what is the mass of france | admit | Hoo. I don't know that one yet; it isn't in my corpus. I won't guess at facts. | — | pass |  |
| X4 | missing | - | what orbits venus | admit | My corpus doesn't say what orbit Venus. | — | pass |  |
| X5 | missing | - | what language does a wolf speak | admit | I know about Wolf, but my corpus doesn't say its language. | — | pass |  |
| X6 | missing | - | what is the boiling point of jupiter | admit | Hoo. I don't know that one yet; it isn't in my corpus. I won't guess at facts. | — | pass |  |
| R1 | reverse | DOCUMENT | what orbits saturn | answer: Titan | Enceladus and Titan orbit Saturn. | TITAN ORBITS SATURN (DOCUMENT, 3d6c5acbb7fe7f00); ENCELADUS ORBITS SATURN (DOCUMENT, 167f8ef201c4332b) | pass | fixed (E7): on M1.1 the same reply cited these as (?, 42db…), ids no stored row has |
| R2 | chain | TABLE | what is the currency of the country whose capital is tokyo | any | Tokyo is capital of Japan. Tokyo is a capital of Japan (since 1869) and a de facto national capital. Tokyo: capital and largest city of Japan. Tokyo's capital is Shinj... | TOKYO CAPITAL_OF JAPAN (TABLE, 88b713b475c61efd); TOKYO IS_A capital of Japan (WIKIDATA, b50b6d71ae4693b3) | observed | unsupported query: a relative clause ('the country whose capital is') isn't a plan shape; the lookup's off-target Tokyo facts are said |
| R3 | chain | TABLE | what is the official language of the capital of france | answer: French | France's capital is Paris (since 1789). Paris's official language is French. | FRANCE CAPITAL PARIS (TABLE, 96b8a3ecd1f9e2dd); FRANCE CAPITAL Paris (WIKIDATA, c152f0006f3a0b18) | pass | gold corrected: the corpus has Paris OFFICIAL_LANGUAGE Q150 (French); the computer's answer was right |
| A1 | units | - | How long can a 50 Wh battery run a 5 W device? | answer: 8.5 hours | The estimated runtime is 8.5 hours: 50 watt-hours at 5 watts, assuming 85% conversion efficiency. | — | pass |  |
| S1 | status | - | corpus status | answer: 1,210,591 facts | The corpus holds 1,210,591 facts about 140,428 subjects. | — | pass |  |

## 8. Unsupported query types (not counted as engine failures)

| Type | Example | What happens |
|---|---|---|
| Names with punctuation or accents | "what is $avvy" (B2); 14% of Wikidata subjects; nearly all DBpedia | "I don't know that one" |
| INTEGER_n naming | "is 97 prime" (M1), "12 in roman numerals" (M2) | not found |
| Boolean property rows as an answer | "is oxygen a metal" (M8) | admits it can't link them; doesn't say "no" |
| Relation synonyms outside the optional lexicon | food, home, environment, live, form, chief city (6 held-out questions; N15) | abstains, naming what the corpus doesn't say |
| Relative-clause chains | "the currency of the country whose capital is tokyo" (R2) | off-target Tokyo facts |
| Elliptical questions | "anything orbiting jupiter?" | abstains |

These need either data (keys) or new plan shapes. All of them abstain or give a hedged reply rather than a
wrong fact, apart from R2's off-target reply. None of them is Milestone 1 work.

## 9. Skipped and failing tests

- **Skipped without data:** 4 tests. `tests/test_real_corpus.py` runs only when `CDOS_DATA` or `./data` exists.
  It passed with `CDOS_DATA=/home/claude/prod/data` (integrity verified, no unclassified failures). The other
  3 are the existing data-dependent `test_computer` skips.
- **`test_computer.TestKnowledge`, with production data linked:** 2 passed and 1 failed. The failure is
  `test_knowledge_aside_for_named_things_only`, which expects "gas giant" but gets "Jupiter is a planet". It
  fails the same way in the original code. It's a stale expectation (the corpus now has both IS_A rows and
  PLANET ranks first), and it was left as it is. The temporary `data` link was removed.

## 10. Reproducing

```
python3 tools/validate_corpus.py data                    # read-only checks, integrity over 3 loads
python3 tools/review_real_corpus.py --data data --md out.md --json out.json
CDOS_DATA=data python3 -m pytest -q tests/test_real_corpus.py
python3 -m pytest -q                                     # 350 passed, 4 skipped
```
