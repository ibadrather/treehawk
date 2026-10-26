"""The real clock."""

from __future__ import annotations

import time

from treehawk.core.clock import SystemClock, continuous_time


def test_the_continuous_clock_never_runs_behind_the_awake_one() -> None:
    """It is the awake clock plus the time spent asleep, so it can only be ahead."""
    awake = time.monotonic()
    assert continuous_time() >= awake


def test_the_continuous_clock_moves_forward() -> None:
    clock = SystemClock()
    first = clock.continuous()
    time.sleep(0.01)
    assert clock.continuous() > first
