"""The CDOS Quantized Compute Engine: how hard the computer's machinery works, and on what.

    CDOS CORE           decides what to retrieve, rank or say           (core.py, navigator.py ...)
        |
    COMPUTE ENGINE      chooses precision, backend and how much to try  (this file)
        |
    backends            python (always) | numpy (if installed) | ccpu (your FPGA, once it validates)

It is an engine, not a GPU simulator: nothing here pretends to be a GPU.  It does three separate jobs,
and they are kept separate because they save different things:

 1. Quantization (memory).  The learned controller's weights can be held as int8 or int4: the first layer
    with one scale per hidden unit (so summing a question's rows is an exact integer sum, done in C by
    zip/sum), the output layer with one scale per action.  The win is memory and file size; time is about
    the same in pure Python.  Measure with tools/bench_compute.py before you believe otherwise.
 2. Profiles (power and latency).  eco / balanced / priority set how many plans the navigator may run,
    whether the mini LM is woken up at all, how many tokens it may write and which LM endpoint to use.
    "auto" picks eco when the board is hot or busy (the Pi may sit in a small, closed enclosure).  The llama-server
    side (threads, context) is set when that service starts: `python3 -m computer.compute --llama-args eco`.
 3. Backends (the CCPU path).  Two operations, the only ones the controller needs: a sparse gather-sum
    of quantized rows and a quantized matrix-vector product.  A CCPU driver is used only after it
    reproduces the Python backend's numbers (validate()), and its timing includes transfers and readback.

None of this changes what the computer says, only how much work it does to say it.  A profile can only take
work away (fewer plans, no model wording); the house rules in core.py still check everything that is said.
"""

from __future__ import annotations

import base64
import importlib
import json
import math
import operator
import os
import time
from array import array
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------- quantization

QFORMAT = "cdos-computer-controller-q/1"
QFORMATS = (QFORMAT, "cdos-owl-controller-q/1")   # files written before the rename still load


@dataclass
class QMatrix:
    """A matrix of signed ints and float scales, symmetric: value[i][j] ~= q[i][j] * scale.

    per="row": one scale per row (value[i][j] ~= q[i][j] * scales[i])      the output layer: one per action
    per="col": one scale per column (value[i][j] ~= q[i][j] * scales[j])   the first layer: one per hidden unit,
               so a gather-sum of rows is an exact integer sum, scaled once at the end

    int8 uses -127..127, int4 uses -7..7.  Each row is an array of signed bytes (1 byte a value, against
    ~32 for a float in a Python list)."""
    bits: int
    cols: int
    q: list            # list of array("b") rows
    scales: list[float]
    per: str = "row"

    @property
    def rows(self) -> int:
        return len(self.q)

    @classmethod
    def quantize(cls, matrix: list[list[float]], bits: int = 8, per: str = "row") -> "QMatrix":
        if bits not in (4, 8):
            raise ValueError("bits must be 8 or 4")
        if per not in ("row", "col"):
            raise ValueError("per must be row or col")
        qmax = (1 << (bits - 1)) - 1
        cols = len(matrix[0]) if matrix else 0
        if per == "row":
            maxes = [max((abs(v) for v in row), default=0.0) for row in matrix]
        else:
            maxes = [max((abs(row[j]) for row in matrix), default=0.0) for j in range(cols)]
        scales = [m / qmax if m > 0 else 1.0 for m in maxes]
        qs = []
        for i, row in enumerate(matrix):
            qs.append(array("b", (max(-qmax, min(qmax, int(round(v / (scales[i] if per == "row" else scales[j])))))
                                  for j, v in enumerate(row))))
        return cls(bits, cols, qs, scales, per)

    def row(self, i: int) -> list[float]:
        if self.per == "row":
            s = self.scales[i]
            return [v * s for v in self.q[i]]
        return [v * s for v, s in zip(self.q[i], self.scales)]

    def max_error(self, matrix: list[list[float]]) -> float:
        return max((abs(a - b) for i, r in enumerate(matrix) for a, b in zip(r, self.row(i))), default=0.0)

    # packed form, for files and for a CCPU driver
    def packed(self) -> bytes:
        if self.bits == 8:
            return b"".join(r.tobytes() for r in self.q)
        flat = [v for r in self.q for v in r]
        if len(flat) % 2:
            flat.append(0)
        return bytes(((a + 8) & 0xF) | (((b + 8) & 0xF) << 4) for a, b in zip(flat[0::2], flat[1::2]))

    @classmethod
    def unpack(cls, bits: int, rows: int, cols: int, blob: bytes, scales: list[float], per: str = "row") -> "QMatrix":
        flat = array("b", blob) if bits == 8 else array("b", (v for byte in blob for v in ((byte & 0xF) - 8, (byte >> 4) - 8)))
        return cls(bits, cols, [flat[i * cols:(i + 1) * cols] for i in range(rows)], list(scales), per)

    def to_json(self) -> dict:
        return {"bits": self.bits, "rows": self.rows, "cols": self.cols, "per": self.per,
                "scales": [float(f"{s:.6g}") for s in self.scales], "data": base64.b64encode(self.packed()).decode()}

    @classmethod
    def from_json(cls, d: dict) -> "QMatrix":
        return cls.unpack(d["bits"], d["rows"], d["cols"], base64.b64decode(d["data"]), d["scales"], d.get("per", "row"))


