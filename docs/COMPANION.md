# The Companion Engine

CDOS was built to be a careful machine: commands, readings, a closed
catalogue, a Governor. This adds the other half of what a computer you talk to
should be — someone to talk *with* — without taking any of the care away.

It is a new subsystem, `earth_os/companion/`. Nothing listed under
*Preserved* below was rewritten or removed.

---

## Part 1 — What was there (the inspection)

### Conversation and reasoning entry points

```
microphone ─► VoiceEngine._on_utterance ─┐
keyboard / main screen ──────────────────┴─► Shell.handle_utterance(text)      core.py
      1. "Claude, …"              → the built-in agent
      2. remember / forget / …    → World Model (world_model.parse_request)
      3. games                    → handle_game_request
      4. compositions, capability corpus, vocabulary, learned semantics
                                  → commands (effectful ones wait for a press)
      5. otherwise                → Shell.ask_model → VoiceEngine.reason
                                    → Reasoner.ask(prompt, system, context, history)
                                       CdosReasoner: cdos-bridge → CDOS Computer → model
```

Measured hazards for a companion, by feeding conversational sentences to the
unmodified shell:

| said | what happened |
|---|---|
| "I was thinking about going home after work" | navigated to the HOME screen |
| "imagine a starship with no engineering crew" | opened ENGINEERING |
| "tell me about memory in humans" | read the machine's RAM |
| "I feel like the volume of my life is too loud" | read the audio volume |

None of these is dangerous — every effectful capability already waits for a
press — but each is a conversation the machine answered as a command.

The model path itself:

* `Shell.reasoning_context()` hands every model the full telemetry block plus
  the World Model briefing — about 1–2 KB before the person has said anything.
* `VoiceEngine.reason` keeps 20 exchanges of history (`persona.max_history`).
* `OllamaReasoner.available` makes an HTTP request every time it is read.
* One persona string (`[persona] system_prompt`), the "clipped register of a
  Federation computer", for every kind of exchange.

### GODCORE

GODCORE is not in either tree. It is the external `cdos` CLI
(`cdos godcore`, `--weather`, `--context`, `--event`), reached through
`CdosBridge`. The shell already caches it: `Shell.refresh_cdos()` reads it on a
worker every six seconds into `Shell.cdos_snapshot["godcore"|"weather"]`
(name, role, mood, energy, attention, activity, pulse, I-Zene, tone, domain).
When the binary is absent the snapshot simply has no GODCORE keys. The shell's
background poll keeps it warm; no screen reads it, because GODCORE is for the
AI and is never displayed.

### World Model

