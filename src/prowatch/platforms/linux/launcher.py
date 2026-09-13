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

from ...core.models import LaunchedWorkload
from .cgroup2 import CgroupV2Source

_RESOLVE_TIMEOUT = 3.0


class DirectLauncher:
    """Start the command in its own session, tracked by /proc alone.

    ``start_new_session=True`` gives the workload its own session and process
    group, which both keeps terminal signals from reaching it behind our back
    and gives the session expansion strategy something stable to follow.
    """

    name = "direct"

    def available(self) -> bool:
        return True

    def launch(self, argv: list[str]) -> LaunchedWorkload:
        proc = subprocess.Popen(argv, start_new_session=True)
        return _handle(proc, argv, group_path=None, isolated=False)


class ScopeLauncher:
    """Start the command inside a transient systemd scope (its own cgroup)."""

    name = "scope"

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
        proc = subprocess.Popen(wrapper, start_new_session=True)
        group = self._resolve_group(proc, unit)
        if group is None:
            raise ScopeUnavailable(
                f"could not place the workload in a cgroup (unit {unit})"
            )
        return _handle(proc, argv, group_path=group, isolated=True)

    def _resolve_group(self, proc: subprocess.Popen, unit: str) -> str | None:
        """Wait for the child to appear inside the new scope's cgroup."""
        needle = f"{unit}.scope"
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            path = _read_cgroup(self._proc_root, proc.pid)
            if path and needle in path:
                return path
            if proc.poll() is not None and proc.returncode != 0:
                return None  # systemd-run itself failed; nothing to salvage
            time.sleep(0.02)
        return None


class ScopeUnavailable(RuntimeError):
    """systemd-run is present but could not give us an accounting boundary."""


class FallbackLauncher:
    """Try each launcher in order; the first that works wins.

    Composite over ``ProcessLauncher``, so callers still see a single launcher.
    """

    name = "fallback"

    def __init__(self, launchers: list) -> None:
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


def _handle(
    proc: subprocess.Popen,
    argv: list[str],
    *,
    group_path: str | None,
    isolated: bool,
) -> LaunchedWorkload:
    def send(signum: int = signal.SIGTERM) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signum)
        except (ProcessLookupError, PermissionError):
            pass

    return LaunchedWorkload(
        pid=proc.pid,
        argv=list(argv),
        group_path=group_path,
        wait=proc.wait,
        signal=send,
        isolated=isolated,
    )


def _read_cgroup(proc_root: str, pid: int) -> str | None:
    from .procfs import parse_cgroup

    try:
        with open(os.path.join(proc_root, str(pid), "cgroup")) as handle:
            return parse_cgroup(handle.read())
    except OSError:
        return None
