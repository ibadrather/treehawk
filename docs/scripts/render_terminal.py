"""Render treehawk's terminal output as SVG, from a real log.

    uv run python docs/scripts/render_terminal.py LOG OUTPUT_DIR

Writes ``report.svg`` (what ``treehawk report LOG`` prints) and
``dashboard.svg`` (the live dashboard, as it looked part-way through the run).
Both are drawn by treehawk's own renderers, so the pictures cannot drift from
what the program shows.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Final

from rich.console import Console, Group, RenderableType
from rich.terminal_theme import TerminalTheme
from rich.text import Text

from treehawk.core.interfaces import Record
from treehawk.report import build_report, read_records
from treehawk.ui.dashboard import Dashboard
from treehawk.ui.theme import PALETTE
from treehawk.ui.views import render_header_facts, render_summary

WIDTH: Final = 118

THEME: Final = TerminalTheme(
    (26, 26, 25),
    (195, 194, 183),
    [
        (26, 26, 25),
        (230, 103, 103),
        (25, 158, 112),
        (201, 133, 0),
        (57, 135, 229),
        (213, 81, 129),
        (144, 133, 233),
        (195, 194, 183),
    ],
    [
        (138, 137, 129),
        (230, 103, 103),
        (25, 158, 112),
        (237, 161, 0),
        (57, 135, 229),
        (232, 123, 164),
        (144, 133, 233),
        (255, 255, 255),
    ],
)
"""treehawk's terminal palette (``ui/theme.py``), as a terminal colour scheme."""


def save_svg(renderable: RenderableType, *, path: Path, title: str) -> None:
    console = Console(record=True, width=WIDTH, force_terminal=True, color_system="truecolor", file=io.StringIO())
    console.print(renderable)
    console.save_svg(str(path), title=title, theme=THEME)


def prompt(command: str) -> Text:
    return Text.assemble(("$ ", PALETTE.text_muted), (command, f"bold {PALETTE.text_primary}"), "\n")


def render_report(log: Path, destination: Path) -> None:
    report = build_report(str(log))
    header, summary = report["header"], report["summary"]
    body = Group(
        prompt(f"treehawk report {log.name}"),
        render_header_facts(header=header, palette=PALETTE),
        render_summary(summary=summary, header=header, palette=PALETTE, title=log.name),
    )
    save_svg(body, path=destination, title="treehawk report")


def render_dashboard(log: Path, destination: Path, *, at_fraction: float = 0.4) -> None:
    records = list(read_records(str(log)))
    samples: list[Record] = [record for record in records if record.get("type") == "sample"]
    header = next((record for record in records if record.get("type") == "header"), {})
    dashboard = Dashboard(palette=PALETTE)
    dashboard.start(header)
    for record in samples[: max(1, round(len(samples) * at_fraction))]:
        dashboard.update(record)
    save_svg(dashboard.render(), path=destination, title="treehawk run")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    log: Path = args.log
    output: Path = args.output
    output.mkdir(parents=True, exist_ok=True)
    render_report(log, output / "report.svg")
    render_dashboard(log, output / "dashboard.svg")


if __name__ == "__main__":
    main()
