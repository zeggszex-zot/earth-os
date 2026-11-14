# CDOS modifying itself

*What the machine may change about itself, and what must stay human-written.*

The goal is a CDOS that grows without someone hand-writing every capability.
The obstacle is the rule the whole architecture rests on:

> A model may help interpret a request. **It may not extend the machine.**

Those look incompatible. They are not, once you stop treating
"self-modification" as one thing. It is three, and they have different
answers.

---

## 1. The rule this follows from

The boundary rule already sorts code by *what kind of fact it is*. The same
question sorts **change**:

> **Meaning can be learned. Mechanism can be verified. Power must be granted.**

| | What changes | Who may change it | Why that is safe |
|---|---|---|---|
| **Learned** | words, aliases, compositions | the machine, by itself | no new power — only new arrangements of power already granted |
| **Verified** | how a capability is achieved | the machine, if it passes | the contract is unchanged and a harness can prove it |
| **Granted** | what the machine can do at all | **a person, always** | this is the gate; a system that widens its own gate has none |

Everything below is that table with the detail filled in.

---

## 2. The ladder

Five rungs. Each has its own safety mechanism. The fifth is never climbed.

### Rung 0 — learn words *(already working)*

`earth_os/semantics.py`. A model explains an unfamiliar phrase once, the
relationship is written down, and it is never asked again. Aliases carry
confidence, start below the execute threshold, are confirmed or weakened by
use, and only overwrite an established meaning below `CONTEST_FLOOR`.

Safe because a learned alias can only point at something the fixed table
already reaches. It changes what CDOS *understands*, never what it *can do*.

**This is self-modification, and it has been running the whole time.** It is
worth saying plainly, because it sets the pattern: the machine already
rewrites itself daily, within a boundary that makes it uninteresting to
attack.

### Rung 1 — learn compositions *(built: `earth_os/compositions.py`)*

A way to name a sequence of capabilities, needing **no model at all**:

```
"movie mode"  =  display.set_brightness(0.2)
                 audio.set_volume(0.7)
```

A composition is a new **name**, not new **power**. Every step is already in
the catalogue, already permitted, already phrase-able. Nothing about the
machine's reach changes.

Five rules make it safe by construction. All five are checked in
`tests/test_compositions.py`, and the first two were each verified by
deliberately breaking them to confirm the suite bites:

1. **The catalogue stays closed.** Every step names a capability in
   `CATALOGUE`, checked when the composition is *built*. A bad step is refused
   at the door rather than discovered halfway through.
2. **Effectfulness inherits.** One effectful step makes the whole composition
   effectful. Otherwise composition is a laundering route: wrap
   `system.shutdown` in "tidy up" and the press disappears. It is a computed
   property with no field behind it, so a hand-edited file cannot claim
   otherwise.
3. **Compositions are flat.** A step names a capability, never another
   composition. Nesting brings cycles, unbounded expansion, and an
   effectfulness computed transitively through hand-edited data.
4. **Arguments are literals.** `set_brightness(0.2)`, never
   `fullscreen(the focused window)`. The moment a step resolves something at
   run time, a composition stops being data and starts being code.
5. **The store cannot act.** `compositions.py` holds no registry and has no
   `run` method, so the only way to execute one is `Shell.run_composition`,
   which confirms. This codebase has already shipped a bypassed effectful gate
   once, when a caller reached for `registry.invoke` instead of the gated
   path; a convenience runner would rebuild that hole somewhere nobody would
   look.

The original sketch had a third step, `window.fullscreen(focused)`. Rule 4
rules it out — "the focused window" is resolved at run time — and that is the
rule doing its job rather than a gap. A composition that could name "whatever
is in front of me right now" is not data any more.

Two built-ins ship: `status report` (four readings, answered together) and
`movie mode`. Neither is effectful, which is deliberate — shipping a
composition that powers something off would be exactly the convenience this
architecture exists to refuse. Effectfulness inheritance is proven by tests,
not by a built-in that demonstrates it on the person's machine.

**Still to come at this rung:** teaching one by voice — *"call that movie
mode"*. The store, the rules and the persistence are done and
`Shell.define_composition` is the entry point; what is missing is the
utterance pattern that captures a sequence and names it. That is additive and
changes none of the five rules.

Most of what people want from a self-modifying computer is here, and it is
reachable without trusting a model with anything.

### Rung 2 — propose mechanisms *(built: `earth_os/agent/`, see [CLAUDE_CODE.md](CLAUDE_CODE.md))*

A new way to achieve an **existing** capability. Brightness through `ddcutil`
on a desktop monitor; window control through a compositor nobody has written
a backend for yet.

This is machine-writable because it is machine-*checkable*: the capability
contract is unchanged, so the conformance harness can prove the new mechanism
keeps it. The gate is mechanical:

```
1. the diff touches only earth_os/platform/
2. /usr/bin/python3.12 -m unittest discover -s tests -q   passes
3. tests/test_boundary.py                                  allowance not raised
4. tools/conformance.py --compare before.json after.json   no capability disagreed
5. git commit, with that output in the message
```

