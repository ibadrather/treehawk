"""Reading a (fake) macOS process table end to end."""

from __future__ import annotations

from conftest import APPLE_SILICON_TIMEBASE, FakeProcess, FakeProcessTable

from treehawk.core.config import MemoryDetail
from treehawk.core.matchers import build_matcher
from treehawk.core.strategies import build_strategies
from treehawk.core.tracker import Tracker
from treehawk.platforms.darwin.coalition import CoalitionSource
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


# -- membership in a coalition that is the whole terminal app ---------------

TERMINAL = 1779
"""One coalition for the terminal app, the shell, treehawk and the workload."""
RUN_ID = "TREEHAWK_RUN_ID=7097c761417f4a599f3a36538f7055e0"


def tracker_in_a_terminal(table: FakeProcessTable, *, marker: str | None) -> Tracker:
    """treehawk (pid 9) watching pid 100, both in the terminal's coalition."""
    table.add(FakeProcess(pid=1, ppid=0, comm="launchd", coalition=1))
    table.add(FakeProcess(pid=9, comm="treehawk", coalition=TERMINAL))
    table.add(FakeProcess(pid=100, sid=100, comm="make", coalition=TERMINAL, environ=(RUN_ID,)))
    source = DarwinProcessSource(table)
    tracker = Tracker(
        matcher=build_matcher(kind="pid", value=100),
        strategies=build_strategies(("tree", "cgroup", "session", "orphan")),
        source=source,
        groups=CoalitionSource(table, ttl=0.0),
        self_pid=9,
        self_group=source.read_group_path(9),
        self_sid=9,
        marker=marker,
        boundary_name="coalition",
        exclude_from_seed=frozenset({9}),
        exclude_from_expansion=frozenset({9}),
    )
    tracker.seed(source.scan())
    tracker.refresh(source.scan())
    return tracker


def test_helpers_launchd_starts_for_the_terminal_are_not_the_workload(table: FakeProcessTable) -> None:
    """What a real two-hour run adopted: 35 processes that were never its own."""
    tracker = tracker_in_a_terminal(table, marker=RUN_ID)
    source = DarwinProcessSource(table)

    table.add(FakeProcess(pid=200, ppid=100, comm="docker", coalition=TERMINAL, environ=(RUN_ID,)))
    for pid, comm in ((300, "MTLCompilerServ"), (301, "chrome_crashpad"), (302, "GoogleUpdater")):
        table.add(FakeProcess(pid=pid, ppid=1, sid=pid, comm=comm, coalition=TERMINAL))

    result = tracker.refresh(source.scan())

    assert {info.pid for info in result.alive} == {100, 200}
    assert result.via[200] == "tree"


def test_a_daemonized_child_is_still_adopted_by_the_run_id_it_inherited(table: FakeProcessTable) -> None:
    tracker = tracker_in_a_terminal(table, marker=RUN_ID)
    source = DarwinProcessSource(table)

    table.add(FakeProcess(pid=400, ppid=1, sid=400, coalition=TERMINAL, environ=("HOME=/tmp", RUN_ID)))
    table.add(FakeProcess(pid=300, ppid=1, sid=300, comm="GoogleUpdater", coalition=TERMINAL))

    result = tracker.refresh(source.scan())

    assert {info.pid for info in result.alive} == {100, 400}
    assert result.via[400] == "orphan"


def test_the_environment_is_read_only_for_a_candidate_never_for_every_process(table: FakeProcessTable) -> None:
    tracker = tracker_in_a_terminal(table, marker=RUN_ID)
    source = DarwinProcessSource(table)
    table.add(FakeProcess(pid=300, ppid=1, sid=300, comm="GoogleUpdater", coalition=TERMINAL))
    table.add(FakeProcess(pid=500, ppid=1, sid=500, comm="Safari", coalition=656))  # another app entirely
    table.env_reads = 0

    tracker.refresh(source.scan())
    tracker.refresh(source.scan())  # no longer new: not asked about again

    assert table.env_reads == 1


def test_watch_has_no_run_id_so_it_adopts_no_orphan_and_says_so(table: FakeProcessTable) -> None:
    tracker = tracker_in_a_terminal(table, marker=None)
    source = DarwinProcessSource(table)

    table.add(FakeProcess(pid=400, ppid=1, sid=400, coalition=TERMINAL, environ=(RUN_ID,)))

    assert {info.pid for info in tracker.refresh(source.scan()).alive} == {100}
    (note,) = tracker.notes
    assert "orphan rule inactive" in note
    assert "coalition" in note
