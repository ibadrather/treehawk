"""Terminal views of whole-machine tracking: the live table and the report.

Pure renderers over records and :class:`~treehawk.top.models.TopHistory`, so
both are tested by rendering to a string.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from datetime import datetime

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from treehawk.core.humanize import format_bytes, format_percent, format_seconds, truncate
from treehawk.core.interfaces import Record
from treehawk.core.models import Identity
from treehawk.core.values import as_float, as_int, as_mapping, as_sequence
from treehawk.top.constants import TOP
from treehawk.top.history import largest_spikes, leak_suspects, ranked_processes
from treehawk.top.models import EventKind, ProcessHistory, Resource, TopHistory
from treehawk.ui.constants import UI
from treehawk.ui.models import Palette


def local_time(epoch: float | None) -> str:
    """Unix seconds as local wall-clock time, to the second."""
    if epoch is None:
        return "-"
    return datetime.fromtimestamp(epoch).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def format_value(resource: str, value: float | None) -> str:
    """A reading in the unit its resource is measured in."""
    if resource == str(Resource.CPU):
        return format_percent(value)
    return format_bytes(value)


def describe_event(record: Record) -> str:
    """One event as a line of text, for the live view and plain output."""
    kind = str(record.get("kind", "?"))
    resource = str(record.get("resource", ""))
    who = f"{record.get('name', '?')} ({record.get('pid', '?')})"
    if kind == str(EventKind.SPIKE):
        value = format_value(resource, as_float(record.get("value")))
        baseline = format_value(resource, as_float(record.get("baseline")))
        return f"spike  {who}: {resource} {value}, usually {baseline}"
    if kind == str(EventKind.CREEP):
        slope = format_bytes(as_float(record.get("slope_per_hour")))
        grown = f"{format_bytes(as_float(record.get('baseline')))} → {format_bytes(as_float(record.get('value')))}"
        return f"creep  {who}: memory rising {slope}/h, {grown}"
    if kind == str(EventKind.LEAVE):
        return f"leave  {who} ({record.get('reason', '?')})"
    return f"{kind:<6} {who}"


class TopBoard:
    """The live ``top`` view: the machine's line, the top N, the latest events."""

    def __init__(self, *, palette: Palette, events: int = UI.top_events) -> None:
        self._palette = palette
        self._header: Record = {}
        self._fields: Sequence[object] = TOP.compact_row
        self._names: dict[Identity, tuple[str, str]] = {}
        self._latest: Record | None = None
        self._events: deque[str] = deque(maxlen=events)

    def start(self, header: Record) -> None:
        self._header = header
        self._fields = as_sequence(header.get("row_fields")) or TOP.compact_row

    def update(self, record: Record) -> None:
        kind = record.get("type")
        if kind == "proc":
            identity = (as_int(record.get("pid")) or 0, as_int(record.get("starttime")) or 0)
            self._names[identity] = (str(record.get("name") or "?"), str(record.get("cmdline") or ""))
        elif kind == "sample":
            self._latest = record
        elif kind == "event" and record.get("kind") in {str(EventKind.SPIKE), str(EventKind.CREEP)}:
            self._events.append(f"{str(record.get('ts', ''))[11:19]}  {describe_event(record)}")

    def render(self) -> RenderableType:
        palette = self._palette
        latest = self._latest or {}
        host = as_mapping(latest.get("host"))
        total = as_mapping(self._header.get("host")).get("mem_total_bytes")
        facts = Text.assemble(
            ("cpu ", palette.text_muted),
            (format_percent(as_float(host.get("cpu_percent"))), f"bold {palette.slot(0)}"),
            ("   mem ", palette.text_muted),
            (format_bytes(as_float(host.get("mem_used_bytes"))), f"bold {palette.slot(2)}"),
            (f" of {format_bytes(as_float(total))}" if total else "", palette.text_secondary),
            ("   procs ", palette.text_muted),
            (str(latest.get("n_procs", "-")), palette.text_secondary),
            ("   every ", palette.text_muted),
            (f"{as_float(latest.get('interval')) or 0:.2f}s", palette.text_secondary),
        )
        table = Table(box=None, pad_edge=False, padding=(0, 1), expand=True)
        table.add_column("pid", justify="right", min_width=7, no_wrap=True, style=palette.text_muted)
        table.add_column("cpu", justify="right", width=8, no_wrap=True)
        table.add_column("rss", justify="right", width=9, no_wrap=True)
        table.add_column("fair", justify="right", width=9, no_wrap=True)
        table.add_column("why", width=3, no_wrap=True, style=palette.text_muted)
        table.add_column("command", overflow="ellipsis", no_wrap=True, ratio=1)
        for raw in as_sequence(latest.get("top")):
            row = {str(name): value for name, value in zip(self._fields, as_sequence(raw), strict=False)}
            identity = (as_int(row.get("pid")) or 0, as_int(row.get("starttime")) or 0)
            name, cmdline = self._names.get(identity, ("?", ""))
            table.add_row(
                str(identity[0]),
                format_percent(as_float(row.get("cpu_percent"))),
                format_bytes(as_float(row.get("rss_bytes"))),
                format_bytes(as_float(row.get("pss_bytes"))),
                str(row.get("reasons") or ""),
                Text(truncate(cmdline or name, width=120), style=palette.text_secondary),
            )
        parts: list[RenderableType] = [facts, table]
        if self._events:
            parts.append(Text("\n".join(self._events), style=palette.warning))
        return Panel(
            Group(*parts),
            title=f"top {self._header.get('top_n', '?')} · {as_mapping(self._header.get('host')).get('hostname', '')}",
            title_align="left",
            border_style=palette.grid,
            padding=(0, 1),
        )


