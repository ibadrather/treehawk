"""CPU rates for every process on the machine.

A rate needs two readings of a rising counter, so the previous tick count of
every visible process is kept - one integer each, which is what makes ranking
the whole table by CPU affordable.
"""

from __future__ import annotations

from collections.abc import Mapping

from treehawk.core.models import Identity, ProcInfo, SystemSample


class CpuRates:
    """Per-process CPU percent (100 = one core) from tick deltas."""

    def __init__(self, *, clk_tck: int) -> None:
        self._clk_tck = clk_tck or 100
        self._previous: dict[Identity, int] = {}

    def update(self, *, procs: Mapping[int, ProcInfo], dt: float) -> tuple[dict[Identity, float], list[Identity]]:
        """``({identity: percent}, exited)``.

        A process seen for the first time has no rate yet and is absent from
        the result; ``exited`` lists the identities no longer present.
        """
        rates: dict[Identity, float] = {}
        current: dict[Identity, int] = {}
        for info in procs.values():
            identity = info.identity
            current[identity] = info.cpu_ticks
            previous = self._previous.get(identity)
            if previous is not None and dt > 0:
                rates[identity] = round(max(info.cpu_ticks - previous, 0) / self._clk_tck / dt * 100.0, 2)
        exited = [identity for identity in self._previous if identity not in current]
        self._previous = current
        return rates, exited


class HostCpuRate:
    """Machine-wide busy percent (100 = every core) from ``SystemSample`` deltas."""

    def __init__(self) -> None:
        self._previous: SystemSample | None = None

    def update(self, sample: SystemSample | None) -> float | None:
        if sample is None:
            return None
        previous, self._previous = self._previous, sample
        if previous is None:
            return None
        total = sample.cpu_total_ticks - previous.cpu_total_ticks
        if total <= 0:
            return None
        busy = sample.cpu_busy_ticks - previous.cpu_busy_ticks
        return round(min(max(busy / total * 100.0, 0.0), 100.0), 2)
