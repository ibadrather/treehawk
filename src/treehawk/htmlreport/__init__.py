"""HTML reporting: one self-contained page per log.

The default way to read a finished log back. The page carries its own
stylesheet, script and charts, so it opens offline in any browser and needs
nothing installed beyond treehawk itself - no matplotlib, which only the PDF
report uses. Structure mirrors :mod:`treehawk.charts`: a log is read into
plain models (:mod:`run`, :mod:`top`), and :mod:`document` alone writes markup.
"""

from treehawk.htmlreport.writer import write_html_report, write_top_html_report

__all__ = ["write_html_report", "write_top_html_report"]
