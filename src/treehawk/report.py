"""Reading a treehawk log back.

Produces the figures people ask for - peak and mean CPU, peak memory, total CPU
time, and which process was responsible - as plain data. Rendering belongs to
:mod:`treehawk.ui.views` (terminal) and :mod:`treehawk.charts` (PDF), so one
reader serves both.

Uses the recorded summary when the run finished cleanly and recomputes from the
samples when it did not, so an interrupted or killed run still reports.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from typing import TypedDict

from treehawk.core.aggregate import cpu_seconds_of, peak_rss_of
from treehawk.core.errors import ReportError
from treehawk.core.interfaces import Record
from treehawk.core.values import as_float, as_int, as_records, peak_of

__all__ = ["Report", "ReportError", "build_report", "read_records"]


class Report(TypedDict):
    """A log, read back: the run's metadata and its computed figures."""

    header: Record
    summary: Record


def read_records(path: str) -> Iterator[Record]:
    try:
        with pathlib.Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a truncated final line from a killed run
                if isinstance(record, dict):
                    yield record
    except OSError as exc:
        raise ReportError(f"cannot read {path}: {exc}") from exc


def build_report(path: str, *, top_n: int = 5) -> Report:
    header: Record = {}
    summary: Record = {}
    samples = 0
    duration = 0.0
    cpu_seconds_used = 0.0
    peak_n_procs = 0
    peak_cpu: float | None = None
    peak_rss: int | None = None
    peak_pss: int | None = None
    peak_group_memory: int | None = None
    cpu_sum = 0.0
    cpu_n = 0
    procs: dict[tuple[object, object], Record] = {}

    for record in read_records(path):
        kind = record.get("type")
        if kind == "header":
            header = record
        elif kind == "summary":
            summary = record
        elif kind == "sample":
            samples += 1
            duration = as_float(record.get("t")) or 0.0
            cpu_seconds_used = as_float(record.get("cpu_seconds_used")) or 0.0
            peak_n_procs = max(peak_n_procs, as_int(record.get("n_procs")) or 0)
            cpu = as_float(record.get("cpu_percent"))
            peak_cpu = peak_of(current=peak_cpu, candidate=cpu)
            peak_rss = peak_of(current=peak_rss, candidate=as_int(record.get("rss_bytes")))
            peak_pss = peak_of(current=peak_pss, candidate=as_int(record.get("pss_bytes")))
            peak_group_memory = peak_of(current=peak_group_memory, candidate=as_int(record.get("group_memory_bytes")))
            if cpu is not None:
                cpu_sum += cpu
                cpu_n += 1
            for proc in as_records(record.get("procs")):
                _fold_process(procs=procs, proc=proc)

    if not samples and not summary:
        raise ReportError(f"{path} contains no samples")

    recomputed: Record = {
        "peak_cpu_percent": peak_cpu,
        "peak_rss_bytes": peak_rss,
        "peak_pss_bytes": peak_pss,
        "peak_group_memory_bytes": peak_group_memory,
        "peak_n_procs": peak_n_procs,
        "cpu_seconds_used": cpu_seconds_used,
        "duration_s": duration,
        "mean_cpu_percent": round(cpu_sum / cpu_n, 2) if cpu_n else None,
        "samples": samples,
    }
    merged: Record = {**recomputed, **{k: v for k, v in summary.items() if v is not None}}
    merged.pop("type", None)

    if not merged.get("top_by_cpu") and procs:
        entries = list(procs.values())
        merged["top_by_cpu"] = sorted(entries, key=cpu_seconds_of, reverse=True)[:top_n]
        merged["top_by_memory"] = sorted(entries, key=peak_rss_of, reverse=True)[:top_n]
    return Report(header=header, summary=merged)


def _fold_process(*, procs: dict[tuple[object, object], Record], proc: Record) -> None:
    """Fold one per-process row into the running totals for that process."""
    entry = procs.setdefault(
        (proc.get("pid"), proc.get("starttime")),
        {
            "pid": proc.get("pid"),
            "name": proc.get("name"),
            "cmdline": proc.get("cmdline"),
            "via": proc.get("via"),
            "cpu_seconds": 0.0,
            "peak_rss_bytes": 0,
        },
    )
    entry["cpu_seconds"] = max(cpu_seconds_of(entry), as_float(proc.get("cpu_seconds")) or 0.0)
    entry["peak_rss_bytes"] = max(peak_rss_of(entry), as_int(proc.get("rss_bytes")) or 0)
