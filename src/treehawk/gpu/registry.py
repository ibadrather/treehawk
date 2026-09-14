"""The metric collectors treehawk knows, by name."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from treehawk.core.errors import ConfigError
from treehawk.core.interfaces import MetricCollector

CollectorFactory = Callable[[], MetricCollector]

COLLECTOR_KINDS: dict[str, CollectorFactory] = {}
"""Name -> factory. A future NVIDIA collector registers as ``"nvidia"`` here."""


def build_collectors(names: Iterable[str] = ()) -> list[MetricCollector]:
    """Instantiate the requested collectors; unknown names raise ``ValueError``."""
    collectors: list[MetricCollector] = []
    for name in names:
        try:
            factory = COLLECTOR_KINDS[name]
        except KeyError:
            known = ", ".join(sorted(COLLECTOR_KINDS)) or "none available yet"
            raise ConfigError(f"unknown metric collector {name!r}; known: {known}") from None
        collectors.append(factory())
    return collectors
