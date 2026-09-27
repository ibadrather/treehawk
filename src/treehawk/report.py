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
import zlib
from collections.abc import Iterator
from typing import Final, TypedDict

from treehawk.core.aggregate import cpu_seconds_of, peak_rss_of
from treehawk.core.errors import ReportError
from treehawk.core.interfaces import Record
from treehawk.core.values import as_float, as_int, as_record, as_records, peak_of

__all__ = ["Report", "ReportError", "build_report", "log_files", "peek_header", "read_records"]

LOG_SUFFIXES: Final = (".jsonl", ".jsonl.gz")
"""What counts as a log when a directory is read."""

GZIP_CHUNK: Final = 65536
"""Compressed bytes read at a time."""


class Report(TypedDict):
    """A log, read back: the run's metadata and its computed figures."""

    header: Record
    summary: Record


def read_records(path: str) -> Iterator[Record]:
    """Every record in a log: a file, a compressed file, or a directory of them.

    A directory is read segment by segment, oldest first, which is how
    ``top`` leaves its logs.
    """
    location = pathlib.Path(path)
    if location.is_dir():
        for segment in log_files(location):
            yield from _read_file(segment)
        return
    yield from _read_file(location)


def log_files(directory: pathlib.Path) -> list[pathlib.Path]:
    """The logs under ``directory``, oldest first."""
    found = [
        candidate for candidate in directory.rglob("*") if candidate.is_file() and candidate.name.endswith(LOG_SUFFIXES)
    ]
    return sorted(found, key=lambda candidate: (candidate.stat().st_mtime, candidate.name))


def peek_header(path: str) -> Record:
    """The first header in a log, without reading the rest of it."""
    for record in read_records(path):
        if record.get("type") == "header":
            return record
    return {}


def _read_file(path: pathlib.Path) -> Iterator[Record]:
    try:
        for line in _lines(path):
            line = line.strip()
            if not line:
                continue
            try:
                record = as_record(json.loads(line))
            except json.JSONDecodeError:
                continue  # a truncated final line from a killed run
            if record is not None:
                yield record
    except OSError as exc:
        raise ReportError(f"cannot read {path}: {exc}") from exc


def _lines(path: pathlib.Path) -> Iterator[str]:
    if not path.name.endswith(".gz"):
        with path.open("r", encoding="utf-8") as handle:
            yield from handle
        return
    yield from _gzip_lines(path)


def _gzip_lines(path: pathlib.Path) -> Iterator[str]:
    """Lines of a gzip file, decompressed as a stream.

    A segment cut short - by a power cut while it was being compressed - still
    yields every complete line it holds, where ``gzip.open`` would raise and
    lose them.
    """
    decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
    pending = b""
    with path.open("rb") as handle:
        while chunk := handle.read(GZIP_CHUNK):
            try:
                pending += decompressor.decompress(chunk)
            except zlib.error:
                break
            *complete, pending = pending.split(b"\n")
            for line in complete:
                yield line.decode("utf-8", "replace")
    if pending:
        yield pending.decode("utf-8", "replace")


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
