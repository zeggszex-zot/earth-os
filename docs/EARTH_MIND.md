# Earth Mind

Earth OS is one computer. Earth Mind is its one mind: a coordination layer
over four subsystems that already existed or were ported, so that the machine
answers as one thing rather than as four programs.

```
   terminal ─┐                                    ┌─ CDOS        knowledge, sums, policy
   voice ────┤                                    │              cdos-computer/ via voice/computer_link.py
   a game ───┼──► EARTH MIND ── routes, never acts┼─ Companion   conversation, personality
   api ──────┘    earth_os/mind/                  │              earth_os/companion/
                    router    what kind of request├─ System      the real machine, read-only;
                    models    one model, shared   │              operations are proposals for the shell's gate
                    memory    one store, namespaced              earth_os/mind/system.py → core.py → platform/
                    personas  many voices         └─ Game        worlds, characters, events
                    mind      the coordinator                    earth_os/mind/game/
```

It is a modular monolith: one Python package, in the shell's process, no new
service, no new dependency (SQLite and `urllib` are in the standard library).

**Earth Mind talks, reads and proposes. It does not act.** Nothing under
`earth_os/mind/` imports a platform adapter, `subprocess`, the agent or the
shell, and a test reads the imports to keep it so. The most it can do about
the machine is return a `Proposal`, which the shell's existing gated path may
carry out — and which waits there for a deliberate press (in the terminal, a
typed `yes`) when the capability is effectful.

This document is the architecture, the component map, the interfaces and the
measurements. What was tested is in [TESTING.md](TESTING.md); what is and is
not finished is in [RELEASE_2_0.md](RELEASE_2_0.md); going back is in
[ROLLBACK.md](ROLLBACK.md).

---

## 1. The component map

Two source projects came in: **Earth OS 1.0.0** (the shell: GUI, terminal,
capability layer) and **CDOS** (two halves — *CDOS LCARS*, the shell Earth OS
was forked from, which had since gained the Companion Engine; and *CDOS
Computer*, the deterministic reasoning computer). Every part was read before
deciding where it went.

| Existing part | Where it was | Decision | In 2.0 |
|---|---|---|---|
| GUI: window, chrome, screens, widgets, splash, theme, phosphor | Earth OS `earth_os/{app,chrome,splash,theme,…}.py`, `screens/`, `widgets/` | **Retained** | Untouched, except the title cap reads `V2.0` and the terminal's header line names who answered |
| Terminal screen and Universal Translator | Earth OS `screens/terminal.py`, `translator.py` | **Extended** | 1.0's steps unchanged; Earth Mind takes the lines that are not commands (§6) |
| Capability layer: closed catalogue, platform adapters, effectful gate | Earth OS `platform/`, `core.py` | **Retained, connected** | The system subsystem reads through it; no capability added |
| Capability corpus: what the machine can be asked, analyses | Earth OS `capability_corpus.py` | **Extended** | One analysis added, `performance_report` — readings only |
| System monitoring: `sysinfo`, `process.list`, `hardware.*` readings | Earth OS `sysinfo.py`, `platform/linux_adapter.py` | **Retained, connected** | Real readings for §4's system queries |
| Voice path and its tiers, reasoner backends (Ollama, cloud, CDOS bridge) | Earth OS `voice/`, `core.handle_utterance` | **Extended** | Tier 4 goes to Earth Mind; tiers 1–3 unchanged |
| Agent workspace (Claude Code), compositions, semantics, comms, windows | Earth OS | **Retained** | Untouched |
| Companion Engine: intent, modes, sessions, prompt builder, evidence check, Ollama client | CDOS LCARS `cdos_lcars/companion/` | **Ported, then connected** | `earth_os/companion/`; its 49 tests ported and passing. Additive changes only: a style hook, `explain()`, a named origin for facts, and optional rules, labels and budget in the prompt builder. `intent`, `memory`, `model` and `session` are identical to the originals |
| World Model: the person's ledger (remember / forget / pending notes) | CDOS LCARS `world_model.py` | **Ported unchanged, connected** | `earth_os/world_model.py`; it *is* the `user` memory namespace |
| Computer link: socket or in-process client for CDOS Computer | CDOS LCARS `voice/computer_link.py` | **Ported unchanged, connected** | `earth_os/voice/computer_link.py` |
| CDOS Computer: corpus, navigator, calculator, rules, Governor, neural proposer, robotics sim | CDOS `cdos-computer/` | **Retained whole** | Imported unchanged into `cdos-computer/`; run in-process, or as its own `cdos-api` service when installed |
| Ollama integration | Both (`voice/backends.py`, `companion/model.py`) | **Retained, connected** | `companion.model.CompanionModel` is the Ollama provider behind the model manager |
| Board games (chess, battle tactics) and their screen | CDOS LCARS `games/`, `screens/games.py` | **Not ported** | A different thing from game *intelligence*; it needs a new screen. Listed as planned |
| A second conversation engine, a second memory database, a second model client | — | **Not built** | Characters reuse the Companion's builder, session and classifier; one store; one client per model |
| **New** | | | `earth_os/mind/`: router, models, memory, personas, characters, system, game, mind |

