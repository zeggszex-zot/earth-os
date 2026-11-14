"""Capability plugins: a new capability, added by dropping a folder in plugins_dir.  No file of the reasoning engine
(core, navigator, planner, plan, corpus, controller) changes.

    <plugins_dir>/<folder>/
        plugin.json     {"format": "cdos-plugin/1", "name", "version", "entry": "plugin.py:ClassName",
                         "capability": {a declaration, as in computer/capabilities.py DECLARATIONS, without name and
                                        version; "modules" are file names in this folder},
                         "examples": [{"input": {...}, "output": {...}}, ...],     the plugin's own self-test
                         "source": "where its numbers come from (a datasheet, a standard)"}
        plugin.py       a computer.neural.components.Component whose spec has the same name and version

What a plugin has to get through before anything uses it (any failure: the plugin is refused, with the reason, and
the computer starts without it; `python3 -m computer --check` lists both):

  1. plugin.json is well formed; the name is new (not a built-in capability, not another plugin's)
  2. its capability declaration passes every check computer/capabilities.py applies to the built-in ones: fields and
     types, error codes, fallbacks, writes_state (plugins may not write: they aren't on the writer list) and, for a
     deterministic one, the source scan for the clock, random numbers and the network.  The scan runs *before* the
     code is imported.
  3. the entry class is a Component, and its spec names the same capability and version
  4. every example in plugin.json gives exactly its output, through the component's input and output checks

Plugins are trusted local code, like compute_ccpu_driver: the scan catches accidents (a deterministic capability that
reads the clock), not someone who means harm.  Only an administrator should be able to write to plugins_dir.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PLUGIN_FORMAT = "cdos-plugin/1"
MAX_PLUGIN_JSON = 256 * 1024
FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}\.py$")


@dataclass
class Plugin:
    folder: Path
    name: str = ""
    version: str = ""
    declaration: dict = field(default_factory=dict)
    component: object = None
    source: str = ""
    error: str = ""


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def load_one(folder: Path, taken: set) -> Plugin:
    """One plugin folder -> a checked Plugin, or one with .error set.  Never raises."""
    from computer.capabilities import DECLARATIONS, CapabilityError, Registry
    from computer.neural.components import Component, InterfaceError
    pl = Plugin(folder)
    try:
        p = folder / "plugin.json"
        if not p.is_file() or p.stat().st_size > MAX_PLUGIN_JSON:
            raise ValueError("no plugin.json (or it is too large)")
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or d.get("format") != PLUGIN_FORMAT:
            raise ValueError(f"plugin.json format must be {PLUGIN_FORMAT!r}")
        pl.name, pl.version, pl.source = str(d.get("name", "")), str(d.get("version", "")), str(d.get("source", ""))
        if pl.name in taken:
            raise ValueError(f"{pl.name!r} is already a capability")
        decl = copy.deepcopy(d.get("capability"))
        if not isinstance(decl, dict):
            raise ValueError("no capability declaration")
        mods = decl.get("modules", [])
        if not isinstance(mods, list) or not all(isinstance(m, str) and FILE.match(m) for m in mods):
            raise ValueError("capability modules are .py file names in the plugin's folder")
        decl.update(name=pl.name, version=pl.version, modules=[str((folder / m).resolve()) for m in mods])
        entry = str(d.get("entry", ""))
        m = re.fullmatch(r"([A-Za-z0-9_][A-Za-z0-9_.\-]*\.py):([A-Za-z_][A-Za-z0-9_]*)", entry)
        if not m or m.group(1) not in mods:
            raise ValueError("entry is <one of its modules>.py:<ClassName>")
        try:   # the same checks as the built-in declarations, determinism scan included, before any import
            reg = Registry(DECLARATIONS + [decl]).validate()
        except CapabilityError as e:
            raise ValueError(f"declaration refused: {e}") from None
        from computer.capabilities import unreviewed_imports
        bad = [b for b in unreviewed_imports(reg) if b.startswith(f"{pl.name}:")]
        if bad:   # a deterministic plugin may import only checked or reviewed parts of computer/
            raise ValueError(f"imports that aren't checked or reviewed: {'; '.join(bad)}")
        modname = f"cdos_plugin_{re.sub(r'[^a-z0-9_]', '_', pl.name)}"
        spec = importlib.util.spec_from_file_location(modname, folder / m.group(1))
        if spec is None or spec.loader is None:
            raise ValueError(f"can't import {m.group(1)}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception as e:  # noqa: BLE001 - the plugin's own code failed to load
            sys.modules.pop(modname, None)
            raise ValueError(f"{m.group(1)} failed to import: {type(e).__name__}: {e}") from None
        cls = getattr(mod, m.group(2), None)
        if not isinstance(cls, type) or not issubclass(cls, Component):
            raise ValueError(f"{m.group(2)} isn't a Component")
        comp = cls()
        if comp.spec.name != pl.name or comp.spec.version != pl.version:
            raise ValueError(f"the component says {comp.spec.ref}, plugin.json says {pl.name}@{pl.version}")
        examples = d.get("examples", [])
        if not isinstance(examples, list) or not examples:
            raise ValueError("a plugin needs at least one example (its self-test)")
        for i, ex in enumerate(examples):
            try:
                got = comp(ex["input"])
            except (InterfaceError, KeyError, TypeError) as e:
                raise ValueError(f"example {i + 1}: {e}") from None
            if not _same(got, ex["output"]):
                raise ValueError(f"example {i + 1}: expected {ex['output']}, got {got}")
        pl.declaration, pl.component = decl, comp
    except Exception as e:  # noqa: BLE001 - a bad plugin is refused, never fatal
        pl.error = str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"
        pl.component = None
    return pl


def load_plugins(plugins_dir) -> list[Plugin]:
    """Every plugin folder, in name order; each one checked against the built-in capabilities and the ones before it."""
    from computer.capabilities import DECLARATIONS
    folder = Path(plugins_dir)
    if not folder.is_dir():
        return [Plugin(folder, error=f"no plugins folder at {folder}")]
    taken = {d["name"] for d in DECLARATIONS}
    out = []
    for sub in sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        pl = load_one(sub, taken)
        if not pl.error:
            taken.add(pl.name)
        out.append(pl)
    return out
