"""Parsing the byte buffers the macOS kernel hands back.

Pure functions over bytes, so these run anywhere - the C library is never
bound. The buffers are built here with the same layout the kernel uses, and
:func:`test_the_documented_layouts_match_the_kernel_structs` pins the sizes
against ``<sys/proc_info.h>``, which is what a silent struct change would break
first.
"""

from __future__ import annotations

import struct

import pytest
from conftest import APPLE_SILICON_TIMEBASE

from treehawk.platforms.darwin.constants import LIBPROC, TASK_ALL_INFO
from treehawk.platforms.darwin.libproc import (
    TaskInfoParseError,
    coalition_id_of,
    coalition_path,
    nanoseconds_from_mach,
    parse_coalition_id,
    parse_phys_footprint,
    parse_procargs2,
    parse_task_all_info,
    parse_timebase,
)
from treehawk.platforms.darwin.models import Timebase, state_from_status

SSLEEP = 3
SZOMB = 5


def task_all_info(
    *,
    pid: int = 100,
    ppid: int = 1,
    pgid: int = 100,
    status: int = SSLEEP,
    comm: bytes = b"worker",
    tvsec: int = 1_700_000_000,
    tvusec: int = 250_000,
    resident: int = 4096,
    user: int = 0,
    system: int = 0,
    threads: int = 1,
) -> bytes:
    """A PROC_PIDTASKALLINFO buffer, laid out as the kernel lays it out."""
    return struct.pack(
        TASK_ALL_INFO.struct_format,
        0,  # pbi_flags
        status,
        0,  # pbi_xstatus
        pid,
        ppid,
        *(0,) * 7,  # uid gid ruid rgid svuid svgid rfu_1
        comm,
        b"worker-long-name",
        0,  # pbi_nfiles
        pgid,
        *(0,) * 3,  # pjobc tdev tpgid
        0,  # pbi_nice
        tvsec,
        tvusec,
        0,  # pti_virtual_size
        resident,
        user,
        system,
        *(0,) * 2,  # threads_user threads_system
        *(0,) * 9,  # policy faults pageins cow messages x2 syscalls x2 csw
        threads,
        *(0,) * 2,  # numrunning priority
    )


def test_the_documented_layouts_match_the_kernel_structs() -> None:
    # From <sys/proc_info.h>: proc_bsdinfo is 136 bytes and proc_taskinfo 96.
    assert TASK_ALL_INFO.size == 136 + 96
    assert LIBPROC.coalition_info_size == 40
    assert LIBPROC.rusage_info_v4_size == 296
    assert LIBPROC.timebase_info_size == 8


def test_task_all_info_is_parsed_into_the_fields_treehawk_uses() -> None:
    info = parse_task_all_info(task_all_info(pid=4213, ppid=99, pgid=4213, user=700, system=300, threads=6))

    assert (info.pid, info.ppid, info.pgid) == (4213, 99, 4213)
    assert info.comm == "worker"
    assert info.threads == 6
    assert info.cpu_mach_units == 1000  # user and system are summed
    assert info.starttime_usec == 1_700_000_000 * 1_000_000 + 250_000


def test_a_fixed_width_comm_stops_at_its_first_nul() -> None:
    # The kernel does not clear the rest of the 16-byte field, so whatever the
    # last tenant left is still sitting there.
    info = parse_task_all_info(task_all_info(comm=b"Python\x00.9\x00er"))

    assert info.comm == "Python"


def test_a_truncated_buffer_is_refused_rather_than_guessed() -> None:
    with pytest.raises(TaskInfoParseError):
        parse_task_all_info(task_all_info()[:100])


@pytest.mark.parametrize(
    ("status", "expected"),
    [(1, "I"), (2, "R"), (3, "S"), (4, "T"), (SZOMB, "Z"), (99, "?")],
)
def test_every_bsd_status_maps_to_a_state_letter(status: int, expected: str) -> None:
    assert state_from_status(status) == expected


def test_a_zombie_is_recognised_through_the_shared_model() -> None:
    # ProcInfo.is_zombie compares against "Z", so the mapping has to agree.
    assert parse_task_all_info(task_all_info(status=SZOMB)).state == "Z"


def test_mach_units_become_nanoseconds_on_apple_silicon() -> None:
    # One second of CPU, as an M-series Mac's 24 MHz timer counts it.
    one_second = 24_000_000

    assert nanoseconds_from_mach(one_second, APPLE_SILICON_TIMEBASE) == 1_000_000_000


def test_mach_units_are_already_nanoseconds_on_intel() -> None:
    assert nanoseconds_from_mach(1_000_000_000, Timebase()) == 1_000_000_000


def test_a_nonsense_timebase_leaves_the_number_alone() -> None:
    # Better a raw count than a division by zero halfway through a run.
    assert nanoseconds_from_mach(1234, Timebase(numer=125, denom=0)) == 1234


def test_the_timebase_is_read_off_the_kernels_buffer() -> None:
    assert parse_timebase(struct.pack("<2I", 125, 3)) == APPLE_SILICON_TIMEBASE


def test_a_short_timebase_buffer_falls_back_to_the_identity() -> None:
    assert parse_timebase(b"\x00") == Timebase(numer=1, denom=1)


def test_the_resource_coalition_is_the_one_that_is_read() -> None:
    # coalition_id[0] is the resource coalition; [1] is jetsam, and unrelated.
    raw = struct.pack("<5Q", 1779, 1780, 0, 0, 0)

    assert parse_coalition_id(raw) == 1779


def test_coalition_zero_reads_as_no_coalition() -> None:
    assert parse_coalition_id(struct.pack("<5Q", 0, 0, 0, 0, 0)) is None
    assert parse_coalition_id(b"") is None


def test_a_coalition_path_round_trips() -> None:
    assert coalition_path(1779) == "coalition:1779"
    assert coalition_id_of("coalition:1779") == 1779


def test_a_cgroup_path_is_not_mistaken_for_a_coalition() -> None:
    assert coalition_id_of("/user.slice/app.scope") is None
    assert coalition_id_of("coalition:not-a-number") is None


def test_the_footprint_is_read_from_its_offset_in_rusage() -> None:
    raw = bytearray(LIBPROC.rusage_info_v4_size)
    struct.pack_into("<Q", raw, 72, 7_749_896)  # ri_phys_footprint

    assert parse_phys_footprint(bytes(raw)) == 7_749_896


def test_a_refused_rusage_read_yields_no_footprint() -> None:
    # Another user's process: treehawk records null rather than a guess.
    assert parse_phys_footprint(b"\x00" * 8) is None


def test_procargs2_returns_the_command_line_without_the_exec_path() -> None:
    # argc, then the exec path, then padding NULs, then argv, then the environment.
    raw = (
        struct.pack("i", 2)
        + b"/usr/bin/python3\x00\x00\x00"
        + b"python3\x00"
        + b"worker.py\x00"
        + b"SECRET_TOKEN=hunter2\x00"
    )

    assert parse_procargs2(raw) == "python3 worker.py"


def test_procargs2_stops_at_argc_so_the_environment_never_reaches_the_log() -> None:
    raw = struct.pack("i", 1) + b"/bin/sleep\x00" + b"sleep\x00" + b"AWS_SECRET_ACCESS_KEY=x\x00"

    assert parse_procargs2(raw) == "sleep"


def test_an_unreadable_procargs2_blob_is_empty_not_an_error() -> None:
    assert parse_procargs2(b"") == ""
    assert parse_procargs2(struct.pack("i", 0)) == ""
