"""Reading values back out of records.

A record is a plain dictionary that has usually been through JSON, where any
field may legitimately be null and nothing guarantees a number is a number.
These coercions are the one place that fact is dealt with, so every renderer
does not grow its own private copy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeVar

from treehawk.core.compat import TypeIs

T = TypeVar("T", bound=float)


def as_float(value: object) -> float | None:
    """The value as a float, or ``None`` if it is not a number."""
    return float(value) if isinstance(value, (int, float)) else None


def as_int(value: object) -> int | None:
    """The value as an int, or ``None`` if it is not a number."""
    return int(value) if isinstance(value, (int, float)) else None


def is_record(value: object) -> TypeIs[dict[str, object]]:
    """Whether the value is a record.

    Only the container is checked, not its keys: records come from JSON or
    from treehawk itself, and neither produces a key that is not a string.
    Checking would mean copying every record just to prove it.
    """
    return isinstance(value, dict)


def as_record(value: object) -> dict[str, object] | None:
    """The value itself if it is a record, or ``None`` if it is not."""
    return value if is_record(value) else None


def as_mapping(value: object) -> Mapping[str, object]:
    """The value as a record-like mapping, or an empty one if it is not."""
    return value if is_record(value) else {}


def as_records(value: object) -> list[dict[str, object]]:
    """The value as a list of records, skipping anything that is not one."""
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if is_record(item)]


def as_sequence(value: object) -> Sequence[object]:
    """The value as a sequence, or an empty one if it is not a list or tuple."""
    return value if isinstance(value, (list, tuple)) else ()


def peak_of(*, current: T | None, candidate: T | None) -> T | None:
    """The larger of the two, where ``None`` means "nothing measured yet"."""
    if candidate is None:
        return current
    return candidate if current is None else max(current, candidate)
