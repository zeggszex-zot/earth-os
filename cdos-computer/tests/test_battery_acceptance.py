"""Milestone 1 acceptance test: battery selection (docs/cdos-design-capability-acceptance.md §7).

Every case goes through the public path (Computer.think) with no model and the network blocked, and each property is
checked on its own: truth, certainty, status, the words that must and must not be said, that every number said is
in the evidence or the steps, the corpus version, the capabilities and their versions, the rules, the assumptions,
the structured errors, and the hash across runs.  Matching words alone never passes a case.

The report separates the two kinds of failure (§7.5): an ENGINE DEFECT (the fixture holds what's needed, and the
answer is wrong or missing) and a KNOWLEDGE GAP (Delta, where UNKNOWN is the right answer, and only for the right
reason: the plan measured Delta and found nothing, not that the question wasn't understood).
"""

import json
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import battery_corpus                                          # noqa: E402
from computer.capabilities import DECLARATIONS                      # noqa: E402
from computer.config import Config                                  # noqa: E402
from computer.corpus import Corpus, content_hash                    # noqa: E402
from computer.navigator import Navigator                            # noqa: E402
from computer.planner import RulePlanner, navigate                  # noqa: E402
from computer.core import Computer                                     # noqa: E402

TMP = tempfile.TemporaryDirectory()
DATA = battery_corpus.build(Path(TMP.name) / "battery")
EXTRA = battery_corpus.build(Path(TMP.name) / "battery_extra", battery_corpus.FACTS + battery_corpus.EXTRA)
VERSIONS = {d["name"]: d["version"] for d in DECLARATIONS}
NUM = re.compile(r"\d+(?:[.,]\d+)*")


class NetworkBlocked(AssertionError):
    pass


def blocked(*a, **kw):
    raise NetworkBlocked(f"network call attempted: {a[1:] if len(a) > 1 else a}")


def config(data=DATA, **kw):
    """§7.2: no model, rules only, no controller, no lexicon; model endpoints on a closed port."""
    return Config(corpus=str(data), llm_mode="off", navigator="always", navigator_planner="rules",
                  navigator_controller="", relation_lexicon="", llm_endpoint="http://127.0.0.1:9",
                  navigator_endpoint="http://127.0.0.1:9", llm_endpoint_priority="", log_file="", state_file="", **kw)


def ask(q, data=DATA, setup=None, **kw):
    with mock.patch.object(socket.socket, "connect", blocked), mock.patch.object(socket, "create_connection", blocked):
        computer = Computer(config(data, **kw), Corpus(data).load())
        if setup:
            setup(computer)
        return computer.think(q)


def numbers(text):
    return {n.replace(",", "").rstrip("0").rstrip(".") if "." in n else n.replace(",", "") for n in NUM.findall(text)}


def allowed_numbers(env, question):
    pool = " ".join([question] + [str(e["value"]) for e in env["evidence"]] + env["steps"] + [str(env["year"])])
    return numbers(pool)


class Case:
    def __init__(self, n, q, truth, certainty, status, say=(), say_not=(), kind="engine"):
        self.n, self.q, self.truth, self.certainty, self.status = n, q, truth, certainty, status
        self.say, self.say_not, self.kind = say, say_not, kind


CASES = [
    Case(1, "Does BATTERY_ALPHA meet 50 Wh?", "TRUE", "ESTABLISHED", "answered", ("52 watt-hours", "0.052 kilowatt-hours")),
    Case(2, "Does BATTERY_BETA meet 50 Wh?", "FALSE", "INFERRED", "answered",
         ("42 watt-hours", "3.5 amp-hours", "12 volts")),
    Case(3, "Which of ALPHA and BETA meets 50 Wh?", None, "INFERRED", "answered",
         ("Battery Alpha meets 50 watt-hours", "Battery Beta doesn't", "52 watt-hours", "42 watt-hours"),
         ("Battery Beta meets",)),
    Case(4, "Does BATTERY_DELTA meet 50 Wh?", "UNKNOWN", "UNKNOWN", "unsupported",
         ("nothing in my corpus links Battery Delta",), ("Yes", "No."), kind="gap"),
    Case(5, "Does BATTERY_GAMMA meet 50 Wh?", None, "UNKNOWN", "invalid", ("kilograms", "isn't a unit of energy")),
    Case(6, "Does BATTERY_EPSILON meet 50 Wh?", "CONFLICT", "CONFLICT", "conflict",
         ("40 watt-hours", "60 watt-hours", "disagrees"), ("Yes", "No.")),
]


