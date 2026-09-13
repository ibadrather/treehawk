"""Starting a workload under prowatch.

Launching, rather than attaching, removes the two weaknesses of polling:

* nothing is missed before the first sample, and
* the workload gets its own cgroup, so *every* descendant - including one that
  double-forks and is re-parented to PID 1 - is accounted for by the kernel
  rather than inferred by us.

Two interchangeable implementations of ``ProcessLauncher`` are provided, plus a
composite that prefers the isolating one and degrades cleanly when systemd is
not usable (no user bus, a container, a non-systemd distribution).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from typing import Final

from ...core.interfaces import ProcessLauncher
from ...core.models import LaunchedWorkload
from .cgroup2 import CgroupV2Source
from .procfs import parse_cgroup

_RESOLVE_TIMEOUT: Final = 3.0


class SubprocessWorkload(LaunchedWorkload):
    """A workload started with :mod:`subprocess`, signalled by process group.

    ``start_new_session=True`` puts it in its own group, so one ``killpg``
    reaches the whole workload rather than just the process we happen to hold.
    """

    def __init__(
        self,
        process: "subprocess.Popen[bytes]",
        argv: list[str],
        *,
        group_path: str | None = None,
        isolated: bool = False,
    ) -> None:
        super().__init__(
            pid=process.pid, argv=list(argv), group_path=group_path, isolated=isolated
        )
        self._process = process

    def poll(self) -> int | None:
        return self._process.poll()

    def signal(self, signum: int = signal.SIGTERM) -> None:
        try:
            os.killpg(os.getpgid(self._process.pid), signum)
        except (ProcessLookupError, PermissionError):
            pass  # already gone, or no longer ours to signal


class DirectLauncher:
    """Start the command in its own session, tracked by /proc alone.

    ``start_new_session=True`` gives the workload its own session and process
    group, which both keeps terminal signals from reaching it behind our back
    and gives the session expansion strategy something stable to follow.
    """

    @property
    def name(self) -> str:
        return "direct"

    def available(self) -> bool:
        return True

    def launch(self, argv: list[str]) -> LaunchedWorkload:
        process = subprocess.Popen(argv, start_new_session=True)
        return SubprocessWorkload(process, argv)


class ScopeLauncher:
    """Start the command inside a transient systemd scope (its own cgroup)."""

    @property
    def name(self) -> str:
        return "scope"

    def __init__(
        self,
        cgroups: CgroupV2Source | None = None,
        *,
        proc_root: str = "/proc",
        timeout: float = _RESOLVE_TIMEOUT,
    ) -> None:
        self._cgroups = cgroups or CgroupV2Source()
        self._proc_root = proc_root
        self._timeout = timeout

    def available(self) -> bool:
        return shutil.which("systemd-run") is not None and self._cgroups.available()

    def launch(self, argv: list[str]) -> LaunchedWorkload:
        unit = f"prowatch-{os.getpid()}-{int(time.time())}"
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
        group = self._resolve_group(process, unit)
        if group is None:
            raise ScopeUnavailable(
                f"could not place the workload in a cgroup (unit {unit})"
            )
        return SubprocessWorkload(process, argv, group_path=group, isolated=True)

    def _resolve_group(
        self, process: "subprocess.Popen[bytes]", unit: str
    ) -> str | None:
        """Wait for the child to appear inside the new scope's cgroup."""
        needle = f"{unit}.scope"
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            path = _read_cgroup(self._proc_root, process.pid)
            if path and needle in path:
                return path
            if process.poll() is not None and process.returncode != 0:
                return None  # systemd-run itself failed; nothing to salvage
            time.sleep(0.02)
        return None


class ScopeUnavailable(RuntimeError):
    """systemd-run is present but could not give us an accounting boundary."""


class FallbackLauncher:
    """Try each launcher in order; the first that works wins.

    Composite over ``ProcessLauncher``, so callers still see a single launcher.
    """

    @property
    def name(self) -> str:
        return "fallback"

    def available(self) -> bool:
        return any(launcher.available() for launcher in self._launchers)

    def __init__(self, launchers: list[ProcessLauncher]) -> None:
        self._launchers = launchers
        self.notes: list[str] = []
        self.used: str | None = None

    def launch(self, argv: list[str]) -> LaunchedWorkload:
        errors: list[str] = []
        for launcher in self._launchers:
            if not launcher.available():
                errors.append(f"{launcher.name}: unavailable")
                continue
            try:
                workload = launcher.launch(argv)
            except (OSError, ScopeUnavailable) as exc:
                errors.append(f"{launcher.name}: {exc}")
                continue
            self.used = launcher.name
            if not workload.isolated:
                self.notes.append(
                    "no cgroup boundary: membership is inferred from /proc, so a "
                    "process that both detaches and changes cgroup could be missed"
                )
            self.notes.extend(errors)
            return workload
        raise RuntimeError("could not start the workload: " + "; ".join(errors))


def default_launcher(cgroups: CgroupV2Source | None = None) -> FallbackLauncher:
    """The launcher used by the CLI: isolated if possible, direct otherwise."""
    return FallbackLauncher([ScopeLauncher(cgroups), DirectLauncher()])


def _read_cgroup(proc_root: str, pid: int) -> str | None:
    try:
        with open(os.path.join(proc_root, str(pid), "cgroup")) as handle:
            return parse_cgroup(handle.read())
    except OSError:
        return None
