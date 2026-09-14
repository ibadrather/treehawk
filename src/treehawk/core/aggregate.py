"""Turning raw per-process readings into a sample, and samples into a summary.

Two small collaborators, each with one job:

* :class:`Aggregator` - what the workload is doing *right now* (one Snapshot).
* :class:`SummaryAccumulator` - what it did *over the run* (peaks and means).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final, TypedDict

from treehawk.core.config import CpuSource
from treehawk.core.models import GroupMetrics, HostInfo, Identity, ProcSample, Snapshot
from treehawk.core.tracker import RefreshResult
from treehawk.core.values import as_float, as_int, peak_of


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

    def __init__(self, *, host: HostInfo, cpu_source: CpuSource = CpuSource.PROCESSES) -> None:
        self._host = host
        self._cpu_source = cpu_source
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
            seq=seq,
            t=round(elapsed, 6),
            timestamp=timestamp,
            procs=samples,
            group=group,
            overrun=overrun,
        )

        for sample in samples:
            identity = sample.identity
            previous = self._prev_proc_ticks.get(identity)
            ticks = sample.info.cpu_ticks
            if previous is not None and dt > 0:
                sample.cpu_percent = round((ticks - previous) / clk_tck / dt * 100.0, 2)
            self._prev_proc_ticks[identity] = ticks
        for identity in refresh.exited:
            self._prev_proc_ticks.pop(identity, None)

        total_cpu = refresh.total_cpu_ticks / clk_tck
        if self._cpu_source is CpuSource.GROUP and group is not None and group.cpu_usec is not None:
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


class ProcessTotals(TypedDict):
    """Per-process figures accumulated over a whole run."""

    pid: int
    name: str
    cmdline: str
    via: str
    cpu_seconds: float
    peak_rss_bytes: int


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
    top_by_cpu: list[ProcessTotals] = field(default_factory=list)
    top_by_memory: list[ProcessTotals] = field(default_factory=list)


class SummaryAccumulator:
    """Folds snapshots into a :class:`RunSummary` as the run proceeds.

    Per-process totals are kept for the whole run so the final tables can rank
    processes that have long since exited. A watch is expected to last until the
    workload ends - which may be days - so that table is pruned back to the
    contenders once it grows past :data:`PROCESS_TABLE_LIMIT`.
    """

    def __init__(self, *, clk_tck: int = 100, top_n: int = 5) -> None:
        self._summary = RunSummary()
        self._clk_tck = clk_tck or 100
        self._cpu_sum = 0.0
        self._cpu_n = 0
        self._top_n = top_n
        self._seen: dict[Identity, ProcessTotals] = {}

    def add(self, snapshot: Snapshot) -> None:
        summary = self._summary
        summary.samples += 1
        summary.duration_s = round(snapshot.t, 3)
        summary.cpu_seconds_total = snapshot.cpu_seconds_total
        summary.cpu_seconds_used = snapshot.cpu_seconds_used
        summary.peak_n_procs = max(summary.peak_n_procs, snapshot.n_procs)
        if snapshot.overrun:
            summary.overruns += 1
        if snapshot.cpu_percent is not None:
            summary.peak_cpu_percent = peak_of(current=summary.peak_cpu_percent, candidate=snapshot.cpu_percent)
            self._cpu_sum += snapshot.cpu_percent
            self._cpu_n += 1
            summary.mean_cpu_percent = round(self._cpu_sum / self._cpu_n, 2)
        summary.peak_rss_bytes = peak_of(current=summary.peak_rss_bytes, candidate=snapshot.rss_bytes)
        summary.peak_pss_bytes = peak_of(current=summary.peak_pss_bytes, candidate=snapshot.pss_bytes)
        if snapshot.group is not None:
            summary.peak_group_memory_bytes = peak_of(
                current=summary.peak_group_memory_bytes,
                candidate=(snapshot.group.memory_peak_bytes or snapshot.group.memory_bytes),
            )
        for sample in snapshot.procs:
            self._record_process(sample)
        if len(self._seen) > PROCESS_TABLE_LIMIT:
            self._prune()

    def _record_process(self, sample: ProcSample) -> None:
        entry = self._seen.get(sample.identity)
        if entry is None:
            entry = ProcessTotals(
                pid=sample.info.pid,
                name=sample.info.comm,
                cmdline=sample.label,
                via=sample.via,
                cpu_seconds=0.0,
                peak_rss_bytes=0,
            )
            self._seen[sample.identity] = entry
        entry["cpu_seconds"] = round(sample.info.cpu_ticks / self._clk_tck, 3)
        rss = sample.info.rss_bytes
        if rss:
            entry["peak_rss_bytes"] = max(entry["peak_rss_bytes"], rss)

    def _prune(self) -> None:
        """Keep only processes that could still reach a top table."""
        keep = set(_rank(entries=self._seen.values(), key=cpu_seconds_of, limit=PRUNE_KEEP))
        keep |= set(_rank(entries=self._seen.values(), key=peak_rss_of, limit=PRUNE_KEEP))
        self._seen = {identity: totals for identity, totals in self._seen.items() if totals["pid"] in keep}

    def finish(self, *, exit_code: int | None = None) -> RunSummary:
        summary = self._summary
        summary.exit_code = exit_code
        summary.total_procs_seen = len(self._seen)
        entries = list(self._seen.values())
        summary.top_by_cpu = sorted(entries, key=cpu_seconds_of, reverse=True)[: self._top_n]
        summary.top_by_memory = sorted(entries, key=peak_rss_of, reverse=True)[: self._top_n]
        return summary


PROCESS_TABLE_LIMIT: Final = 4096
"""Distinct processes remembered for the summary before pruning kicks in."""

PRUNE_KEEP: Final = 256
"""Contenders kept per ranking when pruning."""


def _rank(
    *,
    entries: Iterable[ProcessTotals],
    key: Callable[[ProcessTotals], float],
    limit: int,
) -> list[int]:
    ranked = sorted(entries, key=key, reverse=True)[:limit]
    return [entry["pid"] for entry in ranked]


def cpu_seconds_of(entry: Mapping[str, object]) -> float:
    """Ranking key: CPU seconds accumulated over the run."""
    return as_float(entry.get("cpu_seconds")) or 0.0


def peak_rss_of(entry: Mapping[str, object]) -> int:
    """Ranking key: the highest RSS this process ever reached."""
    return as_int(entry.get("peak_rss_bytes")) or 0


def _sum_or_none(values: Iterable[int | None]) -> int | None:
    total: int | None = None
    for value in values:
        if value is None:
            continue
        total = value if total is None else total + value
    return total
