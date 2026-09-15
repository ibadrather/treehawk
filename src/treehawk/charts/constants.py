"""Named values for the PDF report: page geometry, mark sizes and row limits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ChartConstants:
    """Sizes and limits shared by the page writer, the pages and the styling."""

    page_size: tuple[float, float] = (11.69, 8.27)
    """A4 landscape - charts are wider than they are tall."""

    line_width: float = 2.0

    surface_gap: float = 1.5
    """Gap drawn between stacked bands, in the surface colour, so adjacent fills
    read as separate shapes rather than one blob."""

    footer_band: float = 0.05
    """Anything a page has already drawn below this is its own footer, and the
    writer must not draw a second one over it."""

    top_series: int = 6
    """Processes charted individually before the rest folds into "other".

    The categorical palette is six slots deep and is never cycled, so a seventh
    series would have to repeat a colour - "other" is the honest alternative.
    """

    max_gantt_rows: int = 34
    """Lifetime bars that fit legibly on one page before the rest is summarised."""

    ranking_rows: int = 10
    """Processes in each league table on the "biggest consumers" page."""


CHARTS: Final = ChartConstants()
