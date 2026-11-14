"""The shared neural interface: every learned (or replaceable) part of CDOS is a component with a declared, versioned
input and output, called only through a registry that checks both.

    IOSpec          a named, versioned record type: its fields, each with a type, unit, range or choices
    ComponentSpec   what a component is: name, implementation version, kind, input and output IOSpecs, its model
    Component       spec + run(); calling it validates the input, runs, then validates the output
    ComponentRegistry  register (name, semver and IO checked; a name once only), look up by name and interface major
                       version, call with failures isolated (ComponentFailure, never anything else)

Versioning.  An IOSpec's version is the interface contract; a component's version is its implementation.  A caller
asks for an interface major version (`get("robot.policy", input_major=1)`); a component whose input or output major
differs is refused, so a model trained against one contract can't be fed another.  Minor versions may add optional
fields.

The built-in components:

    reasoning.selector   question -> a probability per plan shape  (the neural core's model, computer/neural/runtime.py)
    robot.policy         robot state + goal -> a candidate motion   (computer/robotics/policy.py; never sent unchecked)

and any a plugin adds (computer/plugins.py).  A component never touches the corpus, the clock or the network through
this interface: inputs come in, outputs go out, and the deterministic parts of CDOS decide what happens next.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
TYPES = ("str", "int", "float", "bool", "list[str]", "list[float]", "list[list[float]]", "dict[str,float]")
KINDS = ("neural", "deterministic", "heuristic")
MAX_STR = 2000
MAX_LIST = 512


class InterfaceError(ValueError):
    """An input or output that doesn't match its declared IOSpec, or a spec that isn't valid."""


class ComponentFailure(RuntimeError):
    """A component that couldn't produce a valid output.  The caller falls back; nothing else is raised."""

    def __init__(self, name: str, reason: str):
        super().__init__(f"{name}: {reason}")
        self.name, self.reason = name, reason


def major(version: str) -> int:
    m = SEMVER.match(version or "")
    if not m:
        raise InterfaceError(f"version {version!r} isn't semver (1.2.3)")
    return int(m.group(1))


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    required: bool = True
    unit: str = ""
    min: float | None = None
    max: float | None = None
    choices: tuple = ()
    doc: str = ""

    def check(self, v, where: str) -> None:
        t = self.type
        bad = lambda why: InterfaceError(f"{where}.{self.name}: {why}")   # noqa: E731
        if t == "str":
            if not isinstance(v, str) or len(v) > MAX_STR:
                raise bad(f"must be a string of at most {MAX_STR} characters")
            if self.choices and v not in self.choices:
                raise bad(f"must be one of {', '.join(map(str, self.choices[:8]))}")
            return
        if t == "bool":
            if not isinstance(v, bool):
                raise bad("must be true or false")
            return
        if t in ("int", "float"):
            if t == "int" and (not isinstance(v, int) or isinstance(v, bool)):
                raise bad("must be an integer")
            if not _finite(v):
                raise bad("must be a finite number")
            self._range(float(v), bad)
            return
        if t == "list[str]":
            if not isinstance(v, list) or len(v) > MAX_LIST or not all(isinstance(x, str) for x in v):
                raise bad(f"must be a list of at most {MAX_LIST} strings")
            if self.choices and any(x not in self.choices for x in v):
                raise bad("has a value that isn't one of the choices")
            return
        if t == "list[float]":
            if not isinstance(v, list) or len(v) > MAX_LIST or not all(_finite(x) for x in v):
                raise bad(f"must be a list of at most {MAX_LIST} finite numbers")
            for x in v:
                self._range(float(x), bad)
            return
        if t == "list[list[float]]":
            if not isinstance(v, list) or len(v) > MAX_LIST or not all(
                    isinstance(r, list) and len(r) <= 16 and all(_finite(x) for x in r) for r in v):
                raise bad(f"must be a list of at most {MAX_LIST} short lists of finite numbers")
            return
        if t == "dict[str,float]":
            if not isinstance(v, dict) or len(v) > MAX_LIST or not all(isinstance(k, str) and _finite(x)
                                                                      for k, x in v.items()):
                raise bad("must be an object of names to finite numbers")
            if self.choices and any(k not in self.choices for k in v):
                raise bad("has a key that isn't one of the choices")
            for x in v.values():
                self._range(float(x), bad)
            return
        raise bad(f"unknown type {t!r}")

    def _range(self, x: float, bad) -> None:
        if self.min is not None and x < self.min:
            raise bad(f"{x:g} is below the minimum {self.min:g}{' ' + self.unit if self.unit else ''}")
        if self.max is not None and x > self.max:
            raise bad(f"{x:g} is above the maximum {self.max:g}{' ' + self.unit if self.unit else ''}")

    def to_json(self) -> dict:
        d = {"name": self.name, "type": self.type, "required": self.required}
        for k in ("unit", "min", "max", "doc"):
            if getattr(self, k) not in ("", None):
                d[k] = getattr(self, k)
        if self.choices:
            d["choices"] = list(self.choices)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Field":
        if not isinstance(d, dict) or set(d) - {"name", "type", "required", "unit", "min", "max", "choices", "doc"}:
            raise InterfaceError(f"a field is {{name, type[, required, unit, min, max, choices, doc]}}, not {d!r}")
        f = cls(d.get("name", ""), d.get("type", ""), bool(d.get("required", True)), d.get("unit", ""), d.get("min"),
                d.get("max"), tuple(d.get("choices", ())), d.get("doc", ""))
        if not re.fullmatch(r"[a-z][a-z0-9_]*", f.name) or f.type not in TYPES:
            raise InterfaceError(f"field {f.name!r}: names are lower_case and types one of {', '.join(TYPES)}")
        for k in ("min", "max"):
            if getattr(f, k) is not None and not _finite(getattr(f, k)):
                raise InterfaceError(f"field {f.name!r}: {k} must be a number")
        return f


