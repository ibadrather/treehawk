"""How long to wait between two samples."""

from __future__ import annotations

from typing import Protocol

from treehawk.core.config import Pacing, TopConfig
from treehawk.top.constants import TOP


class IntervalPolicy(Protocol):
    """Chooses the next interval from what the last sample cost."""

    @property
    def interval(self) -> float:
        """The interval in force now."""
        ...

    def after(self, work_seconds: float) -> float:
        """Record what a sample took, and return the next interval."""
        ...


class FixedInterval:
    """The same interval, whatever a sample costs."""

    def __init__(self, interval: float) -> None:
        self._interval = interval

    @property
    def interval(self) -> float:
        return self._interval

    def after(self, work_seconds: float) -> float:
        del work_seconds  # a fixed cadence does not care
        return self._interval


class AutoInterval:
    """As often as the machine allows.

    Sampling may take ``duty`` of one core: a sample that took 5 ms at a 5%
    duty earns a 100 ms interval. The move towards that target is smoothed, so
    one slow sample does not halve the rate, and bounded at both ends.
    """

    def __init__(
        self,
        *,
        start: float = TOP.default_interval,
        minimum: float = TOP.auto_min_interval,
        maximum: float = TOP.auto_max_interval,
        duty: float = TOP.auto_duty,
        smoothing: float = TOP.auto_smoothing,
    ) -> None:
        self._minimum = minimum
        self._maximum = maximum
        self._duty = duty
        self._smoothing = smoothing
        self._interval = self._clamp(start)

    @property
    def interval(self) -> float:
        return self._interval

    def after(self, work_seconds: float) -> float:
        target = self._clamp(work_seconds / self._duty)
        self._interval = self._clamp(self._interval + self._smoothing * (target - self._interval))
        return self._interval

    def _clamp(self, value: float) -> float:
        return min(max(value, self._minimum), self._maximum)


def build_pacing(config: TopConfig) -> IntervalPolicy:
    if config.pacing is Pacing.AUTO:
        return AutoInterval(start=config.interval)
    return FixedInterval(config.interval)
