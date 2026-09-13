"""End-of-run and post-hoc views.

One renderer serves both: the summary shown when a watch finishes and the output
of ``prowatch report`` are the same information, so they are the same code.
"""

from __future__ import annotations

from typing import Sequence

from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from prowatch.core.humanize import bytes_human, seconds_human, truncate
from prowatch.core.interfaces import Record
from prowatch.ui.theme import Palette, discovery_color


def render_summary(
    summary: Record,
    header: Record,
    palette: Palette,
    *,
    title: str = "summary",
) -> RenderableType:
    """The figures people ask for, plus who was responsible for them."""
    parts: list[RenderableType] = [_facts(summary, header, palette)]
    tables = [
        _ranking(summary.get("top_by_cpu") or [], "cpu time", "cpu_seconds",
                 seconds_human, palette),
        _ranking(summary.get("top_by_memory") or [], "peak rss", "peak_rss_bytes",
                 bytes_human, palette),
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


def render_header_facts(header: Record, palette: Palette) -> RenderableType:
    """Run metadata, for the report command where there is no live panel."""
    host = header.get("host") or {}
    matcher = header.get("matcher") or {}
    target = " ".join(header.get("argv") or ()) or str(matcher.get("value", "?"))
    table = Table.grid(padding=(0, 2))
    table.add_column(style=palette.text_muted, width=9)
    table.add_column()
    table.add_row("target", Text(target, style=f"bold {palette.slot(0)}"))
    table.add_row(
        "run",
        f"{header.get('mode', '?')} · every {header.get('interval', '?')}s · "
        f"started {header.get('started_at', '?')}",
    )
    table.add_row(
        "host",
        f"{host.get('hostname', '?')} · {host.get('ncpu', '?')} cpus · "
        f"{bytes_human(host.get('mem_total_bytes'))} ram",
    )
    boundary = header.get("group_path")
    table.add_row("cgroup", str(boundary) if boundary else Text("none", style=palette.text_muted))
    for note in header.get("notes") or ():
        table.add_row("note", Text(str(note), style=palette.warning))
    return table


def _facts(summary: Record, header: Record, palette: Palette) -> RenderableType:
    ncpu = (header.get("host") or {}).get("ncpu")
    table = Table.grid(padding=(0, 2))
    table.add_column(style=palette.text_muted, width=9)
    table.add_column()
    table.add_row(
        "cpu",
        Text.assemble(
            (f"{_percent(summary.get('peak_cpu_percent'))} peak", f"bold {palette.slot(0)}"),
            (f"  {_percent(summary.get('mean_cpu_percent'))} mean", palette.text_secondary),
            (f"  {seconds_human(summary.get('cpu_seconds_used'))} of cpu time",
             palette.text_secondary),
            (f"  on {ncpu} cpus" if ncpu else "", palette.text_muted),
        ),
    )
    table.add_row(
        "memory",
        Text.assemble(
            (f"{bytes_human(summary.get('peak_rss_bytes'))} peak rss",
             f"bold {palette.slot(2)}"),
            (f"  {bytes_human(summary.get('peak_pss_bytes'))} peak pss",
             palette.text_secondary),
            (f"  {bytes_human(summary.get('peak_group_memory_bytes'))} peak cgroup",
             palette.text_secondary),
        ),
    )
    table.add_row(
        "run",
        f"{summary.get('samples', 0)} samples over "
        f"{seconds_human(summary.get('duration_s'))} · "
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
    rows: Sequence[Record],
    title: str,
    field: str,
    render: object,
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
    formatter = render  # a humanize function; typed loosely to serve both columns
    for row in rows:
        via = str(row.get("via", "-"))
        table.add_row(
            str(row.get("pid", "?")),
            str(formatter(row.get(field))),  # type: ignore[operator]
            Text.assemble(
                (f"{via} ", discovery_color(via, palette)),
                (truncate(str(row.get("cmdline") or row.get("name") or ""), 80),
                 palette.text_secondary),
            ),
        )
    return table


def _percent(value: object) -> str:
    return "-" if not isinstance(value, (int, float)) else f"{float(value):.1f}%"
