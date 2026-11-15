# Earth Semantic Intelligence — assessment, phase 1, the phase 2 bridge, phase 3A–C

*Baseline: branch `sec/phase-2-audit` at `3012e53`. In the build environment
the suite ran 1217 tests with 9 failures, all environmental (no GTK 4
typelib, the install harness, the OpenSSL-loading check) — the same 9
before and after this work. `tests/test_esi.py` adds 41 (phase 1) and
`tests/test_esi_bridge.py` 27 (phase 2); phase 3 adds 9 more to
`tests/test_esi.py`. All passing.*

ESI was proposed as a dictionary-based language-understanding engine for the
AI terminal: WordNet, a semantic network, FastText embeddings, a small
PyTorch model, five phases, and its own control centre. This document checks
that proposal against the code, says what actually needed building, and
records what phase 1 built and what it measured.

---

## 1. The proposal, against what exists

The proposal found `semantics.py` and concluded the foundation was a learned
vocabulary. It missed most of what is already here, because most of it is in
`cdos-computer/`:

| The proposal asks for | What already exists | Where |
|---|---|---|
| A dictionary: definitions, parts of speech, synonyms, antonyms, multiple senses (WordNet) | The CDOS corpus is built from Open English WordNet: `DEFINITION`, `SYNONYM`, `ANTONYM`, `HYPERNYM`, `HYPONYM`, `DERIVATION`, with each fact's sense kept | `cdos-computer/computer/corpus.py` (`LEXICAL_RELS`), `tools/build_shards.py` |
| A semantic network: is-a, has-a, concept hierarchies | The CDOS knowledge graph and its navigator | `computer/navigator.py`, `computer/plan.py` |
| Learning which words mean what, with confidence, from evidence | Two learners: `semantics.py` (aliases and rules, confirmed or rejected by a person) and the CDOS **relation lexicon**, which proposes word→relation mappings from WordNet definitions and validates each against the graph | `earth_os/semantics.py`, `computer/lexicon.py` |
| "A small neural network that learns from corrections" | The CDOS **Neural Core**: versioned schema, reproducible training into staging, evaluation against held-out fixtures, explicit approval before activation, an append-only audit, and no write access to the corpus | `computer/neural/`, `docs/NEURAL_CORE.md` |
| A control centre showing what the system knows | The System Evolution Center | `earth_os/evolution/` |
| "Computer, you're using too much memory" → read the memory | Already answered: the phrasebook found "memory" and read `hardware.memory` | `capability_corpus.py` |

The proposal's architecture is sound where it matters: keep understanding
independent of the conversational model, keep the model away from operating
the machine, and build the measurable lexical layer before any neural one.
Those are this codebase's rules already (`AGENTS.md` §2, §8).

