"""The compute engine (computer/compute.py): quantization, backends, the CCPU gate, profiles, the auto policy, metering.

Quantized-controller tests reuse the controller trained in test_controller.py (they need numpy)."""

import json
import random
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from computer.compute import (PROFILES, CCPUBackend, Engine, Meter, Policy, PyBackend, QMatrix, QuantNetwork,  # noqa: E402
                         Reading, llama_args, load_controller_model, pick_backend, validate)
from computer.config import Config                                            # noqa: E402

try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

TMP = tempfile.TemporaryDirectory()


def rand_matrix(rows, cols, seed=0):
    rng = random.Random(seed)
    return [[rng.gauss(0, 0.4) for _ in range(cols)] for _ in range(rows)]


class TestQuantization(unittest.TestCase):
    def test_error_is_at_most_half_a_step(self):
        m = rand_matrix(50, 32)
        for bits, qmax in ((8, 127), (4, 7)):
            q = QMatrix.quantize(m, bits)
            c = QMatrix.quantize(m, bits, per="col")
            for i, row in enumerate(m):
                step = max(abs(v) for v in row) / qmax
                self.assertLessEqual(max(abs(a - b) for a, b in zip(row, q.row(i))), step / 2 + 1e-12)
                self.assertTrue(all(-qmax <= v <= qmax for v in q.q[i]))
                for j, (a, b) in enumerate(zip(row, c.row(i))):
                    self.assertLessEqual(abs(a - b), max(abs(r[j]) for r in m) / qmax / 2 + 1e-12)

    def test_both_layouts_agree_on_the_python_backend(self):
        m = rand_matrix(40, 16, seed=3)
        rows, b = [1, 5, 9, 33], [0.1] * 16
        exact = [b[j] + 0.5 * sum(m[r][j] for r in rows) for j in range(16)]
        for per in ("row", "col"):
            got = PyBackend().gather_sum(QMatrix.quantize(m, 8, per=per), rows, 0.5, b)
            self.assertLess(max(abs(x - y) for x, y in zip(exact, got)), 0.02, per)
            x = [0.3 * k for k in range(16)]
            want = [sum(a * xi for a, xi in zip(r, x)) for r in m]
            got = PyBackend().matvec(QMatrix.quantize(m, 8, per=per), x, [0.0] * 40)
            self.assertLess(max(abs(p - q) for p, q in zip(want, got)), 0.1, per)

    def test_pack_round_trip(self):
        for bits, cols in ((8, 32), (4, 32), (4, 5)):          # 5 columns x 3 rows: an odd count of nibbles
            q = QMatrix.quantize(rand_matrix(3, cols, seed=bits), bits)
            back = QMatrix.from_json(json.loads(json.dumps(q.to_json())))
            self.assertEqual([list(r) for r in back.q], [list(r) for r in q.q])
            self.assertEqual(len(q.packed()), 3 * cols if bits == 8 else (3 * cols + 1) // 2)

    def test_zero_row(self):
        q = QMatrix.quantize([[0.0, 0.0]], 8)
        self.assertEqual(q.row(0), [0.0, 0.0])


class FakeSensors:
    def __init__(self, *readings):
        self.readings = list(readings)

    def read(self):
        return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]


class TestPolicy(unittest.TestCase):
    def test_fixed(self):
        p, why = Policy("eco").choose()
        self.assertEqual((p.name, why), ("eco", "set"))

    def test_auto_with_hysteresis(self):
        s = FakeSensors(Reading(55, 0.2), Reading(72, 0.2), Reading(66, 0.2), Reading(60, 0.2), Reading(55, 0.95))
        pol = Policy("auto", sensors=s)
        names = [pol.choose()[0].name for _ in range(5)]
        # cool -> hot -> 66 °C is still cooling -> 60 °C balanced -> busy
        self.assertEqual(names, ["balanced", "eco", "eco", "balanced", "eco"])

    def test_auto_never_picks_priority_and_handles_no_sensors(self):
        pol = Policy("auto", sensors=FakeSensors(Reading()))
        self.assertEqual(pol.choose()[0].name, "balanced")

    def test_low_battery(self):
        pol = Policy("auto", sensors=FakeSensors(Reading(40, 0.1, on_battery=True, battery_pct=12)))
        p, why = pol.choose()
        self.assertEqual(p.name, "eco")
        self.assertIn("low battery", why)

    def test_bad_profile(self):
        with self.assertRaises(ValueError):
            Policy("turbo")

    def test_balanced_is_what_the_computer_always_did(self):
        b = PROFILES["balanced"]
        self.assertEqual((b.nav_limit, b.ctl_actions, b.ctl_plans, b.lm_tokens, b.llm_threads, b.llm_ctx),
                         (8, 3, 4, 1.8, 4, 1024))

    def test_llama_args(self):
        self.assertEqual(llama_args("eco"), ["-t", "2", "-c", "512"])
        self.assertEqual(llama_args("priority", cores=8), ["-t", "8", "-c", "2048"])


