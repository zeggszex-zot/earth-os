"""Capabilities: what each part of CDOS declares about itself, checked before the computer starts.

Every tool the computer can use is declared here with its inputs, output, errors, cost, whether it is deterministic,
whether it writes state, and what runs instead when it's unavailable.  `Registry.validate()` refuses to start
(CapabilityError, with a named reason) when a declaration is incomplete or contradicts the code:

  - a required field is missing or has the wrong type, or a version isn't semver
  - deterministic: true, but a declared module reads the clock, random numbers, a hardware RNG or the network
    (checked in the source, with ast).  The one exception is the year: a deterministic capability may import
    computer.clock only if it declares an input with default_from "system.clock", and the envelope records the
    value used.  Elapsed-time calls (time.monotonic, perf_counter, process_time) measure, they don't decide,
    and are allowed; timings are left out of every hash.
  - deterministic: true, but it requires a non-deterministic capability other than through such a default
  - writes_state: true without being on WRITERS
  - optional: true with fallback "none"; a fallback that doesn't exist; a cycle of fallbacks
  - an error code that isn't in the shared vocabulary (ERROR_CODES)

At run time, `error()` refuses a code the capability didn't declare, `choose()` follows fallbacks for an
optional capability that's unavailable (and records the skip), and `need()` raises Unavailable for a required
one.  Same pattern as computer/rules.py: everything is checked up front, nothing is trusted at run time.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import keyword
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

ERROR_CODES = ("MISSING_FACT", "UNIT_INCOMPATIBLE", "DIMENSION_ERROR", "CONFLICT", "UNKNOWN_ENTITY", "UNAVAILABLE",
               "INVALID_INPUT", "BOUND_EXCEEDED", "CORPUS_INTEGRITY")
REQUIRE_KINDS = ("capability", "file", "binary", "network")   # a requires entry is "<kind>:<what>"
INPUT_TYPES = re.compile(r"^[a-z][a-z_]*$")
WRITERS = {"state.working",            # the only capabilities allowed to write anything (computer/state.py)
           "neural.experience",        # ... the neural core's append-only log, in its own folder (never data/)
           "growth.engine",            # ... the Growth Engine: its tasks, journal and answers, and staged models
           "robot.journal",         # ... the robot's append-only decision journal
           "governor.authority"}    # ... the Governor's audit log, its head record and the emergency-stop latch
REQUIRED = {"name": str, "version": str, "inputs": dict, "output": dict, "deterministic": bool, "cost": dict,
            "writes_state": bool, "requires": list, "optional": bool, "fallback": str, "errors": list}
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
NAME = re.compile(r"^[a-z]+(?:\.[a-z_]+)+$")
CPU = ("low", "medium", "high")

# What a deterministic module may not do (attribute calls, and imports).
FORBIDDEN_CALLS = {("time", "time"), ("time", "localtime"), ("time", "gmtime"), ("time", "strftime"),
                   ("time", "ctime"), ("time", "time_ns"), ("datetime", "now"), ("datetime", "today"),
                   ("datetime", "utcnow"), ("date", "today"), ("os", "urandom"), ("os", "getrandom")}
FORBIDDEN_IMPORTS = {"random", "secrets", "socket", "urllib", "http", "requests", "ssl", "asyncio"}
FORBIDDEN_TEXT = ("/dev/hwrng", "/dev/random", "/dev/urandom")


class CapabilityError(ValueError):
    pass


class Unavailable(Exception):
    def __init__(self, name: str, reason: str):
        super().__init__(f"{name} is unavailable: {reason}")
        self.name, self.reason = name, reason


@dataclass
class Capability:
    name: str
    version: str
    inputs: dict
    output: dict
    deterministic: bool
    cost: dict
    writes_state: bool
    requires: list
    optional: bool
    fallback: str
    errors: list
    modules: list = field(default_factory=list)    # source files the determinism check reads ("computer.navigator")
    summary: str = ""

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass
class Usage:
    """What one turn used: capabilities (with versions) and the optional ones skipped, with the reason."""
    used: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


class Registry:
    def __init__(self, declarations: list[dict] | None = None, root: Path | None = None):
        self.root = root or Path(__file__).resolve().parent.parent
        self.caps: dict[str, Capability] = {}
        self.off: dict[str, str] = {}               # name -> why it's unavailable
        self.turn = Usage()
        for d in declarations if declarations is not None else DECLARATIONS:
            self.register(d)

    # ------------------------------------------------------------------ declaring

    def register(self, d: dict) -> Capability:
        if not isinstance(d, dict):
            raise CapabilityError("a declaration is a dict")
        name = d.get("name", "?")
        for k, t in REQUIRED.items():
            if k not in d:
                raise CapabilityError(f"{name}: missing field {k!r}")
            if not isinstance(d[k], t) or (t is int and isinstance(d[k], bool)):
                raise CapabilityError(f"{name}: field {k!r} must be {t.__name__}")
        extra = set(d) - set(REQUIRED) - {"modules", "summary"}
        if extra:
            raise CapabilityError(f"{name}: unknown field(s) {sorted(extra)}")
        if not NAME.match(name):
            raise CapabilityError(f"{name}: names are dotted lower case, like corpus.lookup")
        if name in self.caps:
            raise CapabilityError(f"{name}: declared twice")
        if not SEMVER.match(d["version"]):
            raise CapabilityError(f"{name}: version {d['version']!r} isn't semver (1.2.3)")
        c = d["cost"]
        if set(c) != {"memory_mb", "cpu"} or not isinstance(c["memory_mb"], (int, float)) \
                or isinstance(c["memory_mb"], bool) or c["cpu"] not in CPU:
            raise CapabilityError(f"{name}: cost is {{memory_mb: number, cpu: {'|'.join(CPU)}}}")
        for k, spec in d["inputs"].items():
            if not isinstance(spec, dict) or not isinstance(spec.get("type"), str) or \
                    not isinstance(spec.get("required"), bool) or set(spec) - {"type", "required", "default_from"}:
                raise CapabilityError(f"{name}: input {k!r} is {{type, required[, default_from]}}")
        for r in d["requires"]:
            if not isinstance(r, str) or ":" not in r or r.split(":", 1)[0] not in REQUIRE_KINDS or not r.split(":", 1)[1]:
                raise CapabilityError(f"{name}: requires entries are strings like 'capability:corpus.lookup' "
                                      f"({'/'.join(REQUIRE_KINDS)}), not {r!r}")
        out = d["output"]
        if not isinstance(out.get("type"), str) or not INPUT_TYPES.match(out["type"]) or \
                not all(isinstance(k, str) and isinstance(v, str) for k, v in out.items()):
            raise CapabilityError(f"{name}: output is {{type: name[, spec: where it's defined]}}, all strings")
        for k, spec in d["inputs"].items():
            if not INPUT_TYPES.match(str(spec.get("type", ""))):
                raise CapabilityError(f"{name}: input {k!r} type must be a lower-case name")
            if "default_from" in spec and not isinstance(spec["default_from"], str):
                raise CapabilityError(f"{name}: input {k!r} default_from must name a capability")
        if not all(isinstance(e, str) for e in d["errors"]) or len(set(d["errors"])) != len(d["errors"]):
            raise CapabilityError(f"{name}: errors is a list of distinct codes")
        if not all(isinstance(m, str) for m in d.get("modules", [])):
            raise CapabilityError(f"{name}: modules are strings")
        bad = [e for e in d["errors"] if e not in ERROR_CODES]
        if bad:
            raise CapabilityError(f"{name}: error codes {bad} aren't in the vocabulary {ERROR_CODES}")
        cap = Capability(**{k: d[k] for k in REQUIRED}, modules=list(d.get("modules", [])), summary=d.get("summary", ""))
        self.caps[name] = cap
        return cap

    def validate(self) -> "Registry":
        """Every rule in the module docstring.  Raises CapabilityError naming the capability and the reason."""
        for cap in self.caps.values():
            if cap.writes_state and cap.name not in WRITERS:
                raise CapabilityError(f"{cap.name}: writes_state is true but it isn't on the writer allowlist")
            if cap.optional and cap.fallback == "none":
                raise CapabilityError(f"{cap.name}: optional capabilities need a fallback")
            if cap.fallback != "none" and cap.fallback not in self.caps:
                raise CapabilityError(f"{cap.name}: fallback {cap.fallback!r} isn't declared")
            for r in cap.requires:
                if r.startswith("capability:") and r.split(":", 1)[1] not in self.caps:
                    raise CapabilityError(f"{cap.name}: requires {r!r}, which isn't declared")
            for k, spec in cap.inputs.items():
                src = spec.get("default_from")
                if src and src not in self.caps:
                    raise CapabilityError(f"{cap.name}: input {k!r} defaults from {src!r}, which isn't declared")
            seen, nxt = {cap.name}, cap.fallback
            while nxt != "none":
                if nxt in seen:
                    raise CapabilityError(f"{cap.name}: fallbacks go round in a cycle ({' -> '.join([*seen, nxt])})")
                seen.add(nxt)
                nxt = self.caps[nxt].fallback
            if cap.deterministic:
                self._check_deterministic(cap)
        return self

    def _check_deterministic(self, cap: Capability) -> None:
        defaults = {spec.get("default_from") for spec in cap.inputs.values()} - {None}
        for r in cap.requires:
            if r.startswith("capability:"):
                other = self.caps[r.split(":", 1)[1]]
                if not other.deterministic and other.name not in defaults:
                    raise CapabilityError(f"{cap.name}: deterministic, but requires non-deterministic {other.name} "
                                          "other than as an input's default")
        for mod in cap.modules:
            path = self._source(mod)
            if path is None:
                raise CapabilityError(f"{cap.name}: module {mod!r} not found")
            why = scan_file(path, clock_ok="system.clock" in defaults)
            if why:
                raise CapabilityError(f"{cap.name}: deterministic, but {mod} {why}")

    def _source(self, mod: str) -> Path | None:
        if mod.endswith(".py"):
            p = Path(mod)
            return p if p.is_absolute() else self.root / p
        spec = importlib.util.find_spec(mod)
        return Path(spec.origin) if spec and spec.origin else None

    # ------------------------------------------------------------------ at run time

    def begin_turn(self) -> Usage:
        self.turn = Usage()
        return self.turn

    def disable(self, name: str, reason: str) -> None:
        if name not in self.caps:
            raise CapabilityError(f"no capability {name!r}")
        self.off[name] = reason

    def enable(self, name: str) -> None:
        self.off.pop(name, None)

    def available(self, name: str) -> tuple[bool, str]:
        if name not in self.caps:
            return False, "not declared"
        if name in self.off:
            return False, self.off[name]
        return True, ""

    def mark(self, name: str) -> None:
        c = self.caps[name]
        entry = {"name": c.name, "version": c.version}
        if entry not in self.turn.used:
            self.turn.used.append(entry)

    def choose(self, name: str) -> str:
        """The capability to run for `name`: itself if available, else its fallbacks in turn (each skip is
        recorded).  A required capability that's unavailable raises Unavailable."""
        cur = name
        while True:
            ok, why = self.available(cur)
            if ok:
                self.mark(cur)
                return cur
            c = self.caps.get(cur)
            if c is None or not c.optional or c.fallback == "none":
                raise Unavailable(cur, why)
            skip = {"name": cur, "reason": why}
            if skip not in self.turn.skipped:
                self.turn.skipped.append(skip)
            cur = c.fallback

    def need(self, name: str) -> str:
        return self.choose(name)

    def error(self, name: str, code: str, message: str, recoverable: bool, details: dict | None = None) -> dict:
        """A structured error from `name`.  A code the capability didn't declare is a programming error."""
        c = self.caps.get(name)
        if c is None:
            raise CapabilityError(f"error from undeclared capability {name!r}")
        if code not in c.errors:
            raise CapabilityError(f"{name} returned {code}, which it doesn't declare ({c.errors})")
        return {"code": code, "capability": c.ref, "message": message, "recoverable": recoverable,
                "details": details or {}}

    def ref(self, name: str) -> str:
        return self.caps[name].ref


