"""Membership: which processes make up the workload, right now.

The tracker owns one idea - *stickiness*. A process is admitted once, by the
matcher or by any expansion strategy, and stays a member until that exact
identity disappears from the system. Nothing can evict it: not being
re-parented to PID 1, not leaving the session, not its parent exiting. That is
what makes detached children survivable.

It also keeps the books for members that exit, so their CPU time is not lost
from the running total between two samples.
"""

from __future__ import annotations

from collections.abc import Mapping

from treehawk.core.constants import CORE
from treehawk.core.interfaces import (
    BoundaryStrategy,
    ExpansionContext,
    ExpansionStrategy,
    GroupMetricSource,
    ProcessMatcher,
    ProcessSource,
)
from treehawk.core.models import Identity, ProcInfo, RefreshResult


class Tracker:
    """Maintains the workload's membership set across samples.

    The two exclusion sets are separate because they answer different
    questions. treehawk's own process and the shell/wrapper chain that started
    it routinely carry the search keyword in their command line - you typed it
    there - so they must never be *seeded*. An explicit ``--pid`` names its
    target outright, and then the seed exclusions do not apply at all; the
    expansion exclusions still do, because adopting our own shell would track
    the terminal rather than the work.
    """

    def __init__(
        self,
        *,
        matcher: ProcessMatcher,
        strategies: list[ExpansionStrategy],
        source: ProcessSource,
        groups: GroupMetricSource | None = None,
        self_pid: int = 0,
        self_group: str | None = None,
        self_sid: int = 0,
        pinned_group: str | None = None,
        exclude_from_seed: frozenset[int] = frozenset(),
        exclude_from_expansion: frozenset[int] = frozenset(),
    ) -> None:
        self._matcher = matcher
        self._strategies = strategies
        self._source = source
        self._groups = groups
        self._self_pid = self_pid
        self._self_group = self_group
        self._self_sid = self_sid
        self._pinned_group = pinned_group
        self._exclude_from_seed = frozenset(exclude_from_seed)
        self._exclude_from_expansion = frozenset(exclude_from_expansion) | {self_pid}

        self._members: dict[Identity, str] = {}
        self._seen_pids: set[int] = set()
        self._last_ticks: dict[Identity, int] = {}
        self._exited_ticks = 0
        self._seeded = False

    @property
    def pinned_group(self) -> str | None:
        return self._pinned_group

    @property
    def seeded(self) -> bool:
        return self._seeded

    @property
    def group_paths(self) -> set[str]:
        """Group boundaries accepted as belonging to the workload."""
        paths: set[str] = set()
        if self._pinned_group:
            paths.add(self._pinned_group)
        for strategy in self._strategies:
            if isinstance(strategy, BoundaryStrategy):
                paths |= strategy.accepted
        return paths

    def seed(self, procs: Mapping[int, ProcInfo]) -> list[ProcInfo]:
        """Admit every process the matcher selects. Returns what was admitted."""
        needs_cmdline = self._matcher.needs_cmdline
        admitted: list[ProcInfo] = []
        # Everything already running is the baseline: it cannot be a process the
        # workload spawned, so the "new since last sample" rules must not see it.
        self._seen_pids |= set(procs)
        for pid, info in procs.items():
            if pid in self._exclude_from_seed:
                continue
            cmdline = self._source.read_cmdline(pid) if needs_cmdline else ""
            if self._matcher.matches(info=info, cmdline=cmdline) and info.identity not in self._members:
                self._members[info.identity] = "match"
                admitted.append(info)
        if admitted:
            self._seeded = True
        return admitted

    def refresh(self, procs: Mapping[int, ProcInfo]) -> RefreshResult:
        """Prune dead members, then grow the set with every strategy."""
        result = RefreshResult()
        tracked_pids: set[int] = set()
        for identity, via in list(self._members.items()):
            pid, starttime = identity
            info = procs.get(pid)
            if info is None or info.starttime != starttime:
                # Gone. Fold its final CPU time into the running total so the
                # aggregate never goes backwards when a child exits.
                self._exited_ticks += self._last_ticks.pop(identity, 0)
                del self._members[identity]
                result.exited.append(identity)
                continue
            tracked_pids.add(pid)
            result.via[pid] = via

        # Processes that appeared since the previous sample. A detached child
        # is always one of these, which is what lets the orphan rule find it
        # without dragging in everything else on the machine. Only the previous
        # scan is remembered, not the whole history - treehawk is expected to
        # run for days, and a machine with process churn would otherwise grow
        # this set without limit.
        new_pids = {pid for pid in procs if pid not in self._seen_pids} if self._seen_pids else set()
        self._seen_pids = set(procs)

        # Expansion runs even with nothing currently alive: a workload whose
        # last visible process just exited may have left a detached child that
        # is about to appear.
        if tracked_pids or self._pinned_group or self._seeded:
            self._expand(
                procs=procs,
                tracked_pids=tracked_pids,
                new_pids=new_pids,
                result=result,
            )

        for pid in sorted(tracked_pids):
            info = procs[pid]
            self._last_ticks[info.identity] = info.cpu_ticks
            result.alive_cpu_ticks += info.cpu_ticks
            # A zombie owns no memory and accrues no more CPU. It is left out
            # of the reported set - and out of the "has the workload finished?"
            # question - while its final counters still reach the total.
            if info.is_zombie:
                result.zombies.append(info)
            else:
                result.alive.append(info)

        result.exited_cpu_ticks = self._exited_ticks
        return result

    def _expand(
        self,
        *,
        procs: Mapping[int, ProcInfo],
        tracked_pids: set[int],
        new_pids: set[int],
        result: RefreshResult,
    ) -> None:
        context = ExpansionContext(
            procs=procs,
            tracked_pids=tracked_pids,
            new_pids=new_pids,
            children=_children_map(procs),
            source=self._source,
            groups=self._groups,
            self_pid=self._self_pid,
            self_group=self._self_group,
            self_sid=self._self_sid,
            pinned_group=self._pinned_group,
        )
        for _ in range(CORE.max_expansion_rounds):
            grew = False
            for strategy in self._strategies:
                for pid, reason in strategy.expand(context).items():
                    if pid in tracked_pids or pid in self._exclude_from_expansion:
                        continue
                    info = procs.get(pid)
                    if info is None:
                        continue
                    tracked_pids.add(pid)
                    self._members[info.identity] = reason
                    result.via[pid] = reason
                    result.admitted.append(info.identity)
                    grew = True
            if not grew:
                return


def _children_map(procs: Mapping[int, ProcInfo]) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for pid, info in procs.items():
        children.setdefault(info.ppid, []).append(pid)
    return children
