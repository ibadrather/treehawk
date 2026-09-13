"""Membership: the part that decides what counts as "the workload".

These tests pin down the behaviours that are easy to get wrong and expensive to
get wrong quietly - a detached child silently dropped, or half the machine
silently adopted.
"""

from __future__ import annotations

import shutil

import pytest
from conftest import write_cgroup, write_proc

from prowatch.core.matchers import build_matcher
from prowatch.core.strategies import build_strategies
from prowatch.core.tracker import Tracker
from prowatch.platforms.linux.cgroup2 import CgroupV2Source
from prowatch.platforms.linux.source import LinuxProcessSource

WORKLOAD_CGROUP = "/user.slice/app.scope"
REAPER_CGROUP = "/user.slice"


class World:
    """A fake machine whose process table can be edited between samples."""

    def __init__(self, proc_root, cgroup_root):
        self.proc_root = proc_root
        self.cgroup_root = cgroup_root
        self.source = LinuxProcessSource(str(proc_root), page_size=4096)
        self.groups = CgroupV2Source(str(cgroup_root))

    def spawn(self, pid, **kwargs):
        kwargs.setdefault("cgroup", WORKLOAD_CGROUP)
        write_proc(self.proc_root, pid, starttime=1000 + pid, **kwargs)
        return pid

    def kill(self, pid):
        shutil.rmtree(self.proc_root / str(pid))

    def tracker(self, matcher, *, expand=("tree",), exclude=(), pinned=None, **kwargs):
        return Tracker(
            matcher=matcher,
            strategies=build_strategies(expand),
            source=self.source,
            groups=self.groups,
            self_pid=kwargs.pop("self_pid", 9),
            self_group=kwargs.pop("self_group", REAPER_CGROUP),
            self_sid=kwargs.pop("self_sid", 9),
            pinned_group=pinned,
            exclude_pids=set(exclude),
            **kwargs,
        )

    def refresh(self, tracker):
        return tracker.refresh(self.source.scan())

    def seed(self, tracker):
        return tracker.seed(self.source.scan())


@pytest.fixture
def world(proc_root, cgroup_root):
    return World(proc_root, cgroup_root)


# -- stickiness -----------------------------------------------------------

def test_a_child_stays_tracked_after_being_reparented(world):
    """The whole point: daemonizing must not lose the process."""
    world.spawn(100, cmdline="python train.py")
    world.spawn(200, ppid=100, cmdline="python worker.py")
    tracker = world.tracker(build_matcher("keyword", "train.py"))
    world.seed(tracker)
    assert {info.pid for info in world.refresh(tracker).alive} == {100, 200}

    # The child double-forks away: new parent (init), and the original exits.
    world.spawn(200, ppid=1, cmdline="python worker.py")
    world.kill(100)

    assert {info.pid for info in world.refresh(tracker).alive} == {200}


def test_pid_reuse_is_not_mistaken_for_the_original_process(world):
    world.spawn(100, cmdline="python train.py")
    tracker = world.tracker(build_matcher("keyword", "train.py"))
    world.seed(tracker)
    world.refresh(tracker)

    world.kill(100)
    write_proc(world.proc_root, 100, cmdline="unrelated", starttime=99999)

    result = world.refresh(tracker)
    assert result.alive == []
    assert result.exited == [(100, 1100)]


def test_an_ancestor_of_prowatch_is_never_adopted(world):
    """The shell that ran prowatch carries the keyword; it is not the workload."""
    world.spawn(50, comm="bash", cmdline="bash -c 'prowatch watch train.py'")
    world.spawn(100, ppid=50, cmdline="python train.py")
    tracker = world.tracker(build_matcher("keyword", "train.py"), exclude={50})
    world.seed(tracker)

    assert {info.pid for info in world.refresh(tracker).alive} == {100}


# -- cpu accounting -------------------------------------------------------

def test_cpu_of_an_exited_child_is_carried_forward(world):
    """Totals must not drop when a child exits between two samples."""
    world.spawn(100, cmdline="python train.py", utime=100, stime=20)
    world.spawn(200, ppid=100, utime=300, stime=0)
    tracker = world.tracker(build_matcher("keyword", "train.py"))
    world.seed(tracker)
    assert world.refresh(tracker).total_cpu_ticks == 420

    world.kill(200)
    after = world.refresh(tracker)

    assert after.exited_cpu_ticks == 300
    assert after.total_cpu_ticks == 420  # unchanged, not 120


def test_a_zombie_is_not_reported_alive_but_keeps_its_cpu(world):
    """An unreaped process holds a slot; it must not keep the run going."""
    world.spawn(100, cmdline="python train.py", state="Z", utime=50, stime=0)
    tracker = world.tracker(build_matcher("keyword", "train.py"))
    world.seed(tracker)

    result = world.refresh(tracker)
    assert result.alive == []
    assert [info.pid for info in result.zombies] == [100]
    assert result.total_cpu_ticks == 50


# -- expansion strategies -------------------------------------------------

def test_tree_expansion_follows_grandchildren(world):
    world.spawn(100, cmdline="python train.py")
    world.spawn(200, ppid=100)
    world.spawn(300, ppid=200)
    tracker = world.tracker(build_matcher("keyword", "train.py"))
    world.seed(tracker)

    assert {info.pid for info in world.refresh(tracker).alive} == {100, 200, 300}


def test_session_expansion_picks_up_a_process_that_left_the_tree(world):
    world.spawn(100, cmdline="python train.py", sid=100)
    world.spawn(200, ppid=1, sid=100)  # reparented, same session
    tracker = world.tracker(build_matcher("keyword", "train.py"), expand=("session",))
    world.seed(tracker)

    result = world.refresh(tracker)
    assert {info.pid for info in result.alive} == {100, 200}
    assert result.via[200] == "session"


