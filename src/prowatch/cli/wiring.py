"""Composition: turning settings into wired-up objects.

Kept apart from argument parsing so that the two things that change for
different reasons - what the flags are, and what the objects are - change in
different files. The monitor only ever sees interfaces.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..core.aggregate import Aggregator
from ..core.clock import SystemClock
from ..core.config import WatchConfig
from ..core.interfaces import ProcessMatcher, ProcessSource, Sink
from ..core.monitor import Monitor
from ..core.strategies import build_strategies
from ..core.tracker import Tracker
from ..gpu import build_collectors
from ..platforms.registry import Platform


@dataclass(frozen=True, slots=True)
class Session:
    """A wired-up run, ready to start."""

    monitor: Monitor
    tracker: Tracker
    platform: Platform


def assemble(
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
    source = platform.processes
    self_pid = os.getpid()
    # prowatch's own wrapper chain (shell, uv, timeout, ...) is never part of
    # the workload: it carries the keyword because the user typed it there, and
    # adopting it would drag in the whole terminal. An explicit --pid may still
    # name one of them as the seed.
    by_pid = matcher.describe().get("kind") == "pid"
    tracker = Tracker(
        matcher=matcher,
        strategies=build_strategies(config.expand),
        source=source,
        groups=platform.groups,
        self_pid=self_pid,
        self_group=source.read_group_path(self_pid),
        self_sid=_session_id(source, self_pid),
        pinned_group=pinned_group,
        exclude_pids={self_pid} | ancestors_of(source, self_pid),
        seed_excluded=by_pid,
    )
    monitor = Monitor(
        source=source,
        tracker=tracker,
        aggregator=Aggregator(host, prefer_group_cpu=pinned_group is not None),
        sink=sink,
        clock=SystemClock(),
        config=config,
        host=host,
        groups=platform.groups,
        collectors=build_collectors(config.collectors),
        mode=mode,
        matcher=matcher.describe(),
        argv=argv,
        notes=notes,
    )
    return Session(monitor=monitor, tracker=tracker, platform=platform)


def ancestors_of(source: ProcessSource, pid: int, *, limit: int = 64) -> set[int]:
    """Every process between ``pid`` and PID 1."""
    found: set[int] = set()
    current = pid
    for _ in range(limit):
        info = source.read_info(current)
        if info is None or info.ppid <= 0 or info.ppid in found:
            break
        found.add(info.ppid)
        current = info.ppid
    return found


def _session_id(source: ProcessSource, pid: int) -> int:
    info = source.read_info(pid)
    return info.sid if info is not None else 0
