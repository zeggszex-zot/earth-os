"""Analysis: estimates and calculations from what a person says and what the corpus knows.

    "How long can a 50 Wh battery run a 5 W device?"
       formula  runtime = battery energy x conversion efficiency / load power
       battery     50 watt-hours          stated
       load        5 watts                stated
       efficiency  85%                    assumed (none given)
       compute     50 Wh x 0.85 / 5 W = 8.5 h
    -> "The estimated runtime is 8.5 hours: 50 watt-hours at 5 watts, assuming 85% conversion efficiency."

    "And at half the load?"       -> the same frame, load x 0.5, said as a what-if ("With the load at 2.5 watts, ...")
    "Calculate the runtime."      -> "Specify the battery's usable energy capacity."   (one missing input at a time)
    "How did you work that out?"  -> every input with where it came from, the formula, the arithmetic

The house rules carry over (computer/core.py):

 1. Every number in an answer is a stated input, a corpus fact (cited by id), a named assumption, or the
    result of arithmetic on those.  Nothing is recalled from a model.
 2. An assumption is always said.  A result that rests on one is an ESTIMATE, not a fact.
 3. Missing isn't zero: a missing input is asked for, never filled in silently.
 4. Two corpus figures for the same thing (idle and load power) are both said, and the person picks.
    The engine doesn't choose between conflicting facts.
 5. A what-if is said as one ("With the load at 2.5 watts, ...") and changes only the variable it names.

Formulas are typed: each input has a dimension (computer/quantity.py), so a power can't land where an energy belongs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .calc import calculate
from .clock import current_year
from .corpus import NOT_KNOWLEDGE
from .phrase import listing, name_of, unit_of
from .quantity import ELECTRICAL_DIMS, Quantity, fmt_energy, fmt_hours, fmt_num, fmt_percent, fmt_plain, fmt_power, from_fact, parse, \
    say, spell_out

# Truth status of a result (README "Evidence"): how much the answer can be relied on.
RETRIEVED, DERIVED, ESTIMATE, UNKNOWN = "RETRIEVED", "DERIVED", "ESTIMATE", "UNKNOWN"

DEFAULT_EFFICIENCY = 0.85
MAX_ENTITY_WORDS = 6

ADDRESS = re.compile(r"^\s*(?:(?:hey|ok|okay)\s+)?(?:computer\s*[,:]?|(?:cdos|owl|ollie)\s*[,:])\s+", re.I)
LOSSLESS = re.compile(r"\b(?:no (?:conversion )?loss(?:es)?|without (?:any )?loss(?:es)?|lossless|ideal(?:ly)?|"
                      r"perfect(?:ly)? efficien\w*|100 ?% efficien\w*)\b", re.I)
EXPLAIN = re.compile(r"\b(?:how did you (?:work|get|figure|calculate|come up with|arrive)|show (?:me )?(?:your|the) "
                     r"(?:work|working|calculation|maths?|math)|what did you assume|what (?:are|were) (?:your|the) "
                     r"assumptions|where did (?:that|those|the) .{0,30}come from|explain (?:that|it|the (?:calculation|"
                     r"estimate|answer))|why\??$|tell me more$|how\??$)", re.I)
# Words that mark a turn as a change to the last calculation, not a new question.
CUE = re.compile(r"^(?:and|so|ok|okay|now|then)\b|\b(?:what about|how about|what if|what happens if|if|with|at|"
                 r"assume|assuming|suppose|instead|only|just)\b", re.I)
QUESTIONISH = re.compile(r"\?\s*$|^(?:what|which|who|whom|where|when|why|how|is|are|was|were|does|do|did|can|could|"
                         r"tell|define|calculate|compute|estimate)\b", re.I)
SCALE_WORDS = [(r"\b(?:half|halve[sd]?|halving)\b", 0.5), (r"\b(?:a )?quarter\b", 0.25), (r"\ba third\b", 1 / 3),
               (r"\b(?:double[sd]?|doubling|twice)\b", 2.0), (r"\b(?:triple[sd]?|tripling|three times)\b", 3.0),
               (r"\b(?:quadruple[sd]?|four times)\b", 4.0), (r"\b(\d+(?:\.\d+)?) times\b", None)]
TARGETS = [("battery", r"\b(?:battery|batteries|capacity|pack|cell|power bank)\b"),
           ("load", r"\b(?:load|draw|consumption|device|power)\b"),
           ("power", r"\b(?:load|draw|consumption|device|power)\b"),
           ("hours_per_day", r"\b(?:hours?|time|runs?|running|schedule)\b"),
           ("duration", r"\b(?:hours?|time|runs?|running|duration|long)\b")]
ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "last": -1}


@dataclass(frozen=True)
class Slot:
    name: str                   # battery | load | efficiency | power | hours_per_day | duration | voltage | current
    dim: str                    # its quantity dimension (computer/quantity.py)
    ask: str                    # the clarification when it's missing
    label: str                  # how the explanation names it
    composite: tuple = ()       # other dimensions it can be made from: energy = charge x voltage
    composite_ask: str = ""     # asked when one half of the composite is there
    default: float | None = None
    default_why: str = ""


@dataclass(frozen=True)
class Formula:
    id: str
    title: str                  # "runtime"
    expr: str                   # said in explanations
    slots: tuple
    triggers: tuple             # regexes: the question asks for this
    context: str = ""           # regex: and it's about this domain (stops "how long did Rome last")
    needs: tuple = ()           # dimensions that must be stated for the formula to apply


def _s(name, dim, ask, label, **kw) -> Slot:
    return Slot(name, dim, ask, label, **kw)


BATTERY = _s("battery", "energy", "Specify the battery's usable energy capacity.", "battery energy",
             composite=("charge", "voltage"), composite_ask="Specify the battery's voltage.")
LOAD = _s("load", "power", "Specify the load's power draw.", "load power", composite=("current", "voltage"),
          composite_ask="Specify the load's voltage.")
EFFICIENCY = _s("efficiency", "fraction", "Specify the conversion efficiency.", "conversion efficiency",
                default=DEFAULT_EFFICIENCY,
                default_why="no efficiency was given; 85% is a typical figure for a battery feeding a voltage converter")
POWER = _s("power", "power", "Specify the device's power draw.", "power draw", composite=("current", "voltage"),
           composite_ask="Specify the device's voltage.")
HOURS_PER_DAY = _s("hours_per_day", "time", "Specify the operating hours per day.", "operating time")
DURATION = _s("duration", "time", "Specify how long it runs.", "running time")
VOLTAGE = _s("voltage", "voltage", "Specify the voltage.", "voltage")
CURRENT = _s("current", "current", "Specify the current.", "current")

ELECTRICAL = r"\b(?:batter(?:y|ies)|power bank|cell|load|device|ups|charge|runtime|run ?time|battery life|watts?|" \
             r"volts?|amps?|wh|kwh|mah|ah|energy|power|consum\w*|computer|pi|motor|sensor|bulb|lamp|heater|fridge|" \
             r"laptop|phone|router|server)\b"

FORMULAS = [
    Formula("power", "power", "power = voltage x current", (VOLTAGE, CURRENT),
            (re.compile(r"\b(?:how much power|how many watts|wattage|(?:what|what's|calculate|compute)\b.{0,20}\bpower)\b",
                        re.I),),
            needs=("voltage", "current")),
    Formula("daily", "daily energy requirement", "daily energy = power x operating hours per day", (POWER, HOURS_PER_DAY),
            (re.compile(r"\b(?:daily|per day|a day|each day|every day)\b.*\b(?:energy|consum\w*|use|usage|uses|"
                        r"requirement|watt-?hours?|kwh|kilowatt-?hours?)\b", re.I),
             re.compile(r"\b(?:energy|consumption|usage|use|requirement)\b.*\b(?:daily|per day|a day|each day|every day)\b",
                        re.I))),
    Formula("energy", "energy used", "energy = power x time", (POWER, DURATION),
            (re.compile(r"\bhow much energy\b|\benergy (?:does|will|would|did|do)\b|\b(?:energy|watt-?hours) "
                        r"(?:used|consumed)\b", re.I),)),
    Formula("runtime", "runtime", "runtime = battery energy x conversion efficiency / load power",
            (BATTERY, LOAD, EFFICIENCY),
            (re.compile(r"\bhow (?:long|many (?:hours|minutes|days))\b.*\b(?:run|runs|power|last|lasts|keep|operate|"
                        r"supply|go)\b", re.I),
             re.compile(r"\b(?:run ?time|battery life)\b", re.I)),
            context=ELECTRICAL),
]
BY_ID = {f.id: f for f in FORMULAS}


@dataclass
class Finding:
    """What an analysis answer rests on: the claim, its status, every input with its provenance, the arithmetic,
    the assumptions, and what was missing or in conflict.  The sentence is rendered from this and nothing else."""
    claim: str
    status: str                                    # RETRIEVED | DERIVED | ESTIMATE | UNKNOWN
    formula: str = ""
    inputs: dict = field(default_factory=dict)     # slot -> Quantity.to_json()
    steps: list = field(default_factory=list)      # "50 Wh x 0.85 / 5 W = 8.5 h"
    assumptions: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)  # e.g. two power figures for one device
    facts: list = field(default_factory=list)      # corpus fact ids the answer rests on
    conditional: str = ""                          # the what-if it was computed under, if any

    def to_json(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Frame:
    """One calculation in progress or just answered: what a follow-up or a clarification changes."""
    formula: str
    slots: dict = field(default_factory=dict)      # slot -> Quantity
    partial: dict = field(default_factory=dict)    # slot -> the half of a composite we have (10 Ah, no voltage yet)
    pending: str = ""                              # the slot just asked about
    choice: dict | None = None                     # {"slot", "entity", "options": [Quantity]}: the person picks
    result: float | None = None
    finding: Finding | None = None
    t: float = 0.0

    def to_json(self) -> dict:
        return {"formula": self.formula, "slots": {k: q.to_json() for k, q in self.slots.items()},
                "partial": {k: q.to_json() for k, q in self.partial.items()}, "pending": self.pending,
                "choice": None if not self.choice else {**self.choice,
                                                        "options": [q.to_json() for q in self.choice["options"]]},
                "result": self.result, "t": self.t}

    @classmethod
    def from_json(cls, d: dict) -> "Frame":
        if d.get("formula") not in BY_ID:
            raise ValueError(f"unknown formula {d.get('formula')!r}")
        ch = d.get("choice")
        return cls(d["formula"], {k: Quantity.from_json(q) for k, q in (d.get("slots") or {}).items()},
                   {k: Quantity.from_json(q) for k, q in (d.get("partial") or {}).items()}, d.get("pending") or "",
                   None if not ch else {**ch, "options": [Quantity.from_json(q) for q in ch["options"]]},
                   d.get("result"), None, float(d.get("t") or 0))


@dataclass
class Answer:
    text: str
    kind: str                     # "answer" | "clarify" | "explain" | "refuse"
    finding: Finding | None = None
    trace: list = field(default_factory=list)
    result: float | None = None   # the computed value in its base unit (h, Wh, W), for the envelope


def clean(text: str) -> str:
    t = ADDRESS.sub("", " ".join((text or "").split()))
    return spell_out(t)


class Analyst:
    """The analysis faculty.  `handle(text)` returns an Answer, or None when the turn isn't an analysis."""

    def __init__(self, corpus=None, efficiency: float = DEFAULT_EFFICIENCY):
        self.corpus = corpus
        self.efficiency = efficiency
        self.frame: Frame | None = None
        self.year = 0                 # the year figures must hold in (0: this year, from computer/clock.py)
        self.live = False             # the last turn was answered here: follow-ups and clarifications may apply
        self.changed = False          # the frame changed this turn (worth saving)

    # ------------------------------------------------------------------ the turn

    def handle(self, text: str, route_intent: str = "", year: int = 0) -> Answer | None:
        self.changed = False
        self.year = year
        q = clean(text)
        trace: list = []
        if not q:
            return None
        f = self.detect(q)              # a question that names a formula is a new calculation, not a follow-up
        if self.live and self.frame is not None and f is None:
            if self.frame.choice or self.frame.pending:
                a = self._answer_clarification(q, trace)
                if a is not None:
                    return self._done(a)
            elif EXPLAIN.search(q) and self.frame.finding:
                return self._done(Answer(self.explain(), "explain", self.frame.finding, ["analysis: explain"],
                                         result=self.frame.result))
            elif route_intent != "calc" and CUE.search(q) and not REQUIREMENT.search(q):
                a = self._followup(q, trace)
                if a is not None:
                    return self._done(a)
        if f is None:
            return None
        trace.append(f"analysis: {f.id} ({f.expr})")
        self.frame = Frame(f.id)
        stated, ents = self._inputs(q, trace)
        return self._done(self._solve(stated, ents, trace, lossless=bool(LOSSLESS.search(q))))

    def idle(self) -> None:
        """Another faculty answered: a follow-up can no longer mean this calculation."""
        if self.live:
            self.live = False
            if self.frame and (self.frame.pending or self.frame.choice):
                self.frame.pending, self.frame.choice = "", None    # an abandoned question isn't still open
                self.changed = True

    def _done(self, a: Answer) -> Answer:
        self.live = True
        self.changed = True
        return a

    def detect(self, q: str) -> Formula | None:
        stated = {x.dim for x in parse(q)} & ELECTRICAL_DIMS      # "run 100 m" isn't an electrical question
        for f in FORMULAS:
            if not any(rx.search(q) for rx in f.triggers):
                continue
            if f.needs and not all(d in stated for d in f.needs):
                continue
            if f.context and not (re.search(f.context, q, re.I) or stated or self._entities(q)[0]):
                continue
            return f
        return None

    # ------------------------------------------------------------------ inputs

    def _entities(self, q: str) -> tuple[list, str]:
        """Corpus things named in the question that have electrical figures (longest name first), and the
        question with their names taken out, so 'Raspberry Pi Zero 2 W' isn't read as 2 watts."""
        if self.corpus is None or not getattr(self.corpus, "ready", False):
            return [], q
        words = re.findall(r"[a-z0-9][a-z0-9'\-]*", q.lower())
        found, used = [], [False] * len(words)
        for n in range(min(MAX_ENTITY_WORDS, len(words)), 0, -1):
            for i in range(len(words) - n + 1):
                if any(used[i:i + n]):
                    continue
                key = "_".join(words[i:i + n]).upper().replace("'S", "")
                if not self.corpus.has(key):
                    continue
                qs = self._figures(key)
                if qs:
                    found.append((i, key, qs))
                    used[i:i + n] = [True] * n
        rest = q
        for _, key, _ in found:
            rest = re.sub(r"\b" + r"[\s\-]+".join(map(re.escape, key.lower().split("_"))) + r"\b", " ", rest, flags=re.I)
        return [(k, qs) for _, k, qs in sorted(found)], rest

    def _figures(self, key: str) -> list[Quantity]:
        out = []
        for f in self.corpus.facts_for(key):
            if f.source in NOT_KNOWLEDGE or f.conf < 0.5 or (f.until is not None and f.until <= (self.year or current_year())):
                continue
            x = from_fact(f)
            if x is not None and x.dim in ("energy", "charge", "voltage", "current", "power"):
                out.append(x)
        return out

    def _inputs(self, q: str, trace: list):
        ents, rest = self._entities(q)
        stated = parse(rest)
        if stated:
            trace.append("stated: " + ", ".join(f"{x.text} ({x.dim})" for x in stated))
        for k, qs in ents:
            trace.append(f"corpus: {k}: " + ", ".join(f"{x.fact['relation']} {x.text} [{x.fact['id']}]" for x in qs))
        return stated, ents

    # ------------------------------------------------------------------ solving

    def _solve(self, stated: list, ents: list, trace: list, lossless: bool = False, keep: bool = False,
               conditional: str = "") -> Answer:
        fr = self.frame
        f = BY_ID[fr.formula]
        pool = {d: [x for x in stated if x.dim == d] for d in ("energy", "charge", "voltage", "current", "power",
                                                              "time", "fraction")}
        if lossless:
            pool["fraction"] = [Quantity("fraction", 1.0, "no conversion losses", "stated")]
        shared_v = None
        for slot in f.slots:
            n_same = sum(1 for s in f.slots if s.dim == slot.dim)
            if len(pool.get(slot.dim, [])) > n_same and slot.dim != "fraction" and fr.choice is None:
                # Two figures said for one input ("a 5 W and a 3 W device"): the others are filled, then this is asked.
                fr.choice = {"slot": slot.name, "entity": "", "options": list(pool[slot.dim]), "sum": True}
                pool[slot.dim] = []
                trace.append(f"analysis: {len(fr.choice['options'])} {slot.dim} figures for one {slot.name} -> ask")
                continue
            have = fr.slots.get(slot.name) if keep else None
            got = self._take(slot, pool, shared_v, fr)
            if got is None and have is not None:
                continue
            if got is None:
                got = self._from_entities(slot, ents, fr, trace)
                if got == "choice":
                    return self._ask_choice(trace)
            if got is None and slot.name in fr.partial and pool.get("voltage"):
                got = self._compose(slot, fr.partial.pop(slot.name), pool["voltage"].pop(0))
            if got is None and slot.default is not None:
                d = self.efficiency if slot.name == "efficiency" else slot.default
                got = Quantity(slot.dim, d, fmt_percent(d), "assumed", note=slot.default_why)
            if got is None:
                fr.slots.pop(slot.name, None)
                continue
            fr.slots[slot.name] = got
            fr.partial.pop(slot.name, None)
            for p in got.parts:
                if p.dim == "voltage":
                    shared_v = p
        if fr.choice:
            return self._ask_choice(trace)
        missing = [s for s in f.slots if s.name not in fr.slots]
        if missing:
            s = missing[0]
            fr.pending = s.name
            known = [x for x in fr.slots.values() if x.source == "corpus"]
            pre = " ".join(f"My corpus gives {name_of(x.fact['subject'])}'s {_rel(x.fact['relation'])} as {say(x)}."
                           if x.fact else f"My corpus gives {_desc(x)}." for x in known)
            ask = s.composite_ask if s.name in fr.partial else s.ask
            trace.append(f"analysis: missing {s.name} -> ask")
            fr.finding = Finding(ask, UNKNOWN, f.id, missing=[m.name for m in missing])
            return Answer((pre + " " + ask).strip(), "clarify", fr.finding, trace)
        fr.pending = ""
        return self._compute(trace, conditional)

    def _take(self, slot: Slot, pool: dict, shared_v, fr: Frame) -> Quantity | None:
        if slot.dim == "time":
            xs = pool["time"]
            if slot.name == "duration":
                xs = [x for x in xs if not x.per_day] or xs
            if xs:
                x = xs[0]
                pool["time"].remove(x)
                x.per_day = slot.name == "hours_per_day"
                return x
            return None
        if pool.get(slot.dim):
            return pool[slot.dim].pop(0)
        if slot.composite:
            a, b = slot.composite
            if pool.get(a):
                qa = pool[a].pop(0)
                if pool.get(b):
                    qb = min(pool[b], key=lambda x: abs(x.pos - qa.pos))     # "1 A at 5 V from a 12 V battery"
                    pool[b].remove(qb)
                    return self._compose(slot, qa, qb)
                if shared_v is not None and b == "voltage":
                    return self._compose(slot, qa, shared_v,
                                         note=f"the {slot.name} is taken to run at the battery's {say(shared_v)}")
                fr.partial[slot.name] = qa
        return None

    @staticmethod
    def _compose(slot: Slot, qa: Quantity, qb: Quantity, note: str = "") -> Quantity:
        return Quantity(slot.dim, qa.value * qb.value, f"{qa.text} x {qb.text}", "derived", note=note, parts=[qa, qb])

    def _from_entities(self, slot: Slot, ents: list, fr: Frame, trace: list):
        """The slot from a named corpus thing.  One figure is used (and cited); two or more are put to the person."""
        for key, qs in ents:
            direct = [x for x in qs if x.dim == slot.dim]
            if not direct and slot.composite:
                a, b = slot.composite
                xa = [x for x in qs if x.dim == a]
                xb = [x for x in qs if x.dim == b]
                if len(xa) == 1 and len(xb) == 1:
                    q = self._compose(slot, xa[0], xb[0])
                    q.source = "corpus"
                    return q
                direct = []
            values = {round(x.value, 9) for x in direct}
            if len(values) == 1:
                return direct[0]
            if len(values) > 1:
                fr.choice = {"slot": slot.name, "entity": key, "options": direct}
                trace.append(f"analysis: {key} has {len(values)} {slot.dim} figures -> ask, don't pick")
                return "choice"
        return None

    def _ask_choice(self, trace: list) -> Answer:
        ch = self.frame.choice
        n = len(ch["options"])
        if not ch["entity"]:              # two figures in the question itself
            text = (f"I heard {'two' if n == 2 else n} {ch['options'][0].dim} figures: "
                    f"{listing([say(x) for x in ch['options']])}. Specify which one, or say both if they add up.")
        else:
            opts = [f"{say(x)} ({_qual(x)})" for x in ch["options"]]
            text = (f"My corpus lists {'two' if n == 2 else n} {ch['options'][0].dim} figures for "
                    f"{name_of(ch['entity'])}: {listing(opts)}. Specify which one.")
        self.frame.finding = Finding(text, UNKNOWN, self.frame.formula, missing=[ch["slot"]],
                                     conflicts=[x.fact["id"] for x in ch["options"] if x.fact])
        return Answer(text, "clarify", self.frame.finding, trace)

    def _answer_clarification(self, q: str, trace: list) -> Answer | None:
        fr = self.frame
        if fr.choice:
            pick = self._pick(q, fr.choice["options"], fr.choice.get("sum", False))
            if pick is not None:
                trace.append(f"analysis: picked {pick.text} for {fr.choice['slot']}")
                fr.slots[fr.choice["slot"]] = pick
                fr.choice = None
                return self._solve([], [], trace, keep=True)
        else:
            stated, ents = self._inputs(q, trace)
            for x in stated:
                x.source = "answer"
            if stated or ents:
                before = set(fr.slots)
                fr_pending = fr.pending
                a = self._solve(stated, ents, trace, lossless=bool(LOSSLESS.search(q)), keep=True)
                if set(fr.slots) != before or fr.pending != fr_pending or a.kind == "answer":
                    return a
        if re.search(r"\d", q) and not QUESTIONISH.search(q):
            ask = (BY_ID[fr.formula].slots[0].ask if not fr.pending else
                   next(s for s in BY_ID[fr.formula].slots if s.name == fr.pending).ask)
            return Answer(f"{ask[:-1]}, with its unit: for example 50 watt-hours, 5 watts or 3.7 volts.", "clarify",
                          fr.finding, trace)
        # Something else entirely: the open question is dropped, and the turn goes to the other faculties.
        fr.pending, fr.choice = "", None
        self.live = False
        self.changed = True
        return None

    @staticmethod
    def _pick(q: str, options: list, can_sum: bool = False) -> Quantity | None:
        low = q.lower()
        if can_sum and re.search(r"\b(?:both|all|together|combined|total|add(?:ed)? (?:them|up))\b", low):
            return Quantity(options[0].dim, sum(o.value for o in options), " + ".join(o.text for o in options),
                            "answer", note=f"{listing([say(o) for o in options])} added together")
        for w, i in ORDINALS.items():
            if re.search(rf"\b{w}\b", low):
                return options[i]
        stated = parse(q)
        for x in stated:
            hit = [o for o in options if o.dim == x.dim and abs(o.value - x.value) < 1e-9 * max(1, abs(o.value))]
            if len(hit) == 1:
                return hit[0]
        words = set(re.findall(r"[a-z]+", low))
        hits = [o for o in options if set(_qual(o).lower().split()) & words]
        return hits[0] if len(hits) == 1 else None

    def _followup(self, q: str, trace: list) -> Answer | None:
        """A change to the last finished calculation: "at half the load", "with a 100 Wh battery", "no losses"."""
        fr = self.frame
        f = BY_ID[fr.formula]
        if fr.result is None:
            return None
        if calculate(re.sub(r"^(?:and|so|ok|okay|now|then)\b[\s,]*", "", q, flags=re.I)):
            return None                    # "and what is 15% of 80" is maths, not a change to the estimate
        names = [s.name for s in f.slots]
        changed: list[str] = []
        low = q.lower()
        for rx, k in SCALE_WORDS:
            m = re.search(rx, low)
            if not m:
                continue
            k = k if k is not None else float(m.group(1))
            target = next((n for n, trx in TARGETS if n in names and re.search(trx, low)), None)
            if target and target in fr.slots:
                fr.slots[target] = fr.slots[target].scaled(k)
                changed.append(target)
                break
        stated, ents = self._inputs(q, trace)
        stated = [x for x in stated if x.next_word != "of"]          # "15% of 80" is an expression, not an input
        stated = [x for x in stated if x.dim != "fraction" or re.search(r"efficien|loss|convert", q, re.I)]
        for x in stated:
            slot = next((s for s in f.slots if s.dim == x.dim and s.name not in changed), None)
            if slot is None and x.dim in ("charge", "current"):
                slot = next((s for s in f.slots if x.dim in s.composite), None)
            if slot is None:
                continue
            if x.dim != slot.dim:          # 20 Ah: a new battery charge, at the old battery's voltage if we had one
                old_v = next((p for p in fr.slots.get(slot.name, Quantity("", 0)).parts if p.dim == "voltage"), None)
                if old_v is None:
                    fr.partial[slot.name] = x
                    fr.slots.pop(slot.name, None)
                    changed.append(slot.name)
                    continue
                x = self._compose(slot, x, old_v)
            fr.slots[slot.name] = x
            changed.append(slot.name)
        for key, qs in ents:
            for s in f.slots:
                if s.name in changed:
                    continue
                got = self._from_entities(s, [(key, qs)], fr, trace)
                if got == "choice":
                    return self._ask_choice(trace)
                if got is not None:
                    fr.slots[s.name] = got
                    changed.append(s.name)
        if LOSSLESS.search(q) and "efficiency" in names:
            fr.slots["efficiency"] = Quantity("fraction", 1.0, "no conversion losses", "stated")
            changed.append("efficiency")
        if not changed:
            return None
        trace.append(f"analysis: what-if on {', '.join(changed)}")
        cond = "With " + listing([_with(BY_ID[fr.formula], n, fr.slots[n]) for n in changed if n in fr.slots])
        return self._solve([], [], trace, keep=True, conditional=cond if cond != "With " else "")

    # ------------------------------------------------------------------ computing and saying

    def _compute(self, trace: list, conditional: str = "") -> Answer:
        fr = self.frame
        f = BY_ID[fr.formula]
        s = fr.slots
        v = {k: x.value for k, x in s.items()}
        bad = _validate(f.id, v)
        if bad:
            fr.result = None
            fr.finding = Finding(bad, UNKNOWN, f.id)
            return Answer(bad, "refuse", fr.finding, trace)
        if f.id == "runtime":
            out = v["battery"] * v["efficiency"] / v["load"]
            step = f"{fmt_plain(_r(v['battery']))} Wh x {fmt_plain(_r(v['efficiency']))} / {fmt_plain(_r(v['load']))} W" \
                   f" = {fmt_num(out, 4)} h"
            body = f"the estimated runtime is {fmt_hours(out)}: {_desc(s['battery'])} at {_desc(s['load'])}"
            eff = s["efficiency"]
            if eff.source == "assumed":
                body += f", assuming {fmt_percent(eff.value)} conversion efficiency"
            elif eff.value >= 1:
                body += ", with no conversion losses"
            else:
                body += f", at {fmt_percent(eff.value)} conversion efficiency"
        elif f.id == "daily":
            out = v["power"] * v["hours_per_day"]
            step = f"{fmt_plain(_r(v['power']))} W x {fmt_plain(_r(v['hours_per_day']))} h = {fmt_num(out, 4)} Wh per day"
            body = (f"the estimated daily requirement is {fmt_energy(out)}: {_desc(s['power'])} for "
                    f"{fmt_num(v['hours_per_day'])} hours a day")
        elif f.id == "energy":
            out = v["power"] * v["duration"]
            step = f"{fmt_plain(_r(v['power']))} W x {fmt_plain(_r(v['duration']))} h = {fmt_num(out, 4)} Wh"
            body = f"the energy used is {fmt_energy(out)}: {_desc(s['power'])} for {fmt_hours(v['duration'])}"
        else:  # power
            out = v["voltage"] * v["current"]
            step = f"{fmt_plain(_r(v['voltage']))} V x {fmt_plain(_r(v['current']))} A = {fmt_num(out, 4)} W"
            body = f"the power is {fmt_power(out)}: {_desc(s['voltage'])} at {_desc(s['current'])}"
        notes = [x.note for x in _all(s.values()) if x.note and x.source != "assumed"]
        if notes:
            body += "; " + "; ".join(dict.fromkeys(notes))
        text = (f"{conditional}, {body}." if conditional else body[:1].upper() + body[1:] + ".")
        fr.result = out
        assumed = [f"{_label(f, k)} {say(x)}: {x.note}" for k, x in s.items() if x.source == "assumed"]
        assumed += [x.note for x in _all(s.values()) if x.note and x.source != "assumed"]
        facts = [x.fact["id"] for x in _all(s.values()) if x.fact]
        fr.finding = Finding(text, ESTIMATE if assumed else DERIVED, f.id, {k: x.to_json() for k, x in s.items()},
                             _steps(s) + [step], assumed, facts=list(dict.fromkeys(facts)), conditional=conditional)
        trace.append(f"analysis: {step} ({fr.finding.status})")
        return Answer(text, "answer", fr.finding, trace, result=out)

    def explain(self) -> str:
        """How the last answer was reached: the formula, each input and where it came from, the arithmetic."""
        fr = self.frame
        if not fr or not fr.finding or fr.result is None:
            return "There's no calculation to explain yet."
        f = BY_ID[fr.formula]
        out = [f"I used {f.expr}."]
        for s in f.slots:
            x = fr.slots.get(s.name)
            if x is not None:
                out.append(f"{s.label[:1].upper() + s.label[1:]}: {say(x)}, {_provenance(x)}.")
        out.append(f"{fr.finding.steps[-1].replace(' x ', ' times ').replace(' / ', ' divided by ')}.")
        out.append("That's an estimate, because it rests on an assumption." if fr.finding.status == ESTIMATE
                   else "Every input was given or is in my corpus, so that's a calculation, not an estimate.")
        return " ".join(out)

    # ------------------------------------------------------------------ persistence

    def to_json(self) -> dict:
        return {"frame": self.frame.to_json() if self.frame else None, "live": self.live}

    def restore(self, d: dict) -> None:
        fr = d.get("frame")
        self.frame = Frame.from_json(fr) if fr else None
        self.live = bool(d.get("live")) and self.frame is not None
        if self.frame and self.frame.result is not None:
            self.frame.finding = Finding("", DERIVED, self.frame.formula)
            try:
                self._compute([], "")         # re-derive the finding (and check the inputs still validate)
            except (KeyError, ZeroDivisionError):
                self.frame, self.live = None, False


