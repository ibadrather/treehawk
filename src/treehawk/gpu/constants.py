"""Named values for :mod:`treehawk.gpu`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GpuConstants:
    """What the NVIDIA driver's management library (NVML) is called, and how it answers."""

    nvml_library: str = "libnvidia-ml.so.1"
    """Installed by the NVIDIA driver itself, so no CUDA toolkit is needed."""

    nvml_success: int = 0
    nvml_not_found: int = 6
    """From the utilization call: no sample since the timestamp given, i.e., the GPU sat idle."""

    nvml_insufficient_size: int = 7
    """Returned when the process buffer is too small; the count says how big it must be."""

    nvml_not_available: int = 2**64 - 1
    """``NVML_VALUE_NOT_AVAILABLE`` as an unsigned 64-bit value: the driver will not say."""

    process_capacity: int = 64
    """Processes (or utilization samples) per device read in one call before the buffer is grown."""


GPU = GpuConstants()
