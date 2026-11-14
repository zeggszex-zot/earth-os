"""The OWL Rule Engine: pathways and knowledge rules kept as data (computer/data/rules.json), not as Python.

Two kinds of rules live in that file.

PATHWAY RULES decide what to do next.  The computer's code keeps a small state ("stage": "navigated",
"nav_status": "conflict", "lookup_spoken": true ...); the engine finds the highest-priority rule whose
condition holds and returns its action; the computer runs that action, updates the state, and asks again.

    {"rule_id": "knowledge.conflict",
     "when": {"all": [{"equals": ["stage", "navigated"]}, {"equals": ["nav_status", "conflict"]}]},
     "then": {"action": "say_navigator"},
     "priority": 90,
     "why": "the corpus disagrees with itself: say that, don't let the lookup pick a side"}

  Conditions:  all / any / not / none (NOR)                       combine, in four-valued logic (computer/logic.py)
               equals [key, v]  in [key, [v...]]  greater_than / less_than [key, n]
               contains [key, v]  exists key  fact key            read the state
  A key that isn't in the state is UNKNOWN, not false.  A rule fires its "then" when the condition is TRUE,
  its "else" (if it has one) when FALSE, and its "unknown" (if it has one) when UNKNOWN or CONFLICT;
  otherwise it doesn't fire and the next rule is tried.

  Actions are named by the code that runs the pathway, and only those names are accepted: a rule (whoever
  or whatever wrote it) can choose among the computer's own actions, never run anything else.  Every decision is
  bounded (MAX_STEPS per question, MAX_RULES evaluated per decision) and traced: "-v" shows which rule fired and why.

KNOWLEDGE RULES let a yes/no test say FALSE, not just "I can't find a link".  They are stated assumptions,
each with an id and a reason, that the navigator applies to the corpus's facts:

    disjoint   classes nothing belongs to two of ("mammal", "fish", "bird" ...): if the corpus says a shark is
               a fish, "is a shark a mammal" is FALSE.  If it also says the shark is a mammal: CONFLICT.
    complete   relations the corpus lists in full for a thing (ORBITS): Titan orbits Saturn, so "does Titan
               orbit Jupiter" is FALSE.  With "max": a thing with more values than that is a CONFLICT.

A rule is only as true as its assumption.  Read the file before you trust a FALSE.
"""

from __future__ import annotations

import json
import numbers
from dataclasses import dataclass, field
from pathlib import Path

from . import logic
from .logic import CONFLICT, FALSE, TRUE, UNKNOWN

RULES_FILE = Path(__file__).resolve().parent / "data" / "rules.json"
MAX_STEPS = 8          # actions per pathway run
MAX_RULES = 200        # rules evaluated per decision
MAX_DEPTH = 6          # nesting of all/any/not/none

COMBINE = ("all", "any", "not", "none")
COMPARE = ("equals", "in", "greater_than", "less_than", "contains", "exists", "fact")
RULE_KEYS = {"rule_id", "when", "then", "else", "unknown", "priority", "why"}


class RuleError(ValueError):
    pass


# ---------------------------------------------------------------------- conditions

def _scalar(v) -> bool:
    return v is None or isinstance(v, (str, bool, int, float))


