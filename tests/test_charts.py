"""The PDF report: reshaping the log, and rendering it.

The reshaping is checked in detail because that is where a chart can quietly
lie. The rendering is checked for "did every page draw without blowing up, and
did the right pages appear" - pixels are judged by eye, not by assertion.
"""

from __future__ import annotations

import pytest
from conftest import write_log
from pypdf import PdfReader

from prowatch.charts.report import PAGES, write_pdf_report
from prowatch.charts.series import load_series, stack_for
from prowatch.report import ReportError


# -- reshaping ------------------------------------------------------------

def test_series_carries_the_time_axis_and_the_aggregates(log):
    series = load_series(log)

    assert series.t == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    assert series.cpu_percent[0] is None  # no rate in the first sample
    assert series.interval == 0.5
    assert series.ncpu == 4
    assert series.target == "python train.py"
    assert series.duration == 2.5


def test_each_process_becomes_one_track_with_its_lifetime(log):
    series = load_series(log)

    tracks = {track.pid: track for track in series.tracks}
    assert set(tracks) == {100, 200}
    assert tracks[100].first_t == 0.0
    assert tracks[200].first_t == 1.0  # the worker appears at sample 2
    assert tracks[200].lifetime == pytest.approx(1.5)
    assert tracks[200].group == "detached"
    assert tracks[100].group == "matched"


def test_peaks_are_kept_per_process(log):
    series = load_series(log)
    parent = next(track for track in series.tracks if track.pid == 100)

    assert parent.peak_rss == 55_000_000
    assert parent.cpu_seconds == pytest.approx(2.5)


def test_overruns_are_located_on_the_time_axis(log):
    assert load_series(log).overrun_t == [1.5]


def test_the_other_band_comes_from_untracked_processes_not_from_arithmetic(log):
    """Charging the aggregate's remainder to "other" would invent a series.

    The workload total includes processes that exited mid-interval and rates
    that could not be computed yet; neither belongs to a visible process.
    """
    series = load_series(log)
    bands, other = stack_for(series, series.tracks, "cpu")

    assert len(bands) == 2
    assert all(value == 0.0 for value in other)


def test_the_other_band_holds_what_was_left_out(log):
    series = load_series(log)
    charted = [track for track in series.tracks if track.pid == 100]

    bands, other = stack_for(series, charted, "cpu")

    assert len(bands) == 1
    assert other[2] == pytest.approx(70.0)  # the worker, not charted


def test_a_log_with_no_samples_is_refused(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with pytest.raises(ReportError):
        load_series(str(empty))


def test_a_truncated_log_still_loads(tmp_path):
    path = tmp_path / "run.jsonl"
    write_log(path)
    with path.open("a") as handle:
        handle.write('{"type": "sample", "seq": 99, "cpu_per')

    assert load_series(str(path)).t  # the good lines are still there


# -- rendering ------------------------------------------------------------

def test_every_page_is_written_for_a_full_log(log, tmp_path):
    destination = tmp_path / "report.pdf"

    pages = write_pdf_report(log, str(destination))

    assert pages == len(PAGES)
    assert destination.stat().st_size > 10_000


def test_the_pdf_is_readable_and_describes_itself(log, tmp_path):
    destination = tmp_path / "report.pdf"
    write_pdf_report(log, str(destination))

    reader = PdfReader(str(destination))

    assert len(reader.pages) == len(PAGES)
    assert "prowatch" in (reader.metadata or {}).get("/Title", "")
    first = reader.pages[0].extract_text()
    assert "Overview" in first
    assert "peak cpu" in first


def test_per_process_pages_are_skipped_when_the_log_has_no_detail(tmp_path):
    """An --aggregate-only log gets fewer pages, not blank ones."""
    aggregate = write_log(tmp_path / "agg.jsonl", per_process=False)

    pages = write_pdf_report(aggregate, str(tmp_path / "agg.pdf"))

    assert 0 < pages < len(PAGES)
    reader = PdfReader(str(tmp_path / "agg.pdf"))
    titles = " ".join(page.extract_text() for page in reader.pages)
    assert "Process lifetimes" not in titles
    assert "CPU over time" in titles


def test_a_two_sample_log_still_renders(tmp_path):
    """Short runs happen; the sampling page needs three points and bows out."""
    short = write_log(tmp_path / "short.jsonl", samples=2)

    pages = write_pdf_report(short, str(tmp_path / "short.pdf"))

    assert pages >= 5