def test_cgroup_expansion_remembers_a_boundary_the_workload_owns(world):
    """Once a group is recognised as ours, later arrivals in it are ours too."""
    scope = "/user.slice/train.scope"
    world.spawn(100, cmdline="python train.py", cgroup=scope)
    world.spawn(200, ppid=100, cgroup=scope)
    write_cgroup(world.cgroup_root, scope, pids=[100, 200])
    tracker = world.tracker(
        build_matcher("keyword", "train.py"), expand=("tree", "cgroup")
    )
    world.seed(tracker)
    # Everything in the group is accounted for, so the group is accepted.
    assert {info.pid for info in world.refresh(tracker).alive} == {100, 200}

    # A process that appears in it later needs no parent link to be adopted.
    world.spawn(300, ppid=1, cgroup=scope)
    write_cgroup(world.cgroup_root, scope, pids=[100, 200, 300])

    result = world.refresh(tracker)
    assert {info.pid for info in result.alive} == {100, 200, 300}
    assert result.via[300] == "cgroup"


def test_cgroup_expansion_refuses_a_group_with_untracked_members(world):
    """Conservative on first sight: an unexplained member means it is not ours."""
    scope = "/user.slice/train.scope"
    world.spawn(100, cmdline="python train.py", cgroup=scope)
    world.spawn(200, ppid=1, cgroup=scope)  # unrelated as far as we can tell
    write_cgroup(world.cgroup_root, scope, pids=[100, 200])
    tracker = world.tracker(build_matcher("keyword", "train.py"), expand=("cgroup",))
    world.seed(tracker)

    assert {info.pid for info in world.refresh(tracker).alive} == {100}


def test_cgroup_expansion_refuses_a_group_shared_with_strangers(world):
    """A login-session slice is not a workload boundary."""
    world.spawn(100, cmdline="python train.py")
    world.spawn(700, comm="unrelated", cmdline="firefox")
    write_cgroup(world.cgroup_root, WORKLOAD_CGROUP, pids=[100, 700])
    tracker = world.tracker(
        build_matcher("keyword", "train.py"), expand=("tree", "cgroup")
    )
    world.seed(tracker)

    assert {info.pid for info in world.refresh(tracker).alive} == {100}


def test_cgroup_expansion_refuses_a_group_that_contains_prowatch(world):
    """Even if we are the only other member: that group is our terminal."""
    world.spawn(100, cmdline="python train.py")
    write_cgroup(world.cgroup_root, WORKLOAD_CGROUP, pids=[100])
    tracker = world.tracker(
        build_matcher("keyword", "train.py"),
        expand=("cgroup",),
        self_group=WORKLOAD_CGROUP,
    )
    world.seed(tracker)

    assert {info.pid for info in world.refresh(tracker).alive} == {100}


# -- the orphan rule ------------------------------------------------------

def test_orphan_rule_adopts_a_detached_child_born_during_the_watch(world):
    """The case no other rule can see: no parent link, no shared session."""
    world.spawn(100, cmdline="python train.py", sid=100)
    world.spawn(9, comm="systemd", cgroup=REAPER_CGROUP)  # the subreaper
    tracker = world.tracker(
        build_matcher("keyword", "train.py"), expand=("tree", "orphan")
    )
    world.seed(tracker)
    world.refresh(tracker)

    # Daemonized: adopted by the reaper, own session, same cgroup.
    world.spawn(300, ppid=9, sid=300, cgroup=WORKLOAD_CGROUP)

    result = world.refresh(tracker)
    assert {info.pid for info in result.alive} == {100, 300}
    assert result.via[300] == "orphan"


def test_orphan_rule_ignores_a_sibling_spawned_normally(world):
    """Same cgroup, but its parent is right there in it - not our child."""
    world.spawn(100, cmdline="python train.py")
    world.spawn(50, comm="bash", cgroup=WORKLOAD_CGROUP)
    tracker = world.tracker(
        build_matcher("keyword", "train.py"), expand=("orphan",), exclude={50}
    )
    world.seed(tracker)
    world.refresh(tracker)

    world.spawn(400, ppid=50, cgroup=WORKLOAD_CGROUP)  # user starts something else

    assert {info.pid for info in world.refresh(tracker).alive} == {100}


def test_orphan_rule_ignores_processes_that_predate_the_watch(world):
    """Everything already running is the baseline, not a spawned child."""
    world.spawn(100, cmdline="python train.py")
    world.spawn(800, ppid=1, cgroup=WORKLOAD_CGROUP)  # an old daemon, same cgroup
    tracker = world.tracker(build_matcher("keyword", "train.py"), expand=("orphan",))
    world.seed(tracker)

    assert {info.pid for info in world.refresh(tracker).alive} == {100}


def test_orphan_rule_still_fires_when_the_last_parent_just_exited(world):
    """The narrow race: parent dies in the same interval the child appears."""
    world.spawn(100, cmdline="python train.py")
    world.spawn(9, comm="systemd", cgroup=REAPER_CGROUP)
    tracker = world.tracker(build_matcher("keyword", "train.py"), expand=("orphan",))
    world.seed(tracker)
    world.refresh(tracker)

    world.kill(100)
    world.spawn(300, ppid=9, cgroup=WORKLOAD_CGROUP)

    result = world.refresh(tracker)
    assert {info.pid for info in result.alive} == {300}


# -- seeding --------------------------------------------------------------

def test_exact_matcher_requires_the_whole_command_line(world):
    world.spawn(100, cmdline="python train.py --epochs 10")
    world.spawn(200, cmdline="python train.py")
    tracker = world.tracker(build_matcher("exact", "python train.py"))

    assert [info.pid for info in world.seed(tracker)] == [200]
