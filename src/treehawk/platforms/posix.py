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

:func:`start_process` is the other thing both platforms share: whether the
workload writes to treehawk's terminal or to a pty of its own. A pty rather
than a pipe, because a pipe is not invisible - a workload that checks
``isatty`` turns off its colours and switches from line to block buffering, so
its output would arrive in 8KiB bursts minutes apart. treehawk is supposed to
measure the workload, not change how it behaves.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pty
import shutil
import signal
import struct
import subprocess
import termios
from typing import IO

from treehawk.core.compat import override
from treehawk.core.config import WorkloadOutput
from treehawk.core.errors import LaunchFailed
from treehawk.core.interfaces import ProcessLauncher
from treehawk.core.models import LaunchedWorkload
from treehawk.platforms.constants import POSIX


class SubprocessWorkload(LaunchedWorkload):
    """A workload started with :mod:`subprocess`, signalled by process group."""

    def __init__(
        self,
        *,
        process: subprocess.Popen[bytes],
        argv: list[str],
        group_path: str | None = None,
        output_stream: IO[bytes] | None = None,
    ) -> None:
        super().__init__(
            pid=process.pid,
            argv=list(argv),
            group_path=group_path,
            output_stream=output_stream,
        )
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

    def launch(self, argv: list[str], *, output: WorkloadOutput = WorkloadOutput.INHERIT) -> LaunchedWorkload:
        process, stream = start_process(argv, output=output)
        return SubprocessWorkload(process=process, argv=argv, output_stream=stream)


class FallbackLauncher:
    """Try each launcher in order; the first that works wins.

    Composite over ``ProcessLauncher``, so callers still see a single launcher.
    Any :class:`LaunchFailed` from a member is a reason to try the next one -
    which is what lets a platform offer an isolating launcher without having to
    promise it will work here (no user bus, a container, no systemd at all).

    What was passed over, and why, is written onto the workload that is
    returned rather than kept here, so one launcher can start any number of
    workloads without their notes running together.
    """

    @property
    def name(self) -> str:
        return "fallback"

    def available(self) -> bool:
        return any(launcher.available() for launcher in self._launchers)

    def __init__(self, launchers: list[ProcessLauncher]) -> None:
        self._launchers = launchers

    def launch(self, argv: list[str], *, output: WorkloadOutput = WorkloadOutput.INHERIT) -> LaunchedWorkload:
        errors: list[str] = []
        for launcher in self._launchers:
            if not launcher.available():
                errors.append(f"{launcher.name}: unavailable")
                continue
            try:
                workload = launcher.launch(argv, output=output)
            except (OSError, LaunchFailed) as exc:
                errors.append(f"{launcher.name}: {exc}")
                continue
            if not workload.isolated:
                workload.notes.append(
                    "no kernel accounting boundary: membership is inferred from the "
                    "process table, so a process that both detaches and leaves the "
                    "workload's group could be missed"
                )
            workload.notes.extend(errors)
            return workload
        raise LaunchFailed("could not start the workload: " + "; ".join(errors))


def start_process(
    argv: list[str],
    *,
    output: WorkloadOutput,
) -> tuple[subprocess.Popen[bytes], IO[bytes] | None]:
    """Start ``argv`` in a session of its own, and say where its output went.

    With :attr:`~treehawk.core.config.WorkloadOutput.INHERIT` the workload gets
    treehawk's own stdout and stderr and there is nothing to read. With
    ``CAPTURE`` it gets both ends of a fresh pty, and the read end comes back
    for :class:`~treehawk.core.capture.OutputReader` to drain.

    The slave is closed here either way: once the child holds it, treehawk
    keeping a copy would mean the read end never reports end of file.
    """
    if output is WorkloadOutput.INHERIT:
        return subprocess.Popen(argv, start_new_session=True), None
    master, slave = pty.openpty()
    _set_window_size(slave)
    try:
        process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)
    return process, os.fdopen(master, "rb", 0)


def _set_window_size(fd: int) -> None:
    """Give the new pty the size of the real terminal.

    A pty starts out 0x0, and a workload that lays its output out to the width
    of its terminal would draw it to nothing.
    """
    size = shutil.get_terminal_size(fallback=(POSIX.pty_columns, POSIX.pty_rows))
    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", size.lines, size.columns, 0, 0))
