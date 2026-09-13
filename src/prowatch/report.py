"""Post-hoc reporting over a prowatch log.

Reads back what a run wrote and prints the figures people actually ask for -
peak and mean CPU, peak memory, total CPU time, and which process was
responsible. Uses the recorded summary when the run finished cleanly, and
recomputes from the samples when it did not (an interrupted or killed run still
leaves a usable log).
"""

from __future__ import annotations

import json
from typing import Iterator, Mapping

from .core.humanize import bytes_human, percent_human, seconds_human, truncate


class ReportError(RuntimeError):
    """The log could not be read or contained no samples."""


def read_records(path: str) -> Iterator[dict]:
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


def build_report(path: str, *, top_n: int = 5) -> dict:
    header: dict = {}
    summary: dict = {}
    samples = 0
    recomputed = {
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
    procs: dict[tuple, dict] = {}

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
            for key, field in (
                ("peak_cpu_percent", "cpu_percent"),
                ("peak_rss_bytes", "rss_bytes"),
                ("peak_pss_bytes", "pss_bytes"),
                ("peak_group_memory_bytes", "group_memory_bytes"),
            ):
                recomputed[key] = _max(recomputed[key], record.get(field))
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
        merged["top_by_cpu"] = sorted(
            entries, key=lambda e: e["cpu_seconds"], reverse=True
        )[:top_n]
        merged["top_by_memory"] = sorted(
            entries, key=lambda e: e["peak_rss_bytes"], reverse=True
        )[:top_n]
    return {"header": header, "summary": merged}


def format_report(report: Mapping[str, object]) -> str:
    header = report.get("header") or {}
    summary = report.get("summary") or {}
    host = header.get("host") or {}
    matcher = header.get("matcher") or {}
    target = " ".join(header.get("argv") or []) or matcher.get("value", "?")

    lines = [
        f"target        {target}",
        f"mode          {header.get('mode', '?')}  "
        f"interval={header.get('interval', '?')}s  "
        f"schema={header.get('schema', '?')}",
        f"host          {host.get('hostname', '?')} "
        f"({host.get('ncpu', '?')} cpus, {bytes_human(host.get('mem_total_bytes'))} ram)",
        f"started       {header.get('started_at', '?')}",
        "",
        f"samples       {summary.get('samples', 0)} over "
        f"{seconds_human(summary.get('duration_s'))}"
        + (f"  ({summary['overruns']} overrun)" if summary.get("overruns") else ""),
        f"processes     {summary.get('total_procs_seen', '?')} seen, "
        f"peak {summary.get('peak_n_procs', '?')} concurrent",
        f"cpu           peak {percent_human(summary.get('peak_cpu_percent'))}, "
        f"mean {percent_human(summary.get('mean_cpu_percent'))}, "
        f"{seconds_human(summary.get('cpu_seconds_used'))} of cpu time",
        f"memory        peak rss {bytes_human(summary.get('peak_rss_bytes'))}, "
        f"peak pss {bytes_human(summary.get('peak_pss_bytes'))}, "
        f"peak cgroup {bytes_human(summary.get('peak_group_memory_bytes'))}",
    ]
    if summary.get("exit_code") is not None:
        lines.append(f"exit code     {summary['exit_code']}")

    for title, key, column, render in (
        ("top by cpu time", "top_by_cpu", "cpu_seconds", seconds_human),
        ("top by peak rss", "top_by_memory", "peak_rss_bytes", bytes_human),
    ):
        rows = summary.get(key) or []
        if not rows:
            continue
        lines.append("")
        lines.append(title)
        for row in rows:
            lines.append(
                f"  {row.get('pid'):>8}  {render(row.get(column)):>10}  "
                f"{row.get('via', '-'):<8} "
                f"{truncate(row.get('cmdline') or row.get('name') or '', 56)}"
            )
    return "\n".join(lines)


def _max(current, candidate):
    if candidate is None:
        return current
    return candidate if current is None else max(current, candidate)