DRIVERS = Path(TMP.name) / "drivers"
DRIVERS.mkdir()
(DRIVERS / "good_ccpu.py").write_text(textwrap.dedent("""
    from array import array
    class Driver:
        calls = 0
        def _rows(self, blob, rows, cols):
            flat = array("b", blob).tolist()
            return [flat[i * cols:(i + 1) * cols] for i in range(rows)]
        def gather_sum_int8(self, blob, rows, cols, scales, row_ids, scale, bias):
            Driver.calls += 1
            q = self._rows(blob, rows, cols)
            return [bias[j] + scale * scales[j] * sum(q[r][j] for r in row_ids) for j in range(cols)]
        def matvec_int8(self, blob, rows, cols, scales, x, bias):
            Driver.calls += 1
            q = self._rows(blob, rows, cols)
            return [b + s * sum(a * xi for a, xi in zip(row, x)) for row, s, b in zip(q, scales, bias)]
    """))
(DRIVERS / "bad_ccpu.py").write_text(textwrap.dedent("""
    class Driver:
        def gather_sum_int8(self, blob, rows, cols, scales, row_ids, scale, bias):
            return [0.0] * cols
        def matvec_int8(self, blob, rows, cols, scales, x, bias):
            return [0.0] * rows
    """))
sys.path.insert(0, str(DRIVERS))


class TestBackends(unittest.TestCase):
    def test_python_matches_float(self):
        m = rand_matrix(10, 8)
        q = QMatrix.quantize(m, 8)
        x = [0.5] * 8
        want = [sum(a * b for a, b in zip(r, x)) for r in m]
        got = PyBackend().matvec(q, x, [0.0] * 10)
        self.assertLess(max(abs(a - b) for a, b in zip(want, got)), 0.02)

    @unittest.skipUnless(HAVE_NUMPY, "numpy")
    def test_numpy_matches_python(self):
        from computer.compute import NumpyBackend
        self.assertTrue(validate(NumpyBackend())["ok"])

    def test_a_good_ccpu_driver_is_used_after_it_validates(self):
        b, why = pick_backend("auto", "good_ccpu:Driver")
        self.assertIsInstance(b, CCPUBackend)
        self.assertTrue(b.validated)
        self.assertIn("validated", why)

    def test_a_wrong_ccpu_driver_is_never_used(self):
        b, why = pick_backend("auto", "bad_ccpu:Driver")
        self.assertIsInstance(b, PyBackend)
        self.assertIn("failed validation", why)

    def test_a_missing_driver_falls_back(self):
        b, why = pick_backend("ccpu", "no_such_module:Driver")
        self.assertIsInstance(b, PyBackend)
        self.assertIn("not available", why)
        self.assertEqual(pick_backend("auto")[1], "python")

    def test_int4_stays_on_the_cpu(self):
        import good_ccpu
        b = CCPUBackend("good_ccpu:Driver")
        q = QMatrix.quantize(rand_matrix(4, 8), 4)
        before = good_ccpu.Driver.calls
        self.assertEqual(b.matvec(q, [1.0] * 8, [0.0] * 4), PyBackend().matvec(q, [1.0] * 8, [0.0] * 4))
        self.assertEqual(good_ccpu.Driver.calls, before)


class TestMeter(unittest.TestCase):
    def test_rapl_energy_and_wrap(self):
        root = Path(TMP.name) / "sys"
        rapl = root / "class/powercap/intel-rapl:0"
        rapl.mkdir(parents=True)
        (rapl / "energy_uj").write_text("1000000")
        (rapl / "max_energy_range_uj").write_text("10000000")
        m = Meter(str(root))
        self.assertTrue(m.energy_ok)
        tok = m.start()
        (rapl / "energy_uj").write_text("3500000")
        self.assertAlmostEqual(m.stop(tok)["energy_j"], 2.5)
        tok = m.start()
        (rapl / "energy_uj").write_text("500000")              # wrapped: 3.5 J -> 10 J -> 0.5 J
        self.assertAlmostEqual(m.stop(tok)["energy_j"], 7.0)

    def test_no_energy_on_a_pi(self):
        m = Meter(str(Path(TMP.name) / "nothing"))
        out = m.stop(m.start())
        self.assertNotIn("energy_j", out)
        self.assertIn("cpu_ms", out)


