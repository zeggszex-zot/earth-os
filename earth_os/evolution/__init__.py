"""The System Evolution Center: where Earth OS examines itself.

It answers five questions, each from readings rather than from guesses:

1. What is Earth OS currently doing?   — :mod:`.inspector`, :mod:`.trends`
2. What is working well?               — :mod:`.health` (the findings that read *normal*)
3. What needs improvement?             — :mod:`.health`, :mod:`.optimizer`
4. What capabilities are missing?      — :mod:`.registry`
5. What should we develop next?        — :mod:`.planner`

**The center proposes; it does not act.** The rule the rest of this package
is built around, and the one ``tests/test_evolution.py`` holds it to:

* Nothing here imports what can change the machine — no platform adapter,
  no ``subprocess``, no agent, no composition runner. Readings arrive through
  the shell's gated ``take_reading``, which refuses anything that is not a
  parameterless, non-effectful reading.
* The engine may *notice* and *write up*. Every other move on the roadmap is
  a person's, recorded with their name.
* One module can change something outside the center's own records:
  :mod:`.changes`, a bounded list of ``config.toml`` settings, applied only
  to an APPROVED proposal and backed up first.
* A model may reword findings. It is never the source of one.

See ``docs/SEC_ARCHITECTURE.md`` for why it is shaped this way and
``docs/EVOLUTION_CENTER.md`` for how to use it.
"""

from __future__ import annotations

from .center import EvolutionCenter
from .model import CATEGORIES, SEVERITIES, STATUSES

__all__ = ["EvolutionCenter", "build_center", "SEVERITIES", "STATUSES", "CATEGORIES"]


def build_center(shell, directory=None) -> EvolutionCenter:
    """The center for *shell*. Takes no reading and writes nothing."""
    return EvolutionCenter(shell, directory=directory)
