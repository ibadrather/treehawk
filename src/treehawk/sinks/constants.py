"""Named values for the output sinks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class SinkConstants:
    """Special paths and the column layout of the CSV output."""

    stdout_path: str = "-"
    """The path that means "write to stdout" rather than to a file."""

    workload_log_suffix: str = ".out"
    """Extension of the file holding everything a launched workload printed.
    It sits beside the log, under the same name, because the two are one run."""

    segment_prefix: str = "top-"
    """File name prefix of one ``top`` log segment."""

    segment_suffix: str = ".jsonl"
    archive_suffix: str = ".gz"
    """Added to a segment once it is closed and compressed."""

    unknown_boot: str = "boot-unknown"
    """Directory for segments from a machine that has no boot id."""

    boot_dir_length: int = 8
    """Characters of the boot id used to name its directory."""

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