Nothing was replaced.

---

## 2. One request, start to finish

Every interface hands Earth Mind a sentence and gets a `Response` back:

```python
mind = shell.earth_mind()                       # built once, on first use; None if disabled
response = mind.handle("Why is my computer running slowly?", interface="terminal")
response.text         # what to say
response.route        # knowledge | conversation | system_query | system_action | game | mixed
response.subsystems   # ("system", "companion")
response.source       # readings | computer | model | notice | fixed | scripted | handoff
response.mode()       # "SYSTEM + COMPANION" — what the terminal header shows
response.proposal     # a system operation for the shell's gate, or None
response.detail       # the verified findings behind an explanation
response.checked      # the evidence check, when there was one
```

`handle` never raises: a subsystem that fails becomes a `Response` with
`ok = False`. `mind.route(text)` says how a sentence *would* be handled
without handling it — cheap, and it calls no model. `await mind.ahandle(…)`
is the same turn for an event loop.

### The router (`mind/router.py`)

Six kinds, decided deterministically before any model is near the sentence.
The router adds no classifier of its own: whether a sentence is about the
machine is the capability corpus's answer; what kind of talk it is, is the
Companion's own classifier; who a name refers to is asked of the personality
registry and the game host. The order they are asked in is the policy:

1. a conversation with a game character is in progress → everything typed is
   said to that character, except leaving, time passing, "remember that I…",
   "what happened" and "who is here";
2. a sentence only a person in distress would say → the Companion's fixed
   notices, whatever else the words resemble;
3. "remember that…" / "forget…" → the World Model;
4. a name → "talk to the shopkeeper", "switch to the technical assistant";
5. the machine → a reading, a diagnosis or an operation, unless the sentence
   is plainly talk that merely contains a system word;
6. otherwise knowledge or conversation, as the Companion classifies it.

| You say | Kind | What happens |
|---|---|---|
| "What is using the most RAM?" | `system_query` | Readings taken now through the capability layer. No model. |
| "Explain how neural networks work." | `knowledge` | CDOS is asked first. Found: worded by the Companion and checked. Not found: said as not knowing — a model does not fill the gap. |
| "Open Firefox." | `system_action` | A `Proposal`. The shell starts it only if it is an installed application, by that application's own launcher entry. |
| "Shut down." | `system_action` | A `Proposal` marked effectful. The shell waits for a press. |
| "Why is my computer running slowly?" | `mixed` | The system subsystem takes the readings; the Companion explains them; the explanation is checked against them. |
| "Talk to the shopkeeper." | `game` | The character's conversation is created or resumed. |
| "Remember that I helped this character." | `game` | Filed in the world's event history and in that character's memory. |
| "I had a long day." | `conversation` | The Companion. |

