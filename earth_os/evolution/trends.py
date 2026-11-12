"""The Performance Analyzer: how the machine has behaved over time.

A fixed-size ring of small samples, in memory. Nothing is read here — the
center hands in samples it has already taken — and nothing is kept past the
end of the session. What comes out is arithmetic on those samples, and every
figure says how many samples and how long a span it rests on, because *"mean
processor load 41%"* means something different over four samples than over
four hundred.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

#: The fields a sample may carry. A field a sample has no value for is None,
#: and is left out of every figure rather than counted as zero.
FIELDS = ("cpu", "memory", "memory_used", "swap_used", "disk", "temperature",
          "processes", "own_memory", "frames_drawn", "draw_ms")


@dataclass(frozen=True)
class Sample:
    at: float                       # monotonic seconds
    wall: float                     # wall-clock seconds, for display
    values: Dict[str, Optional[float]]
    visible: bool = True

    def get(self, name: str) -> Optional[float]:
        return self.values.get(name)


@dataclass(frozen=True)
class Stat:
    """A field over a span: lowest, mean, highest, first and latest."""

    name: str
    count: int
    span: float
    low: float
    mean: float
    high: float
    first: float
    last: float


@dataclass(frozen=True)
class Growth:
    """A field that has kept rising, and by how much.

    ``steady`` is the part that makes this a trend rather than a difference:
    the samples were cut into thirds in time order, and each third's mean was
    at least the one before it. Something that rose and fell back is not
    reported as growing.
    """

    name: str
    count: int
    span: float
    start: float                    # mean of the first third
    end: float                      # mean of the last third
    steady: bool

    @property
    def delta(self) -> float:
        return self.end - self.start

    @property
    def per_hour(self) -> float:
        return self.delta / self.span * 3600.0 if self.span > 0 else 0.0


class Trends:
    """A ring buffer of samples and the figures that can be drawn from it."""

    def __init__(self, capacity: int = 360):
        self.capacity = max(int(capacity), 8)
        self._samples: Deque[Sample] = deque(maxlen=self.capacity)

    def __len__(self) -> int:
        return len(self._samples)

    def add(self, sample: Sample) -> None:
        self._samples.append(sample)

    def clear(self) -> None:
        self._samples.clear()

    def samples(self, seconds: float = 0.0) -> List[Sample]:
        """Every sample, or those within the last *seconds*."""
        if not self._samples or seconds <= 0:
            return list(self._samples)
        cutoff = self._samples[-1].at - seconds
        return [s for s in self._samples if s.at >= cutoff]

    def latest(self) -> Optional[Sample]:
        return self._samples[-1] if self._samples else None

    def span(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        return self._samples[-1].at - self._samples[0].at

    def series(self, name: str, seconds: float = 0.0) -> List[float]:
        return [v for v in (s.get(name) for s in self.samples(seconds)) if v is not None]

    def stat(self, name: str, seconds: float = 0.0) -> Optional[Stat]:
        """Figures for *name*, or None when no sample carries it."""
        chosen = [s for s in self.samples(seconds) if s.get(name) is not None]
        if not chosen:
            return None
        values = [float(s.get(name)) for s in chosen]       # type: ignore[arg-type]
        return Stat(name=name, count=len(values), span=chosen[-1].at - chosen[0].at,
                    low=min(values), mean=sum(values) / len(values), high=max(values),
                    first=values[0], last=values[-1])

    def growth(self, name: str, min_samples: int = 9,
               min_span: float = 300.0) -> Optional[Growth]:
        """Whether *name* has kept rising — or None if there is too little to say.

        None is the answer for "not enough samples" and "not a long enough
        span" alike. A leak cannot be called from three readings a minute
        apart, and saying so is more use than a slope drawn through noise.
        """
        chosen = [s for s in self._samples if s.get(name) is not None]
        if len(chosen) < max(min_samples, 3):
            return None
        span = chosen[-1].at - chosen[0].at
        if span < min_span:
            return None
        third = len(chosen) // 3
        parts = (chosen[:third], chosen[third:len(chosen) - third], chosen[len(chosen) - third:])
        means = [sum(float(s.get(name)) for s in part) / len(part)      # type: ignore[arg-type]
                 for part in parts if part]
        steady = all(later >= earlier for earlier, later in zip(means, means[1:]))
        return Growth(name=name, count=len(chosen), span=span,
                      start=means[0], end=means[-1], steady=steady)

    def rate(self, name: str, seconds: float = 0.0) -> Optional[float]:
        """Change in a counter per second over the span, or None.

        For things that only count up — frames drawn. A counter that went
        *down* was reset (the window was rebuilt), and that span is skipped
        rather than reported as a negative rate.
        """
        chosen = [s for s in self.samples(seconds) if s.get(name) is not None]
        if len(chosen) < 2:
            return None
        elapsed = chosen[-1].at - chosen[0].at
        change = float(chosen[-1].get(name)) - float(chosen[0].get(name))   # type: ignore[arg-type]
        if elapsed <= 0 or change < 0:
            return None
        return change / elapsed
