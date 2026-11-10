"""The neural core at run time: load the active model, or say why not; plan with it, or stay silent.

    load_active(registry)  -> (network, manifest, model dir)       or NeuralUnavailable(reason), never anything else
    NeuralPlanner          navigate()'s planner interface: plans(question), accept(question, plan, result)

The planner is the existing controller (computer/controller.py ControllerPlanner) with a validated model.  It runs
after the rules, it only ever proposes plans built from the fixed action templates, and each one is parsed again by
the plan validator with the corpus's relation names before navigate() may run it.  accept() is the controller's
deterministic check, unchanged.  If the model ever fails at answer time, the planner gives no plans for the rest of
the session and the computer answers as it does without a model.

This module is the answer path's part of the neural core, so it is declared deterministic (planner.neural in
computer/capabilities.py): it reads files and computes, and doesn't read the clock, random numbers or the network.
Writing (the experience log, the registry) lives in other modules.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from computer.controller import ACTIONS, ControllerPlanner
from computer.neural.model import MAX_MANIFEST_BYTES, MODEL_ID, ModelError, load_model_dir
from computer.plan import PlanError, parse

ACTIVE_FORMAT = "cdos-neural-active/1"


class NeuralUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def read_active(root) -> str | None:
    """The active model's id from <registry>/ACTIVE.json, None if none is active.  ModelError if the file is bad."""
    p = Path(root) / "ACTIVE.json"
    if not p.exists():
        return None
    try:
        if p.stat().st_size > MAX_MANIFEST_BYTES:
            raise ModelError("corrupt", "ACTIVE.json is too large")
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ModelError("corrupt", f"ACTIVE.json unreadable ({e})") from None
    if not isinstance(d, dict) or d.get("format") != ACTIVE_FORMAT:
        raise ModelError("corrupt", "ACTIVE.json isn't an activation record")
    mid = d.get("model_id")
    if mid is None:
        return None                            # deactivated
    if not isinstance(mid, str) or not MODEL_ID.match(mid):
        raise ModelError("corrupt", "ACTIVE.json names an invalid model id")
    return mid


def load_active(root, corpus_base=None) -> tuple[object, dict, Path]:
    """The active model, every check passed, or NeuralUnavailable with the reason.  Only promoted models
    (<registry>/models/<id>) are ever loaded; staging is never read here."""
    try:
        root = Path(root)
        if corpus_base is not None and (inside(root, Path(corpus_base)) or inside(Path(corpus_base), root)):
            raise NeuralUnavailable("the neural registry must not be inside the corpus folder, or contain it")
        if not root.is_dir():
            raise NeuralUnavailable(f"no neural registry at {root}")
        mid = read_active(root)
        if mid is None:
            raise NeuralUnavailable("no active model (train, evaluate, then promote one: python3 -m computer.neural)")
        model_dir = root / "models" / mid
        if not inside(model_dir, root / "models"):
            raise NeuralUnavailable("the active model's directory is outside the registry")
        net, manifest = load_model_dir(model_dir)
        if manifest["model_id"] != mid:
            raise NeuralUnavailable(f"ACTIVE.json names {mid}, but that directory's manifest says {manifest['model_id']}")
        return net, manifest, model_dir
    except NeuralUnavailable:
        raise
    except ModelError as e:
        raise NeuralUnavailable(str(e)) from None
    except Exception as e:  # noqa: BLE001 - whatever goes wrong, the computer starts without the model
        raise NeuralUnavailable(f"{type(e).__name__}: {e}") from None


class NeuralPlanner(ControllerPlanner):
    """The controller, driven by a model from the neural registry.  Its plans say "neural:<action> <p>"."""
    label = "neural"

    def __init__(self, corpus, model, manifest: dict | None = None, min_p: float = 0.08, max_actions: int = 3,
                 max_plans: int = 4, strict: bool = True):
        super().__init__(corpus, model, min_p=min_p, max_actions=max_actions, max_plans=max_plans)
        self.manifest = manifest or {}
        self.model_id = self.manifest.get("model_id", "")
        self.failed = ""                 # set when the model fails at answer time: no more plans this session
        self.last: dict | None = None    # the last prediction, for the experience log
        self.strict = strict             # the neural path's two extra answer checks (unaddressed, lex_family_unasked)

    def plans(self, question: str):
        self.last, self.last_error, self.last_scores = None, "", {}
        if self.failed:
            self.last_error = self.failed
            return []
        try:
            proposed = super().plans(question)
            scores = dict(self.last_scores)
        except Exception as e:  # noqa: BLE001 - a model that fails is switched off, never allowed to stop a turn
            self.failed = f"inference failed ({type(e).__name__}: {e}); the neural planner is off until restart"
            self.last_error = self.failed
            return []
        rels = set((self.corpus.meta or {}).get("relation_names", [])) or None
        out = []
        for p in proposed:
            try:
                checked = parse(str(p), rels)       # the plan validator decides, with the corpus's relation names
            except PlanError:
                continue
            if not from_template(checked, str(p.source)):
                continue                            # only the fixed action templates, whatever produced the plan
            checked.kind, checked.source = p.kind, p.source
            out.append(checked)
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:5]
        self.last = {"question": question, "top": [[a, round(v, 4)] for a, v in top],
                     "plans": [{"plan": str(p), "source": p.source} for p in out], "accept": {}}
        if not out and not self.last_error:
            self.last_error = "no plan passed the validator"
        return out

    def accept(self, question: str, plan, res) -> str:
        """The controller's accept(), then two checks only the neural path has (the legacy controller is unchanged):
        every content word of the question is addressed by the relations the answer used, and a dictionary relation
        (an opposite, another word, kinds of, related words, a sentence) is said only when it was asked for.  Without
        them a poor model gets "Spain is a country." past accept() for "the population of the capital of spain"."""
        why = super().accept(question, plan, res)
        if not why and self.strict:
            r = self.reader.read(question)
            miss = unaddressed(r, plan, res, self.corpus)
            if miss:
                why = f"the answer's facts don't address {', '.join(miss)}"
            else:
                fam = lex_family_unasked(r, res)
                if fam:
                    why = f"a dictionary answer ({fam}) the question didn't ask for"
        if self.last is not None:
            self.last["accept"][str(plan)] = why
        return why


