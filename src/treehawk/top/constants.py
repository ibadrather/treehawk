"""Named values for :mod:`treehawk.top`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from treehawk.top.models import CreepRule, SpikeRule, Thresholds

_MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class TopConstants:
    """Defaults for whole-machine tracking, and the thresholds of its detectors."""

    default_top_n: int = 10
    default_interval: float = 0.5
    """Seconds between samples when nobody says otherwise."""

    auto_min_interval: float = 0.1
    auto_max_interval: float = 5.0
    auto_duty: float = 0.05
    """With automatic pacing, the share of one core sampling may take."""
    auto_smoothing: float = 0.3
    """How far towards the new target the interval moves in one sample."""

    overrun_factor: float = 1.5
    """A sample this many intervals after the last one is flagged ``overrun``."""

    segment_seconds: float = 3600.0
    """How much time one log file covers."""
    keep_bytes: int = 1024 * _MIB
    """Disk the whole log directory may use before the oldest files go."""

    summary_rank_limit: int = 10
    """Processes listed per ranking in a segment summary and in a report."""

    report_events: int = 12
    """Spikes listed per resource in a report, the largest first."""

    thresholds: Thresholds = Thresholds(
        cpu_spike=SpikeRule(factor=4.0, floor=25.0, time_constant=60.0, warmup=15.0, cooldown=60.0),
        memory_spike=SpikeRule(factor=4.0, floor=64 * _MIB, time_constant=120.0, warmup=15.0, cooldown=60.0),
        host_cpu_spike=SpikeRule(factor=4.0, floor=20.0, time_constant=60.0, warmup=15.0, cooldown=60.0),
        host_memory_spike=SpikeRule(factor=4.0, floor=256 * _MIB, time_constant=120.0, warmup=15.0, cooldown=60.0),
        creep=CreepRule(
            bucket_seconds=60.0,
            window=30,
            min_slope_per_hour=16 * _MIB,
            min_r2=0.8,
            min_growth=8 * _MIB,
            refire_growth=0.25,
        ),
        host_creep=CreepRule(
            bucket_seconds=60.0,
            window=30,
            min_slope_per_hour=64 * _MIB,
            min_r2=0.8,
            min_growth=64 * _MIB,
            refire_growth=0.25,
        ),
    )
    """What counts as a spike or a leak suspect.

    Process CPU is in percent of one core and host CPU in percent of all of
    them; memory is in bytes. A creep window is thirty one-minute points: half
    an hour of steady growth before a process is named.
    """

    compact_row: tuple[str, ...] = (
        "pid",
        "starttime",
        "cpu_percent",
        "rss_bytes",
        "pss_bytes",
        "swap_bytes",
        "gpu_memory_bytes",
        "reasons",
    )
    """Order of the fields in each row of a sample's ``top`` list. Written into
    the header too, so a reader never has to guess."""


TOP: Final = TopConstants()
