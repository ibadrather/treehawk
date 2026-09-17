"""Named values for the terminal views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class UiConstants:
    """Sizes and glyphs shared by the dashboard and its widgets."""

    history: int = 60
    """Samples kept for the sparklines, and the width of the column that shows
    them. Bounded: a watch may run for days."""

    sparkline_blocks: str = "▁▂▃▄▅▆▇█"
    """Sparkline glyphs, lowest to highest."""

    output_lines: int = 8
    """Lines of the workload's own output kept on screen. Bounded so the live
    region stays a fixed height whatever the workload prints; the full stream
    is in the file beside the log."""


UI: Final = UiConstants()
