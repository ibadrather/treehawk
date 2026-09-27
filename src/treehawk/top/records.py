"""The on-disk format of a ``top`` log, defined in one place.

A log is a series of self-contained segments. Each opens with a ``header``,
ends with a ``summary``, and in between carries four kinds of record:

* ``proc`` - who a process is (name, command line), written the first time a
  segment mentions it, so every later mention is just ``[pid, starttime]``;
* ``sample`` - the machine's line plus a compact row per top-N process, in
  the field order the header's ``row_fields`` names;
* ``event`` - an entry to or exit from the top N, a spike or a leak suspect.

Keeping the command line out of the per-sample rows is what lets a log run
for the lifetime of a machine at two samples a second.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict

from treehawk import SCHEMA_VERSION, __version__
from treehawk.core.config import TopConfig
from treehawk.core.interfaces import Record
from treehawk.core.models import HostInfo
from treehawk.top.constants import TOP
from treehawk.top.models import HostReading, RankedProc, Resource, TopEvent, TopSummary

MODE = "top"
"""The ``mode`` of every ``top`` header; what the readers dispatch on."""

REASON_LETTERS = {Resource.CPU: "c", Resource.MEMORY: "m"}
"""How the resources that ranked a process are spelled in its row."""


def header_record(
    *,
    host: HostInfo,
    config: TopConfig,
    interval: float,
    started_at: str,
    boot_id: str | None,
    segment: int,
    memory_kind: str,
    notes: Iterable[str] = (),
) -> Record:
    return {
        "type": "header",
        "schema": SCHEMA_VERSION,
        "treehawk_version": __version__,
        "started_at": started_at,
        "mode": MODE,
        "top_n": config.top_n,
        "interval": interval,
        "pacing": str(config.pacing),
        "segment": segment,
        "boot_id": boot_id,
        "row_fields": list(TOP.compact_row),
        "memory_kind": memory_kind,
        "host": asdict(host),
        "notes": list(notes),
    }


def proc_record(*, pid: int, starttime: int, name: str, cmdline: str) -> Record:
    return {"type": "proc", "pid": pid, "starttime": starttime, "name": name, "cmdline": cmdline}


def sample_record(
    *,
    seq: int,
    t: float,
    ts: str,
    dt: float,
    interval: float,
    overrun: bool,
    host: HostReading | None,
    n_procs: int,
    ranked: Iterable[RankedProc],
) -> Record:
    return {
        "type": "sample",
        "seq": seq,
        "t": round(t, 3),
        "ts": ts,
        "dt": round(dt, 4),
        "interval": round(interval, 4),
        "overrun": overrun,
        "n_procs": n_procs,
        "host": asdict(host) if host is not None else None,
        "top": [_row(entry) for entry in ranked],
    }


def _row(entry: RankedProc) -> list[object]:
    sample = entry.sample
    return [
        sample.info.pid,
        sample.info.starttime,
        sample.cpu_percent,
        sample.info.rss_bytes,
        sample.pss_bytes,
        sample.swap_bytes,
        reasons_text(entry.reasons),
    ]


def reasons_text(reasons: Iterable[Resource]) -> str:
    """``{CPU, MEMORY}`` -> ``"cm"``, always in the same order."""
    present = set(reasons)
    return "".join(letter for resource, letter in REASON_LETTERS.items() if resource in present)


def event_record(event: TopEvent) -> Record:
    record: Record = {"type": "event"}
    for key, value in asdict(event).items():
        if value is not None:
            record[key] = str(value) if key in {"kind", "resource"} else value
    return record


def summary_record(summary: TopSummary) -> Record:
    record: Record = {"type": "summary", "schema": SCHEMA_VERSION, "mode": MODE}
    record.update(asdict(summary))
    return record
