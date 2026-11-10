"""The learned controller (computer/controller.py): reading, filling, the checks, the network, training, the computer.

Uses the tiny corpus (tests/mini_corpus.py).  Training needs numpy; those tests are skipped without it."""

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import mini_corpus                                                       # noqa: E402
from computer.config import Config                                            # noqa: E402
from computer.controller import (ACTION_NAMES, ACTIONS, ControllerPlanner, Network, Uniform, action_of,  # noqa: E402
                            features, fills, hashed)
from computer.corpus import Corpus                                            # noqa: E402
from computer.navdata import generate                                         # noqa: E402
from computer.plan import parse                                               # noqa: E402
from computer.planner import RulePlanner, navigate                            # noqa: E402

try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

TMP = tempfile.TemporaryDirectory()
DATA = mini_corpus.build(Path(TMP.name) / "data")
_NET = {}


def corpus():
    return Corpus(DATA).load()


def trained():
    """Train once for the module (a few seconds on the tiny corpus)."""
    if "net" not in _NET:
        from computer.controller_train import build_dataset, reword, train
        c = corpus()
        rng = random.Random(0)
        exs = []
        for ex in generate(c, 5000):
            exs += [ex] + reword(ex, rng, 4)
        rows, _ = build_dataset(exs, ControllerPlanner(c))
        _NET["net"], _NET["rows"] = train(rows, epochs=40), rows
    return _NET["net"]


class TestReading(unittest.TestCase):
    def setUp(self):
        self.ctl = ControllerPlanner(corpus())

    def test_names_are_only_what_the_corpus_has(self):
        r = self.ctl.reader.read("which one's taller the eiffel tower or the brandenburg gate")
        self.assertEqual([s.phrase for s in r.spans], ["eiffel tower", "brandenburg gate"])
        self.assertIn("taller", [r.toks[i] for i in r.free])
        r = self.ctl.reader.read("glorbs and zibbles")
        self.assertEqual(r.spans, [])

    def test_talking_to_the_computer_is_not_a_name_but_owls_are(self):
        names = lambda q: [s.phrase for s in self.ctl.reader.read(q).spans if not s.relish]
        self.assertEqual(names("hey owl, france capital"), ["france"])     # "capital" is a word, and a relation's
        self.assertEqual(names("owl diet"), ["owl"])

    def test_spelling_slips_are_soft_names(self):
        r = self.ctl.reader.read("meaning of colour")
        self.assertTrue(any(s.soft and s.phrase == "colour" for s in r.spans))

    def test_every_fill_is_a_valid_plan(self):
        for q in ["owl diet", "jupiter vs saturn, which is heavier", "meaning: cheerful", "list what orbits the sun",
                  "is berlin in germany", "how many moons for jupiter", "the opposite of happy", "is dog a kind of animal"]:
            r = self.ctl.reader.read(q)
            for a in ACTION_NAMES:
                for text in fills(a, r):
                    parse(text)          # raises if not

    def test_comparatives_choose_the_direction(self):
        r = self.ctl.reader.read("which is older, the eiffel tower or the brandenburg gate")
        self.assertTrue(fills("compare_less", r))
        self.assertFalse(fills("compare_more", r))


class TestChecks(unittest.TestCase):
    def setUp(self):
        self.c = corpus()
        self.ctl = ControllerPlanner(self.c)

    def verdict(self, q, plan):
        p = parse(plan)
        res = self.ctl.nav.run(p)
        self.assertTrue(res.answered, res.text)
        return self.ctl.accept(q, p, res)

    def test_an_answer_that_ignores_a_name_is_rejected(self):
        why = self.verdict("spain's population of birds", "$1 = FIND spain\n$2 = GET $1 ~population,birds\nANSWER $2")
        self.assertIn("birds", why)

    def test_one_step_that_mixes_two_relations_is_rejected(self):
        why = self.verdict("what is the population of the capital of spain",
                           "$1 = FIND spain\n$2 = GET $1 ~population,capital\nANSWER $2")
        self.assertIn("mixed", why)
        # the same two relations in order are fine
        self.assertEqual(self.verdict("what is the population of the capital of germany",
                                      "$1 = FIND germany\n$2 = GET $1 ~capital\n$3 = GET $2 ~population\nANSWER $3"), "")

    def test_a_dictionary_question_needs_what_it_asked_for(self):
        why = self.verdict("what is the opposite of another word for happy",
                           "$1 = FIND happy\n$2 = GET $1 ~antonym\nANSWER $2")
        self.assertIn("synonym", why)

    def test_a_dictionary_answer_needs_a_question_about_the_word(self):
        why = self.verdict("what language does a dog speak", "$1 = FIND dog\n$2 = GET $1 ~definition\nANSWER $2")
        self.assertIn("dictionary answer", why)
        self.assertEqual(self.verdict("puppy - what's that", "$1 = FIND puppy\n$2 = GET $1 ~definition\nANSWER $2"), "")
        self.assertEqual(self.verdict("other ways to say big", "$1 = FIND big\n$2 = GET $1 ~synonym,similar\nANSWER $2"), "")

    def test_a_good_answer_passes(self):
        self.assertEqual(self.verdict("owl diet", "$1 = FIND owl\n$2 = GET $1 ~diet\nANSWER $2"), "")
        self.assertEqual(self.verdict("capital city for spain please",
                                      "$1 = FIND spain\n$2 = GET $1 ~capital,city\nANSWER $2"), "")   # Madrid is a city

    def test_navigate_does_not_say_a_rejected_answer(self):
        class Bad(ControllerPlanner):
            def plans(self, q):
                p = parse("$1 = FIND spain\n$2 = GET $1 ~population,birds\nANSWER $2")
                p.kind = "learned"
                return [p]
        trace = []
        plan, r = navigate("spain's population of birds", [Bad(self.c)], self.ctl.nav, trace=trace)
        self.assertTrue(r is None or not r.answered)
        self.assertTrue(any("rejected" in t for t in trace))


