"""The pages of the PDF report.

One class per page, each answering a single question about the run. They share
an interface - a title, a subtitle, and ``draw`` - so the writer can render any
list of them, and a new page is a new class plus a registry entry.

A page returns ``False`` from ``draw`` when the log does not contain what it
needs (an aggregate-only log has no per-process pages), and the writer leaves it
out rather than emitting a blank sheet.
"""

from __future__ import annotations

from typing import Callable, Final, Sequence

import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from prowatch.charts import style
from prowatch.charts.series import Metric, ProcessTrack, RunSeries, stack_for
from prowatch.core.humanize import (
    format_bytes,
    format_percent,
    format_seconds,
    truncate,
)
from prowatch.core.values import as_float
from prowatch.ui.theme import DISCOVERY_ORDER, PRINT, Palette

Ranking = Callable[[ProcessTrack], float]
"""How a page orders processes when it can only show a few of them."""


def by_cpu_seconds(track: ProcessTrack) -> float:
    return track.cpu_seconds


def by_peak_rss(track: ProcessTrack) -> float:
    return float(track.peak_rss)


MAX_GANTT_ROWS: Final = 34
"""Lifetime bars that fit legibly on one page before the rest is summarised."""

RANKING_ROWS: Final = 10
"""Processes in each league table on the "biggest consumers" page."""


class Page:
    """Base page: a title, a subtitle, and something to draw."""

    title = ""
    subtitle = ""

    def draw(
        self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT
    ) -> bool:
        raise NotImplementedError


# --------------------------------------------------------------------------


class OverviewPage(Page):
    """What happened, in the five numbers people actually quote."""

    title = "Overview"
    subtitle = "the run at a glance"

    def draw(
        self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT
    ) -> bool:
        summary = series.summary
        style.draw_heading(
            fig,
            title=self.title,
            subtitle=truncate(series.target, width=96),
            palette=palette,
        )

        headline = [
            ("peak cpu", format_percent(as_float(summary.get("peak_cpu_percent")))),
            ("cpu time", format_seconds(as_float(summary.get("cpu_seconds_used")))),
            ("peak memory", format_bytes(_peak_memory(summary))),
            ("duration", format_seconds(series.duration)),
            ("processes", str(summary.get("total_procs_seen") or len(series.tracks))),
        ]
        for index, (label, value) in enumerate(headline):
            x = 0.06 + index * 0.182
            fig.text(x, 0.80, value, fontsize=22, fontweight="bold",
                     color=palette.text_primary)
            fig.text(x, 0.765, label, fontsize=9, color=palette.text_muted)

        self._run_facts(fig, series=series, palette=palette)
        if series.tracks:
            self._discovery(fig, series=series, palette=palette)
        return True

    def _run_facts(
        self, fig: Figure, *, series: RunSeries, palette: Palette
    ) -> None:
        header, summary = series.header, series.summary
        host = header.get("host") or {}
        boundary = header.get("group_path")
        rows = [
            ("mode", str(header.get("mode", "?"))),
            ("started", str(header.get("started_at", "?"))),
            ("interval", f"{series.interval}s requested"),
            ("samples", f"{summary.get('samples', len(series.t))}"
                        + (f", {summary['overruns']} over the interval"
                           if summary.get("overruns") else "")),
            ("host", f"{host.get('hostname', '?')} · {series.ncpu} cpus · "
                     f"{format_bytes(as_float(host.get('mem_total_bytes')))} ram"),
            ("boundary", str(boundary).rsplit("/", 1)[-1] if boundary
             else "none - membership inferred from /proc"),
            ("peak concurrent", str(summary.get("peak_n_procs", "?"))),
            ("mean cpu", format_percent(as_float(summary.get("mean_cpu_percent")))),
            ("peak rss / pss",
             f"{format_bytes(as_float(summary.get('peak_rss_bytes')))} / "
             f"{format_bytes(as_float(summary.get('peak_pss_bytes')))}"),
        ]
        exit_code = summary.get("exit_code")
        if exit_code is not None:
            rows.append(("exit code", str(exit_code)))

        for index, (label, value) in enumerate(rows):
            y = 0.66 - index * 0.045
            fig.text(0.06, y, label, fontsize=9, color=palette.text_muted)
            fig.text(0.20, y, truncate(value, width=70), fontsize=9,
                     color=palette.text_primary)

        for index, note in enumerate(header.get("notes") or ()):
            fig.text(0.06, 0.16 - index * 0.035,
                     f"note  {truncate(str(note), width=100)}",
                     fontsize=8, color=palette.warning)

    def _discovery(
        self, fig: Figure, *, series: RunSeries, palette: Palette
    ) -> None:
        ax = fig.add_axes((0.58, 0.42, 0.36, 0.20))
        counts = {group: 0 for group in DISCOVERY_ORDER}
        for track in series.tracks:
            counts[track.group] += 1
        if not any(counts.values()):
            style.draw_placeholder(
                ax, message="no per-process detail in this log", palette=palette
            )
            return
        labels = list(DISCOVERY_ORDER)
        values = [counts[label] for label in labels]
        colors = [palette.slot(index) for index in range(len(labels))]
        bars = ax.barh(labels, values, color=colors, height=0.62,
                       edgecolor=palette.surface, linewidth=style.SURFACE_GAP)
        widest = max(values)
        for bar, value in zip(bars, values):
            ax.text(bar.get_width() + widest * 0.03,
                    bar.get_y() + bar.get_height() / 2, str(value),
                    va="center", fontsize=9, color=palette.text_primary,
                    fontweight="bold")
        ax.set_title("how each process was found")
        ax.set_xlim(0, widest * 1.2)
        ax.invert_yaxis()
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="y", labelcolor=palette.text_primary)
        ax.set_xlabel("processes")


