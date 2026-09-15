"""Coalition membership: macOS' answer to a cgroup's process list."""

from __future__ import annotations

from conftest import FakeProcess, FakeProcessTable

from treehawk.platforms.darwin.coalition import CoalitionSource


class FakeClock:
    """A monotonic clock a test can advance by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_membership_groups_processes_by_their_coalition(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, coalition=1779))
    table.add(FakeProcess(pid=101, coalition=1779))
    table.add(FakeProcess(pid=200, coalition=656))  # an unrelated app
    source = CoalitionSource(table)

    assert source.pids_in("coalition:1779") == {100, 101}
    assert source.pids_in("coalition:656") == {200}


def test_a_detached_child_is_still_in_the_coalition_it_started_in(table: FakeProcessTable) -> None:
    # fork, setsid, fork, parent exits: re-parented to launchd (ppid 1), its
    # session gone - but the coalition is the one thing it could not shed.
    table.add(FakeProcess(pid=100, coalition=1779))
    table.add(FakeProcess(pid=101, ppid=1, sid=101, coalition=1779))

    assert CoalitionSource(table).pids_in("coalition:1779") == {100, 101}


def test_an_unknown_coalition_has_no_members(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, coalition=1779))

    assert CoalitionSource(table).pids_in("coalition:9999") is None


def test_a_cgroup_path_is_not_treated_as_a_coalition(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, coalition=1779))

    assert CoalitionSource(table).pids_in("/user.slice/app.scope") is None


def test_metrics_reports_the_boundary_with_no_readings(table: FakeProcessTable) -> None:
    metrics = CoalitionSource(table).metrics("coalition:1779")

    assert metrics is not None
    assert metrics.path == "coalition:1779"
    # macOS keeps no per-coalition totals an unentitled process may read, so
    # the log carries nulls rather than an invented number.
    assert (metrics.cpu_usec, metrics.memory_bytes, metrics.memory_peak_bytes) == (None, None, None)


def test_one_scan_serves_the_several_questions_a_sample_asks(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, coalition=1779))
    clock = FakeClock()
    source = CoalitionSource(table, ttl=0.25, monotonic=clock)

    source.pids_in("coalition:1779")
    table.add(FakeProcess(pid=101, coalition=1779))
    # The group rule asks again within the same sample, to decide whether the
    # boundary is ours and then to adopt from it. That reuses the one scan.
    assert source.pids_in("coalition:1779") == {100}


def test_the_next_sample_sees_a_fresh_scan(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, coalition=1779))
    clock = FakeClock()
    source = CoalitionSource(table, ttl=0.25, monotonic=clock)
    source.pids_in("coalition:1779")

    table.add(FakeProcess(pid=101, coalition=1779))
    clock.now = 1.0  # any real sampling interval is longer than the ttl

    assert source.pids_in("coalition:1779") == {100, 101}


def test_a_kernel_that_tells_us_nothing_is_reported_as_unavailable(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, coalition=None))

    assert not CoalitionSource(table).available()


def test_a_kernel_that_answers_is_available(table: FakeProcessTable) -> None:
    table.add(FakeProcess(pid=100, coalition=1779))

    assert CoalitionSource(table).available()
