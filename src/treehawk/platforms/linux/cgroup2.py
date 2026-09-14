"""cgroup v2 reader: exact, kernel-side accounting for a process group.

Why this matters: ``cpu.stat`` and ``memory.current`` are maintained by the
kernel for every process in the group, including ones that were born and died
between two of our samples. Polling /proc can never see those; a cgroup can.
"""

from __future__ import annotations

import os
import pathlib

from treehawk.core.models import GroupMetrics
from treehawk.platforms.linux.constants import LINUX


class CgroupV2Source:
    """Implements ``GroupMetricSource`` against a cgroup2 filesystem."""

    def __init__(self, root: str = LINUX.cgroup_root) -> None:
        self._root = root

    def available(self) -> bool:
        return (pathlib.Path(self._root) / "cgroup.controllers").is_file()

    def _absolute(self, path: str) -> pathlib.Path:
        return pathlib.Path(self._root) / path.lstrip("/")

    def pids_in(self, path: str) -> set[int] | None:
        """Every PID in ``path`` and its descendant groups.

        Descendants matter: systemd and container runtimes nest groups, and a
        child moved into a sub-group is still part of the workload.
        """
        base = self._absolute(path)
        if not base.is_dir():
            return None
        pids: set[int] = set()
        found = False
        for dirpath, _dirnames, filenames in os.walk(base):
            if "cgroup.procs" not in filenames:
                continue
            text = _read_file(pathlib.Path(dirpath) / "cgroup.procs")
            if text is None:
                continue
            found = True
            pids.update(_parse_pids(text))
        return pids if found else None

    def metrics(self, path: str) -> GroupMetrics | None:
        base = self._absolute(path)
        if not base.is_dir():
            return None
        return GroupMetrics(
            path=path,
            cpu_usec=self._cpu_usec(base),
            memory_bytes=_read_int_file(base / "memory.current"),
            memory_peak_bytes=_read_int_file(base / "memory.peak"),
        )

    def _cpu_usec(self, base: pathlib.Path) -> int | None:
        text = _read_file(base / "cpu.stat")
        if text is None:
            return None
        for line in text.splitlines():
            key, _, value = line.partition(" ")
            if key == "usage_usec":
                try:
                    return int(value)
                except ValueError:
                    return None
        return None


def _parse_pids(text: str) -> set[int]:
    """The PIDs listed in a ``cgroup.procs`` file, skipping anything else."""
    return {int(token) for token in text.split() if token.isdecimal()}


def _read_file(path: pathlib.Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None  # vanished, or not delegated to us


def _read_int_file(path: pathlib.Path) -> int | None:
    text = _read_file(path)
    if text is None:
        return None
    text = text.strip()
    if not text or text == "max":
        return None
    try:
        return int(text)
    except ValueError:
        return None