def validate_condition(c, where: str = "when", depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise RuleError(f"{where}: nested more than {MAX_DEPTH} deep")
    if not isinstance(c, dict) or len(c) != 1:
        raise RuleError(f"{where}: a condition is one {{op: ...}}, got {c!r}")
    (op, arg), = c.items()
    if op in ("all", "any", "none"):
        if not isinstance(arg, list) or not arg:
            raise RuleError(f"{where}.{op}: needs a non-empty list")
        for i, x in enumerate(arg):
            validate_condition(x, f"{where}.{op}[{i}]", depth + 1)
    elif op == "not":
        validate_condition(arg, f"{where}.not", depth + 1)
    elif op in ("exists", "fact"):
        if not isinstance(arg, str):
            raise RuleError(f"{where}.{op}: needs a state key")
    elif op in ("equals", "contains", "greater_than", "less_than", "in"):
        if not (isinstance(arg, list) and len(arg) == 2 and isinstance(arg[0], str)):
            raise RuleError(f"{where}.{op}: needs [key, value]")
        if op in ("greater_than", "less_than") and (isinstance(arg[1], bool) or not isinstance(arg[1], numbers.Real)):
            raise RuleError(f"{where}.{op}: needs a number")
        if op == "in" and not (isinstance(arg[1], list) and all(_scalar(x) for x in arg[1])):
            raise RuleError(f"{where}.in: needs a list of values")
        if op != "in" and not _scalar(arg[1]):
            raise RuleError(f"{where}.{op}: the value must be a plain value")
    else:
        raise RuleError(f"{where}: unknown condition {op!r} (allowed: {', '.join(COMBINE + COMPARE)})")


def evaluate(c: dict, state: dict) -> str:
    """A condition against the state -> TRUE / FALSE / UNKNOWN / CONFLICT."""
    (op, arg), = c.items()
    if op == "all":
        return logic.and_(*[evaluate(x, state) for x in arg])
    if op == "any":
        return logic.or_(*[evaluate(x, state) for x in arg])
    if op == "none":
        return logic.nor_(*[evaluate(x, state) for x in arg]) if len(arg) > 1 else logic.not_(evaluate(arg[0], state))
    if op == "not":
        return logic.not_(evaluate(arg, state))
    key = arg if isinstance(arg, str) else arg[0]
    if key not in state:
        return UNKNOWN
    v = state[key]
    if op == "fact":
        if v in logic.VALUES:
            return v
        return UNKNOWN if v is None else TRUE if v else FALSE
    if op == "exists":
        return UNKNOWN if v is None else TRUE if v not in ("", [], {}, False) else FALSE
    if v is None or v == UNKNOWN:
        return UNKNOWN
    if v == CONFLICT and op != "equals":
        return CONFLICT
    want = arg[1]
    if op == "equals":
        return TRUE if v == want else FALSE
    if op == "in":
        return TRUE if v in want else FALSE
    if op == "contains":
        try:
            return TRUE if want in v else FALSE
        except TypeError:
            return UNKNOWN
    if isinstance(v, bool) or not isinstance(v, numbers.Real):
        return UNKNOWN
    return TRUE if (v > want if op == "greater_than" else v < want) else FALSE


# ---------------------------------------------------------------------- rules

@dataclass
class Rule:
    rule_id: str
    when: dict
    then: dict | None = None
    else_: dict | None = None
    unknown: dict | None = None
    priority: int = 0
    why: str = ""
    order: int = 0


@dataclass
class Decision:
    action: str
    args: dict
    rule_id: str
    branch: str              # "then" | "else" | "unknown"
    truth: str
    why: str = ""

    def __str__(self) -> str:
        return f"rule {self.rule_id} ({self.branch}: condition {self.truth}) -> {self.action}" + \
            (f" {self.args}" if self.args else "") + (f"  [{self.why}]" if self.why else "")


def _validate_action(a, where: str, actions: dict[str, set] | None) -> None:
    if not isinstance(a, dict) or "action" not in a or set(a) - {"action", "args"}:
        raise RuleError(f"{where}: needs {{\"action\": name, \"args\": {{...}}}}")
    name, args = a["action"], a.get("args", {})
    if actions is not None and name not in actions:
        raise RuleError(f"{where}: unknown action {name!r} (allowed: {', '.join(sorted(actions))})")
    if not isinstance(args, dict) or not all(isinstance(k, str) and _scalar(v) for k, v in args.items()):
        raise RuleError(f"{where}: args must be names with plain values")
    if actions is not None and set(args) - actions[name]:
        raise RuleError(f"{where}: {name} doesn't take {', '.join(sorted(set(args) - actions[name]))}")


def parse_rules(raw: list, actions: dict[str, set] | None = None) -> list[Rule]:
    """Check every rule completely before any of them is used.  actions: {name: allowed arg names}."""
    if not isinstance(raw, list):
        raise RuleError("a pathway is a list of rules")
    out, seen = [], set()
    for i, r in enumerate(raw):
        where = f"rule {i}"
        if not isinstance(r, dict):
            raise RuleError(f"{where}: not an object")
        rid = r.get("rule_id")
        if not isinstance(rid, str) or not rid:
            raise RuleError(f"{where}: needs a rule_id")
        where = f"rule {rid}"
        if rid in seen:
            raise RuleError(f"{where}: rule_id used twice")
        seen.add(rid)
        extra = set(r) - RULE_KEYS
        if extra:
            raise RuleError(f"{where}: unknown field {', '.join(sorted(extra))}")
        if "when" not in r or not any(k in r for k in ("then", "else", "unknown")):
            raise RuleError(f"{where}: needs when and at least one of then / else / unknown")
        validate_condition(r["when"], f"{where}.when")
        for k in ("then", "else", "unknown"):
            if k in r:
                _validate_action(r[k], f"{where}.{k}", actions)
        pr = r.get("priority", 0)
        if isinstance(pr, bool) or not isinstance(pr, int):
            raise RuleError(f"{where}: priority must be a whole number")
        out.append(Rule(rid, r["when"], r.get("then"), r.get("else"), r.get("unknown"), pr, str(r.get("why", "")), i))
    return out


class Engine:
    """Pick the action for a state: highest priority first, file order between equals."""

    def __init__(self, rules: list[Rule]):
        self.rules = sorted(rules, key=lambda r: (-r.priority, r.order))

    def decide(self, state: dict, trace: list | None = None) -> Decision | None:
        for r in self.rules[:MAX_RULES]:
            t = evaluate(r.when, state)
            branch = {TRUE: "then", FALSE: "else"}.get(t, "unknown")
            act = {"then": r.then, "else": r.else_, "unknown": r.unknown}[branch]
            if act is None:
                if t in (UNKNOWN, CONFLICT) and trace is not None:
                    trace.append(f"rule {r.rule_id}: condition {t}, no unknown branch, skipped")
                continue
            return Decision(act["action"], dict(act.get("args", {})), r.rule_id, branch, t, r.why)
        return None


def run_pathway(engine: Engine, state: dict, handlers: dict, trace: list | None = None, max_steps: int = MAX_STEPS):
    """Decide, act, repeat.  A handler updates the state and returns None to go on, or a result to stop.
    Returns (result, decisions).  Stops with (None, decisions) if no rule fires or the step budget runs out."""
    decisions = []
    trace = trace if trace is not None else []
    for _ in range(max_steps):
        d = engine.decide(state, trace)
        if d is None:
            trace.append("pathway: no rule fired")
            return None, decisions
        decisions.append(d)
        trace.append(f"pathway: {d}")
        out = handlers[d.action](state, **d.args)
        if out is not None:
            return out, decisions
    trace.append(f"pathway: stopped after {max_steps} steps")
    return None, decisions


# ---------------------------------------------------------------------- knowledge rules

@dataclass
class Disjoint:
    rule_id: str
    classes: frozenset
    why: str = ""


@dataclass
class Complete:
    rule_id: str
    relation: str
    max: int | None = None
    why: str = ""


@dataclass
class Functional:
    """A relation with one applicable value at a time (a battery's rated energy capacity).  Two values that both
    hold in the year asked about (Fact.holds: a value whose period has ended, or hasn't begun, doesn't apply) and
    still differ after exact unit conversion are a CONFLICT, said as one.  Without this rule two such values are
    only "several figures": an answer that holds for every one of them is still given, and one that depends on
    which applies is UNKNOWN.  Different relations (rated and usable capacity) never conflict with each other."""
    rule_id: str
    relation: str
    why: str


@dataclass
class Knowledge:
    disjoint: list[Disjoint] = field(default_factory=list)
    complete: dict[str, Complete] = field(default_factory=dict)
    functional: dict[str, Functional] = field(default_factory=dict)

    def disjoint_pair(self, mine: set, theirs: set):
        """A rule and two classes (a in mine, b in theirs, a != b) that nothing can both be, or None."""
        for d in self.disjoint:
            a = sorted(mine & d.classes)
            b = sorted(theirs & d.classes)
            for x in a:
                for y in b:
                    if x != y:
                        return d, x, y
        return None

    def clash_within(self, mine: set):
        """Two classes in one set that a rule says nothing can both be (the corpus contradicts itself)."""
        for d in self.disjoint:
            both = sorted(mine & d.classes)
            if len(both) > 1:
                return d, both[0], both[1]
        return None


def parse_knowledge(raw: dict) -> Knowledge:
    if not isinstance(raw, dict) or set(raw) - {"disjoint", "complete", "functional"}:
        raise RuleError("knowledge has disjoint, complete and functional lists")
    k, seen = Knowledge(), set()
    for d in raw.get("disjoint", []):
        if set(d) - {"rule_id", "classes", "why"} or not isinstance(d.get("classes"), list) or len(d["classes"]) < 2:
            raise RuleError(f"disjoint rule {d.get('rule_id')}: needs rule_id, two or more classes, why")
        if not all(isinstance(c, str) and c == c.upper() for c in d["classes"]):
            raise RuleError(f"disjoint rule {d.get('rule_id')}: classes are corpus keys, like MAMMAL")
        if d["rule_id"] in seen:
            raise RuleError(f"rule_id {d['rule_id']} used twice")
        seen.add(d["rule_id"])
        k.disjoint.append(Disjoint(d["rule_id"], frozenset(d["classes"]), d.get("why", "")))
    for c in raw.get("complete", []):
        if set(c) - {"rule_id", "relation", "max", "why"} or not isinstance(c.get("relation"), str):
            raise RuleError(f"complete rule {c.get('rule_id')}: needs rule_id, relation, optional max, why")
        mx = c.get("max")
        if mx is not None and (isinstance(mx, bool) or not isinstance(mx, int) or mx < 1):
            raise RuleError(f"complete rule {c['rule_id']}: max must be a whole number of at least 1")
        if c["rule_id"] in seen:
            raise RuleError(f"rule_id {c['rule_id']} used twice")
        seen.add(c["rule_id"])
        k.complete[c["relation"]] = Complete(c["rule_id"], c["relation"], mx, c.get("why", ""))
    for f in raw.get("functional", []):
        if set(f) != {"rule_id", "relation", "why"} or not all(isinstance(f[x], str) and f[x] for x in f):
            raise RuleError(f"functional rule {f.get('rule_id')}: needs rule_id, relation and why")
        if f["relation"] != f["relation"].upper():
            raise RuleError(f"functional rule {f['rule_id']}: relations are corpus names, like ENERGY_CAPACITY")
        if f["rule_id"] in seen or f["relation"] in k.functional:
            raise RuleError(f"functional rule {f['rule_id']}: rule_id or relation used twice")
        seen.add(f["rule_id"])
        k.functional[f["relation"]] = Functional(f["rule_id"], f["relation"], f["why"])
    return k


_DEFAULT: list = []


def default_knowledge() -> Knowledge:
    """The knowledge rules in computer/data/rules.json, loaded once.  A broken file means no knowledge rules
    (every test is TRUE or UNKNOWN, as before), never a crash: `python3 -m computer.rules` says what's wrong."""
    if not _DEFAULT:
        try:
            _DEFAULT.append(load()["knowledge"])
        except (OSError, ValueError):
            _DEFAULT.append(Knowledge())
    return _DEFAULT[0]


def load(path=None) -> dict:
    """The rules file, checked: {"knowledge": Knowledge, "pathways": {name: raw rules}}.  Pathways are parsed
    against their actions by the code that runs them (parse_rules), so an unknown action fails at startup."""
    data = json.loads(Path(path or RULES_FILE).read_text())
    if not isinstance(data, dict) or set(data) - {"format", "knowledge", "pathways"}:
        raise RuleError("rules file: format, knowledge and pathways only")
    if data.get("format") not in ("cdos-computer-rules/1", "cdos-owl-rules/1"):   # old name still loads
        raise RuleError("rules file: format must be cdos-computer-rules/1")
    pw = data.get("pathways", {})
    if not isinstance(pw, dict):
        raise RuleError("pathways: {name: [rules]}")
    return {"knowledge": parse_knowledge(data.get("knowledge", {})), "pathways": pw}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m computer.rules", description="Check a rules file.")
    ap.add_argument("path", nargs="?", default=str(RULES_FILE))
    a = ap.parse_args(argv)
    from .core import PATHWAY_ACTIONS
    try:
        r = load(a.path)
        for name, rules in r["pathways"].items():
            acts = PATHWAY_ACTIONS.get(name)
            if acts is None:
                raise RuleError(f"pathway {name!r}: the computer has no such pathway")
            print(f"pathway {name}: {len(parse_rules(rules, acts))} rules ok")
        k = r["knowledge"]
        print(f"knowledge: {len(k.disjoint)} disjoint groups, {len(k.complete)} complete relations, "
              f"{len(k.functional)} functional relations ok")
    except (RuleError, ValueError, OSError) as e:
        print(f"not ok: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
