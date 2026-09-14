"""User-facing run settings, in one place.

Every choice that changes what the program does is a named value rather than a
flag, so a call site says which behaviour it wants instead of leaving the reader
to work out what ``True`` meant.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from treehawk.core.compat import StrEnum
from treehawk.core.errors import ConfigError


class ExpansionName(StrEnum):
    """Membership rules, by name. The CLI renders these as its choices."""

    TREE = "tree"
    CGROUP = "cgroup"
    SESSION = "session"
    ORPHAN = "orphan"


class LogDetail(StrEnum):
    """How much of each sample is written out."""

    AGGREGATE = "aggregate"
    """The workload total only - much smaller logs for a run that lasts days."""

    PER_PROCESS = "per-process"
    """The total plus one row per process."""


class MemoryDetail(StrEnum):
    """Which memory measurement is worth paying for."""

    RESIDENT = "resident"
    """RSS only. Cheap, but summing it over-counts pages shared by children."""

    PROPORTIONAL = "proportional"
    """Also read PSS, which divides a shared page between its users."""


class MissingWorkload(StrEnum):
    """What to do when nothing matches at startup."""

    FAIL = "fail"
    WAIT = "wait"


class WhenEmpty(StrEnum):
    """What to do when a sample finds no processes left alive."""

    STOP = "stop"
    KEEP_WATCHING = "keep-watching"


class CpuSource(StrEnum):
    """Which CPU counter the workload total is derived from."""

    PROCESSES = "processes"
    """Summed per-process counters, plus the members that have since exited."""

    GROUP = "group"
    """The kernel's own cgroup counter - it also covers processes that were
    born and died between two samples."""


class Isolation(StrEnum):
    """Whether a launched workload gets an accounting boundary of its own."""

    CGROUP = "cgroup"
    NONE = "none"


class LogFormat(StrEnum):
    """On-disk formats the log can be written in."""

    JSONL = "jsonl"
    CSV = "csv"


DEFAULT_EXPANSIONS: tuple[ExpansionName, ...] = (
    ExpansionName.TREE,
    ExpansionName.CGROUP,
    ExpansionName.SESSION,
    ExpansionName.ORPHAN,
)


@dataclass(slots=True)
class WatchConfig:
    """Everything the monitor needs to know about *how* to watch."""

    interval: float = 1.0
    duration: float | None = None
    max_samples: int | None = None
    detail: LogDetail = LogDetail.PER_PROCESS
    memory: MemoryDetail = MemoryDetail.PROPORTIONAL
    expand: tuple[ExpansionName, ...] = field(
        default_factory=lambda: DEFAULT_EXPANSIONS
    )
    missing_workload: MissingWorkload = MissingWorkload.FAIL
    when_empty: WhenEmpty = WhenEmpty.STOP
    top_n: int = 5
    collectors: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.interval <= 0:
            raise ConfigError("interval must be greater than 0")
        if self.duration is not None and self.duration <= 0:
            raise ConfigError("duration must be greater than 0")
        if self.max_samples is not None and self.max_samples <= 0:
            raise ConfigError("max-samples must be greater than 0")
        if self.top_n < 0:
            raise ConfigError("top must not be negative")
