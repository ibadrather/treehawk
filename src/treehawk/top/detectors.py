"""Spotting trouble while it happens, over every process on the machine.

Both detectors keep a small, fixed amount of state per key and do constant work
per reading, so they run over the whole process table - not just the top N. A
process that starts leaking is worth flagging long before it is big enough to
rank.

Keys are whatever the caller tracks: a process identity, or a name for the
machine as a whole.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from treehawk.top.models import Creep, CreepRule, Spike, SpikeRule

K = TypeVar("K", bound=Hashable)


@dataclass(slots=True)
class _Baseline:
    mean: float
    variance: float = 0.0
    first_t: float = 0.0
    last_t: float = 0.0
    quiet_until: float = 0.0


class SpikeDetector(Generic[K]):
    """Flags a reading far above that key's own recent behaviour.

    The baseline is an exponentially weighted mean and variance whose weight
    follows elapsed time, not sample count, so the same rule means the same
    thing at 10 Hz and at 0.2 Hz.
    """

    def __init__(self, rule: SpikeRule) -> None:
        self._rule = rule
        self._state: dict[K, _Baseline] = {}

    def observe(self, *, key: K, value: float, t: float) -> Spike | None:
        state = self._state.get(key)
        if state is None:
            self._state[key] = _Baseline(mean=value, first_t=t, last_t=t)
            return None
        dt = max(t - state.last_t, 0.0)
        spike = self._judge(state=state, value=value, t=t)
        alpha = 1.0 - math.exp(-dt / self._rule.time_constant) if dt > 0 else 0.0
        difference = value - state.mean
        increment = alpha * difference
        state.mean += increment
        state.variance = (1.0 - alpha) * (state.variance + difference * increment)
        state.last_t = t
        return spike

    def _judge(self, *, state: _Baseline, value: float, t: float) -> Spike | None:
        rule = self._rule
        if t - state.first_t < rule.warmup or t < state.quiet_until:
            return None
        deviation = value - state.mean
        if deviation < rule.floor or deviation < rule.factor * math.sqrt(state.variance):
            return None
        state.quiet_until = t + rule.cooldown
        return Spike(value=value, baseline=state.mean)

    def forget(self, keys: Iterable[K]) -> None:
        for key in keys:
            self._state.pop(key, None)

    def __len__(self) -> int:
        return len(self._state)


@dataclass(slots=True)
class _Trend:
    bucket_start: float
    bucket_floor: float
    points: deque[tuple[float, float]] = field(default_factory=deque)
    reported_at: float | None = None
    """The floor at the last report, so a key is not reported every bucket."""


class CreepDetector(Generic[K]):
    """Flags memory that keeps rising in a straight line: a leak suspect."""

    def __init__(self, rule: CreepRule) -> None:
        self._rule = rule
        self._state: dict[K, _Trend] = {}

    def observe(self, *, key: K, value: float, t: float) -> Creep | None:
        state = self._state.get(key)
        if state is None:
            self._state[key] = _Trend(
                bucket_start=t,
                bucket_floor=value,
                points=deque(maxlen=self._rule.window),
            )
            return None
        if t - state.bucket_start < self._rule.bucket_seconds:
            state.bucket_floor = min(state.bucket_floor, value)
            return None
        state.points.append((state.bucket_start, state.bucket_floor))
        state.bucket_start = t
        state.bucket_floor = value
        return self._judge(state)

    def _judge(self, state: _Trend) -> Creep | None:
        rule = self._rule
        if len(state.points) < rule.window:
            return None
        fit = _fit_line(state.points)
        if fit is None:
            return None
        slope, r2 = fit
        first, last = state.points[0][1], state.points[-1][1]
        slope_per_hour = slope * 3600.0
        if slope_per_hour < rule.min_slope_per_hour or r2 < rule.min_r2 or last - first < rule.min_growth:
            return None
        if state.reported_at is not None and last < state.reported_at * (1.0 + rule.refire_growth):
            return None
        state.reported_at = last
        return Creep(slope_per_hour=slope_per_hour, r2=r2, first=first, last=last)

    def forget(self, keys: Iterable[K]) -> None:
        for key in keys:
            self._state.pop(key, None)

    def __len__(self) -> int:
        return len(self._state)


def _fit_line(points: Iterable[tuple[float, float]]) -> tuple[float, float] | None:
    """Least-squares ``(slope, r2)`` through ``points``, or None if it is degenerate."""
    xs: list[float] = []
    ys: list[float] = []
    for x, y in points:
        xs.append(x)
        ys.append(y)
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    if sxx == 0 or syy == 0:
        return None
    return sxy / sxx, (sxy * sxy) / (sxx * syy)
