"""What the command line assembles before a run starts."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from treehawk.core.clock import SystemClock
from treehawk.core.interfaces import Clock
from treehawk.core.monitor import Monitor
from treehawk.core.tracker import Tracker
from treehawk.platforms.models import Platform
from treehawk.platforms.registry import get_platform


@dataclass(frozen=True, slots=True)
class Session:
    """A wired-up run, ready to start."""

    monitor: Monitor
    tracker: Tracker
    platform: Platform


@dataclass(frozen=True, slots=True)
class Runtime:
    """What the commands take from the machine they run on.

    The seam at the very top of the program: commands ask this for a platform,
    a clock and their own PID rather than reaching for them, so a test can hand
    the whole command line a fake machine through ``CliRunner.invoke(obj=...)``.
    The platform is a factory, so importing the CLI builds nothing.
    """

    platform: Callable[[], Platform] = get_platform
    clock: Clock = field(default_factory=SystemClock)
    self_pid: Callable[[], int] = os.getpid