What it would have cost to build as written: a second WordNet beside the
one CDOS has; FastText's English vectors, which run to gigabytes, on a shell
whose most recent work (the Evolution Center's "hold less, read less")
was about holding less; and PyTorch, in a
package whose `dependencies` list is empty. It would also have built a
second neural pipeline next to one that already has the safety machinery the
proposal does not mention.

## 2. The gap that was actually there

Measured, not assumed. The phrasebook matches phrases **as written**, so:

| Said | Answered (before) | Why |
|---|---|---|
| what is hogging my ram | nothing | "ram hog" is a phrase; "hogging my ram" is not |
| which app is eating the most memory | **total memory** | found the bare phrase "memory" and answered a different question |
| how full is my disk | nothing | the phrase is "how full is *the* disk" |
| how warm are you | nothing | the phrase is "how *hot* is *it*" |
| why is my pc so slow | nothing | the phrase is "*computer* is slow" |
| you're getting tired | learned layer proposed opening **settings** | fuzzy match: "getting" ≈ "settings" |
| don't lock the screen | **locked the screen** | contains "lock the screen"; `system.lock` is not effectful, so nothing asked first |
| do not open the terminal | **opened the terminal** | the fixed vocabulary matches "terminal" inside it |

The learned layer already knew that RAM is memory. Nothing let the corpus use
it. Every new wording had to be typed into a tuple by hand, and the
phrasebook's comment says as much: "the learned layer is what grows it" — but
the learned layer only ever fed the fixed vocabulary, never the corpus.

The last two rows are defects, not gaps, and phase 1 fixes both.

## 3. What phase 1 built

**`earth_os/lexical.py`** reads a sentence as concepts: contractions undone,
inflections reduced, synonyms collapsed (ram→memory, laptop→computer,
sluggish→slow, hogging→use), a part implying its whole (a sentence about the
processor is about the computer), filler dropped. The tables are small and
written by hand — 86 words in 18 concepts, 8 multi-word phrases, 22
irregular forms — and they are meaning, so they live above the boundary and
name no mechanism. From them the corpus indexes 102 question phrases by
meaning.

**The capability corpus** reads every question phrase the same way and keeps
the resulting concept sets. When a sentence's concepts contain a phrase's,
that is a reading by meaning. No new tier was added to `handle_utterance`:
the change is inside `CapabilityCorpus.resolve`, so the voice path, the
terminal (`resolve_analysis`) and Earth Mind (`mind/system.py`) all get it
from the one place.

Five rules, each a test in `tests/test_esi.py`:

1. **Meaning reaches questions, never acts.** Only analyses and parameterless,
   non-effectful readings are indexed, and not the screenshot, which leaves a
   file behind. An act is reached only by its phrase as written.
2. **Not means not.** A negated sentence is never read by meaning, and never
   starts an act through any command layer — the phrasebook, the fixed
   vocabulary, compositions or the learned layer. Every one of them matched
   by containment, so each did the thing it was told not to: "don't lock the
   screen" locked it, "do not open the terminal" opened it, "don't reboot"
   and "never mind, don't shut down" asked to do it. Now each reaches the
   conversation. A negated *question* is still answered. `lexical.says_not`
   is the one definition; `Shell.handle_utterance` and the corpus both use
   it. This holds with ESI switched off; it is a fix, not a feature.
3. **Meaning refines, it never overrules.** A reading by meaning replaces a
   phrase match only if it contains everything the phrase said. "Which app is
   eating the most memory" found {memory}; by meaning it is {use, most,
   memory}, a refinement. "What's the cpu temperature" found {temperature};
   by meaning it also fits "what cpu", which drops temperature, so the phrase
   stands. All 356 phrasebook phrases resolve exactly as they did before.
4. **A meaning must be about the machine.** "Sluggish" is a phrase; read by
   meaning it is just "slow", and a slow week at work is not a diagnosis. A
   concept set with no machine concept in it is not indexed.
5. **An ambiguous meaning is left out.** "What system is this" (distro) and
   "what machine is this" (hostname) read identically. Both phrases still
   work as written; the meaning is not used. The test lists the collisions,
   so a new one fails until a person decides.

**The learned layer's fuzzy match** now applies only to words that are not
English: it exists for typos ("restrat"), not for real words that happen to
be spelled like a learned one ("getting"). Whether a word is English comes
from Princeton WordNet 3.0's lemma and exception lists, shipped as
`assets/lexicon/english.words.gz` (273 KB, licence alongside, rebuilt
byte-for-byte by `tools/build_lexicon.py`). If the file is absent the answer
is "cannot tell" and the old behaviour stands.

`[semantics] lexical = false` switches the reading by meaning off.

## 4. Measurements

Two sets of paraphrases, each line a sentence and what it should resolve to,
or `-` for a sentence that must resolve to nothing. `tests/esi_heldout.jsonl`
was committed (`5d0532b`) before any table existed; tuning looked only at
`tests/esi_dev.jsonl`. Run `python3 tools/esi_measure.py <set> [--raw] [--show]`.

| Set | Questions answered right | Answered wrongly | Non-questions left alone |
|---|---|---|---|
| Held-out, phrasebook alone | 27 / 58 | 5 | 15 / 15 |
| **Held-out, with ESI** | **50 / 58** | **2** | **15 / 15** |
| Development, phrasebook alone | 16 / 36 | 3 | 4 / 6 |
| Development, with ESI | 34 / 36 | 0 | 5 / 6 |

`TestItIsMeasured` holds these as floors.

