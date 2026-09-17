"""Human-readable progress on stderr.

stderr, not stdout, so that ``--output -`` can stream JSON to a pipe while the
user still sees what is happening.
"""

from __future__ import annotations

import sys
from typing import IO

from treehawk.core.compat import override
from treehawk.core.humanize import (
    format_bytes,
    format_percent,
    format_seconds,
    truncate,
)
from treehawk.core.interfaces import Record
from treehawk.core.models import MemoryMeasure
from treehawk.core.records import PSS, memory_measure, memory_reading, target_of
from treehawk.core.values import as_float, as_records, as_sequence
from treehawk.sinks.base import BaseSink


class ConsoleSink(BaseSink):
    """Prints one line per sample, plus a short report at the end."""

    def __init__(self, stream: IO[str] | None = None, *, show_procs: int = 0) -> None:
        self._stream: IO[str] = stream or sys.stderr
        self._show_procs = show_procs
        # sample() and close() never see the header, so the measure it names is
        # remembered here when the run opens.
        self._header: Record = {}
        self._measure: MemoryMeasure = PSS

    @override
    def open(self, header: Record) -> None:
        self._header = header
        self._measure = memory_measure(header)
        self._write_line(
            f"treehawk {header.get('treehawk_version')} "
            f"| mode={header.get('mode')} "
            f"| target={target_of(header)!r} "
            f"| interval={header.get('interval')}s"
        )
        boundary = header.get("group_path")
        if boundary:
            self._write_line(f"accounting boundary: {boundary}")
        for note in as_sequence(header.get("notes")):
            self._write_line(f"note: {note}")

    @override
    def sample(self, record: Record) -> None:
        reading = memory_reading(record=record, header=self._header)
        # RSS is always shown; the better figure only when there is one, so a
        # run without it prints one column rather than the same number twice.
        better = f"{reading.label}={format_bytes(reading.value):>9} " if reading.label != "rss" else ""
        elapsed = as_float(record.get("t")) or 0.0
        self._write_line(
            f"[{elapsed:>8.2f}s] procs={record.get('n_procs'):<4} "
            f"cpu={format_percent(as_float(record.get('cpu_percent'))):>8} "
            f"rss={format_bytes(as_float(record.get('rss_bytes'))):>9} "
            + better
            + f"cpu_time={format_seconds(as_float(record.get('cpu_seconds_used')))}"
            + (" OVERRUN" if record.get("overrun") else "")
        )
        for proc in as_records(record.get("procs"))[: self._show_procs]:
            self._write_line(
                f"    pid={proc['pid']:<8} via={proc['via']:<8} "
                f"cpu={format_percent(as_float(proc.get('cpu_percent'))):>7} "
                f"rss={format_bytes(as_float(proc.get('rss_bytes'))):>9} "
                + truncate(str(proc.get("cmdline") or proc.get("name") or ""), width=60)
            )

    @override
    def close(self, summary: Record) -> None:
        self._write_line("")
        self._write_line(
            f"samples={summary.get('samples')} "
            f"duration={format_seconds(as_float(summary.get('duration_s')))} "
            f"processes_seen={summary.get('total_procs_seen')} "
            f"peak_procs={summary.get('peak_n_procs')}"
        )
        self._write_line(
            f"cpu: peak={format_percent(as_float(summary.get('peak_cpu_percent')))} "
            f"mean={format_percent(as_float(summary.get('mean_cpu_percent')))} "
            f"total={format_seconds(as_float(summary.get('cpu_seconds_used')))} "
            "of cpu time"
        )
        self._write_line(
            f"memory: "
            f"peak_rss={format_bytes(as_float(summary.get('peak_rss_bytes')))} "
            f"peak_{self._measure.short}={format_bytes(as_float(summary.get('peak_pss_bytes')))} "
            f"peak_cgroup="
            f"{format_bytes(as_float(summary.get('peak_group_memory_bytes')))}"
        )
        if summary.get("overruns"):
            self._write_line(
                f"warning: {summary['overruns']} sample(s) took longer than the "
                "interval - consider a larger --interval, or --no-pss to skip "
                f"the {self._measure.long} read"
            )

    @override
    def output(self, line: str) -> None:
        """Pass a line the workload printed straight through.

        This sink is what a pipe or a CI log gets, and there is no cursor
        control to protect there: interleaved plain lines are exactly what the
        workload would have produced on its own.
        """
        self._write_line(line)

    def _write_line(self, text: str) -> None:
        print(text, file=self._stream, flush=True)
