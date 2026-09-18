"""Named values shared by every POSIX platform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class PosixConstants:
    """Defaults for the terminal treehawk hands a launched workload."""

    pty_columns: int = 120
    """Width given to a captured workload's pty when the real terminal's size
    cannot be read. Only reachable when there is no terminal at all, which is
    also when treehawk does not capture."""

    pty_rows: int = 24
    """Height for the same case. Nothing reads it back, but a pty left at 0x0
    makes a workload that asks its terminal's size draw nothing."""


POSIX: Final = PosixConstants()
