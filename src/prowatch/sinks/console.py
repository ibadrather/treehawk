"""Human-readable progress on stderr.

stderr, not stdout, so that ``--output -`` can stream JSON to a pipe while the
user still sees what is happening.
"""

from __future__ import annotations

import sys
from typing import IO

from prowatch.core.humanize import bytes_human, percent_human, seconds_human, truncate
from prowatch.core.interfaces import Record
from prowatch.sinks.base import BaseSink


class ConsoleSink(BaseSink):
    """Prints one line per sample, plus a short report at the end."""

    def __init__(self, stream: IO[str] | None = None, *, show_procs: int = 0) -> None:
        self._stream: IO[str] = stream or sys.stderr
        self._show_procs = show_procs

    def open(self, header: Record) -> None:
        matcher = header.get("matcher") or {}
        target = matcher.get("value") if isinstance(matcher, dict) else None
        if header.get("mode") == "run":
            target = " ".join(header.get("argv") or [])
        group = header.get("group_path")
        self._say(
            f"prowatch {header.get('prowatch_version')} | mode={header.get('mode')} "
            f"| target={target!r} | interval={header.get('interval')}s"
        )
        if group:
            self._say(f"accounting boundary: {group}")
        for note in header.get("notes") or ():
            self._say(f"note: {note}")

    def sample(self, record: Record) -> None:
        memory = record.get("group_memory_bytes") or record.get("pss_bytes")
        label = "cg" if record.get("group_memory_bytes") else "pss"
        self._say(
            f"[{record.get('t'):>8.2f}s] procs={record.get('n_procs'):<4} "
            f"cpu={percent_human(record.get('cpu_percent')):>8} "
            f"rss={bytes_human(record.get('rss_bytes')):>9} "
            f"{label}={bytes_human(memory):>9} "
            f"cpu_time={seconds_human(record.get('cpu_seconds_used'))}"
            + (" OVERRUN" if record.get("overrun") else "")
        )
        for proc in (record.get("procs") or ())[: self._show_procs]:
            self._say(
                f"    pid={proc['pid']:<8} via={proc['via']:<8} "
                f"cpu={percent_human(proc.get('cpu_percent')):>7} "
                f"rss={bytes_human(proc.get('rss_bytes')):>9} "
                f"{truncate(proc.get('cmdline') or proc.get('name') or '', 60)}"
            )

    def close(self, summary: Record) -> None:
        self._say("")
        self._say(
            f"samples={summary.get('samples')} "
            f"duration={seconds_human(summary.get('duration_s'))} "
            f"processes_seen={summary.get('total_procs_seen')} "
            f"peak_procs={summary.get('peak_n_procs')}"
        )
        self._say(
            f"cpu: peak={percent_human(summary.get('peak_cpu_percent'))} "
            f"mean={percent_human(summary.get('mean_cpu_percent'))} "
            f"total={seconds_human(summary.get('cpu_seconds_used'))} of cpu time"
        )
        self._say(
            f"memory: peak_rss={bytes_human(summary.get('peak_rss_bytes'))} "
            f"peak_pss={bytes_human(summary.get('peak_pss_bytes'))} "
            f"peak_cgroup={bytes_human(summary.get('peak_group_memory_bytes'))}"
        )
        if summary.get("overruns"):
            self._say(
                f"warning: {summary['overruns']} sample(s) took longer than the "
                "interval - consider a larger --interval or --no-pss"
            )

    def _say(self, text: str) -> None:
        print(text, file=self._stream, flush=True)
