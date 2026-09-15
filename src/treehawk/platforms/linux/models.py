"""What treehawk reads out of /proc, as plain values."""

from __future__ import annotations

from typing import TypedDict


class StatFields(TypedDict):
    """Exactly the fields ``parse_stat`` extracts, mirroring ``ProcInfo``."""

    pid: int
    comm: str
    state: str
    ppid: int
    pgid: int
    sid: int
    cpu_ticks: int
    threads: int
    starttime: int
    rss_bytes: int