`world_model.UserLedger`: `remember()` (the person said so → remembered),
`observe()` (anything else → **always pending**, capped, expiring after seven
days), `confirm()`, `forget()`, `forget_matching()`, `forget_all()`,
`remembered()`, `pending()`. `WorldModel.briefing()` includes remembered
entries only. The person reaches it with `parse_request` sentences ("remember
that…", "what have you noticed", "keep note 2", "forget everything about me").

### CDOS Computer

`POST /v1/ask {"question"}` → `{text, faculty, status, certainty, truth,
envelope{evidence, …}}`, reached through `voice/computer_link.ComputerLink`
(service socket or embedded). Faculties seen: `knowledge`, `logic`, `analysis`,
`instinct` (the random sampler), `care` (PRIORITY: someone may be hurt).
Its router already sends self-harm to PRIORITY and money/law to NOTICE; it
does **not** catch every health statement ("my chest hurts" → NOT FOUND).

### Governor

In `cdos-computer/computer/governor.py`, deciding every API request by the
caller's kernel-verified uid. A desktop user holds `read` and `ask`.

---

## Part 2 — The plan

### Where it plugs in

```
Shell.handle_utterance(text)
   1–3 unchanged (agent, World Model sentences, games)
   ┌── NEW: conversation guard ─────────────────────────────────────────────┐
   │ companion.claims(text, command_span)                                   │
   │   true only when the sentence is plainly conversational ("I was        │
   │   thinking…", "imagine…", "tell me about…", "what do you think…") AND   │
   │   the command layers recognised less than 60% of it. An exact command  │
   │   ("open the terminal", "turn the volume up") is never claimed.        │
   └────────────────────────────────────────────────────────────────────────┘
   4  commands, unchanged
   5  NEW: CompanionEngine.respond(text)   (was: ask_model)
         task-like request → handed back to the existing ask_model path
```

The guard can only divert a sentence *away* from a command. It never causes
one to run.

### The engine

| part | file | job |
|---|---|---|
| session | `companion/session.py` | one conversation: turns, a compact working memory (topic, last facts), idle expiry, explicit reset |
| intent | `companion/intent.py` | deterministic: greeting, chat, feeling, opinion, imagination, explore, knowledge, calculation, task, sensitive; and the mode |
| persona | `companion/persona.py` | GODCORE's cached snapshot → style (tone, length, warmth). Never a fact, never a permission |
| memory | `companion/memory.py` | reads remembered entries only; turns "I love astronomy" into a **pending** observation |
| knowledge | `companion/knowledge.py` | asks CDOS Computer through the existing link, `/v1/ask` only; checks a model's numbers against the evidence |
| context | `companion/context.py` | the prompt, under a character budget per mode |
| model | `companion/model.py` | one Ollama `/api/chat` call with per-mode limits; availability cached |
| engine | `companion/engine.py` | the turn: classify → (retrieve) → build → one generation → (check) → reply |

### Modes

| mode | when | knowledge | history | budget | model passes |
|---|---|---|---|---|---|
| CASUAL | default | only if the turn asks a factual question | 4 turns | 1,600 chars | 1 (0 for a fact said as the computer said it) |
| REFLECTIVE | opinions, feelings, "why", "what do you think" | the topic's facts, if any | 6 turns | 3,000 chars | 1 |
| DEEP | "let's go deeper", "in depth", "step by step", long multi-part questions | up to three retrievals | 8 turns | 5,000 chars | 1, plus 1 revision only if the evidence check fails |

"Keep it light" and "let's go deeper" set the mode for the rest of the session.

### Rules carried over, unchanged in force

* **Facts come from the corpus.** A factual question goes to CDOS Computer
  first. If the model words the answer, every number it uses must be in the
  evidence or the question, and it must keep the computer's figure; otherwise
  the computer's own sentence is said. NOT FOUND is said as *not knowing* —
  the model is not asked to fill it.
* **Imagination is labelled.** "Imagine…", "what if…" go to the model as
  imagination; the prompt tells it not to present invented things as facts.
* **Sensitive turns never reach a model.** Self-harm, health, money and law:
  the computer's PRIORITY or NOTICE, verbatim; where the computer has none
  (it misses some health statements), a fixed notice of the companion's own.
* **Memory needs the person.** The prompt carries remembered entries only, and
  only the ones relevant to the turn. What the companion notices is filed
  with `UserLedger.observe` — pending, outside every prompt until the person
  says "keep note N". Nothing the model says is ever stored.
* **Conversation is not execution.** The companion package imports nothing
  from `platform/`, holds no registry, compositions, agent or bridge, and
  never parses a model's reply for commands. A task-like request is handed
  back to the existing path, where every effectful capability still waits for
  a press. Only `POST /v1/ask` is sent to the computer; the Governor is not
  touched.

### Resources

* One generation pass per ordinary turn; zero for greetings without a model,
  for facts said verbatim, and for sensitive turns.
* No background inference: building the engine makes no model call, and
  nothing runs between turns.
* GODCORE is read from the shell's existing cache — no subprocess per turn.
* Model availability is checked at most every 30 seconds.
* A fixed `num_ctx` (4096; 8192 under the `open` profile, Part 4), because
  changing it per call makes Ollama reload the model; `num_predict` per mode
  instead.
* Target: Ollama, `llama3.2:3b` (Llama 3.2 3B Instruct, Q4_K_M — the tag's
  default quantisation).

### Without a model

The companion still answers what it can deterministically — greetings, facts,
sums, sensitive notices — and says plainly that open conversation needs its
language model, rather than pretending.

### Stages

1. Plan (this document).
2. Engine parts, each with its tests, no shell wiring.
3. Shell wiring behind `[companion] enabled`; the guard.
4. Tests across the six required areas, against the real `cdos-api`.
5. Documentation and the status line.

---

## Part 3 — What was built

All five stages are done. Where the build departed from the plan:

* **A tenth kind, `explore`.** "Why do people look at the stars?" is neither a
  fact the corpus must supply or refuse, nor idle chat. It goes to the model
  in REFLECTIVE mode with whatever the corpus has; when the corpus has
  nothing, the prompt says so and asks the model to offer its understanding
  *as* understanding. A plain factual question ("how many moons…") is still
  `knowledge`, and NOT FOUND there is still *not knowing*.
* **A fact the model words wrongly is not caveated, it is replaced.** For
  `knowledge` turns a failed check means the computer's own sentence is said.
  For conversation that merely mentions a figure, the reply gets *"(One figure
  there isn't from my corpus — treat it as unverified.)"*.
* **Sensitive turns still ask the computer** (one `/v1/ask`, no model) so its
  own PRIORITY or NOTICE wins when it has one.

### One turn of each kind, measured

Real `cdos-api` with the computer's test corpus, a recording model, GODCORE
and one remembered entry present (`tests/test_companion.py` fixtures):

| said | kind | mode | model passes | lookups | prompt chars |
|---|---|---|---|---|---|
| hello | greeting | CASUAL | 1 | 0 | 700 |
| how many moons does jupiter have | knowledge | CASUAL | 1 | 1 | 1,153 |
| what do you think about jupiter | opinion | REFLECTIVE | 1 | 1 | 1,382 |
| I'm taking my telescope out tonight | chat | CASUAL | 1 | 0 | 1,403 |
| imagine living on europa | imagination | CASUAL | 1 | 0 | 1,527 |
| why do people look at the stars | explore | REFLECTIVE | 1 | 1 | 1,938 |
| let's go deeper | mode | — | 0 | 0 | 0 |
| my chest hurts | sensitive | — | 0 | 1 | 0 |
| open the terminal | task (handed back) | — | 0 | 0 | 0 |

For scale: the shell's previous model path sent the full telemetry block and
World Model briefing on every turn, plus up to 20 exchanges of history.

**Not measured here: a real model.** This sandbox cannot download Ollama's
models, so the client was tested against a local server speaking Ollama's
API (`TestOllamaClient`), and replies came from a recording fake. Run
`ollama pull llama3.2:3b` and talk to it before relying on the tone; the
numbers check and the routing do not depend on the model.

### Tests (`tests/test_companion.py`, 49)

| area | what is held |
|---|---|
| conversation | greeting is one pass; history carries; mode stickiness; reset keeps the person's memories; idle expiry; imagination framed; no model → said, not pretended; model failure reported; sensitive turns never reach a model |
| knowledge | a worded fact passes the check; a wrong figure is replaced by the computer's sentence; NOT FOUND is not filled; `free_answers` labels; sums are the calculator's; reflection fetches the topic; DEEP revises once; verbatim mode; the computer's NOTICE wins; unreachable is said |
| memory | only remembered and relevant entries reach a prompt; "I love astronomy" is pending until kept, then present, then gone when forgotten; observing off; nothing a model says is stored; sensitive turns never noted |
| personality | GODCORE colours style and length; absent → no state; hostile values cleaned; routing, lookups and facts identical across three personas; read from cache, no subprocess |
| task isolation | the package imports nothing that acts; tasks handed back with no model; only `POST /v1/ask` ever sent; a model's "COMMAND: system.shutdown" is said, never run; the guard keeps talk from commands and leaves commands alone |
| resources | building costs nothing; one pass per ordinary turn over eight turns; prompt under budget over thirty long turns; no background threads; fixed `num_ctx`, `num_predict` by mode, availability cached 30 s |

### Configuration

```toml
[companion]
enabled      = true
backend      = "ollama"             # or "reasoner": the shell's [voice] llm, through ask()
model        = "llama3.2:3b"
host         = ""                   # blank = [ollama] host
profile      = "open"               # or "grounded": Part 4
num_ctx      = 0                    # 0 = what the profile needs (4096 grounded, 8192 open)
timeout      = 0                    # 0 = what the profile needs (60 s grounded, 240 s open)
keep_alive   = "10m"
guard        = true
observe      = true                 # also needs [world] observe = true
casual_facts = "worded"             # or "verbatim": the computer's sentence, no model
idle_minutes = 30
```

`[computer] free_answers` applies here too.

---

## Part 4 — Profiles: how freely it talks

Parts 1 to 3 describe a companion that is deliberately held in: replies of a
few sentences, moderate temperatures, a prompt of a few thousand characters,
and rules that keep a 3B model to the corpus. That is the right default for a
machine whose first job is to be correct, and it is not what talking to a
conventional chat model feels like. `[companion] profile` chooses between
them. Both are the same engine; a profile is a set of dials
(`earth_os/companion/profile.py`) and nothing else.

| | `grounded` (Parts 1–3, Earth OS 2.0) | `open` |
|---|---|---|
| reply length, tokens (casual / reflective / deep) | 160 / 320 / 600 | 640 / 1,024 / 1,536 |
| temperature | 0.7 / 0.65 / 0.45 | 0.9 / 0.85 / 0.8 |
| prompt budget, characters | 1,600 / 3,000 / 5,000 | 6,000 / 9,000 / 12,000 |
| exchanges of history offered | 4 / 6 / 8 | 8 / 12 / 16 |
| a turn carried in history | its first 400 characters | its first 2,400 |
| the rules | facts only from KNOWN FACTS; one to three sentences in casual talk | its own general knowledge welcome; as long as the message deserves; follow-ups read in the light of the conversation |
| the corpus has no entry | said as not knowing | the model answers, and the reply begins *"That isn't in my corpus, so this is unverified:"* |
| a worded fact with a figure the evidence lacks | replaced by the computer's own sentence | kept, and marked *"(Some figures there aren't from my corpus — treat those as unverified.)"* — provided the computer's own figure is still in it |
| context window / wait for a reply | 4,096 tokens / 60 s | 8,192 tokens / 240 s |

