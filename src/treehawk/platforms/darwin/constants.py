"""Named values for the macOS backend.

Two kinds, kept apart: the tuning treehawk chooses (:class:`DarwinConstants`),
and the numbers and buffer layouts the macOS SDK headers define
(:class:`LibprocConstants`, :class:`TaskAllInfoLayout`). The second kind is the
contract with the kernel, pinned by
``test_the_documented_layouts_match_the_kernel_structs``.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True, slots=True)
class DarwinConstants:
    """Tuning and naming for the macOS backend."""

    nanoseconds_per_second: int = 1_000_000_000
    """The CPU tick treehawk reports on macOS.

    ``HostInfo.clk_tck`` is only ever the divisor that turns ``cpu_ticks`` into
    seconds, and libproc reports task time to nanosecond precision (once
    converted from mach units). Reporting nanoseconds keeps
    ``cpu_ticks / clk_tck`` exactly equal to seconds, as it is on Linux, and
    keeps the resolution: the POSIX ``SC_CLK_TCK`` here is 100, which would
    round every CPU figure to 10 ms.
    """

    membership_ttl: float = 0.25
    """Seconds a coalition membership scan is reused for.

    Unlike a cgroup, whose members are one file read away, a coalition's are
    found only by asking every process which coalition it is in. The group rule
    asks more than once per sample - to decide whether a boundary is ours, then
    to adopt from it - so the scan is held briefly rather than repeated. Shorter
    than any usable sampling interval, so no sample ever sees a membership from
    a previous one.
    """

    cmdline_cache_limit: int = 4096
    """Command lines cached before the cache is cleared and refilled."""

    coalition_prefix: str = "coalition:"
    """Marks a group path as a coalition id, so nothing reads it as a cgroup."""


@dataclass(frozen=True, slots=True)
class LibprocConstants:
    """Call flavors, sysctl names and small buffer layouts from the SDK headers."""

    proc_all_pids: int = 1
    proc_pidtaskallinfo: int = 2
    proc_pidcoalitioninfo: int = 20
    rusage_info_v4: int = 4

    ctl_kern: int = 1
    kern_argmax: int = 8
    kern_procargs2: int = 49

    coalition_info_format: str = "<5Q"
    coalition_resource_index: int = 0
    """``coalition_id[COALITION_TYPE_RESOURCE]`` - the one a fork cannot leave."""

    rusage_info_v4_size: int = 296
    phys_footprint_offset: int = 72
    """``ri_phys_footprint`` sits after a 16-byte uuid and seven uint64s."""

    timebase_info_format: str = "<2I"

    process_states: Mapping[int, str] = field(default_factory=lambda: {1: "I", 2: "R", 3: "S", 4: "T", 5: "Z"})
    """``SIDL``/``SRUN``/``SSLEEP``/``SSTOP``/``SZOMB`` as the single letters the
    core models expect - ``Z`` above all, which ``ProcInfo.is_zombie`` compares
    against."""

    @property
    def coalition_info_size(self) -> int:
        return struct.calcsize(self.coalition_info_format)

    @property
    def timebase_info_size(self) -> int:
        return struct.calcsize(self.timebase_info_format)


@dataclass(frozen=True, slots=True)
class TaskAllInfoLayout:
    """``struct proc_taskallinfo``: its format, and where each field lands.

    proc_bsdinfo (136 bytes) then proc_taskinfo (96), little-endian with no
    interior padding on both arm64 and x86_64::

        12 uint32  flags status xstatus pid ppid uid gid ruid rgid svuid svgid rfu
        16s / 32s  comm, name
         5 uint32  nfiles pgid pjobc tdev tpgid
           int32   nice
         2 uint64  start_tvsec start_tvusec
         6 uint64  virtual resident total_user total_system threads_user threads_system
        12 int32   policy faults pageins cow messages_sent/received syscalls_mach/unix
                   csw threadnum numrunning priority

    The remaining fields are indexes into the unpacked tuple.
    """

    struct_format: str = "<12I16s32s5IiQQ6Q12i"
    status: int = 1
    pid: int = 3
    ppid: int = 4
    comm: int = 12
    pgid: int = 15
    start_tvsec: int = 20
    start_tvusec: int = 21
    resident: int = 23
    total_user: int = 24
    total_system: int = 25
    threadnum: int = 37

    @property
    def size(self) -> int:
        return struct.calcsize(self.struct_format)


DARWIN: Final = DarwinConstants()
LIBPROC: Final = LibprocConstants()
TASK_ALL_INFO: Final = TaskAllInfoLayout()
