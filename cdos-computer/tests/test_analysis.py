"""The analysis engine, CALC over corpus numbers, diagnostics, working state and fact ids.

Uses the test corpus plus its DEVICES (tests/mini_corpus.py), so it runs without data/.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "tools"))

import mini_corpus                                                    # noqa: E402
from computer import state                                                 # noqa: E402
from computer.analysis import DERIVED, ESTIMATE, Analyst                   # noqa: E402
from computer.config import Config                                         # noqa: E402
from computer.corpus import Corpus, Fact                                   # noqa: E402
from computer.diagnostics import Diagnostics, wants                        # noqa: E402
from computer.navigator import Navigator                                   # noqa: E402
from computer.phrase import sentence                                       # noqa: E402
from computer.plan import GRAMMAR, PlanError, parse                        # noqa: E402
from computer.planner import RulePlanner, navigate                         # noqa: E402
from computer.proposer import check                                        # noqa: E402
from computer.quantity import fmt_hours, fmt_num, parse as qparse, spell_out, words_to_number  # noqa: E402
from computer.router import classify                                       # noqa: E402
from computer.core import Computer                                            # noqa: E402

TMP = tempfile.TemporaryDirectory()
DATA = mini_corpus.build(Path(TMP.name) / "data", mini_corpus.F + mini_corpus.DEVICES)


def corpus():
    return Corpus(DATA).load()


def computer(**kw):
    cfg = Config(corpus=str(DATA), llm_endpoint="", llm_mode="off", log_file="", **kw)
    return Computer(cfg, corpus())


def talk(o, *turns):
    return [o.think(t).text for t in turns]


class TestQuantities(unittest.TestCase):
    def test_units_and_dimensions(self):
        got = [(q.dim, round(q.value, 6)) for q in qparse("a 10,000 mAh battery at 3.7 volts and a 2 kW heater")]
        self.assertEqual(got, [("charge", 10.0), ("voltage", 3.7), ("power", 2000.0)])
        self.assertEqual([q.dim for q in qparse("5 V, 2 A, 500 mA, 1 kWh, 3600 J, 90%")],
                         ["voltage", "current", "current", "energy", "energy", "fraction"])
        self.assertAlmostEqual(qparse("3600 J")[0].value, 1.0)

    def test_case_where_it_matters(self):
        self.assertAlmostEqual(qparse("5 mW")[0].value, 0.005)
        self.assertAlmostEqual(qparse("5 MW")[0].value, 5e6)
        self.assertEqual(qparse("2 a day"), [], "a lower-case 'a' is an article, not amps")
        self.assertEqual(qparse("2 A")[0].dim, "current")

    def test_schedule_versus_duration(self):
        self.assertTrue(qparse("24 hours a day")[0].per_day)
        self.assertTrue(qparse("3 h per day")[0].per_day)
        self.assertFalse(qparse("for 5 hours")[0].per_day)

    def test_a_bare_number_is_not_a_quantity(self):
        self.assertEqual(qparse("50"), [])
        self.assertEqual(qparse("what is 15 times 7"), [])

    def test_number_words_only_before_units(self):
        self.assertEqual(words_to_number("ten thousand"), 10000)
        self.assertAlmostEqual(words_to_number("three point seven"), 3.7)
        self.assertEqual(words_to_number("two and a half"), 2.5)
        self.assertEqual(words_to_number("one hundred and twenty"), 120)
        self.assertEqual(spell_out("fifty watt hours"), "50 watt hours")
        self.assertEqual(spell_out("another one more"), "another one more")

    def test_formatting(self):
        self.assertEqual(fmt_hours(8.5), "8.5 hours")
        self.assertEqual(fmt_hours(17.0), "17 hours")
        self.assertEqual(fmt_hours(0.2125), "13 minutes")
        self.assertEqual(fmt_hours(170), "170 hours (about 7.1 days)")
        self.assertEqual(fmt_num(152000000), "152,000,000")
        self.assertEqual(fmt_num(3.3398), "3.34")
        self.assertEqual(fmt_num(109.297), "109")


class TestRuntime(unittest.TestCase):
    def test_stated_inputs_and_the_assumption_is_said(self):
        a = Analyst(corpus())
        r = a.handle("How long can a 50 Wh battery run a 5 W device?")
        self.assertEqual(r.kind, "answer")
        self.assertIn("8.5 hours", r.text)
        self.assertIn("assuming 85% conversion efficiency", r.text)
        self.assertEqual(r.finding.status, ESTIMATE)
        self.assertEqual(r.finding.inputs["efficiency"]["source"], "assumed")

    def test_no_assumption_means_derived(self):
        r = Analyst(corpus()).handle("How long can a 100 Wh battery run a 10 W load at 90% efficiency?")
        self.assertIn("9 hours", r.text)
        self.assertEqual(r.finding.status, DERIVED)
        self.assertEqual(r.finding.assumptions, [])

    def test_charge_times_voltage_and_shared_voltage_is_said(self):
        r = Analyst(corpus()).handle("How long will a 12 V 20 Ah battery run a 1.2 A load?")
        self.assertIn("14.2 hours", r.text)
        self.assertIn("taken to run at the battery's 12 volts", r.text)
        self.assertEqual(r.finding.status, ESTIMATE)

    def test_each_charge_takes_the_nearest_voltage(self):
        r = Analyst(corpus()).handle("How long will a 1 A load at 5 V run from a 12 V 20 Ah battery?")
        self.assertIn("240 watt-hours", r.text)
        self.assertIn("5 watts (1 amp at 5 volts)", r.text)

    def test_the_configured_efficiency_is_used(self):
        r = Analyst(corpus(), efficiency=0.9).handle("How long can a 50 Wh battery run a 5 W device?")
        self.assertIn("9 hours", r.text)
        self.assertIn("assuming 90%", r.text)

    def test_a_device_name_is_not_read_as_a_quantity(self):
        r = Analyst(corpus()).handle("How long can a 20 Wh battery run a Raspberry Pi Zero 2 W?")
        self.assertIn("6.8 hours", r.text)
        self.assertIn("Raspberry Pi Zero 2 W's 2.5 watts", r.text)

    def test_corpus_figures_are_cited_by_fact_id(self):
        c = corpus()
        r = Analyst(c).handle("How long can the owl battery run a Raspberry Pi 5?")
        self.assertIn("2.6 hours", r.text)
        ids = {f.id for k in ("OWL_BATTERY", "RASPBERRY_PI_5") for f in c.facts_for(k) if f.kind == "N"}
        self.assertTrue(r.finding.facts)
        self.assertTrue(set(r.finding.facts) <= ids)
        self.assertEqual(len(r.finding.facts), 3)        # capacity, voltage, power draw

    def test_two_corpus_figures_are_put_to_the_person(self):
        a = Analyst(corpus())
        r = a.handle("How long can a 50 Wh battery run a Raspberry Pi 4?")
        self.assertEqual(r.kind, "clarify")
        self.assertIn("3 watts (idle)", r.text)
        self.assertIn("6.4 watts (load)", r.text)
        self.assertEqual(len(r.finding.conflicts), 2)
        self.assertIn("6.6 hours", a.handle("under load").text)
        a.handle("How long can a 50 Wh battery run a Raspberry Pi 4?")
        self.assertIn("14.2 hours", a.handle("the first one").text)
        a.handle("How long can a 50 Wh battery run a Raspberry Pi 4?")
        self.assertIn("6.6 hours", a.handle("6.4 watts").text)

    def test_two_stated_figures_are_asked_not_picked(self):
        a = Analyst(corpus())
        r = a.handle("How long can a 50 Wh battery run a 5 W and a 3 W device?")
        self.assertEqual(r.kind, "clarify")
        self.assertIn("5 watts and 3 watts", r.text)
        self.assertIn("5.3 hours", a.handle("both").text)
        a.handle("How long can a 50 Wh battery run a 5 W and a 3 W device?")
        self.assertIn("14.2 hours", a.handle("the second").text)

    def test_impossible_inputs_are_refused(self):
        a = Analyst(corpus())
        self.assertEqual(a.handle("How long can a 50 Wh battery run a 0 W device?").kind, "refuse")
        self.assertIn("at most 100%", a.handle("How long can a 50 Wh battery run a 5 W device at 150% efficiency?").text)
        self.assertIn("only 24 hours", a.handle("What is the daily energy use of a 30 W device running 30 hours a day?").text)


class TestClarification(unittest.TestCase):
    def test_one_input_at_a_time(self):
        a = Analyst(corpus())
        r = a.handle("Calculate the runtime.")
        self.assertEqual((r.kind, r.text), ("clarify", "Specify the battery's usable energy capacity."))
        self.assertIn("Specify the load's power draw", a.handle("50 watt-hours").text)
        self.assertIn("8.5 hours", a.handle("5 watts").text)

    def test_the_known_part_is_said_with_the_question(self):
        a = Analyst(corpus())
        r = a.handle("Computer, estimate how long the battery can run the Raspberry Pi Zero 2 W.")
        self.assertEqual(r.kind, "clarify")
        self.assertIn("2.5 watts", r.text)
        self.assertIn("battery's usable energy capacity", r.text)
        self.assertIn("6.8 hours", a.handle("It's a 20 watt-hour battery.").text)

    def test_missing_voltage(self):
        a = Analyst(corpus())
        self.assertIn("battery's voltage", a.handle("How long will a 10000 mAh power bank run a 2 W load?").text)
        self.assertIn("15.7 hours", a.handle("3.7 volts").text)

    def test_a_number_without_a_unit_is_asked_again(self):
        a = Analyst(corpus())
        a.handle("Calculate the runtime.")
        r = a.handle("50")
        self.assertEqual(r.kind, "clarify")
        self.assertIn("with its unit", r.text)

    def test_another_question_drops_the_open_one(self):
        o = computer()
        said = talk(o, "Calculate the runtime.", "What is the capital of France?", "5 watts")
        self.assertIn("Paris", said[1])
        self.assertNotIn("hours", said[2])                  # "5 watts" alone doesn't revive the dropped question

    def test_daily_energy_dialogue(self):
        a = Analyst(corpus())
        self.assertIn("power draw", a.handle("Computer, estimate daily energy consumption.").text)
        self.assertIn("hours per day", a.handle("30 watts").text)
        self.assertIn("720 watt-hours", a.handle("24 hours a day").text)


class TestFollowUps(unittest.TestCase):
    def test_what_ifs_change_one_variable_and_say_so(self):
        o = computer()
        said = talk(o, "How long can a 50 Wh battery run a 5 W device?", "And at half the load?",
                    "What about with a 100 Wh battery?", "Assume no conversion losses.")
        self.assertTrue(said[1].startswith("With the load at 2.5 watts"))
        self.assertIn("17 hours", said[1])
        self.assertIn("34 hours", said[2])
        self.assertIn("40 hours", said[3])
        self.assertIn("no conversion losses", said[3])

    def test_scaling_words(self):
        a = Analyst(corpus())
        a.handle("How long can a 50 Wh battery run a 5 W device?")
        self.assertIn("2.8 hours", a.handle("What happens if the load triples?").text)
        self.assertIn("5.7 hours", a.handle("and if the battery doubles?").text)

    def test_daily_follow_ups(self):
        o = computer()
        said = talk(o, "How much energy does a 30 W device use per day if it runs 24 hours a day?",
                    "How about at half the load?", "And if it only runs 8 hours a day?")
        self.assertEqual([("720" in said[0]), ("360" in said[1]), ("120" in said[2])], [True, True, True])

    def test_maths_after_an_estimate_is_not_a_what_if(self):
        o = computer()
        talk(o, "How long can a 50 Wh battery run a 5 W device?")
        r = o.think("and what is 15% of 80")
        self.assertNotIn("runtime", r.text)
        self.assertNotEqual(r.route.intent, "analysis")
        self.assertEqual(o.think("What is 15% of 80?").route.intent, "calc")

    def test_other_follow_ups_still_reach_the_navigator(self):
        o = computer()
        said = talk(o, "How long can a 50 Wh battery run a 5 W device?", "What's the population of the capital of France?",
                    "what about germany")
        self.assertIn("3,800,000", said[2])

    def test_explain_gives_every_input_and_where_it_came_from(self):
        o = computer()
        talk(o, "How long can the owl battery run a Raspberry Pi 5?")
        r = o.think("How did you work that out?")
        for s in ("37 watt-hours", "12 watts", "85%", "from my corpus", "assumed", "estimate"):
            self.assertIn(s, r.text)
        self.assertEqual(o.think("why?").route.intent, "analysis")


class TestNotAnalysis(unittest.TestCase):
    def test_questions_it_must_leave_alone(self):
        a = Analyst(corpus())
        for q in ("how long did the roman empire last", "how long will the meeting last", "how long can a cheetah run",
                  "how long is the blue whale", "what is the power of the sun", "how much power does the raspberry pi 5 use",
                  "what is 15% of 80", "should I buy a bigger battery?", "what does a whale eat"):
            self.assertIsNone(a.handle(q), q)

    def test_sensitive_questions_skip_it(self):
        r = computer().think("how long can I take 500 mg of ibuprofen")
        self.assertNotEqual(r.route.intent, "analysis")

    def test_off_switch(self):
        r = computer(analysis=False).think("How long can a 50 Wh battery run a 5 W device?")
        self.assertNotEqual(r.route.intent, "analysis")


class TestCalcPlans(unittest.TestCase):
    def test_validator(self):
        p = parse("$1 = FIND jupiter\n$2 = FIND saturn\n$3 = GET $1 ~heavy\n$4 = GET $2 ~heavy\n"
                  "$5 = CALC RATIO $3 $4 heavier\nANSWER $5")
        self.assertEqual(p.steps[4].args, ["RATIO", "$3", "$4"])
        self.assertEqual(p.steps[4].text, "heavier")
        for bad in ("$1 = FIND a\n$2 = CALC SUM $1 $1\nANSWER $2",                      # not values
                    "$1 = FIND a\n$2 = GET $1 X\n$3 = CALC DIFF $2\nANSWER $3",           # one part
                    "$1 = FIND a\n$2 = GET $1 X\n$3 = CALC POW $2 $2\nANSWER $3",         # no such op
                    "$1 = FIND a\n$2 = GET $1 X\n$3 = CALC SUM $2 $2 $2 $2\nANSWER $3"):  # too many
            with self.assertRaises(PlanError):
                parse(bad)
        self.assertIn("calc", GRAMMAR)

    def run_q(self, q):
        c = corpus()
        return navigate(q, [RulePlanner(c)], Navigator(c))

    def test_ratio_sum_diff(self):
        p, r = self.run_q("how many times heavier is jupiter than saturn")
        self.assertEqual((p.kind, r.status), ("calc", "answered"))
        self.assertIn("3.34 times", r.text)
        self.assertEqual(r.final, {"kind": "calc", "values": ["3.34"], "op": "RATIO", "derived": True})
        _, r = self.run_q("what is the total population of france, germany and spain")
        self.assertIn("200,000,000", r.text)
        _, r = self.run_q("how much taller is the eiffel tower than the brandenburg gate")
        self.assertIn("304 metres taller", r.text)
        _, r = self.run_q("how much taller is the brandenburg gate than the eiffel tower")
        self.assertIn("isn't taller", r.text)

    def test_derivation_is_recorded_with_its_facts(self):
        c = corpus()
        _, r = navigate("what is the combined population of france and germany", [RulePlanner(c)], Navigator(c))
        d = r.derived[0]
        self.assertEqual(d["op"], "SUM")
        self.assertEqual(d["result"], 152000000)
        pops = {f.id for k in ("FRANCE", "GERMANY") for f in c.facts_for(k) if f.relation == "POPULATION"}
        self.assertTrue(set(d["facts"]) <= pops and len(d["facts"]) == 2)
        self.assertEqual(len(r.evidence), 2)

    def test_units_are_converted_or_refused(self):
        n = Navigator(corpus())
        r = n.run("$1 = FIND eiffel tower\n$2 = FIND earth\n$3 = GET $1 ~height\n$4 = GET $2 ~radius\n"
                  "$5 = CALC DIFF $3 $4\nANSWER $5")
        self.assertEqual(r.status, "unsupported")         # height and radius: not the same measurement
        km = [Fact("ALPHA_ROD", "LENGTH", "N", "1", "KM"), Fact("BETA_ROD", "LENGTH", "N", "250", "M")]
        mini = mini_corpus.F + [(f.subject, f.relation, f.kind, f.value, f.unit, None, None, "MANUAL", 1.0, "")
                                for f in km]
        d2 = mini_corpus.build(Path(TMP.name) / "units", mini)
        r = Navigator(Corpus(d2).load()).run("$1 = FIND alpha rod\n$2 = FIND beta rod\n$3 = GET $1 LENGTH\n$4 = GET $2 LENGTH\n"
                                             "$5 = CALC SUM $3 $4\nANSWER $5")
        self.assertIn("1.25 kilometres", r.text)

    def test_gaps_are_admitted_and_not_papered_over(self):
        for q, missing in (("what is the population density of france", "area"),
                           ("what is the combined population of france and madrid", "Madrid"),
                           ("how many times heavier is jupiter than the eiffel tower", "Eiffel Tower")):
            p, r = self.run_q(q)
            self.assertEqual((p.kind, r.status), ("calc", "unsupported"), q)
            self.assertIn(missing, r.text)
        o = computer()
        self.assertNotIn("68,000,000", o.think("What is the population density of France?").text)

    def test_time_is_respected(self):
        c = corpus()
        _, r = navigate("what was the combined population of france and germany in 2015", [RulePlanner(c)], Navigator(c))
        self.assertEqual(r.status, "unsupported")         # Germany's figure is from 2023
        self.assertIn("Germany", r.text)

    def test_a_second_attribute_is_never_replaced_by_the_first(self):
        _, r = self.run_q("what is the ratio of the mass of jupiter to the radius of saturn")
        self.assertEqual(r.status, "unsupported")

    def test_proposer_holds_a_calc_sentence_to_the_result(self):
        _, r = self.run_q("how many times heavier is jupiter than saturn")
        self.assertEqual(check("Jupiter is 3.34 times as heavy as Saturn.", r), "")
        self.assertIn("doesn't give the answer", check("Jupiter is much heavier than Saturn.", r))


class TestDiagnostics(unittest.TestCase):
    def fake(self, temp="54321", battery=None, mem=True):
        root = Path(tempfile.mkdtemp())
        (root / "sys/class/thermal/thermal_zone0").mkdir(parents=True)
        if temp is not None:
            (root / "sys/class/thermal/thermal_zone0/temp").write_text(temp)
        if battery is not None:
            b = root / "sys/class/power_supply/BAT0"
            b.mkdir(parents=True)
            (b / "type").write_text("Battery")
            (b / "capacity").write_text(str(battery))
        (root / "proc").mkdir()
        if mem:
            (root / "proc/meminfo").write_text("MemTotal:  524288 kB\nMemFree: 100000 kB\nMemAvailable:  317440 kB\n")
        (root / "proc/uptime").write_text("93784.5 1000.0\n")
        return Diagnostics(str(root / "sys"), str(root / "proc"), corpus=corpus())

    def test_readings_are_read_not_guessed(self):
        d = self.fake()
        self.assertEqual(d.answer("what is the processor temperature")[0], "The processor temperature is 54 degrees Celsius.")
        self.assertIn("310 of 512 megabytes", d.answer("how much memory is free")[0])
        self.assertIn("1 day and 2 hours", d.answer("what's the uptime")[0])
        self.assertIn("185 facts", d.answer("corpus status")[0])
        self.assertIn("76 percent", self.fake(battery=76).answer("what is the battery level")[0])

    def test_missing_sensors_are_said_to_be_missing(self):
        d = self.fake(temp=None, mem=False)
        self.assertIn("not available", d.answer("what is the cpu temperature")[0])
        self.assertIn("not available", d.answer("what is the battery level")[0])
        self.assertIn("not available", d.answer("how much memory is free")[0])

    def test_routing(self):
        for q in ("Computer, what is the processor temperature?", "system status", "how hot are you", "run a self-test"):
            self.assertEqual(classify(q).intent, "status", q)
        for q in ("what is the core temperature of the sun", "what is a ram used for", "what is the solar system",
                  "how much memory do elephants have", "how hot is the sun"):
            self.assertNotEqual(classify(q).intent, "status", q)
        self.assertEqual(wants("system status")[:2], ["temperature", "load"])

    def test_through_the_computer(self):
        r = computer().think("system status")
        self.assertEqual(r.faculty, "logic")
        self.assertIn("corpus holds 185 facts", r.text)


class TestState(unittest.TestCase):
    def test_atomic_save_and_load(self):
        p = Path(tempfile.mkdtemp()) / "s.json"
        self.assertTrue(state.save(p, {"analysis": {"x": 1}}))
        self.assertEqual(state.load(p)["analysis"], {"x": 1})
        self.assertEqual([x.name for x in p.parent.iterdir()], ["s.json"])      # no temporary file left behind

    def test_unusable_files_are_ignored(self):
        p = Path(tempfile.mkdtemp()) / "s.json"
        p.write_text("{half a fi")
        self.assertIsNone(state.load(p))
        p.write_text(json.dumps({"version": 99}))
        self.assertIsNone(state.load(p))
        state.save(p, {"analysis": {}})
        d = json.loads(p.read_text())
        d["saved"] = time.time() - 7200
        p.write_text(json.dumps(d))
        self.assertIsNone(state.load(p, max_age=3600))

    def test_a_follow_up_survives_a_restart(self):
        p = Path(tempfile.mkdtemp()) / "computer.state.json"
        o = computer(state_file=str(p))
        o.think("How long can a 50 Wh battery run a 5 W device?")
        self.assertTrue(p.exists())
        again = computer(state_file=str(p))                      # a new process, same file
        self.assertIn("17 hours", again.think("And at half the load?").text)

    def test_an_open_clarification_survives_a_restart(self):
        p = Path(tempfile.mkdtemp()) / "computer.state.json"
        computer(state_file=str(p)).think("Calculate the runtime.")
        self.assertIn("load's power draw", computer(state_file=str(p)).think("50 watt-hours").text)

    def test_writes_only_when_it_changed(self):
        p = Path(tempfile.mkdtemp()) / "computer.state.json"
        o = computer(state_file=str(p))
        o.think("How long can a 50 Wh battery run a 5 W device?")
        t = p.stat().st_mtime_ns
        o.think("hello")                                    # the follow-up window closes: one write
        t2 = p.stat().st_mtime_ns
        o.think("what is 12 times 7")                       # nothing changed: no write
        self.assertEqual(p.stat().st_mtime_ns, t2)
        self.assertGreaterEqual(t2, t)

    def test_a_corrupt_saved_frame_doesnt_stop_the_computer(self):
        p = Path(tempfile.mkdtemp()) / "computer.state.json"
        state.save(p, {"analysis": {"frame": {"formula": "warp_drive"}, "live": True}})
        o = computer(state_file=str(p))
        self.assertIn("saved state not used", o.state_error)
        self.assertIn("Paris", o.think("What is the capital of France?").text)


class TestFactIds(unittest.TestCase):
    def test_stable_and_distinct(self):
        a = Fact("FRANCE", "CAPITAL", "S", "PARIS")
        self.assertEqual(a.id, Fact("FRANCE", "CAPITAL", "S", "PARIS").id)
        self.assertEqual(len(a.id), 16)
        self.assertNotEqual(a.id, Fact("FRANCE", "CAPITAL", "S", "LYON").id)
        self.assertNotEqual(a.id, Fact("FRANCE", "CAPITAL", "S", "PARIS", start=1900).id)
        ids = [f.id for k in sorted(corpus().subjects) for f in corpus().facts_for(k)]
        self.assertEqual(len(ids), len(set(ids)))


class TestPhrasing(unittest.TestCase):
    def test_each_value_keeps_its_own_qualifier(self):
        fs = [Fact("PI", "POWER_DRAW", "N", "3", "W", qual="STATE=IDLE"), Fact("PI", "POWER_DRAW", "N", "6.4", "W", qual="STATE=LOAD")]
        self.assertEqual(sentence("PI", "POWER_DRAW", fs), "Pi's power draw is 3 watts (state idle), or 6.4 watts (state load).")
        same = [Fact("X", "HEIGHT", "N", "3", "M", qual="EST=1"), Fact("X", "HEIGHT", "N", "4", "M", qual="EST=1")]
        self.assertEqual(sentence("X", "HEIGHT", same), "X's height is 3 metres, or 4 metres (est 1).")   # as before


class TestHeldOutDialogues(unittest.TestCase):
    """tests/analysis_heldout.jsonl (written before the code) and analysis_paraphrases.jsonl (written after, scored
    blind once: 28/2/2) through the whole turn.  Both were fixed against since, so they're regression sets now."""

    def test_no_turn_regresses(self):
        import bench_dialogue
        bad = []
        for line in [*(ROOT / "tests" / "analysis_heldout.jsonl").open(), *(ROOT / "tests" / "analysis_paraphrases.jsonl").open()]:
            if not line.strip():
                continue
            d = json.loads(line)
            o = computer()
            for i, t in enumerate(d["turns"]):
                said = o.think(t["q"]).text
                if bench_dialogue.score_turn(t, said) != "right":
                    bad.append((d["id"], i + 1, said))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
