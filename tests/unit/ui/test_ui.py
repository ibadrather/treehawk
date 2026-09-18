"""The terminal views.

Rendered to a recording console rather than a real terminal, so what the user
would see is asserted as text.
"""

from __future__ import annotations

import pathlib

from rich.console import Console, RenderableType

from treehawk.core.interfaces import Record
from treehawk.sinks.live import LiveSink
from treehawk.ui.dashboard import Dashboard
from treehawk.ui.theme import PALETTE, discovery_color, discovery_group
from treehawk.ui.views import render_header_facts, render_summary
from treehawk.ui.widgets import elapsed_clock, sparkline


def draw(renderable: RenderableType) -> str:
    console = Console(record=True, width=120, force_terminal=False)
    console.print(renderable)
    return console.export_text()


def header(**overrides: object) -> Record:
    record: Record = {
        "mode": "run",
        "interval": 0.5,
        "treehawk_version": "0.1.0",
        "argv": ["python", "train.py"],
        "started_at": "2026-01-01T00:00:00Z",
        "group_path": "/user.slice/treehawk-1.scope",
        "notes": [],
        "host": {"hostname": "test", "ncpu": 4, "mem_total_bytes": 8 * 1024**3},
    }
    record.update(overrides)
    return record


def sample(**overrides: object) -> Record:
    record: Record = {
        "seq": 1,
        "t": 1.5,
        "n_procs": 2,
        "cpu_percent": 180.0,
        "cpu_percent_norm": 45.0,
        "cpu_seconds_used": 2.7,
        "rss_bytes": 70_000_000,
        "pss_bytes": 55_000_000,
        "group_memory_bytes": 60_000_000,
        "overrun": False,
        "procs": [
            {
                "pid": 100,
                "ppid": 1,
                "name": "python",
                "cmdline": "python train.py",
                "cpu_percent": 100.0,
                "rss_bytes": 50_000_000,
                "pss_bytes": 40_000_000,
                "threads": 2,
                "via": "match",
            },
            {
                "pid": 200,
                "ppid": 1,
                "name": "worker",
                "cmdline": "python worker.py",
                "cpu_percent": 80.0,
                "rss_bytes": 20_000_000,
                "pss_bytes": 15_000_000,
                "threads": 1,
                "via": "orphan",
            },
        ],
    }
    record.update(overrides)
    return record


# -- widgets --------------------------------------------------------------


def test_sparkline_scales_to_its_own_maximum() -> None:
    assert sparkline([0, 50, 100], width=3) == "▁▄█"


def test_sparkline_shows_a_gap_rather_than_a_zero() -> None:
    """A missing reading must not look like an idle moment."""
    assert sparkline([100, None, 100], width=3) == "█ █"


def test_sparkline_handles_an_all_zero_history() -> None:
    assert sparkline([0, 0, 0], width=3) == "▁▁▁"


def test_sparkline_of_nothing_is_empty() -> None:
    assert sparkline([]) == ""


def test_elapsed_clock_reads_as_a_clock() -> None:
    assert elapsed_clock(0) == "0:00:00"
    assert elapsed_clock(3725) == "1:02:05"


# -- theme ----------------------------------------------------------------


def test_membership_rules_are_presented_as_three_groups() -> None:
    assert discovery_group("match") == "matched"
    assert discovery_group("tree") == "child"
    assert discovery_group("orphan") == "detached"
    assert discovery_group("something-new") == "detached"


def test_each_group_keeps_its_own_colour() -> None:
    colors = {discovery_color(via=via, palette=PALETTE) for via in ("match", "tree", "orphan")}
    assert len(colors) == 3


def test_the_palette_never_cycles_hues() -> None:
    """A seventh series takes the 'other' grey rather than repeating a colour."""
    assert PALETTE.slot(99) == PALETTE.other
    assert len(set(PALETTE.series)) == len(PALETTE.series)


# -- dashboard ------------------------------------------------------------


def test_dashboard_shows_the_workload_and_its_processes() -> None:
    dashboard = Dashboard(palette=PALETTE)
    dashboard.start(header())
    dashboard.update(sample())

    text = draw(dashboard.render())

    assert "python train.py" in text
    assert "180.0%" in text
    assert "100" in text and "200" in text  # both pids
    assert "orphan" in text
    assert "treehawk-1.scope" in text


