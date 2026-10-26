"""Named values for the HTML report: how much of a log it draws, and how."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class HtmlConstants:
    """Point budgets, row limits and tick choices shared by the HTML pages."""

    chart_points: int = 720
    """Points a chart line is reduced to, however long the log. Each keeps the
    peak of the stretch it stands for, so a one-sample spike survives."""

    gap_points: float = 3.0
    """A hole in the readings this many points wide is drawn as a break - the
    machine slept, or treehawk was not running - rather than joined up."""

    y_ticks: int = 4
    """Most gridlines a value axis is given."""

    x_ticks: int = 8
    """Most labelled ticks a time axis is given."""

    elapsed_steps: tuple[float, ...] = (
        1.0,
        2.0,
        5.0,
        10.0,
        15.0,
        30.0,
        60.0,
        120.0,
        300.0,
        600.0,
        900.0,
        1800.0,
        3600.0,
        7200.0,
        10800.0,
        21600.0,
        43200.0,
        86400.0,
        172800.0,
        604800.0,
    )
    """Tick spacings, in seconds, for a time axis."""

    headroom: float = 1.1
    """How far above its highest reading a chart's axis reaches."""

    capacity_share: float = 0.25
    """A workload must reach this share of the machine before the chart draws
    the machine's full capacity; below it the line would sit flat at the
    bottom of an empty chart."""

    band_label_share: float = 0.06
    """Share of the chart's width a shaded stretch needs before it is named."""

    track_rows: int = 400
    """Processes listed in the tracks table. Past it, the biggest by CPU time
    and by memory are kept, half each, so a build that ran ten thousand
    compilers still opens in a browser."""

    ranking_rows: int = 5
    """Processes in each "biggest consumers" list."""

    event_rows: int = 200
    """Spikes and leak reports listed for a ``top`` log, latest first."""


HTML: Final = HtmlConstants()
