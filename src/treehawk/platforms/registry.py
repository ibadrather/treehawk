"""Platform selection - the single place that decides which OS code to use.

Supporting macOS or Windows later means writing implementations of the core
interfaces and registering a builder here. No other module changes.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from treehawk.core.errors import UnsupportedPlatform
from treehawk.core.interfaces import (
    GroupMetricSource,
    HostInfoSource,
    ProcessLauncher,
    ProcessSource,
)
from treehawk.core.models import HostInfo
from treehawk.core.records import PSS

__all__ = ["Platform", "UnsupportedPlatform", "build_linux", "get_platform"]


@dataclass(frozen=True)
class Platform:
    """The bundle of capabilities a platform provides (a tiny DI container)."""

    name: str
    process_source: ProcessSource
    host_source: HostInfoSource
    group_source: GroupMetricSource | None = None
    launcher: ProcessLauncher | None = None
    memory_kind: str = PSS.key
    """Which fair-memory measure this platform's ``ProcessSource`` reports.

    Every platform fills ``ProcSample.pss_bytes``, but not with the same
    measure, so the name travels with the value into the log header.
    """
    notes: list[str] = field(default_factory=list)

    def host_info(self) -> HostInfo:
        return self.host_source.host_info()


def build_linux(*, proc_root: str = "/proc", cgroup_root: str = "/sys/fs/cgroup") -> Platform:
    from treehawk.platforms.linux import (
        CgroupV2Source,
        LinuxHostInfoSource,
        LinuxProcessSource,
    )
    from treehawk.platforms.linux.launcher import default_launcher

    host_source = LinuxHostInfoSource(proc_root)
    host = host_source.host_info()

    mounted = CgroupV2Source(cgroup_root)
    cgroups: CgroupV2Source | None = mounted if mounted.available() else None
    notes: list[str] = []
    if cgroups is None:
        notes.append(
            "cgroup v2 is not mounted: group-level CPU/memory totals are "
            "unavailable and detached children are tracked heuristically"
        )

    return Platform(
        name="linux",
        process_source=LinuxProcessSource(proc_root, page_size=host.page_size),
        host_source=host_source,
        group_source=cgroups,
        launcher=default_launcher(cgroups),
        notes=notes,
    )


PlatformBuilder = Callable[[], Platform]

PLATFORM_BUILDERS: dict[str, PlatformBuilder] = {"linux": build_linux}


def get_platform(*, name: str | None = None) -> Platform:
    """Build the platform bundle for ``name`` (default: the current OS)."""
    name = name or sys.platform
    key = "linux" if name.startswith("linux") else name
    try:
        builder = PLATFORM_BUILDERS[key]
    except KeyError:
        raise UnsupportedPlatform(
            f"treehawk has no backend for {name!r} yet; supported: {', '.join(sorted(PLATFORM_BUILDERS))}"
        ) from None
    return builder()
