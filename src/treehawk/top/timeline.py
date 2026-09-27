"""Series of any length, kept to a fixed number of points.

A machine watched for a month at two samples a second produces five million
samples, and a chart has room for a couple of thousand. Points are put in
time buckets and each bucket keeps its *maximum*, so a one-sample spike
survives however far the view is zoomed out. When there are too many buckets
their width doubles and neighbours merge - in one pass, as the log is read,
without knowing in advance how long it is.

Every series shares one bucket width, so they stay aligned and can be stacked.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)


class Timeline(Generic[K]):
    """Named series over a shared, self-coarsening time axis."""

    def __init__(self, *, limit: int, width: float = 1.0) -> None:
        self._limit = max(limit, 2)
        self._width = width
        self._origin: float | None = None
        self._buckets: set[int] = set()
        self._series: dict[K, dict[int, float]] = {}

    @property
    def width(self) -> float:
        """Seconds each point stands for."""
        return self._width

    def add(self, *, key: K, t: float, value: float) -> None:
        if self._origin is None:
            self._origin = t
        bucket = int((t - self._origin) // self._width)
        self._buckets.add(bucket)
        points = self._series.setdefault(key, {})
        previous = points.get(bucket)
        points[bucket] = value if previous is None else max(previous, value)
        if len(self._buckets) > self._limit:
            self._coarsen()

    def mark(self, t: float) -> None:
        """Put ``t`` on the axis even if no series has a value there."""
        if self._origin is None:
            self._origin = t
        self._buckets.add(int((t - self._origin) // self._width))
        if len(self._buckets) > self._limit:
            self._coarsen()

    def _coarsen(self) -> None:
        self._width *= 2
        self._buckets = {bucket // 2 for bucket in self._buckets}
        for key, points in self._series.items():
            merged: dict[int, float] = {}
            for bucket, value in points.items():
                half = bucket // 2
                current = merged.get(half)
                merged[half] = value if current is None else max(current, value)
            self._series[key] = merged

    def times(self) -> list[float]:
        """The start of every bucket that holds anything, in order."""
        origin = self._origin or 0.0
        return [origin + bucket * self._width for bucket in sorted(self._buckets)]

    def values(self, key: K) -> list[float | None]:
        """``key``'s value in every bucket of :meth:`times`, None where it has none."""
        points = self._series.get(key, {})
        return [points.get(bucket) for bucket in sorted(self._buckets)]

    def series_keys(self) -> list[K]:
        return list(self._series)
