"""The CDOS Growth Engine: find what the computer can't do yet, and prepare checked improvements.  It never promotes.

    1 Discover    read the experience log and the turn log; re-ask each failure (it may be fixed already); classify it
    2 Learn       a task file per kind of finding; a person's answers (append-only, signed by name) become examples
    3 Experiment  train a candidate into staging (computer/neural/train.py): the active model and the corpus untouched
    4 Evaluate    the neural core's full evaluation and gates, plus whether the task's own questions are now answered
    5 Grow        nothing automatic: `python3 -m computer.neural promote ... --approve`, as for any model

Approval policy (config growth_approval): "manual" is the only one there is.  Discovery and preparing candidates can run
unattended (systemd/cdos-growth.timer); activation can't.

What it learns from, and what it doesn't:

  - A person's answer to a question the computer couldn't answer: yes.  Each one is checked against the corpus first
    (does some plan shape reach it?), and kept with who gave it and when.
  - The computer's own answers: no.  An answer the rules gave is already said; one nobody checked isn't evidence.
  - Facts: never.  A question that fails because the corpus lacks the fact is reported as a corpus gap, for whoever
    builds the corpus (tools/build_shards.py).  Nothing here writes to data/.

    python3 -m computer.growth discover | tasks | show <task> | answer <task> ... | run <task> | auto
"""

GROWTH_VERSION = "0.2.0"
