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

from treehawk.core.config import WorkloadOutput
from treehawk.core.errors import LaunchFailed
from treehawk.core.models import LaunchedWorkload
from treehawk.platforms.linux.cgroup2 import CgroupV2Source
from treehawk.platforms.linux.constants import LINUX
from treehawk.platforms.linux.procfs import parse_cgroup
from treehawk.platforms.posix import (
    DirectLauncher,
    FallbackLauncher,
    SubprocessWorkload,
    start_process,
)


class ScopeUnavailable(LaunchFailed):
    """systemd-run is present but could not give us an accounting boundary."""


class ScopeLauncher:
    """Start the command inside a transient systemd scope (its own cgroup)."""

    @property
    def name(self) -> str:
        return "scope"

    def __init__(
        self,
        cgroups: CgroupV2Source,
        *,
        proc_root: str,
        timeout: float = LINUX.scope_resolve_timeout,
    ) -> None:
        self._cgroups = cgroups
        self._proc_root = proc_root
        self._timeout = timeout

    def available(self) -> bool:
        return shutil.which("systemd-run") is not None and self._cgroups.available()

    def launch(self, argv: list[str], *, output: WorkloadOutput = WorkloadOutput.INHERIT) -> LaunchedWorkload:
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
        # systemd-run --scope execs in place, so the pty it is handed is the
        # workload's pty; nothing about the boundary changes.
        process, stream = start_process(wrapper, output=output)
        group = self._resolve_group(process=process, unit=unit)
        if group is None:
            if stream is not None:
                stream.close()  # the fallback launcher will make a pty of its own
            raise ScopeUnavailable(f"could not place the workload in a cgroup (unit {unit})")
        return SubprocessWorkload(process=process, argv=argv, group_path=group, output_stream=stream)

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


def default_launcher(cgroups: CgroupV2Source | None, *, proc_root: str) -> FallbackLauncher:
    """The launcher used by the CLI: isolated if possible, direct otherwise.

    Both readers are handed in rather than defaulted, so a platform built over a
    fake ``/proc`` and cgroup mount never probes the real ones. With no cgroup2
    mount there is no boundary to ask for, so the scope launcher is left out.
    """
    if cgroups is None:
        return FallbackLauncher([DirectLauncher()])
    return FallbackLauncher([ScopeLauncher(cgroups, proc_root=proc_root), DirectLauncher()])


def _read_cgroup(*, proc_root: str, pid: int) -> str | None:
    try:
        text = (pathlib.Path(proc_root) / str(pid) / "cgroup").read_text()
    except OSError:
        return None
    return parse_cgroup(text)