class CpuPage(Page):
    """Was it busy, and did it stay busy?"""

    title = "CPU over time"
    subtitle = ("100% is one core fully used; the workload total includes "
                "processes that exited between samples")

    def draw(
        self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT
    ) -> bool:
        style.draw_heading(
            fig, title=self.title, subtitle=self.subtitle, palette=palette
        )
        top, bottom = fig.subplots(2, 1, height_ratios=(2, 1), sharex=True)
        fig.subplots_adjust(left=0.08, right=0.95, top=0.85, bottom=0.11, hspace=0.25)

        t = series.t
        color = palette.slot(0)
        top.plot(t, _with_gaps(series.cpu_percent), color=color)
        top.fill_between(t, 0, _with_gaps(series.cpu_percent), color=color, alpha=0.12)

        present = [(x, y) for x, y in zip(t, series.cpu_percent) if y is not None]
        if present:
            peak_x, peak_y = max(present, key=lambda point: point[1])
            style.annotate_peak(
                top,
                x=peak_x,
                y=peak_y,
                text=format_percent(peak_y),
                color=color,
                palette=palette,
            )
            mean = sum(y for _x, y in present) / len(present)
            top.axhline(mean, color=palette.text_muted, linewidth=1,
                        linestyle=(0, (4, 4)))
            top.annotate(f"mean {format_percent(mean)}", xy=(1.0, mean),
                         xycoords=("axes fraction", "data"), xytext=(-6, 4),
                         textcoords="offset points", ha="right", fontsize=8,
                         color=palette.text_secondary)
        for moment in series.overrun_t:
            top.axvline(moment, color=palette.warning, linewidth=1, alpha=0.5)

        # The machine's full capacity is only worth drawing when the workload
        # came near it; on a 32-core box a 300% peak would otherwise be a flat
        # line at the bottom of an empty chart.
        capacity = series.ncpu * 100
        peak = max((y for _x, y in present), default=0.0)
        if peak >= capacity * 0.25:
            top.axhline(capacity, color=palette.grid, linewidth=1)
            top.text(t[0] if t else 0, capacity, f" all {series.ncpu} cpus",
                     fontsize=7.5, va="bottom", color=palette.text_muted)
            top.set_ylim(0, capacity * 1.08)
        else:
            top.set_ylim(0, max(peak * 1.3, 1.0))
            top.text(1.0, 1.02, f"machine capacity {capacity:.0f}% "
                                f"({series.ncpu} cpus), above this chart",
                     transform=top.transAxes, ha="right", fontsize=8,
                     color=palette.text_muted)
        top.set_ylabel("cpu %")

        bottom.plot(t, series.cpu_seconds_used, color=palette.slot(0))
        bottom.fill_between(t, 0, series.cpu_seconds_used, color=palette.slot(0),
                            alpha=0.12)
        bottom.set_ylabel("cpu time (s)")
        bottom.set_ylim(bottom=0)
        style.format_time_axis(bottom, duration=series.duration)
        if series.overrun_t:
            style.draw_footer(
                fig,
                left=f"{len(series.overrun_t)} sample(s) took longer than the "
                     f"{series.interval}s interval (marked)",
                right="",
                palette=palette,
            )
        return True


