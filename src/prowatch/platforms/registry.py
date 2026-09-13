"""Platform selection - the single place that decides which OS code to use.

Supporting macOS or Windows later means writing implementations of the core
interfaces and registering a builder here. No other module changes.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from ..core.models import HostInfo


@dataclass(frozen=True)
class Platform:
    """The bundle of capabilities a platform provides (a tiny DI container)."""

    name: str
    processes: object
    host: object
    groups: object | None = None
    launcher: object | None = None
    notes: list[str] = field(default_factory=list)

    def host_info(self) -> HostInfo:
        return self.host.host_info()


class UnsupportedPlatform(RuntimeError):
    """prowatch has no implementation for this operating system yet."""


def build_linux(**kwargs) -> Platform:
    from .linux import CgroupV2Source, LinuxHostInfoSource, LinuxProcessSource
    from .linux.launcher import default_launcher

    proc_root = kwargs.get("proc_root", "/proc")
    cgroup_root = kwargs.get("cgroup_root", "/sys/fs/cgroup")

    host = LinuxHostInfoSource(proc_root)
    info = host.host_info()
    cgroups = CgroupV2Source(cgroup_root)
    notes: list[str] = []
    if not cgroups.available():
        notes.append(
            "cgroup v2 is not mounted: group-level CPU/memory totals are "
            "unavailable and detached children are tracked heuristically"
        )
        cgroups = None

    return Platform(
        name="linux",
        processes=LinuxProcessSource(proc_root, page_size=info.page_size),
        host=host,
        groups=cgroups,
        launcher=default_launcher(cgroups),
        notes=notes,
    )


PLATFORM_BUILDERS = {"linux": build_linux}


def get_platform(name: str | None = None, **kwargs) -> Platform:
    """Build the platform bundle for ``name`` (default: the current OS)."""
    name = name or sys.platform
    key = "linux" if name.startswith("linux") else name
    try:
        builder = PLATFORM_BUILDERS[key]
    except KeyError:
        raise UnsupportedPlatform(
            f"prowatch has no backend for {name!r} yet; "
            f"supported: {', '.join(sorted(PLATFORM_BUILDERS))}"
        ) from None
    return builder(**kwargs)