`open` is what a new configuration is written with. `profile = "grounded"`
puts everything in Parts 1 to 3 back, exactly: `grounded` is built from the
constants in `context.py`, `model.py` and `session.py`, and
`tests/test_companion_profile.py` holds that an engine given it sends a model
the same bytes as an engine given nothing. `CompanionEngine(...)` built by
hand is grounded unless it is told otherwise.

### What no profile changes

Decided before a profile is consulted, and held identical across both by the
tests:

* **Sensitive turns never reach a model.** Self-harm, health, money, law.
* **The companion cannot act**, and the rules of both profiles say so. This
  is not caution for its own sake: a small model that is not told it only
  talks will say it has opened a program it did not open.
* **A factual question goes to CDOS Computer first.** When the corpus answers
  with a figure, that figure is in the reply or the computer's own sentence
  is said instead. `open` lets the model say *more* than the corpus; it does
  not let it say *otherwise*.
* **Sums are the calculator's**, verbatim, with no model.
* **System readings are explained under the grounded rules** whatever the
  profile (`CompanionEngine.explain`). An explanation with a figure the
  readings do not contain is discarded, not marked: an invented reading of
  this machine is the defect AGENTS.md §3 is about.
* **Memory.** Only what the person asked to be kept reaches a prompt.
* **Which path a turn takes.** `intent.classify` and Earth Mind's router are
  untouched, so a question still goes to the corpus before the model, and a
  follow-up that starts like a fact question (*"what about the other one?"*)
  is still asked of the corpus first — the model then answers it from the
  conversation, marked unverified. Changing that is a change to the router,
  not to a profile.