If any step fails, the change is discarded — not argued with. **The tests are
the arbiter, not the model's explanation of itself.**

This gate now exists as `earth_os/agent/gate.py` and runs after every turn of
the built-in Claude Code agent. It judges any change, not only ones confined to
`platform/` — interface changes are the common case — so step 1 is widened to
"touches nothing human-write-only, and not both sides of the boundary"
(`policy.PROTECTED`, `policy.BOUNDARY_PAIR`). Steps 2, 3 and 5 run as written;
step 3 is part of the suite. It also refuses a suite that shrank. "Discarded"
is `git stash`, named for the reason, so a person can still read what was
attempted.

**Step 4 is not automated yet.** Comparing conformance needs a before-report
from the same machine; after an accepted change below the boundary, run
`make conformance` and `tools/conformance.py --compare` by hand.

### Rung 3 — propose capabilities *(drafted by machine, granted by a person)*

Genuinely new machine power. The model may **draft** the whole thing: the
`_spec` entry, the binding, the adapter method, the phrasings, the tests. It
may not merge it.

A draft arrives as a proposal a person reads and approves. That is not
ceremony — it is the one decision that cannot be delegated without losing the
property that makes CDOS worth building. `system.format_disk` reads like
something that ought to exist, and the only reason it does not is that no
person wrote it down.

Expect this rung to be used rarely. 68 capabilities is not a score to raise.

### Rung 4 — never

The following are **human-write-only**, permanently:

* `earth_os/platform/capabilities.py` — the catalogue and the gate
* `tests/test_boundary.py` — the ratchet
* `tests/test_capabilities.py` — the gate's own tests
* `AGENTS.md` — the rules themselves

A system that can rewrite its immune system has no immune system. This is not
a temporary caution to relax later; it is the invariant that makes every rung
below it safe.

---

## 3. The loop

```
        request the machine cannot satisfy
                      │
                      ▼
         is there a composition of things
         it can already do?              ──yes──►  rung 1, no model needed
                      │no
                      ▼
         is it an existing capability that
         this machine lacks a mechanism for? ──yes──►  rung 2, model drafts,
                      │no                              harness verifies
                      ▼
         it is new power  ──────────────────────►  rung 3, model drafts,
                                                    person grants
```

Every self-modification, at every rung, ends the same way: **a git commit
carrying the verification output.** Not a log line — a commit. The audit trail
is the history, and it is the difference between a system that improves and a
system that drifts.

---

## 4. The model question

### TinyLlama cannot do rungs 2 or 3

1.1B parameters. It cannot hold the boundary rule, the ratchet and 574 tests
well enough to write code that respects invariants it has no way to perceive.
It will produce changes that look plausible and quietly violate the
architecture — the exact failure this codebase is built to catch.

It is a fine **tier-4 interpreter** inside CDOS, for the reason already
established: it is contained, not competent. Do not promote it to author.

### What would actually work locally

For rung 2, a coding model in the 7B–32B range: **Qwen2.5-Coder**,
**DeepSeek-Coder-V2-Lite**, **Codestral**. On a machine that can run 14B
comfortably, 14B is the sensible floor for touching this codebase. Below 7B,
the verification gate will reject nearly everything it produces — which is
safe, but wastes the loop.

*(Model availability moves fast; check what is current before committing to
one. The architecture does not depend on which.)*

### Do not fine-tune a CDOS model

It is expensive, it goes stale on every architectural change, and **you have
already built the alternative.** The specialisation this system needs is
context, not weights:

* `AGENTS.md` — the constitution
* `docs/CDOS_DISTRIBUTION.md` — the boundary contract
* 574 tests — the invariants, executable
* `tools/conformance.py` — the portability proof, with an exit code

That is a better brief than a fine-tune, it updates the moment the code does,
and it works with any model you point at it. **The corpus is the
specialisation** — which is the thesis of this whole project, applied to its
own development.

---

## 5. Why CDOS is unusually well placed for this

Most attempts at self-modifying software fail on verification: the system can
write code but cannot tell whether the code is right, so "self-improvement"
becomes drift with extra steps.

CDOS already has the immune system:

| Asset | What it catches |
|---|---|
| 574 tests | behaviour regressions |
| closed catalogue | a model inventing machine power |
| boundary ratchet | mechanism leaking above the line |
| corpus drift-guard | a capability nobody can ask for |
| conformance harness | a change that breaks portability, with an exit code |
| effectful gate | anything irreversible running unasked |

Self-modification is exactly as safe as the verification behind it. That
verification exists, it runs in four seconds, and it has already caught six
real defects during ordinary development.

**Rung 1 is built.** It delivers most of what "a computer that modifies
itself" is actually wanted for, it needed no model, and it is safe by
construction rather than by trust — the five rules are structural, and
breaking either of the first two deliberately makes the suite fail.

**Rung 2 is built** (`earth_os/agent/`), and it is the first that needs a
model at all. The gate, not the model, decides what is kept.
