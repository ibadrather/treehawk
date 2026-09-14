"""Starting a workload, on any POSIX system.

Nothing here is specific to one kernel: a child in its own session, signalled
by process group, is the same arrangement on Linux and on macOS. The parts that
*are* specific - a systemd scope on Linux - live in that platform's package and
plug in through :class:`FallbackLauncher`.

``start_new_session=True`` is the common thread. It gives the workload its own
session and process group, which keeps terminal signals from reaching it behind
our back, gives the session expansion strategy something stable to follow, and
lets one ``killpg`` reach the whole workload rather than just the process we
happen to hold.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess

from treehawk.core.compat import override
from treehawk.core.errors import LaunchFailed
from treehawk.core.interfaces import ProcessLauncher
from treehawk.core.models import LaunchedWorkload


class SubprocessWorkload(LaunchedWorkload):
    """A workload started with :mod:`subprocess`, signalled by process group."""

    def __init__(
        self,
        *,
        process: subprocess.Popen[bytes],
        argv: list[str],
        group_path: str | None = None,
    ) -> None:
        super().__init__(pid=process.pid, argv=list(argv), group_path=group_path)
        self._process = process

    @override
    def poll(self) -> int | None:
        return self._process.poll()

    @override
    def signal(self, signum: int = signal.SIGTERM) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):  # already gone, or no longer ours to signal
            os.killpg(os.getpgid(self._process.pid), signum)


class DirectLauncher:
    """Start the command in its own session, tracked by process listing alone.

    The fallback every platform has: no accounting boundary, but still better
    than attaching, because nothing is missed before the first sample.
    """

    @property
    def name(self) -> str:
        return "direct"

    def available(self) -> bool:
        return True

    def launch(self, argv: list[str]) -> LaunchedWorkload:
        process = subprocess.Popen(argv, start_new_session=True)
        return SubprocessWorkload(process=process, argv=argv)


class FallbackLauncher:
    """Try each launcher in order; the first that works wins.

    Composite over ``ProcessLauncher``, so callers still see a single launcher.
    Any :class:`LaunchFailed` from a member is a reason to try the next one -
    which is what lets a platform offer an isolating launcher without having to
    promise it will work here (no user bus, a container, no systemd at all).
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
            except (OSError, LaunchFailed) as exc:
                errors.append(f"{launcher.name}: {exc}")
                continue
            self.used = launcher.name
            if not workload.isolated:
                self.notes.append(
                    "no kernel accounting boundary: membership is inferred from the "
                    "process table, so a process that both detaches and leaves the "
                    "workload's group could be missed"
                )
            self.notes.extend(errors)
            return workload
        raise LaunchFailed("could not start the workload: " + "; ".join(errors))
