"""Pieces both HTML pages are built from: a shared time axis, and its charts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from treehawk.htmlreport.models import Band, Chart, Line, Tick, Unit
from treehawk.htmlreport.scale import value_axis


@dataclass(frozen=True, slots=True)
class Axis:
    """The time axis every chart of the run shares."""

    x: list[float]
    when: list[str]
    ticks: list[Tick]
    bands: list[Band]


def chart(*, axis: Axis, title: str, unit: Unit, peak: float | None, lines: list[Line], caption: str) -> Chart:
    top, y_ticks = value_axis(peak, unit=unit)
    return Chart(
        title=title,
        unit=unit,
        x=axis.x,
        when=axis.when,
        top=top,
        y_ticks=y_ticks,
        x_ticks=axis.ticks,
        lines=lines,
        caption=caption,
        bands=list(axis.bands),
    )


def peak_of(values: Sequence[float | None]) -> float | None:
    """The highest reading, or None when there is none."""
    return max((value for value in values if value is not None), default=None)


def platform_name(platform: object) -> str:
    """A platform as people name it: Linux, macOS."""
    names = {"linux": "Linux", "darwin": "macOS"}
    return names.get(str(platform), str(platform or "?"))