@dataclass(frozen=True)
class IOSpec:
    name: str
    version: str
    fields: tuple

    def __post_init__(self):
        if not NAME.match(self.name):
            raise InterfaceError(f"interface name {self.name!r}: dotted lower case, like robot.state")
        major(self.version)
        names = [f.name for f in self.fields]
        if len(set(names)) != len(names):
            raise InterfaceError(f"{self.name}: a field is declared twice")

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def validate(self, data) -> dict:
        """The data, if it is exactly this record type: every required field, no unknown field, every value in range."""
        if not isinstance(data, dict):
            raise InterfaceError(f"{self.ref}: expected an object")
        known = {f.name: f for f in self.fields}
        extra = sorted(set(data) - set(known))
        if extra:
            raise InterfaceError(f"{self.ref}: unknown field(s) {', '.join(extra)}")
        for f in self.fields:
            if f.name not in data:
                if f.required:
                    raise InterfaceError(f"{self.ref}: missing {f.name}")
                continue
            f.check(data[f.name], self.ref)
        return data

    def to_json(self) -> dict:
        return {"name": self.name, "version": self.version, "fields": [f.to_json() for f in self.fields]}

    @classmethod
    def from_json(cls, d: dict) -> "IOSpec":
        if not isinstance(d, dict) or not isinstance(d.get("fields"), list):
            raise InterfaceError("an interface is {name, version, fields: [...]}")
        return cls(str(d.get("name", "")), str(d.get("version", "")), tuple(Field.from_json(f) for f in d["fields"]))


@dataclass(frozen=True)
class ComponentSpec:
    name: str
    version: str
    kind: str
    input: IOSpec
    output: IOSpec
    summary: str = ""
    model: dict = field(default_factory=dict)       # {"id", "sha256", ...} for a trained model; {} otherwise

    def __post_init__(self):
        if not NAME.match(self.name):
            raise InterfaceError(f"component name {self.name!r}: dotted lower case, like robot.policy")
        major(self.version)
        if self.kind not in KINDS:
            raise InterfaceError(f"{self.name}: kind must be one of {', '.join(KINDS)}")

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def to_json(self) -> dict:
        return {"name": self.name, "version": self.version, "kind": self.kind, "summary": self.summary,
                "input": self.input.to_json(), "output": self.output.to_json(), "model": dict(self.model)}


