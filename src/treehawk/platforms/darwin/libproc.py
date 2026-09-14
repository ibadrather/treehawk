"""The macOS kernel interface treehawk reads process state through.

macOS has no ``/proc``. The equivalent facts come from ``libproc``
(``proc_listpids``, ``proc_pidinfo``, ``proc_pid_rusage``) and from ``sysctl``,
so this module is the one place that calls into the C library.

It is laid out like :mod:`treehawk.platforms.linux.procfs` and for the same
reason. Every kernel call returns a fixed-layout byte buffer, and the functions
that turn those bytes into values are pure: they take ``bytes`` and return
plain data, touch nothing, and are unit-tested against captured buffers. The
structure layouts below are the contract, checked by
``assert_layouts_match_the_kernel`` in the tests.

The library is bound inside :meth:`LibProc.__init__`, never at import time, for
two reasons: the module then imports cleanly on any platform, and mypy
therefore checks it on the Linux CI runner too - which it would skip entirely
if the code sat behind a ``sys.platform`` guard.

Two Apple Silicon details are easy to get wrong and expensive to miss:

* **CPU times are in mach units, not nanoseconds.** The conversion comes from
  ``mach_timebase_info``, and it is 125/3 on Apple Silicon where an Intel Mac
  reports 1/1 - so reading the raw number as nanoseconds understates CPU time
  by about 41x.
* **Pages are 16 KiB.** Nothing here multiplies by a page size (libproc reports
  bytes already), but :mod:`treehawk.platforms.darwin.host` reports the real
  value rather than assuming 4096.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import struct
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

PROC_ALL_PIDS: Final = 1
PROC_PIDTASKALLINFO: Final = 2
PROC_PIDCOALITIONINFO: Final = 20
RUSAGE_INFO_V4: Final = 4

CTL_KERN: Final = 1
KERN_ARGMAX: Final = 8
KERN_PROCARGS2: Final = 49

COALITION_PREFIX: Final = "coalition:"
"""Marks a group path as a coalition id, so nothing reads it as a cgroup."""

# ``struct proc_taskallinfo`` = proc_bsdinfo (136 bytes) then proc_taskinfo
# (96), little-endian with no interior padding on both arm64 and x86_64.
#   12 uint32  flags status xstatus pid ppid uid gid ruid rgid svuid svgid rfu
#   16s / 32s  comm, name
#    5 uint32  nfiles pgid pjobc tdev tpgid
#      int32   nice
#    2 uint64  start_tvsec start_tvusec
#    6 uint64  virtual resident total_user total_system threads_user threads_system
#   12 int32   policy faults pageins cow messages_sent/received syscalls_mach/unix
#              csw threadnum numrunning priority
TASK_ALL_INFO: Final = "<12I16s32s5IiQQ6Q12i"
TASK_ALL_INFO_SIZE: Final = struct.calcsize(TASK_ALL_INFO)

_STATUS = 1
_PID = 3
_PPID = 4
_COMM = 12
_PGID = 15
_START_TVSEC = 20
_START_TVUSEC = 21
_RESIDENT = 23
_TOTAL_USER = 24
_TOTAL_SYSTEM = 25
_THREADNUM = 37

COALITION_INFO: Final = "<5Q"
COALITION_INFO_SIZE: Final = struct.calcsize(COALITION_INFO)
_COALITION_RESOURCE = 0
"""``coalition_id[COALITION_TYPE_RESOURCE]`` - the one a fork cannot leave."""

RUSAGE_INFO_V4_SIZE: Final = 296
_PHYS_FOOTPRINT_OFFSET: Final = 72
"""``ri_phys_footprint`` sits after a 16-byte uuid and seven uint64s."""

TIMEBASE_INFO: Final = "<2I"
TIMEBASE_INFO_SIZE: Final = struct.calcsize(TIMEBASE_INFO)

_STATES: Final = {1: "I", 2: "R", 3: "S", 4: "T", 5: "Z"}
"""``SIDL``/``SRUN``/``SSLEEP``/``SSTOP``/``SZOMB`` as the single letters the
core models expect - ``Z`` above all, which ``ProcInfo.is_zombie`` compares
against."""


class TaskInfoParseError(ValueError):
    """The buffer was short: the process died mid-read, or is not ours."""


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


@runtime_checkable
class ProcessTable(Protocol):
    """Everything :mod:`treehawk.platforms.darwin.source` asks of the kernel.

    Narrow on purpose, and returning only plain values. The Linux readers each
    take their root directory as an argument, which is what lets the tests
    point them at a fake ``/proc``; there is no directory to point at here, so
    this protocol is the seam the tests substitute at instead.
    """

    def timebase(self) -> Timebase: ...

    def list_pids(self) -> list[int]:
        """Every visible PID, or an empty list if the call failed."""
        ...

    def task_info(self, pid: int) -> TaskInfo | None:
        """Cheap facts for one process, or None if it is gone."""
        ...

    def coalition_id(self, pid: int) -> int | None:
        """The process' resource coalition, or None if it is unreadable."""
        ...

    def argv(self, pid: int) -> str:
        """The full command line, or ``""`` if another user owns it."""
        ...

    def footprint(self, pid: int) -> int | None:
        """``ri_phys_footprint`` in bytes, or None if it is unreadable."""
        ...

    def session_id(self, pid: int) -> int:
        """The session ``pid`` belongs to, or 0 if it cannot be read."""
        ...


