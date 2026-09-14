"""CSV output for spreadsheets and pandas.

Two files, because the data has two shapes: one row per sample in ``<base>.csv``
and one row per process per sample in ``<base>.procs.csv``, joinable on ``seq``.

Rows are appended and the file closed again as each sample arrives, so a run
that is killed still leaves every row it recorded on disk.
"""

from __future__ import annotations

import csv
import json
import pathlib
from collections.abc import Iterable, Mapping, Sequence

from treehawk.core.compat import override
from treehawk.core.config import LogDetail
from treehawk.core.interfaces import Record
from treehawk.core.values import as_records
from treehawk.sinks.base import BaseSink

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

    def __init__(self, path: str, *, detail: LogDetail = LogDetail.PER_PROCESS) -> None:
        given = pathlib.Path(path)
        base = str(given.with_suffix(""))
        self._path = path if given.suffix else path + ".csv"
        self._procs_path = f"{base}.procs.csv" if detail is LogDetail.PER_PROCESS else None
        self._header_path = f"{base}.header.json"
        self._summary_path = f"{base}.summary.json"
        self._writing = False

    @property
    def paths(self) -> list[str]:
        return [p for p in (self._path, self._procs_path, self._header_path) if p]

    @override
    def open(self, header: Record) -> None:
        # CSV has nowhere to put a header record, so run metadata goes beside it.
        _write_json(self._header_path, header)
        _start_table(self._path, columns=SAMPLE_COLUMNS)
        if self._procs_path:
            _start_table(self._procs_path, columns=PROC_COLUMNS)
        self._writing = True

    @override
    def sample(self, record: Record) -> None:
        if not self._writing:
            return
        _append_rows(self._path, columns=SAMPLE_COLUMNS, rows=[record])
        if self._procs_path:
            stamp = {"seq": record.get("seq"), "t": record.get("t"), "ts": record.get("ts")}
            rows = [{**proc, **stamp} for proc in as_records(record.get("procs"))]
            _append_rows(self._procs_path, columns=PROC_COLUMNS, rows=rows)

    @override
    def close(self, summary: Record) -> None:
        self._writing = False
        _write_json(self._summary_path, summary)


def _write_json(path: str, record: Record) -> None:
    with pathlib.Path(path).open("w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, default=str)


def _start_table(path: str, *, columns: Sequence[str]) -> None:
    """Create ``path`` holding only the header row, replacing any earlier file."""
    with pathlib.Path(path).open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=columns).writeheader()


def _append_rows(path: str, *, columns: Sequence[str], rows: Iterable[Mapping[str, object]]) -> None:
    with pathlib.Path(path).open("a", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore").writerows(rows)
