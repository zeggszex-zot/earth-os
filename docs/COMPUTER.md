# CDOS Computer in the shell

CDOS has two halves, and until this change the shell talked to one of them.

| | knows | asked through | lives in |
|---|---|---|---|
| **cdos-bridge** | the machine's state: memory, storage, network, GODCORE | a fixed READ-only phrase table | the `cdos` / `cdos-bridge` binaries |
| **CDOS Computer** | facts, sums, plans checked against evidence, who may ask what | `/v1/ask` on the `cdos-api` service | `cdos-computer/` beside this tree |

[CDOS.md](CDOS.md) ended with the second one as "a reasonable next step": let
the computer answer "what moons does Jupiter have" with the same
never-invents-a-fact guarantee. That is what this is.

## The pipeline now

```
    utterance
        │
        ├─ 1. LCARS vocabulary, compositions, capability corpus   (unchanged)
        │
        └─ 2. CdosReasoner
                 │
                 ├─ cdos-bridge listen      READ-only table           (when installed)
                 │     ACCEPTED / AMBIGUOUS → as before
                 │     NO_MATCH ↓
                 │
                 ├─ CDOS Computer /v1/ask   corpus, calculator, policy
                 │     final  → spoken exactly as given
                 │     NOT FOUND / UNSUPPORTED / no corpus ↓
                 │
                 └─ a model                 may propose a command (validated by
                                            cdos-bridge propose); may not state a fact
```

## What may answer in the computer's place

`Answer.final` decides, in `voice/computer_link.py`, and the tests in
`tests/test_computer_link.py` hold it there:

| the computer said | final? | what the person hears |
|---|---|---|
| a fact from the corpus, with evidence | yes | the computer's sentence, verbatim |
| a calculation | yes | verbatim |
| a stated conflict, a refused unit | yes | verbatim |
| `NOTICE:` (health, money, safety, care) | **always** | verbatim; no model ever sees the question |
| the random sampler (`SAMPLER OUTPUT (random, non-factual)`) | yes | verbatim, labelled as the computer labels it |
| `NOT FOUND` | no | the computer's NOT FOUND, unless `free_answers = true` |
| `UNSUPPORTED` (device control, internet) | no | as NOT FOUND |
| `ERROR: corpus unavailable` | no | as NOT FOUND |
| nothing (unreachable) | no | a model if there is one; else `Knowledge core unreachable. <why>` |

Two consequences worth saying out loud:

* **The computer's words are not reworded.** `[cdos] wording` still applies to
  cdos-bridge's narration facts. It does not apply to the computer, which has
  already checked its own sentence against its own evidence (`proposer.check`);
  a model that rephrased it could change what it claims.
* **A model never fills a gap in the corpus by default.** When the computer
  says NOT FOUND, a model may still be asked — but only so that it can propose
  a command for cdos-bridge to validate. If it answers in prose instead, the
  prose is thrown away and the NOT FOUND is spoken. With no cdos-bridge there
  is no validator for a command, so the model is not asked at all. Set
  `free_answers = true` to hear the model's prose; it is then prefixed
  *"Not in the corpus. Unverified model answer:"*.

## Two ways in

```toml
[computer]
enabled      = true
mode         = "auto"               # auto | socket | embedded | off
socket       = "/run/cdos/api.sock" # the cdos-api service
port         = 0                    # instead of a socket: 127.0.0.1 only
source       = ""                   # embedded: where cdos-computer/ is
config       = ""                   # embedded: the computer's own config.toml
free_answers = false
```

**socket** is the production path. `cdos-api` runs as its own user under
systemd, sandboxed, with no network. The kernel tells it who is connecting
(`SO_PEERCRED`), so the Governor decides with a verified identity — the
shell's user gets what `governor.json`'s `"default"` grants (`read`, `ask`)
and nothing privileged. The socket is `0660` in a `0750` directory owned by
the `cdos` group; being in that group is how a person is allowed to ask. The
shell needs none of the computer's code for this: it speaks HTTP over the
socket with the standard library.

**embedded** is for a checkout with no service: `python3 -m earth_os
--windowed` straight from the merged repository. The computer's `Platform` is
built inside the shell's process on the first question (never during boot,
never on the frame clock), and serves only `ask` and `health` — what the
Governor's default roles allow anyway. Anything privileged needs the service.
With no `config` named, the computer's log goes to the shell's data directory
and its name and user follow `[shell] computer_name` and `operator`.

**auto** decides per question, so a service started after the shell is picked
up without a restart:

1. the socket exists → the service;
2. this machine has the service installed (`/etc/cdos-computer/config.toml`)
   → the service, *even if it is down*. Asking then fails and says *"is
   cdos-api.service running?"*. An embedded computer on such a machine could
   not read the service's corpus (`/var/lib/cdos` is the service user's) and
   would answer worse while looking fine;
3. otherwise, the computer's code if it can be found: `source`, then
   `$CDOS_COMPUTER_SOURCE`, then beside this tree (`../cdos-computer`, which
   is also `<prefix>/lib/cdos-computer` after the top-level `install.sh`), then
   `~/.local/lib/cdos-computer`, `/usr/local/lib/cdos-computer`,
   `/usr/lib/cdos-computer` and `/opt/cdos-computer/current`.

## Where it shows

* **Engineering** — the panel beside System Identification reads *Computer
  Core*: link, corpus ready or not, its content-hash version and integrity,
  the active neural model (or *rules only*), the Governor (*ok*, *no policy*,
  *DEGRADED*), the emergency-stop latch, plugins and capabilities switched
  off. With cdos-bridge installed too, GODCORE's rows follow. Refreshed on a
  worker every six seconds, like GODCORE.
* **Status line** — after an answer: *computer · knowledge · ESTABLISHED · 2
  facts*, so a fact from the corpus, an inference, a calculation and the
  random sampler are told apart.
* **Settings → VOICE** — a COMPUTER row with the link's status and last error.
* **`earth-os --doctor`** — a `computer` line: the service's corpus and
  Governor, or where the embedded code would come from. It does not start an
  embedded computer; that would load a corpus.

## What is deliberately not wired up

**Robot motion, e-stop clearing, model promotion.** The API has them; the
shell does not call them. They need roles the shell's user should not hold,
and a voice path into a robot is exactly the kind of thing the boundary rules
exist to stop. Engaging the emergency stop is open to anyone and would be the
first candidate if one is ever wanted — as a capability in the closed
catalogue, with phrasings, not as a shortcut here.

**Wording by a model.** See above.

**The computer as a capability.** Asking it is reasoning, not something the
machine does, so it is not in `platform/capabilities.py` and nothing in the
catalogue changed. `tests/test_boundary.py` is untouched: the link names no
Linux mechanism.

## Running the tests

```sh
/usr/bin/python3.12 -m unittest tests.test_computer_link -v
```

Twelve of them start the real `cdos-api` from `../cdos-computer` in its own
process, on a private socket, with the computer's own test corpus, and ask it
through the link, the reasoner and the whole shell; seven more run the
computer embedded. Both groups are skipped, with that reason, if
`cdos-computer/` is not beside this tree.
