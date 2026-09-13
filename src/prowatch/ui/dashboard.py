"""The live terminal dashboard.

A pure renderer: it is handed record dictionaries and returns something Rich can
draw. It holds the little history a sparkline needs and nothing else - no files,
no timers, no process access - which is what lets it be unit-tested by rendering
to a string.
"""

from __future__ import annotations

from collections import deque
from typing import Final, Iterable, Sequence

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from prowatch.core.humanize import bytes_human, seconds_human, truncate
from prowatch.core.interfaces import Record
from prowatch.ui.theme import Palette, discovery_color
from prowatch.ui.widgets import elapsed_clock, sparkline

HISTORY: Final = 60
"""Samples kept for the sparklines. Bounded: a watch may run for days."""


class Dashboard:
    """Builds the renderable shown while a workload is being watched."""

    def __init__(
        self,
        palette: Palette,
        *,
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

    def start(self, header: Record) -> None:
        self._header = header

    def update(self, record: Record) -> None:
        self._latest = record
        self._samples += 1
        cpu = _number(record.get("cpu_percent"))
        self._cpu.append(cpu)
        if cpu is not None and (self._peak_cpu is None or cpu > self._peak_cpu):
            self._peak_cpu = cpu
        memory = _memory_of(record)
        self._memory.append(None if memory is None else float(memory))
        if memory is not None and (
            self._peak_memory is None or memory > self._peak_memory
        ):
            self._peak_memory = memory
        self._peak_procs = max(self._peak_procs, int(record.get("n_procs") or 0))
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
        target = _target_of(header)
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(str(header.get("mode", "watch")), style=f"bold {self._accent(0)}")
        line.append("  ")
        line.append(target, style=self._muted)
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
            title="prowatch",
            border_style=self._palette.grid,
            padding=(0, 1),
        )

    def _stats(self) -> RenderableType:
        record = self._latest or {}
        table = Table.grid(padding=(0, 2))
        table.add_column(style=self._muted, width=4)
        table.add_column(justify="right", width=11)
        table.add_column(width=HISTORY_COLUMN)
        table.add_column(style=self._muted)

        cpu = _number(record.get("cpu_percent"))
        table.add_row(
            "cpu",
            Text(_percent(cpu), style=f"bold {self._accent(0)}"),
            Text(sparkline(list(self._cpu)), style=self._accent(0)),
            f"peak {_percent(self._peak_cpu)} · {_percent(record.get('cpu_percent_norm'))}"
            f" of {self._header.get('host', {}).get('ncpu', '?')} cpus",
        )
        memory = _memory_of(record)
        table.add_row(
            "mem",
            Text(bytes_human(memory), style=f"bold {self._accent(2)}"),
            Text(sparkline(list(self._memory)), style=self._accent(2)),
            f"peak {bytes_human(self._peak_memory)} · {_memory_source(record)}",
        )
        elapsed = float(record.get("t") or 0.0)
        procs = int(record.get("n_procs") or 0)
        table.add_row(
            "run",
            Text(elapsed_clock(elapsed), style="bold"),
            Text(
                f"{procs} process{'' if procs == 1 else 'es'}",
                style=self._secondary,
            ),
            f"peak {self._peak_procs} · {self._samples} samples · "
            f"{seconds_human(record.get('cpu_seconds_used'))} cpu time"
            + (f" · {self._overruns} overrun" if self._overruns else ""),
        )
        return Panel(table, border_style=self._palette.grid, padding=(0, 1))

    def _processes(self) -> RenderableType | None:
        procs: Sequence[Record] = (self._latest or {}).get("procs") or ()
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

        ordered = sorted(procs, key=_by_cpu, reverse=True)
        for proc in ordered[: self._max_rows]:
            via = str(proc.get("via", "-"))
            table.add_row(
                str(proc.get("pid", "?")),
                _percent(_number(proc.get("cpu_percent"))),
                bytes_human(proc.get("rss_bytes")),
                bytes_human(proc.get("pss_bytes")),
                str(proc.get("threads", "-")),
                Text(via, style=discovery_color(via, self._palette)),
                truncate(str(proc.get("cmdline") or proc.get("name") or ""), 160),
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


HISTORY_COLUMN: Final = HISTORY


def _number(value: object) -> float | None:
    """Records come from JSON, where a field may legitimately be null."""
    return float(value) if isinstance(value, (int, float)) else None


def _by_cpu(proc: Record) -> float:
    value = _number(proc.get("cpu_percent"))
    return value if value is not None else -1.0


def _memory_of(record: Record) -> int | None:
    """The best memory figure available, in the order we trust them."""
    for key in ("group_memory_bytes", "pss_bytes", "rss_bytes"):
        value = record.get(key)
        if value is not None:
            return int(value)
    return None


def _memory_source(record: Record) -> str:
    if record.get("group_memory_bytes") is not None:
        return "cgroup"
    if record.get("pss_bytes") is not None:
        return "pss"
    return "rss"


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _target_of(header: Record) -> str:
    argv: Iterable[str] = header.get("argv") or ()
    joined = " ".join(argv)
    if joined:
        return joined
    matcher = header.get("matcher") or {}
    return str(matcher.get("value", "?"))
