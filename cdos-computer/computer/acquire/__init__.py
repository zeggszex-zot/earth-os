"""Acquisition: approved sources -> verified downloads -> staged, checked rows.  Phase 2 of the corpus growth plan
(docs/CORPUS_GROWTH_AUDIT.md §6.2, §8).

    registry   sources.toml: what may be fetched, under what licence, mapped how, approved by whom (Q1)
    fetch      download with resume (HTTP Range), size limit, bandwidth limit, sha256 check; failures quarantined
    pipeline   parse -> map -> normalise -> validate -> dedupe -> conflicts -> staging/<source>/<run>/
    index      the dedupe index of the master's fact keys (computer/pfrow.py: the build's own key)

Nothing here writes corpus.pf.  The master is only read, under the corpus guard's shared lock when it is guarded, to
build the dedupe index.  Integration (corpus.integrate) is off until phase 8 passes (Q1): `integrate` refuses.
"""

from pathlib import Path

DEFAULT_ROOT = Path("~/cdos/acquire").expanduser()
DEFAULT_MASTER = Path("~/cdos/corpus/corpus.pf").expanduser()
INTEGRATE_OFF = ("corpus.integrate is off (Q1, docs/CORPUS_GROWTH_AUDIT.md §9): nothing is written to corpus.pf until "
                 "phase 8 passes. Staged runs stay in staging/ for review.")


class AcquireError(RuntimeError):
    """Refused or failed; str(e) says why."""
