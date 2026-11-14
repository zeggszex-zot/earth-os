"""Conversation profiles: how freely the companion talks (docs/COMPANION.md).

A profile is the set of dials that decide how a conversational turn is
prompted and how long and how loosely the model may answer it. There are two:

``grounded``
    Earth OS 2.0's behaviour, exactly: short replies, moderate temperatures, a
    prompt of a few thousand characters, facts only from the corpus, and a
    worded fact replaced by the computer's own sentence if any figure strays.
    It is built from the constants in :mod:`.context`, :mod:`.model` and
    :mod:`.session`, so it cannot drift from them.

``open``
    The same engine talking the way a conventional chat model does: replies
    of several paragraphs, higher temperatures, a prompt several times larger
    with more of the conversation in it, the model's own general knowledge
    allowed alongside the corpus, and a question the corpus cannot answer
    answered by the model and marked unverified.

What a profile may change is how much is said and how it is worded. What no
profile changes is decided before a profile is consulted, and the tests hold
it identical across both:

* sensitive turns (self-harm, health, money, law) never reach a model;
* a task is handed back to the command path; the companion cannot act, and
  both profiles' rules say so, because a model that is not told will say it
  opened a program it did not open;
* a factual question goes to CDOS Computer first, and when the corpus answers
  with a figure that figure survives or the computer's own sentence is said;
* sums are the calculator's, verbatim;
* system readings are explained under the grounded rules whatever the
  profile (:meth:`CompanionEngine.explain`): an invented figure about this
  machine discards the explanation;
* memory: only what the person asked to be kept reaches a prompt.

Nothing here imports anything that acts, or anything outside this package.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple, Union

from . import context as ctx
from .intent import CASUAL, DEEP, REFLECTIVE
from .model import LIMITS
from .session import MAX_TURNS, TURN_CHARS

#: Prompt characters per token when deciding whether a turn fits the model's
#: context. English runs nearer four; three leaves room for the chat template.
CHARS_PER_TOKEN = 3.0
#: Tokens left free in the context for the template and rounding.
HEADROOM = 64


@dataclass(frozen=True)
class Profile:
    name: str
    #: Per mode: (num_predict, temperature) — what :data:`.model.LIMITS` is for ``grounded``.
    limits: Dict[str, Tuple[int, float]]
    #: Per mode: characters of prompt.
    budget: Dict[str, int]
    #: Per mode: recent exchanges offered, before the budget has its say.
    history: Dict[str, int]
    rules: str
    mode_lines: Dict[str, str]
    kind_lines: Dict[str, str]
    #: Said to the model when the corpus has nothing: on an ``explore`` turn,
    #: on any other turn, and on a factual turn it is allowed to answer anyway.
    not_found_explore: str
    not_found: str
    not_found_free: str
    #: A turn kept in the session is clipped to this many characters, and this
    #: many turns are kept.
    turn_chars: int = TURN_CHARS
    max_turns: int = MAX_TURNS
    #: A factual question the corpus cannot answer goes to the model, and the
    #: reply is marked unverified. (``[computer] free_answers`` does the same
    #: for every profile.)
    free_answers: bool = False
    #: False: any figure outside the evidence fails a worded fact, and the
    #: computer's own sentence is said. True: the reply is kept and marked, as
    #: long as the computer's own figure is still in it.
    lenient: bool = False
    #: Appended to a kept reply that has figures the evidence does not.
    caveat: str = ""
    #: The context window and the wait this profile's longest turn needs.
    num_ctx: int = 4096
    timeout: float = 60.0

    # -- what the model client is asked for -----------------------------------
    def shape(self, mode: str, length_factor: float = 1.0,
              temperature_shift: float = 0.0) -> Tuple[float, float]:
        """This profile's length and temperature, as the two things every model
        client already accepts: a factor on its length and a shift on its
        temperature. So a profile needs nothing new of a provider, and
        GODCORE's own factor and shift (low energy → shorter, cooler) still
        apply on top of it."""
        base = LIMITS.get(mode, LIMITS[CASUAL])
        want = self.limits.get(mode, base)
        if want == base:
            return length_factor, temperature_shift
        return length_factor * want[0] / base[0], temperature_shift + (want[1] - base[1])

    # -- fitting a context window ---------------------------------------------
    def needs(self, mode: str) -> int:
        """Tokens the fullest turn in *mode* could use: its prompt and its reply."""
        return int(self.budget[mode] / CHARS_PER_TOKEN) + self.limits[mode][0] + HEADROOM

    def fitted(self, num_ctx: int) -> "Profile":
        """This profile for a model whose context is *num_ctx* tokens.

        Ollama cuts a prompt that does not fit, silently and from the front —
        the rules go first. So when the window is smaller than a mode could
        fill, that mode's prompt budget and reply length are scaled down
        together until they fit. Unchanged when everything already fits.
        """
        num_ctx = int(num_ctx or 0)
        if num_ctx <= 0 or all(self.needs(mode) <= num_ctx for mode in self.budget):
            return self
        budget, limits = dict(self.budget), dict(self.limits)
        for mode in self.budget:
            need = self.needs(mode)
            if need <= num_ctx:
                continue
            scale = max(0.1, (num_ctx - HEADROOM) / float(need - HEADROOM))
            budget[mode] = max(ctx.BUDGET.get(mode, 1600), int(self.budget[mode] * scale))
            limits[mode] = (max(LIMITS.get(mode, LIMITS[CASUAL])[0], int(self.limits[mode][0] * scale)),
                            self.limits[mode][1])
        return replace(self, budget=budget, limits=limits)


GROUNDED = Profile(
    name="grounded",
    limits=LIMITS, budget=ctx.BUDGET, history=ctx.HISTORY,
    rules=ctx.RULES, mode_lines=ctx.MODE_LINES, kind_lines=ctx.KIND_LINES,
    not_found_explore=ctx.NOT_FOUND_EXPLORE, not_found=ctx.NOT_FOUND, not_found_free=ctx.NOT_FOUND,
)

OPEN_RULES = (
    "You are {name}, a computer with a persistent identity, talking with {user}. "
    "Be warm, curious and thoughtful, and talk the way a well-read friend would: in full, natural "
    "sentences and paragraphs, with lists only when they help. "
    "Read each message in the light of the conversation so far: a follow-up such as \"why?\" or "
    "\"what about the other one?\" is about what was just said. "
    "Draw freely on your own general knowledge, reason things through, offer opinions and ideas of "
    "your own, and invent when you are asked to. "
    "KNOWN FACTS, when given, come from a checked corpus: prefer them to your own recollection and "
    "keep their figures exactly. If you are unsure of something, say so rather than making a fact up. "
    "In conversation you talk; you do not run commands or change settings, so never say you have done "
    "something on the machine. When something needs doing, say it can be asked for as a command. "
    "Never claim to remember anything not listed under MEMORY or said in this conversation."
)

OPEN = Profile(
    name="open",
    limits={CASUAL: (640, 0.9), REFLECTIVE: (1024, 0.85), DEEP: (1536, 0.8)},
    budget={CASUAL: 6000, REFLECTIVE: 9000, DEEP: 12000},
    history={CASUAL: 8, REFLECTIVE: 12, DEEP: 16},
    rules=OPEN_RULES,
    mode_lines={
        CASUAL: ("Answer as fully as the message deserves: a line or two for small talk, a few "
                 "paragraphs when there is something to explain or explore."),
        REFLECTIVE: ("Consider it from several angles, bring in examples or comparisons of your own, "
                     "and say where you land."),
        DEEP: ("Work through it thoroughly, step by step, at whatever length it needs. Where KNOWN "
               "FACTS are given, make clear which parts rest on them and which are your own reasoning."),
    },
    kind_lines={
        "imagination": ("This is imagination: invent freely and in detail. Nothing invented is a fact "
                        "about the real world, and you need not keep saying so."),
        "feeling": ctx.KIND_LINES["feeling"],
        "greeting": ctx.KIND_LINES["greeting"],
        "knowledge": ("Lead with what KNOWN FACTS say and keep their figures exactly; then add "
                      "explanation and context from your own knowledge."),
        "explore": ("Think it through with them, using KNOWN FACTS where they help and your own "
                    "knowledge and reasoning beyond them."),
        "calculation": ctx.KIND_LINES["calculation"],
    },
    not_found_explore="The CDOS corpus has no entry on this: reason it through from your own knowledge.",
    not_found=ctx.NOT_FOUND,
    not_found_free=("The CDOS corpus has no entry on this. Answer from your own general knowledge as "
                    "well as you can; the reply will be marked unverified, so say where you are unsure."),
    turn_chars=2400, max_turns=40,
    free_answers=True,
    lenient=True,
    caveat=" (Some figures there aren't from my corpus — treat those as unverified.)",
    num_ctx=8192, timeout=240.0,
)

PROFILES: Dict[str, Profile] = {GROUNDED.name: GROUNDED, OPEN.name: OPEN}
DEFAULT = GROUNDED.name


def known(name: object) -> bool:
    return str(name or "").strip().lower() in PROFILES


def resolve(profile: Union[str, Profile, None] = None) -> Profile:
    """A profile from its name. Anything unrecognised is ``grounded``: a
    misspelt setting must not be what loosens the machine (the doctor names it)."""
    if isinstance(profile, Profile):
        return profile
    return PROFILES.get(str(profile or DEFAULT).strip().lower(), GROUNDED)


def from_block(block: Optional[dict]) -> Tuple[Profile, int, float]:
    """``[companion]`` → (the profile fitted to its context, num_ctx, timeout).

    ``num_ctx`` and ``timeout`` of 0 (the default) mean "what the profile
    needs". A value written in the configuration is used as written, and the
    profile is fitted to it rather than the other way round.
    """
    block = block or {}
    profile = resolve(block.get("profile"))
    num_ctx = int(block.get("num_ctx") or 0) or profile.num_ctx
    timeout = float(block.get("timeout") or 0) or profile.timeout
    return profile.fitted(num_ctx), num_ctx, timeout
