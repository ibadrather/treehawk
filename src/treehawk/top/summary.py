"""Folding samples into a segment summary as they arrive."""

from __future__ import annotations

from treehawk.core.values import peak_of
from treehawk.top.models import EventKind, HostReading, TopSummary


class TopAccumulator:
    """What one segment amounted to: how it was sampled, the machine's peaks, and its events."""

    def __init__(self) -> None:
        self._summary = TopSummary()
        self._host_cpu_sum = 0.0
        self._host_cpu_n = 0
        self._interval_sum = 0.0

    def add(self, *, t: float, dt: float, overrun: bool, host: HostReading | None, n_procs: int) -> None:
        summary = self._summary
        if summary.samples:
            self._interval_sum += dt
        summary.samples += 1
        summary.duration_s = round(t, 3)
        summary.peak_n_procs = max(summary.peak_n_procs, n_procs)
        if overrun:
            summary.overruns += 1
        if summary.samples > 1:
            summary.mean_interval_s = round(self._interval_sum / (summary.samples - 1), 4)
        if host is not None:
            summary.peak_host_mem_used_bytes = peak_of(
                current=summary.peak_host_mem_used_bytes, candidate=host.mem_used_bytes
            )
            if host.cpu_percent is not None:
                summary.peak_host_cpu_percent = peak_of(
                    current=summary.peak_host_cpu_percent, candidate=host.cpu_percent
                )
                self._host_cpu_sum += host.cpu_percent
                self._host_cpu_n += 1
                summary.mean_host_cpu_percent = round(self._host_cpu_sum / self._host_cpu_n, 2)

    def event(self, kind: EventKind) -> None:
        key = str(kind)
        self._summary.events[key] = self._summary.events.get(key, 0) + 1

    def finish(self) -> TopSummary:
        return self._summary
