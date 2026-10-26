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


@dataclass(frozen=True, slots=True)
class Font:
    """One bundled font file, and the weight it draws."""

    family: str
    weight: int
    stem: str
    """File name without its extension: there is a ``.ttf`` and a ``.woff2``."""


@dataclass(frozen=True, slots=True)
class Brand:
    """The look of the docs site, which the HTML and PDF reports share."""

    primary: str
    primary_soft: str
    """The primary, lifted to read on a dark surface."""
    accent: str
    cream: str
    ink: str
    sans: str
    mono: str
    fonts: tuple[Font, ...]
