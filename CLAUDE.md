# CDOS

The rules for editing this repository are in **`AGENTS.md`**. Read it first —
it is shared with every other assistant that works here, so keeping one copy
is how two agents stay in agreement.

Three that get broken most often:

1. **Run the suite with the system interpreter, `/usr/bin/python3`**: Python
   3.13.5 on this machine. There is no `/usr/bin/python3.12` here. Not a
   venv's Python, and not another `python3.x`: PyGObject is built for the
   system's Python, and under any other interpreter the GTK modules fail to
   import and four test files silently do not run. Check before trusting a
   green run: `/usr/bin/python3 -c "import gi, cairo"` must print nothing.
2. **Never raise an allowance in `tests/test_boundary.py`.** If a change needs
   a higher one, the mechanism is in the wrong place.
3. **If a reading cannot be taken, raise `PlatformError`.** Do not return a
   plausible number. Six separate bugs in this codebase were that mistake.
