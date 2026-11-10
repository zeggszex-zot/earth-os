# The CDOS World Model

A companion that can be asked *"want to play Battle Tactics?"* has to know
which games exist, how they work, what the person has made with them and what
the person likes. That knowledge belongs to **CDOS**, not to the model that
happens to be putting it into sentences. Switch from Llama to a cloud model
and nothing about the person or the machine is lost, because none of it ever
lived in the model.

Code: `earth_os/world_model.py`. Tests: `tests/test_world_model.py`.

```
            ┌────────────── any reasoner (cdos / ollama / cloud) ──────────────┐
            │            gets Shell.reasoning_context(): readings + briefing    │
            └───────────────────────────────▲──────────────────────────────────┘
                                            │ briefing() — plain text, short
┌───────────────────────────── WorldModel (assembled on demand) ─────────────────────────────┐
│  self          CATALOGUE · registry.supported · compositions · [[programs]]   (derived)     │
│  applications  apps/*.json manifests, each checked against CATALOGUE           (derived)     │
│  relations     which app lends which mechanic to which                         (derived)     │
│  user          UserLedger.remembered() only                                    (persisted)   │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Five rules

1. **Self-knowledge is derived, never stored.** The catalogue, compositions
   and application index are read each time a snapshot is taken. A stored
   copy of "what I can do" would be a second catalogue, and the second copy is
   always the stale one. Only the person's ledger is persisted.
2. **Nothing is remembered about a person without the person.**
   `remember()` is the person saying so. `observe()` — a game noticing a
   preference, a model inferring a habit — is only ever *pending*, never
   reaches a model, and lapses after seven days unless kept. There is no
   argument to `observe()` that changes this, and a hand-edited file that
   marks an observation as remembered is read back as pending.
3. **Forgetting is one step.** *"Forget that…"*, *"forget everything about
   me"*, or deleting `world.json`.
4. **An application is data, not power.** A manifest's `capabilities` must
   all be in `CATALOGUE`, checked when it is read. Effectfulness is inherited
   from the catalogue, as with compositions. Mechanics are just shared names.
5. **Failure is data.** A manifest that cannot be read is listed in
   `refused` with its reason.

## Talking to it

| Say | What happens |
|---|---|
| *"Remember that I prefer cooperative games."* | Kept, labelled `preference` |
| *"Remember I'm building a rover."* | Kept, labelled `project` |
| *"What do you know about me?"* | Reads back what was kept |
| *"What have you noticed?"* | Lists pending notes, numbered |
| *"Keep note 2"* / *"Discard note 2"* | Answers one |
| *"Forget that I like chess."* | Removes every note mentioning it |
| *"Forget everything about me."* | Empties the ledger |
| *"What are you?"* / *"What can you do?"* | Describes itself from the snapshot |

These are matched only at the start of an utterance, so *"I forget where my
files are"* is not an instruction.

## Applications and games

Drop a manifest in `~/.local/share/earth-os/apps/`:

```json
{
  "name": "battle-tactics",
  "title": "Battle Tactics",
  "kind": "game",
  "summary": "Squad-level tactics on a hex map",
  "capabilities": ["audio.speak"],
  "provides": ["combat.dice", "board.hex"],
  "uses": ["ai.opponent"]
}
```

`provides` / `uses` are **mechanics**: named pieces of behaviour one
application can lend another. `ApplicationIndex.relations()` lists every
link, and `combine("risk", "chess")` answers *"Risk, but with the combat from
our chess game"* with a plan — which of chess's mechanics Risk would borrow.
It is a plan, not an action: the World Model runs nothing.

An application reports what it noticed through `shell.ledger.observe(text,
subject=app.name, origin=app.name)`. That is the only way in for anything
that is not the person.

## Configuration

```toml
[world]
enabled  = true
observe  = true      # false: apps cannot even suggest notes
briefing = true      # false: the model is told nothing from the world model
```

## Not built yet

- ~~No games ship.~~ Three do, built from components: see
  [GAMES.md](GAMES.md). Each is registered here as an application with
  `derived_from`, so the companion knows Battle Tactics Chess came from
  chess + Battle Tactics.
- **No screen.** Pending notes are answered by voice. A MEMORY panel on the
  Parameters screen is the obvious next presentation — and it is only a
  presentation, since the decisions are all in `core.py` and this module.
- **`combine()` produces a plan, not a game.** Turning a plan into a playable
  variant is application code, or a Claude Code change behind the gate.
