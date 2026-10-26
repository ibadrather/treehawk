"""Axes and point budgets for the HTML charts.

Pure arithmetic, no markup: which gridlines a value axis gets, where the ticks
of a time axis fall, and how a long series is cut down to a size a browser
draws instantly without losing its peaks.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timezone

from treehawk.htmlreport.constants import HTML
from treehawk.htmlreport.models import Tick, Unit
from treehawk.top.timeline import Timeline

_BYTE_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
_NICE_STEPS = (1.0, 2.0, 2.5, 5.0, 10.0)


def value_axis(peak: float | None, *, unit: Unit, count: int = HTML.y_ticks) -> tuple[float, list[Tick]]:
    """The top of a value axis that holds ``peak``, and its gridlines.

    Gridlines fall on round numbers - in binary units for bytes, so a memory
    axis reads 2 GiB, 4 GiB rather than 2.1 GB, 4.3 GB - and every label on
    one axis is in the same unit.
    """
    base = _byte_base(peak) if unit is Unit.BYTES else 1.0
    reach = (peak or 0.0) * HTML.headroom / base
    if unit is not Unit.BYTES:
        reach = max(reach, 1.0)
    if reach <= 0:
        reach = 1.0
    raw = reach / count
    magnitude = math.pow(10.0, math.floor(math.log10(raw)))
    step = next(candidate * magnitude for candidate in _NICE_STEPS if candidate * magnitude >= raw)
    if unit is Unit.COUNT:
        step = max(step, 1.0)
    steps = math.ceil(reach / step - 1e-9)
    suffix = _suffix(unit=unit, base=base)
    ticks = [
        Tick(position=index / steps, label=f"{index * step:g}{suffix}" if index else "0") for index in range(steps + 1)
    ]
    return steps * step * base, ticks


def _suffix(*, unit: Unit, base: float) -> str:
    if unit is Unit.PERCENT:
        return "%"
    if unit is Unit.BYTES:
        return f" {_BYTE_UNITS[round(math.log(base, 1024))]}"
    return ""


def _byte_base(value: float | None) -> float:
    """The power of 1024 that ``value`` reads best in."""
    size = abs(value or 0.0)
    exponent = 0
    while size >= 1024 and exponent < len(_BYTE_UNITS) - 1:
        size /= 1024
        exponent += 1
    return float(1024**exponent)


def elapsed_ticks(duration: float, *, count: int = HTML.x_ticks) -> list[Tick]:
    """Ticks for an axis that counts time from the start: 0, 10m, 20m ..."""
    if duration <= 0:
        return [Tick(position=0.0, label="0")]
    step = _step_for(duration, count=count)
    ticks: list[Tick] = []
    moment = 0.0
    while moment <= duration + 1e-9:
        ticks.append(Tick(position=moment / duration, label=elapsed_label(moment)))
        moment += step
    return ticks


def clock_ticks(start: float, end: float, *, count: int = HTML.x_ticks) -> list[Tick]:
    """Ticks for an axis of wall-clock time, on round local times: 14:00, 15:00 ..."""
    span = end - start
    if span <= 0:
        return [Tick(position=0.0, label=clock_label(start, span=0.0))]
    step = _step_for(span, count=count)
    offset = _utc_offset(start)
    moment = math.ceil((start + offset) / step) * step - offset
    ticks: list[Tick] = []
    while moment <= end + 1e-9:
        ticks.append(Tick(position=(moment - start) / span, label=clock_label(moment, span=span, step=step)))
        moment += step
    return ticks


def _step_for(span: float, *, count: int) -> float:
    return next((step for step in HTML.elapsed_steps if span / step <= count), HTML.elapsed_steps[-1])


def _utc_offset(epoch: float) -> float:
    offset = local(epoch).utcoffset()
    return offset.total_seconds() if offset is not None else 0.0


def local(epoch: float) -> datetime:
    """Unix seconds as an aware datetime in this machine's time zone."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone()


def clock_label(epoch: float, *, span: float, step: float = 0.0) -> str:
    """A wall-clock tick: the time alone, with the day once a span crosses days."""
    moment = local(epoch)
    if step >= 86400:
        return moment.strftime("%d %b")
    if span >= 86400:
        return moment.strftime("%a %H:%M")
    if step and step < 60:
        return moment.strftime("%H:%M:%S")
    return moment.strftime("%H:%M")


def elapsed_label(seconds: float) -> str:
    """Time since the start, as a tick says it: ``45s``, ``1m30s``, ``2h``, ``1d6h``."""
    if seconds < 60:
        tenths = round(seconds, 1)
        return f"{tenths:g}s" if tenths else "0"
    whole = round(seconds)
    days, rest = divmod(whole, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(days, "d"), (hours, "h"), (minutes, "m"), (secs, "s")]
    shown = [f"{amount}{suffix}" for amount, suffix in parts if amount]
    return "".join(shown[:2])


def thin(
    *, x: Sequence[float], columns: Sequence[Sequence[float | None]], limit: int = HTML.chart_points
) -> tuple[list[float], list[list[float | None]]]:
    """Cut a series down to about ``limit`` points, each the peak of its stretch.

    A series already that short is returned as it is. Averaging would flatten
    a one-sample spike to nothing; the peak keeps it.
    """
    if len(x) <= limit:
        return list(x), [list(column) for column in columns]
    timeline: Timeline[int] = Timeline(limit=limit, width=max((x[-1] - x[0]) / limit, 1e-9))
    for moment in x:
        timeline.mark(moment)
    for key, column in enumerate(columns):
        for moment, value in zip(x, column, strict=True):
            if value is not None:
                timeline.add(key=key, t=moment, value=value)
    return timeline.times(), [timeline.values(key) for key in range(len(columns))]


def with_breaks(
    *, x: Sequence[float], columns: Sequence[Sequence[float | None]], gap: float
) -> tuple[list[float], list[list[float | None]]]:
    """Insert an empty point inside every hole wider than ``gap``.

    Nothing was measured there - the machine slept, or nothing was running -
    and a line from the last reading before to the first after would invent a
    slope the data does not have.
    """
    out_x: list[float] = []
    out: list[list[float | None]] = [[] for _ in columns]
    for index, moment in enumerate(x):
        if index and moment - x[index - 1] > gap:
            out_x.append((moment + x[index - 1]) / 2)
            for column in out:
                column.append(None)
        out_x.append(moment)
        for column, source in zip(out, columns, strict=True):
            column.append(source[index])
    return out_x, out


def clock(epoch: float) -> str:
    """Local time of day, to the second."""
    return local(epoch).strftime("%H:%M:%S")


def day(epoch: float) -> str:
    """Local date, with the weekday and the time zone it is read in."""
    return local(epoch).strftime("%a %d %b %Y · %Z")


def stamp(epoch: float) -> str:
    """Local date and time together, for a details list."""
    return local(epoch).strftime("%Y-%m-%d %H:%M:%S %Z")
