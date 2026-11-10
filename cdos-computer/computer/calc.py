"""Logic: the deterministic calculator, ported from cdos-web/js/calc.js.  Never uses eval().

    calculate("what is 15% of 80")         -> {"expr": "(15/100)*80", "value": 12.0, "text": "12"}
    calculate("tell me about jupiter")     -> None
"""

from __future__ import annotations

import math
import re

WORDS = [
    (r"\bwhat(?:'s| is)\b|\bcalculate\b|\bcompute\b|\bhow much is\b|\bsolve\b|\bequals?\b|\?", " "),
    (r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+", r"(\1/100)*"),
    (r"\bhalf of\b", "0.5*"), (r"\b(?:a )?quarter of\b", "0.25*"), (r"\bthird of\b", "(1/3)*"),
    (r"\bdouble\b|\btwice\b", "2*"), (r"\btriple\b", "3*"),
    (r"\bsquare root of\b|\bsqrt of\b", "sqrt "), (r"\bcube root of\b", "cbrt "),
    (r"\bto the power of\b|\braised to\b|\bto the\b(?= \d)", "^"),
    (r"\bsquared\b", "^2"), (r"\bcubed\b", "^3"),
    (r"\bplus\b|\badded to\b", "+"), (r"\bminus\b|\bless\b", "-"),
    (r"\btimes\b|\bmultiplied by\b|\bx\b(?=\s*[\d(])", "*"),
    (r"\bdivided by\b|\bover\b", "/"), (r"\bmod(?:ulo)?\b", "%"),
    ("×", "*"), ("÷", "/"), ("−", "-"),
]
ADD = re.compile(r"^\s*add\s+(.+?)\s+(?:and|to)\s+(.+)$", re.I)
SUB = re.compile(r"^\s*subtract\s+(.+?)\s+from\s+(.+)$", re.I)
MUL = re.compile(r"^\s*multiply\s+(.+?)\s+(?:and|by)\s+(.+)$", re.I)

FUNCS = {"sqrt": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x), "sin": math.sin,
         "cos": math.cos, "tan": math.tan, "log": math.log10, "ln": math.log, "abs": abs,
         "round": round, "floor": math.floor, "ceil": math.ceil, "exp": math.exp}
CONSTS = {"pi": math.pi, "e": math.e}
_TOKEN = re.compile(r"\s*(\d+(?:\.\d+)?(?:e[+-]?\d+)?|[a-z]+|[-+*/^%()])")


class _Fail(Exception):
    pass


def tokenize(s: str):
    s = re.sub(r",(?=\d{3}\b)", "", s)
    out, pos = [], 0
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m:
            if not s[pos:].strip():
                break
            return None
        out.append(m.group(1))
        pos = m.end()
    return out


def _parse(tokens):
    i, ops = 0, 0

    def peek():
        return tokens[i] if i < len(tokens) else None

    def nxt():
        nonlocal i
        if i >= len(tokens):
            raise _Fail
        i += 1
        return tokens[i - 1]

    def primary():
        nonlocal ops
        t = nxt()
        if t == "(":
            v = expr()
            if nxt() != ")":
                raise _Fail
            return v
        if t == "-":
            ops += 1
            return -power()
        if t == "+":
            return power()
        if t[0].isdigit():
            return float(t)
        if t in CONSTS:
            return CONSTS[t]
        if t in FUNCS:
            ops += 1
            return float(FUNCS[t](power()))
        raise _Fail

    def power():
        nonlocal ops
        b = primary()
        if peek() == "^":
            nxt()
            ops += 1
            e = power()
            if abs(e) > 1000:
                raise _Fail
            return b ** e
        return b

    def term():
        nonlocal ops
        v = power()
        while peek() in ("*", "/", "%", "("):
            op = "*" if peek() == "(" else nxt()
            r = power()
            ops += 1
            if op in "/%" and r == 0:
                raise _Fail
            v = v * r if op == "*" else v / r if op == "/" else math.fmod(v, r)
        return v

    def expr():
        nonlocal ops
        v = term()
        while peek() in ("+", "-"):
            op = nxt()
            r = term()
            ops += 1
            v = v + r if op == "+" else v - r
        return v

    v = expr()
    if i != len(tokens):
        raise _Fail
    return v, ops


def fmt(n: float) -> str:
    if not math.isfinite(n):
        return str(n)
    if float(n).is_integer() and abs(n) < 1e15:
        return f"{int(n):,}"
    return f"{float(f'{n:.10g}')}"


def calculate(text: str):
    s = f" {text.lower().strip()} "
    if not re.search(r"\d", s):
        return None
    for pat, rep in WORDS:
        s = re.sub(pat, rep, s, flags=re.I)
    s = re.sub(r"[.!]+$", "", s.strip())
    for rx, build in ((ADD, lambda m: f"({m[1]})+({m[2]})"), (SUB, lambda m: f"({m[2]})-({m[1]})"),
                      (MUL, lambda m: f"({m[1]})*({m[2]})")):
        m = rx.match(s)
        if m:
            s = build(m)
            break
    tokens = tokenize(s)
    if not tokens:
        return None
    try:
        v, ops = _parse(tokens)
    except (_Fail, ValueError, OverflowError, ZeroDivisionError):
        return None
    if not ops or not math.isfinite(v):
        return None
    return {"expr": "".join(tokens), "value": v, "text": fmt(v)}


def spoken_expr(expr: str) -> str:
    """'(15/100)*80' -> '15 / 100 times 80' style text for the voice."""
    s = expr.replace("*", " times ").replace("/", " divided by ").replace("^", " to the power of ")
    s = s.replace("+", " plus ").replace("-", " minus ").replace("sqrt", "the square root of ")
    return re.sub(r"\s+", " ", s.replace("(", " ").replace(")", " ")).strip()
