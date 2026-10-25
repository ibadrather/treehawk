"""Coalitions: the closest thing macOS has to a cgroup - and not very close.

A coalition is a boundary a fork cannot leave by accident. A process that
daemonizes - fork, ``setsid``, fork again, original parent exits - is
re-parented to launchd and has left its session, but it is *still in the
coalition it started in*.

What a coalition is not is the workload. It is the whole application: the
terminal, its shells, treehawk, the workload, and every helper and XPC service
launchd starts on the app's behalf. So membership of it is necessary for being
a detached child and nowhere near sufficient, and both rules that read it treat
it that way. The group rule refuses a boundary treehawk is inside, because such
a boundary is broader than the workload. The orphan rule, in that same case,
asks for the run id the workload's descendants inherit (see
:meth:`treehawk.core.interfaces.ExpansionContext.carries_marker`) rather than
taking "new, parented to PID 1, same coalition" as proof - which describes
every one of those helpers.

What a coalition cannot do at all is account. macOS exposes no CPU or memory
total for one without entitlements, so :meth:`metrics` returns no readings and
a log written on macOS carries a null ``group_memory_bytes`` rather than a
number treehawk invented.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from treehawk.core.models import GroupMetrics
from treehawk.platforms.darwin.constants import DARWIN
from treehawk.platforms.darwin.libproc import ProcessTable, coalition_id_of


class CoalitionSource:
    """Implements ``GroupMetricSource`` for macOS coalitions."""

    def __init__(
        self,
        table: ProcessTable,
        *,
        ttl: float = DARWIN.membership_ttl,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._table = table
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
