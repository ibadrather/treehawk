"""Reading live process state on macOS.

Two-tier, for the same reason the Linux reader is. :meth:`scan` runs for
*every* process on the machine each sample - that is how the parent map tree
expansion needs gets built - so it makes one ``proc_pidinfo`` call per process
and nothing more. The costly reads (the command line, which needs a separate
``sysctl``, and the memory footprint, which needs ``proc_pid_rusage``) happen
in :meth:`enrich`, only for processes that actually belong to the workload.

Every read tolerates failure: processes vanish between the listing and the read
(ESRCH), and processes owned by another user refuse ``KERN_PROCARGS2`` and
``proc_pid_rusage`` (EPERM). Neither is exceptional, so both yield partial data
rather than an error - treehawk records a null rather than a number it could
not read.
"""

from __future__ import annotations

from collections.abc import Mapping

from treehawk.core.config import MemoryDetail
from treehawk.core.models import Identity, ProcInfo, ProcSample
from treehawk.platforms.darwin.constants import DARWIN
from treehawk.platforms.darwin.libproc import (
    LibProc,
    ProcessTable,
    coalition_path,
    nanoseconds_from_mach,
)
from treehawk.platforms.darwin.models import Timebase


class DarwinProcessSource:
    """Implements ``ProcessSource`` against libproc."""

    def __init__(self, table: ProcessTable | None = None) -> None:
        self._table: ProcessTable = table or LibProc()
        # Read once: the timebase is a property of the machine, and every CPU
        # figure on macOS is scaled by it.
        self._timebase: Timebase = self._table.timebase()
        # Command lines are immutable in practice and cost a sysctl each, so
        # they are cached per identity rather than re-read every sample.
        self._cmdline_cache: dict[Identity, str] = {}

    def scan(self) -> Mapping[int, ProcInfo]:
        procs: dict[int, ProcInfo] = {}
        for pid in self._table.list_pids():
            info = self.read_info(pid)
            if info is not None:
                procs[info.pid] = info
        return procs

    def read_info(self, pid: int) -> ProcInfo | None:
        task = self._table.task_info(pid)
        if task is None:
            return None
        return ProcInfo(
            pid=task.pid,
            ppid=task.ppid,
            pgid=task.pgid,
            sid=self._table.session_id(pid),
            starttime=task.starttime_usec,
            state=task.state,
            threads=task.threads,
            comm=task.comm,
            # Nanoseconds, matching the clk_tck this platform reports, so that
            # cpu_ticks / clk_tck is seconds here exactly as it is on Linux.
            cpu_ticks=nanoseconds_from_mach(task.cpu_mach_units, self._timebase),
            rss_bytes=task.rss_bytes,
        )

    def read_cmdline(self, pid: int) -> str:
        return self._table.argv(pid)

    def read_group_path(self, pid: int) -> str | None:
        identifier = self._table.coalition_id(pid)
        return None if identifier is None else coalition_path(identifier)

    def enrich(self, *, info: ProcInfo, memory: MemoryDetail = MemoryDetail.PROPORTIONAL) -> ProcSample:
        sample = ProcSample(info=info)
        sample.cmdline = self._cached_cmdline(info)
        sample.cgroup = self.read_group_path(info.pid)
        if memory is MemoryDetail.PROPORTIONAL:
            sample.pss_bytes = self._table.footprint(info.pid)
        # swap_bytes stays None: macOS compresses memory rather than swapping
        # it per process, and exposes no per-process figure for either.
        return sample

    def forget(self, identity: Identity) -> None:
        self._cmdline_cache.pop(identity, None)

    def _cached_cmdline(self, info: ProcInfo) -> str:
        identity = info.identity
        cached = self._cmdline_cache.get(identity)
        if cached is not None:
            return cached
        cmdline = self._table.argv(info.pid) or info.comm
        if len(self._cmdline_cache) >= DARWIN.cmdline_cache_limit:
            self._cmdline_cache.clear()
        self._cmdline_cache[identity] = cmdline
        return cmdline
