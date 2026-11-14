"""The wall clock: the one place the answer path reads the date.

Deterministic capabilities (computer/capabilities.py) never read the clock themselves.  When a question doesn't say
which year it is about, the year is a declared input whose default comes from here (`default_from:
"system.clock"`), and the result envelope records the year that was used, so the same inputs still give the
same output.  Elapsed-time measurement (time.monotonic, for Result.ms) isn't a date and isn't read here.
"""

from __future__ import annotations

import time


def current_year() -> int:
    return time.localtime().tm_year