# ---------------------------------------------------------------------- helpers

def _r(x: float) -> float:
    return float(f"{x:.6g}")


def _all(qs):
    for x in qs:
        yield x
        yield from _all(x.parts)


def _rel(rel: str) -> str:
    return rel.lower().replace("_", " ")


def _qual(x: Quantity) -> str:
    if x.fact and x.fact.get("qual"):
        return x.fact["qual"].split("=", 1)[-1].lower().replace("_", " ")
    return _rel(x.fact["relation"]) if x.fact else x.text


def _label(f: Formula, name: str) -> str:
    return next((s.label for s in f.slots if s.name == name), name)


def _with(f: Formula, name: str, x: Quantity) -> str:
    if name == "efficiency" and x.value >= 1:
        return "no conversion losses"
    word = {"battery": "the battery", "load": "the load", "power": "the power draw", "hours_per_day": "the device running",
            "duration": "a running time of", "efficiency": "conversion efficiency at"}.get(name, f"the {name}")
    if name == "hours_per_day":
        return f"{word} {fmt_num(x.value)} hours a day"
    if name in ("duration", "efficiency"):
        return f"{word} {say(x)}"
    return f"{word} at {say(x)}"


def _desc(x: Quantity) -> str:
    """An input as the answer names it: '50 watt-hours', "Raspberry Pi 5's 12 watts",
    '37 watt-hours (10,000 milliamp-hours at 3.7 volts)'."""
    base = say(x)
    if x.parts and x.dim in ("energy", "power"):
        a, b = x.parts
        base += f" ({say(a)} at {say(b)})"
    subj = x.fact["subject"] if x.fact else next((p.fact["subject"] for p in x.parts if p.fact), "")
    return f"{name_of(subj)}'s {base}" if x.source == "corpus" and subj else base


