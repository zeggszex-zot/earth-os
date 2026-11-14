# CDOS

The rules for editing this repository are in **`AGENTS.md`**. Read it first —
it is shared with every other assistant that works here, so keeping one copy
is how two agents stay in agreement.

Three that get broken most often:

1. **Run the suite with `/usr/bin/python3.12`**, not `python3`. Under 3.11 the
   GTK modules fail to import and four test files silently do not run.
2. **Never raise an allowance in `tests/test_boundary.py`.** If a change needs
   a higher one, the mechanism is in the wrong place.
3. **If a reading cannot be taken, raise `PlatformError`.** Do not return a
   plausible number. Six separate bugs in this codebase were that mistake.
