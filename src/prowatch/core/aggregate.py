"""Turning raw per-process readings into a sample, and samples into a summary.

Two small collaborators, each with one job:

* :class:`Aggregator` - what the workload is doing *right now* (one Snapshot).
* :class:`SummaryAccumulator` - what it did *over the run* (peaks and means).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import GroupMetrics, HostInfo, ProcSample, Snapshot
from .tracker import RefreshResult


class Aggregator:
    """Builds one :class:`Snapshot` per sample.

    CPU is derived from counter *deltas*, never from an instantaneous reading:

    * per process - delta of its own CPU ticks over the elapsed wall time,
      ``None`` on the first sighting because there is nothing to subtract from.
    * for the workload - delta of the total CPU seconds, which already includes
      processes that exited during the interval (the tracker carries their final
      counters forward). That is why the aggregate stays correct while children
      come and go, and why it is not merely the sum of the per-process values.
    """

    def __init__(self, host: HostInfo, *, prefer_group_cpu: bool = False) -> None:
        self._host = host
        self._prefer_group_cpu = prefer_group_cpu
        self._prev_proc_ticks: dict[tuple[int, int], int] = {}
        self._prev_total_cpu: float | None = None
        self._baseline_cpu: float | None = None

    def build(
        self,
        *,
        seq: int,
        elapsed: float,
        dt: float,
        timestamp: str,
        refresh: RefreshResult,
        samples: list[ProcSample],
        group: GroupMetrics | None = None,
        overrun: bool = False,
    ) -> Snapshot:
        clk_tck = self._host.clk_tck or 100
        snap = Snapshot(
            seq=seq, t=round(elapsed, 6), timestamp=timestamp, procs=samples,
            group=group, overrun=overrun,
        )

        for sample in samples:
            identity = sample.identity
            previous = self._prev_proc_ticks.get(identity)
            ticks = sample.info.cpu_ticks
            if previous is not None and dt > 0:
                sample.cpu_percent = round(
                    (ticks - previous) / clk_tck / dt * 100.0, 2
                )
            self._prev_proc_ticks[identity] = ticks
        for identity in refresh.exited:
            self._prev_proc_ticks.pop(identity, None)

        total_cpu = refresh.total_cpu_ticks / clk_tck
        if self._prefer_group_cpu and group is not None and group.cpu_usec is not None:
            total_cpu = group.cpu_usec / 1_000_000.0
        if self._baseline_cpu is None:
            self._baseline_cpu = total_cpu
        # Counters only climb; a drop means a boundary was replaced, not real.
        if self._prev_total_cpu is not None and total_cpu < self._prev_total_cpu:
            total_cpu = self._prev_total_cpu

        snap.cpu_seconds_total = round(total_cpu, 3)
        snap.cpu_seconds_used = round(max(0.0, total_cpu - self._baseline_cpu), 3)
        if self._prev_total_cpu is not None and dt > 0:
            percent = (total_cpu - self._prev_total_cpu) / dt * 100.0
            snap.cpu_percent = round(max(0.0, percent), 2)
            ncpu = self._host.ncpu or 1
            snap.cpu_percent_norm = round(snap.cpu_percent / ncpu, 2)
        self._prev_total_cpu = total_cpu

        snap.rss_bytes = _sum_or_none(s.info.rss_bytes for s in samples)
        snap.pss_bytes = _sum_or_none(s.pss_bytes for s in samples)
        snap.swap_bytes = _sum_or_none(s.swap_bytes for s in samples)
        return snap


@dataclass(slots=True)
class RunSummary:
    """Whole-run figures, computed incrementally so nothing is kept in memory."""

    samples: int = 0
    duration_s: float = 0.0
    peak_cpu_percent: float | None = None
    mean_cpu_percent: float | None = None
    peak_n_procs: int = 0
    total_procs_seen: int = 0
    peak_rss_bytes: int | None = None
    peak_pss_bytes: int | None = None
    peak_group_memory_bytes: int | None = None
    cpu_seconds_total: float = 0.0
    cpu_seconds_used: float = 0.0
    overruns: int = 0
    exit_code: int | None = None
    top_by_cpu: list[dict] = field(default_factory=list)
    top_by_memory: list[dict] = field(default_factory=list)


class SummaryAccumulator:
    """Folds snapshots into a :class:`RunSummary` as the run proceeds."""

    def __init__(self, *, clk_tck: int = 100, top_n: int = 5) -> None:
        self._summary = RunSummary()
        self._clk_tck = clk_tck or 100
        self._cpu_sum = 0.0
        self._cpu_n = 0
        self._top_n = top_n
        self._seen: dict[tuple[int, int], dict] = {}

    def add(self, snap: Snapshot) -> None:
        s = self._summary
        s.samples += 1
        s.duration_s = round(snap.t, 3)
        s.cpu_seconds_total = snap.cpu_seconds_total
        s.cpu_seconds_used = snap.cpu_seconds_used
        s.peak_n_procs = max(s.peak_n_procs, snap.n_procs)
        if snap.overrun:
            s.overruns += 1
        if snap.cpu_percent is not None:
            s.peak_cpu_percent = _max(s.peak_cpu_percent, snap.cpu_percent)
            self._cpu_sum += snap.cpu_percent
            self._cpu_n += 1
            s.mean_cpu_percent = round(self._cpu_sum / self._cpu_n, 2)
        s.peak_rss_bytes = _max(s.peak_rss_bytes, snap.rss_bytes)
        s.peak_pss_bytes = _max(s.peak_pss_bytes, snap.pss_bytes)
        if snap.group is not None:
            s.peak_group_memory_bytes = _max(
                s.peak_group_memory_bytes,
                snap.group.memory_peak_bytes or snap.group.memory_bytes,
            )
        for sample in snap.procs:
            self._record_process(sample)

    def _record_process(self, sample: ProcSample) -> None:
        entry = self._seen.get(sample.identity)
        if entry is None:
            entry = {
                "pid": sample.info.pid,
                "name": sample.info.comm,
                "cmdline": sample.cmdline or sample.info.comm,
                "via": sample.via,
                "cpu_seconds": 0.0,
                "peak_rss_bytes": 0,
            }
            self._seen[sample.identity] = entry
        entry["cpu_seconds"] = round(sample.info.cpu_ticks / self._clk_tck, 3)
        if sample.info.rss_bytes:
            entry["peak_rss_bytes"] = max(entry["peak_rss_bytes"], sample.info.rss_bytes)

    def finish(self, *, exit_code: int | None = None) -> RunSummary:
        s = self._summary
        s.exit_code = exit_code
        s.total_procs_seen = len(self._seen)
        entries = list(self._seen.values())
        s.top_by_cpu = sorted(
            entries, key=lambda e: e["cpu_seconds"], reverse=True
        )[: self._top_n]
        s.top_by_memory = sorted(
            entries, key=lambda e: e["peak_rss_bytes"], reverse=True
        )[: self._top_n]
        return s


def _sum_or_none(values) -> int | None:
    total = None
    for value in values:
        if value is None:
            continue
        total = value if total is None else total + value
    return total


def _max(current, candidate):
    if candidate is None:
        return current
    return candidate if current is None else max(current, candidate)