def _provenance(x: Quantity) -> str:
    if x.source == "corpus":
        facts = [x.fact] if x.fact else [p.fact for p in x.parts if p.fact]
        cited = listing([f"{name_of(f['subject'])} {_rel(f['relation'])} {f['value']} {unit_of(f['unit'])}".strip()
                         for f in facts])
        return f"from my corpus ({cited})"
    if x.source == "assumed":
        return f"assumed, because {x.note}"
    if x.source == "derived":
        a, b = x.parts
        return f"from {say(a)} at {say(b)}" + (f", {x.note}" if x.note else "") + \
            (" (from my corpus)" if any(p.source == "corpus" for p in x.parts) else "")
    if x.source == "previous":
        return "changed from the last answer"
    return "as you said"


def _steps(slots: dict) -> list[str]:
    out = []
    for x in slots.values():
        if x.parts and len(x.parts) == 2:
            a, b = x.parts
            out.append(f"{a.text} x {b.text} = {say(x)}")
    return out


def _validate(fid: str, v: dict) -> str:
    """Why these inputs can't be used, or ''."""
    for k, x in v.items():
        if x < 0 or (x == 0 and k in ("load", "battery", "power", "voltage", "current")):
            return f"The {k.replace('_', ' ')} has to be more than zero, so I can't work that out."
    if "efficiency" in v and not 0 < v["efficiency"] <= 1:
        return "An efficiency has to be above 0% and at most 100%, so I can't use that figure."
    if v.get("hours_per_day", 0) > 24:
        return "A day has only 24 hours, so I can't use that operating time."
    return ""


