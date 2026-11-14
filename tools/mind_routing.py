"""Score Earth Mind's routing: where each question goes, and what it answers.

    python3 tools/mind_routing.py [tests/mind_routing.jsonl] [--show]

Each line: ``text``, the right ``route`` (knowledge | reading | analysis |
diagnosis | unreadable | conversation | action), the right ``expect``
(a capability, an analysis, or "-"), what kind of information it ``asks``
for (definition | comparison | identity | measurement | ranking | diagnosis
| other), and the brief's test areas it ``covers``. A line with ``after`` is
a follow-up, routed after that sentence and scored apart.

Routes are taken with ``EarthMind.route`` on a real shell: no model, no
reading, no CDOS call. The five terminal failures are additionally driven
through the terminal itself by ``tests/test_mind_routing.py``.

**Validation** is scored independently of the code it checks, from this
table — the routing audit's §5 and its review:

    asked for      answered with                       verdict
    measurement    an identity (the model, the name)   reject
    identity       a measurement                       reject
    definition     any reading or analysis             reject
    comparison     any reading or analysis             reject
    ranking        anything but a ranking/diagnosis    reject
    a resource     a reading of a different resource   reject
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

#: What kind of information each answer is. Written here, not imported, so
#: that the check does not share a table with the code it checks.
IDENTITY = {"hardware.cpu_model", "system.hostname", "system.kernel", "system.distro",
            "hardware.enumerate", "user.identity", "user.full_name"}
MEASUREMENT = {"hardware.memory", "hardware.swap", "hardware.disk", "hardware.cpu_load",
               "hardware.cpu_load_per_core", "hardware.load_average", "hardware.temperature",
               "hardware.battery", "hardware.uptime", "hardware.processes", "hardware.net_rate",
               "hardware.cpu_count", "audio.get_volume", "display.get_brightness",
               "display.get_resolution"}
RANKING = {"top_memory", "memory_report", "top_processor", "top_resources"}
DIAGNOSIS = {"performance_report"}
SUBJECT = {"hardware.memory": "memory", "hardware.swap": "memory", "hardware.disk": "disk",
           "hardware.cpu_load": "cpu", "hardware.cpu_load_per_core": "cpu",
           "hardware.cpu_model": "cpu", "hardware.cpu_count": "cpu",
           "hardware.temperature": "temperature", "hardware.battery": "battery"}


def violation(row: dict, route: str, got: str) -> str:
    """Why the answer is the wrong *kind* of information, or ""."""
    asks = row.get("asks", "other")
    if route not in ("reading", "analysis", "diagnosis"):
        return ""
    if asks in ("definition", "comparison"):
        return f"asked for a {asks}, answered with a live {route} ({got})"
    if asks == "measurement" and got in IDENTITY:
        return f"asked for a measurement, answered with an identity ({got})"
    if asks == "identity" and got in MEASUREMENT:
        return f"asked for an identity, answered with a measurement ({got})"
    if asks == "ranking" and got not in RANKING | DIAGNOSIS:
        return f"asked for a ranking, answered with {got}"
    want, have = SUBJECT.get(row.get("expect", "")), SUBJECT.get(got)
    if want and have and want != have:
        return f"asked about {want}, answered about {have} ({got})"
    return ""


def observed(route) -> tuple:
    """(route label, answer) for an Earth Mind ``Route``."""
    from earth_os.mind import envelope as env
    system = route.system
    if system is not None:
        kind = system.kind
        if kind in ("action", "program"):
            return "action", system.capability or "-"
        return kind, (system.analysis or system.capability or "-")
    if route.control == "handoff":
        return "action", "-"
    if route.kind == env.KNOWLEDGE:
        return "knowledge", "-"
    return "conversation", "-"


def make_mind():
    from mind_helpers import dead_host, temp_config
    from earth_os.core import Shell
    config = temp_config()
    config.values["companion"]["host"] = dead_host()
    shell = Shell(config)
    shell.sound.enabled = False
    return shell, shell.earth_mind()


def measure(path: Path, mind=None):
    if mind is None:
        _shell, mind = make_mind()
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    results = []
    for row in rows:
        if row.get("after"):
            mind.route(row["after"], "terminal")
        route, got = observed(mind.route(row["text"], "terminal"))
        results.append((row, route, got, violation(row, route, got)))
    return results


def summary(results) -> dict:
    main = [r for r in results if not r[0].get("after")]
    follow = [r for r in results if r[0].get("after")]

    def tally(rows):
        return {"lines": len(rows),
                "route": sum(1 for row, route, _g, _v in rows if route == row["route"]),
                "answer": sum(1 for row, route, got, _v in rows
                              if route == row["route"] and got == row["expect"]),
                "violations": sum(1 for *_x, v in rows if v)}
    out = {"all": tally(main), "follow_ups": tally(follow)}
    for area in range(1, 11):
        rows = [r for r in main if area in r[0].get("covers", [])]
        if rows:
            out[f"area {area}"] = tally(rows)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, nargs="?", default=ROOT / "tests" / "mind_routing.jsonl")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args(argv)
    results = measure(args.path)
    s = summary(results)
    print(f"{args.path.name}: {s['all']['lines']} questions, {s['follow_ups']['lines']} follow-ups")
    print(f"  {'':12} {'route':>9} {'answer':>9} {'wrong kind':>11}")
    for name, t in s.items():
        if not t["lines"]:
            continue
        print(f"  {name:12} {t['route']:>5}/{t['lines']:<3} {t['answer']:>5}/{t['lines']:<3} "
              f"{t['violations']:>11}")
    if args.show:
        for row, route, got, why in results:
            if route != row["route"] or got != row["expect"] or why:
                tag = f"[{row['terminal']}] " if row.get("terminal") else ""
                print(f"  {tag}{row['text']!r}: wanted {row['route']}/{row['expect']}, "
                      f"got {route}/{got}" + (f" — {why}" if why else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
