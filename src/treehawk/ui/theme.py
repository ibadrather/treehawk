"""One palette, shared by the terminal dashboard and the PDF report.

Both renderers draw from the same named roles, so a series keeps its colour
whether you are watching it live or reading it back a week later.

The hues are the validated reference set. Two things about them are load-bearing
rather than decorative:

* **Order is fixed.** Categorical slots are assigned in order and never cycled;
  a seventh series folds into "other" rather than inventing a hue.
* **Dark is selected, not flipped.** The terminal steps are the same hues chosen
  against a dark surface, not the light values lightened.

Checked with the palette validator: worst adjacent CVD separation 9.1 (light) /
8.4 (dark) and worst adjacent normal-vision separation 19.6 / 19.8, both clear of
the floors. Three light slots fall under 3:1 contrast on paper, so every chart
that uses them ships direct labels or an accompanying table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Mapping


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


PRINT: Final = Palette(
    surface="#fcfcfb",
    text_primary="#0b0b0b",
    text_secondary="#52514e",
    text_muted="#8a8981",
    grid="#e3e2dd",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7"),
    other="#b5b4ac",
    good="#008300",
    warning="#eda100",
    critical="#e34948",
)
"""Stepped for a white page: the PDF report."""

TERMINAL: Final = Palette(
    surface="#1a1a19",
    text_primary="#ffffff",
    text_secondary="#c3c2b7",
    text_muted="#8a8981",
    grid="#3a3a37",
    series=("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9"),
    other="#8a8981",
    good="#008300",
    warning="#c98500",
    critical="#e66767",
)
"""Stepped for a dark terminal: the live dashboard."""

PALETTE: Final = TERMINAL

DISCOVERY_GROUPS: Final[Mapping[str, str]] = {
    "match": "matched",
    "seed": "matched",
    "tree": "child",
    "cgroup": "detached",
    "session": "detached",
    "orphan": "detached",
}
"""How each membership rule is presented.

The rule names are an implementation detail; what a reader wants to know is
whether treehawk was *told* about a process, merely followed a parent link to
it, or had to recognise it after it detached. Three groups also keeps the charts
inside the three-slot palette that validates across every pair of colours.
"""

DISCOVERY_ORDER: Final = ("matched", "child", "detached")


def discovery_group(via: str) -> str:
    """Present a membership rule as one of :data:`DISCOVERY_ORDER`."""
    return DISCOVERY_GROUPS.get(via, "detached")


def discovery_color(*, via: str, palette: Palette) -> str:
    """Stable colour for a membership rule, by its presentation group."""
    return palette.slot(DISCOVERY_ORDER.index(discovery_group(via)))
