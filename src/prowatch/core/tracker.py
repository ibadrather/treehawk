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

from dataclasses import dataclass, field
from typing import Mapping

from .interfaces import ExpansionContext
from .models import Identity, ProcInfo

MAX_EXPANSION_ROUNDS = 8
"""Strategies feed each other (a cgroup adoption reveals a new subtree), so
expansion repeats until it stabilises - bounded, to keep one sample bounded."""


ZOMBIE = "Z"
"""An exited process still holding a slot until its parent reaps it. It owns no
memory and accrues no more CPU, so it is excluded from the reported set - and
from the "has the workload finished?" question - while its final CPU counters
are still carried into the total."""


@dataclass(slots=True)
class RefreshResult:
    """Outcome of one membership pass."""

    alive: list[ProcInfo] = field(default_factory=list)
    zombies: list[ProcInfo] = field(default_factory=list)
    via: dict[int, str] = field(default_factory=dict)
    admitted: list[Identity] = field(default_factory=list)
    exited: list[Identity] = field(default_factory=list)
    alive_cpu_ticks: int = 0
    exited_cpu_ticks: int = 0

    @property
    def total_cpu_ticks(self) -> int:
        return self.alive_cpu_ticks + self.exited_cpu_ticks


class Tracker:
    """Maintains the workload's membership set across samples."""

    def __init__(
        self,
        *,
        matcher,
        strategies: list,
        source,
        groups=None,
        self_pid: int = 0,
        self_group: str | None = None,
        self_sid: int = 0,
        pinned_group: str | None = None,
        rescan: bool = False,
        exclude_pids: set[int] | None = None,
        seed_excluded: bool = False,
    ) -> None:
        self._matcher = matcher
        self._strategies = strategies
        self._source = source
        self._groups = groups
        self._self_pid = self_pid
        self._self_group = self_group
        self._self_sid = self_sid
        self._pinned_group = pinned_group
        self._rescan = rescan
        # prowatch's own process and the shell/wrapper chain that started it.
        # They routinely carry the search keyword in their command line (you
        # typed it), and adopting them would track the terminal, not the work.
        self._excluded = set(exclude_pids or ()) | {self_pid}
        # An explicit --pid names the process directly, so the exclusions (which
        # exist to stop a keyword matching the shell that typed it) should not
        # veto the seed. They still veto everything expansion tries to adopt.
        self._seed_excluded = seed_excluded

        self._members: dict[Identity, str] = {}
        self._seen_pids: set[int] = set()
        self._last_ticks: dict[Identity, int] = {}
        self._exited_ticks = 0
        self._seeded = False

    @property
    def pinned_group(self) -> str | None:
        return self._pinned_group

    @property
    def group_paths(self) -> set[str]:
        """Group boundaries accepted as belonging to the workload."""
        paths: set[str] = set()
        if self._pinned_group:
            paths.add(self._pinned_group)
        for strategy in self._strategies:
            paths |= getattr(strategy, "accepted", set())
        return paths

    def seed(self, procs: Mapping[int, ProcInfo]) -> list[ProcInfo]:
        """Admit every process the matcher selects. Returns what was admitted."""
        needs_cmdline = self._matcher.needs_cmdline
        admitted: list[ProcInfo] = []
        # Everything already running is the baseline: it cannot be a process the
        # workload spawned, so the "new since last sample" rules must not see it.
        self._seen_pids |= set(procs)
        for pid, info in procs.items():
            if pid in self._excluded and not self._seed_excluded:
                continue
            cmdline = self._source.read_cmdline(pid) if needs_cmdline else ""
            if self._matcher.matches(info, cmdline):
                if info.identity not in self._members:
                    self._members[info.identity] = "match"
                    admitted.append(info)
        if admitted:
            self._seeded = True
        return admitted

    @property
    def seeded(self) -> bool:
        return self._seeded

    def refresh(self, procs: Mapping[int, ProcInfo]) -> RefreshResult:
        """Prune dead members, then grow the set with every strategy."""
        result = RefreshResult()

        if self._rescan:
            result.admitted.extend(info.identity for info in self.seed(procs))

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
        # without dragging in everything else on the machine.
        new_pids = (
            {pid for pid in procs if pid not in self._seen_pids}
            if self._seen_pids
            else set()
        )
        self._seen_pids |= set(procs)

        # Expansion runs even with nothing currently alive: a workload whose
        # last visible process just exited may have left a detached child that
        # is about to appear.
        if tracked_pids or self._pinned_group or self._seeded:
            self._expand(procs, tracked_pids, new_pids, result)

        for pid in sorted(tracked_pids):
            info = procs[pid]
            self._last_ticks[info.identity] = info.cpu_ticks
            result.alive_cpu_ticks += info.cpu_ticks
            if info.state == ZOMBIE:
                result.zombies.append(info)
            else:
                result.alive.append(info)

        result.exited_cpu_ticks = self._exited_ticks
        return result

    def _expand(
        self,
        procs: Mapping[int, ProcInfo],
        tracked_pids: set[int],
        new_pids: set[int],
        result: RefreshResult,
    ) -> None:
        children = _children_map(procs)
        ctx = ExpansionContext(
            procs=procs,
            tracked_pids=tracked_pids,
            new_pids=new_pids,
            children=children,
            source=self._source,
            groups=self._groups,
            self_pid=self._self_pid,
            self_group=self._self_group,
            self_sid=self._self_sid,
            pinned_group=self._pinned_group,
        )
        for _ in range(MAX_EXPANSION_ROUNDS):
            grew = False
            for strategy in self._strategies:
                for pid, reason in strategy.expand(ctx).items():
                    if pid in tracked_pids or pid in self._excluded:
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
