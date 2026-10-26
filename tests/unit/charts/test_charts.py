"""The PDF report: reshaping the log, and rendering it.

The reshaping is checked in detail because that is where a chart can quietly
lie. The rendering is checked for "did every page draw without blowing up, and
did the right pages appear" - pixels are judged by eye, not by assertion.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from conftest import write_log
from pypdf import PdfReader

from treehawk.charts.models import Metric
from treehawk.charts.pages import memory_subtitle
from treehawk.charts.report import PAGES, write_pdf_report
from treehawk.charts.series import load_series, stack_for, thin_stack
from treehawk.charts.style import time_tick_step
from treehawk.core.errors import ReportError
from treehawk.core.humanize import format_duration_compact, format_seconds
from treehawk.core.records import PHYS_FOOTPRINT, PSS

# -- reshaping ------------------------------------------------------------


def test_series_carries_the_time_axis_and_the_aggregates(log: str) -> None:
    series = load_series(log)

    assert series.t == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    assert series.cpu_percent[0] is None  # no rate in the first sample
    assert series.interval == pytest.approx(0.5)
    assert series.ncpu == 4
    assert series.target == "python train.py"
    assert series.duration == pytest.approx(2.5)


def test_each_process_becomes_one_track_with_its_lifetime(log: str) -> None:
    series = load_series(log)

    tracks = {track.pid: track for track in series.tracks}
    assert set(tracks) == {100, 200}
    assert tracks[100].first_t == pytest.approx(0.0)
    assert tracks[200].first_t == pytest.approx(1.0)  # the worker appears at sample 2
    assert tracks[200].lifetime == pytest.approx(1.5)
    assert tracks[200].group == "detached"
    assert tracks[100].group == "matched"


def test_peaks_are_kept_per_process(log: str) -> None:
    series = load_series(log)
    parent = next(track for track in series.tracks if track.pid == 100)

    assert parent.peak_rss == 55_000_000
    assert parent.cpu_seconds == pytest.approx(2.5)


def test_overruns_are_located_on_the_time_axis(log: str) -> None:
    assert load_series(log).overrun_t == [1.5]


def test_the_other_band_comes_from_untracked_processes_not_from_arithmetic(log: str) -> None:
    """Charging the aggregate's remainder to "other" would invent a series.

    The workload total includes processes that exited mid-interval and rates
    that could not be computed yet; neither belongs to a visible process.
    """
    series = load_series(log)
    bands, other = stack_for(series=series, tracks=series.tracks, metric=Metric.CPU)

    assert len(bands) == 2
    assert other == pytest.approx([0.0] * len(other))


def test_the_other_band_holds_what_was_left_out(log: str) -> None:
    series = load_series(log)
    charted = [track for track in series.tracks if track.pid == 100]

    bands, other = stack_for(series=series, tracks=charted, metric=Metric.CPU)

    assert len(bands) == 1
    assert other[2] == pytest.approx(70.0)  # the worker, not charted


def test_a_log_with_no_samples_is_refused(tmp_path: pathlib.Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with pytest.raises(ReportError):
        load_series(str(empty))


def test_a_truncated_log_still_loads(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "run.jsonl"
    write_log(path)
    with path.open("a") as handle:
        handle.write('{"type": "sample", "seq": 99, "cpu_per')

    assert load_series(str(path)).t  # the good lines are still there


# -- rendering ------------------------------------------------------------


def test_every_page_is_written_for_a_full_log(log: str, tmp_path: pathlib.Path) -> None:
    destination = tmp_path / "report.pdf"

    pages = write_pdf_report(log_path=log, destination=str(destination))

    assert pages == len(PAGES)
    assert destination.stat().st_size > 10_000


def test_the_pdf_is_readable_and_describes_itself(log: str, tmp_path: pathlib.Path) -> None:
    destination = tmp_path / "report.pdf"
    write_pdf_report(log_path=log, destination=str(destination))

    reader = PdfReader(str(destination))

    assert len(reader.pages) == len(PAGES)
    assert reader.metadata is not None
    assert "treehawk" in (reader.metadata.title or "")
    first = reader.pages[0].extract_text()
    assert "Overview" in first
    assert "peak cpu" in first


def test_per_process_pages_are_skipped_when_the_log_has_no_detail(tmp_path: pathlib.Path) -> None:
    """An --aggregate-only log gets fewer pages, not blank ones."""
    aggregate = write_log(tmp_path / "agg.jsonl", per_process=False)

    pages = write_pdf_report(log_path=aggregate, destination=str(tmp_path / "agg.pdf"))

    assert 0 < pages < len(PAGES)
    reader = PdfReader(str(tmp_path / "agg.pdf"))
    titles = " ".join(page.extract_text() for page in reader.pages)
    assert "Process lifetimes" not in titles
    assert "CPU over time" in titles


def test_a_two_sample_log_still_renders(tmp_path: pathlib.Path) -> None:
    """Short runs happen; the sampling page needs three points and bows out."""
    short = write_log(tmp_path / "short.jsonl", samples=2)

    pages = write_pdf_report(log_path=short, destination=str(tmp_path / "short.pdf"))

    assert pages >= 5


# -- what a long run needs --------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "-"), (8.1, "8.1s"), (59.94, "59.9s"), (725.0, "12m05s"), (3599.9, "59m59s"), (8401.0, "2h20m")],
)
def test_a_headline_duration_is_short_enough_for_its_column(seconds: float | None, expected: str) -> None:
    assert format_duration_compact(seconds) == expected


def test_the_compact_duration_is_never_longer_than_the_full_one() -> None:
    # The full "2h20m01.0s" is what ran into the next figure on the overview.
    assert format_seconds(8401.0) == "2h20m01.0s"
    assert len(format_duration_compact(8401.0)) < len(format_seconds(8401.0))
    assert len(format_duration_compact(359_999.0)) <= 7


@pytest.mark.parametrize(
    ("duration", "step"),
    [
        (30.0, None),  # read in seconds; matplotlib's own ticks are round there
        (200.0, 60.0),
        (1800.0, 300.0),
        (5400.0, 900.0),
        (8401.0, 1800.0),  # was 0.6h 1.1h 1.7h; now 0.5h 1h 1.5h 2h
        (36_000.0, 7200.0),
        (86_400.0 * 3, 43_200.0),
        (86_400.0 * 30, 86_400.0 * 4),
    ],
)
def test_time_ticks_fall_on_round_minutes_and_hours(duration: float, step: float | None) -> None:
    assert time_tick_step(duration) == step


@pytest.mark.parametrize("duration", [180.0, 999.0, 7199.0, 7200.0, 8401.0, 50_000.0, 1e6, 1e8])
def test_a_time_axis_is_never_crowded_or_bare(duration: float) -> None:
    step = time_tick_step(duration)

    assert step is not None
    assert 2 <= duration / step <= 8
    assert step % 60 == 0


def test_the_memory_subtitle_explains_only_what_is_drawn() -> None:
    on_macos = memory_subtitle(measure=PHYS_FOOTPRINT, rss=True, fair=True, group=False)
    on_linux = memory_subtitle(measure=PSS, rss=True, fair=True, group=True)

    assert "cgroup" not in on_macos  # there is no cgroup line on a macOS log
    assert "phys footprint" in on_macos
    assert "cgroup" in on_linux
    assert "pss" in on_linux
    # One line of a heading: this is what ran off the page.
    assert max(len(on_macos), len(on_linux)) <= 140


def test_the_memory_subtitle_for_resident_memory_alone() -> None:
    alone = memory_subtitle(measure=PSS, rss=True, fair=False, group=False)

    assert alone.startswith("summed rss")
    assert "pss" not in alone


def test_a_long_note_is_wrapped_onto_the_page_not_cut_off(tmp_path: pathlib.Path) -> None:
    import json

    path = tmp_path / "run.jsonl"
    write_log(path)
    note = "macOS has no cgroup: " + "membership is inferred from the process tree " * 5 + "and that is the end"
    lines = path.read_text().splitlines()
    header = json.loads(lines[0])
    header["notes"] = [note]
    path.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n")

    write_pdf_report(log_path=str(path), destination=str(tmp_path / "run.pdf"))

    overview = " ".join(PdfReader(str(tmp_path / "run.pdf")).pages[0].extract_text().split())
    assert "and that is the end" in overview
    assert "…" not in overview


def test_a_run_of_thousands_of_samples_still_renders_every_page(tmp_path: pathlib.Path) -> None:
    """Dense enough that one sample is thinner than the gap between bands."""
    long_run = write_log(tmp_path / "long.jsonl", samples=3000, interval=1.0)

    pages = write_pdf_report(log_path=long_run, destination=str(tmp_path / "long.pdf"))

    assert pages == len(PAGES)
    reader = PdfReader(str(tmp_path / "long.pdf"))
    text = " ".join(page.extract_text() for page in reader.pages)
    assert "elapsed (minutes)" in text
    assert "50m" in text  # 3000 s: ticks every ten minutes


def test_thinning_a_stack_keeps_the_spike_and_the_sample_it_came_from() -> None:
    t = [float(index) for index in range(1000)]
    quiet, busy = [1.0] * 1000, [2.0] * 1000
    quiet[437], busy[437] = 90.0, 130.0  # one sample, as a compiler burst is

    thinned_t, (thin_quiet, thin_busy) = thin_stack(t=t, rows=[quiet, busy], limit=100)

    assert len(thinned_t) == len(thin_quiet) == len(thin_busy) == 100
    assert thinned_t[0] == pytest.approx(0.0)
    # Both bands at the spike come from the same instant: a stack that happened.
    assert (thin_quiet[43], thin_busy[43]) == pytest.approx((90.0, 130.0))
    assert max(thin_quiet) == pytest.approx(90.0)


def test_a_stack_already_short_enough_is_left_alone() -> None:
    t, rows = [0.0, 1.0, 2.0], [[1.0, 2.0, 3.0]]

    assert thin_stack(t=t, rows=rows, limit=100) == (t, rows)


def test_a_sleep_is_placed_on_the_time_axis_but_not_counted_as_a_late_sample(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "slept.jsonl"
    write_log(path, samples=4, interval=1.0)
    lines = path.read_text().splitlines()
    records = [json.loads(line) for line in lines]
    for record in records:
        if record["type"] == "sample":
            asleep = 600.0 if record["seq"] >= 2 else 0.0
            record["t"] += asleep
            record["asleep_s"] = asleep
            record["overrun"] = False
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    series = load_series(str(path))

    assert series.t == [0.0, 1.0, 602.0, 603.0]
    assert series.gaps == pytest.approx([1.0, 1.0, 1.0])
    assert series.sleeps == [(1.0, 602.0)]
    assert series.asleep_s == pytest.approx(600.0)
    assert series.duration == pytest.approx(603.0)
    assert write_pdf_report(log_path=str(path), destination=str(tmp_path / "slept.pdf"))
