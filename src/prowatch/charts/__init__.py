"""PDF reporting.

The only part of prowatch that imports matplotlib, and it is imported lazily by
the CLI so that a plain watch never pays for it. Structure mirrors the rest of
the codebase: reshaping the log into series (:mod:`series`) is separate from
drawing it (:mod:`pages`), and the writer (:mod:`report`) knows only that a page
is something with a title that can draw itself onto a figure.
"""

from prowatch.charts.report import PAGES, write_pdf_report

__all__ = ["PAGES", "write_pdf_report"]
