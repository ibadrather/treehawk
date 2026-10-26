"""The metric collectors treehawk knows, by name."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from treehawk.core.errors import ConfigError
from treehawk.core.interfaces import MetricCollector
from treehawk.gpu.nvidia import open_nvidia

CollectorFactory = Callable[[], MetricCollector | None]
"""Builds a collector, or returns None when the hardware it reads is not there."""

COLLECTOR_KINDS: dict[str, CollectorFactory] = {"nvidia": open_nvidia}
"""Name -> factory."""


def build_collectors(names: Iterable[str] = ()) -> list[MetricCollector]:
    """Instantiate the requested collectors; unknown names raise ``ConfigError``.

    A known collector whose hardware is missing is left out, not an error: a
    machine without an NVIDIA GPU simply logs no GPU figures.
    """
    collectors: list[MetricCollector] = []
    for name in names:
        try:
            factory = COLLECTOR_KINDS[name]
        except KeyError:
            known = ", ".join(sorted(COLLECTOR_KINDS))
            raise ConfigError(f"unknown metric collector {name!r}; known: {known}") from None
        collector = factory()
        if collector is not None:
            collectors.append(collector)
    return collectors
