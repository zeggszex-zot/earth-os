"""The navigator's plan language: what a planner (rules or a small model) is allowed to say.

A plan is a few lines, one operation each.  Variables are $1..$9.

    $1 = FIND france                 name -> ranked candidate entities (backtracks if the first leads nowhere)
    $2 = GET $1 CAPITAL              follow a relation (exact name) ...
    $3 = GET $2 ~population          ... or by words (~a,b): resolved against the relations the entity really has
    $4 = GET $1 ~breathe INHERIT     inherit through IS_A when the thing itself has nothing ("a whale is a mammal...")
    $5 = WHO $1 ~orbit               reverse: which subjects have this relation pointing at $1
    $6 = FILTER $5 planet            keep items that are (through IS_A) a planet
    $7 = COMPARE $3 $4 MORE bigger   numbers from two GETs: which is MORE / LESS (same relation, same unit)
    $8 = COUNT $3                    a stated count if the corpus has one, else how many items
    $9 = TEST $4 mammal              is this value (or an IS_A ancestor of it) among the items?
                                     TRUE, FALSE (a knowledge rule rules it out), UNKNOWN or CONFLICT
    $5 = LOGIC AND $3 $4             combine TESTs (or LOGICs): AND, OR, NOR take 2-3; NOT takes 1 (computer/logic.py)
    $7 = CALC RATIO $3 $4            arithmetic on numbers from GETs: SUM (2-3), DIFF, RATIO (same measurement,
                                     units converted) or DIV (any two, e.g. population / area); the result is DERIVED
    $2 = MEASURE $1 energy           the thing's figure in a dimension: a fact in a unit of it (converted exactly),
                                     or charge x voltage; says so when figures conflict or are in the wrong unit
    $2 = MEASURE $1 length ~tall     ... only the figures named for the question's word (HEIGHT, ELEVATION), if any are
    $3 = REQUIRE $2 AT_LEAST 50 Wh   TRUE / FALSE against a stated value (AT_MOST too); UNKNOWN if there's no
                                     figure, CONFLICT if a functional rule says two figures can't both apply
    $7 = SELECT $5 $6                which of the REQUIREs (2-3) came out TRUE ("which of alpha and beta meets 50 Wh")
    $3 = RANK $2 river               keep the items (word senses) that share the most words with "river"
    $4 = EXCEPT $2 glad, cheerful    drop items already said ("another word for happy?")
    CHECK $3                         stop and admit it if $3 is empty or below the confidence floor
    ANSWER $3 [$7]                   say what was found, step by step, with the evidence

Relations: an exact corpus relation (CAPITAL), words (~capital or ~radius,area), or * (every relation).
Dictionary relations are words too: ~definition, ~synonym,similar, ~antonym, ~isa,hypernym, ~hyponym,
~derivation, ~example.  RANK and EXCEPT only reorder or remove what an earlier step found.

The language is small on purpose: a 135M-parameter model can learn it, the validator can
check it completely, and every step can be shown to a person ("-v" in the chat).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

OPS = ("FIND", "GET", "WHO", "FILTER", "COMPARE", "COUNT", "TEST", "LOGIC", "CALC", "MEASURE", "REQUIRE", "SELECT",
       "RANK", "EXCEPT", "CHECK", "ANSWER")
DIMENSIONS = ("energy", "charge", "power", "voltage", "current", "mass", "length", "area", "time")
COMPARATORS = ("AT_LEAST", "AT_MOST")
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$")
CONNECTIVES = ("AND", "OR", "NOR", "NOT")
CALC_OPS = ("SUM", "DIFF", "RATIO", "DIV")
MAX_STEPS = 10

VAR = re.compile(r"^\$[1-9]$")
REL = re.compile(r"^(\*|~[a-z]+(,[a-z]+)*|[A-Z][A-Z0-9_]*)$")
WORDS = re.compile(r"^[a-z0-9][a-z0-9 '\-]*$")
WORD_LIST = re.compile(r"^[a-z0-9][a-z0-9 ',\-]*$")

# What each op produces: an entity list or a list of values (facts).
PRODUCES = {"FIND": "entities", "GET": "values", "WHO": "entities", "FILTER": "same",
            "COMPARE": "verdict", "COUNT": "verdict", "CALC": "verdict", "MEASURE": "measure", "REQUIRE": "truth",
            "SELECT": "verdict", "TEST": "truth", "LOGIC": "truth", "RANK": "same", "EXCEPT": "same"}

GRAMMAR = r"""
root    ::= (step "\n")+ "ANSWER " var (" " var)? "\n"?
step    ::= var " = " (find | get | who | filter | compare | count | test | logic | calc | measure | require | select | rank |
            except) | "CHECK " var
