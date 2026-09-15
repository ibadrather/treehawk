"""Starting a workload: the fallback chain, and what it tells the log."""

from __future__ import annotations

import pytest
from conftest import FakeLauncher

from treehawk.core.errors import LaunchFailed
from treehawk.platforms.posix import FallbackLauncher


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