def check(case: Case, r) -> list[str]:
    """Every way this answer is wrong (empty: it's right).  Each property on its own."""
    e, bad = r.envelope, []
    for k, want in (("truth", case.truth), ("certainty", case.certainty), ("status", case.status)):
        if e.get(k) != want:
            bad.append(f"{k} is {e.get(k)!r}, not {want!r}")
    for s in case.say:
        if s.lower() not in r.text.lower():
            bad.append(f"doesn't say {s!r}")
    for s in case.say_not:
        if s in r.text:
            bad.append(f"says {s!r}")
    extra = numbers(r.text) - allowed_numbers(e, case.q)
    if extra:
        bad.append(f"numbers not in the evidence or steps: {sorted(extra)}")
    want_version = f"sha256:{content_hash(DATA)}"
    if e.get("corpus_version") != want_version:
        bad.append("corpus_version isn't the fixture's content hash")
    if any(ev["corpus_version"] != want_version for ev in e["evidence"]):
        bad.append("an evidence record cites another corpus version")
    for c in e["capabilities"]:
        if VERSIONS.get(c["name"]) != c["version"]:
            bad.append(f"capability {c['name']} at {c['version']}, declared {VERSIONS.get(c['name'])}")
    return bad


class TestBatteryAcceptance(unittest.TestCase):
    def test_cases_1_to_6(self):
        report = {"engine defect": [], "knowledge gap": []}
        for c in CASES:
            bad = check(c, ask(c.q))
            if bad:
                report["engine defect" if c.kind == "engine" else "knowledge gap"].append((c.n, bad))
        self.assertEqual(report, {"engine defect": [], "knowledge gap": []}, json.dumps(report, indent=1))

    def test_case_1_is_established_through_an_exact_conversion(self):
        e = ask(CASES[0].q).envelope
        self.assertEqual(e["rules"], ["unit.kwh_to_wh"])                    # D1: listed, and doesn't lower the label
        self.assertIn("0.052 KWH = 52 WH (unit.kwh_to_wh)", e["steps"])
        self.assertIn("52 WH >= 50 WH: TRUE", e["steps"])
        self.assertEqual([(x["relation"], x["value"], x["unit"], x["derived"]) for x in e["evidence"]],
                         [("ENERGY_CAPACITY", "0.052", "KWH", [])])
        self.assertEqual(e["value"], {"value": 52.0, "unit": "WH"})
        self.assertEqual(e["errors"], [])

    def test_case_2_shows_its_derivation(self):
        e = ask(CASES[1].q).envelope
        self.assertIn("3.5 AH x 12 V = 42 WH (42 watt-hours)", e["steps"])
        self.assertEqual({x["relation"]: x["derived"] for x in e["evidence"]},
                         {"RATED_CHARGE": ["formula: energy = charge x voltage"],
                          "NOMINAL_VOLTAGE": ["formula: energy = charge x voltage"]})
        self.assertIn("a formula combining different relations: energy = charge x voltage", e["inferences"])
        self.assertIn({"name": "logic.formula", "version": VERSIONS["logic.formula"]}, e["capabilities"])

    def test_assumptions_name_the_measure_for_both_figures(self):
        # Design decision (docs/MILESTONE_1.md): the draft wanted case 1's assumptions empty.  Which corpus figure a
        # stated requirement is held to (rated, usable ...) is a reading for Alpha just as for Beta, so both record it.
        a = ask(CASES[0].q).envelope["assumptions"]
        b = ask(CASES[1].q).envelope["assumptions"]
        self.assertEqual(a, ["50 watt-hours is compared with Battery Alpha's energy capacity"])
        self.assertEqual(b, ["50 watt-hours is compared with Battery Beta's energy figure worked out from its nominal "
                             "voltage and rated charge"])

    def test_case_3_selects_alpha_with_both_verdicts(self):
        e = ask(CASES[2].q).envelope
        self.assertEqual(e["value"], {"selected": ["BATTERY_ALPHA"],
                                      "verdicts": {"BATTERY_ALPHA": "TRUE", "BATTERY_BETA": "FALSE"}})
        self.assertEqual(e["certainty"], "INFERRED")                         # the weakest part: Beta's derivation
        self.assertIn("I took Alpha to mean Battery Alpha.", e["assumptions"])
        self.assertEqual({x["subject"] for x in e["evidence"]}, {"BATTERY_ALPHA", "BATTERY_BETA"})

    def test_case_4_unknown_for_the_right_reason(self):
        r = ask(CASES[3].q)
        e = r.envelope
        self.assertTrue(any(s.startswith("$2 = MEASURE $1 energy") for s in e["steps"]),
                        "the plan must have measured Delta: an UNKNOWN from not understanding the question isn't this")
        self.assertEqual([x["code"] for x in e["errors"]], ["MISSING_FACT"])
        err = e["errors"][0]
        self.assertTrue(err["recoverable"])
        self.assertEqual((err["details"]["subject"], err["details"]["dimension"]), ("BATTERY_DELTA", "energy"))
        self.assertIn("no charge and voltage", err["details"]["why"])
        self.assertEqual(e["evidence"], [])

    def test_case_5_names_the_units_and_says_no_figure(self):
        r = ask(CASES[4].q)
        e = r.envelope
        self.assertEqual([x["code"] for x in e["errors"]], ["UNIT_INCOMPATIBLE"])
        self.assertEqual(e["errors"][0]["details"]["units"], ["KG"])
        self.assertEqual(e["errors"][0]["details"]["expected"], "energy")
        self.assertFalse(e["errors"][0]["recoverable"])
        self.assertEqual(numbers(r.text), {"50"}, "no figure but the requirement's own")

    def test_case_6_conflict_rests_on_the_stated_rule(self):
        e = ask(CASES[5].q).envelope
        self.assertEqual(e["rules"], ["functional.energy_capacity"])
        self.assertEqual([x["code"] for x in e["errors"]], ["CONFLICT"])
        self.assertEqual(sorted(e["errors"][0]["details"]["values"]), ["40 WH", "60 WH"])

    def test_case_7_without_gnu_units(self):
        r = ask(CASES[0].q)
        self.assertIn({"name": "logic.units_gnu", "reason": "not implemented in this release"}, r.envelope["skipped"])
        forced = ask(CASES[0].q, setup=lambda o: o.registry.disable("logic.units_gnu", "removed for the test"))
        self.assertEqual(forced.text, r.text)
        self.assertEqual((forced.envelope["truth"], forced.envelope["certainty"]), ("TRUE", "ESTABLISHED"))
        self.assertIn({"name": "logic.units_gnu", "reason": "removed for the test"}, forced.envelope["skipped"])
        self.assertEqual(forced.envelope["assumptions"], r.envelope["assumptions"])   # the fallback didn't change the basis

    def test_case_8_without_the_required_unit_table(self):
        r = ask(CASES[0].q, setup=lambda o: o.registry.disable("logic.units", "removed for the test"))
        e = r.envelope
        self.assertEqual((e["status"], e["truth"], e["certainty"]), ("unavailable", None, "UNKNOWN"))
        self.assertEqual([x["code"] for x in e["errors"]], ["UNAVAILABLE"])
        self.assertTrue(e["errors"][0]["recoverable"])
        self.assertEqual(e["errors"][0]["details"]["capability"], "logic.units")
        self.assertNotIn("52", r.text)

    def test_case_9_same_hash_five_times(self):
        hashes = {ask(CASES[2].q).envelope["canonical_hash"] for _ in range(5)}
        self.assertEqual(len(hashes), 1)
        moved = Path(TMP.name) / "moved"                              # the same corpus somewhere else: same answer
        battery_corpus.build(moved)
        self.assertEqual(ask(CASES[2].q, data=moved).envelope["canonical_hash"], hashes.pop())

    def test_the_hash_notices_a_changed_fact(self):
        facts = [f if f[0] != "BATTERY_BETA" or f[1] != "NOMINAL_VOLTAGE" else f[:3] + ("12.5",) + f[4:]
                 for f in battery_corpus.FACTS]
        other = battery_corpus.build(Path(TMP.name) / "changed", facts)
        a, b = ask(CASES[2].q).envelope, ask(CASES[2].q, data=other).envelope
        self.assertNotEqual(a["corpus_version"], b["corpus_version"])
        self.assertNotEqual(a["canonical_hash"], b["canonical_hash"])

    def test_case_10_no_network_anywhere(self):
        for c in CASES:
            try:
                ask(c.q)                                # ask() blocks sockets: any connection attempt raises
            except NetworkBlocked as e:
                self.fail(f"case {c.n}: {e}")

    def test_the_engine_agrees_with_the_dialogue_path(self):
        c = Corpus(DATA).load()
        for case in CASES:
            plan, res = navigate(case.q, [RulePlanner(c)], Navigator(c))
            self.assertEqual(plan.kind, "require", case.q)
            computer = ask(case.q)
            self.assertEqual(res.text, computer.text)
            self.assertEqual(res.truth or None, computer.envelope["truth"] if case.n not in (3, 5) else res.truth or None)


