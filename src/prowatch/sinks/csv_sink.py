"""CSV output for spreadsheets and pandas.

Two files, because the data has two shapes: one row per sample in ``<base>.csv``
and one row per process per sample in ``<base>.procs.csv``, joinable on ``seq``.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Mapping

from .base import BaseSink

SAMPLE_COLUMNS = (
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

PROC_COLUMNS = (
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


class CsvSink(BaseSink):
    """Writes the aggregate (and optionally per-process) rows as CSV."""

    def __init__(self, path: str, *, per_process: bool = True) -> None:
        base, ext = os.path.splitext(path)
        self._path = path if ext else path + ".csv"
        self._procs_path = f"{base}.procs.csv" if per_process else None
        self._header_path = f"{base}.header.json"
        self._per_process = per_process
        self._handle = None
        self._writer = None
        self._procs_handle = None
        self._procs_writer = None

    @property
    def paths(self) -> list[str]:
        return [p for p in (self._path, self._procs_path, self._header_path) if p]

    def open(self, header: Mapping[str, object]) -> None:
        # CSV has nowhere to put a header record, so run metadata goes beside it.
        with open(self._header_path, "w", encoding="utf-8") as handle:
            json.dump(header, handle, indent=2, default=str)

        self._handle = open(self._path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(
            self._handle, fieldnames=list(SAMPLE_COLUMNS), extrasaction="ignore"
        )
        self._writer.writeheader()

        if self._procs_path:
            self._procs_handle = open(
                self._procs_path, "w", newline="", encoding="utf-8"
            )
            self._procs_writer = csv.DictWriter(
                self._procs_handle, fieldnames=list(PROC_COLUMNS), extrasaction="ignore"
            )
            self._procs_writer.writeheader()

    def sample(self, record: Mapping[str, object]) -> None:
        if self._writer is not None:
            self._writer.writerow(record)
            self._handle.flush()
        if self._procs_writer is not None:
            for proc in record.get("procs", ()) or ():
                row = dict(proc)
                row["seq"] = record.get("seq")
                row["t"] = record.get("t")
                row["ts"] = record.get("ts")
                self._procs_writer.writerow(row)
            self._procs_handle.flush()

    def close(self, summary: Mapping[str, object]) -> None:
        for handle in (self._handle, self._procs_handle):
            if handle is not None:
                handle.close()
        self._handle = self._procs_handle = None
        self._writer = self._procs_writer = None
        base, _ = os.path.splitext(self._path)
        with open(f"{base}.summary.json", "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, default=str)