**How independent the held-out figure is.** Both sets were written by the
same author as the concept table, so the held-out set is a guard against
tuning, not an independent benchmark. It was measured once at 49/58 with 3
wrong, and that run exposed two reading bugs — "show me" was read as
possession, like "my", and the noun "hog" (a memory hog, a program) had been
collapsed with the verb "hogging" (using). Both were fixed as bugs and the set
was measured again: 50/58, 2 wrong. No synonym was added because of a
held-out sentence. A set written by someone else is the next useful thing.

The eight held-out misses, and why:

| Sentence | Result | Why |
|---|---|---|
| how long since the last reboot | `system.reboot` (asks first) | The phrase "reboot" matches as written, before and after. An act, so it is gated; still the wrong reading |
| which wifi network am i connected to | `network.scan` | {wifi, network} fits "wireless networks". A reading, so harmless, but wrong |
| is the processor getting too hot | nothing | "too hot" without "how" fits no phrase |
| how hard is the cpu working | nothing | "work hard" is a concept; "hard … working" is split |
| how long has the computer been on | nothing | no uptime phrase is about being *on* |
| which cpu is this | nothing | "what cpu" needs "what"; "which" is a different word |
| am i connected to wifi · is wifi connected | nothing | no wifi phrase is about being *connected* |

The one development negative still caught is not ESI's: "I have a terrible
memory for faces" finds the phrase "memory" as written, as it always has.

**Cost**, on the build machine:

| | |
|---|---|
| Building the corpus | 2.8 ms more, 99 KB more |
| Resolving a sentence | about 1–1.7 ms |
| English word list | not loaded at start; 0.8 MB on the first fuzzy fallback, never on a machine that is never mistyped at. Loading it as a Python set instead would cost 91 ms and 8.4 MB |

## 5. What was not built, and what phase 2 should be

*Phase 2's first part — the bridge — is built; see §7.*

* **No second dictionary.** Phase 2 should draw synonyms from the corpus CDOS
  already has, not from a new WordNet: propose candidate words for each
  concept from the corpus's `SYNONYM` and `HYPERNYM` facts, the way
  `computer/lexicon.py` proposes relation words, measure each against the
  paraphrase sets, and add only what a person approves. The concept table
  stays the authority; the corpus becomes its source of candidates.
* **Confirmed learned aliases as candidates.** `semantics.json` holds aliases
  people have confirmed. Those are good candidates for the concept table, by
  the same proposal-and-approval route. Unconfirmed ones are not.
* **No neural model yet.** If one is ever wanted, the CDOS Neural Core already
  has the staging, evaluation and approval machinery, and the paraphrase sets
  are its evaluation gate. Nothing in phase 1 needs it: the remaining misses
  above are each a missing word or phrase, not a failure to generalise.
* **No Evolution Center panel yet.** Concept count, indexed meanings,
  collisions and the held-out score are all cheap readings, and would sit
  naturally under the SEC's Intelligence section.
* **A larger, independent held-out set.** The single most useful next step.

**What was not touched:** no capability was added; no allowance in
`tests/test_boundary.py` changed; the effectful gate, Earth Mind's rules, the
Companion Engine and every human-write-only file are as they were.

## 6. Growing it

Add a word to `CONCEPTS` (or a phrase to `MULTIWORD`) in `earth_os/lexical.py`,
then:

```bash
/usr/bin/python3 -m unittest tests.test_esi tests.test_corpus -q
python3 tools/esi_measure.py tests/esi_dev.jsonl --show
python3 tools/esi_measure.py tests/esi_heldout.jsonl
```

A word that belongs to two concepts, a new collision between two questions,
any change to how an existing phrase resolves, or a drop on either set fails
the suite.

---

## 7. Phase 2: the CDOS Dictionary Bridge

*Built against the Phase 1.5 reference corpus (`corpus.pf`, 1,459,260 rows),
compiled with CDOS's own `build_shards.py --fix-wordnet` into 1,229,268 facts
over 143,777 subjects, and read through CDOS's own `Corpus` reader. Nothing
was parsed twice: the bridge never reads `corpus.pf` itself, because that
file's WordNet labels are swapped (its `ANTONYM` rows are hypernyms) and
CDOS already knows how to repair them.*

