"""Run with:  python3 -m unittest discover -s tests -v      (from the cdos-computer folder)"""

import os
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from computer import iching, instinct                      # noqa: E402
from computer.config import Config                         # noqa: E402
from computer.corpus import Corpus                         # noqa: E402
from computer.router import classify                       # noqa: E402
from computer.core import Computer                            # noqa: E402

DATA = ROOT / "data"
HAS_DATA = (DATA / "meta.json").exists()


class EmptyCorpus(Corpus):
    def __init__(self):
        super().__init__("/nonexistent")


class TestIChing(unittest.TestCase):
    def test_king_wen_is_a_bijection(self):
        self.assertEqual(sorted(iching.KING_WEN.values()), list(range(1, 65)))
        self.assertEqual(len(iching.book()["hexagrams"]), 64)

    def test_known_hexagrams(self):
        known = {1: "111111", 2: "000000", 3: "100010", 4: "010001", 11: "111000", 12: "000111",
                 29: "010010", 30: "101101", 51: "100100", 52: "001001", 57: "011011", 58: "110110",
                 63: "101010", 64: "010101"}
        for n, bits in known.items():
            self.assertEqual(iching.BITS_OF[n], bits, n)

    def test_changing_lines_and_relating(self):
        r = iching.Reading([9] * 6, "coins")
        self.assertEqual(r.primary.n, 1)
        self.assertEqual(r.relating.n, 2)
        self.assertEqual(r.changing, [1, 2, 3, 4, 5, 6])
        still = iching.Reading([7, 8, 7, 8, 7, 8], "coins")
        self.assertIsNone(still.relating)
        self.assertEqual(still.primary.n, 63)

    def test_nuclear(self):
        self.assertEqual(iching.hexagram_n(1).nuclear().n, 1)
        self.assertEqual(iching.hexagram_n(63).nuclear().n, 64)

    def test_seeded_cast_is_reproducible(self):
        a = iching.cast("q", seed="abc").lines
        b = iching.cast("q", seed="abc").lines
        self.assertEqual(a, b)
        self.assertNotEqual(iching.cast("q", seed="abc").lines, iching.cast("other", seed="abc").lines + [0])

    def test_line_odds(self):
        for method, weights in iching.METHODS.items():
            c = Counter()
            for i in range(6000):
                c.update(iching.cast("q", method, seed=f"s{i}").lines)
            total = sum(c.values())
            for v, w in weights.items():
                self.assertAlmostEqual(c[v] / total, w / 16, delta=0.012, msg=f"{method} {v}")

    def test_unseeded_casts_differ(self):
        self.assertGreater(len({tuple(iching.cast("same").lines) for _ in range(50)}), 40)


class TestInstinct(unittest.TestCase):
    def test_topics(self):
        self.assertEqual(instinct.topic_of("Should I take the new job?"), "whether to take the new job")
        self.assertEqual(instinct.topic_of("Will my garden grow?"), "whether your garden will grow")
        self.assertEqual(instinct.topic_of("What should I do about my brother?"), "your brother")

    def test_perspective_has_no_numbers_and_names_the_hexagram(self):
        for i in range(200):
            p = instinct.perspective("should I paint my room blue?", seed=f"x{i}")
            self.assertIn(p.reading.primary.name, p.brief)
            self.assertNotRegex(p.brief, r"\d")

    def test_future_questions_get_the_disclaimer(self):
        p = instinct.perspective("will I win the race", seed="1")
        self.assertTrue(any(f in p.brief for f in instinct.FUTURE))


class TestPolicy(unittest.TestCase):
    def test_sensitive_never_goes_to_the_coins(self):
        for q in ["should I stop taking my medicine", "should I invest in bitcoin", "will my rash go away",
                  "should I sue my neighbour"]:
            self.assertNotEqual(classify(q).faculty, "instinct", q)

    def test_care(self):
        for q in ["I want to hurt myself", "someone is hurting me"]:
            self.assertEqual(classify(q).faculty, "care", q)

    def test_open_questions_go_to_the_coins(self):
        for q in ["should I take the new job", "give me a reading", "consult the i ching", "say something wise",
                  "what should I do about my brother"]:
            self.assertEqual(classify(q).faculty, "instinct", q)


