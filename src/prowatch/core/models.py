"""Value objects shared across the application.

These are plain data carriers with no behaviour beyond trivial derivations, so
that every other component can depend on them without depending on each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Identity = tuple[int, int]
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
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def n_procs(self) -> int:
        return len(self.procs)


@dataclass(slots=True)
class LaunchedWorkload:
    """Handle on a process started by prowatch itself."""

    pid: int
    argv: list[str]
    group_path: str | None
    wait: object  # callable() -> int | None, returns the exit code
    signal: object  # callable(signum) -> None, forwards a signal
    isolated: bool = False
