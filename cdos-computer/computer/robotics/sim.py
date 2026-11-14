"""A simulated differential-drive base in a square room with round obstacles.  The only thing in this package that
executes a command.  Seeded: the same seed gives the same room, start, goal and sensor noise.

    world = SimWorld(seed)
    world.observe()        -> observations (robot.observation@1.0.0); motor temperature can come as a raw TMP117
                              register for the sensor.thermometer plugin to convert (raw_temperature=True)
    world.apply(command)   moves the robot along the commanded motion (only ever an approved one, or STOP)
    world.status()         reached / collided / out_of_bounds / timeout, time, distance to goal
"""

from __future__ import annotations

import math
import random

from computer.robotics.world import STEP_S, step

ROOM = 4.5            # the room is [-ROOM, ROOM] square; the envelope's workspace is checked separately
RADIUS = 0.15


class SimWorld:
    def __init__(self, seed: int = 0, n_obstacles: int = 4, time_limit: float = 60.0, raw_temperature: bool = False,
                 noise: float = 0.005):
        rng = random.Random(seed)
        self.rng, self.noise, self.raw_temperature = rng, noise, raw_temperature
        self.t, self.time_limit = 0.0, time_limit
        pick = lambda: (rng.uniform(-3.5, 3.5), rng.uniform(-3.5, 3.5))   # noqa: E731
        self.x, self.y = pick()
        self.goal = pick()
        while math.hypot(self.goal[0] - self.x, self.goal[1] - self.y) < 2.5:
            self.goal = pick()
        self.h = rng.uniform(-math.pi, math.pi)
        self.v = self.w = 0.0
        self.battery, self.temp = 12.4, 30.0
        self.obstacles = []
        tries = 0
        while len(self.obstacles) < n_obstacles and tries < 500:
            tries += 1
            ox, oy, r = rng.uniform(-3.5, 3.5), rng.uniform(-3.5, 3.5), rng.uniform(0.2, 0.45)
            if math.hypot(ox - self.x, oy - self.y) < r + 0.6 or math.hypot(ox - self.goal[0], oy - self.goal[1]) < r + 0.5:
                continue
            if any(math.hypot(ox - a, oy - b) < r + c + 0.3 for a, b, c in self.obstacles):
                continue
            self.obstacles.append((ox, oy, r))
        self.collided = self.out_of_bounds = False
        self.distance = 0.0

    def _n(self, sigma: float) -> float:
        return self.rng.gauss(0.0, sigma) if sigma else 0.0

    def observe(self) -> list[dict]:
        t, n = round(self.t, 4), self.noise
        o = lambda sensor, q, v, unit, conf=0.98: {"sensor": sensor, "quantity": q, "value": v, "unit": unit, "t": t,   # noqa: E731
                                                     "confidence": conf}
        obs = [o("odom", "pose_x", [self.x + self._n(n)], "m"), o("odom", "pose_y", [self.y + self._n(n)], "m"),
               o("imu", "heading", [self.h + self._n(n)], "rad"), o("odom", "speed", [self.v], "m/s"),
               o("imu", "yaw_rate", [self.w], "rad/s"), o("bms", "battery_voltage", [round(self.battery, 3)], "V")]
        if self.raw_temperature:
            obs.append({"sensor": "sensor.thermometer", "quantity": "motor_temp", "raw": int(round(self.temp / 0.0078125)),
                        "t": t, "confidence": 0.99})
        else:
            obs.append(o("thermo", "motor_temp", [round(self.temp, 3)], "degC"))
        obs += [o("lidar", "obstacle", [ox, oy, r], "m", 0.95) for ox, oy, r in self.obstacles]
        return obs

    def apply(self, cmd: dict) -> None:
        dur = max(float(cmd.get("duration", 0.0)), 0.0)
        v, w = float(cmd.get("v", 0.0)), float(cmd.get("omega", 0.0))
        if v == 0 and w == 0:
            dur = max(dur, 0.1)          # a STOP still lets time pass
        steps = max(1, int(round(dur / STEP_S)))
        for _ in range(steps):
            nx, ny, self.h = step(self.x, self.y, self.h, v, w, STEP_S)
            self.distance += math.hypot(nx - self.x, ny - self.y)
            self.x, self.y = nx, ny
            if any(math.hypot(self.x - ox, self.y - oy) < r + RADIUS for ox, oy, r in self.obstacles):
                self.collided = True
            if abs(self.x) > ROOM - RADIUS or abs(self.y) > ROOM - RADIUS:
                self.out_of_bounds = True
        self.v, self.w = v, w
        self.t += steps * STEP_S
        self.battery -= 0.0005 * steps * STEP_S * (1 + abs(v))
        self.temp += steps * STEP_S * (2.0 * abs(v) - 0.02 * (self.temp - 30.0))

    def status(self) -> dict:
        d = math.hypot(self.goal[0] - self.x, self.goal[1] - self.y)
        state = ("collided" if self.collided else "out_of_bounds" if self.out_of_bounds else "reached" if d < 0.15
                 else "timeout" if self.t >= self.time_limit else "running")
        return {"status": state, "t": round(self.t, 3), "to_goal": round(d, 4), "distance": round(self.distance, 3)}
