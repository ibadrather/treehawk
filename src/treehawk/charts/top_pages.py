"""The pages of a ``top`` PDF: the machine, who used it, and what went wrong.

Separate from :mod:`treehawk.charts.pages` because they draw a different log:
a whole machine over hours or weeks, on a wall-clock axis, rather than one
workload over one run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from matplotlib.axes import Axes
from matplotlib.figure import Figure

from treehawk.charts import style
from treehawk.charts.constants import CHARTS
from treehawk.charts.models import TopSeries
from treehawk.core.compat import override
from treehawk.core.humanize import format_bytes, format_percent, format_seconds, truncate
from treehawk.core.interfaces import Record
from treehawk.core.models import Identity
from treehawk.core.values import as_float, as_mapping
from treehawk.top.history import largest_spikes, leak_suspects
from treehawk.top.models import HOST_PID, EventKind, ProcessHistory, Resource
from treehawk.ui.models import Palette
from treehawk.ui.theme import PRINT
from treehawk.ui.top_views import format_value, local_time


class TopPage:
    """Base page for a ``top`` log."""

    title = ""
    subtitle = ""

    def draw(self, fig: Figure, *, series: TopSeries, palette: Palette = PRINT) -> bool:
        raise NotImplementedError


class TopOverviewPage(TopPage):
    title = "Machine overview"

    @override
    def draw(self, fig: Figure, *, series: TopSeries, palette: Palette = PRINT) -> bool:
        history = series.history
        host = as_mapping(history.header.get("host"))
        span = (history.last_time or 0.0) - (history.first_time or 0.0)
        style.draw_heading(
            fig,
            title=self.title,
            subtitle=(
                f"{host.get('hostname', '?')} · {local_time(history.first_time)} → {local_time(history.last_time)}"
            ),
            palette=palette,
        )
        counts = history.event_counts
        headline = [
            ("peak cpu (all cores)", format_percent(history.peak_host_cpu_percent)),
            ("peak memory in use", format_bytes(history.peak_host_mem_used_bytes)),
            ("spikes", str(counts.get(str(EventKind.SPIKE), 0))),
            ("leak suspects", str(len(leak_suspects(history.events)))),
            ("covered", format_seconds(span)),
        ]
        for index, (label, value) in enumerate(headline):
            x = 0.06 + index * 0.182
            fig.text(x, 0.80, value, fontsize=22, fontweight="bold", color=palette.text_primary)
            fig.text(x, 0.765, label, fontsize=9, color=palette.text_muted)
        rows = [
            ("host", f"{host.get('ncpu', '?')} cpus · {format_bytes(as_float(host.get('mem_total_bytes')))} ram"),
            ("mean cpu", format_percent(history.mean_host_cpu_percent)),
            ("tracked", f"top {history.header.get('top_n', '?')} by cpu and by memory"),
            ("samples", f"{history.samples} in {history.segments} segment(s), {history.overruns} late"),
            ("processes", f"{len(history.processes)} reached the top at some point"),
            ("entries / exits", f"{counts.get(str(EventKind.ENTER), 0)} / {counts.get(str(EventKind.LEAVE), 0)}"),
            ("resolution", f"one point per {format_seconds(series.bucket_seconds)} (peak of each)"),
        ]
        for index, (label, value) in enumerate(rows):
            y = 0.66 - index * 0.045
            fig.text(0.06, y, label, fontsize=9, color=palette.text_muted)
            fig.text(0.22, y, truncate(value, width=90), fontsize=9, color=palette.text_primary)
        return True


class HostPage(TopPage):
    title = "The whole machine"
    subtitle = "busy share of every core, and memory in use; red marks are machine-wide spikes, amber a steady rise"

    @override
    def draw(self, fig: Figure, *, series: TopSeries, palette: Palette = PRINT) -> bool:
        if not any(value is not None for value in series.host_cpu + series.host_memory):
            return False
        style.draw_heading(fig, title=self.title, subtitle=self.subtitle, palette=palette)
        cpu_ax, memory_ax = fig.subplots(2, 1, sharex=True)
        fig.subplots_adjust(left=0.09, right=0.95, top=0.85, bottom=0.11, hspace=0.25)
        _line(cpu_ax, series=series, values=series.host_cpu, color=palette.slot(0))
        cpu_ax.set_ylim(0, 105)
        cpu_ax.set_ylabel("cpu % of all cores")
        _line(memory_ax, series=series, values=series.host_memory, color=palette.slot(2))
        total = as_float(as_mapping(series.history.header.get("host")).get("mem_total_bytes"))
        if total:
            memory_ax.axhline(total, color=palette.grid, linewidth=1)
            memory_ax.set_ylim(0, total * 1.05)
        else:
            memory_ax.set_ylim(bottom=0)
        memory_ax.set_ylabel("memory in use")
        style.format_bytes_axis(memory_ax)
        _mark_events(cpu_ax, series=series, resource=Resource.CPU, palette=palette)
        _mark_events(memory_ax, series=series, resource=Resource.MEMORY, palette=palette)
        _time_axis(memory_ax, series=series)
        return True


class ProcessPage(TopPage):
    """The processes that mattered most for one resource, one line each."""

    def __init__(
        self,
        *,
        title: str,
        subtitle: str,
        pick: Callable[[ProcessHistory], float],
        values: Callable[[TopSeries], Mapping[Identity, list[float | None]]],
        bytes_axis: bool,
    ) -> None:
        self.title = title
        self.subtitle = subtitle
        self._pick = pick
        self._values = values
        self._bytes_axis = bytes_axis

    @override
    def draw(self, fig: Figure, *, series: TopSeries, palette: Palette = PRINT) -> bool:
        processes = sorted(series.history.processes.values(), key=self._pick, reverse=True)
        chosen = [entry for entry in processes if self._pick(entry) > 0][: CHARTS.top_series]
        readings = self._values(series)
        if not chosen:
            return False
        style.draw_heading(fig, title=self.title, subtitle=self.subtitle, palette=palette)
        ax = fig.subplots()
        fig.subplots_adjust(left=0.09, right=0.95, top=0.84, bottom=0.12)
        for index, entry in enumerate(chosen):
            _line(
                ax,
                series=series,
                values=readings.get(entry.identity, []),
                color=palette.slot(index),
                label=truncate(f"{entry.label} {entry.cmdline}", width=60),
                fill=False,
            )
        ax.set_ylim(bottom=0)
        if self._bytes_axis:
            style.format_bytes_axis(ax)
        else:
            ax.set_ylabel("cpu % (100 = one core)")
        ax.legend(loc="upper left", ncols=2)
        _time_axis(ax, series=series)
        return True


class EventsPage(TopPage):
    title = "Spikes and leak suspects"
    subtitle = "the largest jumps above each process' own baseline, and memory that kept rising"

    @override
    def draw(self, fig: Figure, *, series: TopSeries, palette: Palette = PRINT) -> bool:
        events = series.history.events
        rows: list[tuple[str, str, str, str]] = []
        for resource in (Resource.CPU, Resource.MEMORY):
            rows.extend(
                (
                    local_time(as_float(event.get("time"))),
                    f"{resource} spike",
                    (
                        f"{format_value(str(resource), as_float(event.get('value')))} "
                        f"(usually {format_value(str(resource), as_float(event.get('baseline')))})"
                    ),
                    _who(event),
                )
                for event in largest_spikes(events, resource=resource, limit=8)
            )
        rows.extend(
            (
                local_time(as_float(event.get("time"))),
                "leak suspect",
                f"+{format_bytes(as_float(event.get('slope_per_hour')))}/h, r² {as_float(event.get('r2')) or 0:.2f}",
                _who(event),
            )
            for event in leak_suspects(events)[:8]
        )
        if not rows:
            return False
        style.draw_heading(fig, title=self.title, subtitle=self.subtitle, palette=palette)
        for x, label in ((0.06, "when"), (0.24, "what"), (0.36, "reading"), (0.58, "who")):
            fig.text(x, 0.84, label, fontsize=9, color=palette.text_muted, fontweight="bold")
        for index, (when, what, reading, who) in enumerate(rows[:34]):
            y = 0.81 - index * 0.021
            color = palette.warning if what == "leak suspect" else palette.critical
            fig.text(0.06, y, when, fontsize=8, color=palette.text_secondary)
            fig.text(0.24, y, what, fontsize=8, color=color)
            fig.text(0.36, y, reading, fontsize=8, color=palette.text_primary)
            fig.text(0.58, y, truncate(who, width=62), fontsize=8, color=palette.text_primary)
        return True


def _who(event: Record) -> str:
    return f"{event.get('name', '?')} ({event.get('pid', '?')}) {event.get('cmdline') or ''}"


def _cpu_values(series: TopSeries) -> Mapping[Identity, list[float | None]]:
    return series.process_cpu


def _rss_values(series: TopSeries) -> Mapping[Identity, list[float | None]]:
    return series.process_rss


TOP_PAGES: tuple[TopPage, ...] = (
    TopOverviewPage(),
    HostPage(),
    ProcessPage(
        title="CPU by process",
        subtitle="the processes that used the most cpu time while in the top N; peak of each time bucket",
        pick=lambda entry: entry.cpu_seconds,
        values=_cpu_values,
        bytes_axis=False,
    ),
    ProcessPage(
        title="Memory by process",
        subtitle="the processes that reached the most resident memory; a line that only climbs is a leak suspect",
        pick=lambda entry: float(entry.peak_rss_bytes),
        values=_rss_values,
        bytes_axis=True,
    ),
    EventsPage(),
)
"""In reading order: the machine, who used it, then what went wrong."""


# --------------------------------------------------------------------------


def _line(
    ax: Axes,
    *,
    series: TopSeries,
    values: Sequence[float | None],
    color: str,
    label: str | None = None,
    fill: bool = True,
) -> None:
    """Plot ``values``, broken wherever the log itself has a hole."""
    xs, ys = _broken(series=series, values=values)
    ax.plot(xs, ys, color=color, label=label, linewidth=1.2)
    if fill:
        ax.fill_between(xs, 0, ys, color=color, alpha=0.12)


def _broken(*, series: TopSeries, values: Sequence[float | None]) -> tuple[list[float], list[float]]:
    gap = series.bucket_seconds * CHARTS.top_gap_buckets / 3600.0
    xs: list[float] = []
    ys: list[float] = []
    previous: float | None = None
    for x, y in zip(series.t, values, strict=False):
        if previous is not None and x - previous > gap:
            xs.append((x + previous) / 2)
            ys.append(float("nan"))
        xs.append(x)
        ys.append(float("nan") if y is None else y)
        previous = x
    return xs, ys


def _mark_events(ax: Axes, *, series: TopSeries, resource: Resource, palette: Palette) -> None:
    for event in series.history.events:
        if event.get("pid") != HOST_PID or event.get("resource") != str(resource):
            continue
        moment = as_float(event.get("time"))
        if moment is None:
            continue
        color = palette.warning if event.get("kind") == str(EventKind.CREEP) else palette.critical
        ax.axvline(series.hours_at(moment), color=color, linewidth=0.8, alpha=0.35)


def _time_axis(ax: Axes, *, series: TopSeries) -> None:
    style.format_time_axis(ax, duration=series.hours * 3600.0)
    hours = series.hours
    if hours >= 2:
        ax.xaxis.set_major_formatter(lambda value, _pos: f"{value:.1f}h")
    elif hours >= 180 / 3600:
        ax.xaxis.set_major_formatter(lambda value, _pos: f"{value * 60:.0f}m")
    else:
        ax.xaxis.set_major_formatter(lambda value, _pos: f"{value * 3600:.0f}s")
    ax.set_xlabel(f"since {local_time(series.started)}")