**CDOS supplies knowledge; ESI interprets language; a person approves.**
`earth_os/dictionary_bridge.py` reads the corpus and proposes words for
ESI's concepts; `tools/esi_bridge.py` is how a person reviews them. Earth OS
never needs the corpus at run time — `tests/test_esi_bridge.py` reads the
imports of the start-up modules to keep it that way.

```bash
python3 cdos-computer/tools/build_shards.py corpus.pf --out DATA --fix-wordnet   # 15 s
python3 tools/esi_bridge.py propose --corpus DATA        # 22 s → reviews/esi_bridge.json
python3 tools/esi_bridge.py list [--concept memory] [--blocked]
python3 tools/esi_bridge.py show "random access memory" memory
python3 tools/esi_bridge.py approve "random access memory" memory --by NAME
python3 tools/esi_bridge.py reject mainframe cpu --by NAME --reason "not a processor"
```

### Where a concept lives

A word's senses are many — "ram" is a sheep, a battering ram, a verb and
computer memory — so a sense is taken as a concept's only on two kinds of
evidence, both required:

1. **The right world.** For a concept about the machine, WordNet files the
   sense under computer science, its *direct* parent is a computing kind, or
   its definition says "computer". For any other concept, two of the
   concept's own words share the sense.
2. **The right concept.** The sense names the concept — "a portable
   *computer*", "computer *memory*" — or, for a concept not about the
   machine, the concept's own name word is in the sense.

Each half was added because the output without it was wrong, read against
the real corpus:

| Without | What it proposed |
|---|---|
| computing evidence, for the machine | ESI's "net" and "network" agree on the fishing net: *mesh, hairnet, tulle* |
| the name word, for other concepts | "consume" and "devour" agree on eating greedily: *demolish, down* as **use**; "usage" and "utilisation" agree: *employment, exercise* as **load** |
| naming the concept | desktop's computing sense is the screen background: *screen background* as **computer** |
| ignoring a definition's "(computer science)" label | the same, through the label |
| walking further up the hierarchy (tried first) | "screen > display > show > entertainment"; "computer > machine > motor vehicle" |

A "kind of" candidate (a hyponym) must be about computing in its own senses:
being a kind of monitor does not make a cardiac monitor part of this
computer.

### What is checked

Every candidate is tried with exactly that word added, against everything
phase 1 guarantees:

| Check | Fails when |
|---|---|
| `usable` | not plain words; a single letter in it ("C.P.U." → "c p u"); a function word; already a word for any concept |
| `own_evidence` | a kind of something, but nothing in its own senses is about computing |
| `one_concept` | the corpus offers it for two concepts |
| `phrases_unchanged` | any of the 356 phrasebook phrases resolves differently ("status" → wifi changes "network status") |
| `no_new_ambiguity` | two questions come to read the same |
| `scores_hold` | either paraphrase set loses a right answer, gains a wrong one, or starts answering a non-question. Re-adding "hog" as using fails here — it is the phase 1 noun/verb bug |
| `other_meanings_quiet` | one of WordNet's own example sentences, for one of the word's *other* senses, starts resolving to a capability |

The last check is the closest thing here to independent testing: the
sentences were written by WordNet's lexicographers, not by anyone tuning
ESI. It is what blocked **store** as memory — WordNet's synonym for computer
memory, and also *"he brought back a large store of Cuban cigars"*.

Approval re-runs every check against the tables *as they are then*, so
words approved one after another are checked together. A word that fails
can only be approved with `--override REASON`, which is recorded; a word
that is not `usable` cannot be approved at all. Approval needs `--by NAME`;
a rejection needs a reason and is final — the bridge never proposes that
word for that concept again. `lexical.py` re-checks every entry each time it
reads `approved.json`, so a hand edit is held to the same rules, and a bad
entry is skipped and named, never fatal.

**The bridge approves nothing itself, at any confidence.** Confidence —
relation weight times the square root of the share of the word's senses that
are this meaning — only orders the list. A word can pass every check with a
confidence of 0.00: "inactive" is similar to "sluggish" in one sense and
means something else in all ten of its own.