class QuantNetwork:
    """The controller's network (computer/controller.py, Network) with quantized weights.

    Same interface: predict(feature indices) -> {action: probability}.  The biases stay float (they are
    tiny).  The arithmetic is done by a backend, so the same file runs on Python, numpy or the CCPU."""

    def __init__(self, actions, dim, hidden, index: dict[int, int], w1: QMatrix, b1, w2: QMatrix, b2, backend=None):
        self.actions, self.dim, self.hidden = list(actions), dim, hidden
        self.index, self.w1, self.b1, self.w2, self.b2 = index, w1, list(b1), w2, list(b2)
        self.backend = backend or PyBackend()

    @property
    def bits(self) -> int:
        return self.w1.bits

    @classmethod
    def from_network(cls, net, bits: int = 8, backend=None) -> "QuantNetwork":
        keys = sorted(net.rows)
        w1 = QMatrix.quantize([net.rows[k] for k in keys], bits, per="col")    # one scale per hidden unit
        w2 = QMatrix.quantize(net.w2, bits, per="row")                         # one scale per action
        return cls(net.actions, net.dim, net.hidden, {k: i for i, k in enumerate(keys)}, w1, net.b1, w2, net.b2, backend)

    def predict(self, idx: list[int]) -> dict[str, float]:
        rows = [self.index[i] for i in idx if i in self.index]
        scale = 1.0 / math.sqrt(max(1, len(idx)))
        h = self.backend.gather_sum(self.w1, rows, scale, self.b1)
        h = [v if v > 0 else 0.0 for v in h]
        z = self.backend.matvec(self.w2, h, self.b2)
        m = max(z)
        e = [math.exp(v - m) for v in z]
        s = sum(e)
        return {a: v / s for a, v in zip(self.actions, e)}

    def to_json(self) -> dict:
        return {"format": QFORMAT, "actions": self.actions, "dim": self.dim, "hidden": self.hidden,
                "index": [k for k, _ in sorted(self.index.items(), key=lambda kv: kv[1])],
                "w1": self.w1.to_json(), "b1": [round(x, 5) for x in self.b1],
                "w2": self.w2.to_json(), "b2": [round(x, 5) for x in self.b2]}

    @classmethod
    def from_json(cls, d: dict, backend=None) -> "QuantNetwork":
        if d.get("format") not in QFORMATS:
            raise ValueError("not a quantized controller file")
        return cls(d["actions"], d["dim"], d["hidden"], {k: i for i, k in enumerate(d["index"])},
                   QMatrix.from_json(d["w1"]), d["b1"], QMatrix.from_json(d["w2"]), d["b2"], backend)

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), separators=(",", ":")))


def load_controller_model(path, precision: str = "float", backend=None):
    """A controller file (float or quantized) -> a model with predict().  precision "int8"/"int4"
    quantizes a float file as it loads; a quantized file is used as it is."""
    from .controller import Network
    d = json.loads(Path(path).read_text())
    if d.get("format") in QFORMATS:
        return QuantNetwork.from_json(d, backend)
    net = Network.from_json(d)
    bits = {"float": 0, "int8": 8, "int4": 4}.get(precision)
    if bits is None:
        raise ValueError(f"controller_precision must be float, int8 or int4, not {precision!r}")
    return QuantNetwork.from_network(net, bits, backend) if bits else net