class TestNetwork(unittest.TestCase):
    def test_round_trip_and_probabilities(self):
        net = Network(ACTION_NAMES, hidden=4, rows={3: [0.1, -0.2, 0.3, 0.0]}, b2=[0.01 * i for i in range(len(ACTION_NAMES))])
        p = net.predict([3, 99])
        self.assertAlmostEqual(sum(p.values()), 1.0, places=6)
        net2 = Network.from_json(json.loads(json.dumps(net.to_json())))
        self.assertEqual(net2.predict([3, 99]).keys(), p.keys())
        self.assertAlmostEqual(net2.predict([3])["hop"], net.predict([3])["hop"], places=4)

    def test_rule_plans_map_to_actions(self):
        c = corpus()
        ex = generate(c, 5000)
        mapped = [action_of(e["plan"]) for e in ex]
        self.assertGreater(sum(m is not None for m in mapped) / len(mapped), 0.9)
        self.assertTrue(set(m for m in mapped if m) <= set(ACTIONS))

    def test_features_mask_names(self):
        ctl = ControllerPlanner(corpus())
        f = features(ctl.reader.read("france capital"), ctl.reader)
        self.assertIn("w:<e>", f)
        self.assertNotIn("w:france", f)
        self.assertTrue(hashed(f))


@unittest.skipUnless(HAVE_NUMPY, "training needs numpy")
class TestLearning(unittest.TestCase):
    def test_exploration_finds_labels_nobody_wrote(self):
        from computer.controller_train import explore
        ctl = ControllerPlanner(corpus())
        good, runs = explore({"question": "jupiter vs saturn: heavier?", "gold": ["Jupiter"], "expect": "answered",
                              "kind": "compare"}, ctl)
        self.assertEqual(good, ["compare_more"])
        good, _ = explore({"question": "owl diet", "gold": ["mice"], "expect": "answered", "kind": "hop"}, ctl)
        self.assertIn("hop", good)
        self.assertNotIn("count", good)     # "My corpus lists 1 for Owl: mice" isn't an answer to that

    def test_the_network_learns_the_shapes(self):
        from computer.controller_train import top1
        net = trained()
        self.assertGreater(top1(net, _NET["rows"]), 0.9)

    def test_new_phrasings_answered_after_the_rules_give_up(self):
        c = corpus()
        rules = RulePlanner(c)
        ctl = ControllerPlanner(c, trained())
        for q, gold in [("jupiter vs saturn, which is heavier", "so jupiter is heavier"),
                        ("meaning: cheerful", "good spirits"), ("jupiter satellites, how many", "4")]:
            _, r0 = navigate(q, [rules], ctl.nav)
            self.assertFalse(r0 and r0.answered, q)
            _, r = navigate(q, [rules, ctl], ctl.nav, limit=8)
            self.assertTrue(r and r.answered, q)
            self.assertIn(gold, r.text.lower(), q)

    def test_direction_after_the_rules_give_up(self):
        c = corpus()
        ctl = ControllerPlanner(c, trained())
        for q, gold in [("anything orbiting saturn?", "titan"), ("titan orbits what", "saturn")]:
            _, r = navigate(q, [RulePlanner(c), ctl], ctl.nav, limit=8)
            self.assertTrue(r and r.answered, q)
            self.assertIn(gold, r.text.lower(), q)
        _, r = navigate("things that orbit ganymede", [RulePlanner(c), ctl], ctl.nav, limit=8)
        self.assertFalse(r and r.answered)              # nothing does; "Ganymede orbits Jupiter" isn't the answer

    def test_fewer_plans_than_trying_everything(self):
        from computer.controller_eval import run_system
        c = corpus()
        rules = RulePlanner(c)
        ex = [json.loads(l) for l in open(Path(__file__).parent / "controller_heldout.jsonl")]
        tot_l, plans_l, _ = run_system(c, ex, [rules, ControllerPlanner(c, trained())])
        tot_u, plans_u, _ = run_system(c, ex, [rules, ControllerPlanner(c, Uniform(), min_p=0, max_actions=99,
                                                                        max_plans=99)], limit=99)
        self.assertLess(plans_l, plans_u / 2)
        self.assertLess(tot_l["wrong"], tot_u["wrong"])
        self.assertGreater(tot_l["right"], 25)

    def test_the_computer_uses_it(self):
        from computer.core import Computer
        path = Path(TMP.name) / "controller.json"
        trained().save(path)
        cfg = Config(corpus=str(DATA), llm_mode="off", llm_endpoint="", log_file=str(Path(TMP.name) / "log.jsonl"),
                     navigator_controller=str(path))
        computer = Computer(cfg)
        self.assertEqual([p.label for p in computer.planners], ["rules", "learned"])
        self.assertIn("so jupiter is heavier", computer.think("jupiter vs saturn, which is heavier").text.lower())
        self.assertIn("mice", computer.think("owl diet").text.lower())

    def test_a_missing_controller_file_doesnt_stop_the_computer(self):
        from computer.core import Computer
        cfg = Config(corpus=str(DATA), llm_mode="off", llm_endpoint="", log_file=str(Path(TMP.name) / "log.jsonl"),
                     navigator_controller=str(Path(TMP.name) / "nope.json"))
        computer = Computer(cfg)
        self.assertEqual([p.label for p in computer.planners], ["rules"])
        self.assertIn("not loaded", computer.controller_error)


if __name__ == "__main__":
    unittest.main()
