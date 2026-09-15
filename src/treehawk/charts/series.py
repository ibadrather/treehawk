"""Reshaping a log into plot-ready series.

Pure data work, no matplotlib: what to plot is decided here, how to draw it is
decided in :mod:`treehawk.charts.pages`. That split is what lets the page
functions stay short and the reshaping stay testable.
"""

from __future__ import annotations

from collections.abc import Sequence

from treehawk.charts.models import Metric, ProcessTrack, RunSeries
from treehawk.core.errors import ReportError
from treehawk.core.interfaces import Record
from treehawk.core.values import as_float, as_int, as_records
from treehawk.report import read_records


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
