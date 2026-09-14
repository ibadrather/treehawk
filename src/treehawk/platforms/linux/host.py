"""Machine facts, read once."""

from __future__ import annotations

import os
import socket

from treehawk.core.models import HostInfo
from treehawk.platforms.linux.procfs import parse_meminfo_total


class LinuxHostInfoSource:
    """Implements ``HostInfoSource`` for Linux."""

    def __init__(self, proc_root: str = "/proc") -> None:
        self._proc_root = proc_root

    def host_info(self) -> HostInfo:
        return HostInfo(
            platform="linux",
            hostname=socket.gethostname(),
            ncpu=os.cpu_count() or 1,
            clk_tck=int(os.sysconf("SC_CLK_TCK")),
            page_size=os.sysconf("SC_PAGE_SIZE"),
            mem_total_bytes=self._mem_total(),
        )

    def _mem_total(self) -> int | None:
        try:
            with open(os.path.join(self._proc_root, "meminfo")) as handle:
                return parse_meminfo_total(handle.read())
        except OSError:
            return None
