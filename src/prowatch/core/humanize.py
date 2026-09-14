"""Formatting numbers for people, shared by every renderer.

Named ``format_*`` because that is all they do: they take a measurement and
return text. ``None`` always renders as ``-``, so "we could not measure it"
never reads as zero.
"""

from __future__ import annotations

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def format_bytes(value: float | None, *, precision: int = 1) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in _UNITS:
        if abs(size) < 1024.0 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.{precision}f}{unit}"
        size /= 1024.0
    return f"{size:.{precision}f}{_UNITS[-1]}"


def format_percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def format_seconds(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 60:
        return f"{value:.2f}s"
    minutes, seconds = divmod(value, 60)
    if minutes < 60:
        return f"{int(minutes)}m{seconds:04.1f}s"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}h{minutes:02d}m{seconds:04.1f}s"


def truncate(text: str, *, width: int) -> str:
    text = text.strip()
    if width <= 1 or len(text) <= width:
        return text
    return text[: width - 1] + "…"
