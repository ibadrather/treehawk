"""Writing the PDF.

The writer knows only that a page has a title and can draw itself. Adding a page
means appending to :data:`PAGES`; nothing here changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Sequence, cast

import matplotlib

matplotlib.use("Agg")  # no display needed, and none is available on a server

import matplotlib.pyplot as plt  # noqa: E402 - must follow the backend choice
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from prowatch import __version__  # noqa: E402
from prowatch.charts import style  # noqa: E402
from prowatch.charts.pages import (  # noqa: E402
    CpuByProcessPage,
    CpuPage,
    LifetimePage,
    MemoryByProcessPage,
    MemoryPage,
    OverviewPage,
    Page,
    RankingPage,
    SamplingPage,
)
from prowatch.charts.series import RunSeries, load_series  # noqa: E402
from prowatch.ui.theme import PRINT, Palette  # noqa: E402

PAGES: Final[tuple[Page, ...]] = (
    OverviewPage(),
    CpuPage(),
    MemoryPage(),
    LifetimePage(),
    CpuByProcessPage(),
    MemoryByProcessPage(),
    RankingPage(),
    SamplingPage(),
)
"""In reading order: what happened, then cpu, then memory, then who did it,
then whether the sampling was good enough to believe."""

FOOTER_BAND = 0.05
"""Anything a page has already drawn below this is its own footer, and the
writer must not draw a second one over it."""


def write_pdf_report(
    *,
    log_path: str,
    destination: str,
    pages: Sequence[Page] = PAGES,
    palette: Palette = PRINT,
) -> int:
    """Render ``log_path`` as a PDF. Returns the number of pages written."""
    series = load_series(log_path)
    written = 0
    # rc_context is typed against a literal key list; our dict is built from
    # the same names but mypy cannot see that through the palette indirection.
    settings = cast(Any, style.rc_params(palette))
    with plt.rc_context(settings), PdfPages(destination) as pdf:
        for number, page in enumerate(pages, start=1):
            figure = plt.figure(figsize=style.PAGE_SIZE)
            try:
                if not page.draw(figure, series=series, palette=palette):
                    continue
                _add_footer(figure, number=number, palette=palette)
                pdf.savefig(figure)
                written += 1
            finally:
                plt.close(figure)
        _describe_pdf(pdf, series=series, pages=written)
    return written


def _add_footer(figure: Figure, *, number: int, palette: Palette) -> None:
    if any(text.get_position()[1] < FOOTER_BAND for text in figure.texts):
        return  # the page drew its own footer and it says something better
    style.draw_footer(
        figure, left=f"prowatch {__version__}", right=str(number), palette=palette
    )


def _describe_pdf(pdf: PdfPages, *, series: RunSeries, pages: int) -> None:
    """Fill in the document metadata a reader sees in their PDF viewer."""
    info = pdf.infodict()  # type: ignore[no-untyped-call]
    info["Title"] = f"prowatch report - {series.target}"
    info["Author"] = f"prowatch {__version__}"
    info["Subject"] = (
        f"CPU and memory of {len(series.tracks)} process(es) over "
        f"{series.duration:.1f}s, {pages} pages"
    )
    info["CreationDate"] = datetime.now()
