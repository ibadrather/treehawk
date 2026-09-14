"""Reading values back out of records.

A record is a plain dictionary that has usually been through JSON, where any
field may legitimately be null and nothing guarantees a number is a number.
These coercions are the one place that fact is dealt with, so every renderer
does not grow its own private copy.
"""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T", int, float)


def as_float(value: object) -> float | None:
    """The value as a float, or ``None`` if it is not a number."""
    return float(value) if isinstance(value, (int, float)) else None


def as_int(value: object) -> int | None:
    """The value as an int, or ``None`` if it is not a number."""
    return int(value) if isinstance(value, (int, float)) else None


def peak_of(*, current: T | None, candidate: T | None) -> T | None:
    """The larger of the two, where ``None`` means "nothing measured yet"."""
    if candidate is None:
        return current
    return candidate if current is None else max(current, candidate)
