"""Reading a (fake) macOS process table end to end."""

from __future__ import annotations

from conftest import APPLE_SILICON_TIMEBASE, FakeProcess, FakeProcessTable

from treehawk.core.config import MemoryDetail
from treehawk.platforms.darwin.source import DarwinProcessSource

SZOMB = 5


def test_scan_returns_every_process_the_kernel_admits_to(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, comm="parent"))
    table.add(FakeProcess(pid=101, comm="child", ppid=100))
    source = DarwinProcessSource(table)

    procs = source.scan()

    assert set(procs) == {100, 101}
    assert procs[101].ppid == 100


def test_a_process_that_exits_mid_scan_is_skipped_not_raised(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100))
    source = DarwinProcessSource(table)
    # The listing named it, then it died before proc_pidinfo reached it.
    table.remove(100)

    assert source.scan() == {}
    assert source.read_info(100) is None


def test_cpu_time_is_converted_from_mach_units_to_nanoseconds(table: FakeProcessTable) -> None:
    # Two seconds of CPU on the 24 MHz Apple Silicon timer.
    table.add(FakeProcess(pid=100, cpu_mach_units=48_000_000))
    info = DarwinProcessSource(table).read_info(100)

    assert info is not None
    # clk_tck is 1e9 on macOS, so cpu_ticks / clk_tck has to be seconds.
    assert info.cpu_ticks == 2_000_000_000


def test_the_timebase_is_read_once_rather_than_per_process(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, cpu_mach_units=24_000_000))
    source = DarwinProcessSource(table)
    # A later machine cannot change its own timer; a cached value is correct.
    table.timebase_fraction = APPLE_SILICON_TIMEBASE
    info = source.read_info(100)

    assert info is not None
    assert info.cpu_ticks == 1_000_000_000


def test_a_zombie_is_reported_as_one(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, status=SZOMB))
    info = DarwinProcessSource(table).read_info(100)

    assert info is not None
    assert info.is_zombie


def test_the_identity_survives_pid_reuse(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, starttime_usec=1_000_000))
    first = DarwinProcessSource(table).read_info(100)
    table.remove(100)
    table.add(FakeProcess(pid=100, starttime_usec=2_000_000))
    second = DarwinProcessSource(table).read_info(100)

    assert first is not None
    assert second is not None
    assert first.identity != second.identity  # same pid, different process


def test_the_group_path_is_the_processs_coalition(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, coalition=1779))

    assert DarwinProcessSource(table).read_group_path(100) == "coalition:1779"


def test_an_unreadable_coalition_is_no_group_at_all(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, coalition=None))

    assert DarwinProcessSource(table).read_group_path(100) is None


def test_enrich_adds_the_command_line_the_footprint_and_the_group(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, argv="python train.py", footprint=1234, coalition=42))
    source = DarwinProcessSource(table)
    info = source.read_info(100)
    assert info is not None

    sample = source.enrich(info=info)

    assert sample.cmdline == "python train.py"
    assert sample.pss_bytes == 1234  # phys_footprint, named in the header
    assert sample.cgroup == "coalition:42"


def test_swap_is_left_unknown_because_macos_does_not_report_it(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, footprint=1234))
    source = DarwinProcessSource(table)
    info = source.read_info(100)
    assert info is not None

    # macOS compresses rather than swapping per process; treehawk never fakes
    # a value it could not read.
    assert source.enrich(info=info).swap_bytes is None


def test_resident_only_mode_skips_the_footprint_read(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, footprint=1234))
    source = DarwinProcessSource(table)
    info = source.read_info(100)
    assert info is not None

    sample = source.enrich(info=info, memory=MemoryDetail.RESIDENT)

    assert sample.pss_bytes is None  # what --no-pss buys back


def test_another_users_process_yields_partial_data_rather_than_an_error(table: FakeProcessTable) -> None:
    # EPERM on both KERN_PROCARGS2 and proc_pid_rusage.
    table.add(FakeProcess(pid=100, comm="launchd", argv="", footprint=None))
    source = DarwinProcessSource(table)
    info = source.read_info(100)
    assert info is not None

    sample = source.enrich(info=info)

    assert sample.pss_bytes is None
    assert sample.cmdline == "launchd"  # falls back to the name we could read


def test_the_command_line_is_read_once_per_process_not_once_per_sample(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, argv="python train.py"))
    source = DarwinProcessSource(table)
    info = source.read_info(100)
    assert info is not None

    for _ in range(5):
        source.enrich(info=info)

    assert table.argv_reads == 1  # a sysctl each, so caching is the point


def test_forgetting_a_process_drops_its_cached_command_line(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, argv="python train.py"))
    source = DarwinProcessSource(table)
    info = source.read_info(100)
    assert info is not None
    source.enrich(info=info)

    source.forget(info.identity)
    source.enrich(info=info)

    assert table.argv_reads == 2
