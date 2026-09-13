"""Small rendering helpers used by more than one view."""

from __future__ import annotations

from typing import Final, Sequence

BLOCKS: Final = "▁▂▃▄▅▆▇█"


def sparkline(values: Sequence[float | None], *, width: int = 24) -> str:
    """A one-line history of ``values``, scaled to its own maximum.

    Gaps (``None``) render as blanks rather than zeroes, so "we could not
    measure" never looks like "it was idle".
    """
    recent = list(values)[-width:]
    if not recent:
        return ""
    present = [value for value in recent if value is not None]
    if not present:
        return " " * len(recent)
    top = max(present)
    if top <= 0:
        return BLOCKS[0] * len(recent)
    scale = len(BLOCKS) - 1
    return "".join(
        " " if value is None else BLOCKS[min(scale, int(value / top * scale))]
        for value in recent
    )


def elapsed_clock(seconds: float) -> str:
    """``h:mm:ss`` - the format a long-running watch is actually read in."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"