### Measured

| | |
|---|---|
| Concepts with an anchor | 11 of 17 grown (`user` is not: pronouns are grammar). None for battery, capacity, hot, load, overheat, temperature |
| Candidates | 175 |
| Passed every check | 72 (44 synonyms, 27 kinds, 1 similar) |
| Blocked | 103: 97 kinds with no computing sense of their own, 4 unusable, 1 offered for two concepts, 1 waking its other meanings (store) |
| All 72 approved together | dev 34/36 right, 0 wrong, 5/6 left alone; held-out 50/58, 2 wrong, 15/15 — **unchanged**; no phrase changed; no new ambiguity; corpus build 2.8 ms, as before |
| Shipped `approved.json` | empty. Nothing has been approved |

**What that measurement does and does not say.** The paraphrase sets cannot
show the bridge's value, because their misses are missing *phrases* ("is
the processor getting too hot"), not missing *words*, and none of them says
"microcomputer" or "random access memory". What they do show is that
approving everything that passed costs nothing on either set. Whether the
words help needs sentences that use them — which is, again, the case for a
held-out set written by someone else.

**What the checks cannot catch.** They test behaviour on known sentences; a
word wrong in meaning but absent from every sentence passes. Read against
the corpus, the 72 include good words (*central processing unit, random
access memory, personal computer, computer program, video display*), WordNet
quirks (*mainframe* as CPU, *monitoring device* as screen), and many kinds of
program few people would say (*parser, loop, patch, tagger*). That is why a
person approves: the bridge narrows 1.2 million facts to a page, and reading
the page is the part that cannot be automated honestly.

### Not built

* **No Evolution Center panel.** `tools/esi_bridge.py status` and
  `load_approved().problems` are the readings one would show.
* **Concepts the bridge cannot grow** (battery, temperature, hot, load,
  overheat, capacity) have no computing sense in WordNet that names them, or
  no sense two of their words share. They stay hand-written.
* **Context resolution and sentence structure** remain unbuilt for the
  reason in §5: no measured failure yet calls for them.

---

## 8. Phase 3A–3C: possessives, three outcomes, and an independent set

Phase 3 is to earn its complexity through demonstrated failures. This
section is the first failure, the fix scoped to it, and the measurement that
says what to do next.

### The failure

*"My computer is struggling because several programs are consuming
memory"* — kept in the set as a regression example — answered **top_memory** — by matching the phrase "taking up **my**
memory", using the "my" of "**my** computer". Phase 1 reads a sentence as an
unordered set of concepts, so a possessive belonged to the whole sentence
rather than to its noun. The answer happened to be reasonable; the reason
was wrong, and the same mechanism will produce wrong answers.

### 3A: a possessive attaches to its noun

"My" and "your" now bind to the noun after them (skipping a modifier —
"my *free* memory" — within three words). A reading carries
`user>computer` or `computer>call` instead of a free-floating `user` or
`computer`, and phrase keys carry the same, so "taking up my memory"
requires `user>memory`. Nothing else moved: every other word keeps exactly
the concept phase 1 gave it, which a test checks word by word across all
three sets. All 356 phrasebook phrases resolve as before; both earlier sets
score as before; the collision list is unchanged.

### 3B: three outcomes, on a developer-labelled set

`tests/esi_context.jsonl` — 44 sentences in six categories, committed
(`72f3c8e`) before any code changed. Each is scored three ways so a
vocabulary problem is not mistaken for a grammar problem:
`python3 tools/esi_measure.py tests/esi_context.jsonl --show`.

| | Recognition | Attachment | Interpretation | Requests answered wrongly | Requests unanswered | Non-requests answered |
|---|---|---|---|---|---|---|
| Phrasebook alone | — | — | 11 / 44 | 13 | 17 | 3 / 7 |
| Before 3A | 35 / 44 | 0 / 19 | 19 / 44 | 11 | 11 | 3 / 7 |
| **After 3A** | **35 / 44** | **19 / 19** | **19 / 44** | **11** | **11** | **3 / 7** |

