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


INTEGRATE_ELSEWHERE = ("corpus.integrate is on, but this package never writes corpus.pf: integrate with "
                      "tools/integrate.py, which writes through the corpus guard (verify, backup, replace).")


class AcquireError(RuntimeError):
    """Refused or failed; str(e) says why."""


def integrate_on(root: Path = DEFAULT_ROOT) -> bool:
    """The corpus.integrate switch: `[corpus] integrate = true` in <root>/settings.toml, written by a person (Q1).
    No file, or anything but exactly that, is off; a file that can't be read is an error, not off-by-accident."""
    import tomllib
    p = Path(root) / "settings.toml"
    if not p.exists():
        return False
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise AcquireError(f"{p} can't be read: {e}") from None
    if set(data) - {"corpus"} or set(data.get("corpus", {})) - {"integrate"}:
        raise AcquireError(f"{p} may hold only [corpus] integrate = true|false")
    v = data.get("corpus", {}).get("integrate", False)
    if not isinstance(v, bool):
        raise AcquireError(f"{p}: corpus.integrate must be true or false")
    return v