# ---------------------------------------------------------------------- backends

class PyBackend:
    """Plain Python.  Always there, and the reference every other backend is checked against."""
    name = "python"

    def available(self) -> bool:
        return True

    def gather_sum(self, m: QMatrix, rows: list[int], scale: float, bias: list[float]) -> list[float]:
        if not rows:
            return list(bias)
        if m.per == "col":         # integer column sums (exact, and the loop runs in C), one multiply per column
            acc = map(sum, zip(*(m.q[r] for r in rows)))
            return [b + scale * s * a for b, s, a in zip(bias, m.scales, acc)]
        out = list(bias)
        for r in rows:
            s = scale * m.scales[r]
            for j, v in enumerate(m.q[r]):
                out[j] += v * s
        return out

    def matvec(self, m: QMatrix, x: list[float], bias: list[float]) -> list[float]:
        if m.per == "col":
            x = [xi * s for xi, s in zip(x, m.scales)]
            return [b + sum(map(operator.mul, row, x)) for row, b in zip(m.q, bias)]
        return [b + s * sum(map(operator.mul, row, x)) for row, s, b in zip(m.q, m.scales, bias)]


class NumpyBackend:
    """numpy, when it's installed and asked for (compute_backend = "numpy")."""
    name = "numpy"

    def __init__(self):
        self.np = importlib.import_module("numpy")
        self._cache: dict[int, tuple] = {}

    def available(self) -> bool:
        return True

    def _arrays(self, m: QMatrix):
        a = self._cache.get(id(m))
        if a is None or a[0] is not m:
            np = self.np
            q = np.frombuffer(b"".join(r.tobytes() for r in m.q), dtype=np.int8).reshape(m.rows, m.cols)
            a = (m, q, np.array(m.scales, dtype=np.float32))
            self._cache[id(m)] = a
        return a[1], a[2]

    def gather_sum(self, m, rows, scale, bias):
        np = self.np
        if not rows:
            return list(bias)
        q, s = self._arrays(m)
        if m.per == "col":
            acc = q[rows].sum(0, dtype=np.int32).astype(np.float32) * s * scale
        else:
            acc = (q[rows].astype(np.float32) * s[rows, None]).sum(0) * scale
        return (acc + np.asarray(bias, dtype=np.float32)).tolist()

    def matvec(self, m, x, bias):
        np = self.np
        q, s = self._arrays(m)
        x = np.asarray(x, dtype=np.float32)
        if m.per == "col":
            return (q.astype(np.float32) @ (x * s) + np.asarray(bias, dtype=np.float32)).tolist()
        return ((q.astype(np.float32) @ x) * s + np.asarray(bias, dtype=np.float32)).tolist()


class CCPUBackend:
    """The CCPU (FPGA) backend.  The engine doesn't talk to hardware itself; it loads *your* driver:

        compute_ccpu_driver = "ccpu_driver:Driver"      # module:class, importable from the computer folder

    and the driver implements these two calls, on the packed int8 layout (QMatrix.packed(): row-major,
    signed bytes).  Only int8 matrices are sent; int4, and any other layout, stays on the CPU.

        gather_sum_int8(blob, rows, cols, scales, row_ids, scale, bias) -> list[float]
            scales: one per COLUMN.  out[j] = bias[j] + scale * scales[j] * sum(q[r][j] for r in row_ids)
            (an integer accumulate, then one multiply per column: a good fit for DSP slices)
        matvec_int8(blob, rows, cols, scales, x, bias)                  -> list[float]
            scales: one per ROW.     out[i] = bias[i] + scales[i] * sum(q[i][j] * x[j] for j)

    The driver is free to cache a matrix on the device by blob identity.  Until validate() has passed,
    the engine won't route anything to it."""
    name = "ccpu"

    def __init__(self, driver: str = ""):
        self.driver_spec = driver
        self.driver = None
        self.error = ""
        self.validated = False
        self._blobs: dict[int, tuple] = {}
        if not driver:
            self.error = "no compute_ccpu_driver set"
            return
        try:
            mod, _, cls = driver.partition(":")
            self.driver = getattr(importlib.import_module(mod), cls or "Driver")()
        except Exception as e:  # noqa: BLE001 - a missing or broken driver just means "not available"
            self.error = f"driver {driver!r} not loaded: {e}"

    def available(self) -> bool:
        return self.driver is not None

    def _blob(self, m: QMatrix) -> bytes:
        b = self._blobs.get(id(m))
        if b is None or b[0] is not m:
            b = (m, m.packed())
            self._blobs[id(m)] = b
        return b[1]

    def gather_sum(self, m, rows, scale, bias):
        if m.bits != 8 or m.per != "col":
            return PyBackend().gather_sum(m, rows, scale, bias)
        return list(self.driver.gather_sum_int8(self._blob(m), m.rows, m.cols, m.scales, rows, scale, list(bias)))

    def matvec(self, m, x, bias):
        if m.bits != 8 or m.per != "row":
            return PyBackend().matvec(m, x, bias)
        return list(self.driver.matvec_int8(self._blob(m), m.rows, m.cols, m.scales, list(x), list(bias)))


