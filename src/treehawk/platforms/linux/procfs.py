"""Parsers for the /proc files treehawk reads.

Pure functions over text: they never touch the filesystem, so the whole Linux
metric path can be unit-tested against captured fixtures.
"""

from __future__ import annotations

from treehawk.platforms.linux.constants import STAT_LAYOUT
from treehawk.platforms.linux.models import StatFields


class ProcStatParseError(ValueError):
    """The stat line was malformed or truncated (the process died mid-read)."""


def parse_stat(text: str, *, page_size: int = 4096) -> StatFields:
    """Parse ``/proc/<pid>/stat`` into the fields treehawk uses."""
    try:
        head, _, rest = text.partition(" (")
        comm, _, tail = rest.rpartition(") ")
        fields = tail.split()
        return {
            "pid": int(head),
            "comm": comm,
            "state": fields[STAT_LAYOUT.state],
            "ppid": int(fields[STAT_LAYOUT.ppid]),
            "pgid": int(fields[STAT_LAYOUT.pgrp]),
            "sid": int(fields[STAT_LAYOUT.session]),
            "cpu_ticks": int(fields[STAT_LAYOUT.utime]) + int(fields[STAT_LAYOUT.stime]),
            "threads": int(fields[STAT_LAYOUT.num_threads]),
            "starttime": int(fields[STAT_LAYOUT.starttime]),
            "rss_bytes": int(fields[STAT_LAYOUT.rss_pages]) * page_size,
        }
    except (IndexError, ValueError) as exc:
        raise ProcStatParseError(f"malformed stat line: {text[:80]!r}") from exc


def parse_cmdline(raw: bytes) -> str:
    """Turn the NUL-separated argv into a displayable command line."""
    return raw.replace(b"\x00", b" ").strip().decode("utf-8", "replace")


def parse_status_memory(text: str) -> dict[str, int]:
    """Pull VmRSS / VmSwap (kB) out of ``/proc/<pid>/status``."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            out["rss_bytes"] = _bytes_from_kb(line)
        elif line.startswith("VmSwap:"):
            out["swap_bytes"] = _bytes_from_kb(line)
        elif out.get("rss_bytes") is not None and out.get("swap_bytes") is not None:
            break
    return out


def parse_smaps_rollup(text: str) -> dict[str, int]:
    """Pull Pss / Swap out of ``/proc/<pid>/smaps_rollup``.

    PSS divides each shared page by the number of processes mapping it, so
    summing PSS across a fork tree gives the memory the workload actually costs
    the machine - summing RSS counts shared pages once per child.
    """
    out: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("Pss:"):
            out["pss_bytes"] = _bytes_from_kb(line)
        elif line.startswith("Swap:"):
            out["swap_bytes"] = _bytes_from_kb(line)
    return out


def parse_cgroup(text: str) -> str | None:
    """Return the cgroup v2 path from ``/proc/<pid>/cgroup`` (the ``0::`` line)."""
    for line in text.splitlines():
        hierarchy, _, path = line.partition("::")
        if hierarchy == "0" and path:
            return path.strip()
    return None


def parse_meminfo_total(text: str) -> int | None:
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            return _bytes_from_kb(line)
    return None


def _bytes_from_kb(line: str) -> int:
    """``VmRSS:\t2048 kB`` -> bytes."""
    return int(line.split()[1]) * 1024
