"""Value types for the presentation layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Palette:
    """Colours by the job they do, not by their name."""

    surface: str
    text_primary: str
    text_secondary: str
    text_muted: str
    grid: str
    series: tuple[str, ...]
    other: str
    good: str
    warning: str
    critical: str

    def slot(self, index: int) -> str:
        """Colour for categorical slot ``index``; past the end, the 'other' grey."""
        if 0 <= index < len(self.series):
            return self.series[index]
        return self.other
