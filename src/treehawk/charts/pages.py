"""The pages of the PDF report.

One class per page, each answering a single question about the run. They share
an interface - a title, a subtitle, and ``draw`` - so the writer can render any
list of them, and a new page is a new class plus a registry entry.

A page returns ``False`` from ``draw`` when the log does not contain what it
needs (an aggregate-only log has no per-process pages), and the writer leaves it
out rather than emitting a blank sheet.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Sequence
from operator import itemgetter

import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.text import Annotation

from treehawk.charts import style
from treehawk.charts.constants import CHARTS
from treehawk.charts.models import Metric, ProcessTrack, RunSeries
from treehawk.charts.series import stack_for, thin_stack
from treehawk.core.compat import override
from treehawk.core.humanize import (
    format_bytes,
    format_duration_compact,
    format_percent,
    format_seconds,
    truncate,
)
from treehawk.core.records import memory_measure, memory_subtitle, peak_memory_reading
from treehawk.core.values import as_float, as_mapping, as_sequence
from treehawk.ui.models import Palette
from treehawk.ui.theme import DISCOVERY_ORDER, PRINT

Ranking = Callable[[ProcessTrack], float]
"""How a page orders processes when it can only show a few of them."""


def by_cpu_seconds(track: ProcessTrack) -> float:
    return track.cpu_seconds


def by_peak_rss(track: ProcessTrack) -> float:
    return float(track.peak_rss)


class Page:
    """Base page: a title, a subtitle, and something to draw."""

    title = ""
    subtitle = ""

    def draw(self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT) -> bool:
        raise NotImplementedError


# --------------------------------------------------------------------------


class OverviewPage(Page):
    """What happened, in the five numbers people actually quote."""

    title = "Overview"
    subtitle = "the run at a glance"

    @override
    def draw(self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT) -> bool:
        summary = series.summary
        style.draw_heading(
            fig,
            title=self.title,
            subtitle=truncate(series.target, width=96),
            palette=palette,
        )

        headline = [
            ("peak cpu", format_percent(as_float(summary.get("peak_cpu_percent")))),
            # Compact, because a headline has a column to fit in: a long run's
            # full "2h20m01.0s" runs into its neighbour. The exact values are
            # in the facts below.
            ("cpu time", format_duration_compact(as_float(summary.get("cpu_seconds_used")))),
            ("peak memory", format_bytes(peak_memory_reading(summary=summary, header=series.header).value)),
            ("duration", format_duration_compact(series.duration)),
            ("processes", str(summary.get("total_procs_seen") or len(series.tracks))),
        ]
        for index, (label, value) in enumerate(headline):
            x = 0.06 + index * 0.182
            fig.text(x, 0.80, value, fontsize=22, fontweight="bold", color=palette.text_primary)
            fig.text(x, 0.765, label, fontsize=9, color=palette.text_muted)

        self._run_facts(fig, series=series, palette=palette)
        if series.tracks:
            self._discovery(fig, series=series, palette=palette)
        return True

    def _run_facts(self, fig: Figure, *, series: RunSeries, palette: Palette) -> None:
        header, summary = series.header, series.summary
        measure = memory_measure(header)
        host = as_mapping(header.get("host"))
        boundary = header.get("group_path")
        rows = [
            ("mode", str(header.get("mode", "?"))),
            ("started", str(header.get("started_at", "?"))),
            ("duration", format_seconds(series.duration) + _asleep_note(series)),
            ("interval", f"{series.interval}s requested"),
            (
                "samples",
                f"{summary.get('samples', len(series.t))}"
                + (f", {summary['overruns']} over the interval" if summary.get("overruns") else ""),
            ),
            (
                "host",
                (
                    f"{host.get('hostname', '?')} · {series.ncpu} cpus · "
                    f"{format_bytes(as_float(host.get('mem_total_bytes')))} ram"
                ),
            ),
            ("boundary", str(boundary).rsplit("/", 1)[-1] if boundary else "none - membership inferred"),
            ("peak concurrent", str(summary.get("peak_n_procs", "?"))),
            (
                "cpu",
                (
                    f"{format_percent(as_float(summary.get('mean_cpu_percent')))} mean · "
                    f"{format_seconds(as_float(summary.get('cpu_seconds_used')))} of cpu time"
                ),
            ),
            (
                f"peak rss / {measure.short}",
                (
                    f"{format_bytes(as_float(summary.get('peak_rss_bytes')))} / "
                    f"{format_bytes(as_float(summary.get('peak_pss_bytes')))}"
                ),
            ),
        ]
        exit_code = summary.get("exit_code")
        if exit_code is not None:
            rows.append(("exit code", str(exit_code)))

        for index, (label, value) in enumerate(rows):
            y = 0.66 - index * 0.042
            fig.text(0.06, y, label, fontsize=9, color=palette.text_muted)
            fig.text(0.20, y, truncate(value, width=70), fontsize=9, color=palette.text_primary)

        # Wrapped to the page, never cut: a note is there because something
        # could not be measured, and half of one says the opposite of nothing.
        y = 0.66 - len(rows) * 0.042 - 0.02
        for note in as_sequence(header.get("notes")):
            lines = textwrap.wrap(f"note  {note}", width=CHARTS.note_width, subsequent_indent="      ")
            for line in lines:
                fig.text(0.06, y, line, fontsize=8, color=palette.warning)
                y -= 0.022
            y -= 0.008

    def _discovery(self, fig: Figure, *, series: RunSeries, palette: Palette) -> None:
        ax = fig.add_axes((0.58, 0.42, 0.36, 0.20))
        counts = dict.fromkeys(DISCOVERY_ORDER, 0)
        for track in series.tracks:
            counts[track.group] += 1
        if not any(counts.values()):
            style.draw_placeholder(ax, message="no per-process detail in this log", palette=palette)
            return
        labels = list(DISCOVERY_ORDER)
        values = [counts[label] for label in labels]
        colors = [palette.slot(index) for index in range(len(labels))]
        bars = ax.barh(
            labels, values, color=colors, height=0.62, edgecolor=palette.surface, linewidth=CHARTS.surface_gap
        )
        widest = max(values)
        for bar, value in zip(bars, values, strict=True):
            ax.text(
                bar.get_width() + widest * 0.03,
                bar.get_y() + bar.get_height() / 2,
                str(value),
                va="center",
                fontsize=9,
                color=palette.text_primary,
                fontweight="bold",
            )
        ax.set_title("how each process was found")
        ax.set_xlim(0, widest * 1.2)
        ax.invert_yaxis()
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="y", labelcolor=palette.text_primary)
        ax.set_xlabel("processes")


class CpuPage(Page):
    """Was it busy, and did it stay busy?"""

    title = "CPU over time"
    subtitle = "100% is one core fully used; the workload total includes processes that exited between samples"

    @override
    def draw(self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT) -> bool:
        style.draw_heading(fig, title=self.title, subtitle=self.subtitle, palette=palette)
        top, bottom = fig.subplots(2, 1, height_ratios=(2, 1), sharex=True)
        fig.subplots_adjust(left=0.08, right=0.95, top=0.85, bottom=0.11, hspace=0.25)

        t = series.t
        color = palette.slot(0)
        broken_t, broken_cpu = _broken_at_sleeps(series, series.cpu_percent)
        top.plot(broken_t, broken_cpu, color=color)
        top.fill_between(broken_t, 0, broken_cpu, color=color, alpha=0.12)

        present = [(x, y) for x, y in zip(t, series.cpu_percent, strict=True) if y is not None]
        if present:
            peak_x, peak_y = max(present, key=itemgetter(1))
            style.annotate_peak(
                top,
                x=peak_x,
                y=peak_y,
                text=format_percent(peak_y),
                color=color,
                palette=palette,
            )
            mean = sum(y for _x, y in present) / len(present)
            top.axhline(mean, color=palette.text_muted, linewidth=1, linestyle=(0, (4, 4)))
            top.annotate(
                f"mean {format_percent(mean)}",
                xy=(1.0, mean),
                xycoords=("axes fraction", "data"),
                xytext=(-6, 4),
                textcoords="offset points",
                ha="right",
                fontsize=8,
                color=palette.text_secondary,
            )
        for moment in series.overrun_t:
            top.axvline(moment, color=palette.warning, linewidth=1, alpha=0.5)
        for start, end in series.sleeps:
            for ax in (top, bottom):
                ax.axvspan(start, end, color=palette.text_muted, alpha=0.12, linewidth=0)

        # The machine's full capacity is only worth drawing when the workload
        # came near it; on a 32-core box a 300% peak would otherwise be a flat
        # line at the bottom of an empty chart.
        capacity = series.ncpu * 100
        peak = max((y for _x, y in present), default=0.0)
        if peak >= capacity * 0.25:
            top.axhline(capacity, color=palette.grid, linewidth=1)
            top.text(
                t[0] if t else 0,
                capacity,
                f" all {series.ncpu} cpus",
                fontsize=7.5,
                va="bottom",
                color=palette.text_muted,
            )
            top.set_ylim(0, capacity * 1.08)
        else:
            top.set_ylim(0, max(peak * 1.3, 1.0))
            top.text(
                1.0,
                1.02,
                f"machine capacity {capacity:.0f}% ({series.ncpu} cpus), above this chart",
                transform=top.transAxes,
                ha="right",
                fontsize=8,
                color=palette.text_muted,
            )
        top.set_ylabel("cpu %")

        bottom.plot(t, series.cpu_seconds_used, color=palette.slot(0))
        bottom.fill_between(t, 0, series.cpu_seconds_used, color=palette.slot(0), alpha=0.12)
        bottom.set_ylabel("cpu time (s)")
        bottom.set_ylim(bottom=0)
        style.format_time_axis(bottom, duration=series.duration)
        notes = []
        if series.overrun_t:
            notes.append(f"{len(series.overrun_t)} sample(s) took longer than the {series.interval}s interval (marked)")
        if series.sleeps:
            notes.append(f"the machine slept for {format_seconds(series.asleep_s)} (shaded)")
        if notes:
            style.draw_footer(fig, left="; ".join(notes), right="", palette=palette)
        return True


class MemoryPage(Page):
    """How much memory, and which measure of it."""

    title = "Memory over time"
    subtitle = "rss double-counts pages shared between children; the group figure is the kernel's own"

    @override
    def draw(self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT) -> bool:
        # Which measures a log carries differs by platform - macOS has no
        # cgroup figure at all - so the subtitle is built from the series that
        # are actually drawn rather than fixed on the class.
        measure = memory_measure(series.header)
        candidates = (("rss (summed)", series.rss), (measure.long, series.pss), ("cgroup", series.group_memory))
        present_in = [any(value is not None for value in values) for _label, values in candidates]
        subtitle = memory_subtitle(measure=measure, rss=present_in[0], fair=present_in[1], group=present_in[2])
        style.draw_heading(fig, title=self.title, subtitle=subtitle, palette=palette)
        ax = fig.subplots()
        fig.subplots_adjust(left=0.10, right=0.95, top=0.84, bottom=0.12)

        drawn = 0
        for index, (label, values) in enumerate(candidates):
            if not present_in[index]:
                continue
            color = palette.slot(index * 2)  # 0, 2, 4 - keeps them far apart
            ax.plot(series.t, _with_gaps(values), color=color, label=label)
            present = [(x, y) for x, y in zip(series.t, values, strict=True) if y is not None]
            if present:
                peak_x, peak_y = max(present, key=itemgetter(1))
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
            style.draw_placeholder(ax, message="no memory readings in this log", palette=palette)
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
    subtitle = "each bar is one process, from the first sample it appeared in to the last"

    @override
    def draw(self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT) -> bool:
        if not series.has_per_process:
            return False
        style.draw_heading(fig, title=self.title, subtitle=self.subtitle, palette=palette)
        tracks = sorted(series.tracks, key=lambda track: (track.first_t, track.pid))
        shown = tracks[: CHARTS.max_gantt_rows]
        gantt, count = _timeline_axes(fig, rows=len(shown))
        # No outline in the surface colour here: rows do not touch, so there
        # is nothing for it to separate, and on a long run it is wider than a
        # short-lived process' whole bar and erases it.
        minimum = max(series.interval * 0.35, series.duration * CHARTS.min_bar_fraction)
        for row, track in enumerate(shown):
            gantt.barh(
                row,
                max(track.lifetime, minimum),
                left=track.first_t,
                height=0.62,
                color=palette.slot(DISCOVERY_ORDER.index(track.group)),
                linewidth=0,
            )
        gantt.set_ylim(len(shown) - 0.5, -0.5)
        gantt.set_yticks(range(len(shown)))
        gantt.set_yticklabels([truncate(track.label, width=28) for track in shown], fontsize=7.5)
        gantt.grid(axis="y", visible=False)
        gantt.tick_params(axis="y", labelcolor=palette.text_secondary)
        gantt.tick_params(axis="x", labelbottom=False)
        gantt.legend(
            handles=[
                Patch(facecolor=palette.slot(index), label=group)
                for index, group in enumerate(DISCOVERY_ORDER)
                if any(track.group == group for track in shown)
            ],
            loc="lower right",
            bbox_to_anchor=(1.0, 1.01),
            ncols=3,
        )
        if len(tracks) > len(shown):
            gantt.set_title(f"{len(shown)} of {len(tracks)} processes, earliest first")

        count.step(series.t, series.n_procs, where="post", color=palette.slot(0))
        count.fill_between(series.t, 0, series.n_procs, step="post", color=palette.slot(0), alpha=0.12)
        count.set_ylabel("alive")
        count.set_ylim(bottom=0)
        style.format_time_axis(count, duration=series.duration)
        return True


class _StackedPage(Page):
    """Shared machinery for the two per-process stacked-area pages."""

    metric = Metric.CPU
    rank: Ranking = staticmethod(by_cpu_seconds)
    ylabel = ""

    @override
    def draw(self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT) -> bool:
        if not series.has_per_process:
            return False
        style.draw_heading(fig, title=self.title, subtitle=self.subtitle, palette=palette)
        ax = fig.subplots()
        fig.subplots_adjust(left=0.10, right=0.80, top=0.84, bottom=0.12)

        tracks = series.top_tracks(rank=self.rank)
        bands, other = stack_for(series=series, tracks=tracks, metric=self.metric)
        if not bands:
            style.draw_placeholder(ax, message="no per-process readings in this log", palette=palette)
            return True

        labels = [truncate(track.label, width=26) for track in tracks]
        colors = [palette.slot(index) for index in range(len(tracks))]
        rows = list(bands)
        if any(value > 0 for value in other):
            rows.append(other)
            labels.append("other")
            colors.append(palette.other)

        # The gap between bands is drawn in the surface colour, which only
        # works while a sample is wider than the gap. On a long run it is not:
        # the outline of each sliver covers the sliver, and the page comes out
        # blank. And without the outline a spike one sample wide is a sliver
        # too thin to print at all.
        if len(series.t) > CHARTS.dense_points:
            # No gap, and stepped columns: see the comment above. Thinned to
            # about a column per point of page width, busiest sample kept.
            t, rows = thin_stack(t=series.t, rows=rows, limit=CHARTS.stack_points)
            ax.stackplot(t, *rows, colors=colors, labels=labels, linewidth=0, step="post")
        else:
            t = series.t
            ax.stackplot(
                t, *rows, colors=colors, labels=labels, edgecolor=palette.surface, linewidth=CHARTS.surface_gap
            )
        _label_bands(ax, t=t, rows=rows, labels=labels, palette=palette)
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
    subtitle = (
        "stacked; a process contributes nothing in the sample it first "
        "appears in, because there is no earlier counter to compare"
    )
    metric = Metric.CPU
    rank: Ranking = staticmethod(by_cpu_seconds)
    ylabel = "cpu %"


class MemoryByProcessPage(_StackedPage):
    """Which process was holding the memory."""

    title = "Memory by process"
    subtitle = "stacked rss; shared pages are counted once per process, so this reads high"
    metric = Metric.RSS
    rank: Ranking = staticmethod(by_peak_rss)
    ylabel = "rss"


class RankingPage(Page):
    """One league table, each process on a row of its own across the page.

    A row carries the process' whole command line, wrapped rather than cut:
    the path of what ran is often all that tells two processes of the same name
    apart.
    """

    def __init__(self, *, subtitle: str, rank: Ranking, formatter: Callable[[float], str]) -> None:
        self.title = "Biggest consumers"
        self.subtitle = subtitle
        self._rank = rank
        self._formatter = formatter

    @override
    def draw(self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT) -> bool:
        if not series.has_per_process:
            return False
        style.draw_heading(fig, title=self.title, subtitle=self.subtitle, palette=palette)
        tracks = series.top_tracks(rank=self._rank, limit=CHARTS.ranking_rows)
        widest = max((self._rank(track) for track in tracks), default=0.0)
        point = 1 / (CHARTS.page_size[1] * 72)
        y = CHARTS.ranking_top
        shown = 0
        for number, track in enumerate(tracks, start=1):
            lines = _command_lines(track.cmdline or track.name)
            height = (CHARTS.ranking_row_points + len(lines) * CHARTS.command_line_points) * point
            if shown and y - height < CHARTS.ranking_bottom:
                break
            share = self._rank(track) / widest if widest else 0.0
            self._row(fig, track=track, number=number, lines=lines, share=share, y=y, point=point, palette=palette)
            y -= height
            shown += 1
        fig.add_artist(Line2D([CHARTS.ranking_left - 0.025, 0.94], [y, y], color=palette.grid, linewidth=0.8))
        if shown < len(tracks):
            style.draw_footer(fig, left=f"{shown} of the top {len(tracks)} fit on this page", right="", palette=palette)
        return True

    def _row(
        self,
        fig: Figure,
        *,
        track: ProcessTrack,
        number: int,
        lines: Sequence[str],
        share: float,
        y: float,
        point: float,
        palette: Palette,
    ) -> None:
        left = CHARTS.ranking_left
        color = palette.slot(DISCOVERY_ORDER.index(track.group))
        fig.add_artist(Line2D([left - 0.025, 0.94], [y, y], color=palette.grid, linewidth=0.8))
        baseline = y - 20 * point
        fig.text(left - 0.025, baseline, str(number), fontsize=9, color=palette.text_muted)
        name = fig.text(left, baseline, track.name, fontsize=10.5, fontweight="bold", color=palette.text_primary)
        pid = Annotation(
            str(track.pid),
            xy=(1, baseline),
            xycoords=(name, "figure fraction"),
            xytext=(6, 0),
            textcoords="offset points",
            fontsize=8.5,
            color=palette.text_muted,
        )
        fig.add_artist(pid)
        fig.add_artist(
            Annotation(
                track.group.upper(),
                xy=(1, baseline),
                xycoords=(pid, "figure fraction"),
                xytext=(6, 0),
                textcoords="offset points",
                fontsize=7,
                fontweight="bold",
                color=color,
            )
        )
        fig.text(
            0.94,
            baseline,
            self._formatter(self._rank(track)),
            ha="right",
            fontsize=12,
            fontweight="bold",
            color=palette.text_primary,
        )
        line_y = baseline - 15 * point
        for line in lines:
            fig.text(left, line_y, line, fontsize=7.5, family="monospace", color=palette.text_secondary)
            line_y -= CHARTS.command_line_points * point
        bar = line_y + CHARTS.command_line_points * point - 9 * point
        width = 0.94 - left
        fig.add_artist(Rectangle((left, bar), width, 3 * point, color=palette.grid, linewidth=0))
        fig.add_artist(Rectangle((left, bar), width * share, 3 * point, color=color, linewidth=0))


class SamplingPage(Page):
    """Can you trust the numbers on the other pages?"""

    title = "Sampling quality"
    subtitle = (
        "polling cannot see a process that starts and ends between two "
        "samples; these show how tight the sampling actually was"
    )

    @override
    def draw(self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT) -> bool:
        if len(series.t) < 3:
            return False
        style.draw_heading(fig, title=self.title, subtitle=self.subtitle, palette=palette)
        left, right = fig.subplots(1, 2)
        fig.subplots_adjust(left=0.08, right=0.96, top=0.84, bottom=0.13, wspace=0.22)

        # Plotted as error against the requested interval: the raw gaps differ
        # by microseconds, and a histogram of those looks alarming at a glance
        # purely because the axis is zoomed to nothing.
        errors = [(gap - series.interval) * 1000 for gap in series.gaps]
        if errors:
            left.hist(
                errors,
                bins=min(40, max(8, len(errors) // 4)),
                color=palette.slot(0),
                edgecolor=palette.surface,
                linewidth=0.8,
            )
            left.axvline(0, color=palette.text_muted, linewidth=1.2, linestyle=(0, (4, 4)))
            worst = max(errors, key=abs)
            left.set_title(f"sampling error · worst {worst:+.1f} ms")
            # "Late" would be wrong for half of them: a sample that follows a
            # slow one arrives early, and that is what a negative value is.
            left.set_xlabel(f"error against the requested {series.interval}s interval (ms; negative is early)")
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
                    f"p{quantile} {format_percent(value)}",
                    xy=(value, quantile),
                    xytext=(-6 if near_edge else 6, 0),
                    textcoords="offset points",
                    ha="right" if near_edge else "left",
                    va="center",
                    fontsize=8,
                    color=palette.text_secondary,
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

    Only bands with room for a word are labelled - a label that needs a leader
    line is worse than the legend it duplicates. Room means both ways: thick
    enough, and for long enough. A band that is tall for a single sample is a
    spike, and a white label centred on it lands on the white page beside it.
    """
    ceiling = max((sum(column) for column in zip(*rows, strict=True)), default=0.0)
    if ceiling <= 0 or not t:
        return
    bottoms = [0.0] * len(t)
    for values, label in zip(rows, labels, strict=True):
        span = _widest_run(values, threshold=ceiling * CHARTS.band_label_share)
        if span is not None and t[span[1]] - t[span[0]] >= (t[-1] - t[0]) * CHARTS.band_label_span:
            middle = (span[0] + span[1]) // 2
            ax.text(
                t[middle],
                bottoms[middle] + values[middle] / 2,
                label,
                ha="center",
                va="center",
                fontsize=7.5,
                fontweight="bold",
                color=palette.surface,
            )
        bottoms = [base + value for base, value in zip(bottoms, values, strict=True)]


