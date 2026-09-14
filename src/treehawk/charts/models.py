"""The data the PDF report is drawn from.

Plain carriers: :mod:`treehawk.charts.series` fills them from a log, and
:mod:`treehawk.charts.pages` reads them to draw.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from treehawk.charts.constants import CHARTS
from treehawk.core.compat import StrEnum
from treehawk.core.interfaces import Record
from treehawk.core.records import target_of
from treehawk.core.values import as_float, as_int, as_mapping
from treehawk.ui.theme import discovery_group


class Metric(StrEnum):
    """Which per-process measurement a chart is built from."""

    CPU = "cpu"
    RSS = "rss"


@dataclass(slots=True)
class ProcessTrack:
    """One process across the whole run."""

    pid: int
    starttime: int
    name: str
    cmdline: str
    via: str
    first_t: float
    last_t: float
    cpu_seconds: float = 0.0
    peak_rss: int = 0
    peak_pss: int = 0
    cpu_at: dict[float, float] = field(default_factory=dict)
    rss_at: dict[float, int] = field(default_factory=dict)

    @property
    def group(self) -> str:
        """How the process was found, as presented in the charts."""
        return discovery_group(self.via)

    @property
    def lifetime(self) -> float:
        return max(self.last_t - self.first_t, 0.0)

    @property
    def label(self) -> str:
        return f"{self.name} ({self.pid})"

    def readings_of(self, metric: Metric) -> Mapping[float, float]:
        """This process' values for ``metric``, keyed by sample time."""
        return self.cpu_at if metric is Metric.CPU else self.rss_at


@dataclass(slots=True)
class RunSeries:
    """Everything the pages draw, already aligned on a shared time axis."""

    header: Record
    summary: Record
    t: list[float] = field(default_factory=list)
    cpu_percent: list[float | None] = field(default_factory=list)
    cpu_percent_norm: list[float | None] = field(default_factory=list)
    cpu_seconds_used: list[float] = field(default_factory=list)
    rss: list[int | None] = field(default_factory=list)
    pss: list[int | None] = field(default_factory=list)
    group_memory: list[int | None] = field(default_factory=list)
    n_procs: list[int] = field(default_factory=list)
    procs_cpu_total: list[float] = field(default_factory=list)
    procs_rss_total: list[float] = field(default_factory=list)
    overrun_t: list[float] = field(default_factory=list)
    gaps: list[float] = field(default_factory=list)
    tracks: list[ProcessTrack] = field(default_factory=list)

    @property
    def has_per_process(self) -> bool:
        return bool(self.tracks)

    @property
    def duration(self) -> float:
        return self.t[-1] if self.t else 0.0

    @property
    def interval(self) -> float:
        return as_float(self.header.get("interval")) or 1.0

    @property
    def ncpu(self) -> int:
        host = as_mapping(self.header.get("host"))
        return as_int(host.get("ncpu")) or 1

    @property
    def target(self) -> str:
        return target_of(self.header)

    def charted_total(self, metric: Metric) -> list[float]:
        """The per-process rows summed at each instant, for ``metric``."""
        if metric is Metric.CPU:
            return self.procs_cpu_total
        return self.procs_rss_total

    def top_tracks(
        self, *, rank: Callable[[ProcessTrack], float], limit: int = CHARTS.top_series
    ) -> list[ProcessTrack]:
        """The ``limit`` processes that matter most by ``rank``, biggest first."""
        return sorted(self.tracks, key=rank, reverse=True)[:limit]
