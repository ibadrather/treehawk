"""Machine-wide CPU and memory, read from /proc."""

from __future__ import annotations

import pathlib

from treehawk.core.models import SystemSample
from treehawk.platforms.linux.constants import LINUX
from treehawk.platforms.linux.procfs import parse_cpu_totals, parse_meminfo


class LinuxSystemSource:
    """Implements ``SystemSource`` against ``/proc/stat`` and ``/proc/meminfo``."""

    def __init__(self, proc_root: str = LINUX.proc_root) -> None:
        self._root = pathlib.Path(proc_root)

    def read(self) -> SystemSample | None:
        stat = self._read("stat")
        cpu = parse_cpu_totals(stat) if stat is not None else None
        if cpu is None:
            return None
        meminfo = self._read("meminfo")
        memory = parse_meminfo(meminfo) if meminfo is not None else {}
        return SystemSample(
            cpu_busy_ticks=cpu[0],
            cpu_total_ticks=cpu[1],
            mem_total_bytes=memory.get("mem_total_bytes"),
            mem_available_bytes=memory.get("mem_available_bytes"),
            swap_total_bytes=memory.get("swap_total_bytes"),
            swap_free_bytes=memory.get("swap_free_bytes"),
        )

    def boot_id(self) -> str | None:
        text = self._read(LINUX.boot_id_path)
        if text is None:
            return None
        return text.strip() or None

    def _read(self, name: str) -> str | None:
        try:
            return (self._root / name).read_text()
        except OSError:
            return None
