"""Platform selection - the single place that decides which OS code to use.

Supporting another OS means writing implementations of the core interfaces in
``platforms/<os>/`` and registering a builder here. No other module changes -
Linux and macOS are both wired up this way, and each imports its own package
inside its builder, so nothing platform-specific is imported until it is asked
for.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from treehawk.core.errors import UnsupportedPlatform
from treehawk.core.records import PHYS_FOOTPRINT
from treehawk.platforms.models import Platform

__all__ = ["UnsupportedPlatform", "build_darwin", "build_linux", "get_platform"]


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


def build_darwin() -> Platform:
    """macOS, on libproc and coalitions.

    The shape differs from Linux in two ways worth stating in the log rather
    than hiding: there is no cgroup, so no kernel-side totals and no isolating
    launcher; and the fair-memory measure is the kernel's phys_footprint
    instead of PSS, which is why ``memory_kind`` travels with it.
    """
    from treehawk.platforms.darwin import (
        CoalitionSource,
        DarwinHostInfoSource,
        DarwinProcessSource,
        LibProc,
    )
    from treehawk.platforms.posix import DirectLauncher, FallbackLauncher

    table = LibProc()
    coalitions = CoalitionSource(table)
    notes = [
        (
            "macOS has no cgroup equivalent: group-level CPU and memory totals "
            "are unavailable, and membership is inferred from coalitions, "
            "sessions and the process tree"
        )
    ]
    if not coalitions.available():
        notes.append(
            "coalitions are unreadable on this machine: a process that detaches between two samples may be missed"
        )

    return Platform(
        name="darwin",
        process_source=DarwinProcessSource(table),
        host_source=DarwinHostInfoSource(),
        group_source=coalitions,
        # Placing a workload in a *new* coalition needs entitlements treehawk
        # does not have, so `run` gives it its own session and nothing more.
        launcher=FallbackLauncher([DirectLauncher()]),
        memory_kind=PHYS_FOOTPRINT.key,
        notes=notes,
    )


PlatformBuilder = Callable[[], Platform]

PLATFORM_BUILDERS: dict[str, PlatformBuilder] = {"darwin": build_darwin, "linux": build_linux}


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
