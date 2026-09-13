"""User-facing run settings, in one place."""

from __future__ import annotations

from dataclasses import dataclass, field

from .strategies import DEFAULT_STRATEGIES


@dataclass(slots=True)
class WatchConfig:
    """Everything the monitor needs to know about *how* to watch."""

    interval: float = 1.0
    duration: float | None = None
    max_samples: int | None = None
    per_process: bool = True
    want_pss: bool = True
    expand: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_STRATEGIES))
    rescan: bool = False
    wait: float | None = None
    stop_when_empty: bool = True
    top_n: int = 5
    collectors: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.interval <= 0:
            raise ValueError("interval must be greater than 0")
        if self.duration is not None and self.duration <= 0:
            raise ValueError("duration must be greater than 0")
        if self.max_samples is not None and self.max_samples <= 0:
            raise ValueError("max-samples must be greater than 0")
