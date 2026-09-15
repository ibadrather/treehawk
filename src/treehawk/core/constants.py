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


CORE: Final = CoreConstants()
