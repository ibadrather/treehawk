"""The HTML report: its axes, its point budget, and what the page says.

The arithmetic is checked in detail, because that is where a chart can quietly
lie. The page is checked for what a reader must find on it - the times, the
warnings, every process - and for never letting a log's text become markup.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from conftest import write_log

from treehawk.core.errors import ReportError
from treehawk.htmlreport import write_html_report
from treehawk.htmlreport.models import Unit
from treehawk.htmlreport.scale import elapsed_label, elapsed_ticks, thin, value_axis, with_breaks

# -- axes -----------------------------------------------------------------


def test_a_percent_axis_lands_on_round_numbers() -> None:
    top, ticks = value_axis(165.0, unit=Unit.PERCENT)

    assert top == pytest.approx(200.0)
    assert [tick.label for tick in ticks] == ["0", "50%", "100%", "150%", "200%"]
    assert ticks[-1].position == pytest.approx(1.0)


def test_a_memory_axis_reads_in_one_binary_unit() -> None:
    top, ticks = value_axis(9.5 * 1024**3, unit=Unit.BYTES)

    assert top >= 9.5 * 1024**3
    assert all(tick.label.endswith(" GiB") for tick in ticks[1:])
    assert ticks[1].label == "5 GiB"


def test_a_count_axis_never_steps_by_less_than_one() -> None:
    _top, ticks = value_axis(2.0, unit=Unit.COUNT)

    assert [tick.label for tick in ticks] == ["0", "1", "2", "3"]


def test_an_empty_axis_still_has_a_height() -> None:
    top, ticks = value_axis(None, unit=Unit.PERCENT)

    assert top > 0
    assert ticks


@pytest.mark.parametrize(
    ("seconds", "label"),
    [(0.0, "0"), (3e-06, "0"), (1.00502, "1s"), (45.0, "45s"), (90.0, "1m30s"), (7200.0, "2h"), (93784.0, "1d2h")],
)
def test_elapsed_labels_are_short(seconds: float, label: str) -> None:
    assert elapsed_label(seconds) == label


def test_elapsed_ticks_cover_the_run_without_crowding() -> None:
    ticks = elapsed_ticks(18558.0)

    assert ticks[0].label == "0"
    assert len(ticks) <= 8
    assert [tick.label for tick in ticks[:3]] == ["0", "1h", "2h"]
    assert all(0.0 <= tick.position <= 1.0 for tick in ticks)


# -- the point budget -----------------------------------------------------


def test_a_short_series_is_left_alone() -> None:
    x, (values,) = thin(x=[0.0, 1.0, 2.0], columns=[[1.0, None, 3.0]], limit=10)

    assert x == [0.0, 1.0, 2.0]
    assert values == [1.0, None, 3.0]


def test_thinning_keeps_a_one_sample_spike() -> None:
    readings: list[float | None] = [1.0] * 10_000
    readings[5_123] = 900.0

    x, (values,) = thin(x=[float(moment) for moment in range(10_000)], columns=[readings], limit=500)

    assert len(x) <= 500
    assert max(value for value in values if value is not None) == pytest.approx(900.0)


def test_a_hole_in_the_readings_becomes_a_break() -> None:
    x, (values,) = with_breaks(x=[0.0, 1.0, 2.0, 50.0, 51.0], columns=[[1.0, 2.0, 3.0, 4.0, 5.0]], gap=3.0)

    assert values == [1.0, 2.0, 3.0, None, 4.0, 5.0]
    assert x[3] == pytest.approx(26.0)


# -- the page -------------------------------------------------------------


def _page(log: str, tmp_path: pathlib.Path) -> str:
    destination = tmp_path / "page.html"
    write_html_report(log_path=log, destination=str(destination))
    return destination.read_text(encoding="utf-8")


def test_the_page_stands_alone(log: str, tmp_path: pathlib.Path) -> None:
    page = _page(log, tmp_path)

    assert page.startswith("<!doctype html>")
    assert "<style>" in page
    assert "<script>" in page
    # Nothing to fetch: it opens offline, from an attachment, years later.
    assert not re.search(r"""(src|href)=["']https?://""", page)


def test_the_page_says_what_ran_and_when(log: str, tmp_path: pathlib.Path) -> None:
    page = _page(log, tmp_path)

    assert "python train.py" in page
    for label in ("Started", "Ended", "Duration", "Peak CPU", "Peak memory", "Processes"):
        assert label in page
    assert "exit 0" in page


def test_the_page_lists_every_process_as_a_track(log: str, tmp_path: pathlib.Path) -> None:
    page = _page(log, tmp_path)

    assert "Process tracks" in page
    assert page.count('class="life-track"') == 2
    assert "worker.py" in page
    assert "detached" in page


def test_a_late_sample_is_a_warning_and_a_mark(log: str, tmp_path: pathlib.Path) -> None:
    page = _page(log, tmp_path)

    assert "1 of 6 samples took longer than the 0.5s interval" in page
    assert page.count('class="mark warning"') == 1


def test_every_chart_carries_its_hover_readings(log: str, tmp_path: pathlib.Path) -> None:
    page = _page(log, tmp_path)

    blobs = re.findall(r'data-series="([^"]*)"', page)
    assert len(blobs) == 3  # cpu, memory, processes alive
    for blob in blobs:
        data = json.loads(blob.replace("&quot;", '"').replace("&amp;", "&"))
        assert len(data["x"]) == len(data["t"])
        assert all(len(values) == len(data["x"]) for values in data["v"])


def test_a_log_without_a_summary_says_it_was_interrupted(tmp_path: pathlib.Path) -> None:
    log = pathlib.Path(write_log(tmp_path / "run.jsonl"))
    lines = log.read_text().splitlines()
    log.write_text("\n".join(lines[:-1]) + "\n")

    page = _page(str(log), tmp_path)

    assert "The log ends without a summary" in page
    assert "interrupted" in page


def test_an_aggregate_only_log_has_no_tracks(tmp_path: pathlib.Path) -> None:
    log = write_log(tmp_path / "run.jsonl", per_process=False)

    page = _page(log, tmp_path)

    assert "Process tracks" not in page
    assert "Logged without per-process detail" in page


def test_text_from_the_log_never_becomes_markup(tmp_path: pathlib.Path) -> None:
    log = pathlib.Path(write_log(tmp_path / "run.jsonl"))
    log.write_text(log.read_text().replace('"train.py"', '"<script>alert(1)</script>"'))

    page = _page(str(log), tmp_path)

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_a_long_run_stays_small(tmp_path: pathlib.Path) -> None:
    log = write_log(tmp_path / "run.jsonl", samples=5_000, interval=1.0)

    page = _page(log, tmp_path)

    assert len(page) < 600_000
    assert "Each point is the peak of" in page


def test_an_empty_log_is_refused(tmp_path: pathlib.Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")

    with pytest.raises(ReportError):
        write_html_report(log_path=str(empty), destination=str(tmp_path / "page.html"))


def test_a_page_that_cannot_be_written_fails_clearly(log: str, tmp_path: pathlib.Path) -> None:
    with pytest.raises(ReportError, match="cannot write"):
        write_html_report(log_path=log, destination=str(tmp_path / "missing" / "page.html"))
