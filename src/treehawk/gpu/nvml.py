"""NVIDIA GPUs, read through the driver's own management library (NVML).

Bound with :mod:`ctypes` rather than a Python package: the library ships with
every NVIDIA driver, and a handful of calls do not justify a dependency.
Nothing here starts a process or touches the GPU's work queue - NVML answers
from the driver's bookkeeping - so reading it does not disturb what is being
measured.

Only per-process figures are read: the GPU memory each process holds, and the
share of the GPU's compute (SM) time each one used since the previous read.
What the whole GPU is doing says nothing about the workload when something
else - a desktop, another job - shares it.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from typing import TypeVar

from treehawk.core.models import GpuUsage
from treehawk.gpu.constants import GPU

Number = TypeVar("Number", int, float)


class _ProcessInfo(ctypes.Structure):
    """``nvmlProcessInfo_v2_t``, the layout the ``_v3`` process calls fill in."""

    _fields_ = (
        ("pid", ctypes.c_uint),
        ("used_gpu_memory", ctypes.c_ulonglong),
        ("gpu_instance_id", ctypes.c_uint),
        ("compute_instance_id", ctypes.c_uint),
    )


class _UtilizationSample(ctypes.Structure):
    """``nvmlProcessUtilizationSample_t``: one process' share of one driver sample period."""

    _fields_ = (
        ("pid", ctypes.c_uint),
        ("timestamp", ctypes.c_ulonglong),
        ("sm_util", ctypes.c_uint),
        ("mem_util", ctypes.c_uint),
        ("enc_util", ctypes.c_uint),
        ("dec_util", ctypes.c_uint),
    )


class _Device:
    """One GPU's handle, with the buffers its reads reuse every sample."""

    __slots__ = ("handle", "last_seen", "processes", "samples")

    def __init__(self, handle: ctypes.c_void_p) -> None:
        self.handle = handle
        self.last_seen = 0
        """Driver timestamp of the newest utilization sample already read."""
        self.processes = (_ProcessInfo * GPU.process_capacity)()
        self.samples = (_UtilizationSample * GPU.process_capacity)()


class Nvml:
    """Per-process usage of every NVIDIA GPU on the machine.

    Satisfies :class:`~treehawk.core.interfaces.ProcessGpuSource`. Build it
    with :meth:`open`, which returns None when there is no NVIDIA driver.
    """

    def __init__(self, *, library: ctypes.CDLL, devices: list[_Device]) -> None:
        self._library = library
        self._devices = devices

    @classmethod
    def open(cls, library: str = GPU.nvml_library) -> Nvml | None:
        """Load and start NVML, or None if this machine has no NVIDIA GPU to read."""
        try:
            nvml = ctypes.CDLL(library)
            if nvml.nvmlInit_v2() != GPU.nvml_success:
                return None
        except (OSError, AttributeError):  # no driver, or one too old for the _v2 calls
            return None
        count = ctypes.c_uint(0)
        devices: list[_Device] = []
        if nvml.nvmlDeviceGetCount_v2(ctypes.byref(count)) == GPU.nvml_success:
            for index in range(count.value):
                handle = ctypes.c_void_p()
                if nvml.nvmlDeviceGetHandleByIndex_v2(ctypes.c_uint(index), ctypes.byref(handle)) == GPU.nvml_success:
                    devices.append(_Device(handle))
        if not devices:
            nvml.nvmlShutdown()
            return None
        return cls(library=nvml, devices=devices)

    def read(self) -> dict[int, GpuUsage]:
        """``{pid: usage}`` for every process holding GPU memory, summed across GPUs."""
        usage: dict[int, GpuUsage] = {}
        for device in self._devices:
            memory = self._memory(device)
            percent = self._utilization(device)
            for pid, used in memory.items():
                busy = None if percent is None else percent.get(pid, 0.0)
                usage[pid] = _combine(usage.get(pid), GpuUsage(memory_bytes=used, percent=busy))
        return usage

    def close(self) -> None:
        self._library.nvmlShutdown()

    def _memory(self, device: _Device) -> dict[int, int | None]:
        """GPU memory per process on one device; None where the driver will not say."""
        held: dict[int, int | None] = {}
        for call in (
            self._library.nvmlDeviceGetComputeRunningProcesses_v3,
            self._library.nvmlDeviceGetGraphicsRunningProcesses_v3,
        ):
            for info in _processes(device, call):
                used = None if info.used_gpu_memory == GPU.nvml_not_available else int(info.used_gpu_memory)
                # A process doing both compute and graphics is listed by both
                # calls, with the same memory: count it once.
                previous = held.get(info.pid)
                held[info.pid] = used if previous is None else max(previous, used or 0)
        return held

    def _utilization(self, device: _Device) -> dict[int, float] | None:
        """Mean SM percent per process since the last read; None if the GPU cannot say."""
        count = ctypes.c_uint(len(device.samples))
        call = self._library.nvmlDeviceGetProcessUtilization
        code = call(device.handle, device.samples, ctypes.byref(count), ctypes.c_ulonglong(device.last_seen))
        if code == GPU.nvml_insufficient_size:
            device.samples = (_UtilizationSample * (count.value + GPU.process_capacity))()
            count = ctypes.c_uint(len(device.samples))
            code = call(device.handle, device.samples, ctypes.byref(count), ctypes.c_ulonglong(device.last_seen))
        if code == GPU.nvml_not_found:
            return {}  # nothing ran on the GPU since the last read
        if code != GPU.nvml_success:
            return None
        sums: dict[int, int] = {}
        seen: dict[int, int] = {}
        for sample in device.samples[: count.value]:
            sums[sample.pid] = sums.get(sample.pid, 0) + sample.sm_util
            seen[sample.pid] = seen.get(sample.pid, 0) + 1
            device.last_seen = max(device.last_seen, sample.timestamp)
        return {pid: sums[pid] / seen[pid] for pid in sums}


def _processes(device: _Device, call: Callable[..., int]) -> list[_ProcessInfo]:
    count = ctypes.c_uint(len(device.processes))
    code = call(device.handle, ctypes.byref(count), device.processes)
    if code == GPU.nvml_insufficient_size:
        # More processes than ever before: grow the buffer for good, then ask again.
        device.processes = (_ProcessInfo * (count.value + GPU.process_capacity))()
        count = ctypes.c_uint(len(device.processes))
        code = call(device.handle, ctypes.byref(count), device.processes)
    if code != GPU.nvml_success:
        return []
    return list(device.processes[: count.value])


def _combine(first: GpuUsage | None, second: GpuUsage) -> GpuUsage:
    """One process' usage on two GPUs, as one figure: memory and percent add up."""
    if first is None:
        return second
    return GpuUsage(
        memory_bytes=_plus(first.memory_bytes, second.memory_bytes),
        percent=_plus(first.percent, second.percent),
    )


def _plus(first: Number | None, second: Number | None) -> Number | None:
    if first is None:
        return second
    if second is None:
        return first
    return first + second
