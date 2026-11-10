# Earth OS 2.0 — Unified Computer Mind

What this release implements, phase by phase against the specification, and
what remains planned. "Implemented" here means the code is in this archive and
a test in `tests/` exercises it; where something was built but could not be
tested in the environment this release was made in, it says so.

## The one caveat that covers everything below

**No language model was available where this was built and tested** — no
Ollama, no GPU, and no network route to download a model. Every path that
calls a model was exercised with the real Ollama client speaking HTTP to a
local stand-in for Ollama, or with a recording fake. So:

* what Earth OS sends a model, when, how often, and what it does with whatever
  comes back — **tested**;
* that it keeps working with no model at all — **tested**;
* what a real `llama3.2:3b` actually says as the Companion or as a character,
  how well it stays in character, how often its explanations pass the
  evidence check, and how long a turn takes — **not tested**.

Before relying on conversation quality, run it against a real model:
`ollama pull llama3.2:3b`, then `earth-os --doctor`, then the terminal.

## Phases

### Phase 1 — Earth Mind foundation · implemented

`earth_os/mind/`: the coordinator (`mind.py`), the structured envelope between
subsystems (`envelope.py`), and CDOS and the Companion connected through
interfaces that already existed (`ComputerLink.ask`, `CompanionEngine.respond`)
plus one new one (`CompanionEngine.explain`). Both implementations preserved:
CDOS Computer is byte-for-byte what arrived; the Companion Engine's own 49
tests pass, changed only in the package name and where they look for the
computer's source.

### Phase 2 — Unified terminal · implemented

The existing terminal screen sends what is not a command through Earth Mind,
shows the processing mode in its header, and is 1.0's terminal exactly when
Earth Mind is off or unavailable. Ordinary Linux commands, the translator,
history, completion and interrupt are unchanged.

### Phase 3 — Shared memory · implemented

One SQLite store with seven namespaced kinds of memory, metadata and
timestamps on every record, and an access check on every read and write.
User memory remains the World Model's ledger and is reached through the same
interface. Conversation memory across restarts is implemented and **off by
default** (`[mind] keep_conversations`).

### Phase 4 — System intelligence · implemented

System questions are answered from the capability layer's real readings; a
diagnosis ("why is my computer slow") is readings first, then the Companion's
explanation, checked against those readings. One analysis was added to the
capability corpus (`performance_report`); no capability was added to the
catalogue.

Covered by readings today: processor load and load average, memory, swap,
storage, temperature, uptime, battery, network status, and the process list
(what is using the most memory).
Not covered: per-process disk or network I/O, GPU, service state, logs.

### Phase 5 — Personality framework · implemented

Personalities as data with identity, manner, context, a derived memory
namespace, goals, relationships, permissions and environment. Three shipped
(Companion, Technical Assistant, Robot Companion); your own load from
`~/.config/earth-os/personalities/`. Multiple simultaneous sessions: each
interface has its own personality, each personality its own conversation, all
on one model client.

### Phase 6 — AI gaming foundation · implemented as interfaces, with one demonstration world

`earth_os/mind/game/`: worlds as data, characters with separate identities and
memories, events with witnesses, directional relationships, a calendar,
persistent history, character-to-character exchanges, characters added at run
time. The shipped world (Greywater Harbour) has four independently configured
characters: a shopkeeper, a harbour master, an antagonist and a narrator.

This is the foundation the specification asked for, not a game: there is no
map, movement, inventory, combat, quest logic or rendering.

### Phase 7 — Optimisation · measured; partly done

`tools/mind_bench.py` measures build time, routing, readings, CDOS, the memory
store and character turns, and reports resident memory. Results for the build
machine are in [EARTH_MIND.md](EARTH_MIND.md) §10. Earth Mind's own overhead
is a few milliseconds a turn and about 8 MB.

**Not done:** anything that depends on a real model's behaviour — latency,
loading time, memory, the right `num_ctx` and prompt budgets for a given
machine. The tool measures these when a model is reachable; none was.

## Deliverables