# ---------------------------------------------------------------------- measuring a thing (logic.formula)
#
# The requirement check (navigator MEASURE / REQUIRE) asks "what is this thing's energy figure?".  The answer uses
# the same units (computer/quantity.py) and the same composition rule (energy = charge x voltage) as the estimates
# above, so there is one unit table and one formula engine.

COMPOSE = {"energy": ("charge", "voltage"), "power": ("current", "voltage")}
BASE_CODE = {"energy": "WH", "charge": "AH", "power": "W", "voltage": "V", "current": "A", "mass": "KG",
             "length": "M", "area": "M2", "time": "HOURS"}
# Words in a relation's name that say it's meant to be a figure of that dimension: a fact named so, in a unit of
# another dimension (ENERGY_CAPACITY 2.0 KG), is reported as incompatible rather than silently ignored.
DIM_WORDS = {"energy": {"energy", "capacity", "watt", "wh"}, "charge": {"charge", "capacity"},
             "power": {"power", "draw", "consumption", "wattage"}, "voltage": {"voltage", "volt"},
             "current": {"current", "amperage"}, "mass": {"mass", "weight"},
             "length": {"length", "height", "radius", "distance", "width"}, "area": {"area"},
             "time": {"duration", "period", "runtime"}}
DIM_NOUN = {"energy": "energy figure", "charge": "charge figure", "power": "power figure", "voltage": "voltage",
            "current": "current", "mass": "mass", "length": "length", "area": "area", "time": "duration"}


