# CDOS Computer

A small, deterministic reasoning computer running on a **Raspberry Pi Zero 2 W** (or any machine with Python 3.11+).
It answers from a structured fact corpus, calculates with units, and shows how each answer was reached.

```
CDOS> how many moons does jupiter have
[KNOWLEDGE] Jupiter's moons are Io, Europa, Ganymede and Callisto.
CDOS> what is 12 times 7
[LOGIC] 12 times 7 is 84.
CDOS> what is the capital of narnia
[KNOWLEDGE] NOT FOUND: no matching fact in the corpus. No answer generated; facts are not guessed.
```

This tree was previously the **CDOS Owl** (a talking owl plush). It has been renamed to `computer`, and every
owl-voiced line has been replaced with terse machine output. The reasoning core (corpus, navigator, plan language,
rules, analysis, units, capabilities, result envelopes) is unchanged. See *Migrating from the owl build* below.

## How the computer answers

```
 input ──► ROUTER (rules + policy, deterministic)
             │
             ├─ someone may be hurt ─────────────► PRIORITY   directs the person to a human, never the sampler
             ├─ hi / time / arithmetic ──────────► LOGIC      code answers (calc.py, no eval)
             ├─ battery / power / energy ────────► ANALYSIS   unit-aware estimates with stated assumptions
             ├─ facts, definitions ──────────────► KNOWLEDGE  your CDOS Corpus (same data/ as cdos-web)
             │     health / money ──► facts only + NOTICE: consult an expert; never the sampler
             └─ "should I…", "what do you think…",
                "say something wise", a shake ───► SAMPLER    random I Ching output (labelled non-factual)
                                                     │  Knowledge may add one KNOWN FACT first
                                                     │  ("should I visit Jupiter?" → "KNOWN FACT: Jupiter is a gas giant.")
                                                     ▼
                                       mini LM (optional) re-words it
                                       └─ adds a number? drops the hexagram? predicts? → discarded,
                                          template output is used
```

### Operating rules (enforced in `computer/core.py`, not left to a model)

1. Facts come only from KNOWLEDGE; arithmetic only from LOGIC. The random sampler is never asked
   a factual question and can't change a fact or a sum. (A test shakes the device 20 times while
   asking "what is 12 times 7".)
2. The SAMPLER answers open questions only, and its output is labelled random and non-factual.
   "Will I…" questions get: *"NOTICE: this is not a prediction. The sampler cannot see the future."*
3. Health, money/law and safety never go to the sampler, however they're phrased.
   "Should I stop taking my medicine?" gets a NOTICE to consult a doctor, not a hexagram.
4. The mini LM is an output formatter, not a source. It can re-word what the computer already
   decided; it can't add to it.

Rule 3 lives in `computer/router.py` (`classify`, the "Policy" comment). The turn loop is `Computer.think()` in
`computer/core.py`.

### Output style

| Situation | Output |
|---|---|
| Greeting | `CDOS online. Hello, user. Ready for input.` |
| Fact not in the corpus | `NOT FOUND: no matching fact in the corpus. No answer generated; facts are not guessed.` |
| Corpus missing | `ERROR: corpus unavailable. No answer generated; facts are not guessed.` |
| Corpus fails its integrity check (strict) | `INTEGRITY FAILURE: the corpus does not match its build record. …` |
| Request needs device control or the internet | `UNSUPPORTED: this system has no device control or internet access.` |
| Health / money question | the facts, then `NOTICE: …consult a doctor…` / `NOTICE: …consult a qualified expert.` |
| Time | `TIME: 8:38 PM, Friday, October 23.` |
| Sampler | `SAMPLER OUTPUT (random, non-factual). Hexagram: … ` |

The terminal tags each line with the module that produced it: `[KNOWLEDGE]`, `[LOGIC]`, `[SAMPLER]`, `[PRIORITY]`
(plus `| model-name` when a language model worded it). Set `name` and `user` in `config.toml` to change
`CDOS` / `user`.

## The random sampler (`computer/iching.py`, `computer/instinct.py`)

The sampler is optional, application-level output. It is **never evidence** about facts, calculations or the
state of the machine: the router keeps it away from factual, health, money and safety questions, and nothing it
produces feeds the corpus, the navigator, the analysis engine or the result envelope's evidence.

- **Casting**: six lines, bottom to top, using the traditional odds.
  `coins`: old yin 1/8, young yang 3/8, young yin 3/8, old yang 1/8.
  `yarrow`: 1/16, 5/16, 7/16, 3/16. Set `method` in `config.toml`.
- **Changing lines** give the hexagram it transitions to ("state waiting to moving forward"). The
  **nuclear** hexagram appears in "tell me more".
- **Entropy**: the Pi's hardware random generator (`/dev/hwrng`) mixed with the OS's secure
  generator, the query text, and the exact moment of the cast.
- **Shake input**: a tilt switch records the nanosecond timing of every flip, and those timings
  are mixed into the cast.
- **Status LEDs show the cast**: before output, the LEDs blink the six lines bottom to top.
  A short blink is a still line, a long glow is a moving line.
- **Data**: `computer/data/iching.json` holds the 64 hexagrams (King Wen numbers, names,
  trigrams) plus a theme, a stance, a state, a line of counsel and two aphorisms each. The
  text is original paraphrase, not quoted from a translation.

```sh
python3 -m computer --cast "should I learn the violin?"
python3 -m computer --wise
python3 -m computer --text -v          # terminal session; -v shows how each answer was reached
```

## The Navigator (`computer/navigator.py`, `computer/plan.py`, `computer/planner.py`)

The plain lookup answers one thing about one name. The Navigator answers questions that
need several steps. A **planner** writes a short plan, a **validator** checks it, and an
**executor** runs it against the corpus, keeping every fact it used:

```
"what's the population of the capital of France?"
   $1 = FIND france
   $2 = GET $1 ~capital          -> PARIS            (France CAPITAL Paris)
   $3 = GET $2 ~population       -> 2100000          (Paris POPULATION 2100000, since 2023)
   ANSWER $3
🦉 France's capital is Paris. Paris's population is 2,100,000 (since 2023).
```

| Question shape | Example | How |
|---|---|---|
| chains | "population of the capital of France", "France's capital's population" | `GET` after `GET` |
| comparisons | "which is bigger, Jupiter or Saturn", "is Saturn heavier than Jupiter" | two `GET`s + `COMPARE` (same relation, same unit, or it won't) |
| reverse | "what orbits the sun", "which countries border France" | `WHO` (the reverse index) + `FILTER` |
| yes / can't find | "is a whale a mammal", "is the Eiffel Tower in France", "do owls have feathers" | `TEST` through IS_A or the same relation |
| how / inheritance | "how do whales breathe" | `GET … INHERIT`: whale → marine mammal → mammal → lungs, and the computer says each step |
| counts | "how many moons does Jupiter have" | the corpus's stated count, or it counts what it lists |
| time | "what currency did France use in 1990" | facts filtered to that year |
| meaning (and which sense) | "what does bank mean, as in a river", "define puppy", "carnivore definition" | `GET ~definition`, then `RANK` by the question's words |
| other words, opposites | "another word for happy", "small synonyms", "the opposite of big" | `GET ~synonym,similar` / `GET ~antonym` |
| kind of / kinds of | "what kind of thing is a dog", "what are some kinds of dog" | `GET ~isa,hypernym` / `GET ~hyponym` (or `WHO` the other way) |
| related words, a sentence | "words related to happy", "use happy in a sentence" | `GET ~derivation` / `GET ~example` |
| word chains and word yes/no | "what does the opposite of happy mean", "is glad a synonym of happy", "is a dog an animal" | `GET` after `GET`; `TEST` through the dictionary's "a kind of" |
| follow-ups | "another one", "what about sad", "and the opposite?", "what does that mean", "tell me more" | the last plan, re-run with `EXCEPT` or a new word |

House rules for the Navigator:

1. **It can't invent a fact.** The executor is the only thing that reads the corpus, and every
   sentence is rendered from a fact it fetched. A bad plan can only end in "my corpus doesn't say".
2. **Missing isn't "no".** A missing link is UNKNOWN: *"I can't find that in my corpus: nothing there links
   it to tower."* A "no" needs a stated rule (see *Reasoning: more than true and false* below), so "Is a
   shark a mammal?" gets *"No. Shark is a fish. Nothing is both a fish and a mammal."*
3. **Bounded.** At most 10 steps, 8 entities per step, 4 levels of IS_A and 6 backtracks.
   When a reading leads nowhere it tries the next one ("which language is spoken in France" is
   tried as a reverse lookup first, then as France's language).
4. **Inherited facts are shown as inherited.** The computer says *why* a whale has lungs.

### Words: the dictionary as a graph

The corpus already holds Open English WordNet's relations (DEFINITION, SYNONYM, WN_SIMILAR_TO,
WN_ANTONYM, HYPERNYM, HYPONYM, DERIVATION, EXAMPLE). The Navigator walks them the same way it walks
CAPITAL or ORBITS, so word questions get the same guarantees as fact questions:

```
"what does the opposite of happy mean"
   $1 = FIND happy
   $2 = GET $1 ~antonym          -> UNHAPPY               (Happy WN_ANTONYM unhappy)
   $3 = GET $2 ~definition       -> experiencing or marked by ... sadness or sorrow
   ANSWER $3
🦉 The opposite of happy is unhappy. Unhappy: experiencing or marked by or causing sadness or sorrow or discontent.
```

- **Which sense.** `RANK $2 river` keeps the definitions that share the most words with "river",
  counting the usage examples in quotes. Ties keep WordNet's order (most common sense first). If nothing
  matches, it tries the corpus's own synonyms of the cue word once, then keeps every sense. The hexagram's
  "tell me more" note uses the same ranking (`corpus.rank_by_overlap`).
- **Soft matching.** When a word isn't a subject as written, `FIND` tries, in order: another form of it
  (happier → happy, biggest → big), a word the corpus lists it under (joyful → *another word for* happy,
  through the reverse index), and one spelling slip, but only when exactly one word is that close
  (colour → color). It **says which** ("I think you mean color.", "I don't have joyful on its own, but my
  corpus lists it as another word for happy.") and it can only land on an entity that exists.
- **"A kind of"** follows the curated IS_A and WordNet's HYPERNYM together (dog → canine → carnivore → mammal
  → animal), and the computer says each step. Synonyms and opposites are *not* followed onward, because a synonym
  of a synonym can be a different sense, so "is sad a synonym of happy" can't turn into a yes.
- **Follow-ups.** The computer keeps the last plan, the word it was about and the names it answered with
  (`planner.Context`). "Another one" re-runs the plan with `EXCEPT` for what was already said, and says
  "That's all the other words my corpus has for happy" when there's nothing left. "What about sad" runs the
  same plan on a new word. "And the opposite?" or "what does that mean" switch the relation. Right after
  "what does bank mean", "what about money?" picks a sense. "Tell me more" after a knowledge answer is about
  that subject; after a reading, it's still about the hexagram. Context only holds plans and names, so every
  fact still comes from running a plan.

`navigator = "auto"` (the default) gives the Navigator the question shapes above and leaves
simple lookups to the old path, which still answers if the Navigator can't. `"always"` and
`"off"` are there too. To see how any answer was reached:

```sh
python3 -m computer --plan "which is older, the Eiffel Tower or the Brandenburg Gate"
```

### Teaching a small model to plan (phases 3–4)

The rule planner is deterministic and needs no model. It is also the *teacher*. A small model
can learn the same plan language and handle phrasings the rules don't know:

```sh
# 1. training data from your corpus: every example's plan is executed, kept only if it reaches its answer
#    (dictionary questions are included: --lexical 0.5 for fewer, 0 for none)
python3 tools/make_navigator_data.py --corpus data --subjects 20000 --out navdata
#    (add --paraphrase http://host:8080 to have a bigger model reword the questions)

# 2. fine-tune SmolLM2-135M on a laptop/desktop (pip install torch transformers)
python3 tools/train_navigator.py --data navdata --base HuggingFaceTB/SmolLM2-135M-Instruct --out navigator-135m

# 3. convert to GGUF, serve with llama-server (see the top of tools/train_navigator.py), then
python3 tools/eval_navigator.py --corpus data --examples navdata/test.jsonl --lm http://127.0.0.1:8081
```

Then set `navigator_planner = "lm"`. The model's plans are generated under a grammar (llama-server's
`grammar` field), so they always parse. They are still validated, and if one is invalid or finds
nothing, the rules take over.

