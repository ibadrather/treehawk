"""The workload's share of the NVIDIA GPUs, as a per-sample metric collector."""

from __future__ import annotations

from collections.abc import Mapping

from treehawk.core.interfaces import ProcessGpuSource
from treehawk.core.models import Snapshot
from treehawk.gpu.nvml import Nvml


class GpuCollector:
    """Adds the tracked processes' GPU usage to each sample, under ``gpu``.

    Only processes in the workload count: a desktop or another job on the
    same GPU is left out, which is why device-wide figures are not used.

    ``memory_bytes`` is the GPU memory they hold, summed across GPUs;
    ``percent`` is their share of GPU compute time, where 100 is one GPU fully
    busy - None when the GPU cannot report it per process.
    """

    namespace = "gpu"

    def __init__(self, source: ProcessGpuSource) -> None:
        self._source = source

    def collect(self, snapshot: Snapshot) -> Mapping[str, object]:
        usage = self._source.read()
        mine = [usage[sample.info.pid] for sample in snapshot.procs if sample.info.pid in usage]
        percents = [entry.percent for entry in mine]
        return {
            "memory_bytes": sum(entry.memory_bytes or 0 for entry in mine),
            "percent": None if None in percents else sum(percent or 0.0 for percent in percents),
        }

    def close(self) -> None:
        self._source.close()


def open_nvidia() -> GpuCollector | None:
    """A collector for this machine's NVIDIA GPUs, or None if it has none."""
    nvml = Nvml.open()
    return GpuCollector(nvml) if nvml is not None else None
