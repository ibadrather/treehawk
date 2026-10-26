"""GPU metrics.

A collector returns a flat mapping that the monitor merges into each sample
record under the collector's namespace, so the sampling loop, the record
schema and the sinks stay as they are. NVIDIA GPUs are read through NVML;
only the GPU usage of the tracked processes is recorded.
"""

from treehawk.gpu.nvidia import GpuCollector
from treehawk.gpu.nvml import Nvml
from treehawk.gpu.registry import COLLECTOR_KINDS, CollectorFactory, build_collectors

__all__ = ["COLLECTOR_KINDS", "CollectorFactory", "GpuCollector", "Nvml", "build_collectors"]
