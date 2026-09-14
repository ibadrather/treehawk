"""Machine facts, read once.

Everything here comes from :func:`os.sysconf`, so nothing in this module needs
the C library: ``SC_PHYS_PAGES * SC_PAGE_SIZE`` gives exactly what
``sysctl hw.memsize`` reports, and the page size is the real one - 16 KiB on
Apple Silicon, where x86 reports 4 KiB.
"""

from __future__ import annotations

import os
import socket

from treehawk.core.models import HostInfo
from treehawk.platforms.darwin.constants import DARWIN


class DarwinHostInfoSource:
    """Implements ``HostInfoSource`` for macOS."""

    def host_info(self) -> HostInfo:
        return HostInfo(
            platform="darwin",
            hostname=socket.gethostname(),
            ncpu=os.cpu_count() or 1,
            clk_tck=DARWIN.nanoseconds_per_second,
            page_size=os.sysconf("SC_PAGE_SIZE"),
            mem_total_bytes=self._mem_total(),
        )

    @staticmethod
    def _mem_total() -> int | None:
        try:
            return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError):
            return None
