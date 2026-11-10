"""CDOS robotics, simulation only: sensor state in, a candidate motion out, and a deterministic safety envelope between.

    observations ──► state.py  build_state(): latest reading per quantity, units checked, age and confidence kept
                        │       (a raw sensor value goes through its sensor component first, e.g. a plugin)
                        ▼
    policy.py  a component proposes a motion (robot.policy: learned; robot.policy_heuristic: a naive baseline)
                        │
                        ▼
    safety.py  Envelope.check(): every limit in computer/data/robot_safety.json, checked against the state and the
               motion's predicted path (world.py).  Any violation: rejected, and the command is STOP.  Missing or old
               data is a violation, never a guess.  Stopping is always allowed.
                        │
                        ▼
    controller.py  the decision (candidate, verdict, violations, command) goes to an append-only journal; only an
                   approved command is ever returned as something to execute
    sim.py         a simulated base, sensors and obstacles: the only thing that executes commands here

There is no hardware driver in this package, and nothing in it opens a device or the network.  The envelope is
independent of the policy: the policy's output is a proposal like any other input.  For a physical robot, the design
note (docs/PLATFORM_0_2.md) keeps hard real-time limits and the emergency stop in a microcontroller that enforces
them on its own; this envelope is the higher-level check, not a replacement for that.
"""