def validate(candidate, reference=None, cases: int = 50, tol: float = 1e-3, seed: int = 0) -> dict:
    """Does `candidate` compute what the reference does, and is it actually faster, end to end?

    Random int8 matrices shaped like the controller's (rows of 32, a 24x32 output layer).  Times are wall
    clock around each call, so a device's transfers and readback are counted."""
    import random
    reference = reference or PyBackend()
    rng = random.Random(seed)
    w1 = QMatrix.quantize([[rng.gauss(0, 0.3) for _ in range(32)] for _ in range(400)], 8, per="col")
    w2 = QMatrix.quantize([[rng.gauss(0, 0.3) for _ in range(32)] for _ in range(24)], 8)
    b1, b2 = [rng.gauss(0, 0.1) for _ in range(32)], [rng.gauss(0, 0.1) for _ in range(24)]
    err, t_ref, t_cand = 0.0, 0.0, 0.0
    for _ in range(cases):
        rows = rng.sample(range(w1.rows), rng.randint(1, 40))
        x = [max(0.0, rng.gauss(0, 1)) for _ in range(32)]
        for op, args in (("gather_sum", (w1, rows, 0.2, b1)), ("matvec", (w2, x, b2))):
            t = time.perf_counter()
            want = getattr(reference, op)(*args)
            t_ref += time.perf_counter() - t
            t = time.perf_counter()
            got = getattr(candidate, op)(*args)
            t_cand += time.perf_counter() - t
            err = max(err, max(abs(a - b) for a, b in zip(want, got)) if len(want) == len(got) else math.inf)
    ok = err <= tol
    if hasattr(candidate, "validated"):
        candidate.validated = ok
    return {"backend": candidate.name, "ok": ok, "max_error": err, "ref_us": t_ref / cases / 2 * 1e6,
            "us": t_cand / cases / 2 * 1e6, "speedup": (t_ref / t_cand) if t_cand else 0.0}


def pick_backend(name: str = "auto", ccpu_driver: str = ""):
    """(backend, why).  "auto": a CCPU that validates, else Python.  numpy only when asked for: it is a few
    times faster per question on a NUC, but importing it costs tens of MB the Zero 2 W needs for Vosk and
    the LM, and the controller already takes well under a millisecond (bench both on your machine)."""
    if name in ("auto", "ccpu"):
        ccpu = CCPUBackend(ccpu_driver)
        if ccpu.available():
            v = validate(ccpu)
            if v["ok"]:
                return ccpu, f"ccpu validated (max error {v['max_error']:.1e}, {v['speedup']:.2f}x the Python backend)"
            why = f"ccpu failed validation (max error {v['max_error']:.2e}): using python"
        else:
            why = f"ccpu not available ({ccpu.error})" if name == "ccpu" else "python"
        return PyBackend(), why
    if name == "numpy":
        try:
            return NumpyBackend(), "numpy"
        except ImportError:
            return PyBackend(), "numpy not installed: using python"
    if name != "python":
        return PyBackend(), f"unknown backend {name!r}: using python"
    return PyBackend(), "python"


# ---------------------------------------------------------------------- profiles

