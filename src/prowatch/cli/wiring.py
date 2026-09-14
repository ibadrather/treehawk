"""Composition: turning settings into wired-up objects.

Kept apart from argument parsing so that the two things that change for
different reasons - what the flags are, and what the objects are - change in
different files. The monitor only ever sees interfaces.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from prowatch.core.aggregate import Aggregator
from prowatch.core.clock import SystemClock
from prowatch.core.config import CpuSource, WatchConfig
from prowatch.core.interfaces import ProcessMatcher, ProcessSource, Sink
from prowatch.core.monitor import Monitor
from prowatch.core.strategies import build_strategies
from prowatch.core.tracker import Tracker
from prowatch.gpu import build_collectors
from prowatch.platforms.registry import Platform

ANCESTOR_LIMIT = 64
"""How far up the parent chain to walk before giving up. A process tree that
deep is a loop we have failed to detect, not a real wrapper chain."""


@dataclass(frozen=True, slots=True)
class Session:
    """A wired-up run, ready to start."""

    monitor: Monitor
    tracker: Tracker
    platform: Platform


def build_session(
    *,
    platform: Platform,
    config: WatchConfig,
    sink: Sink,
    matcher: ProcessMatcher,
    mode: str,
    argv: list[str] | None = None,
    pinned_group: str | None = None,
    notes: tuple[str, ...] = (),
) -> Session:
    host = platform.host_info()
    source = platform.process_source
    self_pid = os.getpid()
    # prowatch's own wrapper chain (shell, uv, timeout, ...) is never part of
    # the workload: it carries the keyword because the user typed it there, and
    # adopting it would drag in the whole terminal. An explicit --pid names its
    # target outright, so it is allowed to seed from that chain - but nothing
    # may ever be *adopted* from it.
    unrelated = frozenset({self_pid}) | ancestors_of(source=source, pid=self_pid)
    tracker = Tracker(
        matcher=matcher,
        strategies=build_strategies(config.expand),
        source=source,
        groups=platform.group_source,
        self_pid=self_pid,
        self_group=source.read_group_path(self_pid),
        self_sid=session_id_of(source=source, pid=self_pid),
        pinned_group=pinned_group,
        exclude_from_seed=(
            frozenset() if matcher.names_one_process else unrelated
        ),
        exclude_from_expansion=unrelated,
    )
    monitor = Monitor(
        source=source,
        tracker=tracker,
        aggregator=Aggregator(
            host=host,
            cpu_source=(
                CpuSource.GROUP if pinned_group is not None else CpuSource.PROCESSES
            ),
        ),
        sink=sink,
        clock=SystemClock(),
        config=config,
        host=host,
        groups=platform.group_source,
        collectors=build_collectors(config.collectors),
        mode=mode,
        matcher=matcher.describe(),
        argv=argv,
        notes=notes,
    )
    return Session(monitor=monitor, tracker=tracker, platform=platform)


def ancestors_of(
    *, source: ProcessSource, pid: int, limit: int = ANCESTOR_LIMIT
) -> frozenset[int]:
    """Every process between ``pid`` and PID 1."""
    found: set[int] = set()
    current = pid
    for _ in range(limit):
        info = source.read_info(current)
        if info is None or info.ppid <= 0 or info.ppid in found:
            break
        found.add(info.ppid)
        current = info.ppid
    return frozenset(found)


def session_id_of(*, source: ProcessSource, pid: int) -> int:
    """The session ``pid`` belongs to, or 0 if it cannot be read."""
    info = source.read_info(pid)
    return info.sid if info is not None else 0
