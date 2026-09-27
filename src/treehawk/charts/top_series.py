"""Reading a ``top`` log into plot-ready series."""

from __future__ import annotations

from treehawk.charts.constants import CHARTS
from treehawk.charts.models import TopSeries
from treehawk.top.history import CPU, HOST_CPU, HOST_MEMORY, RSS, SeriesKey, load_history
from treehawk.top.timeline import Timeline


def load_top_series(
    path: str,
    *,
    since: float | None = None,
    until: float | None = None,
    points: int = CHARTS.top_points,
) -> TopSeries:
    timeline: Timeline[SeriesKey] = Timeline(limit=points)
    history = load_history(path, since=since, until=until, timeline=timeline)
    times = timeline.times()
    started = history.first_time if history.first_time is not None else (times[0] if times else 0.0)
    series = TopSeries(
        history=history,
        started=started,
        bucket_seconds=timeline.width,
        t=[(moment - started) / 3600.0 for moment in times],
        host_cpu=timeline.values(HOST_CPU),
        host_memory=timeline.values(HOST_MEMORY),
    )
    for measure, pid, starttime in timeline.series_keys():
        identity = (pid, starttime)
        if measure == CPU:
            series.process_cpu[identity] = timeline.values((measure, pid, starttime))
        elif measure == RSS:
            series.process_rss[identity] = timeline.values((measure, pid, starttime))
    return series