# ---------------------------------------------------------------------------
# pure parsers - no syscalls, so the whole metric path is testable anywhere
# ---------------------------------------------------------------------------


def parse_task_all_info(raw: bytes) -> TaskInfo:
    """Parse a ``PROC_PIDTASKALLINFO`` buffer into the fields treehawk uses."""
    if len(raw) < TASK_ALL_INFO_SIZE:
        raise TaskInfoParseError(f"short task_all_info buffer: {len(raw)} of {TASK_ALL_INFO_SIZE} bytes")
    fields = struct.unpack_from(TASK_ALL_INFO, raw)
    return TaskInfo(
        pid=int(fields[_PID]),
        ppid=int(fields[_PPID]),
        pgid=int(fields[_PGID]),
        status=int(fields[_STATUS]),
        starttime_usec=int(fields[_START_TVSEC]) * 1_000_000 + int(fields[_START_TVUSEC]),
        comm=_c_string(fields[_COMM]),
        cpu_mach_units=int(fields[_TOTAL_USER]) + int(fields[_TOTAL_SYSTEM]),
        rss_bytes=int(fields[_RESIDENT]),
        threads=int(fields[_THREADNUM]),
    )


def parse_coalition_id(raw: bytes) -> int | None:
    """The resource coalition id in a ``PROC_PIDCOALITIONINFO`` buffer."""
    if len(raw) < COALITION_INFO_SIZE:
        return None
    identifier = int(struct.unpack_from(COALITION_INFO, raw)[_COALITION_RESOURCE])
    return identifier or None


def parse_phys_footprint(raw: bytes) -> int | None:
    """``ri_phys_footprint`` out of a ``rusage_info_v4`` buffer.

    macOS has no PSS. This is the kernel's own charge for what a process costs
    the machine - what Activity Monitor shows - and it is what treehawk records
    as the fair-memory measure there, named as such in the log header.
    """
    if len(raw) < _PHYS_FOOTPRINT_OFFSET + 8:
        return None
    return int(struct.unpack_from("<Q", raw, _PHYS_FOOTPRINT_OFFSET)[0])


def parse_timebase(raw: bytes) -> Timebase:
    """A ``mach_timebase_info`` buffer, defaulting to the identity fraction."""
    if len(raw) < TIMEBASE_INFO_SIZE:
        return Timebase()
    numer, denom = struct.unpack_from(TIMEBASE_INFO, raw)
    return Timebase(numer=int(numer) or 1, denom=int(denom) or 1)


def parse_procargs2(raw: bytes) -> str:
    """Turn a ``KERN_PROCARGS2`` blob into a displayable command line.

    The layout is a native ``int`` argc, then the executable path, then padding
    NULs, then exactly ``argc`` NUL-terminated arguments. The environment
    follows them and is deliberately dropped: it holds secrets, and treehawk
    writes what it reads straight into the log.
    """
    header = struct.calcsize("i")
    if len(raw) < header:
        return ""
    argc = int(struct.unpack_from("i", raw)[0])
    if argc <= 0:
        return ""
    chunks = raw[header:].split(b"\x00")
    # chunks[0] is the executable path, which argv[0] repeats; drop it, then
    # drop the padding NULs that align the start of argv.
    arguments = [chunk for chunk in chunks[1:] if chunk][:argc]
    return b" ".join(arguments).decode("utf-8", "replace")


def state_from_status(status: int) -> str:
    """Map ``pbi_status`` to the single-letter state the core models use."""
    return _STATES.get(status, "?")


def nanoseconds_from_mach(units: int, timebase: Timebase) -> int:
    """Convert mach absolute time units to nanoseconds.

    The whole macOS CPU path rests on this: ``proc_pidinfo`` reports task time
    in mach units, and one unit is 125/3 ns on Apple Silicon.
    """
    if timebase.denom <= 0:
        return units
    return units * timebase.numer // timebase.denom


def coalition_path(identifier: int) -> str:
    """The group path treehawk records for a coalition."""
    return f"{COALITION_PREFIX}{identifier}"


def coalition_id_of(path: str) -> int | None:
    """The coalition id in a group path, or None if it is not one."""
    if not path.startswith(COALITION_PREFIX):
        return None
    try:
        return int(path[len(COALITION_PREFIX) :])
    except ValueError:
        return None


