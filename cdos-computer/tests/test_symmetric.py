"""A relation that holds both ways (knowledge rule `symmetric`, computer/data/rules.json), and reverse answers that
never cut a list short in silence.  "Which countries border France" has to name all eight, whichever side's rows
say so."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_shards                                                                          # noqa: E402
from computer.corpus import Corpus                                                           # noqa: E402
from computer.navigator import Navigator                                                     # noqa: E402
from computer.planner import RulePlanner, navigate                                           # noqa: E402
from computer.rules import RuleError, parse_knowledge                                        # noqa: E402

T = "\t"
NEIGHBOURS = ["Switzerland", "Germany", "Belgium", "Luxembourg", "Italy", "Andorra", "Monaco", "Spain"]


def r(s, rel, v, src="GEONAMES"):
    return T.join([s, rel, "S", v, "", "0", "0", src, "x", "1.0", ""])


ROWS = ([r("France", "SHARES_BORDER_WITH", n) for n in NEIGHBOURS]              # France's own rows: all eight
        + [r(n, "SHARES_BORDER_WITH", "France") for n in NEIGHBOURS[:6]]         # the other way: six of them
        + [r(n, "IS_A", "country") for n in NEIGHBOURS + ["France"]])
EATERS = ("Blue whale", "Fin whale", "Humpback whale", "Penguin", "Seal", "Squid", "Manta ray")


class TestBothWays(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        (t / "c.pf").write_text("\n".join(ROWS) + "\n")
        build_shards.build(t / "c.pf", t / "data", 4, 4, fix_keys=True, log=lambda *a: None)
        cls.c = Corpus(t / "data").load()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ask(self, q):
        nav = Navigator(self.c)
        a = navigate(q, [RulePlanner(self.c)], nav)[1]
        return (a.text if a else "(no answer)"), (a.rules if a else [])

    def test_every_neighbour_is_named_whichever_side_says_so(self):
        text, rules = self.ask("which countries border france")
        self.assertEqual(text, "France shares border with Andorra, Belgium, Germany, Italy, Luxembourg, Monaco, "
                               "Spain and Switzerland.")
        self.assertIn("symmetric.shares_border_with", rules)

    def test_a_long_list_is_never_cut_short_in_silence(self):
        from computer.corpus import Fact
        from computer.navigator import Item
        items = [Item(a.upper().replace(" ", "_"), Fact(a.upper().replace(" ", "_"), "DIET", "S", "krill"))
                 for a in EATERS]
        text = Navigator(self.c)._say_who(items)
        for a in EATERS:                                  # five said in full, the other two named after "Also:"
            self.assertIn(a.title(), text)
        self.assertIn("Also: Seal and Squid.", text)


class TestTheRule(unittest.TestCase):
    def test_a_symmetric_rule_needs_its_fields(self):
        with self.assertRaises(RuleError):
            parse_knowledge({"symmetric": [{"rule_id": "x", "relation": "R"}]})
        with self.assertRaises(RuleError):
            parse_knowledge({"symmetric": [{"rule_id": "x", "relation": "R", "why": "w"},
                                           {"rule_id": "y", "relation": "R", "why": "w"}]})
        k = parse_knowledge({"symmetric": [{"rule_id": "x", "relation": "R", "why": "w"}]})
        self.assertIn("R", k.symmetric)


if __name__ == "__main__":
    unittest.main()