### The model proposes, the Navigator checks (`computer/proposer.py`)

With `navigator_planner = "propose"`, the model writes the plan **and** the sentence it wants to say:

```
$1 = FIND germany
$2 = GET $1 CAPITAL
$3 = GET $2 POPULATION
ANSWER $3
SAY: Berlin is Germany's capital, and 3,800,000 people live there.
```

The executor runs the plan, then `check()` holds the sentence to what it found. The sentence is
spoken only if:

- the plan reached an answer,
- it actually says that answer (the value, the winner, the count, the yes),
- every number and every Capitalised Name in it is in the evidence or the question,
- it has no negation, no prediction and no markup, and is short.

Otherwise the computer says the executor's own template sentence. A model plan that finds nothing never
gets voiced; the rules answer instead. This is house rule 4 ("a voice, not a source") applied to
knowledge answers.

| Proposed | Verdict |
|---|---|
| "Berlin is Germany's capital, and 3,800,000 people live there." | spoken |
| "Berlin has 4 million people!" | template: number not in the evidence |
| "France's capital is Lyon, with 2,100,000 people." | template: Lyon isn't in the evidence |
| "Saturn is bigger than Jupiter." | template: wrong way round |
| "Jupiter is not bigger than Saturn." | template: negation |
| "Jupiter's capital is Io." | template: the corpus doesn't support an answer |

`navigator_planner = "direct"` is the distillation step. A model trained to answer from its
weights writes only `SAY: …`, and that sentence is checked against the rule planner's run of the
same question. If the rules can't ground it, it's dropped.

Training data for both comes from the same generator:

```sh
python3 tools/make_navigator_data.py --corpus data --format propose --out navdata-propose
python3 tools/make_navigator_data.py --corpus data --format direct  --out navdata-direct
#   add --paraphrase http://host:8080 for model-worded answers; each re-wording is kept only if it passes check()
python3 tools/eval_navigator.py --corpus data --examples navdata-propose/test.jsonl --lm http://127.0.0.1:8081 --propose
```

The eval reports accuracy and also how often the model's own words passed the check.

**What the check can't catch.** It works at the level of numbers, names and the final answer.
It can't tell whether a true number is attached to the right thing. "France's capital is Paris,
and France has 2,100,000 people" uses only evidenced numbers and names, says the answer, and
passes. Training on the executor's own sentences (or on checked paraphrases) makes this rare, but
it isn't impossible. Treat "propose" as more fluent and nearly as safe, not as equally safe.
Leave it on "rules" or "lm" if that difference matters.

**Distillation and the corpus.** A distilled model saves time per answer, not RAM: the shards are
read from the SD card as needed, and only the ~15 MB subject list is held in memory. The corpus
also can't shrink while it is what checks the model. The house rules mean every spoken fact needs
something to check it against.

### Is it better? (phase 6)

`tools/eval_navigator.py` asks the same questions of the old lookup and the Navigator, on the same
corpus, and scores right / wrong / abstained. On the small test corpus (`tests/mini_corpus.py`) with
the 27 hand-written questions in `tests/navigator_questions.jsonl`:

| | old lookup | Navigator (rules) |
|---|---|---|
| right | 8 / 27 | 27 / 27 |
| said something that wasn't the answer | 10 | 0 |

Dictionary questions on the same small corpus (its WordNet-shaped words), before and after adding
words to the Navigator:

| | old lookup | Navigator before | Navigator now |
|---|---|---|---|
| `tests/lexical_questions.jsonl` (37, written alongside the rules) | 15 right, 10 wrong | 12 right, 5 wrong | 37 right, 0 wrong |
| `tests/lexical_paraphrases.jsonl` (30, written afterwards, before looking) | 10 right, 7 wrong | 8 right, 1 wrong | first run 23 right, 0 wrong; now 30 |

The paraphrase set was a blind test only on its first run. The misses there (politeness openers like
"can you think of", "like" as a sense cue, "a fancy word for", a four-letter typo, "what's a puppy" for a
word with only a dictionary entry) were then fixed, and one gold answer was wrong (the corpus really has no
opposite for glad), so it now measures regressions, not generalisation.

That corpus is tiny and the rules were written alongside those questions, so it shows that the
machinery works, **not** how well it does on your 1.46M facts. Run the evaluation on your own
`data/`, and treat template-made questions as optimistic for the rule planner. Paraphrased or
hand-written questions are the fair test.

### The learned controller (`computer/controller.py`)

A small neural network that chooses **how** to answer, never **what**. It doesn't memorise the corpus. It
learns which kind of plan (an *action*: hop, chain, compare, count, reverse, test, define, opposite …, 24 in
all) fits the *shape* of a question, including shapes the rules have no pattern for:

```
"jupiter vs saturn, which is heavier"   rules: no pattern     network: compare_more 0.97
   $1 = FIND jupiter   $2 = FIND saturn   $3 = GET $1 ~heavy   $4 = GET $2 ~heavy   $5 = COMPARE $3 $4 MORE heavier
🦉 Jupiter's mass is 1.898e27 kilograms, and Saturn's is 5.683e26 kilograms, so Jupiter is heavier.
```

It runs **after** the rules, so anything the rules answer is answered exactly as before. About 200 KB of
weights, plain Python at run time (no numpy, no model server). It takes about 0.3 ms per question on my test
machine. I haven't measured it on a Zero 2 W, but expect a few milliseconds there.

How it stays inside the house rules:

1. **The network only picks an action.** Code fills its slots with names the corpus really has, the
   validator checks the plan, and the executor is still the only thing that reads facts.
2. **`accept()` checks every answer from a learned plan**, deterministically, and it can only say no:
   - every name in the question has to be in the evidence ("spain's population of birds" doesn't come out as Spain's population),
   - one step can't mix two relations that different words asked for ("population of the capital of spain" read as one hop),
   - a dictionary question has to get what it asked for ("the opposite of another word for happy" needs both).

   If `accept()` rejects an answer, the computer doesn't say it, and the next plan is tried.

**How it learns.** It uses both kinds of learning:

- *From examples:* the rule planner is the teacher (`computer/navdata.py` examples, each one executed and checked),
  plus any questions you write with their answers (`--mine my_questions.jsonl`).
- *From exploring the corpus:* each teacher question is re-worded ("owl diet", "{a} or {b}: which is heavier").
  The re-wordings keep the answer but not the plan. CDOS then tries **every** action on every question and
  runs each one. The actions that reach the known answer in the evidence and pass `accept()` become the
  training target. Nobody labels those questions: the corpus and the checker do. When several actions work,
  the simplest one is the label. "Answered" isn't enough on its own, because "France is a country" contains
  "franc" but doesn't answer the currency question.

The network then learns to predict what that search would have found, so at run time the computer tries one or two
plans instead of all 24.

```sh
python3 tools/train_controller.py --corpus data --out controller.json --heldout my_questions.jsonl
# then in config.toml:
navigator_controller = "controller.json"
navigator_controller_min_p = 0.08     # raise it for fewer, surer answers
```

**Results on the small test corpus.** `tests/controller_heldout.jsonl` has 42 casual or telegraphic questions.
I wrote them before I wrote the controller, and the controller is never trained on them.

| | right | wrong | abstained | plans run per question |
|---|---|---|---|---|
| rules alone | 11 | 0 | 31 | 0.3 |
| rules + controller, **first run** | 34 | 3 | 5 | 1.5 |
| rules + controller, now | 32 | 4 | 6 | 1.3 |
| rules + *every* action, untrained | 31 | 10 | 1 | 6.3 |

The existing sets (`navigator_questions`, `lexical_questions`, `lexical_paraphrases`) are unchanged at 27/27,
37/37 and 30/30, all with 0 wrong.

The last row is what the training buys. Trying every action and keeping the first one that answers finds
nearly as much, but it says 10 wrong things and runs 5× the plans. The network knows that "anything orbiting
saturn" means the things that orbit Saturn, not what Saturn orbits. Mostly, anyway.

Read this table carefully:

- **The controller adds wrong answers where the rules had none.** Each one is a true fact from the corpus that
  answers a different question: "anything orbiting saturn?" gets "Saturn orbits Sun", and "whale breathing - how
  does it work" gets the dictionary definition of whale. `accept()` catches invented names, ignored names and
  mixed relations. It can't catch the right facts picked for the wrong reading. If that matters, raise
  `navigator_controller_min_p` or leave the controller off.
- After the first run I fixed two label bugs (a count was counted as answering "owl diet", and ties went to
  the wrong action), so the "now" row was no longer blind. I made no changes based on the specific questions
  in the set.
- The test corpus has 48 subjects. These numbers show that the machinery works, not how it does on your 1.46M
  facts. On the real corpus nearly every English word is a dictionary entry, so more words will look like names.
  The slot filler orders them sensibly and `accept()` is strict, but train on your `data/` and run
  `--heldout` on questions you wrote yourself before you trust it.
- Exploration costs one executor run per action per question. Start with `--subjects 3000` on the real corpus
  and see how long it takes.

### Words for relations, and which way round (`computer/lexicon.py`, `controller.roles`)

**The relation lexicon** learns which words mean which relation from the corpus's own dictionary. It never
changes the corpus: the mappings live in `lexicon.json`, each one with its confidence and the facts it rests
on, and deleting the file forgets everything it learned.

