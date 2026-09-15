"""Writing the PDF.

The writer knows only that a page has a title and can draw itself. Adding a page
means appending to :data:`PAGES`; nothing here changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Final

import matplotlib

matplotlib.use("Agg")  # no display needed, and none is available on a server

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

from treehawk import __version__
from treehawk.charts import style
from treehawk.charts.constants import CHARTS
from treehawk.charts.models import RunSeries
from treehawk.charts.pages import (
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
from treehawk.charts.series import load_series
from treehawk.ui.models import Palette
from treehawk.ui.theme import PRINT

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
    with plt.rc_context(style.rc_params(palette)), PdfPages(destination) as pdf:
        for number, page in enumerate(pages, start=1):
            figure = plt.figure(figsize=CHARTS.page_size)
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
    if any(text.get_position()[1] < CHARTS.footer_band for text in figure.texts):
        return  # the page drew its own footer and it says something better
    style.draw_footer(figure, left=f"treehawk {__version__}", right=str(number), palette=palette)


def _describe_pdf(pdf: PdfPages, *, series: RunSeries, pages: int) -> None:
    """Fill in the document metadata a reader sees in their PDF viewer."""
    info = pdf.infodict()  # type: ignore[no-untyped-call]
    info["Title"] = f"treehawk report - {series.target}"
    info["Author"] = f"treehawk {__version__}"
    info["Subject"] = f"CPU and memory of {len(series.tracks)} process(es) over {series.duration:.1f}s, {pages} pages"
    info["CreationDate"] = datetime.now().astimezone()
