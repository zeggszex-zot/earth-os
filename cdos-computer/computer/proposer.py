"""The model proposes, the Navigator disposes.

A small model writes a plan *and* the sentence it would say:

    $1 = FIND france
    $2 = GET $1 CAPITAL
    $3 = GET $2 POPULATION
    ANSWER $3
    SAY: Paris is France's capital, and about 2,100,000 people live there.

The plan is validated and run by the executor (the only thing that reads the corpus).  The
sentence is then held to what the executor found (`check`).  It is used only if:

  * the plan actually reached an answer (nothing to say "I don't know" in the model's words),
  * it says the answer (the value, the winner, the count, the yes),
  * every number in it is in the evidence or the question,
  * every Name In Capitals in it is in the evidence or the question,
  * it doesn't negate, hedge into a prediction, or ramble.

Otherwise the executor's own template sentence is used, exactly as before.  So the model makes
the computer's output more fluent but still cannot add a fact.

A model distilled to answer from its weights can skip the plan and write only `SAY: ...`.
That sentence is checked against the rule planner's run of the same question; if the rules
can't ground it, it is discarded.  ("Answer from memory, verify from the corpus.")
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .navigator import Navigator, Result, num_text
from .plan import GRAMMAR, Plan, PlanError, parse
from .planner import normalize, navigate

SAY = re.compile(r"^\s*SAY:\s*(.*)$", re.M)
NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?:e[+-]?\d+)?", re.I)
NEGATION = re.compile(r"\b(not|isn't|aren't|wasn't|weren't|doesn't|don't|didn't|never|no longer|neither|nor)\b", re.I)
PREDICTS = re.compile(r"\b(will definitely|definitely will|guarantee\w*|for sure|certainly will|100 ?%)\b", re.I)
HEDGE_START = {"yes", "no", "the", "a", "an", "it", "its", "it's", "they", "there", "that", "this", "these", "those",
               "so", "and", "but", "hoo", "well", "oh", "i", "my", "we", "you", "your", "both", "of", "in", "on",
               "about", "around", "only", "just", "every", "each", "all", "some", "most", "according", "from"}
MAX_WORDS = 60

PROPOSAL_GRAMMAR = GRAMMAR.replace('root    ::= (step "\\n")+ "ANSWER " var (" " var)? "\\n"?',
                                   'root    ::= ((step "\\n")+ "ANSWER " var (" " var)? "\\n")? "SAY: " say\n'
                                   'say     ::= [^\\n]+')

SYSTEM = ("You are a reasoning computer that answers from a knowledge graph. First write a plan using only the steps "
          "FIND, GET, WHO, FILTER, COMPARE, COUNT, TEST, RANK, EXCEPT, CHECK, ANSWER. Then write one line starting with SAY: "
          "with a short, precise answer that only uses what the plan will find.")
SYSTEM_DIRECT = "You are a reasoning computer. Answer in one short, precise line starting with SAY:."

FEWSHOT = """Q: what is the population of the capital of germany
$1 = FIND germany
$2 = GET $1 ~capital
$3 = GET $2 ~population
ANSWER $3
SAY: Germany's capital is Berlin, and 3,800,000 people live there.
Q: which is heavier, mars or venus
$1 = FIND mars
$2 = FIND venus
$3 = GET $1 ~heavy
$4 = GET $2 ~heavy
$5 = COMPARE $3 $4 MORE heavier
ANSWER $5
SAY: Venus is heavier than Mars.
Q: is a dolphin a mammal
$1 = FIND dolphin
$2 = GET $1 IS_A
$3 = TEST $2 mammal
ANSWER $3
SAY: Yes, a dolphin is a mammal.
Q: what is the opposite of happy
$1 = FIND happy
$2 = GET $1 ~antonym
ANSWER $2
SAY: The opposite of happy is unhappy."""


@dataclass
class Proposal:
    raw: str
    say: str
    plan: Plan | None = None
    plan_error: str = ""


def parse_proposal(raw: str, known_relations: set | None = None) -> Proposal | None:
    m = SAY.search(raw or "")
    if not m:
        return None
    say = " ".join(m.group(1).split()).strip().strip('"')
    head = raw[: m.start()].strip()
    head = "\n".join(l for l in head.splitlines() if l.strip() and not l.startswith("Q:"))
    prop = Proposal(raw, say)
    if head:
        try:
            prop.plan = parse(head, known_relations)
        except PlanError as e:
            prop.plan_error = str(e)
    return prop


# ---------------------------------------------------------------------- the check

def _nums(text: str) -> set[str]:
    return {num_text(n) for n in NUMBER.findall(text or "")}


def _names(text: str) -> list[str]:
    """Capitalised word runs, skipping a sentence's first word when it is an ordinary word."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", text or ""):
        words = re.findall(r"[A-Za-z][\w'\-]*|[^\sA-Za-z]", sent)      # punctuation is a token: it ends a name
        cap = lambda w: w[0].isupper()
        i = 0
        while i < len(words):
            if cap(words[i]) and not (i == 0 and words[i].lower() in HEDGE_START):
                j = i
                while j + 1 < len(words) and (cap(words[j + 1]) or words[j + 1].lower() in ("of", "de", "the")
                                              and j + 2 < len(words) and cap(words[j + 2])):
                    j += 1
                out.append(" ".join(words[i:j + 1]).removesuffix("'s"))
                i = j + 1
            else:
                i += 1
    return out


