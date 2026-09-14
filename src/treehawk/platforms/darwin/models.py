"""What treehawk reads out of the macOS kernel, as plain values."""

from __future__ import annotations

from dataclasses import dataclass

from treehawk.platforms.darwin.constants import LIBPROC


@dataclass(frozen=True, slots=True)
class Timebase:
    """How many nanoseconds one mach time unit is worth, as a fraction.

    1/1 on Intel, 125/3 on Apple Silicon. The default is the identity, so a
    reader that never asked the kernel cannot silently rescale anything.
    """

    numer: int = 1
    denom: int = 1


@dataclass(frozen=True, slots=True)
class TaskInfo:
    """One process, as a single ``PROC_PIDTASKALLINFO`` call reports it."""

    pid: int
    ppid: int
    pgid: int
    status: int
    starttime_usec: int
    """Start time in microseconds since the epoch. treehawk uses it only to
    tell a recycled PID from the original, which this is unique enough for."""
    comm: str
    cpu_mach_units: int
    rss_bytes: int
    threads: int

    @property
    def state(self) -> str:
        return state_from_status(self.status)


def state_from_status(status: int) -> str:
    """Map ``pbi_status`` to the single-letter state the core models use."""
    return LIBPROC.process_states.get(status, "?")
