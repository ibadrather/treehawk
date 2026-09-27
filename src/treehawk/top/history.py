"""Reading a ``top`` log back, for the report and the charts.

One pass over the records, however many segments there are. Totals and
events are gathered for the report; when a :class:`Timeline` is supplied the
host line and every process' rows are also bucketed into it for charting.

Times are wall-clock (Unix seconds from each record's ``ts``), not the
per-run ``t``: a directory may span several runs and several boots, and only
the wall clock orders them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import TypeAlias

from treehawk.core.errors import ReportError
from treehawk.core.interfaces import Record
from treehawk.core.models import Identity
from treehawk.core.values import as_float, as_int, as_mapping, as_sequence, peak_of
from treehawk.report import read_records
from treehawk.top.constants import TOP
from treehawk.top.models import EventKind, ProcessHistory, Resource, TopHistory
from treehawk.top.records import MODE
from treehawk.top.timeline import Timeline

SeriesKey: TypeAlias = tuple[str, int, int]
"""``(measure, pid, starttime)``; the host line uses pid and starttime 0."""

HOST_CPU: SeriesKey = ("host-cpu", 0, 0)
HOST_MEMORY: SeriesKey = ("host-mem", 0, 0)
CPU = "cpu"
RSS = "rss"


def epoch_of(ts: object) -> float | None:
    """Unix seconds from an ISO-8601 timestamp, or None if it is not one."""
    if not isinstance(ts, str) or not ts:
        return None
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def load_history(
    path: str,
    *,
    since: float | None = None,
    until: float | None = None,
    timeline: Timeline[SeriesKey] | None = None,
) -> TopHistory:
    """Read ``path`` (a file or a directory) into a :class:`TopHistory`."""
    reader = _Reader(since=since, until=until, timeline=timeline)
    for record in read_records(path):
        reader.feed(record)
    history = reader.history
    if not history.header:
        raise ReportError(f"{path} holds no treehawk top log")
    if not history.samples:
        raise ReportError(f"{path} contains no samples in the requested range")
    return history


class _Reader:
    def __init__(self, *, since: float | None, until: float | None, timeline: Timeline[SeriesKey] | None) -> None:
        self.history = TopHistory(header={})
        self._since = since
        self._until = until
        self._timeline = timeline
        self._names: dict[Identity, tuple[str, str]] = {}
        self._fields: Sequence[object] = TOP.compact_row
        self._host_cpu_sum = 0.0
        self._host_cpu_n = 0

    def feed(self, record: Record) -> None:
        kind = record.get("type")
        if kind == "header":
            self._header(record)
        elif kind == "proc":
            identity = (as_int(record.get("pid")) or 0, as_int(record.get("starttime")) or 0)
            self._names[identity] = (str(record.get("name") or "?"), str(record.get("cmdline") or ""))
        elif kind == "sample":
            self._sample(record)
        elif kind == "event":
            self._event(record)

    def _header(self, record: Record) -> None:
        if record.get("mode") != MODE:
            raise ReportError("not a treehawk top log; use it with the workload report instead")
        if not self.history.header:
            self.history.header = record
        self.history.segments += 1
        self._fields = as_sequence(record.get("row_fields")) or TOP.compact_row

    def _in_range(self, when: float | None) -> bool:
        if when is None:
            return False
        if self._since is not None and when < self._since:
            return False
        return self._until is None or when <= self._until

    def _sample(self, record: Record) -> None:
        when = epoch_of(record.get("ts"))
        if when is None or not self._in_range(when):
            return
        history = self.history
        history.samples += 1
        if record.get("overrun"):
            history.overruns += 1
        if history.first_time is None:
            history.first_time = when
        history.last_time = when
        if self._timeline is not None:
            self._timeline.mark(when)

        host = as_mapping(record.get("host"))
        cpu = as_float(host.get("cpu_percent"))
        used = as_int(host.get("mem_used_bytes"))
        history.peak_host_cpu_percent = peak_of(current=history.peak_host_cpu_percent, candidate=cpu)
        history.peak_host_mem_used_bytes = peak_of(current=history.peak_host_mem_used_bytes, candidate=used)
        if cpu is not None:
            self._host_cpu_sum += cpu
            self._host_cpu_n += 1
            history.mean_host_cpu_percent = round(self._host_cpu_sum / self._host_cpu_n, 2)
            if self._timeline is not None:
                self._timeline.add(key=HOST_CPU, t=when, value=cpu)
        if used is not None and self._timeline is not None:
            self._timeline.add(key=HOST_MEMORY, t=when, value=float(used))

        dt = as_float(record.get("dt")) or 0.0
        for raw in as_sequence(record.get("top")):
            row = self._row(raw)
            if row is not None:
                self._process_row(row=row, when=when, dt=dt)

    def _row(self, raw: object) -> Mapping[str, object] | None:
        values = as_sequence(raw)
        if not values:
            return None
        return {str(name): value for name, value in zip(self._fields, values, strict=False)}

    def _process_row(self, *, row: Mapping[str, object], when: float, dt: float) -> None:
        pid = as_int(row.get("pid")) or 0
        starttime = as_int(row.get("starttime")) or 0
        identity = (pid, starttime)
        cpu = as_float(row.get("cpu_percent"))
        rss = as_int(row.get("rss_bytes")) or 0
        entry = self.history.processes.get(identity)
        if entry is None:
            name, cmdline = self._names.get(identity, ("?", ""))
            entry = ProcessHistory(
                pid=pid,
                starttime=starttime,
                name=name,
                cmdline=cmdline or name,
                first_seen=when,
                last_seen=when,
                first_rss_bytes=rss,
            )
            self.history.processes[identity] = entry
        entry.samples += 1
        entry.last_seen = when
        entry.last_rss_bytes = rss
        entry.peak_rss_bytes = max(entry.peak_rss_bytes, rss)
        if cpu is not None:
            entry.cpu_seconds += cpu / 100.0 * dt
            entry.peak_cpu_percent = max(entry.peak_cpu_percent, cpu)
        if self._timeline is not None:
            if cpu is not None:
                self._timeline.add(key=(CPU, pid, starttime), t=when, value=cpu)
            self._timeline.add(key=(RSS, pid, starttime), t=when, value=float(rss))

    def _event(self, record: Record) -> None:
        when = epoch_of(record.get("ts"))
        if not self._in_range(when):
            return
        kind = str(record.get("kind") or "?")
        counts = self.history.event_counts
        counts[kind] = counts.get(kind, 0) + 1
        if kind not in {str(EventKind.SPIKE), str(EventKind.CREEP)}:
            return
        identity = (as_int(record.get("pid")) or 0, as_int(record.get("starttime")) or 0)
        _, cmdline = self._names.get(identity, ("", ""))
        self.history.events.append({**record, "time": when, "cmdline": cmdline or record.get("name")})


def largest_spikes(events: Sequence[Record], *, resource: Resource, limit: int = TOP.report_events) -> list[Record]:
    """The biggest jumps above baseline for one resource, in time order."""
    spikes = [
        event
        for event in events
        if event.get("kind") == str(EventKind.SPIKE) and event.get("resource") == str(resource)
    ]
    spikes.sort(key=lambda event: (as_float(event.get("value")) or 0.0) - (as_float(event.get("baseline")) or 0.0))
    chosen = spikes[-limit:]
    return sorted(chosen, key=lambda event: as_float(event.get("time")) or 0.0)


def leak_suspects(events: Sequence[Record]) -> list[Record]:
    """The latest leak report per process, steepest first."""
    latest: dict[tuple[object, object], Record] = {}
    for event in events:
        if event.get("kind") == str(EventKind.CREEP):
            latest[event.get("pid"), event.get("starttime")] = event
    return sorted(latest.values(), key=lambda event: as_float(event.get("slope_per_hour")) or 0.0, reverse=True)


def history_record(history: TopHistory, *, limit: int = TOP.summary_rank_limit) -> Record:
    """The report as plain data, for ``--json``."""
    processes = list(history.processes.values())

    def listed(entries: list[ProcessHistory]) -> list[Record]:
        return [
            {
                "pid": entry.pid,
                "starttime": entry.starttime,
                "name": entry.name,
                "cmdline": entry.cmdline,
                "cpu_seconds": round(entry.cpu_seconds, 3),
                "peak_cpu_percent": entry.peak_cpu_percent,
                "peak_rss_bytes": entry.peak_rss_bytes,
                "first_rss_bytes": entry.first_rss_bytes,
                "last_rss_bytes": entry.last_rss_bytes,
                "first_seen": entry.first_seen,
                "last_seen": entry.last_seen,
            }
            for entry in entries[:limit]
        ]

    return {
        "header": history.header,
        "segments": history.segments,
        "samples": history.samples,
        "overruns": history.overruns,
        "first_time": history.first_time,
        "last_time": history.last_time,
        "peak_host_cpu_percent": history.peak_host_cpu_percent,
        "mean_host_cpu_percent": history.mean_host_cpu_percent,
        "peak_host_mem_used_bytes": history.peak_host_mem_used_bytes,
        "event_counts": history.event_counts,
        "top_by_cpu": listed(sorted(processes, key=lambda entry: entry.cpu_seconds, reverse=True)),
        "top_by_memory": listed(sorted(processes, key=lambda entry: entry.peak_rss_bytes, reverse=True)),
        "cpu_spikes": largest_spikes(history.events, resource=Resource.CPU),
        "memory_spikes": largest_spikes(history.events, resource=Resource.MEMORY),
        "leak_suspects": leak_suspects(history.events),
    }
