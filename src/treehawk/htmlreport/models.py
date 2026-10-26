"""What an HTML report page is made of.

Plain carriers: :mod:`treehawk.htmlreport.run` and
:mod:`treehawk.htmlreport.top` fill them from a log, and
:mod:`treehawk.htmlreport.document` turns them into markup. Text in them is
plain; escaping happens once, where the markup is written.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from treehawk.core.compat import StrEnum


class Level(StrEnum):
    """How much a notice needs the reader's attention."""

    CRITICAL = "critical"
    WARNING = "warning"
    NOTE = "note"
    GOOD = "good"
    """All is well: a clean exit. Used for status chips, never a warning."""


class Unit(StrEnum):
    """What a chart's values measure, which decides its axis labels."""

    PERCENT = "percent"
    BYTES = "bytes"
    COUNT = "count"


@dataclass(frozen=True, slots=True)
class Tick:
    """A labelled place on an axis, as a fraction of its length."""

    position: float
    label: str


@dataclass(frozen=True, slots=True)
class Line:
    """One series on a chart."""

    label: str
    color: str
    """A colour token of the page: ``s1`` to ``s6``, ``ink`` or ``muted``."""
    values: list[float | None]
    readouts: list[str]
    """Each value as the reader should see it on hover."""
    summary: str
    """What the legend says when nothing is hovered: usually the peak."""
    fill: bool = False
    step: bool = False


@dataclass(frozen=True, slots=True)
class Rule:
    """A horizontal reference line: a mean, or the machine's capacity."""

    value: float
    label: str


@dataclass(frozen=True, slots=True)
class Band:
    """A shaded stretch of the time axis, as fractions of it."""

    start: float
    end: float
    label: str


@dataclass(frozen=True, slots=True)
class Mark:
    """A moment worth flagging on the time axis: a late sample, a spike."""

    position: float
    level: Level
    label: str


@dataclass(slots=True)
class Chart:
    """A time series chart, already placed on a shared 0-1 time axis."""

    title: str
    unit: Unit
    x: list[float]
    when: list[str]
    """The time of each point as the reader should see it on hover."""
    top: float
    y_ticks: list[Tick]
    x_ticks: list[Tick]
    lines: list[Line] = field(default_factory=list)
    caption: str = ""
    rules: list[Rule] = field(default_factory=list)
    bands: list[Band] = field(default_factory=list)
    marks: list[Mark] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Figure:
    """One headline number, or one end of the page's time span."""

    label: str
    value: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Notice:
    """Something the reader should know before trusting the figures."""

    level: Level
    title: str
    text: str = ""


@dataclass(frozen=True, slots=True)
class Fact:
    """A label and its value, for the details list."""

    label: str
    value: str
    mono: bool = False


@dataclass(frozen=True, slots=True)
class Process:
    """Who a table row is about."""

    name: str
    pid: int
    command: str
    found: str = ""
    """How treehawk found it (matched, child, detached), or why it was ranked."""


@dataclass(frozen=True, slots=True)
class Cell:
    """A plain table cell: what it shows, and what it sorts by."""

    text: str
    sort: float | str
    level: Level | None = None


@dataclass(frozen=True, slots=True)
class Lifetime:
    """When a process was alive, as fractions of the page's time axis."""

    start: float
    end: float
    label: str


@dataclass(frozen=True, slots=True)
class Column:
    label: str
    numeric: bool = True
    hint: str = ""


@dataclass(slots=True)
class Track:
    """One row of the process tracks table."""

    process: Process
    lifetime: Lifetime
    cells: list[Cell]


@dataclass(slots=True)
class Tracks:
    """The process tracks table: one row per process, across the time axis."""

    columns: list[Column]
    rows: list[Track]
    total: int
    """Processes in the log; more than ``rows`` when the table was cut."""
    axis: tuple[str, str]
    """What the lifetime column's two ends stand for."""
    note: str = ""


@dataclass(frozen=True, slots=True)
class Ranked:
    """One entry of a "biggest consumers" list."""

    process: Process
    value: float
    text: str


@dataclass(frozen=True, slots=True)
class Ranking:
    title: str
    entries: list[Ranked]


@dataclass(slots=True)
class Section:
    """A titled block of the page, holding whichever parts it has."""

    title: str
    intro: str = ""
    charts: list[Chart] = field(default_factory=list)
    rankings: list[Ranking] = field(default_factory=list)
    table: Tracks | None = None
    events: list[list[Cell]] = field(default_factory=list)
    event_columns: list[Column] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)


@dataclass(slots=True)
class Page:
    """A whole report, ready to be written out."""

    title: str
    kind: str
    """What was watched: "run", "watch" or "machine"."""
    headline: str
    status: list[Notice]
    """Short chips beside the headline: the exit code, the host."""
    span: list[Figure]
    """When it started and ended, and how long that was."""
    figures: list[Figure]
    notices: list[Notice]
    sections: list[Section]
    footer: str