@dataclass
class Measurement:
    subject: str
    dim: str
    status: str                                   # ok | several | conflict | missing | incompatible
    quantity: Quantity | None = None              # status ok
    candidates: list = field(default_factory=list)   # several / conflict: one Quantity per distinct value
    facts: list = field(default_factory=list)     # the Fact objects it rests on (evidence)
    rules: list = field(default_factory=list)     # exact conversions (unit.kwh_to_wh) and the functional rule
    formula: dict | None = None                   # {"expr", "step", "parts": [Quantity, Quantity]}
    incompatible: list = field(default_factory=list)  # facts named for this dimension in a unit of another
    rule: object = None                           # the Functional rule a conflict rests on
    reason: str = ""                              # why it's missing / incompatible, for the answer
    steps: list = field(default_factory=list)


def _rel_words(rel: str) -> set:
    return set(rel.lower().split("_"))


def conversion_rule(q: Quantity) -> str:
    """'unit.kwh_to_wh' for an exact conversion to the base unit, or '' if the figure was already in it."""
    unit = (q.fact or {}).get("unit", "").upper()
    base = BASE_CODE.get(q.dim, "")
    return f"unit.{unit.lower()}_to_{base.lower()}" if unit and base and unit != base else ""


def measure(subject: str, facts: list, dim: str, functional: dict | None = None, year: int = 0,
            min_conf: float = 0.5) -> Measurement:
    """`subject`'s figure for `dim` from its own facts: a fact in a unit of that dimension (converted exactly), or
    else the composition (charge x voltage).  Facts must hold in `year`.  Two different applicable values are a
    conflict when a functional rule covers their relation, otherwise "several"."""
    functional = functional or {}
    year = year or current_year()
    held = [f for f in facts if f.holds(year, min_conf)]
    direct, incompatible = [], []
    for f in held:
        if f.kind != "N":
            continue
        q = from_fact(f)
        if q is not None and q.dim == dim:
            direct.append((q, f))
        elif _rel_words(f.relation) & DIM_WORDS.get(dim, set()) and (q is None or q.dim not in (dim, *COMPOSE.get(dim, ()))):
            incompatible.append(f)
    if direct:
        return _settle(subject, dim, direct, functional, incompatible)
    if dim in COMPOSE:
        a, b = COMPOSE[dim]
        pa, pb = (measure(subject, facts, d, functional, year, min_conf) for d in (a, b))
        if pa.status == "ok" and pb.status == "ok":
            q = Quantity(dim, pa.quantity.value * pb.quantity.value, f"{pa.quantity.text} x {pb.quantity.text}",
                         "corpus", parts=[pa.quantity, pb.quantity])
            step = f"{pa.quantity.text} x {pb.quantity.text} = {fmt_plain(_r(q.value))} {BASE_CODE[dim]} ({say(q)})"
            return Measurement(subject, dim, "ok", q, facts=pa.facts + pb.facts, rules=pa.rules + pb.rules,
                               formula={"expr": f"{dim} = {a} x {b}", "step": step, "parts": [pa.quantity, pb.quantity]},
                               steps=pa.steps + pb.steps + [step])
        if incompatible:                       # named for this dimension, in a unit of another: say that first
            return _incompatible(subject, dim, incompatible)
        for p in (pa, pb):
            if p.status in ("conflict", "several", "incompatible"):
                p.reason = p.reason or f"its {DIM_NOUN[p.dim]} isn't settled, so its {DIM_NOUN[dim]} can't be worked out"
                return Measurement(subject, dim, p.status, candidates=p.candidates, facts=p.facts, rules=p.rules,
                                   incompatible=p.incompatible, rule=p.rule, reason=p.reason, steps=p.steps)
        have = [p for p in (pa, pb) if p.status == "ok"]
        if have:
            h = have[0]
            missing = b if h.dim == a else a
            return Measurement(subject, dim, "missing", facts=h.facts,
                               reason=f"It has a {DIM_NOUN[h.dim]} ({say(h.quantity)}) but no {DIM_NOUN[missing]}, so "
                                      f"its {DIM_NOUN[dim]} can't be worked out.")
        return Measurement(subject, dim, "missing", reason=f"It has no fact in a unit of {dim}, and no {a} and {b} to "
                                                           f"work one out from.")
    if incompatible:
        return _incompatible(subject, dim, incompatible)
    return Measurement(subject, dim, "missing", reason=f"It has no fact in a unit of {dim}.")


