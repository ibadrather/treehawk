"""The abstractions every other layer depends on.

Kept deliberately narrow (Interface Segregation): a component asks for the one
capability it uses, not for a god-object "backend". Concrete Linux/macOS/GPU
implementations satisfy these structurally - no inheritance required.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeAlias, runtime_checkable

from treehawk.core.config import MemoryDetail
from treehawk.core.models import (
    GroupMetrics,
    HostInfo,
    Identity,
    LaunchedWorkload,
    ProcInfo,
    ProcSample,
    Snapshot,
)

Record: TypeAlias = dict[str, object]
"""One serialised log record. Deliberately loose: sinks must stay indifferent to
which fields a schema version happens to carry, so a value is narrowed where it
is read (see :mod:`treehawk.core.values`)."""


@runtime_checkable
class ProcessSource(Protocol):
    """Reads live process state from the OS."""

    def scan(self) -> Mapping[int, ProcInfo]:
        """Return cheap facts for every visible process, one pass."""
        ...

    def read_info(self, pid: int) -> ProcInfo | None:
        """Return cheap facts for one process, or None if it is gone."""
        ...

    def read_cmdline(self, pid: int) -> str:
        """Return the full command line, or ``""`` if unreadable."""
        ...

    def read_group_path(self, pid: int) -> str | None:
        """Return the process' group boundary path, if the OS has one.

        A cgroup on Linux, a coalition on macOS: whatever a forked child
        cannot leave by accident.
        """
        ...

    def enrich(self, *, info: ProcInfo, memory: MemoryDetail) -> ProcSample:
        """Add the costlier fields (command line, fair memory, swap, group).

        Which measure ``ProcSample.pss_bytes`` carries is the platform's
        choice; it is named in the log header (see ``MemoryMeasure``).
        """
        ...

    def forget(self, identity: Identity) -> None:
        """Drop any cached state for a process that has exited."""
        ...


@runtime_checkable
class GroupMetricSource(Protocol):
    """Reads a process group boundary: its membership, and any kernel totals.

    ``metrics`` may return None on a platform that has a boundary but exposes
    no aggregate for it - macOS coalitions are one. Membership is still worth
    having on its own: it is what the orphan rule follows.
    """

    def pids_in(self, path: str) -> set[int] | None:
        """PIDs in ``path`` and all of its descendants, or None if unreadable."""
        ...

    def metrics(self, path: str) -> GroupMetrics | None: ...


@runtime_checkable
class ProcessLauncher(Protocol):
    """Starts a workload, ideally inside its own accounting boundary."""

    @property
    def name(self) -> str: ...

    def available(self) -> bool: ...

    def launch(self, argv: list[str]) -> LaunchedWorkload: ...


@runtime_checkable
class HostInfoSource(Protocol):
    def host_info(self) -> HostInfo: ...


@runtime_checkable
class ProcessMatcher(Protocol):
    """Decides whether a process is the workload the user asked for."""

    @property
    def needs_cmdline(self) -> bool:
        """True if :meth:`matches` inspects the command line."""
        ...

    @property
    def names_one_process(self) -> bool:
        """True if the matcher names a single process outright.

        The exclusions that stop a keyword from matching the shell you typed it
        in do not apply to a matcher this specific - you meant that process.
        """
        ...

    def matches(self, *, info: ProcInfo, cmdline: str) -> bool: ...

    def describe(self) -> Record:
        """Serializable description, recorded in the log header."""
        ...


@runtime_checkable
class ExpansionStrategy(Protocol):
    """Finds processes that belong to the workload but were not matched directly.

    One rule per implementation; the tracker applies whichever set it is given,
    so new rules never require editing the tracker (Open/Closed).
    """

    @property
    def name(self) -> str: ...

    def expand(self, context: ExpansionContext) -> Mapping[int, str]:
        """Return ``{pid: reason}`` for processes to adopt into the workload."""
        ...


@runtime_checkable
class MetricCollector(Protocol):
    """Contributes extra metrics to each sample (GPU, I/O, ... ).

    Collectors are additive: the monitor merges whatever they return into the
    sample record under the collector's namespace, so adding one changes no
    existing code path.
    """

    @property
    def namespace(self) -> str: ...

    def collect(self, snapshot: Snapshot) -> Mapping[str, object]: ...

    def close(self) -> None: ...


@runtime_checkable
class Sink(Protocol):
    """Writes records somewhere. Every sink is substitutable for any other."""

    def open(self, header: Record) -> None: ...

    def sample(self, record: Record) -> None: ...

    def close(self, summary: Record) -> None: ...


@runtime_checkable
class Clock(Protocol):
    """Time and sleeping, injected so the sampling loop is testable."""

    def monotonic(self) -> float: ...

    def now_iso(self) -> str: ...

    def sleep_until(self, deadline: float) -> None: ...


class ExpansionContext:
    """Everything an :class:`ExpansionStrategy` may look at.

    Passing a context object (rather than a growing argument list) lets new
    strategies use new inputs without changing the strategy interface.
    """

    __slots__ = (
        "_group_cache",
        "children",
        "groups",
        "new_pids",
        "pinned_group",
        "procs",
        "self_group",
        "self_pid",
        "self_sid",
        "source",
        "tracked_pids",
    )

    def __init__(
        self,
        *,
        procs: Mapping[int, ProcInfo],
        tracked_pids: set[int],
        new_pids: set[int],
        children: Mapping[int, list[int]],
        source: ProcessSource,
        groups: GroupMetricSource | None,
        self_pid: int,
        self_group: str | None,
        self_sid: int,
        pinned_group: str | None,
    ) -> None:
        self.procs = procs
        self.tracked_pids = tracked_pids
        self.new_pids = new_pids
        self.children = children
        self.source = source
        self.groups = groups
        self.self_pid = self_pid
        self.self_group = self_group
        self.self_sid = self_sid
        self.pinned_group = pinned_group
        self._group_cache: dict[int, str | None] = {}

    def group_of(self, pid: int) -> str | None:
        """Group path of ``pid``, memoized for the duration of this sample."""
        if pid not in self._group_cache:
            self._group_cache[pid] = self.source.read_group_path(pid)
        return self._group_cache[pid]