@dataclass(frozen=True)
class Profile:
    name: str
    nav_limit: int           # plans navigate() may run for one question
    ctl_actions: int         # controller: top actions tried
    ctl_plans: int           # controller: plans it may hand over
    lm_wording: bool         # wake the mini LM to word instinct replies
    lm_planner: bool         # let an LM plan or propose (navigator_planner lm/propose/direct)
    lm_tokens: float         # max_tokens = llm_max_words * this
    llm_threads: int         # llama-server -t  (0 = every core)   } applied when llama-server starts
    llm_ctx: int             # llama-server -c                     }
    note: str


PROFILES = {
    "eco": Profile("eco", 4, 1, 2, False, False, 1.2, 2, 512,
                   "rules and template output; the mini LM isn't woken"),
    "balanced": Profile("balanced", 8, 3, 4, True, True, 1.8, 4, 1024,
                        "what the computer always did"),
    "priority": Profile("priority", 8, 3, 4, True, True, 2.2, 0, 2048,
                        "the LM gets every core, a longer reply, and llm_endpoint_priority if set"),
}


def llama_args(profile: str | Profile, cores: int | None = None) -> list[str]:
    p = PROFILES[profile] if isinstance(profile, str) else profile
    t = p.llm_threads or cores or os.cpu_count() or 4
    return ["-t", str(t), "-c", str(p.llm_ctx)]


# ---------------------------------------------------------------------- sensors and the auto policy

@dataclass
class Reading:
    temp_c: float | None = None
    load: float | None = None             # 1-minute load average per core
    on_battery: bool | None = None
    battery_pct: float | None = None


class Sensors:
    """What Linux tells us without extra hardware.  Anything missing is None (a power bank has no gauge)."""

    def __init__(self, root: str = "/sys"):
        self.root = Path(root)

    def read(self) -> Reading:
        r = Reading()
        try:
            r.temp_c = int((self.root / "class/thermal/thermal_zone0/temp").read_text()) / 1000
        except (OSError, ValueError):
            pass
        try:
            r.load = os.getloadavg()[0] / (os.cpu_count() or 1)
        except OSError:
            pass
        for ps in sorted((self.root / "class/power_supply").glob("*")) if (self.root / "class/power_supply").is_dir() else []:
            try:
                kind = (ps / "type").read_text().strip()
                if kind == "Mains":
                    r.on_battery = (ps / "online").read_text().strip() == "0"
                elif kind == "Battery" and (ps / "capacity").exists():
                    r.battery_pct = float((ps / "capacity").read_text())
            except (OSError, ValueError):
                pass
        return r


class Policy:
    """Which profile for this turn.  A fixed profile is just returned.  "auto" moves between eco and
    balanced (never priority, which you ask for) with hysteresis, so a board at 69-71 °C doesn't flap:
    eco when hot, busy or on a low battery; back to balanced once it has cooled and calmed down."""

    def __init__(self, mode: str = "balanced", hot_c: float = 70.0, cool_c: float = 62.0, busy: float = 0.85,
                 low_battery: float = 20.0, sensors: Sensors | None = None):
        if mode not in (*PROFILES, "auto"):
            raise ValueError(f"compute_profile must be one of {', '.join([*PROFILES, 'auto'])}, not {mode!r}")
        self.mode, self.hot_c, self.cool_c, self.busy, self.low_battery = mode, hot_c, cool_c, busy, low_battery
        self.sensors = sensors or Sensors()
        self.state = "balanced"

    def choose(self) -> tuple[Profile, str]:
        if self.mode != "auto":
            return PROFILES[self.mode], "set"
        s = self.sensors.read()
        hot = s.temp_c is not None and s.temp_c >= self.hot_c
        busy = s.load is not None and s.load >= self.busy
        low = bool(s.on_battery) and s.battery_pct is not None and s.battery_pct < self.low_battery
        facts = ", ".join(x for x in (f"{s.temp_c:.0f}°C" if s.temp_c is not None else "",
                                      f"load {s.load:.2f}/core" if s.load is not None else "",
                                      f"battery {s.battery_pct:.0f}%" if s.on_battery else "") if x)
        if hot or busy or low:
            self.state = "eco"
            why = "hot" if hot else "busy" if busy else "low battery"
        elif self.state == "eco":
            calm = (s.temp_c is None or s.temp_c < self.cool_c) and (s.load is None or s.load < self.busy * 0.7)
            if calm:
                self.state, why = "balanced", "cooled down"
            else:
                why = "still cooling"
        else:
            why = "cool and idle"
        return PROFILES[self.state], f"auto: {why}" + (f" ({facts})" if facts else "")