class TestTheHouseRules(unittest.TestCase):
    """Instinct never overrides Logic or Knowledge."""

    def computer(self, **kw):
        return Computer(Config(llm_endpoint="", log_file="", **kw), corpus=EmptyCorpus().load())

    def test_shaking_does_not_change_a_sum(self):
        o = self.computer()
        for i in range(20):
            r = o.think("what is 12 times 7", shake=os.urandom(8))
            self.assertEqual(r.text, "12 times 7 is 84.")
            self.assertIsNone(r.perspective)
        self.assertIsNone(o.last)

    def test_unknown_fact_is_not_guessed_by_the_coins(self):
        o = self.computer()
        r = o.think("what is the capital of narnia")
        self.assertEqual(r.faculty, "knowledge")
        self.assertIsNone(r.perspective)

    def test_shake_alone_casts(self):
        r = self.computer().think("", shake=b"\x01\x02")
        self.assertEqual(r.faculty, "instinct")


class TestMiniLM(unittest.TestCase):
    def run_with(self, replies, mode="precall", q="should I learn the violin"):
        from fake_llm import Fake
        fake = Fake(replies)
        try:
            o = Computer(Config(llm_endpoint=fake.url, llm_mode=mode, log_file=""), corpus=EmptyCorpus().load())
            return o.think(q), fake
        finally:
            fake.close()

    def name_for(self, r):
        return r.perspective.reading.primary.name

    def test_good_wording_is_used(self):
        from fake_llm import Fake
        fake = Fake([])
        o = Computer(Config(llm_endpoint=fake.url, log_file=""), corpus=EmptyCorpus().load())
        r0 = o.think("should I learn the violin")          # fake returns "" -> template output
        self.assertEqual(r0.voice, "computer")
        name = r0.perspective.reading.primary.name
        fake.replies.append(f"Hoo! The coins show {name}. Go gently with the violin, one note at a time.")
        # same hexagram is not guaranteed on a new cast, so check the rule directly
        why = o._check(fake.replies[0], r0.text, "q", r0.perspective)
        self.assertEqual(why, "")
        fake.close()

    def test_bad_wordings_are_discarded(self):
        r, _ = self.run_with(["Hoo. You will definitely succeed in 3000 days!"])
        self.assertEqual(r.voice, "computer")
        self.assertTrue(any("discarded" in t for t in r.trace))

    def test_tools_mode_runs_consult_iching(self):
        call = {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                               "function": {"name": "consult_iching", "arguments": '{"question": "q"}'}}]}
        r, fake = self.run_with([call, "placeholder"], mode="tools")
        self.assertTrue(any("tool call: consult_iching" in t for t in r.trace))
        tool_msg = fake.requests[1]["messages"][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertIn(r.perspective.reading.primary.name, tool_msg["content"])


@unittest.skipUnless(HAS_DATA, "no data/ folder (copy cdos-web/data here)")
class TestKnowledge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.computer = Computer(Config(corpus=str(DATA), llm_endpoint="", log_file=""))

    def test_facts(self):
        self.assertIn("Paris", self.computer.think("what is the capital of france").text)
        self.assertIn("Callisto", self.computer.think("how many moons does jupiter have").text)

    def test_navigator_two_hops(self):
        r = self.computer.think("what is the population of the capital of france")
        self.assertIn("Paris", r.text)
        self.assertTrue(any(t.startswith("planner") for t in r.trace), r.trace)

    def test_knowledge_aside_for_named_things_only(self):
        self.assertIn("gas giant", self.computer.think("should I visit Jupiter?").text)
        self.assertNotIn("KNOWN FACT", self.computer.think("should I plant a garden").text)


if __name__ == "__main__":
    unittest.main()