def _c_string(raw: bytes) -> str:
    """A fixed-width C string field: the kernel leaves junk after the NUL."""
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# the live implementation
# ---------------------------------------------------------------------------


class LibProc:
    """The live :class:`ProcessTable`, bound to the C library through ctypes.

    Every call tolerates failure the way the Linux reader tolerates a missing
    file: a process can exit between the listing and the read (ESRCH), and one
    owned by another user refuses ``rusage`` and ``KERN_PROCARGS2`` (EPERM).
    Neither is exceptional here, so both yield partial data rather than raise.
    """

    def __init__(self) -> None:
        self._libc = _load_libc()
        self._argmax = self._read_argmax()
        self._timebase = self._read_timebase()

    def timebase(self) -> Timebase:
        return self._timebase

    def list_pids(self) -> list[int]:
        needed = int(self._libc.proc_listpids(PROC_ALL_PIDS, 0, None, 0))
        if needed <= 0:
            return []
        # Processes can appear between sizing the buffer and filling it, so ask
        # for headroom rather than racing the kernel for an exact fit.
        width = struct.calcsize("<i")
        buffer = ctypes.create_string_buffer(needed + 64 * width)
        written = int(self._libc.proc_listpids(PROC_ALL_PIDS, 0, buffer, len(buffer)))
        if written <= 0:
            return []
        count = written // width
        return [pid for pid in struct.unpack_from(f"<{count}i", buffer.raw) if pid > 0]

    def task_info(self, pid: int) -> TaskInfo | None:
        raw = self._pidinfo(pid=pid, flavor=PROC_PIDTASKALLINFO, size=TASK_ALL_INFO_SIZE)
        if raw is None:
            return None
        try:
            return parse_task_all_info(raw)
        except TaskInfoParseError:
            return None

    def coalition_id(self, pid: int) -> int | None:
        raw = self._pidinfo(pid=pid, flavor=PROC_PIDCOALITIONINFO, size=COALITION_INFO_SIZE)
        return None if raw is None else parse_coalition_id(raw)

    def argv(self, pid: int) -> str:
        if self._argmax <= 0:
            return ""
        mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
        buffer = ctypes.create_string_buffer(self._argmax)
        size = ctypes.c_size_t(self._argmax)
        if int(self._libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0)) != 0:
            return ""  # another user's process, or it exited
        return parse_procargs2(buffer.raw[: size.value])

    def footprint(self, pid: int) -> int | None:
        buffer = ctypes.create_string_buffer(RUSAGE_INFO_V4_SIZE)
        if int(self._libc.proc_pid_rusage(pid, RUSAGE_INFO_V4, buffer)) != 0:
            return None  # EPERM: not our process
        return parse_phys_footprint(buffer.raw)

    def session_id(self, pid: int) -> int:
        """libproc does not report the session, so this one fact is plain libc.

        The session expansion rule needs it on macOS just as much as on Linux.
        """
        try:
            return os.getsid(pid)
        except OSError:
            return 0

    def _pidinfo(self, *, pid: int, flavor: int, size: int) -> bytes | None:
        buffer = ctypes.create_string_buffer(size)
        written = int(self._libc.proc_pidinfo(pid, flavor, 0, buffer, size))
        return buffer.raw if written >= size else None

    def _read_argmax(self) -> int:
        mib = (ctypes.c_int * 2)(CTL_KERN, KERN_ARGMAX)
        value = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if int(self._libc.sysctl(mib, 2, ctypes.byref(value), ctypes.byref(size), None, 0)) != 0:
            return 0
        return int(value.value)

    def _read_timebase(self) -> Timebase:
        buffer = ctypes.create_string_buffer(TIMEBASE_INFO_SIZE)
        if int(self._libc.mach_timebase_info(buffer)) != 0:
            return Timebase()
        return parse_timebase(buffer.raw)


def _load_libc() -> ctypes.CDLL:
    """Bind the C library and declare the signatures treehawk relies on.

    Called only from :class:`LibProc`, so importing this module costs nothing
    and fails nowhere.
    """
    name = ctypes.util.find_library("c")
    if name is None:  # pragma: no cover - libSystem is always present on macOS
        raise OSError("could not find the C library")
    libc = ctypes.CDLL(name, use_errno=True)

    libc.proc_listpids.restype = ctypes.c_int
    libc.proc_listpids.argtypes = (ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int)
    libc.proc_pidinfo.restype = ctypes.c_int
    libc.proc_pidinfo.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int)
    libc.proc_pid_rusage.restype = ctypes.c_int
    libc.proc_pid_rusage.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
    libc.sysctl.restype = ctypes.c_int
    libc.sysctl.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
    )
    libc.mach_timebase_info.restype = ctypes.c_int
    libc.mach_timebase_info.argtypes = (ctypes.c_void_p,)
    return libc
