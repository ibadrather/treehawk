"""Named values for :mod:`treehawk.core`.

Kept in one frozen dataclass so each number has a name, a type and a reason
next to it, and a call site reads ``CORE.prune_keep`` rather than a bare 256.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class CoreConstants:
    """Limits and sentinels the platform-agnostic logic depends on."""

    zombie_state: str = "Z"
    """Process state for an exited process still awaiting its parent's ``wait()``."""

    process_table_limit: int = 4096
    """Distinct processes remembered for the summary before pruning kicks in."""

    prune_keep: int = 256
    """Contenders kept per ranking when pruning."""

    max_expansion_rounds: int = 8
    """Strategies feed each other (a cgroup adoption reveals a new subtree), so
    expansion repeats until it stabilises - bounded, to keep one sample bounded."""

    output_read_size: int = 65536
    """Bytes asked of the workload's output in one read."""

    output_line_limit: int = 16384
    """Characters a single output line may reach before it is emitted anyway.
    A workload that writes megabytes without a newline must not be able to grow
    treehawk's buffer without bound."""

    output_encoding: str = "utf-8"
    """How the workload's bytes are decoded. Undecodable bytes are replaced
    rather than raised: a mangled line is worth more than a lost run."""

    output_drain_grace: float = 2.0
    """Seconds the reader is given to finish draining after the workload exits."""


CORE: Final = CoreConstants()