def _incompatible(subject, dim, facts) -> Measurement:
    units = sorted({unit_of(f.unit) or f.unit or "no unit" for f in facts})
    return Measurement(subject, dim, "incompatible", facts=list(facts), incompatible=list(facts),
                       reason=f"{_rel(facts[0].relation)} is recorded in {listing(units)}, which isn't a unit of {dim}")


def _settle(subject, dim, direct, functional, incompatible) -> Measurement:
    values: list = []                              # [(value, [(q, f), ...])], equal values (52 Wh, 0.052 kWh) merged
    for q, f in direct:
        for v in values:
            if abs(v[0] - q.value) <= 1e-9 * max(1.0, abs(q.value)):
                v[1].append((q, f))
                break
        else:
            values.append((q.value, [(q, f)]))
    rules = list(dict.fromkeys(r for q, _ in direct for r in [conversion_rule(q)] if r))
    facts = [f for _, f in direct]
    steps = [f"{q.text} = {fmt_plain(_r(q.value))} {BASE_CODE[dim]} ({conversion_rule(q)})" for q, _ in direct
             if conversion_rule(q)]
    if len(values) == 1:
        return Measurement(subject, dim, "ok", values[0][1][0][0], facts=facts, rules=rules, steps=steps)
    cands = [v[1][0][0] for v in values]
    by_rel: dict = {}
    for v in values:
        for q, f in v[1]:
            by_rel.setdefault(f.relation, set()).add(round(v[0], 9))
    rule = next((functional[r] for r, vs in by_rel.items() if r in functional and len(vs) > 1), None)
    if rule is not None:
        return Measurement(subject, dim, "conflict", candidates=cands, facts=facts, rules=rules + [rule.rule_id],
                           rule=rule, steps=steps)
    return Measurement(subject, dim, "several", candidates=cands, facts=facts, rules=rules, steps=steps,
                       reason="nothing says which one applies")


# What makes a turn a requirement check ("does X meet 50 Wh", "at least 40 Wh"): never a what-if on an estimate.
REQUIREMENT = re.compile(r"\b(?:meets?|satisf(?:y|ies)|fulfil(?:l|ls|s)?|at least|at most|no more than|"
                         r"no less than|enough for)\b", re.I)