Interpretation is split three ways because they are not equally bad: a
request unanswered falls through to conversation; a request answered
wrongly gives a wrong reading; a non-request answered does something nobody
asked for. All three answered non-requests ("my memory is terrible", "never
mind the memory", "how hot is it outside") come from the phrasebook's bare
phrases, not from ESI. The test holds wrong answers and answered
non-requests as ceilings.

Attachment is scored **exactly**: the possessives must attach to precisely
the labelled nouns, none missing and none extra. An outside review found
that the first version only checked the labelled ones were present, and
that "my computer and my phone are both slow" was labelled with one of its
two. The label was completed and the scoring made exact (a test now proves
an unlabelled attachment fails); every one of the 19 lines was checked
against the reading, and that was the only incomplete label.

Interpretation did not move, and that is the honest result: the two
sentences that were answered for the wrong reason ("my computer is
struggling…", "my laptop is taking up a lot of memory") are now not
answered for that reason, and fall to the plain "memory" reading. They were
wrong before by their labels and are wrong now — but now for a reason that
can be seen and fixed.

One correction is on the record: the first baseline scored recognition
44/44, because for slang the labels named only concepts ESI already had. Nine
labels were corrected (`812c778`) before any code changed, and the figures
above are after that correction.

**Why the 25 remaining interpretations miss** — every miss assigned one
cause:

| Cause | Misses | Example | Needs |
|---|---|---|---|
| Vocabulary — a word not recognised | 9 | "my pc is bogged down", "how much juice is left" | words, through the table or the bridge |
| A phrase that does not exist | 6 | "which program is using the most processor" | phrases |
| Several resources, one diagnosis | 3 | "the cpu is busy and the memory is full" | structure |
| Clause scope — *but*, *not*, *tell me* | 3 | "it's not the memory, the cpu is slow" | structure |
| A bare phrasebook word with another meaning | 3 | "my memory is terrible", "how hot is it outside" | the phrasebook, not ESI |
| Word order | 1 | "what is using the most cpu" → *what cpu* | structure |

Seven of 25 need structure; fifteen need words or phrases. On this set,
**vocabulary is the larger problem, not grammar** — so 3D is not yet
earned. The structural seven are recorded here as its evidence when it is.

This set was written and labelled by the developer. It guards against
regressions and against tuning; it does not measure generalisation.
`TestItIsMeasured.test_context_three_outcomes` holds the figures as floors.

### 3C: the independent set

[`docs/ESI_INDEPENDENT_SET.md`](ESI_INDEPENDENT_SET.md) is the protocol: a
brief to hand to a writer who has not seen ESI (and nothing else), the file
format, labelling from what the writer *wanted*, and the rules — commit the
labels before the first run, run once, never tune against it, retire it when
it has been used to decide too much. `tools/esi_measure.py --labels` lists
every label, generated from the catalogue; the measurement refuses a file
with a label that does not exist. A test checks the file's shape when it
exists, and holds no score for it on purpose.

### 3D: not started

When the independent set exists, the table above is re-made from it. If
structure is still the smaller share, the next work is vocabulary — the
bridge's queue, and slang the bridge cannot find. Clause scope is the first
structural candidate either way: three sentences here, and the shell-level
"not means not" rule (§3) is already a whole-sentence version of it.

---

## 9. Routing P1–P2 extended ESI

The Earth Mind routing audit ([EARTH_MIND_ROUTING_AUDIT.md](EARTH_MIND_ROUTING_AUDIT.md))
found that two of its five terminal failures were ESI phase 1 regressions:
reading a phrase as an unordered set lost that "what processor" asks *which
processor*, and reading by meaning answered "what is ram" with a memory
reading. Two additions to `lexical.py` fix them, both built on 3A's
attachment:

* **question words attach** — "what"/"which" bind to their noun
  (`what>cpu`), so "what processes are using the most cpu" no longer carries
  what "what processor" requires;
* **a sentence has a frame** — what it asks for (definition, comparison,
  another thing's quantity, identity, measurement, ranking) and whether it
  points at this machine. The corpus answers only with the kind of
  information asked for.

Every ESI figure held: dev 34/36, context 35/19/19, false positives 3/7;
held-out rose to 51/58 (see the audit §10 for why that is reported, not
claimed).
