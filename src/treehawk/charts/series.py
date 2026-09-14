"""Reshaping a log into plot-ready series.

Pure data work, no matplotlib: what to plot is decided here, how to draw it is
decided in :mod:`treehawk.charts.pages`. That split is what lets the page
functions stay short and the reshaping stay testable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from treehawk.core.compat import StrEnum
from treehawk.core.errors import ReportError
from treehawk.core.interfaces import Record
from treehawk.core.records import target_of
from treehawk.core.values import as_float, as_int, as_mapping, as_records
from treehawk.report import read_records
from treehawk.ui.theme import discovery_group


class Metric(StrEnum):
    """Which per-process measurement a chart is built from."""

    CPU = "cpu"
    RSS = "rss"


TOP_SERIES: Final = 6
"""Processes charted individually before the rest folds into "other".

The categorical palette is six slots deep and is never cycled, so a seventh
series would have to repeat a colour - "other" is the honest alternative.
"""


@dataclass(slots=True)
class ProcessTrack:
    """One process across the whole run."""

    pid: int
    starttime: int
    name: str
    cmdline: str
    via: str
    first_t: float
    last_t: float
    cpu_seconds: float = 0.0
    peak_rss: int = 0
    peak_pss: int = 0
    cpu_at: dict[float, float] = field(default_factory=dict)
    rss_at: dict[float, int] = field(default_factory=dict)

    @property
    def group(self) -> str:
        """How the process was found, as presented in the charts."""
        return discovery_group(self.via)

    @property
    def lifetime(self) -> float:
        return max(self.last_t - self.first_t, 0.0)

    @property
    def label(self) -> str:
        return f"{self.name} ({self.pid})"

    def readings_of(self, metric: Metric) -> Mapping[float, float]:
        """This process' values for ``metric``, keyed by sample time."""
        return self.cpu_at if metric is Metric.CPU else self.rss_at


@dataclass(slots=True)
class RunSeries:
    """Everything the pages draw, already aligned on a shared time axis."""

    header: Record
    summary: Record
    t: list[float] = field(default_factory=list)
    cpu_percent: list[float | None] = field(default_factory=list)
    cpu_percent_norm: list[float | None] = field(default_factory=list)
    cpu_seconds_used: list[float] = field(default_factory=list)
    rss: list[int | None] = field(default_factory=list)
    pss: list[int | None] = field(default_factory=list)
    group_memory: list[int | None] = field(default_factory=list)
    n_procs: list[int] = field(default_factory=list)
    procs_cpu_total: list[float] = field(default_factory=list)
    procs_rss_total: list[float] = field(default_factory=list)
    overrun_t: list[float] = field(default_factory=list)
    gaps: list[float] = field(default_factory=list)
    tracks: list[ProcessTrack] = field(default_factory=list)

    @property
    def has_per_process(self) -> bool:
        return bool(self.tracks)

    @property
    def duration(self) -> float:
        return self.t[-1] if self.t else 0.0

    @property
    def interval(self) -> float:
        return as_float(self.header.get("interval")) or 1.0

    @property
    def ncpu(self) -> int:
        host = as_mapping(self.header.get("host"))
        return as_int(host.get("ncpu")) or 1

    @property
    def target(self) -> str:
        return target_of(self.header)

    def charted_total(self, metric: Metric) -> list[float]:
        """The per-process rows summed at each instant, for ``metric``."""
        if metric is Metric.CPU:
            return self.procs_cpu_total
        return self.procs_rss_total

    def top_tracks(self, *, rank: Callable[[ProcessTrack], float], limit: int = TOP_SERIES) -> list[ProcessTrack]:
        """The ``limit`` processes that matter most by ``rank``, biggest first."""
        return sorted(self.tracks, key=rank, reverse=True)[:limit]


