"""Named values for the output sinks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class SinkConstants:
    """Special paths and the column layout of the CSV output."""

    stdout_path: str = "-"
    """The path that means "write to stdout" rather than to a file."""

    csv_sample_columns: tuple[str, ...] = (
        "seq",
        "t",
        "ts",
        "n_procs",
        "cpu_percent",
        "cpu_percent_norm",
        "cpu_seconds_total",
        "cpu_seconds_used",
        "rss_bytes",
        "pss_bytes",
        "swap_bytes",
        "group_memory_bytes",
        "group_memory_peak_bytes",
        "overrun",
    )
    """Columns of ``<base>.csv``: one row per sample."""

    csv_proc_columns: tuple[str, ...] = (
        "seq",
        "t",
        "ts",
        "pid",
        "ppid",
        "starttime",
        "name",
        "state",
        "threads",
        "cpu_percent",
        "cpu_seconds",
        "rss_bytes",
        "pss_bytes",
        "swap_bytes",
        "via",
        "cmdline",
    )
    """Columns of ``<base>.procs.csv``: one row per process per sample."""


SINKS: Final = SinkConstants()
