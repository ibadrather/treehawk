"""Composition: what ``build_session`` wires up from a platform and a matcher.

The rules that stop treehawk tracking the terminal it was started from live in
the wiring, so they are tested here against a fake machine rather than only end
to end.
"""

from __future__ import annotations

import pathlib

import pytest
from conftest import FakeClock, RecordingSink, fake_platform, write_cgroup, write_proc

from treehawk.cli.models import Session
from treehawk.cli.wiring import build_session
from treehawk.core.config import MemoryDetail, WatchConfig
from treehawk.core.interfaces import ProcessMatcher
from treehawk.core.matchers import build_matcher
from treehawk.platforms.models import Platform

SELF_PID = 9
SHELL_PID = 8
SCOPE = "/user.slice/treehawk-1.scope"


def session_on(
    platform: Platform,
    matcher: ProcessMatcher,
    *,
    sink: RecordingSink,
    pinned_group: str | None = None,
) -> Session:
    return build_session(
        platform=platform,
        config=WatchConfig(interval=1.0, max_samples=1, memory=MemoryDetail.RESIDENT),
        sink=sink,
        matcher=matcher,
        mode="watch",
        clock=FakeClock(),
        self_pid=SELF_PID,
        pinned_group=pinned_group,
    )


def start_from_a_shell(proc_root: pathlib.Path) -> None:
    """treehawk, started by a shell - and both mention the keyword."""
    write_proc(proc_root, SHELL_PID, comm="zsh", cmdline="zsh -c treehawk watch train.py")
    write_proc(proc_root, SELF_PID, ppid=SHELL_PID, sid=SHELL_PID, comm="treehawk", cmdline="treehawk watch train.py")


def test_a_keyword_never_seeds_from_treehawk_or_its_wrapper_chain(
    proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    start_from_a_shell(proc_root)
    write_proc(proc_root, 100, cmdline="python train.py")
    platform = fake_platform(proc_root, cgroup_root)
    session = session_on(platform, build_matcher(kind="keyword", value="train.py"), sink=RecordingSink())

    admitted = session.tracker.seed(platform.process_source.scan())

    assert [info.pid for info in admitted] == [100]


def test_an_explicit_pid_may_name_a_process_in_the_wrapper_chain(
    proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    start_from_a_shell(proc_root)
    platform = fake_platform(proc_root, cgroup_root)
    session = session_on(platform, build_matcher(kind="pid", value=SHELL_PID), sink=RecordingSink())

    admitted = session.tracker.seed(platform.process_source.scan())

    assert [info.pid for info in admitted] == [SHELL_PID]


def test_nothing_is_adopted_from_the_wrapper_chain(proc_root: pathlib.Path, cgroup_root: pathlib.Path) -> None:
    """Not even from a boundary treehawk created, if the shell ended up inside it."""
    start_from_a_shell(proc_root)
    write_proc(proc_root, 100, cmdline="python train.py", cgroup=SCOPE)
    write_cgroup(cgroup_root, SCOPE, pids=[100, SHELL_PID])
    platform = fake_platform(proc_root, cgroup_root)
    session = session_on(platform, build_matcher(kind="pid", value=100), sink=RecordingSink(), pinned_group=SCOPE)

    session.tracker.seed(platform.process_source.scan())
    refresh = session.tracker.refresh(platform.process_source.scan())

    assert [info.pid for info in refresh.alive] == [100]


def test_a_pinned_boundary_takes_cpu_from_the_kernel_counter(
    proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    write_proc(proc_root, 100, cmdline="python train.py", cgroup=SCOPE, utime=150)
    write_cgroup(cgroup_root, SCOPE, pids=[100], cpu_usec=2_500_000)
    sink = RecordingSink()

    session_on(
        fake_platform(proc_root, cgroup_root),
        build_matcher(kind="pid", value=100),
        sink=sink,
        pinned_group=SCOPE,
    ).monitor.run()

    assert sink.samples[0]["cpu_seconds_total"] == pytest.approx(2.5)


def test_without_a_boundary_cpu_is_summed_from_the_processes(
    proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    write_proc(proc_root, 100, cmdline="python train.py", cgroup=SCOPE, utime=150)
    write_cgroup(cgroup_root, SCOPE, pids=[100], cpu_usec=2_500_000)
    sink = RecordingSink()

    session_on(fake_platform(proc_root, cgroup_root), build_matcher(kind="pid", value=100), sink=sink).monitor.run()

    assert sink.samples[0]["cpu_seconds_total"] == pytest.approx(1.5)