class MemoryPage(Page):
    """How much memory, and which measure of it."""

    title = "Memory over time"
    subtitle = ("rss double-counts pages shared between children; pss divides "
                "them fairly; cgroup is the kernel's own figure")

    def draw(
        self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT
    ) -> bool:
        style.draw_heading(
            fig, title=self.title, subtitle=self.subtitle, palette=palette
        )
        ax = fig.subplots()
        fig.subplots_adjust(left=0.10, right=0.95, top=0.84, bottom=0.12)

        drawn = 0
        for index, (label, values) in enumerate(
            (("rss (summed)", series.rss),
             ("pss (shared-adjusted)", series.pss),
             ("cgroup", series.group_memory)),
        ):
            if not any(value is not None for value in values):
                continue
            color = palette.slot(index * 2)  # 0, 2, 4 - keeps them far apart
            ax.plot(series.t, _with_gaps(values), color=color, label=label)
            present = [(x, y) for x, y in zip(series.t, values) if y is not None]
            if present:
                peak_x, peak_y = max(present, key=lambda point: point[1])
                style.annotate_peak(
                    ax,
                    x=peak_x,
                    y=peak_y,
                    text=format_bytes(peak_y),
                    color=color,
                    palette=palette,
                )
            drawn += 1

        if not drawn:
            style.draw_placeholder(
                ax, message="no memory readings in this log", palette=palette
            )
            return True

        ax.set_ylabel("memory")
        ax.set_ylim(bottom=0)
        style.format_bytes_axis(ax)
        style.format_time_axis(ax, duration=series.duration)
        if drawn > 1:
            ax.legend(loc="upper left", ncols=drawn)
        return True


class LifetimePage(Page):
    """Who was alive, when - the shape of the process population."""

    title = "Process lifetimes"
    subtitle = ("each bar is one process, from the first sample it appeared in "
                "to the last")

    def draw(
        self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT
    ) -> bool:
        if not series.has_per_process:
            return False
        style.draw_heading(
            fig, title=self.title, subtitle=self.subtitle, palette=palette
        )
        tracks = sorted(series.tracks, key=lambda track: (track.first_t, track.pid))
        shown = tracks[:MAX_GANTT_ROWS]
        gantt, count = _timeline_axes(fig, rows=len(shown))
        minimum = max(series.interval * 0.35, series.duration * 0.002)
        for row, track in enumerate(shown):
            gantt.barh(
                row, max(track.lifetime, minimum), left=track.first_t, height=0.62,
                color=palette.slot(DISCOVERY_ORDER.index(track.group)),
                edgecolor=palette.surface, linewidth=style.SURFACE_GAP,
            )
        gantt.set_ylim(len(shown) - 0.5, -0.5)
        gantt.set_yticks(range(len(shown)))
        gantt.set_yticklabels([truncate(track.label, width=28) for track in shown],
                              fontsize=7.5)
        gantt.grid(axis="y", visible=False)
        gantt.tick_params(axis="y", labelcolor=palette.text_secondary)
        gantt.tick_params(axis="x", labelbottom=False)
        gantt.legend(
            handles=[
                Patch(facecolor=palette.slot(index), label=group)
                for index, group in enumerate(DISCOVERY_ORDER)
                if any(track.group == group for track in shown)
            ],
            loc="lower right", bbox_to_anchor=(1.0, 1.01), ncols=3,
        )
        if len(tracks) > len(shown):
            gantt.set_title(f"{len(shown)} of {len(tracks)} processes, "
                            f"earliest first")

        count.step(series.t, series.n_procs, where="post", color=palette.slot(0))
        count.fill_between(series.t, 0, series.n_procs, step="post",
                           color=palette.slot(0), alpha=0.12)
        count.set_ylabel("alive")
        count.set_ylim(bottom=0)
        style.format_time_axis(count, duration=series.duration)
        return True


