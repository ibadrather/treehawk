"""Time, behind an interface, so the sampling loop can be driven in tests."""

from __future__ import annotations

import time
from datetime import datetime, timezone


class SystemClock:
    """Real time. Sleeps against absolute deadlines so error cannot accumulate."""

    def monotonic(self) -> float:
        return time.monotonic()

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def sleep_until(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
