"""The live terminal dashboard.

A pure renderer: it is handed record dictionaries and returns something Rich can
draw. It holds the little history a sparkline needs and nothing else - no files,
no timers, no process access - which is what lets it be unit-tested by rendering
to a string.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from typing import Final

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from treehawk.core.humanize import (
    format_bytes,
    format_percent,
    format_seconds,
    truncate,
)
from treehawk.core.interfaces import Record
from treehawk.core.records import target_of
from treehawk.core.values import as_float, as_int, as_mapping, as_records
from treehawk.ui.theme import Palette, discovery_color
from treehawk.ui.widgets import elapsed_clock, sparkline

HISTORY: Final = 60
"""Samples kept for the sparklines, and the width of the column that shows
them. Bounded: a watch may run for days."""


class Dashboard:
    """Builds the renderable shown while a workload is being watched."""

    def __init__(
        self,
        *,
        palette: Palette,
        max_rows: int = 12,
        history: int = HISTORY,
    ) -> None:
        self._palette = palette
        self._max_rows = max_rows
        self._cpu: deque[float | None] = deque(maxlen=history)
        self._memory: deque[float | None] = deque(maxlen=history)
        self._header: Record = {}
        self._latest: Record | None = None
        self._peak_cpu: float | None = None
        self._peak_memory: int | None = None
        self._peak_procs = 0
        self._samples = 0
        self._overruns = 0

    # -- state ------------------------------------------------------------

    @property
    def cpu_history(self) -> list[float | None]:
        """The CPU readings the sparkline draws from, oldest first."""
        return list(self._cpu)

    def start(self, header: Record) -> None:
        self._header = header

    def update(self, record: Record) -> None:
        self._latest = record
        self._samples += 1
        cpu = as_float(record.get("cpu_percent"))
        self._cpu.append(cpu)
        if cpu is not None and (self._peak_cpu is None or cpu > self._peak_cpu):
            self._peak_cpu = cpu
        memory = _memory_reading(record)
        self._memory.append(None if memory is None else float(memory))
        if memory is not None and (self._peak_memory is None or memory > self._peak_memory):
            self._peak_memory = memory
        self._peak_procs = max(self._peak_procs, as_int(record.get("n_procs")) or 0)
        if record.get("overrun"):
            self._overruns += 1

    # -- rendering --------------------------------------------------------

    def render(self) -> RenderableType:
        parts: list[RenderableType] = [self._title(), self._stats()]
        table = self._processes()
        if table is not None:
            parts.append(table)
        return Group(*parts)

    def _title(self) -> RenderableType:
        header = self._header
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(str(header.get("mode", "watch")), style=f"bold {self._accent(0)}")
        line.append("  ")
        line.append(target_of(header), style=self._muted)
        detail = Text(overflow="ellipsis", no_wrap=True)
        boundary = header.get("group_path")
        detail.append(
            "cgroup " if boundary else "tracking ",
            style=self._muted,
        )
        detail.append(
            str(boundary).rsplit("/", 1)[-1] if boundary else "via /proc",
            style=self._secondary,
        )
        detail.append("   every ", style=self._muted)
        detail.append(f"{header.get('interval', '?')}s", style=self._secondary)
        return Panel(
            Group(line, detail),
            title="treehawk",
            border_style=self._palette.grid,
            padding=(0, 1),
        )

    def _stats(self) -> RenderableType:
        record = self._latest or {}
        table = Table.grid(padding=(0, 2))
        table.add_column(style=self._muted, width=4)
        table.add_column(justify="right", width=11)
        table.add_column(width=HISTORY)
        table.add_column(style=self._muted)

        ncpu = as_mapping(self._header.get("host")).get("ncpu", "?")
        normalised = as_float(record.get("cpu_percent_norm"))
        table.add_row(
            "cpu",
            Text(
                format_percent(as_float(record.get("cpu_percent"))),
                style=f"bold {self._accent(0)}",
            ),
            Text(sparkline(list(self._cpu)), style=self._accent(0)),
            f"peak {format_percent(self._peak_cpu)} · {format_percent(normalised)} of {ncpu} cpus",
        )
        memory = _memory_reading(record)
        table.add_row(
            "mem",
            Text(format_bytes(memory), style=f"bold {self._accent(2)}"),
            Text(sparkline(list(self._memory)), style=self._accent(2)),
            f"peak {format_bytes(self._peak_memory)} · {_memory_source(record)}",
        )
        elapsed = as_float(record.get("t")) or 0.0
        procs = as_int(record.get("n_procs")) or 0
        cpu_time = format_seconds(as_float(record.get("cpu_seconds_used")))
        table.add_row(
            "run",
            Text(elapsed_clock(elapsed), style="bold"),
            Text(
                f"{procs} process{'' if procs == 1 else 'es'}",
                style=self._secondary,
            ),
            f"peak {self._peak_procs} · {self._samples} samples · "
            f"{cpu_time} cpu time" + (f" · {self._overruns} overrun" if self._overruns else ""),
        )
        return Panel(table, border_style=self._palette.grid, padding=(0, 1))

    def _processes(self) -> RenderableType | None:
        procs: Sequence[Record] = as_records((self._latest or {}).get("procs"))
        if not procs:
            return None
        table = Table(
            box=None,
            pad_edge=False,
            expand=True,
            header_style=self._muted,
            padding=(0, 1),
        )
        table.add_column("pid", justify="right", width=7)
        table.add_column("cpu", justify="right", width=7)
        table.add_column("rss", justify="right", width=9)
        table.add_column("pss", justify="right", width=9)
        table.add_column("thr", justify="right", width=4)
        table.add_column("found", width=8)
        table.add_column("command", overflow="ellipsis", no_wrap=True)

        ordered = sorted(procs, key=_cpu_percent_of, reverse=True)
        for proc in ordered[: self._max_rows]:
            via = str(proc.get("via", "-"))
            command = str(proc.get("cmdline") or proc.get("name") or "")
            table.add_row(
                str(proc.get("pid", "?")),
                format_percent(as_float(proc.get("cpu_percent"))),
                format_bytes(as_float(proc.get("rss_bytes"))),
                format_bytes(as_float(proc.get("pss_bytes"))),
                str(proc.get("threads", "-")),
                Text(via, style=discovery_color(via=via, palette=self._palette)),
                truncate(command, width=160),
            )
        hidden = len(ordered) - self._max_rows
        if hidden > 0:
            table.add_row("", "", "", "", "", "", f"… {hidden} more")
        return Panel(
            table,
            title="processes",
            title_align="left",
            border_style=self._palette.grid,
            padding=(0, 1),
        )

    # -- styles -----------------------------------------------------------

    def _accent(self, slot: int) -> str:
        return self._palette.slot(slot)

    @property
    def _muted(self) -> str:
        return self._palette.text_muted

    @property
    def _secondary(self) -> str:
        return self._palette.text_secondary


def _cpu_percent_of(proc: Record) -> float:
    """Sort key: a process with no rate yet sorts below one that has any."""
    value = as_float(proc.get("cpu_percent"))
    return value if value is not None else -1.0


def _memory_reading(record: Record) -> int | None:
    """The best memory figure in this sample, in the order we trust them."""
    for key in ("group_memory_bytes", "pss_bytes", "rss_bytes"):
        value = as_int(record.get(key))
        if value is not None:
            return value
    return None


def _memory_source(record: Record) -> str:
    """Which measure :func:`_memory_reading` actually found."""
    if record.get("group_memory_bytes") is not None:
        return "cgroup"
    if record.get("pss_bytes") is not None:
        return "pss"
    return "rss"