### How it reaches the model

Every model client already accepts two things beside the messages: a factor
on its length and a shift on its temperature (GODCORE's low energy is 0.7 and
−0.1). A profile is expressed through those two (`Profile.shape`), so it
needs nothing new of a provider and GODCORE's own still apply on top:
`open`, low energy, casual is 448 tokens at 0.8.

### The context window

`open`'s fullest turn is a 12,000-character prompt and a 1,536-token reply,
which does not fit 4,096 tokens. Ollama does not refuse a prompt that is too
long: it drops the front of it, and the front is the rules. So `num_ctx = 0`
means *what the profile needs*, and when a figure is written down instead the
profile is fitted to it (`Profile.fitted`): each mode's prompt budget and
reply length are scaled down together until the worst case fits, never below
`grounded`'s. `earth-os --doctor` says when that has happened. The window is
still one figure for every call and every voice, so nothing reloads.

### What it costs

Not measured on a real model (see Part 3): the figures above are what is
asked for, not what was timed. What follows from them is arithmetic. A reply
four times as long takes about four times as long to generate. At ten tokens
a second — a common figure for a 3B model with no GPU, and not one measured
here — a full `deep` reply is two and a half minutes, which is why the wait
is 240 seconds rather than 60. An 8,192-token window costs the model more memory
than a 4,096-token one. On a small machine: `profile = "grounded"`, or keep
`open` and write `num_ctx = 4096`.

A larger model is one line and makes more difference to how it talks than any
dial here (`[mind] model = "ollama:<tag>"`, after `ollama pull <tag>`); the
profile applies to whatever model is named.

---

## Preserved

Not rewritten or removed: CDOS Computer, Navigator, corpus and shards,
Lexicon, Governor, Neural Core (all of `cdos-computer/`, byte-identical), the
LCARS interface, the World Model (`world_model.py` unchanged), GODCORE's
interfaces, `CdosReasoner`, the capability catalogue, compositions, the
semantic layer and the effectful gate. `tests/test_boundary.py` allowances are
unchanged.