def test_dashboard_remembers_the_peak_after_it_has_passed() -> None:
    dashboard = Dashboard(palette=PALETTE)
    dashboard.start(header())
    dashboard.update(sample(cpu_percent=400.0))
    dashboard.update(sample(cpu_percent=10.0))

    text = draw(dashboard.render())

    assert "peak 400.0%" in text


def test_dashboard_survives_a_sample_with_nothing_in_it() -> None:
    dashboard = Dashboard(palette=PALETTE)
    dashboard.start(header())
    dashboard.update(
        sample(procs=[], n_procs=0, cpu_percent=None, rss_bytes=None, pss_bytes=None, group_memory_bytes=None)
    )

    text = draw(dashboard.render())

    assert "0 processes" in text


def test_dashboard_caps_the_table_and_says_how_many_are_hidden() -> None:
    dashboard = Dashboard(palette=PALETTE, max_rows=1)
    dashboard.start(header())
    dashboard.update(sample())

    assert "1 more" in draw(dashboard.render())


def test_dashboard_history_is_bounded() -> None:
    """A watch may run for days; the sparkline must not accumulate for ever."""
    dashboard = Dashboard(palette=PALETTE, history=10)
    dashboard.start(header())
    for index in range(500):
        dashboard.update(sample(cpu_percent=float(index)))

    assert len(dashboard.cpu_history) == 10


# -- summary view ---------------------------------------------------------


def test_summary_reports_the_headline_figures() -> None:
    summary: Record = {
        "samples": 12,
        "duration_s": 6.0,
        "peak_cpu_percent": 180.0,
        "mean_cpu_percent": 120.0,
        "peak_rss_bytes": 70_000_000,
        "peak_pss_bytes": 55_000_000,
        "peak_group_memory_bytes": 60_000_000,
        "cpu_seconds_used": 7.2,
        "total_procs_seen": 2,
        "peak_n_procs": 2,
        "overruns": 0,
        "exit_code": 0,
        "top_by_cpu": [
            {"pid": 100, "cmdline": "python train.py", "via": "match", "cpu_seconds": 7.2, "peak_rss_bytes": 70_000_000}
        ],
        "top_by_memory": [],
    }

    text = draw(render_summary(summary=summary, header=header(), palette=PALETTE))

    assert "180.0% peak" in text
    assert "66.8MiB peak rss" in text
    assert "top by cpu time" in text
    assert "python train.py" in text


def test_summary_warns_about_overruns() -> None:
    text = draw(render_summary(summary={"samples": 3, "overruns": 2}, header=header(), palette=PALETTE))
    assert "longer than the interval" in text


def test_header_facts_include_the_boundary_and_notes() -> None:
    text = draw(render_header_facts(header=header(notes=["cgroup v2 is not mounted"]), palette=PALETTE))
    assert "treehawk-1.scope" in text
    assert "cgroup v2 is not mounted" in text


# -- live sink ------------------------------------------------------------


def test_live_sink_leaves_the_summary_behind(tmp_path: pathlib.Path) -> None:
    """The live region is transient; what stays in the scrollback is the result."""
    with (tmp_path / "out").open("w") as out:
        console = Console(record=True, width=120, file=out)
        sink = LiveSink(console=console)

        sink.open(header())
        sink.sample(sample())
        sink.close({"samples": 1, "peak_cpu_percent": 180.0, "duration_s": 1.5})

    assert "180.0% peak" in console.export_text()


def test_the_dashboard_names_the_measure_the_log_actually_carries() -> None:
    board = Dashboard(palette=PALETTE)
    board.start(header(memory_kind="phys_footprint", group_path=None))
    board.update(sample(group_memory_bytes=None, procs=[{"pid": 100, "pss_bytes": 1234, "via": "match"}]))

    text = draw(board.render())

    # macOS has no PSS, so calling the column "pss" there would be a lie.
    assert "foot" in text
    assert "pss" not in text


