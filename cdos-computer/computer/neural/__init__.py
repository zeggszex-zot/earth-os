"""CDOS Neural Core: a managed, optional learning subsystem for the reasoning controller.

The network is the existing lightweight controller (computer/controller.py: hashed question-shape features ->
one ReLU layer -> softmax over plan shapes).  What this package adds is everything around it that makes learning
safe to run on a deterministic machine:

    schema.py      the feature schema, versioned and fingerprinted (a model built for another schema won't load)
    records.py     training records: inputs, every candidate action, its validator and executor outcome, provenance
    train.py       reproducible training into a staging directory (fixed seed; CPU, batch, epoch and time limits)
    evaluate.py    rule-only vs untrained vs trained (vs the active model) on identical held-out fixtures; gates
    registry.py    staging -> evaluated -> explicitly approved -> active; append-only audit of every activation
    model.py       checkpoint and manifest validation (missing, corrupt, incompatible: refused with a reason)
    runtime.py     the NeuralPlanner the computer uses, and the loader that never raises anything but NeuralUnavailable
    experience.py  append-only, hash-chained log of predictions and their checked outcomes
    bench.py       inference latency, load time and memory on the machine it runs on

What it can't do, by construction:

  - The network outputs a probability per action name.  Plans come only from the fixed templates in
    computer/controller.py, filled from names the corpus has, and every plan goes through the plan validator
    (computer/plan.py), the executor (computer/navigator.py) and accept() before anything is said.
  - Nothing here writes to the corpus.  The registry refuses to live inside the corpus folder.
  - A model that is missing, corrupt or built for another schema is not loaded, and the computer starts with the
    rules, as it does with no model at all.

`python3 -m computer.neural --help` lists the commands; docs/NEURAL_CORE.md is the design note.
"""

NEURAL_VERSION = "0.1.0"
