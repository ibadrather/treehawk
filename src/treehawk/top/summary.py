"""Folding samples into a segment summary as they arrive."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from treehawk.core.aggregate import cpu_seconds_of, peak_rss_of
from treehawk.core.constants import CORE
from treehawk.core.models import Identity, ProcessTotals
from treehawk.core.values import peak_of
from treehawk.top.constants import TOP
from treehawk.top.models import EventKind, HostReading, RankedProc, TopSummary
from treehawk.top.records import reasons_text


class TopAccumulator:
    """What one segment amounted to: peaks, event counts, and who used the most.

    A process' CPU time here is what it used *while in the top N*, not since it
    started - a daemon that has run for a month would otherwise lead every
    table for having existed.
    """

    def __init__(self, *, rank_limit: int = TOP.summary_rank_limit) -> None:
        self._summary = TopSummary()
        self._rank_limit = rank_limit
        self._host_cpu_sum = 0.0
        self._host_cpu_n = 0
        self._interval_sum = 0.0
        self._seen: dict[Identity, ProcessTotals] = {}

    def add(
        self,
        *,
        t: float,
        dt: float,
        overrun: bool,
        host: HostReading | None,
        n_procs: int,
        ranked: Iterable[RankedProc],
        cmdlines: dict[Identity, str],
    ) -> None:
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
        for entry in ranked:
            self._record_process(entry=entry, dt=dt, cmdline=cmdlines.get(entry.identity, ""))
        if len(self._seen) > CORE.process_table_limit:
            self._prune()

    def event(self, kind: EventKind) -> None:
        key = str(kind)
        self._summary.events[key] = self._summary.events.get(key, 0) + 1

    def _record_process(self, *, entry: RankedProc, dt: float, cmdline: str) -> None:
        sample = entry.sample
        totals = self._seen.get(entry.identity)
        if totals is None:
            totals = ProcessTotals(
                pid=sample.info.pid,
                name=sample.info.comm,
                cmdline=cmdline or sample.label,
                via=reasons_text(entry.reasons),
                cpu_seconds=0.0,
                peak_rss_bytes=0,
            )
            self._seen[entry.identity] = totals
        if sample.cpu_percent is not None:
            totals["cpu_seconds"] = round(totals["cpu_seconds"] + sample.cpu_percent / 100.0 * dt, 3)
        totals["peak_rss_bytes"] = max(totals["peak_rss_bytes"], sample.info.rss_bytes or 0)

    def _prune(self) -> None:
        keep = set(_ranked(self._seen, key=cpu_seconds_of, limit=CORE.prune_keep))
        keep |= set(_ranked(self._seen, key=peak_rss_of, limit=CORE.prune_keep))
        self._seen = {identity: totals for identity, totals in self._seen.items() if identity in keep}

    def finish(self) -> TopSummary:
        summary = self._summary
        summary.total_procs_seen = len(self._seen)
        entries = list(self._seen.values())
        summary.top_by_cpu = sorted(entries, key=cpu_seconds_of, reverse=True)[: self._rank_limit]
        summary.top_by_memory = sorted(entries, key=peak_rss_of, reverse=True)[: self._rank_limit]
        return summary


def _ranked(
    seen: Mapping[Identity, ProcessTotals],
    *,
    key: Callable[[Mapping[str, object]], float],
    limit: int,
) -> list[Identity]:
    return sorted(seen, key=lambda identity: key(seen[identity]), reverse=True)[:limit]
