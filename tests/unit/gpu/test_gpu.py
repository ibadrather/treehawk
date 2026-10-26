"""GPU usage: only the tracked processes' share is recorded."""

from __future__ import annotations

import pytest

from treehawk.core.errors import ConfigError
from treehawk.core.models import GpuUsage, ProcInfo, ProcSample, Snapshot
from treehawk.gpu import COLLECTOR_KINDS, GpuCollector, Nvml, build_collectors

MIB = 1024 * 1024


class FakeGpuSource:
    """What a GPU driver would report, for every process on the machine."""

    def __init__(self, usage: dict[int, GpuUsage]) -> None:
        self.usage = usage
        self.closed = False

    def read(self) -> dict[int, GpuUsage]:
        return self.usage

    def close(self) -> None:
        self.closed = True


def snapshot_of(*pids: int) -> Snapshot:
    procs = [
        ProcSample(
            info=ProcInfo(
                pid=pid,
                ppid=1,
                pgid=pid,
                sid=pid,
                starttime=pid,
                state="R",
                threads=1,
                comm="python",
                cpu_ticks=0,
                rss_bytes=MIB,
            )
        )
        for pid in pids
    ]
    return Snapshot(seq=0, t=0.0, timestamp="2026-01-01T00:00:00Z", procs=procs)


def test_only_the_tracked_processes_count() -> None:
    source = FakeGpuSource(
        {
            100: GpuUsage(memory_bytes=512 * MIB, percent=60.0),
            101: GpuUsage(memory_bytes=128 * MIB, percent=30.0),
            # The desktop shares the GPU, but it is not the workload.
            999: GpuUsage(memory_bytes=900 * MIB, percent=10.0),
        }
    )

    figures = GpuCollector(source).collect(snapshot_of(100, 101, 102))

    assert figures == {"memory_bytes": 640 * MIB, "percent": 90.0}


def test_a_workload_off_the_gpu_reads_zero() -> None:
    source = FakeGpuSource({999: GpuUsage(memory_bytes=900 * MIB, percent=10.0)})

    figures = GpuCollector(source).collect(snapshot_of(100))

    assert figures == {"memory_bytes": 0, "percent": 0.0}


def test_percent_is_unknown_when_the_gpu_cannot_report_it_per_process() -> None:
    source = FakeGpuSource({100: GpuUsage(memory_bytes=512 * MIB, percent=None)})

    figures = GpuCollector(source).collect(snapshot_of(100))

    assert figures == {"memory_bytes": 512 * MIB, "percent": None}


def test_closing_the_collector_releases_the_driver() -> None:
    source = FakeGpuSource({})
    GpuCollector(source).close()
    assert source.closed is True


def test_no_driver_means_no_gpu_reader() -> None:
    assert Nvml.open("libtreehawk-no-such-library.so") is None


def test_a_collector_whose_hardware_is_missing_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(COLLECTOR_KINDS, "nvidia", lambda: None)
    assert build_collectors(["nvidia"]) == []


def test_an_unknown_collector_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="unknown metric collector 'tpu'; known: nvidia"):
        build_collectors(["tpu"])
