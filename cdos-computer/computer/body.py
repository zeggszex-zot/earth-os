"""Device hardware: a push-to-talk button, a tilt switch, two status LEDs.

Uses gpiozero on the Pi.  Anywhere else (your laptop) it quietly becomes a
body with no hardware, so the same code runs for testing.

The shake is part of the cast: the nanosecond timings of the tilt switch
flipping are mixed into the sampler's entropy, so how the device is shaken
really does go into the random cast.  (It never affects facts or calculations.)
"""

from __future__ import annotations

import threading
import time

try:
    from gpiozero import Button, PWMLED  # type: ignore
    HAVE_GPIO = True
except Exception:  # noqa: BLE001 - not on a Pi, or no GPIO access
    HAVE_GPIO = False

# Status-LED brightness 0..1 per state, and the LED pattern.
MOOD_EYES = {
    "neutral": (0.5, "steady"), "happy": (0.9, "steady"), "excited": (1.0, "flicker"),
    "laughing": (1.0, "flicker"), "curious": (0.8, "pulse"), "thinking": (0.6, "pulse"),
    "surprised": (1.0, "steady"), "concerned": (0.3, "steady"), "sad": (0.2, "steady"),
    "wink": (0.9, "wink"),
}


class Body:
    def __init__(self, cfg):
        self.cfg = cfg
        self.shakes: list[int] = []
        self._lock = threading.Lock()
        self.button = self.tilt = self.left = self.right = None
        if HAVE_GPIO:
            try:
                self.button = Button(cfg.gpio_button, pull_up=True, bounce_time=0.03)
                self.tilt = Button(cfg.gpio_tilt, pull_up=True, bounce_time=None)
                self.tilt.when_pressed = self._flip
                self.tilt.when_released = self._flip
                self.left = PWMLED(cfg.gpio_eye_left)
                self.right = PWMLED(cfg.gpio_eye_right)
            except Exception as e:  # noqa: BLE001
                print(f"[body] GPIO not available ({e}); running without hardware")
                self.button = self.tilt = self.left = self.right = None

    @property
    def has_hardware(self) -> bool:
        return self.button is not None

    # ---------------------------------------------------------------- shaking

    def _flip(self):
        with self._lock:
            self.shakes.append(time.perf_counter_ns())
            self.shakes = self.shakes[-64:]

    def take_shake(self, window: float = 2.5, minimum: int = 6) -> bytes:
        """Shake timings from the last `window` seconds as bytes, or b'' if it wasn't a real shake."""
        now = time.perf_counter_ns()
        with self._lock:
            recent = [t for t in self.shakes if now - t < window * 1e9]
            if len(recent) < minimum:
                return b""
            self.shakes.clear()
        return b"".join(t.to_bytes(8, "little") for t in recent)

    def wait_for_shake(self, timeout: float = 8.0) -> bytes:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = self.take_shake()
            if got:
                return got
            time.sleep(0.1)
        return b""

    # ---------------------------------------------------------------- status LEDs

    def status_leds(self, mood: str = "neutral"):
        if not self.left:
            return
        level, style = MOOD_EYES.get(mood, MOOD_EYES["neutral"])
        for led in (self.left, self.right):
            led.off()
        if style == "pulse":
            for led in (self.left, self.right):
                led.pulse(fade_in_time=0.8, fade_out_time=0.8)
        elif style == "flicker":
            for led in (self.left, self.right):
                led.blink(on_time=0.08, off_time=0.05, fade_in_time=0, fade_out_time=0)
        elif style == "wink":
            self.right.value = level
            self.left.value = 0
            threading.Timer(0.4, lambda: setattr(self.left, "value", level)).start()
        else:
            self.left.value = self.right.value = level

    eyes = status_leds          # the old name, for scripts written against the computer build

    def show_cast(self, lines: list[int]):
        """Blink out the six lines, bottom to top: short blink = still line, long glow = moving line."""
        if not self.left:
            return
        for v in lines:
            on = 0.6 if v in (6, 9) else 0.15
            for led in (self.left, self.right):
                led.value = 1.0
            time.sleep(on)
            for led in (self.left, self.right):
                led.value = 0.05
            time.sleep(0.2)

    def sleep(self):
        for led in (self.left, self.right):
            if led:
                led.value = 0.05
