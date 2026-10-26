"""Writing the HTML report to disk."""

from __future__ import annotations

import pathlib
import time

from treehawk.charts.series import load_series
from treehawk.charts.top_series import load_top_series
from treehawk.core.errors import ReportError
from treehawk.htmlreport.document import render
from treehawk.htmlreport.models import Page
from treehawk.htmlreport.run import run_page
from treehawk.htmlreport.top import top_page
from treehawk.report import build_report


def write_html_report(*, log_path: str, destination: str) -> None:
    """Render a workload log (``run`` or ``watch``) as one HTML file."""
    series = load_series(log_path)
    summary = build_report(log_path)["summary"]
    _write(run_page(series=series, summary=summary, log_path=log_path, generated=time.time()), destination)


def write_top_html_report(
    *,
    log_path: str,
    destination: str,
    since: float | None = None,
    until: float | None = None,
) -> None:
    """Render a ``top`` log (a file or a directory) as one HTML file."""
    series = load_top_series(log_path, since=since, until=until)
    page = top_page(series=series, log_path=log_path, generated=time.time(), since=since, until=until)
    _write(page, destination)


def _write(page: Page, destination: str) -> None:
    try:
        pathlib.Path(destination).write_text(render(page), encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"cannot write {destination}: {exc}") from exc