```
"What is the food of a blue whale?"   "What does a blue whale eat?"   "What is a blue whale's diet?"
   $2 = GET $1 ~food -> KRILL  (took 'food' as DIET (definition head, 0.80))
🦉 Blue Whale's diet is krill.        all three: one fact, BLUE_WHALE DIET KRILL
```

- **Proposals** come from two places. The first is the corpus's dictionary entries for the words a relation is
  named with: their synonyms (LANGUAGE → *tongue*, HEIGHT → *tallness*) and the head word of each definition
  (DIET: "the usual **food** and drink consumed by an organism"). The second is questions you give it with
  their answers (`--mine`). For "grub of an owl" = mice, the relation whose value *is* the answer is DIET,
  so the left-over word *grub* is proposed for DIET.
- **Validation** is CDOS checking each proposal against the knowledge graph, and every decision is recorded:
  - A definition counts only if it's the sense the relation is used in. Its words have to overlap what the
    relation is really about. DIET's subjects are owls and whales, which are animals and organisms. "A
    prescribed selection of foods" mentions neither, so *selection* is rejected and *food* is kept.
  - A word that already names a relation, or is a known alias, isn't learned.
  - A word proposed for two relations is marked *ambiguous*. It's used only when the thing asked about has
    just one of them.
- **Use.** The executor consults the lexicon only when a relation's own words found nothing, and only for
  relations the thing really has. So "food of france" still gets "my corpus doesn't say". The trace and
  `Result.mapped` show which mapping was used and why.

```sh
python3 tools/learn_lexicon.py --corpus data --out lexicon.json --show     # every proposal and the verdict
# config.toml:  relation_lexicon = "lexicon.json"
```

**Direction.** "What orbits Saturn" and "what Saturn orbits" use the same relation the other way round.
`roles()` reads it from word order and keeps it explicit:

| | Saturn is the | so the plan must |
|---|---|---|
| "anything orbiting saturn", "what eats krill" | object | `WHO` (what points at it) |
| "titan orbits what", "what does ganymede orbit" | subject | `GET` (its own facts) |
| "what is saturn orbited by" (passive) | object | `WHO` |
| "the orbit of titan", "spoken in france" | subject | `GET` |

`accept()` rejects a learned plan that goes the wrong way. A reverse lookup that found nothing and fell back
to the thing's own relation also counts as going the wrong way, so "things that orbit ganymede" no longer
comes out as "Ganymede orbits Jupiter". The roles are also features for the network. FIND can now start
from a name that exists only as a value (krill), so "what eats krill" has somewhere to begin.

**Unseen wordings** (`tests/lexicon_heldout.jsonl`): 28 questions written before the lexicon and direction code,
with gold set by what's true in the corpus, not by what I expected to work. None of these questions are
used to learn the lexicon.

| | right | wrong | abstained |
|---|---|---|---|
| rules, before | 12 | 0 | 16 |
| rules + controller, before | 13 | 3 | 12 |
| rules + lexicon | 18 | 0 | 10 |
| rules + lexicon + controller, first run | 25 | 0 | 3 |
| … now | 26 | 0 | 2 |
| rules + lexicon + every action, untrained | 20 | 7 | 1 |

Between the first run and now, I made one change: a hop that finds nothing tries inheritance next, which
fixed "environment of whales" (marine mammals live in the ocean). The two that are still unanswered are
"seat of government of germany" and "nourishment of an owl". The dictionary entries for capital and
nourishment don't link them to those relations, so the computer abstains, which is right. The earlier
`controller_heldout.jsonl` set went from 32/4/6 to 38/1/3 with the lexicon, and the three hand-written sets
are unchanged.

Limits:
- **Head words are guessed without a part-of-speech tagger.** "The metal or paper medium of exchange" gives
  *metal* and *medium*. Here the sense check rejected both, but on 1.46M facts some odd mappings will get
  through. Read `--show` before you switch the lexicon on.
- **Roles come from word order**, which covers English questions like those above. A sentence like "is saturn
  what titan orbits" will confuse it.

### On the real corpus (Phase 1.5 `corpus.pf`, 1.46M rows)

**Building `data/` from `corpus.pf`.** `tools/build_shards.py` writes the shard format the computer reads, in about
20 seconds. It never writes to `corpus.pf`.

```sh
python3 tools/build_shards.py corpus.pf --out data --fix-wordnet --fix-wikidata --fix-keys
```

`--fix-wordnet` repairs the WordNet import, which has two labels swapped and stores synset ids where words should be.
I checked this by hand on dog, owl, food, happy, big and hot:

| corpus.pf says | it actually is | e.g. | rows |
|---|---|---|---|
| `ANTONYM` | the hypernym (what kind of thing) | DOG → canine, domestic animal | 153,895 |
| `HYPERNYM` | the hyponym (kinds of it) | DOG → puppy, pooch, mutt | 189,011 |
| `WORDNET_POINTER_!` | the real antonym | HAPPY → unhappy | 12,059 |
| `WORDNET_POINTER_&` | similar-to | HAPPY → felicitous | 33,977 |

Values like `WN_n_01286456` become that synset's first word (679,485 rows). The other `WORDNET_POINTER_x` labels are
renamed after their WordNet symbol (`;C` → `WN_TOPIC_DOMAIN`, …); those weren't checked by hand. Before the repair,
"what is the opposite of happy" got "no opposite" and "kinds of dog" got synset ids. After it, they get "unhappy" and
"puppy, pooch, cur, …". The importer that wrote `corpus.pf` is probably worth fixing at the source.

**Half the corpus was out of reach** (found when running the learning experiment on the real `corpus.pf`). The computer
looks every subject up by an upper-case key (`YEMEN`), and the builder stored subjects as written (`Yemen`). So the
72,800 subjects written in mixed or lower case, nearly all of the Wikidata and DBpedia rows, were in the shards but
could never be found. "What is the capital of Yemen" got "I don't know". cdos-web's `corpus.js` uses the same keys
(this is a port of it), so it probably has the same blind spot: worth checking there.

Those rows had a second problem: 69,125 values were bare Wikidata ids (`France CAPITAL Q90`). Two build options fix
both, and like `--fix-wordnet` they never write to `corpus.pf`:

| option | what it does | rows on Phase 1.5 |
|---|---|---|
| `--fix-keys` | stores every subject under its lookup key; a thing in the corpus twice (`FRANCE` curated, `France` from Wikidata) becomes one subject with both sets of facts | 449,986 rows re-keyed |
| `--fix-wikidata` | every Wikidata row names its own subject's id in `source_id` (`Paris … Q90/P2046`), so ids resolve to the corpus's own names: `Q90` → Paris. Five common unit ids are named (yottagram, solar mass, Jupiter mass, light-year, square mile) | 53,232 values and 100 subjects resolved; 14,905 values and 909 subjects pointing at things never imported (Q4, "death") dropped, since the computer could only ever say the id; 43 units named |

After both: "Yemen's capitals are Sanaa (since 1990) and Aden.", "Bolivia's highest point is Nevado Sajama.",
"Albert Einstein's occupation is theoretical physicist." Dated facts keep their dates (France's capital in 1875 is
Versailles).

What it changed on the 40 real held-out questions (rules planner, lexicon learned with the held-out guard):

| build | rules: right / wrong / abstained | + lexicon | unanswerable admitted |
|---|---|---|---|
| `--fix-wordnet` (as before) | 19 / 1 / 9 | 24 / 1 / 4 | 11 of 11 |
| `+ --fix-wikidata --fix-keys` | 19 / 1 / 9 | 24 / 1 / 4 | 11 of 11 |

The score is the same: those questions were written against the curated rows. The answers are fuller, though:
"Mexico's official languages are Spanish, Nahuatl and Yucatec Maya." (was "Spanish"). Two things needed care:

- **Words that are also things.** With Wikidata visible, "home" is a thing (it has facts) as well as the word for
  HABITAT. So "what is the home of a tiger" names two things, and the lexicon stopped learning from it. Now
  (`lexicon.about`), when one of the two holds the known answer in its facts (TIGER HABITAT FOREST), that one is
  what the question is about, and "home" is a word again. The held-out guard uses the same rule.
- **More relations, more wrong mappings.** On the fixed build, the dictionary step accepts 35 mappings instead of 7,
  because Wikidata's relations (CAUSE, AUTHOR, ATTRIBUTE …) now have facts. By my reading, about 10 of the 28 new
  ones are right (fatherland → COUNTRY_OF_ORIGIN, writer → AUTHOR, tightness → DENSITY). Most are wrong senses:
  wealth → CAPITAL, lawsuit / suit / case → CAUSE, status → OFFICIAL_LANGUAGE, order → ATOMIC_NUMBER. None changed
  a held-out answer, but "what is the wealth of france" would be read as its capital. **On the fixed build, read
  `--show` and delete the bad mappings from `lexicon.json` before you switch it on.**

Senses are kept: each OEWN row's synset is stored as `SENSE=…`, so "dog" the animal can be told apart from "dog" the
frump. The navigator doesn't use senses yet: "what kind of thing is a dog" still lists every sense's hypernyms,
including sausage and villain.

**What the lexicon learned, and how often it's right.** I marked the accepted mappings by hand (my judgement,
not yours: please check `review.jsonl`). The question I asked for each was: would someone asking about a thing
with this word mean this relation?

