"""Machine facts, read once.

Everything here comes from :func:`os.sysconf`, so nothing in this module needs
the C library: ``SC_PHYS_PAGES * SC_PAGE_SIZE`` gives exactly what
``sysctl hw.memsize`` reports, and the page size is the real one - 16 KiB on
Apple Silicon, where x86 reports 4 KiB.
"""

from __future__ import annotations

import os
import socket
from typing import Final

from treehawk.core.models import HostInfo

NANOSECONDS_PER_SECOND: Final = 1_000_000_000
"""The CPU tick treehawk reports on macOS.

``HostInfo.clk_tck`` is only ever the divisor that turns ``cpu_ticks`` into
seconds, and libproc reports task time to nanosecond precision (once converted
from mach units). Reporting nanoseconds keeps ``cpu_ticks / clk_tck`` exactly
equal to seconds, as it is on Linux, and keeps the resolution: the POSIX
``SC_CLK_TCK`` here is 100, which would round every CPU figure to 10 ms.
"""


class DarwinHostInfoSource:
    """Implements ``HostInfoSource`` for macOS."""

    def host_info(self) -> HostInfo:
        return HostInfo(
            platform="darwin",
            hostname=socket.gethostname(),
            ncpu=os.cpu_count() or 1,
            clk_tck=NANOSECONDS_PER_SECOND,
            page_size=os.sysconf("SC_PAGE_SIZE"),
            mem_total_bytes=self._mem_total(),
        )

    @staticmethod
    def _mem_total() -> int | None:
        try:
            return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError):
            return None