def render_top_report(*, history: TopHistory, palette: Palette, title: str = "top") -> RenderableType:
    """What happened on the machine, and who was responsible."""
    parts: list[RenderableType] = [_facts(history=history, palette=palette)]
    by_cpu, by_memory = ranked_processes(history)
    parts.extend(
        (
            _consumers(title="top by cpu time while ranked", rows=by_cpu, palette=palette, cpu=True),
            _consumers(title="top by peak rss", rows=by_memory, palette=palette, cpu=False),
        )
    )
    for resource in (Resource.CPU, Resource.MEMORY):
        spikes = largest_spikes(history.events, resource=resource)
        if spikes:
            parts.append(_spikes(rows=spikes, resource=resource, palette=palette))
    creeps = leak_suspects(history.events)
    if creeps:
        parts.append(_creeps(rows=creeps, palette=palette))
    return Panel(Group(*parts), title=title, title_align="left", border_style=palette.grid, padding=(0, 1))


def _facts(*, history: TopHistory, palette: Palette) -> RenderableType:
    header = history.header
    host = as_mapping(header.get("host"))
    table = Table.grid(padding=(0, 2))
    table.add_column(style=palette.text_muted, width=9)
    table.add_column()
    table.add_row(
        "host",
        f"{host.get('hostname', '?')} · {host.get('ncpu', '?')} cpus · "
        f"{format_bytes(as_float(host.get('mem_total_bytes')))} ram",
    )
    span = (history.last_time or 0.0) - (history.first_time or 0.0)
    table.add_row(
        "span",
        f"{local_time(history.first_time)} → {local_time(history.last_time)} ({format_seconds(span)}) · "
        f"{history.samples} samples in {history.segments} segment(s)",
    )
    table.add_row(
        "cpu",
        Text.assemble(
            (f"{format_percent(history.peak_host_cpu_percent)} peak", f"bold {palette.slot(0)}"),
            (f"  {format_percent(history.mean_host_cpu_percent)} mean, of all cores", palette.text_secondary),
        ),
    )
    table.add_row(
        "memory",
        Text(f"{format_bytes(history.peak_host_mem_used_bytes)} peak in use", style=f"bold {palette.slot(2)}"),
    )
    counts = history.event_counts
    table.add_row(
        "events",
        " · ".join(f"{counts.get(str(kind), 0)} {kind}" for kind in EventKind),
    )
    if history.overruns:
        table.add_row(
            "warning",
            Text(f"{history.overruns} sample(s) arrived late - the interval is too short", style=palette.warning),
        )
    return table


