"""Choosing the top N, and noticing who came and went.

The set is the *union* of the top N by each resource, so a process that is
quiet on CPU but holds the most memory is still followed - which is exactly the
process a leak hides in. Each ranked process carries the resources that put it
there.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from treehawk.core.models import GpuUsage, Identity, ProcInfo
from treehawk.top.models import Resource, Selection


class Ranker:
    """Picks the top ``n`` processes by each resource."""

    def __init__(self, *, n: int) -> None:
        self._n = n
        self._previous: set[Identity] = set()

    def rank(
        self,
        *,
        procs: Mapping[int, ProcInfo],
        cpu: Mapping[Identity, float],
        gpu: Mapping[int, GpuUsage] | None = None,
    ) -> Selection:
        """This sample's top N, and how it differs from the last one's."""
        live = [info for info in procs.values() if not info.is_zombie]
        reasons: dict[Identity, set[Resource]] = {}

        def pick(resource: Resource, key: Callable[[ProcInfo], float]) -> None:
            candidates = [info for info in live if key(info) > 0]
            candidates.sort(key=key, reverse=True)
            for info in candidates[: self._n]:
                reasons.setdefault(info.identity, set()).add(resource)

        pick(Resource.CPU, lambda info: cpu.get(info.identity, 0.0))
        pick(Resource.MEMORY, lambda info: float(info.rss_bytes or 0))
        if gpu:
            pick(Resource.GPU, lambda info: float(_gpu_memory(gpu.get(info.pid))))

        by_identity = {info.identity: info for info in live}
        order = sorted(
            reasons,
            key=lambda identity: (cpu.get(identity, 0.0), by_identity[identity].rss_bytes or 0),
            reverse=True,
        )
        chosen = {identity: frozenset(reasons[identity]) for identity in order}
        current = set(chosen)
        entered = [identity for identity in order if identity not in self._previous]
        left = sorted(self._previous - current)
        self._previous = current
        return Selection(chosen=chosen, entered=entered, left=left)


def _gpu_memory(usage: GpuUsage | None) -> int:
    if usage is None or usage.memory_bytes is None:
        return 0
    return usage.memory_bytes