| Asked for | Where |
|---|---|
| Updated source project | This archive; a git repository with the history |
| Earth Mind coordination layer | `earth_os/mind/` |
| Integrated CDOS and Companion interfaces | `earth_os/mind/mind.py`, `earth_os/companion/`, `earth_os/voice/computer_link.py`, `cdos-computer/` |
| Unified terminal | `earth_os/screens/terminal.py` |
| Memory architecture | `earth_os/mind/memory.py` |
| Personality framework | `earth_os/mind/personas.py`, `earth_os/mind/personalities/` |
| Initial game intelligence interfaces | `earth_os/mind/game/`, `earth_os/mind/characters.py`, `earth_os/mind/worlds/` |
| Installation instructions | [INSTALL.md](INSTALL.md), "Earth Mind (2.0)" |
| Architecture documentation | [EARTH_MIND.md](EARTH_MIND.md) |
| Testing report | [TESTING.md](TESTING.md) |
| Rollback instructions | [ROLLBACK.md](ROLLBACK.md) |

## Changed in existing code

Kept as small as it could be made, and each change is additive or behind a
switch:

| File | Change |
|---|---|
| `earth_os/core.py` | Builds Earth Mind on first use; tier 4 of the voice path goes to it; `carry_out`, `answer_reading`, `find_program`, `open_program` |
| `earth_os/screens/terminal.py` | The order in EARTH_MIND.md §6; a typed `yes` for held operations; the mode in the header |
| `earth_os/capability_corpus.py` | One analysis, `performance_report` |
| `earth_os/config.py` | `[mind]`, `[companion]`, `[computer]`, `[world]` |
| `earth_os/diagnostics.py` | Three doctor rows |
| `earth_os/voice/{backends,cdos_backend,engine}.py` | The computer link, as CDOS LCARS wired it |
| `earth_os/brand.py`, `earth_os/__init__.py`, `pyproject.toml` | Version 2.0 |
| `install.sh` | Ships `cdos-computer/` beside `earth_os/`; uninstall no longer deletes the data directory |
| `tests/test_port.py` | One test waits for the worker thread |

The GUI's drawing code — chrome, splash, widgets, theme, every screen but the
terminal — is untouched.

## Behaviour that changed, on purpose

* A sentence the voice path does not recognise as a command now gets a
  conversational answer instead of *"Unable to comply."*
  (`[companion] enabled = false` restores that).
* In the terminal, a line that is neither a command nor English the translator
  knows used to be handed to the shell to fail; it now goes to Earth Mind
  (`[mind] terminal = false` restores that).
* In the terminal, "delete the file X" used to run `rm -i X`, whose question
  could not be answered in a panel with no keyboard for the program, so
  nothing was deleted. It now asks for a typed `yes` and then deletes.
* `./install.sh --user --uninstall` used to delete `~/.local/share/earth-os`,
  which holds learned meanings and now memories. It removes only what it
  installed.

## Remains planned

Not started, in rough order of how much the rest depends on them:

1. **Validation against a real model**, and tuning from it (prompt budgets,
   `num_ctx`, which model for characters).
2. **A CDOS corpus in the archive.** None came with the source projects, so
   factual questions answer *corpus unavailable* until one is built
   (`cdos-computer/tools/build_shards.py`) and placed in `cdos-computer/data/`.
   Sums, units, time and policy work without one.
3. **Semantic retrieval for memories.** Recall is word overlap, importance and
   recency. Good enough for "do you remember the ledger"; not for paraphrase.
4. **Remote model providers.** The interface is there
   (`ModelManager.register_provider`); only Ollama and the shell's existing
   reasoners are implemented.
5. **Streaming replies**, and interrupting a model mid-answer from the
   terminal.
6. **Voice selection of characters with speech output per character.** The
   voice path reaches characters today, in the one configured voice.
7. **A game.** Movement, places, inventory, quests, a narrator that drives
   scenes, characters acting on their goals unprompted, a simulated society
   ticking between player turns. `exchange()` is the only character-to-
   character behaviour.
8. **Memory consolidation** — summarising a character's old chatter into
   lasting memories instead of pruning it.
9. **The board games from CDOS LCARS** (chess, battle tactics). They need a
   screen and were left where they are.
10. **Robotics.** `robot` is a personality; CDOS Computer's simulated robot
    and safety envelope are in `cdos-computer/` and are not connected to it.
11. **System intelligence beyond the current readings** — services, logs,
    per-process I/O, GPU — each of which is a deliberate addition to the
    capability catalogue, not to Earth Mind.
12. **A game-development tool in the terminal** (inspect a world, edit a
    character). Worlds are edited as JSON today.