def _titled(title: str, palette: Palette) -> Table:
    return Table(
        box=None,
        expand=True,
        pad_edge=False,
        padding=(0, 1),
        collapse_padding=True,
        title=title,
        title_style=palette.text_muted,
        title_justify="left",
    )


def _consumers(*, title: str, rows: Sequence[ProcessHistory], palette: Palette, cpu: bool) -> RenderableType:
    table = _titled(title, palette)
    table.add_column("pid", justify="right", min_width=7, no_wrap=True, style=palette.text_muted)
    table.add_column("cpu time" if cpu else "peak rss", justify="right", min_width=9, no_wrap=True)
    table.add_column("peak cpu" if cpu else "first → last", justify="right", min_width=8, no_wrap=True)
    table.add_column("command", overflow="ellipsis", no_wrap=True, ratio=1)
    for entry in rows:
        if cpu:
            figure, detail = format_seconds(entry.cpu_seconds), format_percent(entry.peak_cpu_percent)
        else:
            figure = format_bytes(entry.peak_rss_bytes)
            detail = f"{format_bytes(entry.first_rss_bytes)} → {format_bytes(entry.last_rss_bytes)}"
        table.add_row(
            str(entry.pid), figure, detail, Text(truncate(entry.cmdline, width=90), style=palette.text_secondary)
        )
    return table


def _spikes(*, rows: Sequence[Record], resource: Resource, palette: Palette) -> RenderableType:
    label = "cpu" if resource is Resource.CPU else "memory"
    table = _titled(f"largest {label} spikes", palette)
    table.add_column("when", min_width=19, no_wrap=True, style=palette.text_muted)
    table.add_column("pid", justify="right", min_width=7, no_wrap=True, style=palette.text_muted)
    table.add_column("reached", justify="right", min_width=8, no_wrap=True)
    table.add_column("usually", justify="right", min_width=8, no_wrap=True, style=palette.text_secondary)
    table.add_column("who", overflow="ellipsis", no_wrap=True, ratio=1)
    for event in rows:
        table.add_row(
            local_time(as_float(event.get("time"))),
            str(event.get("pid", "?")),
            Text(format_value(str(resource), as_float(event.get("value"))), style=f"bold {palette.critical}"),
            format_value(str(resource), as_float(event.get("baseline"))),
            Text(truncate(str(event.get("cmdline") or event.get("name") or ""), width=80)),
        )
    return table


def _creeps(*, rows: Sequence[Record], palette: Palette) -> RenderableType:
    table = _titled("leak suspects: memory rising steadily", palette)
    table.add_column("since", min_width=19, no_wrap=True, style=palette.text_muted)
    table.add_column("pid", justify="right", min_width=7, no_wrap=True, style=palette.text_muted)
    table.add_column("rate", justify="right", min_width=10, no_wrap=True)
    table.add_column("grew", justify="right", min_width=12, no_wrap=True, style=palette.text_secondary)
    table.add_column("fit r²", justify="right", no_wrap=True, style=palette.text_muted)
    table.add_column("who", overflow="ellipsis", no_wrap=True, ratio=1)
    for event in rows:
        table.add_row(
            local_time(as_float(event.get("time"))),
            str(event.get("pid", "?")),
            Text(f"{format_bytes(as_float(event.get('slope_per_hour')))}/h", style=f"bold {palette.warning}"),
            f"{format_bytes(as_float(event.get('baseline')))} → {format_bytes(as_float(event.get('value')))}",
            f"{as_float(event.get('r2')) or 0:.2f}",
            Text(truncate(str(event.get("cmdline") or event.get("name") or ""), width=80)),
        )
    return table
