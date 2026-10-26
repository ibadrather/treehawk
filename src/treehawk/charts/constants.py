"""Named values for the PDF report: page geometry, mark sizes and row limits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ChartConstants:
    """Sizes and limits shared by the page writer, the pages and the styling."""

    page_size: tuple[float, float] = (11.69, 8.27)
    """A4 landscape - charts are wider than they are tall."""

    line_width: float = 2.0

    surface_gap: float = 1.5
    """Gap drawn between stacked bands, in the surface colour, so adjacent fills
    read as separate shapes rather than one blob."""

    dense_points: int = 200
    """Samples beyond which one sample is narrower on the page than
    ``surface_gap``. Past this the gap is no longer a hairline between bands:
    it is wider than the bands, and paints over them."""

    stack_points: int = 600
    """Columns a stacked chart is reduced to, about one per point of page
    width. Each keeps the busiest sample it covers, so a spike one sample wide
    in a two-hour run is still a column wide enough to print."""

    min_bar_fraction: float = 0.004
    """Shortest a lifetime bar is drawn, as a share of the run, so a process
    that lived for one sample of a long run is still a visible mark."""

    band_label_share: float = 0.12
    """How much of the chart's height a band needs before its name fits on it."""

    band_label_span: float = 0.10
    """And how much of the chart's width it must hold that height for. A band
    that is tall for one sample has nowhere to put a word."""

    note_width: int = 150
    """Characters of a header note that fit across the overview page."""

    time_ticks: int = 8
    """Most labelled ticks a time axis is given."""

    minute_steps: tuple[float, ...] = (60.0, 120.0, 300.0, 600.0, 900.0, 1200.0, 1800.0)
    """Tick spacings, in seconds, for an axis read in minutes."""

    hour_steps: tuple[float, ...] = (1800.0, 3600.0, 7200.0, 10800.0, 21600.0, 43200.0, 86400.0)
    """Tick spacings, in seconds, for an axis read in hours."""

    footer_band: float = 0.05
    """Anything a page has already drawn below this is its own footer, and the
    writer must not draw a second one over it."""

    top_series: int = 6
    """Processes charted individually before the rest folds into "other".

    The categorical palette is six slots deep and is never cycled, so a seventh
    series would have to repeat a colour - "other" is the honest alternative.
    """

    max_gantt_rows: int = 34
    """Lifetime bars that fit legibly on one page before the rest is summarised."""

    ranking_rows: int = 10
    """Processes in each league table on the "biggest consumers" pages."""

    ranking_top: float = 0.87
    """Where the first row of a league table starts, as a share of the page."""

    ranking_bottom: float = 0.07
    """Rows stop above this, clear of the footer."""

    ranking_left: float = 0.085
    """Where a row's name, command and bar start; its rank sits just left."""

    ranking_row_points: float = 46.0
    """Height of a league-table row without its command, in points: the name
    line, the bar and the space around them."""

    command_line_points: float = 10.5
    """Height of one line of a command, in points."""

    command_width: int = 155
    """Characters of a command that fit across the page in 7.5pt Fira Code."""

    command_lines: int = 6
    """Most lines a command is given on a league table. The path of what ran is
    in the first line; only an enormous argument list reaches this."""

    top_points: int = 2000
    """Time buckets a ``top`` chart is reduced to, however long the log."""

    top_gap_buckets: float = 3.0
    """A hole this many buckets wide in a ``top`` series is drawn as a break
    - the machine was off, or treehawk was - rather than joined up."""


CHARTS: Final = ChartConstants()