def _widest_run(values: Sequence[float], *, threshold: float) -> tuple[int, int] | None:
    """First and last index of the longest stretch at or above ``threshold``."""
    best: tuple[int, int] | None = None
    start: int | None = None
    for index, value in enumerate([*values, float("-inf")]):
        if value >= threshold and threshold > 0:
            start = index if start is None else start
            continue
        if start is not None and (best is None or index - 1 - start > best[1] - best[0]):
            best = (start, index - 1)
        start = None
    return best


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


def _command_lines(command: str) -> list[str]:
    """A command line wrapped to the page, with only a pathological one cut.

    It breaks at spaces where it can and inside a long path where it must; past
    :attr:`CHARTS.command_lines` lines the rest is left to the HTML report,
    which shows it all - the path of what ran is in the first line regardless.
    """
    lines = textwrap.wrap(command, width=CHARTS.command_width, break_long_words=True, break_on_hyphens=False) or [
        command
    ]
    if len(lines) > CHARTS.command_lines:
        lines = [
            *lines[: CHARTS.command_lines - 1],
            truncate(lines[CHARTS.command_lines - 1], width=CHARTS.command_width - 1) + "…",
        ]
    return lines


def _asleep_note(series: RunSeries) -> str:
    """How much of the duration the machine slept through, when it did."""
    return f" · {format_seconds(series.asleep_s)} asleep" if series.sleeps else ""


def _broken_at_sleeps(series: RunSeries, values: Sequence[float | int | None]) -> tuple[list[float], list[float]]:
    """``(t, values)`` with a break inside each sleep, so no line is drawn across it.

    Nothing was measured while the machine slept; a line from the last reading
    before to the first after would invent a slope.
    """
    starts = dict(series.sleeps)
    t: list[float] = []
    out: list[float] = []
    for moment, value in zip(series.t, _with_gaps(values), strict=True):
        t.append(moment)
        out.append(value)
        if moment in starts:
            t.append((moment + starts[moment]) / 2)
            out.append(float("nan"))
    return t, out


def _with_gaps(values: Sequence[float | int | None]) -> list[float]:
    """Missing readings become NaN, which matplotlib draws as a break.

    A gap must look like a gap: interpolating across "we could not measure"
    would draw a line the data does not support.
    """
    return [float("nan") if value is None else float(value) for value in values]
