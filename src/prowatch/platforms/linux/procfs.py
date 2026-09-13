"""Parsers for the /proc files prowatch reads.

Pure functions over text: they never touch the filesystem, so the whole Linux
metric path can be unit-tested against captured fixtures.
"""

from __future__ import annotations

from typing import TypedDict

# Field numbers per proc(5), counted from 1. Everything after the comm field is
# positional, and comm itself may contain spaces and parentheses - hence the
# rsplit on ')' rather than a naive split.
_STATE = 0  # field 3
_PPID = 1  # field 4
_PGRP = 2  # field 5
_SESSION = 3  # field 6
_UTIME = 11  # field 14
_STIME = 12  # field 15
_NUM_THREADS = 17  # field 20
_STARTTIME = 19  # field 22
_RSS_PAGES = 21  # field 24


class StatFields(TypedDict):
    """Exactly the fields :func:`parse_stat` extracts, mirroring ``ProcInfo``."""

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


class ProcStatParseError(ValueError):
    """The stat line was malformed or truncated (the process died mid-read)."""


def parse_stat(text: str, *, page_size: int = 4096) -> StatFields:
    """Parse ``/proc/<pid>/stat`` into the fields prowatch uses."""
    try:
        head, _, rest = text.partition(" (")
        comm, _, tail = rest.rpartition(") ")
        fields = tail.split()
        return {
            "pid": int(head),
            "comm": comm,
            "state": fields[_STATE],
            "ppid": int(fields[_PPID]),
            "pgid": int(fields[_PGRP]),
            "sid": int(fields[_SESSION]),
            "cpu_ticks": int(fields[_UTIME]) + int(fields[_STIME]),
            "threads": int(fields[_NUM_THREADS]),
            "starttime": int(fields[_STARTTIME]),
            "rss_bytes": int(fields[_RSS_PAGES]) * page_size,
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
            out["rss_bytes"] = _kb(line)
        elif line.startswith("VmSwap:"):
            out["swap_bytes"] = _kb(line)
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
            out["pss_bytes"] = _kb(line)
        elif line.startswith("Swap:"):
            out["swap_bytes"] = _kb(line)
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
            return _kb(line)
    return None


def _kb(line: str) -> int:
    return int(line.split()[1]) * 1024
