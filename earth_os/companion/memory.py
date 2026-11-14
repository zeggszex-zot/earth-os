"""What the companion may remember about the person: what the person allowed.

A thin reader and a narrower writer over the World Model's ledger
(``world_model.UserLedger``), which already has the rules:

* only **remembered** entries are ever read into a prompt, and only the ones
  that bear on what was just said;
* the companion's one way to add anything is ``UserLedger.observe`` — always
  **pending**, outside every prompt, until the person says "keep note N";
* nothing a model says is stored. Notes come from the person's own words,
  matched by fixed patterns here, never from a reply.

"Remember that…", "forget…", "what have you noticed" and "keep note N" are
the World Model's own sentences and are handled by the shell before the
companion sees a turn.
"""

from __future__ import annotations

import re
from typing import List, Optional

_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "is", "are", "was", "it", "i", "me", "my",
         "you", "your", "that", "this", "with", "for", "about", "what", "do", "does", "how", "be", "at",
         "like", "so", "just", "think", "really", "we", "can", "have", "has"}

#: The person stating something about themselves, in their own words.
NOTICE = (
    (re.compile(r"^i (?:really |absolutely )?(love|like|enjoy|adore|prefer) ([a-z][\w' -]{2,60})$"), "preference"),
    (re.compile(r"^my fav(?:ou)?rite ([a-z][\w' -]{1,30}) is ([a-z0-9][\w' -]{1,40})$"), "preference"),
    (re.compile(r"^i(?:'m| am) (?:working on|building|making|writing|learning) ([a-z][\w' -]{2,60})$"), "project"),
)


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _STOP and len(w) > 2}


class CompanionMemory:
    def __init__(self, ledger=None, observe: bool = True, limit: int = 5):
        self.ledger = ledger
        self.observing = observe
        self.limit = limit

    def relevant(self, text: str, always: int = 0) -> List[str]:
        """Remembered entries that share a word with *text*; pending never.

        *always* adds the most recent remembered entries regardless, for the
        modes that can afford a little more of the person in view.
        """
        if self.ledger is None:
            return []
        kept = self.ledger.remembered()          # the World Model's rule: remembered only
        said = words(text)
        hits = [e for e in kept if said & (words(e.text) | words(e.subject))]
        for e in reversed(kept[-always:] if always else []):
            if e not in hits:
                hits.append(e)
        return [e.text for e in hits[: self.limit]]

    def notice(self, sentence: str) -> Optional[object]:
        """File what the person said about themselves as a pending note."""
        if self.ledger is None or not self.observing:
            return None
        clean = " ".join(re.sub(r"[^\w\s'-]", " ", sentence.lower()).split())
        for pattern, kind in NOTICE:
            m = pattern.match(clean)
            if not m:
                continue
            if kind == "preference" and m.re is NOTICE[1][0]:
                text = f"favourite {m.group(1)} is {m.group(2)}"
            elif kind == "preference":
                text = f"{m.group(1)}s {m.group(2)}"
            else:
                text = f"working on {m.group(1)}"
            entry = self.ledger.observe(text, kind=kind, origin="companion")
            if entry is not None:
                self.ledger.save()
            return entry
        return None
