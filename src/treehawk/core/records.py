"""The on-disk record format, defined in exactly one place.

Sinks receive ready-made dictionaries, so a new output format never has to know
what a Snapshot is, and a schema change touches this module alone. Every record
carries ``schema`` so later additions (GPU fields, new platforms) stay readable
by older consumers.

Reading a header back lives here too, for the same reason: what "the target of
this run" means - or which memory measure a log carries - is a property of the
format, not of whichever view is asking.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Final

from treehawk import SCHEMA_VERSION, __version__
from treehawk.core.aggregate import RunSummary
from treehawk.core.config import LogDetail, WatchConfig
from treehawk.core.interfaces import Record
from treehawk.core.models import HostInfo, Snapshot
from treehawk.core.values import as_int, as_sequence


@dataclass(frozen=True, slots=True)
class MemoryMeasure:
    """How one platform's fair-memory figure is named and explained.

    Every platform can report resident memory, and summing it over a fork tree
    over-counts every shared page. What each offers *instead* differs - Linux
    has PSS, macOS has the kernel's phys_footprint - so the log records which
    one it carries and the views read the names from here.
    """

    key: str
    short: str
    """Fits a narrow column and a one-line sample."""
    long: str
    """Names the measure in a chart legend or a summary line."""
    blurb: str
    """One clause saying what the measure means, for a chart subtitle."""


PSS: Final = MemoryMeasure(
    key="pss",
    short="pss",
    long="pss (shared-adjusted)",
    blurb="pss divides each shared page among the processes mapping it",
)

PHYS_FOOTPRINT: Final = MemoryMeasure(
    key="phys_footprint",
    short="foot",
    long="phys footprint",
    blurb="phys footprint is the kernel's own charge for what a process costs the machine",
)

MEMORY_MEASURES: Final[Mapping[str, MemoryMeasure]] = {measure.key: measure for measure in (PSS, PHYS_FOOTPRINT)}


@dataclass(frozen=True, slots=True)
class MemoryReading:
    """The best memory figure in a record, and the name of the measure it is."""

    value: int | None
    label: str


def memory_measure(header: Record) -> MemoryMeasure:
    """Which fair-memory measure this log carries.

    A log written before the field existed is PSS: treehawk was Linux-only
    then, so there was nothing else it could have been.
    """
    return MEMORY_MEASURES.get(str(header.get("memory_kind") or PSS.key), PSS)


def memory_reading(*, record: Record, header: Record) -> MemoryReading:
    """The best memory figure in one sample, in the order we trust the measures.

    The kernel's own charge for a group boundary beats anything we sum
    ourselves; the platform's fair measure beats resident memory; resident
    memory is always there. Every view asks this rather than keeping its own
    copy of the order, so they cannot drift apart.
    """
    return _best(
        record=record,
        header=header,
        group_key="group_memory_bytes",
        fair_key="pss_bytes",
        resident_key="rss_bytes",
    )


def peak_memory_reading(*, summary: Record, header: Record) -> MemoryReading:
    """:func:`memory_reading`, over the whole-run peaks in a summary record."""
    return _best(
        record=summary,
        header=header,
        group_key="peak_group_memory_bytes",
        fair_key="peak_pss_bytes",
        resident_key="peak_rss_bytes",
    )


def _best(*, record: Record, header: Record, group_key: str, fair_key: str, resident_key: str) -> MemoryReading:
    group = as_int(record.get(group_key))
    if group is not None:
        return MemoryReading(value=group, label="cgroup")
    fair = as_int(record.get(fair_key))
    if fair is not None:
        return MemoryReading(value=fair, label=memory_measure(header).short)
    return MemoryReading(value=as_int(record.get(resident_key)), label="rss")


def header_record(
    *,
    host: HostInfo,
    config: WatchConfig,
    mode: str,
    matcher: Record | None,
    started_at: str,
    group_path: str | None,
    argv: list[str] | None = None,
    memory_kind: str = PSS.key,
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
        "memory_kind": memory_kind,
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
    argv = as_sequence(header.get("argv"))
    joined = " ".join(str(argument) for argument in argv)
    if joined:
        return joined
    matcher = header.get("matcher") or {}
    if isinstance(matcher, dict):
        return str(matcher.get("value", "?"))
    return "?"
