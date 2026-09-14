"""Reading live process state from /proc.

Two-tier by design. :meth:`scan` reads one file per process (``stat``) because
it runs for *every* process on the machine each sample - that is how the parent
map needed for tree expansion is built. The expensive reads (command line, PSS)
happen in :meth:`enrich`, only for processes that actually belong to the
workload.

Every read tolerates failure: processes vanish between the directory listing and
the open (ESRCH), and processes owned by other users refuse some files (EPERM).
Neither is exceptional here, so both yield partial data rather than an error.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Mapping
from typing import Final

from treehawk.core.config import MemoryDetail
from treehawk.core.models import Identity, ProcInfo, ProcSample
from treehawk.platforms.linux.procfs import (
    ProcStatParseError,
    parse_cgroup,
    parse_cmdline,
    parse_smaps_rollup,
    parse_stat,
    parse_status_memory,
)

DEFAULT_PROC_ROOT: Final = "/proc"
_CMDLINE_CACHE_LIMIT: Final = 4096


class LinuxProcessSource:
    """Implements ``ProcessSource`` against /proc."""

    def __init__(
        self,
        root: str = DEFAULT_PROC_ROOT,
        *,
        page_size: int | None = None,
    ) -> None:
        self._root = root
        self._page_size = page_size or os.sysconf("SC_PAGE_SIZE")
        # Command lines are immutable in practice and cost a read each, so they
        # are cached per identity rather than re-read every sample.
        self._cmdline_cache: dict[Identity, str] = {}

    def scan(self) -> Mapping[int, ProcInfo]:
        procs: dict[int, ProcInfo] = {}
        try:
            entries = [entry.name for entry in pathlib.Path(self._root).iterdir()]
        except OSError:
            return procs
        for entry in entries:
            if not entry.isdigit():
                continue
            info = self.read_info(int(entry))
            if info is not None:
                procs[info.pid] = info
        return procs

    def read_info(self, pid: int) -> ProcInfo | None:
        text = self._read_text(pid=pid, name="stat")
        if text is None:
            return None
        try:
            fields = parse_stat(text, page_size=self._page_size)
        except ProcStatParseError:
            return None
        return ProcInfo(**fields)

    def read_cmdline(self, pid: int) -> str:
        raw = self._read_bytes(pid=pid, name="cmdline")
        if raw is None:
            return ""
        return parse_cmdline(raw)

    def read_group_path(self, pid: int) -> str | None:
        text = self._read_text(pid=pid, name="cgroup")
        if text is None:
            return None
        return parse_cgroup(text)

    def enrich(self, *, info: ProcInfo, memory: MemoryDetail = MemoryDetail.PROPORTIONAL) -> ProcSample:
        sample = ProcSample(info=info)
        sample.cmdline = self._cached_cmdline(info)
        sample.cgroup = self.read_group_path(info.pid)

        status = self._read_text(pid=info.pid, name="status")
        if status:
            measured = parse_status_memory(status)
            sample.swap_bytes = measured.get("swap_bytes")

        if memory is MemoryDetail.PROPORTIONAL:
            rollup = self._read_text(pid=info.pid, name="smaps_rollup")
            if rollup:
                measured = parse_smaps_rollup(rollup)
                sample.pss_bytes = measured.get("pss_bytes")
                if sample.swap_bytes is None:
                    sample.swap_bytes = measured.get("swap_bytes")
        return sample

    def forget(self, identity: Identity) -> None:
        self._cmdline_cache.pop(identity, None)

    def _cached_cmdline(self, info: ProcInfo) -> str:
        identity = info.identity
        cached = self._cmdline_cache.get(identity)
        if cached is not None:
            return cached
        cmdline = self.read_cmdline(info.pid) or info.comm
        if len(self._cmdline_cache) >= _CMDLINE_CACHE_LIMIT:
            self._cmdline_cache.clear()
        self._cmdline_cache[identity] = cmdline
        return cmdline

    def _path(self, *, pid: int, name: str) -> pathlib.Path:
        return pathlib.Path(self._root, str(pid), name)

    def _read_text(self, *, pid: int, name: str) -> str | None:
        try:
            return self._path(pid=pid, name=name).read_text(errors="replace")
        except OSError:
            return None

    def _read_bytes(self, *, pid: int, name: str) -> bytes | None:
        try:
            return self._path(pid=pid, name=name).read_bytes()
        except OSError:
            return None
