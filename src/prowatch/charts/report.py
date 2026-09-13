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

from .. import __version__  # noqa: E402
from ..ui.theme import PRINT, Palette  # noqa: E402
from . import style  # noqa: E402
from .pages import (  # noqa: E402
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
from .series import RunSeries, load_series  # noqa: E402

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
    log_path: str,
    destination: str,
    *,
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
                if not page.draw(figure, series, palette):
                    continue
                _footer(figure, series, number, palette)
                pdf.savefig(figure)
                written += 1
            finally:
                plt.close(figure)
        _describe(pdf, series, written)
    return written


def _footer(
    figure: Figure, series: RunSeries, number: int, palette: Palette
) -> None:
    if not any(text.get_position()[1] < 0.05 for text in figure.texts):
        style.footer(figure, f"prowatch {__version__}", str(number), palette)


def _describe(pdf: PdfPages, series: RunSeries, pages: int) -> None:
    info = pdf.infodict()  # type: ignore[no-untyped-call]
    info["Title"] = f"prowatch report - {series.target}"
    info["Author"] = f"prowatch {__version__}"
    info["Subject"] = (
        f"CPU and memory of {len(series.tracks)} process(es) over "
        f"{series.duration:.1f}s, {pages} pages"
    )
    info["CreationDate"] = datetime.now()