_SCANNED: dict = {}


def scan_file(path: Path, clock_ok: bool = False) -> str:
    """scan() of a file, remembered by (path, size, mtime): a changed file is read again."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns, clock_ok)
    if key not in _SCANNED:
        _SCANNED[key] = scan(path.read_text(encoding="utf-8"), clock_ok)
    return _SCANNED[key]


def scan(source: str, clock_ok: bool = False) -> str:
    """Why this source isn't deterministic, or ''.  Reads Python's token stream, so a docstring or comment that
    mentions time.time() doesn't count.  (Not the syntax tree: navigator.py's tree alone takes ~9 MB to build, which
    matters at start-up on a 512 MB board; the tokens are read a few at a time.)"""
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING}
    win: list = []                      # the last few significant tokens
    stmt: list | None = None            # an import statement being read
    at_start = True
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in skip:
            continue
        if tok.type in (tokenize.NEWLINE, tokenize.ENDMARKER) or (tok.type == tokenize.OP and tok.string == ";"):
            if stmt:
                why = _import_why(stmt, clock_ok)
                if why:
                    return why
            stmt, at_start, win = None, True, []
            continue
        if at_start and tok.type == tokenize.NAME and tok.string in ("import", "from"):
            stmt = []
        at_start = False
        if stmt is not None:
            stmt.append(tok.string)
            continue
        if tok.type == tokenize.STRING and len(win) >= 2 and win[-1] == "(" and \
                re.fullmatch(r"[A-Za-z_]\w*", win[-2]) and not keyword.iskeyword(win[-2]):
            # A device path as the first argument of a call (open, os.open, Path) reads it; the same text in a
            # tuple of names to forbid (like FORBIDDEN_TEXT above) doesn't.
            try:
                if ast.literal_eval(tok.string) in FORBIDDEN_TEXT:
                    return f"reads {ast.literal_eval(tok.string)}"
            except (ValueError, SyntaxError):
                pass
        win = (win + [tok.string])[-4:]
        if len(win) == 4 and win[1] == "." and win[3] == "(" and (win[0], win[2]) in FORBIDDEN_CALLS:
            return f"calls {win[0]}.{win[2]}()"
    return ""


def _import_why(stmt: list, clock_ok: bool) -> str:
    words = [w for w in stmt if w not in ("(", ")")]
    if words[0] == "import":
        mods = " ".join(words[1:]).replace(" . ", ".").replace(" .", ".").replace(". ", ".").split(",")
        for m in mods:
            name = m.split(" as ")[0].strip()
            if name.split(".")[0] in FORBIDDEN_IMPORTS:
                return f"imports {name}"
        return ""
    i = words.index("import")
    mod = "".join(words[1:i])
    names = [n.strip().split(" ")[0] for n in " ".join(words[i + 1:]).split(",") if n.strip()]
    level = len(mod) - len(mod.lstrip("."))
    bare = mod.lstrip(".")
    if level == 0 and bare.split(".")[0] in FORBIDDEN_IMPORTS:
        return f"imports from {bare}"
    if bare in ("clock", "computer.clock") and not clock_ok:
        return "reads the clock (computer.clock) without declaring a year input with default_from system.clock"
    if level == 0 and bare in ("time", "datetime", "os"):
        for n in names:
            if (bare, n) in FORBIDDEN_CALLS or (bare == "datetime" and n in ("datetime", "date")):
                return f"imports {n} from {bare}"
    return ""


# ---------------------------------------------------------------------- imports the scan doesn't follow
#
# scan() reads the modules a capability declares, not everything they import.  Every computer module a deterministic
# module imports must be one of those declared modules, computer.clock (through a declared year input), or listed here
# with the names used and why that's safe.  tests/test_capabilities.py fails on any other import, so a new
# dependency of the answer path gets looked at before it's trusted.
REVIEWED_IMPORTS = {
    "computer.navdata": ({"shape"}, "shape() is a pure function of a relation name; navdata's random and clock are used "
                               "only to generate training data"),
    "computer.capabilities": ({"default_registry", "Unavailable"}, "the registry itself: checked by scan() in the tests"),
    "computer.core": ({"PATHWAY_ACTIONS"}, "a constant dict, used only by `python3 -m computer.rules`, not on the answer path"),
}


def internal_imports(source: str, module: str = "") -> list[tuple[str, set]]:
    """(computer module, names) for every `from .x import ...` / `from computer.x import ...`, wherever it appears.
    `module` is the importing module's dotted name, so a sub-package's imports resolve: in computer.neural.runtime,
    `from .model import` is computer.neural.model and `from ..plan import` is computer.plan.  Without it, the module is
    taken to be directly in computer/ (as every module was before computer/neural)."""
    package = module.rsplit(".", 1)[0].split(".") if module.count(".") >= 1 else ["computer"]
    out = []
    for m in re.finditer(r"^\s*from (\.+[\w.]*|computer(?:\.\w+)+) import \(?([^)\n]+)", source, re.M):
        target = m.group(1)
        names = {n.strip().split(" as ")[0] for n in m.group(2).split(",") if n.strip()}
        if target.startswith("."):
            level = len(target) - len(target.lstrip("."))
            base = package[: len(package) - (level - 1)] if level > 1 else package
            if not target.lstrip("."):          # `from . import logic`: each name is a whole module
                out += [(".".join(base + [n]), {"*"}) for n in sorted(names)]
                continue
            target = ".".join(base + [target.lstrip(".")])
        out.append((target, names))
    return out


def unreviewed_imports(registry: "Registry") -> list[str]:
    """Imports from deterministic modules that are neither checked nor reviewed (empty: all accounted for)."""
    checked = {m for c in registry.caps.values() if c.deterministic for m in c.modules}
    bad = []
    for c in registry.caps.values():
        if not c.deterministic:
            continue
        clock_ok = any(s.get("default_from") == "system.clock" for s in c.inputs.values())
        for mod in c.modules:
            path = registry._source(mod)
            for target, names in internal_imports(path.read_text(encoding="utf-8"), "" if mod.endswith(".py") else mod):
                if target in checked or (target == "computer.clock" and clock_ok):
                    continue
                allowed = REVIEWED_IMPORTS.get(target, (set(), ""))[0]
                if not names <= allowed:
                    bad.append(f"{c.name}: {mod} imports {sorted(names - allowed)} from {target}")
    return sorted(set(bad))


# ---------------------------------------------------------------------- the inventory

def _d(name, version, summary, inputs, output, *, det, cpu="low", mem=1, writes=False, requires=(), optional=False,
       fallback="none", errors=(), modules=()):
    return {"name": name, "version": version, "summary": summary, "inputs": inputs, "output": output,
            "deterministic": det, "cost": {"memory_mb": mem, "cpu": cpu}, "writes_state": writes,
            "requires": list(requires), "optional": optional, "fallback": fallback, "errors": list(errors),
            "modules": list(modules)}


YEAR = {"year": {"type": "int", "required": False, "default_from": "system.clock"}}
ENVELOPE = {"type": "envelope", "spec": "computer/envelope.py"}

# memory_mb figures are rough estimates from this machine (the test corpus), not measured on a Pi.
DECLARATIONS = [
    _d("system.clock", "1.0.0", "today's year, for questions that don't say", {}, {"type": "int"}, det=False,
       modules=["computer.clock"]),
    _d("corpus.lookup", "1.2.0", "facts by subject and by value from the data/ shards; content hash checked at load",
       {"question": {"type": "str", "required": True}, **YEAR}, ENVELOPE, det=True, mem=15,
       requires=["file:data/meta.json", "capability:system.clock"],
       errors=["MISSING_FACT", "UNKNOWN_ENTITY", "CORPUS_INTEGRITY"],
       modules=["computer.corpus"]),
    _d("planner.rules", "1.2.0", "question -> plans, by deterministic patterns",
       {"question": {"type": "str", "required": True}}, {"type": "plans"}, det=True, errors=["INVALID_INPUT"],
       modules=["computer.planner"]),
    _d("navigator.execute", "1.2.0", "runs a validated plan on the corpus and keeps the evidence",
       {"plan": {"type": "plan", "required": True}, **YEAR}, ENVELOPE, det=True, cpu="medium", mem=2,
       requires=["capability:corpus.lookup", "capability:system.clock"],
       errors=["MISSING_FACT", "UNIT_INCOMPATIBLE", "DIMENSION_ERROR", "CONFLICT", "UNKNOWN_ENTITY", "UNAVAILABLE",
               "INVALID_INPUT", "BOUND_EXCEEDED"],
       modules=["computer.navigator", "computer.plan", "computer.logic", "computer.phrase"]),
    _d("knowledge.rules", "1.1.0", "disjoint, complete and functional rules; pathways",
       {"rules_file": {"type": "path", "required": False}}, {"type": "knowledge"}, det=True,
       errors=["INVALID_INPUT"], modules=["computer.rules"]),
    _d("logic.calc", "1.0.0", "arithmetic on what was said (no eval)", {"text": {"type": "str", "required": True}},
       {"type": "number"}, det=True, errors=["INVALID_INPUT"], modules=["computer.calc"]),
    _d("logic.units", "1.0.0", "units, dimensions and exact conversions (computer/quantity.py)",
       {"quantity": {"type": "quantity", "required": True}}, {"type": "quantity"}, det=True,
       errors=["UNIT_INCOMPATIBLE", "DIMENSION_ERROR", "INVALID_INPUT"], modules=["computer.quantity"]),
    _d("logic.units_gnu", "0.1.0", "GNU Units as a second unit table (not implemented in this release)",
       {"quantity": {"type": "quantity", "required": True}}, {"type": "quantity"}, det=True, optional=True,
       fallback="logic.units", requires=["binary:units"], errors=["UNIT_INCOMPATIBLE", "UNAVAILABLE"]),
    _d("logic.formula", "1.0.0", "dimension-checked formulas: energy = charge x voltage, power = current x voltage, "
       "runtime, daily energy", {"inputs": {"type": "quantities", "required": True}, **YEAR}, {"type": "quantity"},
       det=True,
       requires=["capability:logic.units", "capability:system.clock"],
       errors=["DIMENSION_ERROR", "INVALID_INPUT", "MISSING_FACT"], modules=["computer.analysis"]),
    _d("logic.requirement", "1.0.0", "MEASURE a thing's figure in a dimension and REQUIRE it AT_LEAST / AT_MOST a "
       "stated value", {"measure": {"type": "measurement", "required": True},
                        "limit": {"type": "quantity", "required": True}, **YEAR}, ENVELOPE, det=True,
       requires=["capability:logic.units", "capability:logic.formula", "capability:system.clock"],
       errors=["MISSING_FACT", "UNIT_INCOMPATIBLE", "DIMENSION_ERROR", "CONFLICT", "UNAVAILABLE"],
       modules=["computer.navigator"]),
    _d("analysis.estimate", "1.1.0", "estimates from stated quantities and corpus figures, with clarification",
       {"text": {"type": "str", "required": True}, "frame": {"type": "frame", "required": False}, **YEAR},
       ENVELOPE, det=True, requires=["capability:logic.formula", "capability:system.clock"],
       errors=["MISSING_FACT", "INVALID_INPUT", "CONFLICT"], modules=["computer.analysis", "computer.quantity"]),
    _d("knowledge.lexicon", "1.0.0", "learned word -> relation mappings (lexicon.json), read at run time",
       {"word": {"type": "str", "required": True}, **YEAR}, {"type": "mappings"}, det=True, optional=True,
       fallback="navigator.execute", requires=["file:lexicon.json", "capability:system.clock"],
       modules=["computer.lexicon"]),
    _d("planner.controller", "1.0.0", "the learned controller (fixed weights) picks a plan shape",
       {"question": {"type": "str", "required": True}}, {"type": "plans"}, det=True, cpu="medium", optional=True,
       fallback="planner.rules", requires=["file:controller.json"], modules=["computer.controller"]),
    _d("planner.neural", "0.1.0", "the neural core's active model (fixed, validated weights) picks a plan shape; its "
       "plans are only the controller's templates and go through the validator, the executor and accept()",
       {"question": {"type": "str", "required": True}}, {"type": "plans"}, det=True, cpu="medium", optional=True,
       fallback="planner.rules", requires=["file:neural_registry"],
       modules=["computer.neural.runtime", "computer.neural.model", "computer.neural.schema",
                "computer.neural.components"]),
    _d("neural.experience", "0.1.0", "append-only, hash-chained log of the neural core's predictions and their checked "
       "outcomes, in <neural_registry>/experience/ (never read when answering, never the corpus)",
       {"record": {"type": "dict", "required": True}}, {"type": "bool"}, det=False, writes=True,
       requires=["file:neural_registry"], modules=["computer.neural.experience"]),
    _d("growth.engine", "0.2.0", "finds what the computer can't answer, makes tasks, takes a person's answers, trains "
       "and evaluates candidates into staging; never promotes, never writes the corpus",
       {"registry": {"type": "path", "required": True}}, {"type": "tasks"}, det=False, cpu="high", mem=200, writes=True,
       requires=["file:neural_registry"], modules=["computer.growth.engine"]),
    _d("robot.safety", "0.1.0", "the robot's safety envelope: deterministic limits on every candidate motion, "
       "checked along its predicted path; fail closed", {"state": {"type": "robot_state", "required": True},
                                                         "action": {"type": "robot_action", "required": True},
                                                         "now": {"type": "float", "required": True}},
       {"type": "verdict"}, det=True, errors=["INVALID_INPUT"],
       modules=["computer.robotics.safety", "computer.robotics.world", "computer.robotics.state"]),
    _d("robot.controller", "0.1.0", "observations in, a checked decision out; only an approved motion is ever "
       "returned as a command (simulation only: no hardware driver)",
       {"observations": {"type": "list", "required": True}, "goal": {"type": "point", "required": True},
        "now": {"type": "float", "required": False, "default_from": "system.clock"}},
       {"type": "decision"}, det=True, requires=["capability:robot.safety", "capability:system.clock"],
       errors=["INVALID_INPUT", "UNAVAILABLE"], modules=["computer.robotics.controller"]),
    _d("robot.policy", "0.1.0", "the learned robot policy (fixed, validated weights): a candidate motion, never "
       "a command", {"state": {"type": "robot_state", "required": True}}, {"type": "robot_action"}, det=True,
       optional=True, fallback="robot.policy_expert", requires=["file:neural_registry"],
       modules=["computer.robotics.policy"]),
    _d("robot.policy_expert", "0.1.0", "goal-seeking within the limits, steering around obstacles (deterministic)",
       {"state": {"type": "robot_state", "required": True}}, {"type": "robot_action"}, det=True,
       modules=["computer.robotics.policy"]),
    _d("robot.sim", "0.1.0", "a simulated differential-drive base, sensors and obstacles (seeded noise)",
       {"command": {"type": "robot_action", "required": True}}, {"type": "observations"}, det=False,
       modules=["computer.robotics.sim"]),
    _d("robot.journal", "0.1.0", "append-only, hash-chained journal of every robot decision",
       {"decision": {"type": "dict", "required": True}}, {"type": "bool"}, det=False, writes=True,
       requires=["file:robotics_journal"], modules=["computer.neural.experience"]),
    _d("governor.authority", "0.1.0", "who may do what: roles from a root-owned policy, identities from the kernel, the "
       "persistent emergency-stop latch (engaged unless a verified record says an operator cleared it), and a "
       "hash-chained audit of every privileged decision", {"request": {"type": "dict", "required": True}},
       {"type": "decision"}, det=False, writes=True, requires=["file:governor_policy"],
       errors=["INVALID_INPUT", "UNAVAILABLE"], modules=["computer.governor"]),
    _d("api.local", "0.1.0", "the local API (a Unix socket, or TCP on loopback only) for LCARS, voice and tools",
       {"request": {"type": "http", "required": True}}, {"type": "json"}, det=False,
       requires=["file:api_socket"], errors=["INVALID_INPUT", "UNAVAILABLE"], modules=["computer.services.api"]),
    _d("planner.lm", "1.0.0", "a small model writes or proposes the plan (llama-server)",
       {"question": {"type": "str", "required": True}}, {"type": "plans"}, det=False, cpu="high", mem=150,
       optional=True, fallback="planner.rules", requires=["network:llm_endpoint"], errors=["UNAVAILABLE"]),
    _d("instinct.iching", "1.0.0", "casts the I Ching: chance by design, never facts or sums",
       {"question": {"type": "str", "required": False}}, {"type": "perspective"}, det=False,
       modules=["computer.iching"]),
    _d("system.diagnostics", "1.0.0", "processor temperature, load, memory, storage, battery, uptime",
       {"text": {"type": "str", "required": True}}, {"type": "text"}, det=False, requires=["file:/sys", "file:/proc"],
       modules=["computer.diagnostics"]),
    _d("state.working", "1.0.0", "the open calculation saved across restarts (computer/state.py)",
       {"state": {"type": "dict", "required": True}}, {"type": "bool"}, det=False, writes=True, optional=True,
       fallback="state.memory", requires=["file:state_file"]),
    _d("state.memory", "1.0.0", "working state kept in memory only", {"state": {"type": "dict", "required": True}},
       {"type": "bool"}, det=True),
    _d("voice.speech", "1.0.0", "Vosk ears, Piper or eSpeak voice", {"audio": {"type": "audio", "required": False}},
       {"type": "text"}, det=False, cpu="high", mem=80, optional=True, fallback="voice.print",
       errors=["UNAVAILABLE"]),
    _d("voice.print", "1.0.0", "text in, text out", {"text": {"type": "str", "required": True}}, {"type": "text"},
       det=True),
]


def default_registry(cfg=None, extra=()) -> Registry:
    """The declared inventory, validated, with what this configuration can't use switched off.  `extra`: plugin
    declarations (computer/plugins.py), each already checked on its own; they get the same validation here."""
    reg = Registry(DECLARATIONS + list(extra)).validate()
    reg.disable("logic.units_gnu", "not implemented in this release")
    if cfg is not None:
        if not getattr(cfg, "relation_lexicon", ""):
            reg.disable("knowledge.lexicon", "relation_lexicon not set")
        if not getattr(cfg, "navigator_controller", ""):
            reg.disable("planner.controller", "navigator_controller not set")
        if not getattr(cfg, "neural_registry", ""):
            reg.disable("planner.neural", "neural_registry not set")
        if not getattr(cfg, "neural_registry", "") or not getattr(cfg, "neural_experience", True):
            reg.disable("neural.experience", "neural_registry not set or neural_experience = false")
        if not getattr(cfg, "neural_registry", ""):
            reg.disable("growth.engine", "neural_registry not set")
        if not getattr(cfg, "robotics", False):
            for name in ("robot.controller", "robot.policy", "robot.sim", "robot.journal"):
                reg.disable(name, "robotics = false")
        elif not getattr(cfg, "neural_registry", ""):
            reg.disable("robot.policy", "neural_registry not set: the expert policy is used")
        if not getattr(cfg, "governor_policy", ""):
            reg.disable("governor.authority", "governor_policy not set: nobody holds a privileged role")
        if not getattr(cfg, "api_socket", "") and not getattr(cfg, "api_tcp_port", 0):
            reg.disable("api.local", "api_socket not set")
        if getattr(cfg, "llm_mode", "off") == "off" or getattr(cfg, "navigator_planner", "rules") == "rules" \
                or not (getattr(cfg, "navigator_endpoint", "") or getattr(cfg, "llm_endpoint", "")):
            reg.disable("planner.lm", "no planner model configured")
        if not getattr(cfg, "state_file", ""):
            reg.disable("state.working", "state_file not set")
        if not getattr(cfg, "analysis", True):
            reg.disable("analysis.estimate", "analysis = false")
    return reg
