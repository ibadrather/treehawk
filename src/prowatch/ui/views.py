"""End-of-run and post-hoc views.

One renderer serves both: the summary shown when a watch finishes and the output
of ``prowatch report`` are the same information, so they are the same code.
"""

from __future__ import annotations

from typing import Callable, Sequence

from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from prowatch.core.humanize import (
    format_bytes,
    format_percent,
    format_seconds,
    truncate,
)
from prowatch.core.interfaces import Record
from prowatch.core.records import target_of
from prowatch.core.values import as_float
from prowatch.ui.theme import Palette, discovery_color

Formatter = Callable[[float | None], str]
"""How one ranking column turns its measurement into text."""


def render_summary(
    *,
    summary: Record,
    header: Record,
    palette: Palette,
    title: str = "summary",
) -> RenderableType:
    """The figures people ask for, plus who was responsible for them."""
    parts: list[RenderableType] = [
        _summary_facts(summary=summary, header=header, palette=palette)
    ]
    tables = [
        _ranking(
            rows=summary.get("top_by_cpu") or [],
            title="cpu time",
            field="cpu_seconds",
            formatter=format_seconds,
            palette=palette,
        ),
        _ranking(
            rows=summary.get("top_by_memory") or [],
            title="peak rss",
            field="peak_rss_bytes",
            formatter=format_bytes,
            palette=palette,
        ),
    ]
    present = [table for table in tables if table is not None]
    if present:
        # Not forced equal-width: on a narrow terminal Rich stacks them, which
        # is better than squeezing a value column until it has to truncate.
        parts.append(Columns(present, padding=(0, 4)))
    return Panel(
        Group(*parts), title=title, title_align="left",
        border_style=palette.grid, padding=(0, 1),
    )


def render_header_facts(*, header: Record, palette: Palette) -> RenderableType:
    """Run metadata, for the report command where there is no live panel."""
    host = header.get("host") or {}
    table = Table.grid(padding=(0, 2))
    table.add_column(style=palette.text_muted, width=9)
    table.add_column()
    table.add_row(
        "target", Text(target_of(header), style=f"bold {palette.slot(0)}")
    )
    table.add_row(
        "run",
        f"{header.get('mode', '?')} · every {header.get('interval', '?')}s · "
        f"started {header.get('started_at', '?')}",
    )
    table.add_row(
        "host",
        f"{host.get('hostname', '?')} · {host.get('ncpu', '?')} cpus · "
        f"{format_bytes(as_float(host.get('mem_total_bytes')))} ram",
    )
    boundary = header.get("group_path")
    table.add_row(
        "cgroup",
        str(boundary) if boundary else Text("none", style=palette.text_muted),
    )
    for note in header.get("notes") or ():
        table.add_row("note", Text(str(note), style=palette.warning))
    return table


def _summary_facts(
    *, summary: Record, header: Record, palette: Palette
) -> RenderableType:
    ncpu = (header.get("host") or {}).get("ncpu")
    table = Table.grid(padding=(0, 2))
    table.add_column(style=palette.text_muted, width=9)
    table.add_column()
    table.add_row(
        "cpu",
        Text.assemble(
            (
                f"{format_percent(as_float(summary.get('peak_cpu_percent')))} peak",
                f"bold {palette.slot(0)}",
            ),
            (
                f"  {format_percent(as_float(summary.get('mean_cpu_percent')))} mean",
                palette.text_secondary,
            ),
            (
                f"  {format_seconds(as_float(summary.get('cpu_seconds_used')))}"
                " of cpu time",
                palette.text_secondary,
            ),
            (f"  on {ncpu} cpus" if ncpu else "", palette.text_muted),
        ),
    )
    table.add_row(
        "memory",
        Text.assemble(
            (
                f"{format_bytes(as_float(summary.get('peak_rss_bytes')))} peak rss",
                f"bold {palette.slot(2)}",
            ),
            (
                f"  {format_bytes(as_float(summary.get('peak_pss_bytes')))} peak pss",
                palette.text_secondary,
            ),
            (
                "  "
                f"{format_bytes(as_float(summary.get('peak_group_memory_bytes')))}"
                " peak cgroup",
                palette.text_secondary,
            ),
        ),
    )
    table.add_row(
        "run",
        f"{summary.get('samples', 0)} samples over "
        f"{format_seconds(as_float(summary.get('duration_s')))} · "
        f"{summary.get('total_procs_seen', '?')} processes seen, "
        f"peak {summary.get('peak_n_procs', '?')} at once",
    )
    if summary.get("overruns"):
        table.add_row(
            "warning",
            Text(
                f"{summary['overruns']} sample(s) took longer than the interval - "
                "try a longer interval",
                style=palette.warning,
            ),
        )
    exit_code = summary.get("exit_code")
    if exit_code is not None:
        style = palette.good if exit_code == 0 else palette.critical
        table.add_row("exit", Text(str(exit_code), style=f"bold {style}"))
    return table


def _ranking(
    *,
    rows: Sequence[Record],
    title: str,
    field: str,
    formatter: Formatter,
    palette: Palette,
) -> RenderableType | None:
    if not rows:
        return None
    table = Table(
        box=None, pad_edge=False, padding=(0, 1), collapse_padding=True,
        title=f"top by {title}", title_style=palette.text_muted, title_justify="left",
    )
    # 7 digits covers the kernel default pid_max; never let it truncate.
    table.add_column("pid", justify="right", width=7, no_wrap=True,
                     style=palette.text_muted)
    table.add_column(title.replace(" ", "\n"), justify="right", width=9,
                     no_wrap=True)
    table.add_column("command", overflow="ellipsis", no_wrap=True, max_width=64)
    for row in rows:
        via = str(row.get("via", "-"))
        command = str(row.get("cmdline") or row.get("name") or "")
        table.add_row(
            str(row.get("pid", "?")),
            formatter(as_float(row.get(field))),
            Text.assemble(
                (f"{via} ", discovery_color(via=via, palette=palette)),
                (truncate(command, width=80), palette.text_secondary),
            ),
        )
    return table