| version | accepted | right |
|---|---|---|
| first: synonyms of every word in a relation's name, any sense | 580 | 1 of a random 60 (2%) |
| only the head word; only noun senses; sense must fit the curated taxonomy; synonyms only from that sense | 21 | 3 of 21 (14%) |
| also: no head word of a noun-noun compound ("conversion factor" isn't a factor), first conjunct only ("food and drink" is food), type words only in the sense check | 5 | 3 of 5 (60%) |
| an extra tier, WordNet's "first" noun sense when nothing fits (tried, measured, turned off) | +36 | 4 of 36 (11%) |
| plus examples (`tests/real_mine.jsonl`) | 7 (+2 ambiguous) | 5 of 7 accepted |

The first-sense tier failed because this corpus lists senses in synset-id order, not commonest first:
OCCUPATION's "first" sense is "the act of occupying".

What it accepts now: food → DIET, environment → HABITAT, altitude → HEIGHT, and from examples home → HABITAT and
grub → DIET. Still wrong: envelope → ATMOSPHERE, set → GROUP (the mathematical group; GROUP here is the periodic
table's). "Tongue" is marked **ambiguous** between OFFICIAL_LANGUAGE and CURRENCY. The examples really can't tell
them apart (Russian is in "Russian ruble"), and since every country has both, the computer won't pick one.

**The confidence scores aren't calibrated.** By the hand marks, 0.8 was right 2 of 4 times and 0.6 was right 2 of 3.
With samples this small, decide by the measured precision, not the score. `tools/calibrate_lexicon.py` reports it by
kind of evidence and confidence band, with 95% intervals:

```sh
python3 tools/learn_lexicon.py --corpus data --out lexicon.json --mine mine.jsonl --heldout test.jsonl --review-out review.jsonl
# mark "correct": true/false in review.jsonl
python3 tools/calibrate_lexicon.py review.jsonl
```

**Accidental learning, and keeping the test fair.**
- `--heldout` drops any example that repeats a held-out question or asks about the same *thing* (a curated subject,
  not a dictionary word like "use").
- A word needs examples about at least two different things. "landmass of france" = Europe is CONTINENT *and*
  LOCATED_IN, and one example can't tell which.
- A word the question already explains isn't evidence. In "what money does france use", "money" already says
  CURRENCY, so "use" isn't learned. (Before this rule it was.)
- Answers match on whole words: "euro" is not in "europe".
- `tools/eval_lexicon.py` checks that no held-out question is about a thing an example-learned mapping rests on.

**Held-out questions on the real corpus** (`tests/real_heldout.jsonl`, 40 questions written before the lexicon was
learned from this corpus, with gold taken from `corpus.pf`; rules planner, no controller):

| | answerable (29): right | wrong | abstained | can't be answered (11): admitted | wrong |
|---|---|---|---|---|---|
| rules | 19 | 1 | 9 | 11 | 0 |
| + lexicon from the dictionary | 23 | 1 | 5 | 11 | 0 |
| + lexicon from dictionary and examples | 24 | 1 | 4 | 11 | 0 |

The leak check passed: 0 held-out questions are about the 8 things behind the example-learned mappings. The one
wrong answer is a data gap. "How many satellites does neptune have" gets "my corpus lists 1: Triton" because the
corpus names only Triton, while MOON_COUNT says 16. On the first run, "what orbits venus" came out as "Venus orbits
Sun" (the reverse lookup fell back to Venus's own fact). The rule planner now gets the same direction check as the
controller, and that question is admitted as unknown.

Still unanswered: "where does a kangaroo live" (*live* isn't linked to HABITAT), "the chief city of canada" (two-word
phrases aren't learned), "anything orbiting jupiter?" and "deimos orbits what" (no rule pattern; the controller
handles these shapes, but it isn't in this table).

**The controller on the real corpus.** Trained on the full corpus (600 subjects, 3,072 questions with re-wordings;
70 s, because it keeps every shard in memory while training). Same 40 questions, with the lexicon:

| | right | wrong | abstained | plans per question |
|---|---|---|---|---|
| rules | 35 | 1 | 4 | 0.9 |
| rules + controller, first run | 34 | 4 | 2 | 1.9 |
| rules + controller, now | 36 | 1 | 3 | 2.0 |
| rules + every action, untrained | 30 | 9 | 1 | 8.0 |

On the first run, all three new wrong answers were dictionary answers to questions that weren't about words. "What
language does a wolf speak" got the definition of wolf. On a full corpus every word has a definition, so that answer
is always available. Now a dictionary answer from a learned plan needs a question about the word: every other content
word has to be a dictionary cue ("mean", "opposite", "another word"…). The fix came after seeing the results, so the
"now" row isn't blind. On the small corpus it costs one answer on `controller_heldout.jsonl` (38/1/3 → 37/1/4).
**On the real corpus the controller adds one right answer ("deimos orbits what") over the rules alone.** Most of what
it did on the small corpus, the lexicon now does through the rules. Keep it off until it does more on questions you wrote.

Running the lexicon learner on the full corpus takes about 15 s and ~1 GB of memory. That's done offline. On the Pi
the computer only loads `lexicon.json`, and uses about 45 MB with the full corpus.

### Reasoning: more than true and false (`computer/logic.py`, `computer/rules.py`, `computer/data/rules.json`)

A yes/no question has four possible answers, not two:

| | when | the computer says |
|---|---|---|
| `TRUE` | the corpus links them | "Yes. Titan orbits Saturn. Saturn orbits Sun." |
| `FALSE` | a **knowledge rule** rules it out | "No. Shark is a fish. Nothing is both a fish and a mammal." |
| `UNKNOWN` | no evidence either way | "I can't find that in my corpus: nothing there links it to tower." |
| `CONFLICT` | the evidence supports both sides | "My corpus disagrees with itself here. … So I won't pick one." |

Relevance and confidence scores are kept separate from truth. A fact ranked 0.9 relevant isn't "90% true".

**Knowledge rules** are stated assumptions in `computer/data/rules.json`, each with an id and a reason:

- `disjoint`: classes nothing belongs to two of (mammal / bird / fish / reptile / amphibian / insect; animal / plant;
  star / planet / moon). These follow only the curated `IS_A`, not WordNet, whose odd senses ("dog" the frump) would
  clash with everything.
- `complete`: relations the corpus lists in full. With `ORBITS` (max 1), "does Titan orbit Jupiter" is *No. Titan orbits
  Saturn*, while "does Titan orbit a star" is *Yes: Titan orbits Saturn, Saturn orbits the Sun, the Sun is a star*. A
  thing with two `ORBITS` is a CONFLICT. `ORBITS` held on every subject in the Phase 1.5 corpus.

  `CAPITAL` was built in at first and has been **taken out**. On the real corpus, "is the capital of South Africa Cape
  Town" came out as *No. South Africa's capital is Pretoria.*: South Africa has three capitals and the corpus lists
  one. Now that question gets the plain fact. A `rules_file` can add `{"rule_id": "complete.capital", "relation":
  "CAPITAL"}` for a corpus you've checked. A completeness rule is only as true as the data's completeness, so add one
  only after checking the cases most likely to break it.

  These rules apply only when the question asked about that one relation. A plan that collected *every* relation
  proves nothing about any one of them. (The first version got this wrong: the controller's plan for "food of france"
  came out as "No. France's capital is Paris." The regression run caught it.)

Without the rules (an empty `knowledge` section), every test is TRUE or UNKNOWN, exactly as before.

**and / or / neither-nor / not.** The plan language has a `LOGIC` step that combines tests in four-valued logic. One
FALSE settles an AND and one TRUE settles an OR. Otherwise CONFLICT outranks UNKNOWN.

```
"is a whale a mammal or a fish"
   $3 = TEST $2 mammal   -> yes
   $4 = TEST $2 fish     -> no (disjoint.animal_classes)
   $5 = LOGIC OR $3 $4   -> true
🦉 Yes, at least one. Mammal: yes. Whale is a marine mammal. Marine mammal is a mammal. Fish: no. Nothing is both a mammal and a fish.
```

The rule planner also reads "is the capital of france berlin" and "does titan orbit jupiter" as tests. When a plan
finds a CONFLICT, `navigate()` stops there, so a later reading can't cover it up with a yes.

**Pathways as data.** The order of the knowledge path is no longer hard-coded in `core.py`. It's a list of rules in
the same file: navigate, then the lookup, then the health or money line, then the navigator's backup, then "I don't know".

```json
{"rule_id": "knowledge.conflict_is_said",
 "when": {"all": [{"equals": ["stage", "navigated"]}, {"equals": ["nav_status", "conflict"]}]},
 "then": {"action": "say_navigator"}, "priority": 95,
 "why": "the corpus disagrees with itself: say so, and don't let the lookup pick a side"}
```

- Conditions: `all`, `any`, `not`, `none` (NOR), `equals`, `in`, `greater_than`, `less_than`, `contains`, `exists`,
  `fact`. A key the state doesn't have is UNKNOWN, not false. A rule has `then` (TRUE), optional `else` (FALSE) and
  optional `unknown` (UNKNOWN or CONFLICT) branches.
- **A rule can only pick one of the computer's own actions** (`PATHWAY_ACTIONS` in `core.py`) with the arguments each one
  takes. Anything else is refused when the computer starts, whoever or whatever wrote the rule.
- It's bounded (8 actions per question, 200 rules per decision) and traced. `-v` shows every rule that fired and why.
- `python3 -m computer.rules [file]` checks a rules file. `rules_file = "my_rules.json"` in `config.toml` uses one. If it's
  broken, the computer says so in `--check` and uses the built-in rules.

**Did it break anything?** The same five question sets were run on the test corpus before and after, with the rules
planner alone and with the trained controller:

| | before: right / wrong / abstained | after |
|---|---|---|
| navigator_questions (rules, +controller) | 27/0/0, 27/0/0 | 27/0/0, 27/0/0 |
| lexical_questions | 37/0/0, 37/0/0 | 37/0/0, 37/0/0 |
| lexical_paraphrases | 30/0/0, 30/0/0 | 30/0/0, 30/0/0 |
| controller_heldout | 11/0/31, 36/1/5 | 11/0/31, 36/1/5 |
| lexicon_heldout (without a lexicon) | 12/0/16, 18/0/10 | 12/0/16, 18/0/10 |

The only answers that changed were the shark questions, from "can't find a link" to a reasoned "no". One gold label
changed **after** seeing the results: `controller_heldout.jsonl` line 23 ("is it true that sharks are mammals") was
`test`, where any committed answer counts as wrong, because when it was written the computer couldn't say "no". It's now
`test_no` (any "yes" is wrong). The boolean and functional questions in `tests/test_rules.py` were written alongside the
code. They show the machinery works, not how well it generalises. Write some of your own before you trust it.

### Learning from the corpus (`computer/navdata.py`, `tools/experiment_learning.py`)

Three things get called "training", and they aren't interchangeable:

| | what it teaches | here |
|---|---|---|
| corpus growth | more facts, definitions, sources | `data/`, `tools/build_shards.py` |
| knowledge construction | relations, rules, derived facts | `computer/data/rules.json`, the lexicon, inheritance |
| model training | how to read a question, which plan to try, how to say it | the controller, the SmolLM planner |

The corpus teaches the models about itself. The generator walks the corpus, writes questions, runs each plan, and
keeps an example only if the executor's answer is right. Each example records:

| field | what it holds |
|---|---|
| `question`, `plan` | the training input and target |
| `evidence` | each fact used: subject, relation, value, unit, source, confidence, and `derived`, which is empty for the thing's own fact or the IS_A path it was inherited through (`WHALE>MARINE_MAMMAL>MAMMAL`) |
| `rules` | the knowledge rules a "no" rests on |
| `steps` | every plan step and what it found (the inference) |
| `final`, `truth` | the answer itself |
| `answer` | the computer's sentence, which says why the answer follows |
| `concepts` | every corpus thing it touches |

There are new example kinds for the reasoning above: `test_false` ("is shark a mammal" → *No.*), `logic`
(and / or / neither-nor / not), and `rel_test` ("does titan orbit jupiter"). Their answer heads are all different
("Yes, at least one." isn't "Yes."), so a plain test can't pass for an or-question. There are about as many as the
plain yes questions, because more crowded out the other kinds (see below).

**Held-out things, not just held-out questions.** Splits were already by subject. Now `leak_free()` also drops any
training example that *mentions* a held-out subject anywhere: as the subject, as a class tested against, or in its
evidence. On the test corpus that drops about a third of the training questions, because a held-out "marine mammal"
is also a class many other questions use. That's the price of a fair test. Before this, `train_controller.py` split
train and dev by *question text*, so a re-wording of a training question could land in dev. Its dev accuracy now
reads 79% on the test corpus, and earlier dev figures were optimistic.

**The experiment.** `tools/experiment_learning.py` trains the controller only on what the corpus generated about
itself, using 25%, 50% and all of the training subjects. It then scores it on questions it never saw:

| set | what it tests |
|---|---|
| A | unseen things, the generator's own phrasings |
| B | unseen things, **unseen phrasings** (the re-word templates are split in two; the second half is never trained on) |
| C | seen things, unseen phrasings |
| D | hand-written questions (`--heldout`), never generated |

```sh
python3 tools/experiment_learning.py --corpus data --subjects 3000 \
    --heldout tests/real_heldout.jsonl --heldout my_questions.jsonl --out exp.json
```

On the test corpus (59 subjects, so this shows that the experiment works, not how well learning does on 1.46M facts),
with right / wrong / abstained:

| | B unseen things + phrasings (127) | C unseen phrasings (73) | D controller_heldout (42) | D lexicon_heldout (28) |
|---|---|---|---|---|
| rules alone | 46 / 1 / 80 | 32 / 2 / 39 | 11 / 0 / 31 | 12 / 0 / 16 |
| rules + every action, untrained | 74 / 49 / 4 | 42 / 27 / 4 | 33 / 6 / 3 | 15 / 6 / 7 |
| rules + trained on 25% | 93 / 6 / 28 | 57 / 8 / 8 | 29 / 4 / 9 | 17 / 0 / 11 |
| rules + trained on 100% | 97 / 8 / 22 | 62 / 6 / 5 | 32 / 2 / 8 | 14 / 0 / 14 |

How to read it:

- **Training on the corpus does help on new questions.** On things and phrasings it never saw, the trained
  controller answers about twice as many as the rules alone, with a sixth of the wrong answers that trying every
  action gives.
- **It isn't free.** It says more wrong things than the rules alone (8 against 1 on B; 2 of the 8 come from the rule
  planner, which runs first). The report lists every wrong answer (`wrong_answers` in `--out`), and I read them all.
  None states a false fact, but each is a true fact that answers something else:
  - a neighbouring question: "explain big to me" got big's synonym instead of its definition
  - a different thing: "owl: a kind of animal?" got "Animal is a kind of organism." from a 0.06-confidence guess
  - a yes/no question without the yes: "is it true that ganymede orbits jupiter" got "Ganymede orbits Jupiter."

  The scoring counts all of these as wrong on purpose. Raising `navigator_controller_min_p` cuts the low-confidence ones.
- **More data isn't steadily better here.** lexicon_heldout drops from 17 right at 25% to 14 at 100%. With 33
  training subjects that's noise as much as a trend. Run it on your corpus with `--subjects 3000` or more before
  drawing a curve.
- **D is the fair test**, because A–C come from templates. Write your own questions about things you know the corpus
  has, and add them with `--heldout`.

#### Audit: findable is not the same as correct

Before the fixed build becomes the baseline, I checked separately whether the fixes **lose** facts, **merge**
different things, or **resolve** ids to the wrong thing.

**Lost facts.** I compared every fact in the `--fix-wordnet` build with the fixed build: subject key, relation,
value, unit, dates and WordNet sense. Ids are left out, since they could never be said. No fact disappears. The first
version did lose 400 dictionary facts: deduplication ignored the sense, so a word listed under two senses kept only one.
Deduplication now keeps senses (and units) apart. The only rows that differ are the 43 whose unit id now has a name.
`tests/test_real_build.py` covers both.

**Merged subjects.** These come in three kinds:

| merge | how many | verdict |
|---|---|---|
| curated + Wikidata spelling of one thing (`ARGENTINA` / `Argentina`) | 499 keys | safe: a random 20 were all the same thing |
| dictionary word + Wikidata concept (`home`, `river`) | 2,210 keys | intended (that's how words and things share keys), but it changed how questions read; fixed for lexicon learning (`lexicon.about`) |
| **two different things with one name** (Mars the planet, Q111, and Mars the god, Q112) | 226 names; 3,933 resolved values (7%) land on one | **not fixed.** `corpus.pf` already files both as "Mars"; the fix makes the merge reachable |

Curated facts come first, so "what is mars" still says *Mars is a planet and a terrestrial planet*. But the god's
facts sit on the same subject. The real fix is at the source: the importer should keep the Wikidata id as the thing's
identity and the label as its name.

**Resolved values.** I read a random 45 resolved facts (`Nässjö Municipality LOCATED_IN Jönköping County`,
`Tokyo PART_OF three major cities in Japan` …). All 43 with a named subject point at the right thing. Two are odd
claims Wikidata itself makes (plate tectonics PART_OF lithosphere). The first version's "points at itself" rule
compared names, so it dropped *Mars NAMED_AFTER Mars* (the god) and the computer said Mars was "named after Ares and
fire". It now compares ids, and 178 such facts are kept.

**Still open:**
- Accents aren't folded, so "Brazil's capitals are Brasilia and Brasília".
- Historical facts with no dates in the source read as current: *Caracas LOCATED_IN Venezuela Province*, a province
  of the Spanish Empire.
- The 2,210 word-and-thing merges change how the question reader sees everyday words. `lexicon.about` handles it for
  learning, but not for every reading.

**Defaults, as the code has them** (`computer/config.py`): the navigator uses the rule planner; the learned controller,
the relation lexicon and every model are off; compute is "balanced". The knowledge rules are on (three disjoint-class
groups, and ORBITS), because each held across the whole real corpus.

The real-corpus experiment numbers below were measured before the sense, unit, self-reference and CAPITAL changes.
On the 40 held-out questions, the rebuilt corpus scores the same: 19 / 1 / 9, and 24 / 1 / 4 with the lexicon.

#### On your corpus (Phase 1.5 `corpus.pf`, 1.46M rows)

Same experiment, 3,000 subjects sampled, your 40 hand-written `tests/real_heldout.jsonl` questions as set D, lexicon
learned with the held-out guard. Each run takes 4–16 minutes and about 1.1 GB of memory.

**Set D, the fair test** (right / wrong / abstained):

| | rules alone | untrained, every action | trained on 25% | trained on 100% |
|---|---|---|---|---|
| first run, original build | 35 / 1 / 4 | 30 / 9 / 1 | 36 / 1 / 3 | 34 / 3 / 3 |
| after the fixes below, original build | 35 / 1 / 4 | 34 / 4 / 2 | 36 / 1 / 3 | 36 / 1 / 3 |
| after the fixes, `--fix-wikidata --fix-keys` build | 35 / 1 / 4 | 33 / 5 / 2 | 36 / 1 / 3 | 36 / 1 / 3 |

Learning adds one right answer on your questions and no wrong ones. The one wrong answer for every system is the
known Neptune data gap. Forty questions can't show more than that: **this needs a few hundred hand-written questions
before anyone can say how much learning helps.**

**Generated questions, fixed build** (set B: unseen things, unseen phrasings, 3,035 questions):

| | right | wrong | abstained |
|---|---|---|---|
| rules alone | 1,407 | 91 | 1,537 |
| untrained, every action | 2,283 | 352 | 400 |
| trained on 25% | 2,345 | 129 | 561 |
| trained on 100% | 1,983 | 130 | 922 |

The controller answers about 940 more questions than the rules, at the cost of about 40 more wrong ones. Only 39
questions (1.3%) are wrong because of the controller itself. More training made it more cautious (more abstentions),
not more wrong. Before the fixes, its wrong answers grew with the data (135 → 280).

Read sets A–C with care on this corpus. 1.33M of the 1.46M rows are WordNet, so sampling every subject makes about 80%
of the generated questions dictionary questions ("total usage domains of metricate"). `--things` samples only the
15,658 subjects with non-dictionary facts. That run takes over an hour, and it found the multi-word-name bug below.

**What running it on your corpus found and fixed.** None of these showed up on the 59-subject test corpus:

| problem | example | fix |
|---|---|---|
| half the corpus out of reach | "capital of Yemen" → I don't know | `--fix-keys`, `--fix-wikidata` (above) |
| the probability floor was skipped when the top action couldn't be filled | "birdhouse kinds" → its definition, from an action scored 0.00 | only the top action may be tried below the floor |
| "what kind of thing is it" answered questions about something else | "what language does a wolf speak" → "Wolf is a mammal." | that action only when the question asks nothing else |
| a yes/no plan split multi-word names at the first word | "is the capital of cape verde praia" read as *cape* + *verde praia* | longest name first, up to three readings |
| a connective the question never said | "would you call X a Y" read as NOT → "No, it is." | `accept()` checks connectives both ways |
| "alternative word" wasn't a synonym question | "sneer alternative word" → its definition | added to the words that ask for a synonym |
| WordNet's pointer relations in generated questions | "how many also sees for loud" | the generator skips `WN_*` relations |

**What's still wrong.** I read the controller-only mistakes on set B:

- Some are template questions nobody would ask ("integer 315 prime attribute", "metabolize entailment entailment").
- Some are real:
  - "fabrizio donato citizenship currency" gets his citizenship and ignores the currency.
  - "cell type's value?" gets a "Yes" built from a WordNet chain (cell → compartment → cavity → decay).
- The rule planner's own 91 include "define happy's opposite" (it defines happy instead) and counting questions that
  get a list instead of a number.
- `tests/test_computer.py`'s knowledge-aside test fails on this corpus with the original code too: it expects "gas giant"
  and the computer's first fact about Jupiter is "Jupiter is a planet".

**What the experiment caught and fixed.** All three were found by reading the wrong answers, so the rows above aren't blind:

- `accept()` didn't notice "or" or "and" in a question, because the reader files them as cue words. So "sun: star or
  planet?" could be answered by one plain test. It's now refused unless the plan has a `LOGIC` step.
- The controller tried every name as the subject of a yes/no question, so "marine mammal: a kind of whale?" came out
  "Yes. Whale is a marine mammal." Yes/no actions now take the first thing named.
- Adding three times as many logic examples as plain yes questions pushed "reverse" below the controller's probability
  floor ("anything orbiting saturn?" stopped being answered). The new kinds now match the plain yes questions in number.

On the old question sets, the retrained controller with these fixes scores the same as before, except on
controller_heldout. There it went from 36/1/5 to 36/0/6: "different dogs" went from wrong to right, and "list the
countries that border france" went from right to abstained.

**The SmolLM planner.** `tools/make_navigator_data.py` writes the same leak-free examples, including `LOGIC` plans, as
chat rows for `tools/train_navigator.py`. I couldn't fine-tune a model from here: the model download is blocked and
there's no torch. Run it on your laptop or NUC, then `tools/eval_navigator.py` on `navdata/test.jsonl`. The same
question applies: does it beat the rules and the controller on things and phrasings it never saw?

**The terabyte corpus** isn't built yet, and it shouldn't be until these numbers say more data helps. When it is: keep
the indexed facts as they are (only the subject list is in RAM), put documents and passages in a separate store keyed
to the entities they mention, and record each extracted fact with its passage as its source. The `derived` and `rules`
fields above already keep stated facts and conclusions apart.

## Analysis: calculations, estimates and diagnostics (`computer/analysis.py`, `computer/quantity.py`, `CALC`)

The computer can now work things out, not only look them up. The design, measurements and limits are in
[`docs/STARSHIP_UPGRADE.md`](docs/STARSHIP_UPGRADE.md).

```
you: How long can a 50 Wh battery run a 5 W device?
🦉   The estimated runtime is 8.5 hours: 50 watt-hours at 5 watts, assuming 85% conversion efficiency.
you: And at half the load?
🦉   With the load at 2.5 watts, the estimated runtime is 17 hours: 50 watt-hours at 2.5 watts, assuming 85% conversion efficiency.
you: How long can the battery run the Raspberry Pi Zero 2 W?
🦉   My corpus gives Raspberry Pi Zero 2 W's power draw as 2.5 watts. Specify the battery's usable energy capacity.
you: How many times heavier is Jupiter than Saturn?
🦉   Jupiter's mass is 1.898e27 kilograms, and Saturn's is 5.683e26 kilograms, so Jupiter's mass is 3.34 times Saturn's.
you: What is the population density of France?
🦉   I know about France, but my corpus doesn't say its area.
you: What is the processor temperature?
🦉   The processor temperature is 54 degrees Celsius.
```

| What | How |
|---|---|
| estimates from what you say | typed formulas: runtime, daily energy, energy, power. Units are parsed (Wh, mAh with volts, W, A with volts, %, hours a day), including number words for voice |
| missing inputs | asked for one at a time ("Specify the battery's usable energy capacity."), never guessed |
| corpus figures | a named device's figures are used and cited by fact id. Two different figures (idle and load) are both said, and you pick |
| assumptions | only the conversion efficiency has a default (`analysis_efficiency`, 85%), and it's always said. A result that rests on it is an ESTIMATE |
| follow-ups | "half the load", "with a 100 Wh battery", "no losses", "what if the load triples": the last calculation with one variable changed, said as a what-if |
| explanations | "how did you work that out?" gives the formula, each input and where it came from, and the arithmetic |
| arithmetic over facts | `CALC SUM / DIFF / RATIO / DIV` in the plan language: "combined population", "how much taller", "how many times heavier", "population density". Same measurement and compatible units, or it refuses. A calculation it can't do is said, never answered in part |
| diagnostics | "processor temperature", "system status", "how much memory is free", "uptime": read from `/sys`, `/proc` and `meta.json`, or said to be unavailable |
| working state | `state_file = "computer.state.json"` keeps the open calculation across restarts: atomic writes, only when it changed, ignored after `state_max_age_minutes` |

House rules here: every number is stated, from the corpus, a said assumption, or arithmetic on those. Health
and money questions never reach the analyst. The corpus is never written.

**Measured on the test corpus** (`python3 tools/bench_dialogue.py`, right / wrong / abstained): the 53-turn
dialogue set went from 6 / 10 / 37 to 53 / 0 / 0. A 32-turn paraphrase set, written afterwards and scored
blind once, got 28 / 2 / 2 on the first run (32 / 0 / 0 after the fixes it prompted). The five existing sets
score exactly as before, and none of 425 existing replies changed. I wrote both new sets myself, so they show
the machinery works; they don't show how it does on your corpus. Run the bench on your `data/`.

## Capabilities, result envelopes and requirement checks (Milestone 1)

Details, measurements and decisions are in [`docs/MILESTONE_1.md`](docs/MILESTONE_1.md).

**Checked on the production corpus** (M1.1 review, [`docs/REAL_CORPUS_REVIEW.md`](docs/REAL_CORPUS_REVIEW.md)):
integrity verified and the version stable on every load. 73 hand-checked cases and 40 held-out questions were run,
covering WordNet, Wikidata, DBpedia and the curated facts. Eight engine defects were found, fixed and given
regression tests, and the corpus data issues and unsupported query types are listed. To run it on your own data:
`python3 tools/validate_corpus.py data` (read-only) and `python3 tools/review_real_corpus.py --data data`.

- **Every capability declares itself** (`computer/capabilities.py`): inputs, output, error codes, cost, whether it
  is deterministic or writes state, and what runs instead when it's off. The declarations are checked against
  the code when the computer starts, and a bad one stops it. A capability marked deterministic can't read the clock,
  random numbers or the network; the year is a declared input (`computer/clock.py`).
- **Every reply carries a result envelope** (`Reply.envelope`; `python3 -m computer --envelope "question"`). It has
  truth and certainty as separate fields, each fact with its id and the corpus version, the steps and rules,
  assumptions, structured errors, the capabilities used, and a canonical hash that repeats exactly across runs.
- **The corpus version is a hash of the shards' content,** stored by `tools/build_shards.py` and recomputed at
  every start. An answer from a corpus that no longer matches its build record carries a CORPUS_INTEGRITY error,
  and `corpus_integrity = "strict"` refuses to answer from it.
- **Requirement checks:** "does Battery Beta meet 50 Wh?" → *No. Battery Beta's rated charge is 3.5 amp-hours
  and its nominal voltage is 12 volts, which makes 42 watt-hours. That's less than 50 watt-hours.* (FALSE,
  INFERRED). Units are converted exactly. A wrong unit, a missing figure and conflicting figures each get
  their own answer and error, never a guess. A conflict needs a stated `functional` rule in
  `computer/data/rules.json`.

## The neural core (`computer/neural/`)

The learned controller (above) with the parts that make training it safe to run: a versioned feature schema,
training records that keep every candidate plan with its validator and executor outcome, reproducible training into a
staging folder, an evaluation that compares the rules alone with an untrained and the trained network on the same
held-out questions, and activation only with `--approve`. The network and its features are the controller's,
unchanged. The design, the measurements and the limits are in [`docs/NEURAL_CORE.md`](docs/NEURAL_CORE.md).

```sh
python3 -m computer.neural train    --corpus data --registry models/neural          # -> staging/<id>; nothing active changes
python3 -m computer.neural evaluate --corpus data --registry models/neural --model <id> --heldout my_questions.jsonl
python3 -m computer.neural promote  --registry models/neural --model <id> --approve # only if every gate passed
# config.toml:  neural_registry = "models/neural"
```

- The network picks a plan shape. The plan comes from a fixed template, the validator parses it again, the executor
  runs it, and `accept()` checks the answer. A neural plan's result is said only if it answered and passed; otherwise
  the turn goes on exactly as without the model.
- A missing, damaged or incompatible model (another feature schema, another action list, a hash that doesn't match) is
  refused with a reason. The computer starts and answers with the rules, and `--check` says why.
- Every prediction and its checked outcome go to an append-only, hash-chained log (`python3 -m computer.neural log verify`).
- `--preset mini` runs the whole cycle on the test corpus. There, with five training seeds: held-out net (right − wrong)
  is 134.6 trained vs 67.2 untrained and 68 for the rules alone. No right answer is lost, and the whole computer goes
  from 159 right / 51 wrong to 203 / 29 on the held-out sets. That's 59 subjects: run it on your `data/` before you
  trust it.

## Neural Platform 0.2: components, growth, plugins, a simulated robot, services

The first milestone of the self-growing plan, built on the neural core without changing how the computer reasons.
What was built, the acceptance matrix, the measurements and the limits are in
[`docs/PLATFORM_0_2.md`](docs/PLATFORM_0_2.md).

- **One interface for learned parts** (`computer/neural/components.py`). Each component declares a versioned input and
  output (typed fields, units, ranges), and the registry checks both on every call. The reasoning selector, the robot
  policies and plugins all sit behind it, and `computer/neural/registry.py` runs the same staging → evaluate →
  `--approve` lifecycle for each.
- **Growth Engine** (`computer/growth/`). It reads the experience and turn logs, re-asks what wasn't answered, and
  sorts each case: the selector, a missing relation, a corpus gap, or a conflict. It makes tasks; a person answers
  them, and the engine trains and evaluates a candidate. Promotion stays manual, and nothing it does writes `data/`.
- **Plugins** (`plugins/`, `computer/plugins.py`). A folder with `plugin.json` and code adds a capability with no edit
  to the engine. It is checked like a built-in one and refused with a reason if it fails. Example: a TMP117
  thermometer.
- **A simulated robot** (`computer/robotics/`). Observations become state, and a policy proposes a motion. A
  deterministic envelope (`computer/data/robot_safety.json`) checks it along its predicted path. Anything not allowed
  becomes STOP, and every decision is journaled. **Simulation only.**
- **Services** (`computer/services/`, `systemd/`, `tools/install_services.sh`). A local API on a Unix socket, a daily
  growth timer, and the simulated robot as its own process. Code, configuration and state are kept apart, so
  upgrades don't touch your corpus, models or logs.

```sh
python3 -m computer.growth discover            # what couldn't be answered, and tasks for it
python3 -m computer.growth show <task>         # answer one: ... answer <task> --question "..." --gold ... --by "You"
python3 -m computer.growth run <task>          # staged + evaluated candidate; an operator promotes it (--approve --reason)
python3 -m computer.robotics sim --policy expert --rooms 5   # simulated rooms, every decision checked
python3 -m computer.robotics train-policy / evaluate-policy / promote-policy --approve --reason "..."
sudo tools/install_services.sh                 # /opt/cdos-computer, /etc/cdos-computer, /var/lib/cdos; cdos-api + timer
```

## The Governor (`computer/governor.py`)

Who may make CDOS do what. The neural core and the language model propose, the deterministic engine checks claims,
and the safety envelope checks motions. The Governor decides who is *allowed* to act, and keeps the record. Design,
tests and limits are in [`docs/GOVERNOR.md`](docs/GOVERNOR.md).

- **A persistent emergency stop.** The latch is saved in `governor_dir/estop.json`. It counts as clear only when
  the file is intact **and** a verified audit entry shows an operator clearing it. A missing, damaged, edited or
  unbacked record means *engaged*. Anyone can engage the stop. Only an operator can clear it, and only with a reason.
- **One policy.** Roles (`operator`, `robot`, `service`, `teacher`, `ask`, `read`) come from a root-owned
  `governor.json`. Callers are identified by the kernel: Unix-socket peer credentials for the API, the process uid
  for commands. A caller over TCP is unverified and gets read and ask at most. Without a policy, nobody is an
  operator.
- **Audited approvals.** Promote, rollback, deactivate, growth answers and training, clearing the stop, every
  denial, and resealing the log each write a hash-chained, fsynced line to `governor_dir/audit.jsonl`. A head record
  catches lines removed from the end. The log is verified at start-up and before each privileged decision. If it is
  broken, privileged actions and motions are refused until an operator reseals it.

```sh
python3 -m computer.governor whoami                         # your roles, and what they allow
python3 -m computer.governor status                         # policy, audit log, latch (and the running service's view)
sudo python3 -m computer.governor estop clear --reason "base inspected, room clear"
python3 -m computer.governor estop engage --reason "..."    # anyone
sudo python3 -m computer.neural promote --model <id> --approve --reason "beats nc1-… on my held-out set"
python3 -m computer.governor audit verify
```

**Not hardware safety.** The latch makes the controller command STOP; it can't cut motor power. A physical robot needs
an independent interlock that no software here can override.

## The mini LM

The I Ching is offered to the model as a tool, `consult_iching` (plus `lookup_fact` for the corpus).

| `llm_mode` | What happens |
|---|---|
| `precall` (default) | The computer runs the sampler, then gives the result to the model to re-word. Tiny models are unreliable at deciding when to call a tool, so this is the dependable path. |
| `tools` | The model gets the tools and decides. Use it with a model that's good at tool calling. |
| `off` | No model. The computer's templates produce all output. |

Every model reply is checked: it must name the hexagram, add no numbers, stay short and
make no predictions, or it's discarded.

**Memory is the constraint.** The Zero 2 W has 512 MB. The computer's own process peaked at
about 40–70 MB on my test machine with your full corpus loaded. Speech recognition and the
model need room too, so:

- `./install.sh --with-llm` uses **SmolLM2-135M** (Q4_K_M, 105 MB file). That's the safe choice.
- `./install.sh --llm-360m` uses **SmolLM2-360M** (Q4_K_M, 271 MB). It writes better, but it's tight next to Vosk.
- Or run the model on another machine and point `llm_endpoint` at it. A Jetson, a laptop running
  `llama-server` or Ollama: anything OpenAI-compatible works.

I couldn't run a model on a real Zero 2 W from here, so I don't have speed numbers. Expect
a few seconds per reply, and check memory with `free -m` once everything is running.

## The compute engine (`computer/compute.py`)

Between what the computer decides and the machine that does the work, there's a small engine that chooses
**precision**, **backend** and **how much to try**. It's an engine, not a GPU simulator: simulating a GPU
wouldn't make a Zero or a NUC any faster. Its three jobs are kept separate because they save different things.

```
 CDOS CORE         what to retrieve, rank, say                        (unchanged)
     │
 COMPUTE ENGINE    profile: eco | balanced | priority | auto           how much work, which model
     │             precision: float | int8 | int4                      the controller's weights
     ▼
 backends          python (always) · numpy (if asked) · ccpu (your FPGA, only after it validates)
```

**1. Quantization: memory, not speed.** `controller_precision = "int8"` holds the learned controller as signed
bytes: one scale per hidden unit in the first layer, so adding up a question's rows is an exact integer sum, and
one scale per action in the output layer. Measured on the test corpus's controller (`tools/bench_compute.py`):

| | file | in memory | per question (python) | same action as float | held-out right / wrong / abstained |
|---|---|---|---|---|---|
| float | 219 KB | 909 KB | ~70 µs | – | 36 / 1 / 5 |
| int8 | 39 KB | 170 KB | ~60 µs | 100% | 36 / 1 / 5 |
| int4 | 22 KB | 170 KB | ~50 µs | 95% | 36 / 1 / 5 |

That machine isn't a Pi, and the controller was already fast, so the gain is about 0.7 MB of RAM on a 512 MB board.
int4 shrinks only the file, because rows are unpacked to bytes in memory, and it changes 1 pick in 20. **Use int8.**
Run the bench on your real controller and your own questions before switching:

```sh
python3 tools/bench_compute.py --controller controller.json --corpus data --questions tests/real_heldout.jsonl --score
python3 tools/bench_compute.py --controller controller.json --write-int8 controller.q8.json   # optional: a smaller file
```

The large model's quantization is llama.cpp's job (the Q4_K_M GGUF). The engine doesn't re-implement those kernels.

**2. Profiles: power and latency.** A profile can only take work *away*. Every reply still goes through the same
checks, and a test makes sure sums and fact routing come out the same on every profile.

| `compute_profile` | navigator plans | controller tries | mini LM | max tokens | llama-server |
|---|---|---|---|---|---|
| `eco` | 4 | 1 action, 2 plans | not woken (rules and the computer's own words) | – | `-t 2 -c 512` |
| `balanced` (default) | 8 | 3 actions, 4 plans | as before | 70 × 1.8 | `-t 4 -c 1024` (what install.sh sets) |
| `priority` | 8 | 3 actions, 4 plans | `llm_endpoint_priority` if it's up (e.g. a 360M or bigger model on a NUC) | 70 × 2.2 | `-t <all cores> -c 2048` |
| `auto` | eco when the CPU is at 70 °C or more, the load is high, or a battery is under 20%; balanced again below 62 °C | | | | |

Priority doesn't try *more plans*. The controller tables above show that more plans means more wrong answers.
llama-server's threads and context are fixed when it starts. `python3 -m computer.compute --llama-args eco` prints them for
`systemd/computer-llm.service`. In the text chat, `/profile eco` switches profile on the fly.

**Measuring what actually saves power.** Every turn's log line has a `compute` record with the profile, wall time,
this process's CPU time, llama-server's own timings (tokens, tokens/s) and, on an Intel machine like a NUC, package
energy from RAPL (`sudo chmod o+r /sys/class/powercap/intel-rapl:0/energy_uj`). The Pi doesn't report energy, so
put a USB power meter in line and compare the profiles' CPU time and LM calls:

```sh
python3 tools/compute_report.py computer.log.jsonl              # per profile and faculty: ms, CPU ms, mJ, LM calls, tok/s
python3 tools/bench_compute.py --lm http://127.0.0.1:8080  # the model at each profile's budget
```

RAPL only measures the machine the computer runs on. If the model runs on a NUC and the computer runs on the Pi, the NUC's
energy for that model isn't in the computer's log.

**3. Backends: the CCPU path.** The controller needs only two operations: a gather-sum of int8 rows (per-column
scales) and an int8 matrix-vector product (per-row scales). The engine doesn't talk to hardware. It loads your driver:

```toml
compute_ccpu_driver = "ccpu_driver:Driver"   # implements gather_sum_int8(...) and matvec_int8(...); see CCPUBackend
```

```sh
python3 -m computer.compute --validate ccpu_driver:Driver    # max error vs the Python backend, and µs per call, end to end
```

At startup, `compute_backend = "auto"` runs that same check. The CCPU is used only if it reproduces the Python
backend's numbers, and its timing includes transfers and readback, so a speedup is a real speedup. A driver that
gets the numbers wrong is never used. int4 matrices stay on the CPU.

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi Zero 2 W | with soldered header (or a "WH") |
| microSD, 16 GB+ | Raspberry Pi OS **Lite, 64-bit** |
| INMP441 I2S microphone | speech input |
| MAX98357A I2S amplifier + small 4 Ω/8 Ω speaker (~2–3 W, 28–40 mm) | speech output |
| Momentary push button | push-to-talk |
| Tilt / vibration switch (e.g. SW-520D ball tilt switch) | shake input for the sampler (optional) |
| 2 × LEDs + 2 × 220 Ω resistors | status LEDs |
| 5 V USB power bank, or LiPo + 5 V boost board | something that can supply 2 A |
| A ventilated enclosure | a small project box with holes for the mic, speaker, button and LEDs |

Easier alternatives for audio: a USB sound card with a mic (on a micro-USB OTG adapter)
plus a small powered speaker, or a Pi-Zero-sized I2S audio HAT. Set `mic_device` if `arecord`'s
default isn't your mic.

### Wiring (physical pin numbers)

| From | Pi pin | GPIO |
|---|---|---|
| Both I2S boards: BCLK / SCK | 12 | GPIO18 |
| Both I2S boards: LRC / WS | 35 | GPIO19 |
| INMP441 SD (mic data out) | 38 | GPIO20 |
| MAX98357A DIN (speaker data in) | 40 | GPIO21 |
| INMP441 L/R | GND | (left channel) |
| INMP441 VDD / MAX98357A Vin | 1 (3.3 V) / 2 (5 V) | |
| Button (other leg → GND) | 11 | GPIO17 |
| Tilt switch (other leg → GND) | 13 | GPIO27 |
| Status LED 1 (via 220 Ω, cathode → GND) | 32 | GPIO12 |
| Status LED 2 (via 220 Ω, cathode → GND) | 33 | GPIO13 |

The button and tilt switch use the Pi's internal pull-ups, so they need no resistors.
For the I2S mic + amp pair, add to `/boot/firmware/config.txt` and reboot:

```
dtparam=i2s=on
dtoverlay=googlevoicehat-soundcard
```

Then `arecord -l` and `aplay -l` should both list the card. This overlay is the common
recipe for this mic/amp pair, but check those two commands before you close the enclosure.

### Enclosure

- Mount the Pi and battery so the USB port stays reachable for charging and power.
- Put the speaker and mic behind open grilles or holes, not behind padding.
- Mount the button where it is easy to press, and the LEDs where they are visible.
- Mount the tilt switch rigidly to the enclosure if you use shake input.
- Leave the Pi airflow, since the model makes it warm. `python3 -m computer --check` reports the temperature.
  (The pin numbers and config keys `gpio_eye_left` / `gpio_eye_right` are unchanged from the owl build.)

## Install (on the Pi)

```sh
# Copy this folder to the Pi, and put your cdos-web data/ folder inside it (cdos-computer/data/)
cd cdos-computer
./install.sh                 # computer + Piper voice + Vosk speech input + start-at-boot service
./install.sh --with-llm      # …plus llama.cpp and SmolLM2-135M (the build takes a long time on a Zero)

.venv/bin/python3 -m computer --check     # system check
.venv/bin/python3 -m computer --text      # terminal session
sudo systemctl start computer             # device mode (or just reboot)
journalctl -u computer -f                 # follow the log
```

Every turn is appended to `computer.log.jsonl`, including the full cast (lines, hexagram, the
hexagram it's becoming, and where the chance came from).

## Using it

- **Hold the button**, state the query, release.
- "How many moons does Jupiter have?" → `[KNOWLEDGE]`.
- "What's 15% of 80?" → `[LOGIC]`.
- "How long can a 50 Wh battery run a 5 W device?" → `[LOGIC]` analysis, with inputs, assumptions and steps.
- "System status" / "what is the processor temperature?" → diagnostics read from the machine.
- "Should I start a band?" → the LEDs blink out the cast, then `[SAMPLER]` output.
- "Tell me more" → the trigrams, the moving lines, the nuclear hexagram, and a corpus definition.
- **Shake it** with no query → one sampler run.
- Addressing the device by name is ignored: "computer, what is the capital of France" = "what is the capital of France".

## Migrating from the owl build

| Owl build | Computer build |
|---|---|
| `owl/` package, `python3 -m owl` | `computer/` package, `python3 -m computer` |
| `owl.wisdom.Owl` | `computer.core.Computer` (same constructor and `think()`) |
| `owl.service`, `owl-llm.service` | `computer.service`, `computer-llm.service` (the installer disables the old two) |
| `CDOS_OWL_CONFIG`, `/etc/cdos-owl/config.toml` | `CDOS_COMPUTER_CONFIG` (the old variable still works), `/etc/cdos-computer/config.toml` |
| `owl.log.jsonl` | `computer.log.jsonl` |
| `Reply.voice == "owl"` for template output | `Reply.voice == "computer"` |
| defaults `name = "Ollie"`, `user = "friend"` | `name = "CDOS"`, `user = "user"` |
| `Body.eyes()` | `Body.status_leds()` (`eyes` remains as an alias) |
| files with `"format": "cdos-owl-…/1"` | new files are written as `cdos-computer-…/1`; the old format IDs still load |

Facts about real owls in the corpus ("owl diet" → mice) are data and are unchanged.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The tests check that the King Wen table is a complete 1–64 mapping, that known
hexagrams have the right lines, that the line odds match coins and yarrow, that sensitive
questions never reach the sampler, that shaking can't change a sum, and that bad model replies
are discarded (using a fake model server). `tests/test_navigator.py` covers the plan
validator, every executor operation (units, time, inheritance, backtracking, admitting gaps),
the planners, the LM-planner fallback and the training data. It uses a tiny corpus that
`tests/mini_corpus.py` writes in the real shard format, so it runs without your `data/`.
`tests/test_lexical.py` covers word senses, other words, opposites, kinds, soft matching (and that it
can't invent), follow-ups in conversation and the dictionary training data.
`tests/test_controller.py` covers the controller's reading, slot filling, the `accept()` checks, exploration,
training, and that the computer loads it (training tests need numpy).
`tests/test_neural.py` covers the neural core: the feature schema against a golden file, serialization, more than 20
kinds of damaged or incompatible checkpoints (each refused, the computer still answering), reproducibility (same seed,
same bytes; the trainer and the labels are the controller's), leakage, fallback, promotion, the experience log's chain,
that the trained model beats the untrained one on the held-out metric, and that rule answers, envelopes, safety
routing, calculations and the corpus are unchanged (training tests need numpy).
`tests/test_platform.py` is the Neural Platform 0.2 acceptance test. It covers starting with no model, the component
interface, the Growth Engine finding questions and running a task (nothing active or in the corpus changes),
deliberately bad candidates rejected, twelve kinds of bad plugin refused, the robot's envelope, the API, and an
install into empty folders that is killed and restarted with everything retained (needs numpy; the unit-file check
needs `systemd-analyze`).
`tests/test_governor.py` covers the Governor: the latch after restarts (a real process, killed with SIGKILL), and
every way its record can fail to verify. It also covers the original defect reproduced and fixed, policies,
per-endpoint permissions, real peer credentials from a second user, tampering with the audit log (edits, removed or
reordered lines, a torn write, the head record), several processes writing the log at once, the degraded state and
resealing, and approvals through the commands (who, why, which artifact; dry runs need no authority).
`tests/test_lexicon.py` covers learning the lexicon (heads, senses, synonyms, examples), that the corpus is unchanged,
the milestone questions, ambiguity, and direction.
`tests/test_real_build.py` builds shards from a small corpus.pf with the Phase 1.5 quirks and checks the WordNet repair,
sense-checked learning, the held-out guard and calibration.
`tests/test_learning.py` covers the example records (evidence, inherited facts, rules), the truth and logic examples,
the leak-free split, held-out phrasings, the controller's logic actions and checks, and that the experiment runs.
`tests/test_rules.py` covers the four-valued connectives, rule validation (an unknown action is refused), UNKNOWN
branches, bounded pathways, FALSE and CONFLICT in the navigator, `LOGIC`, the boolean questions, and that the
knowledge pathway is data (a rules file can reorder it; a broken one falls back).
`tests/test_compute.py` covers quantization error bounds, packed files, that int8 picks the same actions,
the CCPU gate (a wrong driver is never used), the auto policy's hysteresis, RAPL metering, and that eco doesn't wake
the model while no profile changes a sum or a fact.
`tests/test_analysis.py` covers quantities and units, runtime and energy estimates (provenance, conflicting figures,
refusals), clarification, follow-ups and what must not count as one, explanations, `CALC` (every op, units, gaps,
time), diagnostics on a fake `/sys` and `/proc`, the working state (atomic, restart, corruption), fact ids, and both
dialogue sets end to end.
`tests/test_battery_acceptance.py` is the Milestone 1 acceptance test: ten battery cases through the public path with
no model and the network blocked, each property checked on its own. `tests/test_capabilities.py` covers every
registry refusal, the source check, run-time fallbacks and errors, the envelope's hash, and the corpus version.
`tests/test_integrity.py` edits shards behind the computer's back (before and after it starts) and checks that every
answer says so, in each `corpus_integrity` mode, and that nothing is written to `data/`.
`tests/test_proposer.py` checks that grounded wording is used and that wrong numbers, invented
names, reversed comparisons, negations and unsupported answers fall back to the template. With `data/` present, they also check facts
against your corpus.

## Files

```
computer/core.py        the turn: route → module → (mini LM) → reply.  The operating rules live here.
computer/router.py      intents + policy (what may go to the sampler)
computer/iching.py      casting, King Wen table, chance (hwrng + shake)
computer/instinct.py    hexagram → labelled sampler output (brief, "tell me more", aphorisms)
computer/data/iching.json   the 64 hexagrams and their text
computer/corpus.py      Knowledge: reads cdos-web's data/ shards (port of corpus.js)
computer/plan.py        the Navigator's plan language: parser, validator, llama.cpp grammar
computer/navigator.py   the executor: runs a plan on the corpus, keeps the evidence, says the answer
computer/planner.py     rule planner, LM planner, and navigate() (plan -> validate -> run -> fallback)
computer/proposer.py    the model proposes plan + words; check() holds the words to the evidence
computer/navdata.py     training-data generator (facts and dictionary questions) and the lookup-vs-navigator evaluation
computer/controller.py  the learned controller: question shape -> action, slot filling, accept()  (plain Python)
computer/controller_train.py   re-wordings, exploration with a checked reward, training (numpy)
computer/controller_eval.py    rules vs rules + controller vs try-everything
computer/logic.py       TRUE / FALSE / UNKNOWN / CONFLICT and the connectives
computer/rules.py       the rule engine: pathways and knowledge rules as data, validated, bounded, traced
computer/data/rules.json    the knowledge pathway and the knowledge rules: edit to change the order or the assumptions
computer/compute.py     the compute engine: int8/int4 controller weights, backends (python, numpy, CCPU gate),
                   eco/balanced/priority/auto profiles, per-turn metering
computer/lexicon.py     the relation lexicon: proposals from the dictionary and examples, validated against the graph
computer/neural/        the neural core: schema, records, train, evaluate, registry (staging -> --approve -> active),
                        model checks, runtime planner and loader, experience log, bench; python3 -m computer.neural
computer/neural/components.py  the shared interface: versioned components with checked inputs and outputs
computer/growth/        the Growth Engine: discover, tasks, a person's answers, train + evaluate; python3 -m computer.growth
computer/plugins.py     capability plugins: plugin.json + code, checked before import, refused with a reason
computer/robotics/      simulation only: state, world model, safety envelope, policies, controller, sim, policy training
computer/data/robot_safety.json   the robot's safety limits, each with its reason
computer/services/      the platform, the local API (Unix socket / 127.0.0.1) and its client
computer/governor.py    the Governor: policy, identities, permissions, emergency-stop latch, audit; python3 -m computer.governor
governor.example.json   an example policy (the installer writes /etc/cdos-computer/governor.json)
plugins/tmp117_temperature/   example plugin: a TMP117 register -> degrees C
systemd/                cdos-api.service, cdos-growth.service + .timer, cdos-robot-sim.service (sandboxed, no network)
tools/install_services.sh   installs code, config and state apart (/opt, /etc, /var/lib) and the units
tools/             make_navigator_data.py, train_navigator.py, eval_navigator.py, train_controller.py, learn_lexicon.py,
                   build_shards.py, eval_lexicon.py, calibrate_lexicon.py, bench_compute.py, compute_report.py,
                   experiment_learning.py (does training on the corpus help on new questions?),
                   bench_dialogue.py (the whole turn on fixed sets: this tree vs another, with ms, CPU, shard reads, RSS)
                   validate_corpus.py (read-only checks of a data/ folder), review_real_corpus.py (the review cases)
docs/STARSHIP_UPGRADE.md   assessment, plan, design, measurements and limits of the analysis upgrade
docs/MILESTONE_1.md        the capability registry, envelopes and requirement checks: what was built, how it's tested
docs/REAL_CORPUS_REVIEW.md the production-corpus review: every case, defects fixed, data issues, performance
docs/NEURAL_CORE.md        the neural core: baseline, design, invariants and their tests, evaluation, results, limits
docs/PLATFORM_0_2.md       Neural Platform 0.2: acceptance matrix, growth, robotics, services, changes, limits
docs/GOVERNOR.md           the Governor (0.3): requirements and their tests, design, behaviour changes, limits
docs/cdos-design-*.md      the Milestone 1 and 2 design drafts these follow
computer/analysis.py    estimates from stated quantities and corpus figures: formulas, clarification, what-ifs, evidence
computer/quantity.py    numbers with units: parsing (digits and number words), dimensions, conversion, spoken formatting
computer/diagnostics.py processor temperature, load, memory, storage, battery, corpus, uptime: read, never estimated
computer/state.py       working state across restarts: atomic, versioned, age-limited (knowledge is never written)
computer/capabilities.py  what each capability declares, checked at start-up; fallbacks, availability, structured errors
computer/envelope.py    the result envelope: truth, certainty, evidence with corpus version, steps, errors, canonical hash
computer/clock.py       the one place the answer path reads the date
computer/phrase.py      facts → sentences (port of phrase.js)
computer/calc.py        Logic: the calculator (port of calc.js)
computer/llm.py         mini LM client + the consult_iching / lookup_fact tool schemas
computer/body.py        button, tilt switch, status LEDs (gpiozero; no-op off the Pi)
computer/voice.py       Piper / espeak-ng speech output, Vosk push-to-talk speech input
computer/__main__.py    device mode, --text, --cast, --wise, --check, --plan, --envelope
install.sh  systemd/  config.example.toml  tests/
```
