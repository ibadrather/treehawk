"""Named values for the command line."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class CliConstants:
    """Timeouts, limits and labels used while parsing and wiring a run."""

    terminate_grace: float = 5.0
    """Seconds a workload is given to exit on SIGTERM before it is killed."""

    stop_poll_interval: float = 0.05
    """Seconds between checks on a workload that has been asked to exit."""

    ancestor_limit: int = 64
    """How far up the parent chain to walk before giving up. A process tree that
    deep is a loop we have failed to detect, not a real wrapper chain."""

    advanced_panel: str = "Advanced"
    """The help panel for options that exist only for an awkward situation."""


CLI: Final = CliConstants()
