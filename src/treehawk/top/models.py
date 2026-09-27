"""Value objects for ``top``: what is ranked, what is detected, what is kept."""

from __future__ import annotations

from dataclasses import dataclass, field

from treehawk.core.compat import StrEnum
from treehawk.core.interfaces import Record
from treehawk.core.models import GpuUsage, Identity, ProcessTotals, ProcSample


class Resource(StrEnum):
    """What a process can be ranked, or flagged, by."""

    CPU = "cpu"
    MEMORY = "mem"
    GPU = "gpu"


class EventKind(StrEnum):
    """Something worth a line of its own in the log."""

    ENTER = "enter"
    """A process joined the top N."""

    LEAVE = "leave"
    """A process dropped out of the top N, or exited while in it."""

    SPIKE = "spike"
    """A reading far above that process' own recent baseline."""

    CREEP = "creep"
    """Memory that has kept rising, steadily, for a long time: a leak suspect."""


HOST_PID = 0
"""The pid an event about the whole machine carries. No process has it."""


@dataclass(frozen=True, slots=True)
class SpikeRule:
    """When a jump counts as a spike.

    A reading is a spike when it exceeds the running mean by ``factor``
    standard deviations *and* by at least ``floor`` in absolute terms, so a
    process idling at 0.1% cannot fire by reaching 0.5%.
    """

    factor: float
    floor: float
    time_constant: float
    """Seconds the baseline takes to follow a new level (the EWMA's tau)."""
    warmup: float
    """Seconds a key must be watched before it may fire at all."""
    cooldown: float
    """Seconds after a spike during which the same key stays quiet."""


@dataclass(frozen=True, slots=True)
class CreepRule:
    """When slow growth counts as a leak suspect.

    Memory is reduced to one point per bucket - the bucket's *minimum*, so a
    sawtooth from a garbage collector reads as its floor - and a straight line
    is fitted through the last ``window`` points.
    """

    bucket_seconds: float
    window: int
    min_slope_per_hour: float
    """Bytes per hour the fitted line must rise by."""
    min_r2: float
    """How straight the rise must be, from 0 (noise) to 1 (a ruler)."""
    min_growth: float
    """Bytes the floor must have grown across the window."""
    refire_growth: float
    """Fractional growth past the last report before the same key reports again."""


@dataclass(frozen=True, slots=True)
class Spike:
    value: float
    baseline: float


@dataclass(frozen=True, slots=True)
class Creep:
    slope_per_hour: float
    r2: float
    first: float
    last: float


@dataclass(slots=True)
class RankedProc:
    """A process in the top N at one instant, and why it is there."""

    sample: ProcSample
    reasons: frozenset[Resource]
    gpu: GpuUsage | None = None

    @property
    def identity(self) -> Identity:
        return self.sample.identity


@dataclass(frozen=True, slots=True)
class Selection:
    """The outcome of one ranking pass."""

    chosen: dict[Identity, frozenset[Resource]]
    """Identity to the resources that put it in the top N, busiest first."""
    entered: list[Identity]
    left: list[Identity]


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Every detector rule ``top`` applies, in one bundle."""

    cpu_spike: SpikeRule
    memory_spike: SpikeRule
    host_cpu_spike: SpikeRule
    host_memory_spike: SpikeRule
    creep: CreepRule
    host_creep: CreepRule


@dataclass(frozen=True, slots=True)
class TopEvent:
    """One entry of the event stream."""

    kind: EventKind
    t: float
    ts: str
    pid: int
    starttime: int
    name: str
    resource: Resource | None = None
    value: float | None = None
    baseline: float | None = None
    slope_per_hour: float | None = None
    r2: float | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class HostReading:
    """The machine-wide line of one sample."""

    cpu_percent: float | None
    """Busy share of every CPU, 0 to 100."""
    mem_used_bytes: int | None
    mem_available_bytes: int | None
    swap_used_bytes: int | None


@dataclass(slots=True)
class TopSummary:
    """One log segment, folded incrementally so nothing is kept in memory."""

    samples: int = 0
    duration_s: float = 0.0
    overruns: int = 0
    mean_interval_s: float | None = None
    peak_host_cpu_percent: float | None = None
    mean_host_cpu_percent: float | None = None
    peak_host_mem_used_bytes: int | None = None
    peak_n_procs: int = 0
    total_procs_seen: int = 0
    events: dict[str, int] = field(default_factory=dict)
    top_by_cpu: list[ProcessTotals] = field(default_factory=list)
    top_by_memory: list[ProcessTotals] = field(default_factory=list)


@dataclass(slots=True)
class ProcessHistory:
    """One process across a ``top`` log, as read back."""

    pid: int
    starttime: int
    name: str
    cmdline: str
    first_seen: float
    """Wall-clock seconds (Unix time) of its first row."""
    last_seen: float
    samples: int = 0
    cpu_seconds: float = 0.0
    """CPU time used while in the top N."""
    peak_cpu_percent: float = 0.0
    peak_rss_bytes: int = 0
    first_rss_bytes: int = 0
    last_rss_bytes: int = 0

    @property
    def identity(self) -> Identity:
        return (self.pid, self.starttime)

    @property
    def label(self) -> str:
        return f"{self.name} ({self.pid})"


@dataclass(slots=True)
class TopHistory:
    """A ``top`` log, read back: one file, or a directory of segments."""

    header: Record
    segments: int = 0
    samples: int = 0
    overruns: int = 0
    first_time: float | None = None
    last_time: float | None = None
    peak_host_cpu_percent: float | None = None
    mean_host_cpu_percent: float | None = None
    peak_host_mem_used_bytes: int | None = None
    processes: dict[Identity, ProcessHistory] = field(default_factory=dict)
    events: list[Record] = field(default_factory=list)
    """Spikes and leak suspects, oldest first. Entries and exits are only counted."""
    event_counts: dict[str, int] = field(default_factory=dict)
