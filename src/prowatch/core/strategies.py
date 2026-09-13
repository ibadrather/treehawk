"""Rules for deciding which *other* processes belong to the workload.

A process that daemonizes (fork, ``setsid``, fork again, parent exits) is
re-parented to PID 1, so a parent-child walk alone loses it. Each strategy here
catches a different escape route; the tracker applies them together and keeps
whatever any of them finds.
"""

from __future__ import annotations

from typing import Callable, Iterable, Mapping

from prowatch.core.config import ExpansionName
from prowatch.core.interfaces import ExpansionContext, ExpansionStrategy
from prowatch.core.models import ProcInfo


class TreeExpansion:
    """Adopt the transitive children of tracked processes."""

    @property
    def name(self) -> str:
        return "tree"

    def expand(self, ctx: ExpansionContext) -> Mapping[int, str]:
        found: dict[int, str] = {}
        queue = list(ctx.tracked_pids)
        seen = set(ctx.tracked_pids)
        while queue:
            for child in ctx.children.get(queue.pop(), ()):
                if child in seen:
                    continue
                seen.add(child)
                found[child] = self.name
                queue.append(child)
        return found


class GroupExpansion:
    """Adopt everything inside a control group owned by the workload.

    A cgroup is the one boundary a daemonized child cannot escape by accident:
    ``fork``/``setsid`` change the parent and the session, never the cgroup.

    The risk is adopting a boundary that is *not* ours - a login session slice
    would drag in every unrelated process in the terminal. So a candidate is
    accepted only when it passes both guards:

    * prowatch itself must not live in it (or under it), and
    * every process currently in it must already be tracked.

    Once accepted, the boundary is remembered, so processes that appear in it
    later are adopted immediately. A boundary created by prowatch itself
    (``pinned_group``) skips the guards - ownership is not in question.
    """

    def __init__(self) -> None:
        self._accepted: set[str] = set()
        self._rejected: set[str] = set()

    @property
    def name(self) -> str:
        return "cgroup"

    @property
    def accepted(self) -> set[str]:
        return set(self._accepted)

    def expand(self, ctx: ExpansionContext) -> Mapping[int, str]:
        if ctx.groups is None:
            return {}
        if ctx.pinned_group:
            self._accepted.add(ctx.pinned_group)

        for pid in ctx.tracked_pids:
            path = ctx.group_of(pid)
            if path and path not in self._accepted and path not in self._rejected:
                if self._is_ours(ctx, path):
                    self._accepted.add(path)
                else:
                    self._rejected.add(path)

        found: dict[int, str] = {}
        for path in list(self._accepted):
            for pid in ctx.groups.pids_in(path) or ():
                if pid not in ctx.tracked_pids and pid != ctx.self_pid:
                    found[pid] = self.name
        return found

    def _is_ours(self, ctx: ExpansionContext, path: str) -> bool:
        if ctx.self_group and (
            ctx.self_group == path or ctx.self_group.startswith(path.rstrip("/") + "/")
        ):
            return False  # we are inside it, so it is broader than the workload
        members = ctx.groups.pids_in(path) if ctx.groups else None
        if not members:
            return False
        return all(pid in ctx.tracked_pids for pid in members)


class SessionExpansion:
    """Adopt processes sharing a session led by a tracked process.

    Catches children that were re-parented away but kept the session, and (once
    a session is accepted) ones started after the leader has already exited.
    """

    def __init__(self) -> None:
        self._accepted: set[int] = set()

    @property
    def name(self) -> str:
        return "session"

    def expand(self, ctx: ExpansionContext) -> Mapping[int, str]:
        for pid in ctx.tracked_pids:
            info = ctx.procs.get(pid)
            if info is None or info.sid == ctx.self_sid or info.sid <= 0:
                continue
            if info.sid == info.pid:  # a session leader we are tracking
                self._accepted.add(info.sid)

        if not self._accepted:
            return {}
        found: dict[int, str] = {}
        for pid, info in ctx.procs.items():
            if (
                info.sid in self._accepted
                and pid not in ctx.tracked_pids
                and pid != ctx.self_pid
            ):
                found[pid] = self.name
        return found


class OrphanExpansion:
    """Adopt newly orphaned processes that share a tracked process' cgroup.

    This is the one rule aimed squarely at the gap that polling leaves open. A
    process that daemonizes does so in microseconds - fork, ``setsid``, fork,
    parent exits - so between two samples it can lose its parent *and* its
    session before we ever observe it as a descendant. What it cannot shed by
    forking is its cgroup.

    So: a process that (a) appeared since the last sample, (b) has been
    re-parented, and (c) sits in the same cgroup as something we already track,
    is taken to be a detached child of the workload.

    Recognising (b) is the subtle part. "Parent is PID 1" is not enough: systemd
    --user (and container init, and anything that sets PR_SET_CHILD_SUBREAPER)
    collects orphans itself, so a detached child usually reports a very much
    alive parent. What gives it away is that the adopting reaper lives in a
    *different* cgroup - whereas a process that was simply spawned normally
    always starts in its parent's. That single comparison separates a detached
    grandchild from an unrelated sibling started in the same terminal.
    """

    def __init__(self) -> None:
        # Remembered rather than recomputed each sample: a workload's last
        # process can exit in the very interval its detached child is born, and
        # the child still has to be recognised when it turns up a moment later.
        self._groups: set[str] = set()

    @property
    def name(self) -> str:
        return "orphan"

    def expand(self, ctx: ExpansionContext) -> Mapping[int, str]:
        self._groups |= {
            group for group in (ctx.group_of(pid) for pid in ctx.tracked_pids) if group
        }
        if not ctx.new_pids or not self._groups:
            return {}
        tracked_groups = self._groups

        found: dict[int, str] = {}
        for pid in ctx.new_pids:
            info = ctx.procs.get(pid)
            if info is None or pid in ctx.tracked_pids or pid == ctx.self_pid:
                continue
            group = ctx.group_of(pid)
            if group is None or group not in tracked_groups:
                continue
            if self._was_reparented(ctx, info, group):
                found[pid] = self.name
        return found

    @staticmethod
    def _was_reparented(
        ctx: ExpansionContext, info: ProcInfo, group: str
    ) -> bool:
        if info.ppid <= 1 or info.ppid not in ctx.procs:
            return True  # adopted by init, or the parent is already gone
        return ctx.group_of(info.ppid) != group


StrategyFactory = Callable[[], ExpansionStrategy]

STRATEGY_KINDS: dict[str, StrategyFactory] = {
    ExpansionName.TREE: TreeExpansion,
    ExpansionName.CGROUP: GroupExpansion,
    ExpansionName.SESSION: SessionExpansion,
    ExpansionName.ORPHAN: OrphanExpansion,
}


def build_strategies(names: Iterable[str]) -> list[ExpansionStrategy]:
    """Instantiate expansion strategies by name, preserving the given order."""
    built: list[ExpansionStrategy] = []
    for name in names:
        try:
            factory = STRATEGY_KINDS[name]
        except KeyError:
            raise ValueError(
                f"unknown expansion strategy {name!r}; "
                f"known: {', '.join(sorted(STRATEGY_KINDS))}"
            ) from None
        built.append(factory())
    return built
