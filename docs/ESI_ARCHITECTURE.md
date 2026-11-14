# Earth Semantic Intelligence — assessment, and phase 1

*Baseline: branch `sec/phase-2-audit` at `3012e53`. In the build environment
the suite ran 1217 tests with 9 failures, all environmental (no GTK 4
typelib, the install harness, the OpenSSL-loading check) — the same 9
before and after this work. `tests/test_lexical.py` adds 41, all passing.*

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

Five rules, each a test in `tests/test_lexical.py`:

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
/usr/bin/python3.12 -m unittest tests.test_lexical tests.test_corpus -q
python3 tools/esi_measure.py tests/esi_dev.jsonl --show
python3 tools/esi_measure.py tests/esi_heldout.jsonl
```

A word that belongs to two concepts, a new collision between two questions,
any change to how an existing phrase resolves, or a drop on either set fails
the suite.
