"""Starting a workload: the fallback chain, and what it tells the log."""

from __future__ import annotations

import sys

import pytest
from conftest import FakeLauncher

from treehawk.core.config import WorkloadOutput
from treehawk.core.errors import LaunchFailed
from treehawk.platforms.constants import POSIX
from treehawk.platforms.posix import DirectLauncher, FallbackLauncher, start_process

PRINT_RUN_ID = [sys.executable, "-c", f"import os; print(os.environ.get({POSIX.run_id_variable!r}))"]


def test_an_unavailable_launcher_is_passed_over_and_the_reason_noted() -> None:
    isolating = FakeLauncher(launcher_name="scope", is_available=False)
    direct = FakeLauncher(launcher_name="direct", group_path="/treehawk-1.scope")

    workload = FallbackLauncher([isolating, direct]).launch(["job"])

    assert not isolating.workloads
    assert direct.workloads == [workload]
    assert workload.notes == ["scope: unavailable"]


def test_a_launcher_that_fails_is_passed_over_with_its_error() -> None:
    broken = FakeLauncher(launcher_name="scope", failure="no user bus")
    direct = FakeLauncher(launcher_name="direct", group_path="/treehawk-1.scope")

    workload = FallbackLauncher([broken, direct]).launch(["job"])

    assert workload.notes == ["scope: no user bus"]


def test_a_workload_with_no_boundary_says_so() -> None:
    workload = FallbackLauncher([FakeLauncher()]).launch(["job"])

    assert len(workload.notes) == 1
    assert "no kernel accounting boundary" in workload.notes[0]


def test_notes_do_not_run_together_across_launches() -> None:
    """The notes belong to each workload, not to the launcher that started it."""
    launcher = FallbackLauncher([FakeLauncher(launcher_name="scope", is_available=False), FakeLauncher()])

    launcher.launch(["first"])
    second = launcher.launch(["second"])

    assert len(second.notes) == 2


def test_nothing_to_launch_with_is_an_error() -> None:
    with pytest.raises(LaunchFailed, match="scope: unavailable"):
        FallbackLauncher([FakeLauncher(launcher_name="scope", is_available=False)]).launch(["job"])


def test_a_started_workload_is_given_a_run_id_of_its_own() -> None:
    started = start_process(PRINT_RUN_ID, output=WorkloadOutput.CAPTURE)
    assert started.stream is not None
    with started.stream as stream:
        printed = stream.readline().decode().strip()
    started.process.wait(timeout=10)

    name, _, value = started.marker.partition("=")
    assert name == POSIX.run_id_variable
    assert printed == value
    assert len(value) == 32  # a uuid, so two runs cannot share one


def test_every_run_draws_a_fresh_run_id() -> None:
    first = DirectLauncher().launch([sys.executable, "-c", "pass"])
    second = DirectLauncher().launch([sys.executable, "-c", "pass"])
    for workload in (first, second):
        while workload.poll() is None:
            pass

    assert first.marker is not None
    assert second.marker is not None
    assert first.marker != second.marker