A wrong guess costs an odd reply. It cannot cost an action: nothing
downstream of the router can act.

---

## 3. The subsystems

### CDOS — knowledge and reasoning

Unchanged. Earth Mind reaches it only through the Companion's knowledge path
(`POST /v1/ask`), over the `cdos-api` socket when that service is installed
and in-process otherwise. Sums, units, time and policy need no corpus and no
model. A fact needs the corpus; without one the computer says *corpus
unavailable* and **no model answers in its place** (unless
`[computer] free_answers = true`, which labels the answer as the model's).

### Companion — personality and interaction

Unchanged in what it does: one model pass per ordinary turn, three modes, a
working memory of a few turns, the evidence check on every figure. Earth Mind
keeps one engine per OS personality, all on one shared model client. An engine
is handed the link to CDOS only if its personality has the `knowledge`
permission and the person's ledger only if it has `memory.user`, so a
personality without the permission has no path to either.

### System — the operating environment (`mind/system.py`)

Everything Earth Mind says about the machine comes from the shell's closed
capability catalogue, by way of the corpus that already decides what the
machine can be asked.

* **A reading or an analysis** is taken now through `Shell.answer_reading` /
  `Shell.answer_analysis`, which re-check at the moment of running that the
  capability is a parameterless, non-effectful reading. One that cannot be
  taken is said to be unavailable, never estimated.
* **A diagnosis** is still only readings: the findings are fixed before any
  model sees the question, and the Companion's explanation is checked against
  them. An explanation with a figure the readings do not contain is thrown
  away and the findings themselves are said.
* **An operation** is never performed here. It is returned as a `Proposal`.

This is why a model cannot invent a system condition (it is never asked for
one) and cannot run a command (nothing it writes is read as one).

### Game — intelligent environments (`mind/game/`)

Section 5.

---

## 4. One intelligence, many personalities

### Model management (`mind/models.py`)

A personality is not a model. The Companion, the Technical Assistant and every
character in a game world are prompts over the *same* loaded model.

* A **spec** names a model: `ollama:llama3.2:3b`, optionally `@http://host:port`;
  or `reasoner`, meaning whatever `[voice] llm` already is.
* A **client** is one connection to one model. Two environments naming the
  same spec get the same client object; `ModelManager.clients` is keyed by
  the resolved spec, so nothing is connected or loaded twice.
* An **environment** (`os`, `game`, `game:harbour`) is assigned a spec. A game
  falls back to `game`, then to the default — so giving characters a smaller
  model is one configuration line (`[mind] game_model`), and giving them the
  same one is none.

Three things happen here because it is the one place every model call passes:
secrets are **redacted** from every message (`mind/redact.py`); calls are
**serialised per client**, so twelve characters asking at once queue rather
than thrash a local model; and **usage is counted per environment**.

A new kind of model — another local runtime, a remote API — is one function:

```python
mind.models.register_provider("myapi", lambda spec, options: MyClient(spec.model, spec.host))
# a client needs: .available, .status(), .chat(messages, mode, length_factor, temperature_shift)
```

Only Ollama and the shell's existing reasoners are provided in 2.0.

If no model is reachable nothing raises and nothing is contacted until a turn
asks. Readings, CDOS's answers, remember/forget, routing, commands and the
whole of 1.0 do not pass through this module.

### Personalities (`mind/personas.py`)

A personality is a JSON object. Shipped: `companion`, `technician`
(Technical Assistant), `robot` (Robot Companion). Your own go in
`~/.config/earth-os/personalities/*.json`:

```json
{
  "id": "poet", "name": "EARTH OS", "role": "Poet",
  "aliases": ["poet", "the poet"],
  "description": "You are the same computer, speaking sparely.",
  "traits": ["spare", "observant"],
  "style": "Few words. One image at most.",
  "permissions": ["converse"],
  "greeting": "Listening."
}
```

Each has its own **identity** (`id`, `name`, `role`), **personality**
(`description`, `traits`, `style`), **context** (`background`, `knowledge`),
**memory namespace** (derived, never chosen), **goals**, **relationships**,
**permissions** and **environment** (`os`, or `game:<world>`).

Permissions are a closed list — `converse`, `knowledge`, `memory.user`,
`system.read`, `system.act` — and an unknown one is refused when the file is
read. `system.act` means "may have an operation *proposed*"; the gate is
still the shell's. **A game character may hold `converse` and nothing else**:
a world definition that asks for more is refused with its reason, not quietly
trimmed.

Each interface talks to its own personality: "switch to the technical
assistant" in the terminal does not change who the voice is talking to.

---

## 5. Game intelligence

A game supplies personalities and world context. Earth Mind supplies memory,
dialogue, the shared model, relationships and event tracking. A world is a
JSON object (`mind/worlds/harbour.json` is the shipped demonstration; yours go
in `~/.local/share/earth-os/mind/worlds/`):

```python
from earth_os.mind.game import GameEnvironment

mind.games.install({                             # checked; refused if it asks for more than a voice
    "id": "saltmarsh", "name": "Saltmarsh",
    "facts": ["The bridge north of town is washed out."],
    "characters": [{
        "id": "elara", "name": "Elara", "role": "shopkeeper",
        "description": "You keep the general store.", "traits": ["shrewd", "kind"],
        "background": "You have run the store for twenty years.",
        "goals": ["keep the store open through winter"],
        "knowledge": ["A lantern costs 3 marks."],
        "relationships": {"garrick": [4, "an old friend"]}}]})

world = mind.games.open("saltmarsh")
reply = world.talk("elara", "Do you sell lanterns?")          # the player speaks
world.event("The traveller mended Elara's roof.",              # something happened
            witnesses=["elara"], relationships=[("elara", "player", 3)],
            state={"roof": "mended"})
world.advance(3)                                               # three days pass
world.talk("elara", "Do you remember me?")                    # she is given that memory, dated
world.exchange("elara", "garrick", "the bridge")              # two characters speak to each other
world.add_character({"id": "ines", "name": "Ines", "role": "ferryman"})
world.history(); world.relationship("elara", "player"); world.memories("elara")
```

That list is the whole of what a world can do, and every item changes only
that world's own namespaces.

* **A character's turn is the Companion's machinery wearing another
  identity** (`mind/characters.py`): the same prompt builder, session and
  classifier, the same shared model. It is given its own rules, its own
  identity, and its own memories read through a view that can see only its
  own namespace and its world's public facts.
* **A witness remembers an event; a character who was not there does not.**
  Its view cannot read the world's history either.
* **Only what the speaker said is kept as a memory**, never what the model
  made the character say — a small model's flourish would otherwise become a
  fact the character acts on next week. What *happened* is the game's to
  record, with `event()`.
* **What a character knows and remembers is never trimmed from its prompt**;
  each is capped and ranked by relevance instead.
* **With no model** a character still answers — from its written lines, what
  it knows, and what it remembers — and the reply says so
  (`source = "scripted"`).
* **Many characters, bounded cost**: one model client however many
  characters; 32 working memories are kept warm and the rest are rebuilt from
  the store when someone speaks to them again.
* **Everything is persistent**: the day, the history, every relationship and
  every character's memories are in the store. An open conversation is not —
  restarting puts every interface back at the operating system.

From the terminal or by voice: `talk to the shopkeeper`, then just talk;
`remember that I helped this character`, `three days later`, `what happened`,
`who is here`, `talk to brann`, `goodbye`.

What this is not: a game engine. There is no map, inventory, combat, quest
system or renderer. These are the interfaces one would be built on.

---

## 6. The unified terminal

The existing terminal, with Earth Mind behind the same input line. A line is
tried in this order and the first that claims it has it:

1. `!command` — the shell, exactly as typed.
2. A real command line (an option flag, a pipe, a redirect) — the shell.
3. Something Earth Mind knows better than a command would: a question the
   machine answers from live readings, an installed program's name, a
   character's name — or anything at all while a character is being talked to.
4. English the 1.0 translator knows — the command it stands for, shown first.
5. A line that starts with a program on this machine, or a built-in — the shell.
6. Anything else that reads as a sentence — Earth Mind.
7. Otherwise the shell, which will say it does not know the command.

Steps 1, 2, 4, 5 and 7 are 1.0's terminal, unchanged, and they are all there
is when Earth Mind is off, cannot be built, or TRANSLATOR is off. `? words`
forces Earth Mind. The header shows who answered last — `SHELL`,
`TRANSLATED`, `SYSTEM`, `CDOS`, `COMPANION`, `SYSTEM + COMPANION`,
`GAME · ELARA` — and `THINKING` while a model is answering.

Two consequences worth knowing:

* **A typed command is yours.** `reboot` typed at the terminal is the program
  of that name, as it was in 1.0; "restart the computer" is a request, and
  waits for a `yes`. The terminal is the declared escape hatch.
* **A destructive command worked out from English waits for a typed `yes`**
  ("delete the file notes.txt" → `rm notes.txt`, held). So does an effectful
  capability Earth Mind proposed.

---

## 7. Memory (`mind/memory.py`)

One SQLite file (`~/.local/share/earth-os/mind/memory.db`), seven kinds of
memory, kept apart by a **namespace** on every record and a **principal** on
every read and write.

| Namespace | What lives there | Kind of memory |
|---|---|---|
| `user` | What the person let this computer remember. *Not stored here*: it is the World Model's ledger (`world.json`), with its own rules. | 1. User |
| `conversation/<persona>` | What was said with an OS personality — only when `[mind] keep_conversations = true`. Otherwise conversation lives in working memory and ends with the session. | 2. Conversation |
| `system` | What the machine was found doing: diagnoses, dated. | 3. System |
| `knowledge` | The CDOS corpus. *Not stored here and never written.* | 4. Knowledge |
| `character/<world>/<id>` | One character's private memories. | 5. Character |
| `world/<world>` | A world's facts, state, relationships, who met whom. | 6. Game world |
| `events/<world>` (and `events/system`) | A world's event history. Append-only. | 7. Event history |

Every record carries its namespace, a kind, a subject, free-form data, an
importance, a visibility (`private`/`public`), who wrote it, and created and
updated timestamps.

Who may touch what is one function, `allowed()`:

| Principal | May |
|---|---|
| `mind` (the coordinator) | Everything except rewriting history. It only ever hands a voice the view that voice is entitled to. |
| `persona` (an OS personality) | Its own `conversation/<id>`. `user` and `system` only if its permissions say so. Nothing of any game. |
| `game` (a world's engine) | `world/`, `events/` and `character/` of *its own* world. Not another world, not `user`, not `system`. |
| `character` | Its own `character/<world>/<id>`, and the *public* records of its `world/`. Nothing else, ever. |

A refusal raises `MemoryAccessError` and is recorded in `store.denials`. It
never returns an empty list instead: an empty list reads as "nothing
remembered", which is a different fact.

Retrieval is word overlap weighted by importance and recency over a bounded
scan (the newest 400 records and the 60 most important), so a turn's cost does
not grow with a character's age. Semantic retrieval stays CDOS's job; this
module does not grow a second one. Chatter is pruned beyond 1,500 records per
namespace; events and facts never are.

---

## 8. Security and permissions

| Requirement | How it is met | Checked by |
|---|---|---|
| No unrestricted OS access | `earth_os/mind/` imports nothing that can act | `test_mind.test_the_mind_package_cannot_act` |
| Read-only inspection | Readings only through `answer_reading` / `answer_analysis`, which refuse anything effectful or parameterised at the moment of running | `test_the_real_shell_refuses_to_take_an_effectful_reading` |
| Approval for destructive actions | English → destructive command is held for a typed `yes` | `test_deleting_worked_out_from_english_waits_for_a_yes` |
| Approval for administrative operations | Effectful capability → `Proposal` → the shell's existing confirm | `test_an_administrative_operation_waits_for_a_typed_yes` |
| Sandboxed game environments | `mind/game/` and `characters.py` may import only `earth_os.mind`, `earth_os.companion` and a short list of the standard library | `test_the_game_package_imports_nothing_that_can_act` |
| Separate memory permissions | Section 7 | `TestMemory` |
| Controlled file access | Earth Mind reads no files but its own JSON definitions and its database | the import test |
| No shell execution of model output | A reply is text. Nothing parses it for commands, and a `Proposal` is built only from the person's own sentence | `test_what_a_model_says_is_never_read_as_a_command` |
| No secrets in model context | `redact()` on every message, at the one place every call passes | `test_secrets_never_reach_a_model` |
| Characters never reach the real machine | Game permissions are `{converse}`; inside a game the router never consults the system subsystem | `test_inside_a_game_nothing_typed_reaches_the_machine` |

Redaction is a tripwire for the common shapes (prefixed tokens, `KEY=value`,
`--password`, private-key blocks, URL credentials, long high-entropy strings),
not a guarantee.

---

## 9. Configuration

`~/.config/earth-os/config.toml`. Every setting has a default; a machine
configured before 2.0 needs no new line.

```toml
[mind]
enabled    = true          # false: the Companion Engine answers on its own
persona    = "companion"   # companion | technician | robot | one of your own
model      = ""            # provider:model[@host]; blank = the [companion] model
game_model = ""            # a smaller model for characters; blank = the same one
explain    = true          # the Companion explains verified system findings
terminal   = true          # false: the terminal is 1.0's
world      = "harbour"     # the demonstration world; "" for none
memory     = ""            # blank = mind/memory.db in the data directory
keep_conversations = false # true: conversations survive a restart

[companion]
enabled = true             # false as well: the 1.0 model path, exactly
model   = "llama3.2:3b"
```

`earth-os --doctor` reports three rows: `computer`, `companion`, `earth mind`.

---

## 10. Measured

`python3 tools/mind_bench.py`, on the machine this release was built on
(2 CPUs, 7.8 GB, Python 3.12.3, no GPU, **no language model available**):

| | |
|---|---|
| Earth Mind built | 48 ms, +8.3 MB — no model call, no reading, no database |
| Routing, per request | 1.0 ms |
| System reading / analysis / diagnosis (findings) | 1.1 ms / 3.7 ms / 6.3 ms |
| CDOS: first question / a sum afterwards | 149 ms / 1.5 ms |
| Memory: write / recall in 1,000 / in 10,000 records | 1.2 ms / 6.0 ms / 10.6 ms |
| 200 characters, a turn each (Earth Mind's share) | 4.2 ms per turn; 4.5 ms with memories |
| …their prompts | 986 characters median, 1,002 max |
| …memory for all 200 | +0.1 MB; 32 working memories kept warm |
| Model clients for the OS and 200 characters | 1 per distinct model |
| Whole process at the end | 47.7 MB |
| **Language model: latency, loading, memory** | **Not measured — none was reachable** |

Everything Earth Mind adds is a few milliseconds a turn, so on a real machine
a turn costs what the model costs. That number is the one this table does not
have; run the tool where Ollama is running and it will fill in.

What was done about performance, on that evidence: one model client per model
rather than per voice; `num_ctx` held fixed so Ollama never reloads between
modes or voices; availability asked at most every 30 seconds; calls
serialised; the store opened on first use; recall bounded; working memories
capped. What was not done, because there was nothing to measure it against:
tuning for a particular model or GPU, batching, streaming.