# ---------------------------------------------------------------------- measuring

RAPL = "class/powercap/intel-rapl:0"


class Meter:
    """Per-turn cost: wall time, this process's CPU time, and package energy when the machine reports it
    (Intel RAPL on a NUC; usually root-readable only: `sudo chmod o+r /sys/class/powercap/intel-rapl:0/energy_uj`).
    The Pi reports no energy, so measure it with a USB power meter and compare the profiles' CPU time."""

    def __init__(self, root: str = "/sys"):
        self.rapl = Path(root) / RAPL
        self.energy_ok = self._energy() is not None

    def _energy(self) -> int | None:
        try:
            return int((self.rapl / "energy_uj").read_text())
        except (OSError, ValueError):
            return None

    def start(self) -> tuple:
        return time.perf_counter(), time.process_time(), self._energy() if self.energy_ok else None

    def stop(self, tok: tuple, lm: dict | None = None) -> dict:
        wall, cpu, e0 = tok
        out = {"ms": round((time.perf_counter() - wall) * 1000, 1), "cpu_ms": round((time.process_time() - cpu) * 1000, 1)}
        if e0 is not None:
            e1 = self._energy()
            if e1 is not None:
                if e1 < e0:      # the counter wrapped
                    try:
                        e1 += int((self.rapl / "max_energy_range_uj").read_text())
                    except (OSError, ValueError):
                        e1 = e0
                out["energy_j"] = round((e1 - e0) / 1e6, 3)
        if lm:
            out["lm"] = lm
        return out


# ---------------------------------------------------------------------- the engine

@dataclass
class Engine:
    profile: str = "balanced"
    backend: str = "auto"
    precision: str = "float"
    ccpu_driver: str = ""
    hot_c: float = 70.0
    cool_c: float = 62.0
    sensors: Sensors | None = None
    meter_root: str = "/sys"
    _backend: object = field(default=None, repr=False)
    backend_note: str = ""

    def __post_init__(self):
        self.policy = Policy(self.profile, self.hot_c, self.cool_c, sensors=self.sensors)
        self._backend, self.backend_note = pick_backend(self.backend, self.ccpu_driver)
        self.meter = Meter(self.meter_root)

    @classmethod
    def from_config(cls, cfg, **kw) -> "Engine":
        return cls(cfg.compute_profile, cfg.compute_backend, cfg.controller_precision, cfg.compute_ccpu_driver,
                   cfg.compute_hot_c, cfg.compute_cool_c, **kw)

    @property
    def backend_impl(self):
        return self._backend

    def set_profile(self, mode: str) -> None:
        self.policy = Policy(mode, self.hot_c, self.cool_c, sensors=self.policy.sensors)
        self.profile = mode

    def choose(self) -> tuple[Profile, str]:
        return self.policy.choose()

    def load_controller(self, path):
        return load_controller_model(path, self.precision, self._backend)

    def status(self) -> str:
        p = self.profile
        return (f"profile {p}{' (' + PROFILES[p].note + ')' if p in PROFILES else ''}; "
                f"controller {self.precision}; backend {self.backend_note}; "
                f"energy {'RAPL' if self.meter.energy_ok else 'not reported here'}")


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m computer.compute", description="The CDOS compute engine: status and llama-server flags.")
    ap.add_argument("--llama-args", metavar="PROFILE", choices=list(PROFILES),
                    help="print llama-server's -t/-c for a profile (paste into systemd/computer-llm.service)")
    ap.add_argument("--validate", metavar="DRIVER", help="check a CCPU driver (module:Class) against the Python backend")
    a = ap.parse_args(argv)
    if a.llama_args:
        print(" ".join(llama_args(a.llama_args)))
        return 0
    if a.validate:
        b = CCPUBackend(a.validate)
        if not b.available():
            print(b.error)
            return 1
        print(json.dumps(validate(b), indent=1))
        return 0
    from . import config as config_mod
    cfg = config_mod.load()
    e = Engine.from_config(cfg)
    print(e.status())
    print(f"sensors: {json.dumps(asdict(e.policy.sensors.read()))}")
    for name in PROFILES:
        print(f"  {name:<9} llama-server {' '.join(llama_args(name))}   {PROFILES[name].note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
