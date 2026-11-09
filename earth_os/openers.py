"""The list of desktop file openers, kept somewhere with no toolkit imports.

This is a leaf module on purpose.  ``--doctor`` reports which openers exist,
and it has to be able to do that on a machine where GTK is broken — which is
precisely the machine someone runs ``--doctor`` on.  Importing
:mod:`earth_os.core` to read the list pulled in Pango through the widgets and
turned a missing-GTK diagnosis into an ImportError in the diagnosis itself.

Each entry is an argv prefix.  The path is appended as its own element, so
spaces and quotes in filenames survive.

``gio open`` comes first. The EARTH OS session sets ``XDG_CURRENT_DESKTOP``
to its own name, which puts ``xdg-open`` in its "generic" mode — the one that
ignores ``Terminal=true`` handlers and can exit 0 for a program that failed to
start. ``gio`` resolves the same mimeapps defaults itself and exits non-zero
with a reason when it cannot, so a failure there falls through to the next
entry instead of being mistaken for an open file.
"""

from __future__ import annotations

from typing import List, Tuple

OPENERS: Tuple[List[str], ...] = (
    ["gio", "open"],
    ["xdg-open"],
    ["gnome-open"],
    ["kde-open"],
    ["exo-open"],
    ["handlr", "open"],
    ["mimeopen", "-n"],
)