class Component:
    """Subclass and set `spec`; implement run(inputs) -> outputs.  Call the instance, never run() directly."""
    spec: ComponentSpec

    def run(self, inputs: dict) -> dict:
        raise NotImplementedError

    def __call__(self, inputs: dict) -> dict:
        self.spec.input.validate(inputs)
        try:
            out = self.run(inputs)
        except InterfaceError:
            raise
        except Exception as e:  # noqa: BLE001 - a component that fails is a failure, said as one
            raise ComponentFailure(self.spec.name, f"{type(e).__name__}: {e}") from None
        try:
            return self.spec.output.validate(out)
        except InterfaceError as e:
            raise ComponentFailure(self.spec.name, f"output doesn't match {self.spec.output.ref}: {e}") from None


class ComponentRegistry:
    def __init__(self):
        self._by_name: dict[str, Component] = {}
        self.refused: dict[str, str] = {}        # name -> why it wasn't registered (plugins, damaged models)

    def register(self, comp: Component, replace: bool = False) -> Component:
        spec = getattr(comp, "spec", None)
        if not isinstance(spec, ComponentSpec):
            raise InterfaceError(f"{type(comp).__name__} has no ComponentSpec")
        if not callable(getattr(comp, "run", None)):
            raise InterfaceError(f"{spec.name}: no run()")
        if spec.name in self._by_name and not replace:
            raise InterfaceError(f"{spec.name}: registered twice")
        self._by_name[spec.name] = comp
        self.refused.pop(spec.name, None)
        return comp

    def refuse(self, name: str, why: str) -> None:
        self.refused[name] = why

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def has(self, name: str) -> bool:
        return name in self._by_name

    def get(self, name: str, input_major: int | None = None, output_major: int | None = None) -> Component:
        c = self._by_name.get(name)
        if c is None:
            raise ComponentFailure(name, self.refused.get(name, "not registered"))
        if input_major is not None and major(c.spec.input.version) != input_major:
            raise ComponentFailure(name, f"input is {c.spec.input.ref}; the caller speaks v{input_major}")
        if output_major is not None and major(c.spec.output.version) != output_major:
            raise ComponentFailure(name, f"output is {c.spec.output.ref}; the caller expects v{output_major}")
        return c

    def call(self, name: str, inputs: dict, input_major: int | None = None, output_major: int | None = None) -> dict:
        """The component's validated output, or ComponentFailure (a bad input is the caller's error: InterfaceError)."""
        return self.get(name, input_major, output_major)(inputs)

    def describe(self) -> list[dict]:
        return [self._by_name[n].spec.to_json() for n in self.names()]


# ---------------------------------------------------------------------- the reasoning selector as a component

SELECTOR_INPUT = IOSpec("reasoning.question", "1.0.0", (Field("question", "str", doc="the question as asked"),))


def selector_output(actions) -> IOSpec:
    acts = tuple(actions)
    return IOSpec("reasoning.plan_scores", "1.0.0", (
        Field("scores", "dict[str,float]", min=0.0, max=1.0, choices=acts, doc="probability per plan shape"),
        Field("top", "str", choices=acts, doc="the best-scored plan shape"),
        Field("schema", "str", doc="the feature schema fingerprint the model was trained for")))


class SelectorComponent(Component):
    """The neural core's model behind the shared interface.  Scores only: the plans are still built from templates,
    validated, run and checked by the NeuralPlanner and the navigator."""

    def __init__(self, planner):
        from computer.neural.schema import fingerprint
        self.planner = planner
        self._schema = fingerprint()
        m = getattr(planner, "manifest", {}) or {}
        acts = list(getattr(planner.model, "actions", []))
        self.spec = ComponentSpec("reasoning.selector", "1.0.0", "neural", SELECTOR_INPUT, selector_output(acts),
                                  "question -> a probability for each plan shape (the plan itself is never the model's)",
                                  {"id": m.get("model_id", ""), "sha256": m.get("model_sha256", ""),
                                   "schema": m.get("schema", {}).get("fingerprint", "")})

    def run(self, inputs: dict) -> dict:
        _, sc = self.planner.scores(inputs["question"])
        if not sc:
            raise ValueError("no scores")
        top = max(sc, key=sc.get)
        return {"scores": {a: round(float(p), 6) for a, p in sc.items()}, "top": top, "schema": self._schema}
