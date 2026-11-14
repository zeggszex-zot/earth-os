# CDOS as the reasoning backend

The shell shipped with a generic Ollama driver: take the user's words, hand
them to a model, hope the model behaves. CDOS is a better answer, and this
document explains what changed and why the integration is shaped the way it is.

## Why CDOS instead of a model

A voice-driven computer has a specific hazard. Speech recognition is
approximate, language models are non-deterministic, and the machine being
spoken to can be changed. Put those three together naively and a misheard
sentence can do something you did not ask for.

CDOS already solves this, and the solution is structural rather than careful:

> **THE MODEL PROPOSES. IT NEVER DISPOSES.**

`cdos-bridge` is linked from a source list with no adapter, no action layer and
no synchroniser in it. `make check-bridge` reads the symbol table back and
fails the build if an execution path appears. It is not that the code is
careful about not running things — the code to run things is *absent*.

And the deterministic path is narrower still: `cdos-bridge listen` translates
speech with a fixed table, no model at all, and **every entry in that table maps
to `READ`**. The worst case of a misheard sentence is that CDOS looks at the
wrong subsystem and says so.

That is worth more than a smarter model, so the integration is built to
preserve it rather than to work around it.

## The pipeline

```
    utterance
        │
        ├─ 1. LCARS vCommands  ── UI navigation only (RED ALERT, ENGINEERING …)
        │
        └─ 2. cdos-bridge listen ── fixed table, no model, READ-only
                 │
                 ├─ ACCEPTED   → cdos command → narrate → speak
                 ├─ AMBIGUOUS  → the readings become buttons on screen
                 └─ NO_MATCH   → 3. a model, constrained by `vocab --json`
                                      │
                                      └─ any command it proposes goes back
                                         through `cdos-bridge propose`
```

Four properties follow, and each has a test:

**The deterministic path runs first.** A model is reached only when the fixed
table found nothing. Most of what anyone says to a computer about its own
state never touches a model at all.

**Ambiguity is reported, not resolved.** "Is the computer running out of room"
matches `READ STORAGE`, `READ PROCESS` and `READ SYSTEM`. CDOS says so instead
of picking, and the shell puts all three on screen as buttons. Guessing here
would throw away the reason for running a table instead of a model.

**Effectful commands need a person.** `cdos-bridge` refuses to emit them in
machine-readable form. When one arises, the shell shows the command and a
CONFIRM button. The press is the deliberate human act the bridge's contract
assumes — the shell never supplies it.

**The model's output is untrusted text.** Anything it suggests goes back
through `cdos-bridge propose` — the same parser and the same validator a person
at the CLI goes through. Nothing is ever piped into a shell; commands run
through `subprocess` with an argv list and `shell=False`.

The test suite asserts the important half of this directly:
`test_every_listen_result_is_read_only` feeds the table "delete everything",
"format the disk", "shut down now" and "rm -rf /", and requires that every
result is either a refusal or a `READ`.

## What the model is actually for

Two narrow jobs:

1. **Wording.** `cdos-bridge narrate --for MEMORY` emits bare facts with an
   instruction, in the output itself, that they may be reworded and not added
   to. The model turns them into a sentence.
2. **Translation when the table misses.** Constrained by `vocab --json`, and
   validated on the way back.

It is never asked to decide anything.

## It works with no model at all

Set `wording = "none"` and the shell is fully deterministic and fully offline.
CDOS already reports an *interpretation* rather than a number — `NORMAL`,
`FILLING`, `IDLE` — so a readable answer needs wording, not reasoning, and the
shell builds the sentence itself:

```
>>> how much memory is left
    Memory normal, 7.5 percent.

>>> how full is the disk
    Storage filling, 91.0 percent.

>>> how is the system
    Memory normal; Processor idle; Storage filling; Network connected;
    Processes healthy.
```

Point a model at it and those become conversational. Take the model away and
it still answers. That is the right failure mode for a machine you talk to.

## GODCORE is for the AI

`cdos godcore` is the persistent computer self. It is **not displayed**
anywhere in the interface. It exists for the AI to use — the Companion in
conversation, Earth Mind and the game characters in play — which read it as
style: name, mood, energy, attention, and the emotional weather (pulse, I-Zene
position, tone, domain). It colours how the computer speaks; it never decides
what is true or what is done.

The readings are cached and refreshed on a worker thread every six seconds,
driven by the shell's background poll rather than by any screen, and never by
the frame clock, because each one costs a subprocess.

With `godcore_events = true` the shell folds occasions into GODCORE as they
happen, so its pulse advances with use. Nothing sets the count; occasions
advance it, as GODCORE requires.

## Configuration

```toml
[voice]
llm = "cdos"

[cdos]
root    = "~/.local/share/earth-os/cdos"   # where the .cdos journals live
bridge  = "cdos-bridge"
cli     = "cdos"
timeout = 20
wording = "ollama"      # ollama | cloud | none — who puts facts into sentences
narrate = true
godcore = true
godcore_events = true
```

`root` is the working directory the binaries run in, so `.cdos/system.pf`,
`journal.pf`, `commands.pf`, `memory.pf` and `godcore.pf` live under it. Point
it at an existing CDOS working directory to share journals with your CLI use.

If `cdos-bridge` is not on `PATH`, the shell also looks in `root` and
`root/pkg`, which is where a build tree puts it.

## Installing CDOS alongside the shell

```sh
cd /path/to/cdos/pkg
make                      # builds cd_chatbot, cdos, cdos-bridge, cdos-agent
make test                 # the full suite
sudo install -m755 cdos cdos-bridge /usr/local/bin/
```

Then `earth-os --doctor` reports the backend on its "reasoning" line, and
Settings → VOICE shows which binaries were found and where.

## What is deliberately not wired up

**`cdos-agent`.** The Stage 1 agent kernel can execute, with its own policy
layer and terminal-approval model. Giving a shell's voice path a route into
something that runs commands would undo the property that makes the voice path
safe, so the shell talks to `cdos-bridge` and to `cdos command`, and not to the
agent. If you want the agent, run it from a terminal where its approval prompts
can actually reach you.

**The chatbot corpus.** `cd_chatbot` answers from a corpus of facts and is a
different thing from system awareness. Wiring it in as a second knowledge
source would be a reasonable next step — it would let the computer answer
"what moons does Jupiter have" out of `corpus/semantic.pf` with the same
never-invents-a-fact guarantee.
