"""The on-disk record format, defined in exactly one place.

Sinks receive ready-made dictionaries, so a new output format never has to know
what a Snapshot is, and a schema change touches this module alone. Every record
carries ``schema`` so later additions (GPU fields, new platforms) stay readable
by older consumers.

Reading a header back lives here too, for the same reason: what "the target of
this run" means is a property of the format, not of whichever view is asking.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from treehawk import SCHEMA_VERSION, __version__
from treehawk.core.aggregate import RunSummary
from treehawk.core.config import LogDetail, WatchConfig
from treehawk.core.interfaces import Record
from treehawk.core.models import HostInfo, Snapshot


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
        "treehawk_version": __version__,
        "started_at": started_at,
        "mode": mode,
        "matcher": dict(matcher) if matcher else None,
        "argv": argv,
        "interval": config.interval,
        "expand": [str(name) for name in config.expand],
        "per_process": config.detail is LogDetail.PER_PROCESS,
        "group_path": group_path,
        "host": asdict(host),
        "notes": list(notes),
    }


def sample_record(
    *,
    snapshot: Snapshot,
    detail: LogDetail = LogDetail.PER_PROCESS,
    clk_tck: int = 100,
) -> Record:
    group = snapshot.group
    record: Record = {
        "type": "sample",
        "seq": snapshot.seq,
        "t": snapshot.t,
        "ts": snapshot.timestamp,
        "n_procs": snapshot.n_procs,
        "cpu_percent": snapshot.cpu_percent,
        "cpu_percent_norm": snapshot.cpu_percent_norm,
        "cpu_seconds_total": snapshot.cpu_seconds_total,
        "cpu_seconds_used": snapshot.cpu_seconds_used,
        "rss_bytes": snapshot.rss_bytes,
        "pss_bytes": snapshot.pss_bytes,
        "swap_bytes": snapshot.swap_bytes,
        "group_memory_bytes": group.memory_bytes if group else None,
        "group_memory_peak_bytes": group.memory_peak_bytes if group else None,
        "overrun": snapshot.overrun,
    }
    record.update(snapshot.extra)
    if detail is LogDetail.PER_PROCESS:
        record["procs"] = [
            {
                "pid": sample.info.pid,
                "ppid": sample.info.ppid,
                "starttime": sample.info.starttime,
                "name": sample.info.comm,
                "cmdline": sample.cmdline,
                "state": sample.info.state,
                "threads": sample.info.threads,
                "cpu_percent": sample.cpu_percent,
                "cpu_seconds": round(sample.info.cpu_ticks / (clk_tck or 100), 3),
                "rss_bytes": sample.info.rss_bytes,
                "pss_bytes": sample.pss_bytes,
                "swap_bytes": sample.swap_bytes,
                "via": sample.via,
            }
            for sample in snapshot.procs
        ]
    return record


def summary_record(summary: RunSummary) -> Record:
    record: Record = {"type": "summary", "schema": SCHEMA_VERSION}
    record.update(asdict(summary))
    return record


def target_of(header: Record) -> str:
    """What the run was watching, as a reader should see it.

    A ``run`` header carries the command that was started; a ``watch`` header
    carries whatever the user gave the matcher. Every view wants the same
    answer, so it is worked out once here.
    """
    argv = header.get("argv") or ()
    joined = " ".join(str(argument) for argument in argv)
    if joined:
        return joined
    matcher = header.get("matcher") or {}
    if isinstance(matcher, dict):
        return str(matcher.get("value", "?"))
    return "?"