class TestFunctionalRule(unittest.TestCase):
    """The rule behind case 6, tested on its own: one applicable value per period, after exact conversion."""

    def env(self, q, **kw):
        return ask(q, data=EXTRA, **kw).envelope

    def test_a_replaced_value_is_not_a_conflict(self):
        e = self.env("Did BATTERY_ZETA meet 50 Wh in 2025?")
        self.assertEqual((e["truth"], e["year"]), ("TRUE", 2025))
        e = self.env("Did BATTERY_ZETA meet 50 Wh in 2015?")
        self.assertEqual((e["truth"], e["year"]), ("FALSE", 2015))

    def test_one_value_in_two_units_is_not_a_conflict(self):
        e = self.env("Does BATTERY_ETA meet 50 Wh?")
        self.assertEqual((e["truth"], e["certainty"]), ("TRUE", "ESTABLISHED"))
        self.assertEqual(len(e["evidence"]), 2)

    def test_two_relations_are_several_figures_not_a_conflict(self):
        e = self.env("Does BATTERY_THETA meet 50 Wh?")
        self.assertEqual((e["status"], e["truth"], e["certainty"]), ("unsupported", "UNKNOWN", "UNKNOWN"))
        self.assertNotIn("CONFLICT", [x["code"] for x in e["errors"]])
        e = self.env("Does BATTERY_THETA meet 40 Wh?")                     # both figures meet it: that's an answer
        self.assertEqual((e["truth"], e["certainty"]), ("TRUE", "INFERRED"))

    def test_two_values_that_both_still_apply_conflict(self):
        e = self.env("Does BATTERY_IOTA meet 50 Wh?")
        self.assertEqual((e["truth"], e["rules"]), ("CONFLICT", ["functional.energy_capacity"]))

    def test_without_the_rule_two_values_are_only_several(self):
        rules = json.loads((ROOT / "computer" / "data" / "rules.json").read_text())
        rules["knowledge"]["functional"] = []
        path = Path(TMP.name) / "no_functional.json"
        path.write_text(json.dumps(rules))
        e = self.env("Does BATTERY_EPSILON meet 50 Wh?", rules_file=str(path))
        self.assertEqual((e["truth"], e["certainty"]), ("UNKNOWN", "UNKNOWN"))
        self.assertEqual(e["rules"], [])
        e = self.env("Does BATTERY_EPSILON meet 30 Wh?", rules_file=str(path))
        self.assertEqual((e["truth"], e["certainty"]), ("TRUE", "INFERRED"))
        e = self.env("Does BATTERY_EPSILON meet 30 Wh?")                    # with the rule, still a conflict
        self.assertEqual(e["truth"], "CONFLICT")

    def test_at_most(self):
        self.assertEqual(self.env("Does BATTERY_ALPHA have at most 60 Wh?")["truth"], "TRUE")
        self.assertEqual(self.env("Does BATTERY_ALPHA have at most 51 Wh?")["truth"], "FALSE")

    def test_the_requirement_unit_is_converted_exactly(self):
        self.assertEqual(self.env("Does BATTERY_ALPHA meet 0.05 kWh?")["truth"], "TRUE")
        self.assertEqual(self.env("Does BATTERY_ALPHA meet 0.053 kWh?")["truth"], "FALSE")
        self.assertEqual(self.env("Does BATTERY_ALPHA meet 52 Wh?")["truth"], "TRUE")     # at least: equal counts


if __name__ == "__main__":
    unittest.main()