def check(say: str, res: Result | None, question: str = "") -> str:
    """Why `say` can't be used as the answer, or '' if it can."""
    if not say:
        return "empty"
    if res is None or not res.answered:
        return "the corpus doesn't support an answer"
    if len(say.split()) > MAX_WORDS:
        return "too long"
    if re.search(r"[*#`\[\]<>{}]", say):
        return "markup"
    if PREDICTS.search(say):
        return "predicts"
    if NEGATION.search(say):
        return "negates"           # grounded answers here are positive statements; a "not" could flip one
    ground = " ".join([res.text, question] + [f"{f.subject} {f.value} {f.unit}" for f in res.evidence])
    extra = _nums(say) - _nums(ground)
    if extra:
        return f"numbers not in the evidence: {sorted(extra)}"
    g = ground.lower().replace("_", " ")
    for name in _names(say):
        n = name.lower()
        if n not in g and n.rstrip("s") not in g:
            return f"names {name!r}, which isn't in the evidence"
    fin, low = res.final or {}, say.lower()
    kind = fin.get("kind")
    if kind == "test":
        if fin.get("truth", "TRUE") != "TRUE":
            return "a no, an unknown or a conflict is output in the computer's template words"
        if not re.match(r"^\W*(yes|yep|yup|that's right|indeed)\b", low):
            return "doesn't say yes"
    elif kind == "compare":
        w, lose, label = fin.get("winner"), fin.get("loser"), fin.get("label") or ""
        if w is None:
            return "" if re.search(r"\b(same|equal|equally)\b", low) else "doesn't say they're equal"
        words = label.split()[-1] if label else r"(?:more|greater|bigger|larger|heavier|taller|older|longer|higher)"
        if not re.search(rf"\b{re.escape(w.lower())}\b[^.]{{0,60}}\b{words}\b", low):
            return f"doesn't say {w} is {label or 'more'}"
        if lose and re.search(rf"\b{re.escape(lose.lower())}(?:'s)?\s+(?:is|are|was|has|looks|seems)\s+"
                              rf"(?:the\s+|a\s+|an\s+)?(?:\w+\s+){{0,2}}{words}\b", low):
            return f"says {lose} is {label or 'more'}"
    elif kind in ("values", "count", "calc"):
        vals = fin.get("values") or []
        hits = [v for v in vals if (v.lower() in low) or (re.fullmatch(r"[\d.e+-]+", v) and v in _nums(say))]
        need = min(len(vals), 3)
        if len(hits) < need:
            return f"doesn't give the answer ({', '.join(vals[:3])})"
    return ""


# ---------------------------------------------------------------------- the model

class Proposer:
    """The model behind `navigator_planner = "propose"`.  `fewshot=False` for a model fine-tuned on
    tools/make_navigator_data.py --format propose (or --format direct for a distilled one)."""

    def __init__(self, llm, corpus=None, fewshot: bool = True, grammar: bool = True, direct: bool = False):
        self.llm, self.corpus = llm, corpus
        self.fewshot, self.grammar, self.direct = fewshot, grammar, direct
        self.last_raw = self.last_error = ""

    @property
    def label(self) -> str:
        return getattr(self.llm, "label", "lm")

    def prompt(self, question: str) -> list[dict]:
        if self.direct:
            return [{"role": "system", "content": SYSTEM_DIRECT}, {"role": "user", "content": f"Q: {normalize(question)}\n"}]
        user = (FEWSHOT + "\n" if self.fewshot else "") + f"Q: {normalize(question)}\n"
        return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]

    def propose(self, question: str) -> Proposal | None:
        self.last_raw = self.last_error = ""
        try:
            extra = {"grammar": PROPOSAL_GRAMMAR} if self.grammar else None
            raw = self.llm.chat(self.prompt(question), temperature=0.0, max_tokens=200, extra=extra)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"model failed: {e}"
            return None
        self.last_raw = raw
        rels = set((self.corpus.meta or {}).get("relation_names", [])) if self.corpus else set()
        prop = parse_proposal(raw, rels or None)
        if prop is None:
            self.last_error = "no SAY line"
        return prop


def propose_and_verify(question: str, proposer: Proposer, navigator: Navigator, fallback: list,
                       year: int = 0, trace: list | None = None):
    """-> (plan, result, words): `words` is the model's sentence if it passed `check`, else None.
    The plan and result are the model's if its plan reached an answer, else the fallback planners'."""
    trace = trace if trace is not None else []
    prop = proposer.propose(question)
    if prop is None:
        trace.append(f"proposer {proposer.label}: nothing usable ({proposer.last_error})")
        p, r = navigate(question, fallback, navigator, year=year, trace=trace)
        return p, r, None
    trace.append(f"proposer {proposer.label}: SAY {prop.say!r}")
    plan, res = None, None
    if prop.plan:
        prop.plan.source, prop.plan.kind = proposer.label, "lm"
        res = navigator.run(prop.plan, year=year)
        plan = prop.plan
        trace.append("  model's plan: " + " | ".join(str(s) for s in prop.plan.steps))
        trace += [f"    {s}" for s in res.steps]
        trace.append(f"    => {res.status}")
    elif prop.plan_error:
        trace.append(f"  model's plan is invalid ({prop.plan_error})")
    if res is None or not res.answered:
        if prop.plan:
            trace.append("  the model's plan didn't reach an answer: its words are dropped, trying the rules")
        plan, res = navigate(question, fallback, navigator, year=year, trace=trace)
        if prop.plan or prop.plan_error:
            return plan, res, None          # never voice an answer the model's own plan couldn't ground
    why = check(prop.say, res, question)
    trace.append("  model's words " + ("accepted" if not why else f"rejected: {why}"))
    return plan, res, (None if why else prop.say)