@unittest.skipUnless(HAVE_NUMPY, "training the controller needs numpy")
class TestQuantizedController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_controller as tc
        cls.tc = tc
        cls.net = tc.trained()
        cls.rows = tc._NET["rows"]

    def agreement(self, model):
        same = 0
        for row in self.rows:
            a = self.net.predict(row["idx"])
            b = model.predict(row["idx"])
            same += max(a, key=a.get) == max(b, key=b.get)
        return same / len(self.rows)

    def test_int8_picks_the_same_action(self):
        self.assertGreaterEqual(self.agreement(QuantNetwork.from_network(self.net, 8)), 0.97)   # measured 98.7-100%

    def test_int4_mostly_does(self):
        self.assertGreaterEqual(self.agreement(QuantNetwork.from_network(self.net, 4)), 0.9)

    def test_file_round_trip_and_size(self):
        f = Path(TMP.name) / "c.json"
        q = Path(TMP.name) / "c.q8.json"
        self.net.save(f)
        qn = QuantNetwork.from_network(self.net, 8)
        qn.save(q)
        self.assertLess(q.stat().st_size, f.stat().st_size / 2)
        back = load_controller_model(q)
        row = self.rows[0]["idx"]
        a, b = back.predict(row), qn.predict(row)          # the file keeps 6 significant figures of each scale
        self.assertLess(max(abs(a[k] - b[k]) for k in a), 1e-4)
        self.assertIsInstance(load_controller_model(f, "int4"), QuantNetwork)
        with self.assertRaises(ValueError):
            load_controller_model(f, "int3")

    def test_the_computer_answers_with_an_int8_controller(self):
        from computer.core import Computer
        path = Path(TMP.name) / "controller.json"
        self.net.save(path)
        cfg = Config(corpus=str(self.tc.DATA), llm_mode="off", llm_endpoint="", log_file="",
                     navigator_controller=str(path), controller_precision="int8")
        computer = Computer(cfg)
        self.assertIsInstance(computer.planners[-1].model, QuantNetwork)
        self.assertIn("so jupiter is heavier", computer.think("jupiter vs saturn, which is heavier").text.lower())
        self.assertIn("mice", computer.think("owl diet").text.lower())

    def test_eco_tries_fewer_controller_plans(self):
        from computer.core import Computer
        path = Path(TMP.name) / "controller.json"
        self.net.save(path)
        cfg = Config(corpus=str(self.tc.DATA), llm_mode="off", llm_endpoint="", log_file="",
                     navigator_controller=str(path), compute_profile="eco")
        computer = Computer(cfg)
        r = computer.think("jupiter vs saturn, which is heavier")
        self.assertEqual(computer.planners[-1].max_actions, 1)
        self.assertIn("so jupiter is heavier", r.text.lower())     # its top action is still enough here


class TestTheComputerOnAProfile(unittest.TestCase):
    def computer(self, profile, url="", **kw):
        from test_computer import EmptyCorpus
        from computer.core import Computer
        return Computer(Config(llm_endpoint=url, compute_profile=profile, log_file="", **kw), corpus=EmptyCorpus().load())

    def test_eco_doesnt_wake_the_model(self):
        from fake_llm import Fake
        fake = Fake(["Hoo, the coins say rest."])
        try:
            r = self.computer("eco", fake.url).think("should I learn the violin")
            self.assertEqual(fake.requests, [])
            self.assertEqual(fake.gets, 0)                      # it didn't even check the server was up
            self.assertEqual(r.voice, "computer")
            self.assertTrue(any("mini LM resting" in t for t in r.trace))
            self.assertEqual(r.compute["profile"], "eco")
        finally:
            fake.close()

    def test_balanced_wakes_it_and_logs_its_time(self):
        from fake_llm import Fake
        fake = Fake([""])
        try:
            r = self.computer("balanced", fake.url).think("should I learn the violin")
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(fake.requests[0]["max_tokens"], int(70 * 1.8))
            self.assertIn("ms", r.compute["lm"][0])
        finally:
            fake.close()

    def test_priority_uses_the_bigger_model_when_it_is_up(self):
        from fake_llm import Fake
        small, big = Fake([""]), Fake([""])
        try:
            r = self.computer("priority", small.url, llm_endpoint_priority=big.url).think("should I learn the violin")
            self.assertEqual((len(small.requests), len(big.requests)), (0, 1))
            self.assertEqual(big.requests[0]["max_tokens"], int(70 * 2.2))
            self.assertEqual(r.compute["profile"], "priority")
        finally:
            small.close()
            big.close()

    def test_profiles_dont_change_sums_or_facts(self):
        for prof in PROFILES:
            o = self.computer(prof, llm_mode="off")
            self.assertIn("84", o.think("what is 12 times 7").text, prof)
            self.assertEqual(o.think("what is the capital of france").faculty, "knowledge", prof)

    def test_a_bad_profile_doesnt_stop_the_computer(self):
        o = self.computer("turbo", llm_mode="off")
        self.assertIn("using balanced", o.compute_error)
        self.assertEqual(o.think("hi").compute["profile"], "balanced")

    def test_runtime_switch(self):
        o = self.computer("balanced", llm_mode="off")
        o.engine.set_profile("eco")
        self.assertEqual(o.think("hi").compute["profile"], "eco")


class TestEngine(unittest.TestCase):
    def test_status(self):
        e = Engine("auto", "python", "int8", meter_root=str(Path(TMP.name) / "nothing"))
        self.assertIn("controller int8", e.status())
        self.assertIn("energy not reported", e.status())


if __name__ == "__main__":
    unittest.main()
