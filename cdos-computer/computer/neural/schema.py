"""The neural core's input feature schema and output vocabulary, versioned.

A checkpoint is only meaningful together with the code that turns a question into its input vector and the list of
actions its outputs stand for.  This file names both, and `fingerprint()` hashes that description.  Every manifest
records the fingerprint, and the loader refuses a model whose fingerprint isn't this one.

Version 1 is the existing controller's features (computer/controller.py `features()` and `hashed()`), unchanged, so
models trained by tools/train_controller.py and by this package see the same inputs.

    input    a question -> Reader.read() (names are only what the corpus has) -> features() -> a set of hashed indices
    hash     fnv1a32(utf-8("f:" + feature)) mod DIM; a set, so a feature counts once however often it occurs
    output   one score per action in ACTION_NAMES (sorted), the plan shapes in computer/controller.py ACTIONS

Feature families (FAMILIES below), from features():

    bias                      always on
    ne:<0-3>, nf:<0-3>        how many names the question has / content words that aren't names (capped at 3)
    len:<0-5>                 tokens // 3, capped at 5
    w:<token>                 each token, with names masked as <e> (a word that names a relation, like "capital", is kept)
    b:<a>_<b>                 token bigrams, with ^ and $ at the ends
    first:, last:, first2:    the first token, the last, and the first two
    possessive                the question has 's
    comparative, less         a comparative ("heavier", "bigger") / a "less" word ("older", "smaller")
    ing                       a token ending in -ing
    role:subject|object       which way round a verb relation is asked (controller.roles)
    e:dict|e:nodict           the first name has a DEFINITION fact (it's a dictionary word)
    e:isa|e:noisa             the first name has an IS_A fact

The last two families read the corpus (through the navigator's applicable() policy, in the year the controller module
was imported).  They are properties of the thing asked about, not of the question's wording: the network can learn
that dictionary words get dictionary questions, but it never sees a fact's value.

If features(), hashed(), DIM or the action list change, the golden test in tests/test_neural.py fails.  Then bump
SCHEMA_VERSION, update FAMILIES, regenerate the golden file, and retrain: the old models are incompatible.
"""

from __future__ import annotations

import hashlib
import json

from computer.controller import ACTION_NAMES, ACTIONS, DIM, features, hashed

SCHEMA_NAME = "cdos-neural-features"
SCHEMA_VERSION = 1
HASH = "fnv1a32(utf8('f:' + feature)) mod dim; set of indices (presence, not counts)"
EXTRACTOR = "computer.controller.features + computer.controller.hashed"
FAMILIES = ["bias", "ne:<n>", "nf:<n>", "len:<n>", "w:<token>", "b:<a>_<b>", "first:<token>", "last:<token>",
            "first2:<a>_<b>", "possessive", "comparative", "less", "ing", "role:subject|object", "e:dict|e:nodict",
            "e:isa|e:noisa"]


def describe(actions=None, dim: int | None = None) -> dict:
    """The schema as data: what a manifest stores and the fingerprint covers."""
    acts = list(ACTION_NAMES if actions is None else actions)
    return {"name": SCHEMA_NAME, "version": SCHEMA_VERSION, "dim": DIM if dim is None else dim, "hash": HASH,
            "extractor": EXTRACTOR, "families": list(FAMILIES), "actions": acts,
            # the plan template behind each action: a change to what an action *does* is a change of schema too
            "templates": {a: ACTIONS[a] for a in acts if a in ACTIONS}}


def fingerprint(desc: dict | None = None) -> str:
    body = json.dumps(describe() if desc is None else desc, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def encode(reading, reader) -> tuple[list[str], list[int]]:
    """A Reading -> (feature strings, hashed indices).  The strings go in training records, so a later schema can
    re-hash old records; the indices are what the network sees."""
    f = features(reading, reader)
    return f, hashed(f, DIM)
