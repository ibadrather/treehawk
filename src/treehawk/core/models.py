"""Value objects shared across the application.

Plain data carriers with no behaviour beyond trivial derivations, so that every
other component can depend on them without depending on each other.
"""

from __future__ import annotations

import signal
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Final, Mapping, TypeAlias

Identity: TypeAlias = tuple[int, int]
"""A process identity: ``(pid, starttime)``.

The kernel recycles PIDs, so a bare PID is not a stable key. ``starttime`` (the
process start time in clock ticks since boot) makes the pair unique for the
lifetime of the machine.
"""

ZOMBIE_STATE: Final = "Z"
"""Process state for an exited process still awaiting its parent's ``wait()``."""


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
        return self.state == ZOMBIE_STATE


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
