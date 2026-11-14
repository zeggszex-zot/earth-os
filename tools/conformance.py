#!/usr/bin/env python3
"""Exercise every capability on this machine and report what happened.

Run this on two different machines and compare the reports.  That comparison
is the whole experiment:

    The capability answers should match.
    The mechanisms underneath should not.

Both halves matter.  Matching answers alone prove nothing if both machines ran
the same ``systemctl`` — that is one computer tested twice.  Differing
mechanisms alone prove nothing either.  It is the *combination* that supports
the claim the architecture is built on:

    The computer changed.  CDOS didn't.

What is checked, per capability:

* **Shape** — a reading that answers with a number on one machine must not
  answer with a list on another, whatever the number is.
* **Reachability** — does a sentence exist that gets here?  A capability with
  no phrasing is a thing the machine can do that nobody can ask for.
* **Verdict** — ``ok``; ``absent`` (honestly refused — the machine does not
  have the hardware); ``broken`` (should have worked and did not); ``eff``
  (effectful, never invoked); or ``pol`` (safe, but would disturb the machine
  being measured — see :data:`DISTURBS`).

``absent`` is a **pass**.  A robot has no screen and a container has no
backlight; refusing clearly is the correct behaviour and the one the audit
found four adapters getting wrong by inventing an answer instead.

Effectful capabilities are listed and never invoked.  A conformance run must
not reboot the machine it is measuring.

Usage:

    python3 tools/conformance.py                # report on this machine
    python3 tools/conformance.py --json out.json
    python3 tools/conformance.py --robot        # the stub, for comparison
    python3 tools/conformance.py --compare a.json b.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform as host_platform
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: Modules this harness needs. Nothing here imports GTK, so a test machine
#: needs python3 and these files — no packages, no desktop libraries.
NEEDED = (
    "earth_os/__init__.py",
    "earth_os/capability_corpus.py",
    "earth_os/platform/__init__.py",
    "earth_os/platform/api.py",
    "earth_os/platform/capabilities.py",
    "earth_os/platform/linux_adapter.py",
    "earth_os/platform/linux_windows.py",
    "earth_os/platform/linux_radio.py",
    "earth_os/platform/registry.py",
    "earth_os/platform/robot_stub.py",
)


def _locate_package() -> None:
    """Find ``earth_os``, or say plainly what is missing.

    This file lives at ``<repo>/tools/conformance.py`` and used to assume it
    always would, taking its own parent's parent as the repo root. Copied on
    its own to a test machine — which is the obvious thing to do with a
    single-file diagnostic, and what happened the first time — that resolves
    to the home directory and the import fails with a bare
    ``ModuleNotFoundError`` that says nothing about what to do next.

    So the package is looked for in the places it could reasonably be, and
    when it genuinely is not there the message says so and offers the way
    out: ``--bundle``, which produces a copy of this script that needs
    nothing beside it.
    """
    if any((Path(p) / "earth_os" / "__init__.py").is_file()
           for p in sys.path if p):
        return

    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent,                     # the repo, run from tools/
        here.parent,                            # beside the script
        Path.cwd(),
        Path.cwd().parent,
        *here.parents,
    ]
    env = os.environ.get("CDOS_ROOT")
    if env:
        candidates.insert(0, Path(env))

    for candidate in candidates:
        if (candidate / "earth_os" / "__init__.py").is_file():
            sys.path.insert(0, str(candidate))
            return

    sys.stderr.write(
        "\n  conformance.py cannot find the earth_os package.\n\n"
        "  It needs these files somewhere beside it:\n"
        + "".join(f"      {name}\n" for name in NEEDED)
        + "\n  Three ways to fix it:\n\n"
        "    1. Run it from inside the repository:\n"
        "         cd earth-os && python3 tools/conformance.py\n\n"
        "    2. Point at the repository:\n"
        "         CDOS_ROOT=/path/to/earth-os python3 conformance.py\n\n"
        "    3. Build a single file that needs nothing beside it — do this\n"
        "       on a machine that has the repo, then copy the one file to\n"
        "       each machine you want to measure:\n"
        "         python3 tools/conformance.py --bundle cdos-conformance.py\n\n"
        "  The third is the right one for a multi-machine run: it puts the\n"
        "  same code on every box, and prints a digest so you can prove it.\n\n")
    raise SystemExit(2)


_locate_package()

from earth_os.capability_corpus import PHRASEBOOK          # noqa: E402
from earth_os.platform import (                            # noqa: E402
    CapabilityRegistry, PlatformError, get_platform, reset_platform,
)
from earth_os.platform.capabilities import CATALOGUE       # noqa: E402

#: Arguments safe to pass to capabilities that need one.
#:
#: Read-only and harmless everywhere: a path that exists on every Unix, this
#: process's own pid, the current user. Nothing here modifies the machine.
SAFE_ARGUMENTS: Dict[str, Dict[str, object]] = {
    "filesystem.exists": {"path": "/"},
    "filesystem.list": {"path": "/"},
    "hardware.disk": {"path": "/"},
    # The cached list: asking the radio to rescan is the person's call.
    "network.scan": {"rescan": False},
}

#: Arguments that only make sense once you have asked the machine.
#:
#: The first version of this file hard-coded ``name="lo"`` and this process's
#: own pid, and then reported that the robot "disagreed" about
#: ``network.status`` — because a robot has no loopback interface. That was
#: the harness testing its own assumptions rather than the capability. An
#: argument that names a thing must come *from* the machine being measured.
DERIVED_ARGUMENTS = {
    "network.status": lambda p: (
        {"name": p.network.list_interfaces()[0].name}
        if p.network.list_interfaces() else None),
    "process.inspect": lambda p: (
        {"pid": p.process.list_processes()[0].pid}
        if p.process.list_processes() else None),
    "filesystem.read": lambda p: next(
        ({"path": path} for path in ("/etc/hostname", "/etc/unit", "/etc/os-release")
         if p.filesystem.exists(path)), None),
    "user.full_name": lambda p: {"username": p.user.current_user()},
}


#: Capabilities this harness will not invoke, though they are safe to run.
#:
#: Not the same list as *effectful*, and the difference is worth stating
#: because conflating them is a mistake I made and a test caught.
#:
#: ``effectful`` answers "should a person have to press something first?" —
#: a safety question about irreversible acts. Marking a trivially reversible
#: thing effectful would train people to confirm without reading, which is
#: worse than confirming nothing, so ``system.lock`` is correctly *not*
#: effectful: if you say lock the screen, it should lock.
#:
#: This list answers a different question: "may a measuring instrument do
#: this to the machine it is measuring?" Locking someone's screen mid-run is
#: not unsafe, it is *rude*, and it happened — a conformance run on a real
#: desktop locked its owner out before this list existed.
DISTURBS: Dict[str, str] = {
    "system.lock": "would lock the screen of the machine being measured",
    "audio.speak": "would make the machine talk",
    "audio.listen": "would open the microphone",
    "audio.set_volume": "would change what the person is listening to",
    "display.set_brightness": "would change the screen in front of them",
    "window.focus": "would take focus away from whoever is running this",
    "network.wifi_on": "would switch the wireless radio on",
    "bluetooth.on": "would switch bluetooth on",
    "bluetooth.disconnect": "would disconnect their headphones and keyboard",
    "bluetooth.scan": "would hold the radio searching for seconds",
    "display.screenshot": "would write a picture into their folders",
}


def shape_of(value: object) -> str:
    """A description of the answer's form, not its content.

    Two machines will not report the same temperature, and should not be
    expected to. They must agree on whether a temperature is a number.
    """
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, (tuple, list)):
        if not value:
            return "list[]"          # a list, just an empty one
        inner = {shape_of(item) for item in value[:8]}
        return f"list[{'|'.join(sorted(inner))}]"
    if isinstance(value, dict):
        return "mapping"
    return type(value).__name__


def run(registry: CapabilityRegistry, username: str) -> List[Dict[str, object]]:
    """Every non-effectful capability, once."""
    rows: List[Dict[str, object]] = []
    for name in sorted(CATALOGUE):
        spec = CATALOGUE[name]
        phrases = PHRASEBOOK.get(name, ())
        row: Dict[str, object] = {
            "capability": name,
            "domain": spec.domain,
            "effectful": spec.effectful,
            "phrasings": len(phrases),
            "asked_by": phrases[0] if phrases else "",
        }

        if spec.effectful:
            # Listed, never run. A conformance run must not reboot the
            # machine it is measuring.
            row.update(verdict="skipped", shape="", detail="effectful")
            rows.append(row)
            continue

        if name in DISTURBS:
            # Its own verdict, not "skipped". Sharing the effectful marker
            # would show `system.lock` as effectful in the report and undo
            # the distinction this list exists to draw.
            row.update(verdict="declined", shape="", detail=DISTURBS[name])
            rows.append(row)
            continue

        arguments = dict(SAFE_ARGUMENTS.get(name, {}))
        if name in DERIVED_ARGUMENTS:
            try:
                derived = DERIVED_ARGUMENTS[name](registry.platform)
            except Exception:
                derived = None
            if derived is None:
                row.update(verdict="unchecked", shape="",
                           detail="nothing on this machine to ask about")
                rows.append(row)
                continue
            arguments.update(derived)
        missing = [p for p in spec.params if p not in arguments]
        if missing:
            row.update(verdict="unchecked", shape="",
                       detail=f"needs {', '.join(missing)}")
            rows.append(row)
            continue

        started = time.monotonic()
        result = registry.invoke(name, **arguments)
        elapsed = (time.monotonic() - started) * 1000

        if result.ok:
            row.update(verdict="ok", shape=shape_of(result.value), detail="")
        elif _is_absence(result.error):
            row.update(verdict="absent", shape="", detail=result.error[:90])
        else:
            row.update(verdict="broken", shape="", detail=result.error[:90])
        row["ms"] = round(elapsed, 1)
        rows.append(row)
    return rows


def _is_absence(error: str) -> bool:
    """Is this "the machine does not have that", or "it went wrong"?

    The distinction is the one the whole error contract rests on. A machine
    with no backlight is not faulty, and a report that marks it broken would
    make every honest refusal look like a defect — which is the fastest way
    to teach people to ignore the report.
    """
    text = (error or "").lower()
    return any(phrase in text for phrase in (
        "not available", "no such", "this machine has no", "no backlight",
        "could not determine", "is not installed", "cannot place",
        "no lock command", "no logout command", "not supported",
    ))


def report(rows: List[Dict[str, object]], machine: Dict[str, object]) -> str:
    out: List[str] = []
    out.append("=" * 74)
    out.append(f"  CDOS CAPABILITY CONFORMANCE — {machine['adapter']}")
    out.append("=" * 74)
    out.append(f"  host      {machine['host']}")
    out.append(f"  distro    {machine['distro']}")
    out.append(f"  kernel    {machine['kernel']}")
    out.append(f"  python    {machine['python']}")
    # Which build produced this. Printed rather than buried in the JSON
    # because a report was handed back that looked current and was not — the
    # machine still had an older bundle on it, and nothing on screen said so.
    build = str(machine.get("bundle") or "")
    out.append(f"  build     {build}" if build
               else "  build     from a source tree (not a bundle)")
    out.append("")

    out.append("  MECHANISMS (these are expected to differ between machines)")
    for area, mechanism in sorted(machine.get("mechanisms", {}).items()):
        out.append(f"    {area:<12} {mechanism}")
    out.append("")

    out.append("  CAPABILITIES (these are expected to match)")
    domain = ""
    for row in rows:
        if row["domain"] != domain:
            domain = str(row["domain"])
            out.append(f"    -- {domain} " + "-" * (58 - len(domain)))
        mark = {"ok": "ok  ", "absent": "--  ", "broken": "FAIL",
                "skipped": "eff ", "declined": "pol ",
                "unchecked": "?   "}[str(row["verdict"])]
        shape = str(row.get("shape") or row.get("detail", ""))[:34]
        out.append(f"    {mark} {row['capability']:<28} {shape}")
    out.append("")

    counts: Dict[str, int] = {}
    for row in rows:
        counts[str(row["verdict"])] = counts.get(str(row["verdict"]), 0) + 1
    total = len(rows)
    out.append(f"  {total} capabilities: "
               + ", ".join(f"{n} {v}" for v, n in sorted(counts.items())))
    unreachable = [r["capability"] for r in rows if not r["phrasings"]]
    out.append(f"  reachable by words: {total - len(unreachable)}/{total}")
    broken = [r["capability"] for r in rows if r["verdict"] == "broken"]
    if broken:
        out.append(f"  BROKEN: {', '.join(map(str, broken))}")
    out.append("=" * 74)
    return "\n".join(out)


def shapes_agree(a: str, b: str) -> Tuple[bool, str]:
    """Do two answer shapes describe the same contract?

    Returns ``(agree, why)``. Two cases are compatible rather than equal, and
    both were reported as failures by the first version of this file:

    * **A list is a list.** ``list[]`` against ``list[ProcessInfo]`` is one
      machine being busier than the other, not a different answer.
    * **``none`` is within an Optional contract.** ``battery()`` is declared
      ``Optional``, so a machine with no battery answering ``None`` is the
      *hardware* differing — which is what this experiment is measuring, not
      a semantic divergence. Calling that a failure would mean the claim
      could only hold between two identical machines, which is no claim.
    """
    if a == b:
        return (True, "")
    if a.startswith("list[") and b.startswith("list["):
        return (True, "both lists")
    if "none" in (a, b):
        return (True, "optional: absent on one machine")
    return (False, "")


def compare(left: Dict, right: Dict) -> str:
    """The experiment: same answers, different mechanisms."""
    out: List[str] = []
    a, b = left["machine"], right["machine"]
    out.append("=" * 74)
    out.append("  CONFORMANCE COMPARISON")
    out.append(f"    A: {a['adapter']} — {a['distro']}")
    out.append(f"    B: {b['adapter']} — {b['distro']}")
    out.append("=" * 74)

    da, db = a.get("bundle", ""), b.get("bundle", "")
    if da and db and da != db:
        out.append("")
        out.append("  WARNING: different code.")
        out.append(f"    A ran bundle {da}, B ran bundle {db}.")
        out.append("    The experiment asks whether the *same* CDOS behaves the")
        out.append("    same on two computers. Two different builds cannot answer")
        out.append("    that, however well the answers line up below.")
    elif da and db:
        out.append(f"\n  Both machines ran bundle {da}.")
    elif da or db:
        out.append("")
        out.append("  NOTE: only one side was run from a bundle, so there is no")
        out.append("  way to check the two machines ran identical code.")

    ma, mb = a.get("mechanisms", {}), b.get("mechanisms", {})
    differing = sorted(k for k in set(ma) | set(mb)
                       if ma.get(k, "?") != mb.get(k, "?"))
    out.append(f"\n  MECHANISMS THAT DIFFER — {len(differing)} of "
               f"{len(set(ma) | set(mb))}")
    for area in differing:
        out.append(f"    {area:<12} {ma.get(area,'?'):<22} vs  {mb.get(area,'?')}")

    rows_a = {r["capability"]: r for r in left["capabilities"]}
    rows_b = {r["capability"]: r for r in right["capabilities"]}
    shared = sorted(set(rows_a) & set(rows_b))

    agree, explained, disagree = [], [], []
    for name in shared:
        ra, rb = rows_a[name], rows_b[name]
        if ra["verdict"] == rb["verdict"] == "ok":
            ok, why = shapes_agree(str(ra["shape"]), str(rb["shape"]))
            if ok and why:
                explained.append((name, str(ra["shape"]), str(rb["shape"])))
            elif ok:
                agree.append((name, str(ra["shape"]), str(rb["shape"])))
            else:
                disagree.append((name, str(ra["shape"]), str(rb["shape"])))
        elif {ra["verdict"], rb["verdict"]} <= {"ok", "absent", "skipped",
                                                "declined", "unchecked"}:
            # One machine has the hardware and the other does not. That is the
            # machine differing, which is the point, not CDOS differing.
            if ra["verdict"] != rb["verdict"]:
                explained.append((name, str(ra["verdict"]), str(rb["verdict"])))
            else:
                agree.append((name, str(ra["verdict"]), str(rb["verdict"])))
        else:
            disagree.append((name, str(ra["verdict"]), str(rb["verdict"])))

    out.append(f"\n  CAPABILITIES COMPARED — {len(shared)}")
    out.append(f"    same answer shape           {len(agree)}")
    out.append(f"    differs, hardware differs   {len(explained)}")
    out.append(f"    DISAGREES                   {len(disagree)}")

    if explained:
        out.append("\n  Present on one machine, absent on the other:")
        for name, va, vb in explained:
            out.append(f"    {name:<30} {va:<10} vs {vb}")
    if disagree:
        out.append("\n  SEMANTIC DISAGREEMENT — the claim fails here:")
        for name, va, vb in disagree:
            out.append(f"    {name:<30} {va:<16} vs {vb}")

    out.append("\n" + "=" * 74)
    if disagree:
        out.append("  RESULT: capabilities disagreed. CDOS changed with the computer.")
    elif not differing:
        out.append("  RESULT: answers matched, but so did every mechanism —")
        out.append("          this is one computer measured twice, not a proof.")
    else:
        out.append(f"  RESULT: {len(differing)} mechanisms differed; "
                   f"no capability disagreed.")
        out.append("          The computer changed. CDOS didn't.")
    out.append("=" * 74)
    return "\n".join(out)


def snapshot(robot: bool = False) -> Dict[str, object]:
    if robot:
        os.environ["CDOS_ROBOT"] = "1"
    else:
        os.environ.pop("CDOS_ROBOT", None)
    reset_platform()
    adapter = get_platform()
    registry = CapabilityRegistry(adapter)

    try:
        username = adapter.user.current_user()
    except PlatformError:
        username = ""

    machine = {
        "adapter": f"{adapter.name} {adapter.version}",
        "host": _safe(lambda: adapter.system.get_hostname()),
        "distro": _safe(lambda: adapter.system.distro()),
        "kernel": _safe(lambda: adapter.system.kernel()),
        "python": host_platform.python_version(),
        "mechanisms": adapter.mechanisms(),
        "taken": time.strftime("%Y-%m-%d %H:%M:%S"),
        # Present only when run from a bundle. Two reports carrying the same
        # digest were produced by the same code, which is the assumption the
        # whole comparison rests on and is otherwise unverifiable.
        "bundle": globals().get("BUNDLE_DIGEST", ""),
    }
    return {"machine": machine, "capabilities": run(registry, username)}


def _safe(call) -> str:
    try:
        return str(call())
    except Exception as exc:
        return f"unknown ({type(exc).__name__})"


BUNDLE_PREAMBLE = '''#!/usr/bin/env python3
"""CDOS capability conformance — self-contained.

