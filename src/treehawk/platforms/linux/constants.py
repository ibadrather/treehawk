"""Named values for the Linux backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class LinuxConstants:
    """Where the kernel's files live, and the tuning applied to reading them."""

    proc_root: str = "/proc"
    cgroup_root: str = "/sys/fs/cgroup"

    cmdline_cache_limit: int = 4096
    """Command lines cached before the cache is cleared and refilled."""

    scope_resolve_timeout: float = 3.0
    """Seconds to wait for a launched workload to show up in its systemd scope."""


@dataclass(frozen=True, slots=True)
class StatLayout:
    """Where each field sits in ``/proc/<pid>/stat``, counted after the comm field.

    Field numbers per proc(5) count from 1. Everything after the comm field is
    positional, and comm itself may contain spaces and parentheses - hence the
    rsplit on ')' rather than a naive split.
    """

    state: int = 0  # field 3
    ppid: int = 1  # field 4
    pgrp: int = 2  # field 5
    session: int = 3  # field 6
    utime: int = 11  # field 14
    stime: int = 12  # field 15
    num_threads: int = 17  # field 20
    starttime: int = 19  # field 22
    rss_pages: int = 21  # field 24


LINUX: Final = LinuxConstants()
STAT_LAYOUT: Final = StatLayout()