class _StackedPage(Page):
    """Shared machinery for the two per-process stacked-area pages."""

    metric = Metric.CPU
    rank: Ranking = staticmethod(by_cpu_seconds)
    ylabel = ""

    def draw(
        self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT
    ) -> bool:
        if not series.has_per_process:
            return False
        style.draw_heading(
            fig, title=self.title, subtitle=self.subtitle, palette=palette
        )
        ax = fig.subplots()
        fig.subplots_adjust(left=0.10, right=0.80, top=0.84, bottom=0.12)

        tracks = series.top_tracks(rank=self.rank)
        bands, other = stack_for(series=series, tracks=tracks, metric=self.metric)
        if not bands:
            style.draw_placeholder(
                ax, message="no per-process readings in this log", palette=palette
            )
            return True

        labels = [truncate(track.label, width=26) for track in tracks]
        colors = [palette.slot(index) for index in range(len(tracks))]
        rows = list(bands)
        if any(value > 0 for value in other):
            rows.append(other)
            labels.append("other")
            colors.append(palette.other)

        ax.stackplot(series.t, *rows, colors=colors, labels=labels,
                     edgecolor=palette.surface, linewidth=style.SURFACE_GAP)
        _label_bands(ax, t=series.t, rows=rows, labels=labels, palette=palette)
        ax.set_ylabel(self.ylabel)
        ax.set_ylim(bottom=0)
        style.format_time_axis(ax, duration=series.duration)
        if self.metric is Metric.RSS:
            style.format_bytes_axis(ax)
        ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False)
        return True


class CpuByProcessPage(_StackedPage):
    """Which process was burning the CPU."""

    title = "CPU by process"
    subtitle = ("stacked; a process contributes nothing in the sample it first "
                "appears in, because there is no earlier counter to compare")
    metric = Metric.CPU
    rank: Ranking = staticmethod(by_cpu_seconds)
    ylabel = "cpu %"


class MemoryByProcessPage(_StackedPage):
    """Which process was holding the memory."""

    title = "Memory by process"
    subtitle = ("stacked rss; shared pages are counted once per process, so this "
                "reads high")
    metric = Metric.RSS
    rank: Ranking = staticmethod(by_peak_rss)
    ylabel = "rss"


class RankingPage(Page):
    """The two league tables."""

    title = "Biggest consumers"
    subtitle = "totals over the whole run, not a single instant"

    def draw(
        self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT
    ) -> bool:
        if not series.has_per_process:
            return False
        style.draw_heading(
            fig, title=self.title, subtitle=self.subtitle, palette=palette
        )
        rows = min(RANKING_ROWS, len(series.tracks))
        height = min(0.66, 0.055 * rows + 0.05)
        bottom = 0.82 - height
        left = fig.add_axes((0.15, bottom, 0.31, height))
        right = fig.add_axes((0.64, bottom, 0.31, height))

        self._bars(
            left,
            tracks=series.top_tracks(rank=by_cpu_seconds, limit=RANKING_ROWS),
            measure=by_cpu_seconds,
            title="cpu time (s)",
            formatter=format_seconds,
            palette=palette,
        )
        self._bars(
            right,
            tracks=series.top_tracks(rank=by_peak_rss, limit=RANKING_ROWS),
            measure=by_peak_rss,
            title="peak rss",
            formatter=format_bytes,
            palette=palette,
        )
        return True

    def _bars(
        self,
        ax: Axes,
        *,
        tracks: Sequence[ProcessTrack],
        measure: Ranking,
        title: str,
        formatter: Callable[[float], str],
        palette: Palette,
    ) -> None:
        if not tracks:
            style.draw_placeholder(ax, message="nothing recorded", palette=palette)
            return
        values = [measure(track) for track in tracks]
        colors = [palette.slot(DISCOVERY_ORDER.index(track.group)) for track in tracks]
        positions = range(len(tracks))
        ax.barh(list(positions), values, color=colors, height=0.62,
                edgecolor=palette.surface, linewidth=style.SURFACE_GAP)
        ax.set_ylim(len(tracks) - 0.5, -0.5)
        ax.set_yticks(list(positions))
        ax.set_yticklabels([truncate(track.label, width=24) for track in tracks],
                           fontsize=8)
        ax.grid(axis="y", visible=False)
        ax.set_title(title)
        ax.set_xlim(0, max(values) * 1.25 if max(values) else 1)
        widest = max(values)
        for index, value in enumerate(values):
            ax.text(value + widest * 0.03, index, formatter(value),
                    va="center", fontsize=8, color=palette.text_primary)


