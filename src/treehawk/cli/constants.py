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

    top_panel: str = "Top"
    """The help panel for the options that shape whole-machine tracking."""

    default_top_dir: str = "treehawk-top"
    """Where ``top`` writes when run by hand without ``--dir``: the current directory."""

    service_name: str = "treehawk"
    unit_dir: str = "/etc/systemd/system"
    service_state_dir: str = "/var/lib/treehawk"
    """What systemd's ``StateDirectory=treehawk`` creates, and where the service logs."""

    service_restart_seconds: int = 5
    service_stop_timeout: int = 30
    """Seconds systemd waits for the final summary and the last compression."""

    service_nice: int = 10
    """Below ordinary work: the monitor must not be what makes a machine slow."""


CLI: Final = CliConstants()