Generated by ``tools/conformance.py --bundle``. Everything it needs is in
this one file: no installation, no packages, no GTK, no repository. Copy it
to a machine, run it with python3, and it writes a JSON report.

    python3 %(name)s --json thisbox.json
    python3 %(name)s --compare thisbox.json thatbox.json

The digest below is the point of bundling rather than cloning. The experiment
asks whether the *same* CDOS behaves the same on different computers, and two
checkouts made at different times are not obviously the same code. Both
machines running a bundle with the same digest is a thing you can check:

    CDOS BUNDLE %(digest)s

Built %(built)s from %(count)d modules.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import sys

BUNDLE_DIGEST = "%(digest)s"

_SOURCES = %(sources)s


class _BundleFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serve the embedded modules from memory.

    In memory rather than unpacked to a temporary directory because this tool
    measures a machine and should not write to it — a conformance run that
    leaves files behind is a conformance run that changed its subject.
    """

    def find_spec(self, name, path=None, target=None):
        if name not in _SOURCES:
            return None
        _source, is_package = _SOURCES[name]
        return importlib.util.spec_from_loader(name, self, is_package=is_package)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source, _is_package = _SOURCES[module.__name__]
        exec(compile(source, "<bundled %%s>" %% module.__name__, "exec"),
             module.__dict__)


sys.meta_path.insert(0, _BundleFinder())

'''


def build_bundle(destination: Path) -> Tuple[int, str]:
    """Pack this harness and the modules it needs into one runnable file."""
    import hashlib

    root = Path(__file__).resolve().parent.parent
    sources: Dict[str, Tuple[str, bool]] = {}
    digest = hashlib.sha256()

    for relative in NEEDED:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        digest.update(relative.encode())
        digest.update(text.encode())
        module = relative[:-3].replace("/", ".")
        is_package = module.endswith(".__init__")
        if is_package:
            module = module[: -len(".__init__")]
        sources[module] = (text, is_package)

    own = Path(__file__).read_text(encoding="utf-8")
    digest.update(own.encode())
    short = digest.hexdigest()[:16]

    # The harness runs after the embedded modules are importable, so its own
    # package-finding step is not wanted and is replaced by a no-op.
    # ``from __future__`` must be the first statement in a file, so it is
    # hoisted into the preamble and removed from the body.
    body = own.replace("from __future__ import annotations\n", "", 1)
    body = body.replace("_locate_package()\n", "", 1)
    body = body.replace("def _locate_package() -> None:",
                        "def _locate_package() -> None:  # not used in a bundle")

    preamble = BUNDLE_PREAMBLE % {
        "name": destination.name,
        "digest": short,
        "built": time.strftime("%Y-%m-%d"),
        "count": len(sources),
        "sources": "{\n" + "".join(
            f"    {module!r}: ({source!r}, {is_package!r}),\n"
            for module, (source, is_package) in sorted(sources.items())) + "}",
    }
    destination.write_text(preamble + body, encoding="utf-8")
    destination.chmod(0o755)
    return (len(sources), short)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", metavar="FILE", help="write the report as JSON")
    parser.add_argument("--robot", action="store_true",
                        help="measure the robot stub instead of this machine")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"),
                        help="compare two JSON reports")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--bundle", metavar="FILE",
                        help="write a self-contained copy that needs nothing "
                             "beside it, for copying to other machines")
    args = parser.parse_args(argv)

    if args.bundle:
        count, digest = build_bundle(Path(args.bundle))
        print(f"  wrote {args.bundle} — {count} modules embedded")
        print(f"  digest {digest}")
        print(f"\n  Copy it to each machine and run:")
        print(f"    python3 {Path(args.bundle).name} --json thisbox.json")
        print(f"\n  Both machines must report the same digest, or they are")
        print(f"  not running the same CDOS and the comparison means nothing.")
        return 0

    if args.compare:
        left = json.loads(Path(args.compare[0]).read_text())
        right = json.loads(Path(args.compare[1]).read_text())
        print(compare(left, right))
        rows_a = {r["capability"]: r for r in left["capabilities"]}
        rows_b = {r["capability"]: r for r in right["capabilities"]}
        for name in set(rows_a) & set(rows_b):
            a, b = rows_a[name], rows_b[name]
            if a["verdict"] == b["verdict"] == "ok" \
                    and not shapes_agree(str(a["shape"]), str(b["shape"]))[0]:
                return 1
        return 0

    data = snapshot(robot=args.robot)
    if not args.quiet:
        print(report(data["capabilities"], data["machine"]))   # type: ignore[arg-type]
    if args.json:
        Path(args.json).write_text(json.dumps(data, indent=2))
        if not args.quiet:
            print(f"\n  written to {args.json}")
    broken = [r for r in data["capabilities"]                 # type: ignore[union-attr]
              if r["verdict"] == "broken"]
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
