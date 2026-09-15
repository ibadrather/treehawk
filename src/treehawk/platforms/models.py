"""What a platform backend provides, bundled."""

from __future__ import annotations

from dataclasses import dataclass, field

from treehawk.core.interfaces import (
    GroupMetricSource,
    HostInfoSource,
    ProcessLauncher,
    ProcessSource,
)
from treehawk.core.models import HostInfo
from treehawk.core.records import PSS


@dataclass(frozen=True)
class Platform:
    """The bundle of capabilities a platform provides (a tiny DI container)."""

    name: str
    process_source: ProcessSource
    host_source: HostInfoSource
    group_source: GroupMetricSource | None = None
    launcher: ProcessLauncher | None = None
    """Starts a workload inside an accounting boundary, where the platform can make one."""
    direct_launcher: ProcessLauncher | None = None
    """Starts a workload without asking for a boundary - what ``run --no-isolate`` uses."""
    memory_kind: str = PSS.key
    """Which fair-memory measure this platform's ``ProcessSource`` reports.

    Every platform fills ``ProcSample.pss_bytes``, but not with the same
    measure, so the name travels with the value into the log header.
    """
    notes: list[str] = field(default_factory=list)

    def host_info(self) -> HostInfo:
        return self.host_source.host_info()
