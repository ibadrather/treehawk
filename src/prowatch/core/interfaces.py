"""The abstractions every other layer depends on.

Kept deliberately narrow (Interface Segregation): a component asks for the one
capability it uses, not for a god-object "backend". Concrete Linux/macOS/GPU
implementations satisfy these structurally - no inheritance required.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, TypeAlias, runtime_checkable

from .models import (
    GroupMetrics,
    HostInfo,
    Identity,
    LaunchedWorkload,
    ProcInfo,
    ProcSample,
    Snapshot,
)

Record: TypeAlias = dict[str, Any]
"""One serialised log record. Deliberately loose: sinks must stay indifferent to
which fields a schema version happens to carry."""


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
        """Return the process' group boundary path (cgroup), if any."""
        ...

    def enrich(self, info: ProcInfo, *, want_pss: bool) -> ProcSample:
        """Add the costlier fields (command line, PSS, swap, group) to ``info``."""
        ...

    def forget(self, identity: Identity) -> None:
        """Drop any cached state for a process that has exited."""
        ...


@runtime_checkable
class GroupMetricSource(Protocol):
    """Reads kernel-side aggregates for a process group boundary (cgroup)."""

    def exists(self, path: str) -> bool: ...

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

    def matches(self, info: ProcInfo, cmdline: str) -> bool: ...

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

    def expand(self, ctx: ExpansionContext) -> Mapping[int, str]:
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
        "procs",
        "tracked_pids",
        "new_pids",
        "children",
        "groups",
        "source",
        "self_pid",
        "self_group",
        "self_sid",
        "pinned_group",
        "_group_cache",
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
