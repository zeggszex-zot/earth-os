"""The list of desktop file openers, kept somewhere with no toolkit imports.

This is a leaf module on purpose.  ``--doctor`` reports which openers exist,
and it has to be able to do that on a machine where GTK is broken — which is
precisely the machine someone runs ``--doctor`` on.  Importing
:mod:`earth_os.core` to read the list pulled in Pango through the widgets and
turned a missing-GTK diagnosis into an ImportError in the diagnosis itself.

Each entry is an argv prefix.  The path is appended as its own element, so
spaces and quotes in filenames survive.
"""

from __future__ import annotations

from typing import List, Tuple

OPENERS: Tuple[List[str], ...] = (
    ["xdg-open"],
    ["gio", "open"],
    ["gnome-open"],
    ["kde-open"],
    ["exo-open"],
    ["handlr", "open"],
    ["mimeopen", "-n"],
)
