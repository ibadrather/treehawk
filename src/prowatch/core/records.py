"""The on-disk record format, defined in exactly one place.

Sinks receive ready-made dictionaries, so a new output format never has to know
what a Snapshot is, and a schema change touches this module alone. Every record
carries ``schema`` so later additions (GPU fields, new platforms) stay readable
by older consumers.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from prowatch import SCHEMA_VERSION, __version__
from prowatch.core.aggregate import RunSummary
from prowatch.core.config import WatchConfig
from prowatch.core.interfaces import Record
from prowatch.core.models import HostInfo, Snapshot


def header_record(
    *,
    host: HostInfo,
    config: WatchConfig,
    mode: str,
    matcher: Record | None,
    started_at: str,
    group_path: str | None,
    argv: list[str] | None = None,
    notes: Iterable[str] = (),
) -> Record:
    return {
        "type": "header",
        "schema": SCHEMA_VERSION,
        "prowatch_version": __version__,
        "started_at": started_at,
        "mode": mode,
        "matcher": dict(matcher) if matcher else None,
        "argv": argv,
        "interval": config.interval,
        "expand": [str(name) for name in config.expand],
        "per_process": config.per_process,
        "group_path": group_path,
        "host": asdict(host),
        "notes": list(notes),
    }


def sample_record(
    snap: Snapshot, *, per_process: bool = True, clk_tck: int = 100
) -> Record:
    record: Record = {
        "type": "sample",
        "seq": snap.seq,
        "t": snap.t,
        "ts": snap.timestamp,
        "n_procs": snap.n_procs,
        "cpu_percent": snap.cpu_percent,
        "cpu_percent_norm": snap.cpu_percent_norm,
        "cpu_seconds_total": snap.cpu_seconds_total,
        "cpu_seconds_used": snap.cpu_seconds_used,
        "rss_bytes": snap.rss_bytes,
        "pss_bytes": snap.pss_bytes,
        "swap_bytes": snap.swap_bytes,
        "group_memory_bytes": snap.group.memory_bytes if snap.group else None,
        "group_memory_peak_bytes": (
            snap.group.memory_peak_bytes if snap.group else None
        ),
        "overrun": snap.overrun,
    }
    record.update(snap.extra)
    if per_process:
        record["procs"] = [
            {
                "pid": s.info.pid,
                "ppid": s.info.ppid,
                "starttime": s.info.starttime,
                "name": s.info.comm,
                "cmdline": s.cmdline,
                "state": s.info.state,
                "threads": s.info.threads,
                "cpu_percent": s.cpu_percent,
                "cpu_seconds": round(s.info.cpu_ticks / (clk_tck or 100), 3),
                "rss_bytes": s.info.rss_bytes,
                "pss_bytes": s.pss_bytes,
                "swap_bytes": s.swap_bytes,
                "via": s.via,
            }
            for s in snap.procs
        ]
    return record


def summary_record(summary: RunSummary) -> Record:
    record: Record = {"type": "summary", "schema": SCHEMA_VERSION}
    record.update(asdict(summary))
    return record
