"""More than true and false: what the computer can honestly say about a yes/no question.

    TRUE      the corpus supports it (a fact, or a chain of facts)
    FALSE     the corpus contradicts it, through a stated rule ("nothing is both a fish and a mammal")
    UNKNOWN   there isn't enough evidence either way.  Missing isn't "no".
    CONFLICT  the evidence supports both sides.  The computer says so instead of picking one.

The connectives are Kleene's strong three-valued logic, with CONFLICT added as "can't be settled":
a value that settles the answer on its own wins (one FALSE settles an AND, one TRUE settles an OR);
otherwise CONFLICT outranks UNKNOWN, because "my corpus disagrees" is more than "my corpus doesn't say".

    AND       any FALSE -> FALSE;  else any CONFLICT -> CONFLICT;  else any UNKNOWN -> UNKNOWN;  else TRUE
    OR        any TRUE  -> TRUE;   else any CONFLICT -> CONFLICT;  else any UNKNOWN -> UNKNOWN;  else FALSE
    NOT       swaps TRUE and FALSE; UNKNOWN and CONFLICT stay as they are
    NOR       NOT (OR ...)

Truth is kept apart from relevance and confidence.  A fact ranked 0.9 relevant isn't "90% true", and
nothing here turns a score into a truth value.
"""

from __future__ import annotations

TRUE, FALSE, UNKNOWN, CONFLICT = "TRUE", "FALSE", "UNKNOWN", "CONFLICT"
VALUES = (TRUE, FALSE, UNKNOWN, CONFLICT)


def check(v: str) -> str:
    if v not in VALUES:
        raise ValueError(f"not a truth value: {v!r}")
    return v


def and_(*vs: str) -> str:
    vs = [check(v) for v in vs]
    if FALSE in vs:
        return FALSE
    if CONFLICT in vs:
        return CONFLICT
    if UNKNOWN in vs:
        return UNKNOWN
    return TRUE


def or_(*vs: str) -> str:
    vs = [check(v) for v in vs]
    if TRUE in vs:
        return TRUE
    if CONFLICT in vs:
        return CONFLICT
    if UNKNOWN in vs:
        return UNKNOWN
    return FALSE


def not_(v: str) -> str:
    return {TRUE: FALSE, FALSE: TRUE}.get(check(v), v)


def nor_(*vs: str) -> str:
    return not_(or_(*vs))


def combine(op: str, vs: list[str]) -> str:
    op = op.upper()
    if op == "NOT":
        if len(vs) != 1:
            raise ValueError("NOT takes one value")
        return not_(vs[0])
    fn = {"AND": and_, "OR": or_, "NOR": nor_}.get(op)
    if fn is None:
        raise ValueError(f"unknown connective {op!r}")
    if len(vs) < 2:
        raise ValueError(f"{op} takes two or more values")
    return fn(*vs)


def both(supported: bool, contradicted: bool) -> str:
    """Evidence for and against -> a truth value."""
    if supported and contradicted:
        return CONFLICT
    return TRUE if supported else FALSE if contradicted else UNKNOWN