# ---------------------------------------------------------------------- the neural path's extra answer checks

# A dictionary relation in the answer needs one of these in the question (or, for synonyms, a question about meaning).
ASKS = {"antonym": {"opposite", "opposites", "antonym", "antonyms", "contrary", "reverse"},
        "synonym": {"synonym", "synonyms", "similar", "another", "other", "alternative", "same", "like", "ways", "way"},
        "hyponym": {"kind", "kinds", "type", "types", "sort", "sorts", "variety", "varieties", "example", "examples",
                    "different", "list", "some"},
        "derivation": {"related", "relate", "derived", "derive", "come", "comes", "from"},
        "example": {"sentence", "sentences", "example", "examples", "use", "used", "using"}}
MEANING = {"mean", "means", "meaning", "meanings", "define", "definition", "definitions", "explain", "word"}
FAMILY = {"ANTONYM": "antonym", "WN_ANTONYM": "antonym", "SYNONYM": "synonym", "WN_SIMILAR_TO": "synonym",
          "HYPONYM": "hyponym", "DERIVATION": "derivation", "EXAMPLE": "example"}


def unaddressed(r, plan, res, corpus) -> list[str]:
    """The question's content words (free words and words read as relation names) that nothing in the answer
    addresses: not a word of a relation the answer used (its own words, its aliases, the lexicon's), not a word the
    plan tested or found, not part of a fact's value.  The same word-to-relation matching GET uses (hint_words)."""
    from computer.controller import ALL_UNIT_WORDS, singular
    from computer.corpus import key_of
    from computer.lexicon import words_for
    from computer.navigator import REL_ALIASES, hint_words, word_forms
    from computer.planner import COMPARATIVE
    rel_w = set()
    for rel in {f.relation for f in res.evidence}:
        rel_w |= words_for(corpus, rel) | REL_ALIASES.get(rel, set())
    plan_w, val_w = set(), set()
    for st in plan.steps:
        if st.op in ("FIND", "TEST", "FILTER", "RANK", "EXCEPT", "COMPARE") and st.text:
            plan_w |= set(re.findall(r"[a-z0-9]+", st.text))
    for f in res.evidence:
        val_w |= set(key_of(f.subject).lower().split("_")) | set(key_of(str(f.value)).lower().split("_"))
    words = [r.toks[i] for i in r.free] + [s.phrase for s in r.spans if s.relish]
    out = []
    for w in words:
        if w in ALL_UNIT_WORDS:
            continue                       # units are accounted() for
        forms = {w, singular(w), *word_forms(w)[:4]}
        base = COMPARATIVE.get(w) or (w[:-2] if w.endswith("er") and len(w) > 4 else "")
        if base:
            forms.add(base)
        hints = set(hint_words(sorted(forms)))
        if hints & rel_w or forms & plan_w or forms & val_w:
            continue
        out.append(w)
    return out


def lex_family_unasked(r, res) -> str:
    """The dictionary relation family of the answer, if the question never asked for that kind of thing."""
    toks = set(r.toks)
    fams = {FAMILY[f.relation] for f in res.evidence if f.relation in FAMILY}
    for fam in sorted(fams):
        if toks & ASKS[fam] or (fam == "synonym" and toks & MEANING):
            continue
        return fam
    return ""


def template_ops(action: str) -> list[str]:
    return [(line.split("=", 1)[1] if "=" in line else line).split()[0] for line in ACTIONS[action]]


def from_template(plan, source: str) -> bool:
    """Is this plan an instance of the action its source names ("neural:compare_more 0.97")?  Same operations in the
    same order as that action's template in computer/controller.py ACTIONS."""
    m = re.match(r"^[a-z]+:([a-z_0-9]+)\s", source or "")
    if not m or m.group(1) not in ACTIONS:
        return False
    return [s.op for s in plan.steps] == template_ops(m.group(1))