def load_series(path: str) -> RunSeries:
    """Read a log into a :class:`RunSeries`, tolerating a truncated tail."""
    series = RunSeries(header={}, summary={})
    tracks: dict[tuple[int, int], ProcessTrack] = {}
    previous_t: float | None = None
    samples = 0

    for record in read_records(path):
        kind = record.get("type")
        if kind == "header":
            series.header = record
            continue
        if kind == "summary":
            series.summary = record
            continue
        if kind != "sample":
            continue

        samples += 1
        t = as_float(record.get("t")) or 0.0
        series.t.append(t)
        series.cpu_percent.append(as_float(record.get("cpu_percent")))
        series.cpu_percent_norm.append(as_float(record.get("cpu_percent_norm")))
        series.cpu_seconds_used.append(as_float(record.get("cpu_seconds_used")) or 0.0)
        series.rss.append(as_int(record.get("rss_bytes")))
        series.pss.append(as_int(record.get("pss_bytes")))
        series.group_memory.append(as_int(record.get("group_memory_bytes")))
        series.n_procs.append(as_int(record.get("n_procs")) or 0)
        if record.get("overrun"):
            series.overrun_t.append(t)
        if previous_t is not None:
            series.gaps.append(t - previous_t)
        previous_t = t

        rows = as_records(record.get("procs"))
        series.procs_cpu_total.append(sum(as_float(proc.get("cpu_percent")) or 0.0 for proc in rows))
        series.procs_rss_total.append(sum(float(as_int(proc.get("rss_bytes")) or 0) for proc in rows))
        for proc in rows:
            _track_process(tracks=tracks, proc=proc, t=t)

    if not samples:
        raise ReportError(f"{path} contains no samples")

    series.tracks = sorted(tracks.values(), key=lambda track: track.first_t)
    return series


def _track_process(*, tracks: dict[tuple[int, int], ProcessTrack], proc: Record, t: float) -> None:
    """Fold one per-process row into the track it belongs to."""
    pid = as_int(proc.get("pid")) or 0
    starttime = as_int(proc.get("starttime")) or 0
    key = (pid, starttime)
    track = tracks.get(key)
    if track is None:
        track = ProcessTrack(
            pid=pid,
            starttime=starttime,
            name=str(proc.get("name") or "?"),
            cmdline=str(proc.get("cmdline") or proc.get("name") or ""),
            via=str(proc.get("via") or "-"),
            first_t=t,
            last_t=t,
        )
        tracks[key] = track

    track.last_t = t
    track.cpu_seconds = max(track.cpu_seconds, as_float(proc.get("cpu_seconds")) or 0.0)
    rss = as_int(proc.get("rss_bytes")) or 0
    pss = as_int(proc.get("pss_bytes")) or 0
    track.peak_rss = max(track.peak_rss, rss)
    track.peak_pss = max(track.peak_pss, pss)
    # A process has no CPU rate in the sample it is first seen in - there is no
    # earlier counter to subtract. Charted as zero, which is why the stacked
    # views carry a note.
    track.cpu_at[t] = as_float(proc.get("cpu_percent")) or 0.0
    track.rss_at[t] = rss


def stack_for(
    *, series: RunSeries, tracks: Sequence[ProcessTrack], metric: Metric
) -> tuple[list[list[float]], list[float]]:
    """Align per-process values onto the run's time axis, plus an "other" band.

    Returns ``(bands, other)``: one row per track, and the remainder needed to
    reach the run total at each instant.
    """
    bands: list[list[float]] = []
    for track in tracks:
        readings = track.readings_of(metric)
        bands.append([float(readings.get(t, 0.0)) for t in series.t])

    # Measured against the per-process rows, never against the workload total:
    # the total includes processes that exited mid-interval and rates we could
    # not compute yet, and charging those to "other" would invent a series.
    charted = [sum(column) for column in zip(*bands, strict=True)] if bands else [0.0] * len(series.t)
    return bands, [max(total - shown, 0.0) for total, shown in zip(series.charted_total(metric), charted, strict=True)]