def test_a_log_written_before_the_field_existed_still_reads_as_pss() -> None:
    board = Dashboard(palette=PALETTE)
    board.start(header(group_path=None))  # no memory_kind at all
    board.update(sample(group_memory_bytes=None, procs=[{"pid": 100, "pss_bytes": 1234, "via": "match"}]))

    # treehawk was Linux-only then, so there is nothing else it could be.
    assert "pss" in draw(board.render())


def test_the_summary_names_the_measure_too() -> None:
    rendered = draw(
        render_summary(
            summary={"peak_rss_bytes": 100, "peak_pss_bytes": 90},
            header=header(memory_kind="phys_footprint"),
            palette=PALETTE,
            title="run.jsonl",
        )
    )

    assert "peak foot" in rendered


def test_the_summary_omits_a_group_figure_no_platform_could_supply() -> None:
    rendered = draw(
        render_summary(
            summary={"peak_rss_bytes": 100, "peak_pss_bytes": 90, "peak_group_memory_bytes": None},
            header=header(memory_kind="phys_footprint"),
            palette=PALETTE,
            title="run.jsonl",
        )
    )

    assert "cgroup" not in rendered


# -- what the workload printed ----------------------------------------------


def test_the_output_panel_is_absent_until_the_workload_prints_something() -> None:
    dashboard = Dashboard(palette=PALETTE)
    dashboard.start(header())

    assert "output" not in draw(dashboard.render())


def test_the_output_panel_shows_what_the_workload_printed() -> None:
    dashboard = Dashboard(palette=PALETTE)
    dashboard.start(header())
    dashboard.append_output("epoch 0 loss=2.31")

    drawn = draw(dashboard.render())

    assert "output" in drawn
    assert "epoch 0 loss=2.31" in drawn


def test_only_the_last_lines_are_kept_so_the_region_stays_a_fixed_height() -> None:
    dashboard = Dashboard(palette=PALETTE, output_lines=3)
    dashboard.start(header())
    for step in range(10):
        dashboard.append_output(f"step {step}")

    drawn = draw(dashboard.render())

    assert "step 9" in drawn
    assert "step 6" not in drawn


def test_a_line_wider_than_the_terminal_is_truncated_rather_than_wrapped() -> None:
    """Wrapping would change the height of the live region between refreshes."""
    dashboard = Dashboard(palette=PALETTE, output_lines=1)
    dashboard.start(header())
    dashboard.append_output("x" * 500)

    lines = [line for line in draw(dashboard.render()).splitlines() if "x" in line]

    assert len(lines) == 1
    assert "\u2026" in lines[0]


def test_the_workload_s_own_colours_are_read_rather_than_shown_as_escapes() -> None:
    dashboard = Dashboard(palette=PALETTE, output_lines=1)
    dashboard.start(header())
    dashboard.append_output("\x1b[32m[ok]\x1b[0m done")

    drawn = draw(dashboard.render())

    assert "[ok] done" in drawn
    assert "[32m" not in drawn


def test_the_output_panel_is_drawn_above_the_process_table() -> None:
    """Order matters: a busy dashboard is taller than the terminal.

    Rich crops the bottom of the live region, so the bounded panels go first
    and the process table - whose height the workload decides, and whose rows
    are all in the log anyway - is what gets cropped.
    """
    dashboard = Dashboard(palette=PALETTE)
    dashboard.start(header())
    dashboard.update(sample())
    dashboard.append_output("epoch 0 loss=2.31")

    drawn = draw(dashboard.render())

    # The panel titles, not the word "processes" in the stats row above them.
    assert drawn.index("─ output") < drawn.index("─ processes")


def test_the_live_sink_takes_a_printed_line_at_any_point_in_the_run() -> None:
    """The reader thread does not know where the sampling loop has got to.

    It can hand over a line before the first sample or after the summary has
    been drawn, and neither may raise: the workload keeps printing until it is
    gone, and a reader that dies on one line stops draining the rest.
    """
    sink = LiveSink(console=Console(record=True, width=120, force_terminal=False))

    sink.output("before the run opened")
    sink.open(header())
    sink.output("during the run")
    sink.close({"samples": 1})
    sink.output("after the summary")
