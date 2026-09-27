"""Turning the text people type into values: sizes, spans of time, moments."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Final

from treehawk.core.config import Pacing
from treehawk.core.errors import ConfigError

_SIZE: Final = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?\s*$", re.IGNORECASE)
_SIZE_UNITS: Final = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}

_SPAN: Final = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_SPAN_UNITS: Final = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def parse_interval(text: str) -> tuple[Pacing, float | None]:
    """``"auto"`` or a number of seconds (``"0.5"``, ``"250ms"`` is not accepted)."""
    if text.strip().lower() == str(Pacing.AUTO):
        return Pacing.AUTO, None
    try:
        seconds = float(text)
    except ValueError:
        raise ConfigError(f"interval must be seconds or 'auto', not {text!r}") from None
    return Pacing.FIXED, seconds


def parse_size(text: str) -> int:
    """``"500M"``, ``"1G"``, ``"1GiB"``, ``"2048"`` -> bytes (binary units)."""
    match = _SIZE.match(text)
    if match is None:
        raise ConfigError(f"not a size: {text!r}; try 500M or 2G")
    return int(float(match.group(1)) * _SIZE_UNITS[match.group(2).lower()])


def parse_span(text: str) -> float:
    """``"90"``, ``"30m"``, ``"1h"``, ``"2d"`` -> seconds."""
    match = _SPAN.match(text)
    if match is None:
        raise ConfigError(f"not a span of time: {text!r}; try 30m, 1h or 2d")
    return float(match.group(1)) * _SPAN_UNITS[match.group(2).lower()]


def parse_moment(text: str, *, now: float) -> float:
    """A point in time: ISO 8601 (local if no zone is given), or a span ago (``"2h"``)."""
    try:
        return now - parse_span(text)
    except ConfigError:
        pass
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        raise ConfigError(f"not a time: {text!r}; try '2026-09-27 14:00' or 2h (ago)") from None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.timestamp()
