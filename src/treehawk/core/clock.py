"""Time, behind an interface, so the sampling loop can be driven in tests."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone


class SystemClock:
    """Real time. Sleeps against absolute deadlines so error cannot accumulate."""

    def monotonic(self) -> float:
        return time.monotonic()

    def continuous(self) -> float:
        return continuous_time()

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def sleep_until(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


def continuous_time() -> float:
    """Monotonic seconds that keep counting while the machine is asleep.

    :func:`time.monotonic` stops during system sleep on both macOS
    (``mach_absolute_time``) and Linux (``CLOCK_MONOTONIC``), so a run that
    spans a closed lid would report hours less than the wall clock shows.
    macOS's ``CLOCK_MONOTONIC`` and Linux's ``CLOCK_BOOTTIME`` count the sleep.
    """
    if sys.platform == "linux":
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    if sys.platform == "darwin":
        return time.clock_gettime(time.CLOCK_MONOTONIC)
    return time.monotonic()
