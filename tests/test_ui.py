"""The terminal views.

Rendered to a recording console rather than a real terminal, so what the user
would see is asserted as text.
"""

from __future__ import annotations

import json

from rich.console import Console

from prowatch.sinks.live import LiveSink
from prowatch.ui.dashboard import Dashboard
from prowatch.ui.theme import PALETTE, discovery_color, discovery_group
from prowatch.ui.views import render_header_facts, render_summary
from prowatch.ui.widgets import elapsed_clock, sparkline


def draw(renderable) -> str:
    console = Console(record=True, width=120, force_terminal=False)
    console.print(renderable)
    return console.export_text()


def header(**overrides):
    record = {
        "mode": "run", "interval": 0.5, "prowatch_version": "0.1.0",
        "argv": ["python", "train.py"], "started_at": "2026-01-01T00:00:00Z",
        "group_path": "/user.slice/prowatch-1.scope", "notes": [],
        "host": {"hostname": "test", "ncpu": 4, "mem_total_bytes": 8 * 1024**3},
    }
    record.update(overrides)
    return record


def sample(**overrides):
    record = {
        "seq": 1, "t": 1.5, "n_procs": 2, "cpu_percent": 180.0,
        "cpu_percent_norm": 45.0, "cpu_seconds_used": 2.7,
        "rss_bytes": 70_000_000, "pss_bytes": 55_000_000,
        "group_memory_bytes": 60_000_000, "overrun": False,
        "procs": [
            {"pid": 100, "ppid": 1, "name": "python", "cmdline": "python train.py",
             "cpu_percent": 100.0, "rss_bytes": 50_000_000, "pss_bytes": 40_000_000,
             "threads": 2, "via": "match"},
            {"pid": 200, "ppid": 1, "name": "worker", "cmdline": "python worker.py",
             "cpu_percent": 80.0, "rss_bytes": 20_000_000, "pss_bytes": 15_000_000,
             "threads": 1, "via": "orphan"},
        ],
    }
    record.update(overrides)
    return record


# -- widgets --------------------------------------------------------------

def test_sparkline_scales_to_its_own_maximum():
    assert sparkline([0, 50, 100], width=3) == "▁▄█"


def test_sparkline_shows_a_gap_rather_than_a_zero():
    """A missing reading must not look like an idle moment."""
    assert sparkline([100, None, 100], width=3) == "█ █"


def test_sparkline_handles_an_all_zero_history():
    assert sparkline([0, 0, 0], width=3) == "▁▁▁"


def test_sparkline_of_nothing_is_empty():
    assert sparkline([]) == ""


def test_elapsed_clock_reads_as_a_clock():
    assert elapsed_clock(0) == "0:00:00"
    assert elapsed_clock(3725) == "1:02:05"


# -- theme ----------------------------------------------------------------

def test_membership_rules_are_presented_as_three_groups():
    assert discovery_group("match") == "matched"
    assert discovery_group("tree") == "child"
    assert discovery_group("orphan") == "detached"
    assert discovery_group("something-new") == "detached"


def test_each_group_keeps_its_own_colour():
    colors = {
        discovery_color(via=via, palette=PALETTE)
        for via in ("match", "tree", "orphan")
    }
    assert len(colors) == 3


def test_the_palette_never_cycles_hues():
    """A seventh series takes the 'other' grey rather than repeating a colour."""
    assert PALETTE.slot(99) == PALETTE.other
    assert len(set(PALETTE.series)) == len(PALETTE.series)


# -- dashboard ------------------------------------------------------------

def test_dashboard_shows_the_workload_and_its_processes():
    dashboard = Dashboard(palette=PALETTE)
    dashboard.start(header())
    dashboard.update(sample())

    text = draw(dashboard.render())

    assert "python train.py" in text
    assert "180.0%" in text
    assert "100" in text and "200" in text  # both pids
    assert "orphan" in text
    assert "prowatch-1.scope" in text


def test_dashboard_remembers_the_peak_after_it_has_passed():
    dashboard = Dashboard(palette=PALETTE)
    dashboard.start(header())
    dashboard.update(sample(cpu_percent=400.0))
    dashboard.update(sample(cpu_percent=10.0))

    text = draw(dashboard.render())

    assert "peak 400.0%" in text


def test_dashboard_survives_a_sample_with_nothing_in_it():
    dashboard = Dashboard(palette=PALETTE)
    dashboard.start(header())
    dashboard.update(sample(procs=[], n_procs=0, cpu_percent=None, rss_bytes=None,
                            pss_bytes=None, group_memory_bytes=None))

    text = draw(dashboard.render())

    assert "0 processes" in text


def test_dashboard_caps_the_table_and_says_how_many_are_hidden():
    dashboard = Dashboard(palette=PALETTE, max_rows=1)
    dashboard.start(header())
    dashboard.update(sample())

    assert "1 more" in draw(dashboard.render())


def test_dashboard_history_is_bounded():
    """A watch may run for days; the sparkline must not accumulate for ever."""
    dashboard = Dashboard(palette=PALETTE, history=10)
    dashboard.start(header())
    for index in range(500):
        dashboard.update(sample(cpu_percent=float(index)))

    assert len(dashboard._cpu) == 10


# -- summary view ---------------------------------------------------------

def test_summary_reports_the_headline_figures():
    summary = {
        "samples": 12, "duration_s": 6.0, "peak_cpu_percent": 180.0,
        "mean_cpu_percent": 120.0, "peak_rss_bytes": 70_000_000,
        "peak_pss_bytes": 55_000_000, "peak_group_memory_bytes": 60_000_000,
        "cpu_seconds_used": 7.2, "total_procs_seen": 2, "peak_n_procs": 2,
        "overruns": 0, "exit_code": 0,
        "top_by_cpu": [{"pid": 100, "cmdline": "python train.py", "via": "match",
                        "cpu_seconds": 7.2, "peak_rss_bytes": 70_000_000}],
        "top_by_memory": [],
    }

    text = draw(render_summary(summary=summary, header=header(), palette=PALETTE))

    assert "180.0% peak" in text
    assert "66.8MiB peak rss" in text
    assert "top by cpu time" in text
    assert "python train.py" in text


def test_summary_warns_about_overruns():
    text = draw(render_summary(
        summary={"samples": 3, "overruns": 2}, header=header(), palette=PALETTE
    ))
    assert "longer than the interval" in text


def test_header_facts_include_the_boundary_and_notes():
    text = draw(render_header_facts(
        header=header(notes=["cgroup v2 is not mounted"]), palette=PALETTE
    ))
    assert "prowatch-1.scope" in text
    assert "cgroup v2 is not mounted" in text


# -- live sink ------------------------------------------------------------

def test_live_sink_leaves_the_summary_behind(tmp_path):
    """The live region is transient; what stays in the scrollback is the result."""
    console = Console(record=True, width=120, file=open(tmp_path / "out", "w"))
    sink = LiveSink(console=console)

    sink.open(header())
    sink.sample(sample())
    sink.close({"samples": 1, "peak_cpu_percent": 180.0, "duration_s": 1.5})

    assert "180.0% peak" in console.export_text()
