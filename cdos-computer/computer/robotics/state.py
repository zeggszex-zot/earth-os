"""Observations -> robot state.  Deterministic: the receiving time is an input, never read from the clock here.

An observation (robot.observation@1.0.0):

    {"sensor": "imu0", "quantity": "speed", "value": [0.21], "unit": "m/s", "t": 12.5, "confidence": 0.98}

    quantity         unit    value
    pose_x, pose_y   m       [x]            position in the workspace frame
    heading          rad     [theta]
    speed            m/s     [v]            forward speed
    yaw_rate         rad/s   [omega]
    battery_voltage  V       [volts]
    motor_temp       degC    [degrees]
    obstacle         m       [x, y, radius] one circular obstacle (several observations for several obstacles)

A unit other than the quantity's is refused, not converted: a sensor that reports in the wrong unit is misconfigured.
The state keeps, per quantity, the latest reading and the time the controller received it; the envelope decides what
is too old or too uncertain.  Obstacles are those of the latest obstacle scan (the newest `t` among them).
"""

from __future__ import annotations

from dataclasses import dataclass

from computer.neural.components import Field, IOSpec

QUANTITIES = {"pose_x": ("m", 1), "pose_y": ("m", 1), "heading": ("rad", 1), "speed": ("m/s", 1),
              "yaw_rate": ("rad/s", 1), "battery_voltage": ("V", 1), "motor_temp": ("degC", 1), "obstacle": ("m", 3)}
REQUIRED = ("pose_x", "pose_y", "heading", "speed", "yaw_rate", "battery_voltage", "motor_temp")

OBSERVATION = IOSpec("robot.observation", "1.0.0", (
    Field("sensor", "str", doc="which sensor (or sensor component) produced it"),
    Field("quantity", "str", choices=tuple(QUANTITIES)),
    Field("value", "list[float]", min=-1e6, max=1e6),
    Field("unit", "str"),
    Field("t", "float", min=0.0, unit="s", doc="the sensor's own timestamp"),
    Field("confidence", "float", min=0.0, max=1.0)))


class ObservationError(ValueError):
    pass


def check_observation(o: dict) -> dict:
    OBSERVATION.validate(o)
    unit, n = QUANTITIES[o["quantity"]]
    if o["unit"] != unit:
        raise ObservationError(f"{o['quantity']} is in {unit}, not {o['unit']!r}: a misconfigured sensor isn't converted")
    if len(o["value"]) != n:
        raise ObservationError(f"{o['quantity']} has {n} value(s), not {len(o['value'])}")
    if o["quantity"] == "obstacle" and o["value"][2] <= 0:
        raise ObservationError("an obstacle's radius must be positive")
    return o


@dataclass(frozen=True)
class RobotState:
    x: float | None
    y: float | None
    heading: float | None
    speed: float | None
    yaw_rate: float | None
    battery_v: float | None
    motor_temp_c: float | None
    obstacles: tuple                     # ((x, y, r), ...)
    received_oldest: float | None        # controller time the oldest required reading arrived
    confidence: float                    # the lowest confidence among the required readings
    missing: tuple                       # required quantities with no reading
    e_stop: bool

    @property
    def complete(self) -> bool:
        return not self.missing

    def summary(self) -> dict:
        return {"x": self.x, "y": self.y, "heading": self.heading, "speed": self.speed, "yaw_rate": self.yaw_rate,
                "battery_v": self.battery_v, "motor_temp_c": self.motor_temp_c,
                "obstacles": [list(o) for o in self.obstacles], "received_oldest": self.received_oldest,
                "confidence": self.confidence, "missing": list(self.missing), "e_stop": self.e_stop}


def build_state(latest: dict, obstacles: list, e_stop: bool = False) -> RobotState:
    """latest: quantity -> (observation, received_at); obstacles: [(observation, received_at)] of the newest scan."""
    val = lambda q: latest[q][0]["value"][0] if q in latest else None   # noqa: E731
    req = [latest[q] for q in REQUIRED if q in latest]
    return RobotState(val("pose_x"), val("pose_y"), val("heading"), val("speed"), val("yaw_rate"),
                      val("battery_voltage"), val("motor_temp"),
                      tuple(tuple(o["value"]) for o, _ in obstacles),
                      min((r for _, r in req), default=None),
                      min((o["confidence"] for o, _ in req), default=0.0),
                      tuple(q for q in REQUIRED if q not in latest), bool(e_stop))
