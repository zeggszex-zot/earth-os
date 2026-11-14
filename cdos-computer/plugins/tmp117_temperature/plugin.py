"""Example plugin: the TMP117 digital temperature sensor, as a CDOS capability.

The sensor reports a signed 16-bit register; one count is 7.8125 millidegrees Celsius (1/128 degree).  The datasheet
specifies -55 to +150 degrees, accurate to +/-0.1 degree from -20 to +50 and +/-0.15 degree elsewhere in range.  The
register reads 0x8000 (-256 degrees) until the first conversion: that is "no reading", not a temperature.

Nothing here decides anything.  The robot's safety envelope (computer/robotics/safety.py) decides what a motor
temperature allows; this only turns counts into degrees, and says when a value is outside what the part can report.
"""

from computer.neural.components import Component, ComponentSpec, Field, IOSpec

LSB_C = 0.0078125           # degrees Celsius per count
RANGE_C = (-55.0, 150.0)
NO_CONVERSION = -32768      # 0x8000 as a signed 16-bit value


class TMP117(Component):
    spec = ComponentSpec(
        "sensor.thermometer", "1.0.0", "deterministic",
        IOSpec("sensor.raw_register", "1.0.0", (Field("raw", "int", min=-32768, max=32767,
                                                      doc="the temperature register, as a signed 16-bit value"),)),
        IOSpec("sensor.temperature", "1.0.0", (
            Field("temperature_c", "float", unit="degC", doc="the reading in degrees Celsius"),
            Field("valid", "bool", doc="false: no conversion yet, or outside the part's specified range"),
            Field("accuracy_c", "float", unit="degC", min=0.0, doc="the datasheet's accuracy at this temperature"))),
        "TMP117 register counts -> degrees Celsius, with the datasheet's range and accuracy")

    def run(self, inputs: dict) -> dict:
        raw = inputs["raw"]
        t = raw * LSB_C
        valid = raw != NO_CONVERSION and RANGE_C[0] <= t <= RANGE_C[1]
        acc = 0.1 if -20.0 <= t <= 50.0 else 0.15
        return {"temperature_c": t, "valid": valid, "accuracy_c": acc}
