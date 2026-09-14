"""Starting a workload under treehawk, on Linux.

Launching, rather than attaching, removes the two weaknesses of polling:

* nothing is missed before the first sample, and
* the workload gets its own cgroup, so *every* descendant - including one that
  double-forks and is re-parented to PID 1 - is accounted for by the kernel
  rather than inferred by us.

Only the isolating launcher is Linux-specific and lives here; the plain
session-based one is shared from :mod:`treehawk.platforms.posix`. The composite
prefers isolation and degrades cleanly when systemd is not usable (no user bus,
a container, a non-systemd distribution).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import time

from treehawk.core.errors import LaunchFailed
from treehawk.core.models import LaunchedWorkload
from treehawk.platforms.linux.cgroup2 import CgroupV2Source
from treehawk.platforms.linux.constants import LINUX
from treehawk.platforms.linux.procfs import parse_cgroup
from treehawk.platforms.posix import DirectLauncher, FallbackLauncher, SubprocessWorkload


class ScopeUnavailable(LaunchFailed):
    """systemd-run is present but could not give us an accounting boundary."""


class ScopeLauncher:
    """Start the command inside a transient systemd scope (its own cgroup)."""

    @property
    def name(self) -> str:
        return "scope"

    def __init__(
        self,
        cgroups: CgroupV2Source | None = None,
        *,
        proc_root: str = LINUX.proc_root,
        timeout: float = LINUX.scope_resolve_timeout,
    ) -> None:
        self._cgroups = cgroups or CgroupV2Source()
        self._proc_root = proc_root
        self._timeout = timeout

    def available(self) -> bool:
        return shutil.which("systemd-run") is not None and self._cgroups.available()

    def launch(self, argv: list[str]) -> LaunchedWorkload:
        unit = f"treehawk-{os.getpid()}-{int(time.time())}"
        wrapper = [
            "systemd-run",
            "--user",
            "--scope",
            "--collect",
            "--quiet",
            f"--unit={unit}",
            "--",
            *argv,
        ]
        process = subprocess.Popen(wrapper, start_new_session=True)
        group = self._resolve_group(process=process, unit=unit)
        if group is None:
            raise ScopeUnavailable(f"could not place the workload in a cgroup (unit {unit})")
        return SubprocessWorkload(process=process, argv=argv, group_path=group)

    def _resolve_group(self, *, process: subprocess.Popen[bytes], unit: str) -> str | None:
        """Wait for the child to appear inside the new scope's cgroup."""
        needle = f"{unit}.scope"
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            path = _read_cgroup(proc_root=self._proc_root, pid=process.pid)
            if path and needle in path:
                return path
            if process.poll() is not None and process.returncode != 0:
                return None  # systemd-run itself failed; nothing to salvage
            time.sleep(0.02)
        return None


def default_launcher(cgroups: CgroupV2Source | None = None) -> FallbackLauncher:
    """The launcher used by the CLI: isolated if possible, direct otherwise."""
    return FallbackLauncher([ScopeLauncher(cgroups), DirectLauncher()])


def _read_cgroup(*, proc_root: str, pid: int) -> str | None:
    try:
        text = (pathlib.Path(proc_root) / str(pid) / "cgroup").read_text()
    except OSError:
        return None
    return parse_cgroup(text)
