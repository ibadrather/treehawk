"""The macOS kernel interface treehawk reads process state through.

macOS has no ``/proc``. The equivalent facts come from ``libproc``
(``proc_listpids``, ``proc_pidinfo``, ``proc_pid_rusage``) and from ``sysctl``,
so this module is the one place that calls into the C library.

It is laid out like :mod:`treehawk.platforms.linux.procfs` and for the same
reason. Every kernel call returns a fixed-layout byte buffer, and the functions
that turn those bytes into values are pure: they take ``bytes`` and return
plain data, touch nothing, and are unit-tested against captured buffers. The
structure layouts in :mod:`treehawk.platforms.darwin.constants` are the
contract, checked by ``test_the_documented_layouts_match_the_kernel_structs``.

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
from typing import Protocol, runtime_checkable

from treehawk.platforms.darwin.constants import DARWIN, LIBPROC, TASK_ALL_INFO
from treehawk.platforms.darwin.models import TaskInfo, Timebase


class TaskInfoParseError(ValueError):
    """The buffer was short: the process died mid-read, or is not ours."""


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
    if len(raw) < TASK_ALL_INFO.size:
        raise TaskInfoParseError(f"short task_all_info buffer: {len(raw)} of {TASK_ALL_INFO.size} bytes")
    fields = struct.unpack_from(TASK_ALL_INFO.struct_format, raw)
    return TaskInfo(
        pid=int(fields[TASK_ALL_INFO.pid]),
        ppid=int(fields[TASK_ALL_INFO.ppid]),
        pgid=int(fields[TASK_ALL_INFO.pgid]),
        status=int(fields[TASK_ALL_INFO.status]),
        starttime_usec=int(fields[TASK_ALL_INFO.start_tvsec]) * 1_000_000 + int(fields[TASK_ALL_INFO.start_tvusec]),
        comm=_c_string(fields[TASK_ALL_INFO.comm]),
        cpu_mach_units=int(fields[TASK_ALL_INFO.total_user]) + int(fields[TASK_ALL_INFO.total_system]),
        rss_bytes=int(fields[TASK_ALL_INFO.resident]),
        threads=int(fields[TASK_ALL_INFO.threadnum]),
    )


def parse_coalition_id(raw: bytes) -> int | None:
    """The resource coalition id in a ``PROC_PIDCOALITIONINFO`` buffer."""
    if len(raw) < LIBPROC.coalition_info_size:
        return None
    identifier = int(struct.unpack_from(LIBPROC.coalition_info_format, raw)[LIBPROC.coalition_resource_index])
    return identifier or None


def parse_phys_footprint(raw: bytes) -> int | None:
    """``ri_phys_footprint`` out of a ``rusage_info_v4`` buffer.

    macOS has no PSS. This is the kernel's own charge for what a process costs
    the machine - what Activity Monitor shows - and it is what treehawk records
    as the fair-memory measure there, named as such in the log header.
    """
    if len(raw) < LIBPROC.phys_footprint_offset + 8:
        return None
    return int(struct.unpack_from("<Q", raw, LIBPROC.phys_footprint_offset)[0])


def parse_timebase(raw: bytes) -> Timebase:
    """A ``mach_timebase_info`` buffer, defaulting to the identity fraction."""
    if len(raw) < LIBPROC.timebase_info_size:
        return Timebase()
    numer, denom = struct.unpack_from(LIBPROC.timebase_info_format, raw)
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
    return f"{DARWIN.coalition_prefix}{identifier}"


def coalition_id_of(path: str) -> int | None:
    """The coalition id in a group path, or None if it is not one."""
    if not path.startswith(DARWIN.coalition_prefix):
        return None
    try:
        return int(path[len(DARWIN.coalition_prefix) :])
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
        needed = int(self._libc.proc_listpids(LIBPROC.proc_all_pids, 0, None, 0))
        if needed <= 0:
            return []
        # Processes can appear between sizing the buffer and filling it, so ask
        # for headroom rather than racing the kernel for an exact fit.
        width = struct.calcsize("<i")
        buffer = ctypes.create_string_buffer(needed + 64 * width)
        written = int(self._libc.proc_listpids(LIBPROC.proc_all_pids, 0, buffer, len(buffer)))
        if written <= 0:
            return []
        count = written // width
        return [pid for pid in struct.unpack_from(f"<{count}i", buffer.raw) if pid > 0]

    def task_info(self, pid: int) -> TaskInfo | None:
        raw = self._pidinfo(pid=pid, flavor=LIBPROC.proc_pidtaskallinfo, size=TASK_ALL_INFO.size)
        if raw is None:
            return None
        try:
            return parse_task_all_info(raw)
        except TaskInfoParseError:
            return None

    def coalition_id(self, pid: int) -> int | None:
        raw = self._pidinfo(pid=pid, flavor=LIBPROC.proc_pidcoalitioninfo, size=LIBPROC.coalition_info_size)
        return None if raw is None else parse_coalition_id(raw)

    def argv(self, pid: int) -> str:
        if self._argmax <= 0:
            return ""
        mib = (ctypes.c_int * 3)(LIBPROC.ctl_kern, LIBPROC.kern_procargs2, pid)
        buffer = ctypes.create_string_buffer(self._argmax)
        size = ctypes.c_size_t(self._argmax)
        if int(self._libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0)) != 0:
            return ""  # another user's process, or it exited
        return parse_procargs2(buffer.raw[: size.value])

    def footprint(self, pid: int) -> int | None:
        buffer = ctypes.create_string_buffer(LIBPROC.rusage_info_v4_size)
        if int(self._libc.proc_pid_rusage(pid, LIBPROC.rusage_info_v4, buffer)) != 0:
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
        mib = (ctypes.c_int * 2)(LIBPROC.ctl_kern, LIBPROC.kern_argmax)
        value = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if int(self._libc.sysctl(mib, 2, ctypes.byref(value), ctypes.byref(size), None, 0)) != 0:
            return 0
        return int(value.value)

    def _read_timebase(self) -> Timebase:
        buffer = ctypes.create_string_buffer(LIBPROC.timebase_info_size)
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