var     ::= "$" [1-9]
find    ::= "FIND " words
get     ::= "GET " var " " rel (" INHERIT")?
who     ::= "WHO " var " " rel
filter  ::= "FILTER " var " " words
compare ::= "COMPARE " var " " var " " ("MORE" | "LESS") (" " words)?
count   ::= "COUNT " var
test    ::= "TEST " var " " words
logic   ::= "LOGIC " ("AND" | "OR" | "NOR") " " var " " var (" " var)? | "LOGIC NOT " var
calc    ::= "CALC " ("SUM" " " var " " var (" " var)? | ("DIFF" | "RATIO" | "DIV") " " var " " var) (" " words)?
measure ::= "MEASURE " var " " ("energy" | "charge" | "power" | "voltage" | "current" | "mass" | "length" | "area" | "time")
            (" ~" [a-z]+ ("," [a-z]+)*)?
require ::= "REQUIRE " var " " ("AT_LEAST" | "AT_MOST") " " "-"? [0-9]+ ("." [0-9]+)? " " [A-Za-z%] [A-Za-z%-]*
select  ::= "SELECT " var " " var (" " var)?
rank    ::= "RANK " var " " words
except  ::= "EXCEPT " var " " [a-z0-9] [a-z0-9 ',-]*
rel     ::= "*" | "~" [a-z]+ ("," [a-z]+)* | [A-Z] [A-Z0-9_]*
words   ::= [a-z0-9] [a-z0-9 '-]*
""".strip()
"""GBNF for llama.cpp's llama-server ("grammar" field): the model cannot emit a malformed plan."""


@dataclass
class Step:
    out: str            # "$2", or "" for CHECK / ANSWER
    op: str
    args: list[str]
    flags: set = field(default_factory=set)
    text: str = ""      # FIND / FILTER / TEST words, COMPARE label

    def __str__(self) -> str:
        parts = [self.op] + self.args + sorted(self.flags) + ([self.text] if self.text else [])
        return (f"{self.out} = " if self.out else "") + " ".join(parts)


@dataclass
class Plan:
    steps: list[Step]
    kind: str = ""      # what the planner thought the question was (chain, compare, ...): for traces and stats
    source: str = ""    # "rules" or the model's label
    notes: list = field(default_factory=list)   # how the planner read a name ("alpha" as BATTERY_ALPHA), said first

    def __str__(self) -> str:
        return "\n".join(str(s) for s in self.steps)

    @property
    def gets(self) -> int:
        return sum(s.op == "GET" for s in self.steps)


class PlanError(ValueError):
    pass


def parse(text: str, known_relations: set[str] | None = None) -> Plan:
    """Parse and validate.  Raises PlanError with the reason; a plan that parses is safe to run."""
    lines = [l.strip() for l in (text or "").strip().splitlines() if l.strip()]
    if not lines:
        raise PlanError("empty plan")
    if len(lines) > MAX_STEPS + 1:
        raise PlanError(f"too many steps ({len(lines)})")
    steps, kinds, dims, by_op = [], {}, {}, {}
    for i, line in enumerate(lines):
        out = ""
        m = re.match(r"^(\$[1-9])\s*=\s*(.+)$", line)
        if m:
            out, line = m.group(1), m.group(2)
            if out in kinds:
                raise PlanError(f"{out} assigned twice")
        tok = line.split()
        op, rest = tok[0], tok[1:]
        if op not in OPS:
            raise PlanError(f"unknown operation {op!r}")
        needs_out = op not in ("CHECK", "ANSWER")
        if needs_out != bool(out):
            raise PlanError(f"{op} {'needs' if needs_out else 'takes no'} result variable")

        def var(j):
            if j >= len(rest) or not VAR.match(rest[j]):
                raise PlanError(f"{op}: argument {j + 1} must be a variable")
            if rest[j] not in kinds:
                raise PlanError(f"{op}: {rest[j]} used before it is set")
            return rest[j]

        def words(j, rx=WORDS):
            w = " ".join(rest[j:]).strip().strip("\"'").lower()
            if not rx.match(w or "?"):
                raise PlanError(f"{op}: needs words")
            return w

        st = Step(out, op, [])
        if op == "FIND":
            st.text = words(0)
        elif op in ("GET", "WHO"):
            st.args = [var(0)]
            if len(rest) < 2 or not REL.match(rest[1]):
                raise PlanError(f"{op}: bad relation {rest[1:2]}")
            rel = rest[1]
            if known_relations is not None and rel[0] not in "~*" and rel not in known_relations:
                raise PlanError(f"{op}: no relation {rel} in the corpus")
            st.args.append(rel)
            extra = rest[2:]
            if op == "GET" and extra == ["INHERIT"]:
                st.flags.add("INHERIT")
            elif extra:
                raise PlanError(f"{op}: unexpected {' '.join(extra)!r}")
        elif op in ("FILTER", "TEST", "RANK"):
            st.args = [var(0)]
            st.text = words(1)
            if op == "RANK" and kinds[st.args[0]] != "values":
                raise PlanError("RANK: needs values from GET")
        elif op == "EXCEPT":
            st.args = [var(0)]
            st.text = words(1, WORD_LIST)
            if kinds[st.args[0]] in ("verdict", "truth"):
                raise PlanError("EXCEPT: needs a list, not a verdict")
        elif op == "COMPARE":
            st.args = [var(0), var(1)]
            if len(rest) < 3 or rest[2] not in ("MORE", "LESS"):
                raise PlanError("COMPARE: needs MORE or LESS")
            st.args.append(rest[2])
            st.text = " ".join(rest[3:]).lower()
            for a in st.args[:2]:
                if kinds[a] != "values":
                    raise PlanError(f"COMPARE: {a} must come from GET")
        elif op == "LOGIC":
            if not rest or rest[0] not in CONNECTIVES:
                raise PlanError(f"LOGIC: needs one of {', '.join(CONNECTIVES)}")
            n = len(rest) - 1
            if (rest[0] == "NOT" and n != 1) or (rest[0] != "NOT" and not 2 <= n <= 3):
                raise PlanError(f"LOGIC {rest[0]}: wrong number of parts")
            st.args = [rest[0]] + [var(j) for j in range(1, len(rest))]
            for a in st.args[1:]:
                if kinds[a] != "truth":
                    raise PlanError(f"LOGIC: {a} must come from TEST or LOGIC")
        elif op == "CALC":
            if not rest or rest[0] not in CALC_OPS:
                raise PlanError(f"CALC: needs one of {', '.join(CALC_OPS)}")
            vs = []
            for t in rest[1:]:
                if not VAR.match(t):
                    break
                vs.append(t)
            n = len(vs)
            if (rest[0] == "SUM" and not 2 <= n <= 3) or (rest[0] != "SUM" and n != 2):
                raise PlanError(f"CALC {rest[0]}: wrong number of parts")
            st.args = [rest[0]] + [var(j) for j in range(1, n + 1)]
            for a in st.args[1:]:
                if kinds[a] != "values":
                    raise PlanError(f"CALC: {a} must come from GET")
            label = " ".join(rest[n + 1:]).lower()
            if label and not WORDS.match(label):
                raise PlanError("CALC: bad label")
            st.text = label
        elif op == "MEASURE":
            st.args = [var(0)]
            if kinds[st.args[0]] != "entities":
                raise PlanError("MEASURE: needs a thing from FIND")
            if len(rest) not in (2, 3) or rest[1] not in DIMENSIONS:
                raise PlanError(f"MEASURE: needs one of {', '.join(DIMENSIONS)}")
            if len(rest) == 3 and not re.match(r"^~[a-z]+(,[a-z]+)*$", rest[2]):
                raise PlanError("MEASURE: optional words are ~a,b (what the question called the figure)")
            st.args += rest[1:]
            dims[out] = rest[1]
        elif op == "REQUIRE":
            st.args = [var(0)]
            if kinds[st.args[0]] != "measure":
                raise PlanError("REQUIRE: needs a figure from MEASURE")
            if len(rest) != 4 or rest[1] not in COMPARATORS or not NUMBER.match(rest[2]):
                raise PlanError("REQUIRE: $n AT_LEAST|AT_MOST <number> <unit>")
            from .quantity import unit_dimension
            ud = unit_dimension(rest[3])
            if ud is None:
                raise PlanError(f"REQUIRE: unknown unit {rest[3]!r}")
            if ud != dims[st.args[0]]:
                raise PlanError(f"REQUIRE: {rest[3]} measures {ud}, but {st.args[0]} is {dims[st.args[0]]}")
            st.args += rest[1:4]
        elif op == "SELECT":
            if not 2 <= len(rest) <= 3:
                raise PlanError("SELECT: needs 2 or 3 REQUIRE results")
            st.args = [var(j) for j in range(len(rest))]
            for a in st.args:
                if by_op.get(a) != "REQUIRE":
                    raise PlanError(f"SELECT: {a} must come from REQUIRE")
        elif op == "COUNT":
            st.args = [var(0)]
            if len(rest) > 1:
                raise PlanError("COUNT takes one variable")
        elif op in ("CHECK", "ANSWER"):
            if not rest or len(rest) > (1 if op == "CHECK" else 3):
                raise PlanError(f"{op}: needs variables")
            st.args = [var(j) for j in range(len(rest))]
        if op == "ANSWER" and i != len(lines) - 1:
            raise PlanError("ANSWER must be the last step")
        if out:
            by_op[out] = op
            p = PRODUCES[op]
            kinds[out] = kinds[st.args[0]] if p == "same" else p
        steps.append(st)
    if steps[-1].op != "ANSWER":
        raise PlanError("plan has no ANSWER")
    return Plan(steps)
