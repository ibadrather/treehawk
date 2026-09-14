"""Reading a prowatch log back.

Produces the figures people ask for - peak and mean CPU, peak memory, total CPU
time, and which process was responsible - as plain data. Rendering belongs to
:mod:`prowatch.ui.views` (terminal) and :mod:`prowatch.charts` (PDF), so one
reader serves both.

Uses the recorded summary when the run finished cleanly and recomputes from the
samples when it did not, so an interrupted or killed run still reports.
"""

from __future__ import annotations

import json
from typing import Iterator, TypedDict

from prowatch.core.aggregate import cpu_seconds_of, peak_rss_of
from prowatch.core.errors import ReportError
from prowatch.core.interfaces import Record
from prowatch.core.values import peak_of

__all__ = ["Report", "ReportError", "build_report", "read_records"]


class Report(TypedDict):
    """A log, read back: the run's metadata and its computed figures."""

    header: Record
    summary: Record


def read_records(path: str) -> Iterator[Record]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # a truncated final line from a killed run
    except OSError as exc:
        raise ReportError(f"cannot read {path}: {exc}") from exc


def build_report(path: str, *, top_n: int = 5) -> Report:
    header: Record = {}
    summary: Record = {}
    samples = 0
    recomputed: Record = {
        "peak_cpu_percent": None,
        "peak_rss_bytes": None,
        "peak_pss_bytes": None,
        "peak_group_memory_bytes": None,
        "peak_n_procs": 0,
        "cpu_seconds_used": 0.0,
        "duration_s": 0.0,
    }
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
            recomputed["duration_s"] = record.get("t") or 0.0
            recomputed["cpu_seconds_used"] = record.get("cpu_seconds_used") or 0.0
            recomputed["peak_n_procs"] = max(
                recomputed["peak_n_procs"], record.get("n_procs") or 0
            )
            for peak_key, sample_key in (
                ("peak_cpu_percent", "cpu_percent"),
                ("peak_rss_bytes", "rss_bytes"),
                ("peak_pss_bytes", "pss_bytes"),
                ("peak_group_memory_bytes", "group_memory_bytes"),
            ):
                recomputed[peak_key] = peak_of(
                    current=recomputed[peak_key], candidate=record.get(sample_key)
                )
            if record.get("cpu_percent") is not None:
                cpu_sum += record["cpu_percent"]
                cpu_n += 1
            for proc in record.get("procs") or ():
                key = (proc.get("pid"), proc.get("starttime"))
                entry = procs.setdefault(
                    key,
                    {
                        "pid": proc.get("pid"),
                        "name": proc.get("name"),
                        "cmdline": proc.get("cmdline"),
                        "via": proc.get("via"),
                        "cpu_seconds": 0.0,
                        "peak_rss_bytes": 0,
                    },
                )
                entry["cpu_seconds"] = max(
                    entry["cpu_seconds"], proc.get("cpu_seconds") or 0.0
                )
                entry["peak_rss_bytes"] = max(
                    entry["peak_rss_bytes"], proc.get("rss_bytes") or 0
                )

    if not samples and not summary:
        raise ReportError(f"{path} contains no samples")

    recomputed["mean_cpu_percent"] = round(cpu_sum / cpu_n, 2) if cpu_n else None
    recomputed["samples"] = samples
    merged = {**recomputed, **{k: v for k, v in summary.items() if v is not None}}
    merged.pop("type", None)

    if not merged.get("top_by_cpu") and procs:
        entries = list(procs.values())
        merged["top_by_cpu"] = sorted(entries, key=cpu_seconds_of, reverse=True)[
            :top_n
        ]
        merged["top_by_memory"] = sorted(entries, key=peak_rss_of, reverse=True)[
            :top_n
        ]
    return Report(header=header, summary=merged)

