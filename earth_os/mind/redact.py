"""Keeping secrets out of a model's context.

Everything Earth Mind sends to a language model passes through
:func:`redact` on the way out — one choke point, in
:class:`~earth_os.mind.models.ManagedModel`, rather than a rule every caller
has to remember. A command line read from the process list, a path typed into
the terminal or a note somebody asked to keep can each carry a credential,
and a model's context is the one place in this system that may leave the
machine (a remote backend) or be echoed back in a reply.

It is a tripwire for the common shapes, not a guarantee: tokens with a known
prefix, ``KEY=value`` pairs whose key says what the value is, ``--password``
style flags, private-key blocks, credentials inside a URL, and long
high-entropy strings. What it removes is replaced by ``[REDACTED]`` so the
sentence still reads, and the count is reported so a test — and a person
reading the status line — can see it fired.
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Tuple

MARK = "[REDACTED]"

_SECRET_KEY = r"(?:pass(?:word|wd|phrase)?|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|" \
              r"auth(?:orization)?|credential|bearer|session[_-]?id|client[_-]?secret)"

_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    # A private key, whole.
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
                               re.S)),
    # Tokens that announce themselves by prefix.
    ("token", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
                         r"xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
                         r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})")),
    # user:password@host inside a URL.
    ("url-credentials", re.compile(r"(?<=://)[^\s/:@]+:[^\s/@]+(?=@)")),
    # Authorization: Bearer abc…
    ("bearer", re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}")),
)

# PASSWORD=hunter2, api_key: "…", --token abc, --password=abc. The key stays;
# the value goes.
_ASSIGN = re.compile(
    r"(?i)((?:--?)?\b[\w.-]*" + _SECRET_KEY + r"[\w.-]*\s*(?:=|:|\s)\s*)"
    r"(\"[^\"\n]{1,200}\"|'[^'\n]{1,200}'|[^\s,;\"']{3,200})")

# Words that end a "password is …" sentence without being the secret.
_NOT_A_VALUE = {"is", "was", "the", "a", "an", "to", "for", "of", "and", "or", "not", "in", "on",
                "required", "needed", "wrong", "incorrect", "expired", "missing", "invalid", "reset",
                "manager", "prompt", "field", "policy", "file", "store", "protected", "none", "null",
                "true", "false", "yes", "no", MARK.lower()}

_LONG = re.compile(r"\b[A-Za-z0-9+/_=-]{32,}\b")


def _entropy(text: str) -> float:
    counts: Dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    total = float(len(text))
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def redact(text: str) -> Tuple[str, int]:
    """*text* with anything that looks like a credential replaced. Returns (text, how many)."""
    if not text:
        return text, 0
    found = 0

    for _name, pattern in _PATTERNS:
        text, n = pattern.subn(MARK, text)
        found += n

    def assign(match: "re.Match[str]") -> str:
        nonlocal found
        value = match.group(2).strip("\"'")
        if value.lower() in _NOT_A_VALUE or value == MARK:
            return match.group(0)
        found += 1
        return match.group(1) + MARK

    text = _ASSIGN.sub(assign, text)

    def long_string(match: "re.Match[str]") -> str:
        nonlocal found
        token = match.group(0)
        # A file path is long and mixed too, and a process list is full of
        # them. Its parts are short; a key's are not.
        if "/" in token and max(len(part) for part in token.split("/")) < 24:
            return token
        # Long, mixed and dense: a key, not a word or a path component.
        if _entropy(token) >= 3.6 and re.search(r"\d", token) and re.search(r"[A-Za-z]", token):
            found += 1
            return MARK
        return token

    text = _LONG.sub(long_string, text)
    return text, found


def redact_messages(messages: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], int]:
    """A chat's messages, each redacted. The originals are not modified."""
    out, total = [], 0
    for message in messages:
        content, n = redact(str(message.get("content", "")))
        total += n
        out.append({**message, "content": content})
    return out, total
