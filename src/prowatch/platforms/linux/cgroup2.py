"""cgroup v2 reader: exact, kernel-side accounting for a process group.

Why this matters: ``cpu.stat`` and ``memory.current`` are maintained by the
kernel for every process in the group, including ones that were born and died
between two of our samples. Polling /proc can never see those; a cgroup can.
"""

from __future__ import annotations

import os
from typing import Final

from prowatch.core.models import GroupMetrics

DEFAULT_CGROUP_ROOT: Final = "/sys/fs/cgroup"


class CgroupV2Source:
    """Implements ``GroupMetricSource`` against a cgroup2 filesystem."""

    def __init__(self, root: str = DEFAULT_CGROUP_ROOT) -> None:
        self._root = root

    @property
    def root(self) -> str:
        return self._root

    def available(self) -> bool:
        return os.path.isfile(os.path.join(self._root, "cgroup.controllers"))

    def _abs(self, path: str) -> str:
        return os.path.join(self._root, path.lstrip("/"))

    def pids_in(self, path: str) -> set[int] | None:
        """Every PID in ``path`` and its descendant groups.

        Descendants matter: systemd and container runtimes nest groups, and a
        child moved into a sub-group is still part of the workload.
        """
        base = self._abs(path)
        if not os.path.isdir(base):
            return None
        pids: set[int] = set()
        found = False
        for dirpath, _dirnames, filenames in os.walk(base):
            if "cgroup.procs" not in filenames:
                continue
            text = _read(os.path.join(dirpath, "cgroup.procs"))
            if text is None:
                continue
            found = True
            for line in text.split():
                try:
                    pids.add(int(line))
                except ValueError:
                    continue
        return pids if found else None

    def metrics(self, path: str) -> GroupMetrics | None:
        base = self._abs(path)
        if not os.path.isdir(base):
            return None
        return GroupMetrics(
            path=path,
            cpu_usec=self._cpu_usec(base),
            memory_bytes=_read_int(os.path.join(base, "memory.current")),
            memory_peak_bytes=_read_int(os.path.join(base, "memory.peak")),
        )

    def _cpu_usec(self, base: str) -> int | None:
        text = _read(os.path.join(base, "cpu.stat"))
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


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None  # vanished, or not delegated to us


def _read_int(path: str) -> int | None:
    text = _read(path)
    if text is None:
        return None
    text = text.strip()
    if not text or text == "max":
        return None
    try:
        return int(text)
    except ValueError:
        return None
