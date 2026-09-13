"""GPU metrics - the seam, ahead of the implementation.

A collector returns a flat mapping that the monitor merges into each sample
record under the collector's namespace, so adding GPU support means registering
a class here: the sampling loop, the record schema and the sinks stay as they
are. Nothing is registered yet, so ``build_collectors()`` returns an empty list
and no GPU keys appear in the output.
"""

from __future__ import annotations

from typing import Callable, Iterable

from ..core.interfaces import MetricCollector

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
            raise ValueError(f"unknown metric collector {name!r}; known: {known}")
        collectors.append(factory())
    return collectors