class SamplingPage(Page):
    """Can you trust the numbers on the other pages?"""

    title = "Sampling quality"
    subtitle = ("polling cannot see a process that starts and ends between two "
                "samples; these show how tight the sampling actually was")

    def draw(
        self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT
    ) -> bool:
        if len(series.t) < 3:
            return False
        style.draw_heading(
            fig, title=self.title, subtitle=self.subtitle, palette=palette
        )
        left, right = fig.subplots(1, 2)
        fig.subplots_adjust(left=0.08, right=0.96, top=0.84, bottom=0.13, wspace=0.22)

        # Plotted as error against the requested interval: the raw gaps differ
        # by microseconds, and a histogram of those looks alarming at a glance
        # purely because the axis is zoomed to nothing.
        errors = [(gap - series.interval) * 1000 for gap in series.gaps]
        if errors:
            left.hist(errors, bins=min(40, max(8, len(errors) // 4)),
                      color=palette.slot(0), edgecolor=palette.surface, linewidth=0.8)
            left.axvline(0, color=palette.text_muted, linewidth=1.2,
                         linestyle=(0, (4, 4)))
            worst = max(errors, key=abs)
            left.set_title(f"sampling error · worst {worst:+.1f} ms")
            left.set_xlabel(f"milliseconds late (requested every {series.interval}s)")
            left.set_ylabel("samples")
            left.yaxis.get_major_locator().set_params(integer=True)
            span = max(abs(worst), 1.0) * 1.3
            left.set_xlim(-span, span)
        else:
            style.draw_placeholder(left, message="not enough samples", palette=palette)

        present = sorted(value for value in series.cpu_percent if value is not None)
        if present:
            fraction = np.arange(1, len(present) + 1) / len(present) * 100
            right.step(present, fraction, where="post", color=palette.slot(2))
            for quantile in (50, 95):
                index = max(0, int(len(present) * quantile / 100) - 1)
                value = present[index]
                right.axvline(value, color=palette.grid, linewidth=1)
                near_edge = value > present[-1] * 0.75
                right.annotate(
                    f"p{quantile} {format_percent(value)}", xy=(value, quantile),
                    xytext=(-6 if near_edge else 6, 0), textcoords="offset points",
                    ha="right" if near_edge else "left", va="center",
                    fontsize=8, color=palette.text_secondary,
                )
            right.set_title("cpu distribution")
            right.set_xlabel("cpu %")
            right.set_ylabel("% of samples")
            right.set_ylim(0, 100)
        else:
            style.draw_placeholder(right, message="no cpu readings", palette=palette)
        return True


def _label_bands(
    ax: Axes,
    *,
    t: Sequence[float],
    rows: Sequence[Sequence[float]],
    labels: Sequence[str],
    palette: Palette,
) -> None:
    """Name each band on the band itself, so identity is never colour alone.

    Only bands thick enough to hold a word are labelled - a label that needs a
    leader line is worse than the legend it duplicates.
    """
    ceiling = max((sum(column) for column in zip(*rows)), default=0.0)
    if ceiling <= 0:
        return
    bottoms = [0.0] * len(t)
    for values, label in zip(rows, labels):
        thickest = max(range(len(t)), key=lambda index: values[index])
        thickness = values[thickest]
        if thickness >= ceiling * 0.12:
            ax.text(
                t[thickest], bottoms[thickest] + thickness / 2, label,
                ha="center", va="center", fontsize=7.5, fontweight="bold",
                color=palette.surface,
            )
        bottoms = [base + value for base, value in zip(bottoms, values)]


def _timeline_axes(fig: Figure, *, rows: int) -> tuple[Axes, Axes]:
    """A lifetime chart sized to its content, with the population below it."""
    height = min(0.58, 0.032 * rows + 0.05)
    count_height = 0.16
    # Centre the pair in the page body, so a short chart does not sit marooned
    # at the top of an empty sheet.
    slack = max(0.0, (0.84 - 0.12) - (height + 0.09 + count_height))
    top = 0.84 - slack / 2 - height
    gantt = fig.add_axes((0.22, top, 0.73, height))
    count_bottom = max(0.10, top - 0.09 - count_height)
    count = fig.add_axes((0.22, count_bottom, 0.73, count_height), sharex=gantt)
    return gantt, count


def _with_gaps(values: Sequence[float | int | None]) -> list[float]:
    """Missing readings become NaN, which matplotlib draws as a break.

    A gap must look like a gap: interpolating across "we could not measure"
    would draw a line the data does not support.
    """
    return [float("nan") if value is None else float(value) for value in values]


def _peak_memory(summary: RunSeries | dict[str, object]) -> float | None:
    """The best whole-run memory figure, in the order we trust the measures."""
    if not isinstance(summary, dict):
        return None
    for key in ("peak_group_memory_bytes", "peak_pss_bytes", "peak_rss_bytes"):
        value = as_float(summary.get(key))
        if value is not None:
            return value
    return None
