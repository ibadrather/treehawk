"""Value objects shared across the application.

Plain data carriers with no behaviour beyond trivial derivations, so that every
other component can depend on them without depending on each other.
"""

from __future__ import annotations

import signal
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypeAlias, TypedDict

from treehawk.core.constants import CORE

Identity: TypeAlias = tuple[int, int]
"""A process identity: ``(pid, starttime)``.

The kernel recycles PIDs, so a bare PID is not a stable key. ``starttime`` (the
process start time in clock ticks since boot) makes the pair unique for the
lifetime of the machine.
"""


@dataclass(frozen=True, slots=True)
class ProcInfo:
    """Cheap per-process facts, available from a single stat read."""

    pid: int
    ppid: int
    pgid: int
    sid: int
    starttime: int
    state: str
    threads: int
    comm: str
    cpu_ticks: int
    rss_bytes: int | None

    @property
    def identity(self) -> Identity:
        return (self.pid, self.starttime)

    @property
    def is_zombie(self) -> bool:
        return self.state == CORE.zombie_state


@dataclass(slots=True)
class ProcSample:
    """A tracked process at one instant: cheap facts plus enriched fields."""

    info: ProcInfo
    cmdline: str = ""
    pss_bytes: int | None = None
    swap_bytes: int | None = None
    cgroup: str | None = None
    via: str = "seed"
    cpu_percent: float | None = None

    @property
    def identity(self) -> Identity:
        return self.info.identity

    @property
    def label(self) -> str:
        return self.cmdline or self.info.comm


@dataclass(frozen=True, slots=True)
class GroupMetrics:
    """Kernel-side aggregates for a process group boundary (a cgroup)."""

    path: str
    cpu_usec: int | None = None
    memory_bytes: int | None = None
    memory_peak_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class HostInfo:
    """Facts about the machine, read once at startup."""

    platform: str
    hostname: str
    ncpu: int
    clk_tck: int
    page_size: int
    mem_total_bytes: int | None


@dataclass(slots=True)
class Snapshot:
    """One fully computed sample: the aggregate plus its per-process detail."""

    seq: int
    t: float
    timestamp: str
    procs: list[ProcSample] = field(default_factory=list)
    cpu_percent: float | None = None
    cpu_percent_norm: float | None = None
    cpu_seconds_total: float = 0.0
    cpu_seconds_used: float = 0.0
    rss_bytes: int | None = None
    pss_bytes: int | None = None
    swap_bytes: int | None = None
    group: GroupMetrics | None = None
    overrun: bool = False
    extra: dict[str, Mapping[str, object]] = field(default_factory=dict)

    @property
    def n_procs(self) -> int:
        return len(self.procs)


@dataclass(slots=True)
class RefreshResult:
    """Outcome of one membership pass."""

    alive: list[ProcInfo] = field(default_factory=list)
    zombies: list[ProcInfo] = field(default_factory=list)
    via: dict[int, str] = field(default_factory=dict)
    admitted: list[Identity] = field(default_factory=list)
    exited: list[Identity] = field(default_factory=list)
    alive_cpu_ticks: int = 0
    exited_cpu_ticks: int = 0

    @property
    def total_cpu_ticks(self) -> int:
        return self.alive_cpu_ticks + self.exited_cpu_ticks


class ProcessTotals(TypedDict):
    """Per-process figures accumulated over a whole run."""

    pid: int
    name: str
    cmdline: str
    via: str
    cpu_seconds: float
    peak_rss_bytes: int


@dataclass(slots=True)
class RunSummary:
    """Whole-run figures, computed incrementally so nothing is kept in memory."""

    samples: int = 0
    duration_s: float = 0.0
    peak_cpu_percent: float | None = None
    mean_cpu_percent: float | None = None
    peak_n_procs: int = 0
    total_procs_seen: int = 0
    peak_rss_bytes: int | None = None
    peak_pss_bytes: int | None = None
    peak_group_memory_bytes: int | None = None
    cpu_seconds_total: float = 0.0
    cpu_seconds_used: float = 0.0
    overruns: int = 0
    exit_code: int | None = None
    top_by_cpu: list[ProcessTotals] = field(default_factory=list)
    top_by_memory: list[ProcessTotals] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MemoryMeasure:
    """How one platform's fair-memory figure is named and explained.

    Every platform can report resident memory, and summing it over a fork tree
    over-counts every shared page. What each offers *instead* differs - Linux
    has PSS, macOS has the kernel's phys_footprint - so the log records which
    one it carries and the views read the names from here.
    """

    key: str
    short: str
    """Fits a narrow column and a one-line sample."""
    long: str
    """Names the measure in a chart legend or a summary line."""
    blurb: str
    """One clause saying what the measure means, for a chart subtitle."""


@dataclass(frozen=True, slots=True)
class MemoryReading:
    """The best memory figure in a record, and the name of the measure it is."""

    value: int | None
    label: str


class LaunchedWorkload(ABC):
    """A process treehawk started, and the two things it needs to do to it.

    An abstract class rather than a bag of callables: it keeps the signalling
    and reaping rules with the implementation that knows how the process was
    started, and it is substitutable - the CLI treats a systemd scope and a bare
    subprocess identically.
    """

    def __init__(
        self,
        *,
        pid: int,
        argv: list[str],
        group_path: str | None = None,
    ) -> None:
        self.pid = pid
        self.argv = argv
        self.group_path = group_path

    @property
    def isolated(self) -> bool:
        """True when the workload has an accounting boundary of its own.

        Derived rather than declared: owning a cgroup path *is* what being
        isolated means, so the two cannot drift apart.
        """
        return self.group_path is not None

    @abstractmethod
    def poll(self) -> int | None:
        """Exit code if the workload has finished, else ``None``. Never blocks."""

    @abstractmethod
    def signal(self, signum: int = signal.SIGTERM) -> None:
        """Forward a signal to the workload, ignoring a process already gone."""
