"""What the command line assembles before a run starts."""

from __future__ import annotations

from dataclasses import dataclass

from treehawk.core.monitor import Monitor
from treehawk.core.tracker import Tracker
from treehawk.platforms.models import Platform


@dataclass(frozen=True, slots=True)
class Session:
    """A wired-up run, ready to start."""

    monitor: Monitor
    tracker: Tracker
    platform: Platform
