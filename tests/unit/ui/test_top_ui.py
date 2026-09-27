"""The live ``top`` board, rendered to text."""

from __future__ import annotations

from rich.console import Console

from treehawk.ui.theme import PALETTE
from treehawk.ui.top_views import TopBoard, describe_event


def rendered(board: TopBoard) -> str:
    console = Console(width=120, record=True, color_system=None)
    console.print(board.render())
    return console.export_text()


def test_the_board_names_processes_and_shows_events() -> None:
    board = TopBoard(palette=PALETTE)
    board.start({"type": "header", "top_n": 5, "host": {"hostname": "robot", "mem_total_bytes": 8 * 1024**3}})
    board.update({"type": "proc", "pid": 7, "starttime": 70, "name": "planner", "cmdline": "/opt/planner --fast"})
    board.update(
        {
            "type": "sample",
            "n_procs": 312,
            "interval": 0.5,
            "host": {"cpu_percent": 42.0, "mem_used_bytes": 2 * 1024**3},
            "top": [[7, 70, 180.0, 300 * 1024**2, None, 0, None, "cm"]],
        }
    )
    board.update(
        {
            "type": "event",
            "kind": "spike",
            "ts": "2026-09-27T10:00:05+00:00",
            "pid": 7,
            "name": "planner",
            "resource": "cpu",
            "value": 180.0,
            "baseline": 20.0,
        }
    )
    text = rendered(board)
    assert "robot" in text
    assert "/opt/planner --fast" in text
    assert "180.0%" in text
    assert "42.0%" in text
    assert "spike  planner (7): cpu 180.0%, usually 20.0%" in text


def test_a_creep_is_described_by_its_rate() -> None:
    line = describe_event(
        {
            "kind": "creep",
            "pid": 3,
            "name": "leaky",
            "slope_per_hour": 64 * 1024**2,
            "baseline": 100 * 1024**2,
            "value": 132 * 1024**2,
        }
    )
    assert line == "creep  leaky (3): memory rising 64.0MiB/h, 100.0MiB → 132.0MiB"
