"""Coalitions: the closest thing macOS has to a cgroup.

A coalition is the boundary a fork cannot leave by accident. A process that
daemonizes - fork, ``setsid``, fork again, original parent exits - is
re-parented to launchd and has left its session, but it is *still in the
coalition it started in*. That is precisely the property the orphan rule needs,
and it is why membership tracking works on macOS for the same reason it works
on Linux.

What a coalition cannot do is account. macOS exposes no CPU or memory total for
one without entitlements, so :meth:`metrics` returns ``None`` and a log written
on macOS carries a null ``group_memory_bytes`` rather than a number treehawk
invented. Membership alone is still worth having: it is what the orphan and
cgroup rules follow.

Note that everything started from one terminal shares a coalition, so a
workload's coalition is usually treehawk's own. The group rule already guards
against that - it refuses a boundary treehawk is inside, because such a
boundary is broader than the workload - so the coalition informs the orphan
rule without dragging in the whole terminal.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Final

from treehawk.core.models import GroupMetrics
from treehawk.platforms.darwin.libproc import LibProc, ProcessTable, coalition_id_of

MEMBERSHIP_TTL: Final = 0.25
"""Seconds a membership scan is reused for.

Unlike a cgroup, whose members are one file read away, a coalition's are found
only by asking every process which coalition it is in. The group rule asks more
than once per sample - to decide whether a boundary is ours, then to adopt from
it - so the scan is held briefly rather than repeated. Shorter than any usable
sampling interval, so no sample ever sees a membership from a previous one.
"""


class CoalitionSource:
    """Implements ``GroupMetricSource`` for macOS coalitions."""

    def __init__(
        self,
        table: ProcessTable | None = None,
        *,
        ttl: float = MEMBERSHIP_TTL,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._table: ProcessTable = table or LibProc()
        self._ttl = ttl
        self._monotonic = monotonic
        self._members: Mapping[int, set[int]] = {}
        self._read_at: float | None = None

    def available(self) -> bool:
        """True when the kernel will tell us any process' coalition at all."""
        return bool(self._membership())

    def pids_in(self, path: str) -> set[int] | None:
        identifier = coalition_id_of(path)
        if identifier is None:
            return None
        members = self._membership().get(identifier)
        return set(members) if members else None

    def metrics(self, path: str) -> GroupMetrics | None:
        """The boundary, with no readings - which is the honest answer here.

        macOS keeps no CPU or memory total for a coalition that an
        unentitled process may read, so every figure stays ``None`` and the
        log's ``group_memory_bytes`` is null rather than invented. The
        membership half of this source is the useful half.
        """
        return GroupMetrics(path=path)

    def _membership(self) -> Mapping[int, set[int]]:
        """Coalition id -> the PIDs in it, refreshed at most every ``ttl``."""
        now = self._monotonic()
        if self._read_at is not None and now - self._read_at < self._ttl:
            return self._members
        found: dict[int, set[int]] = {}
        for pid in self._table.list_pids():
            identifier = self._table.coalition_id(pid)
            if identifier is not None:
                found.setdefault(identifier, set()).add(pid)
        self._members = found
        self._read_at = now
        return found
