"""The sampling loop, driven by a fake clock so the tests are instant."""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Iterable, Mapping

import pytest
from conftest import FakeClock, RecordingSink, write_proc

from treehawk.core.aggregate import Aggregator
from treehawk.core.compat import override
from treehawk.core.config import (
    ExpansionName,
    MemoryDetail,
    MissingWorkload,
    WatchConfig,
    WhenEmpty,
)
from treehawk.core.errors import WorkloadNotFound
from treehawk.core.interfaces import MetricCollector, ProcessMatcher, ProcessSource, Record, Sink
from treehawk.core.matchers import build_matcher
from treehawk.core.models import HostInfo, ProcInfo, Snapshot
from treehawk.core.monitor import Monitor
from treehawk.core.strategies import build_strategies
from treehawk.core.tracker import Tracker
from treehawk.core.values import as_mapping, as_records
from treehawk.platforms.linux.source import LinuxProcessSource


class ScriptedSource(LinuxProcessSource):
    """The real /proc reader, with a script that edits the tree before each scan."""

    def __init__(self, root: pathlib.Path, script: Iterable[Callable[[], object]] = ()) -> None:
        super().__init__(str(root), page_size=4096)
        self._script = list(script)
        self.scans = 0

    @override
    def scan(self) -> Mapping[int, ProcInfo]:
        if self._script:
            self._script.pop(0)()
        self.scans += 1
        return super().scan()


def build_monitor(
    source: ProcessSource,
    config: WatchConfig,
    sink: Sink | None = None,
    *,
    matcher: ProcessMatcher | None = None,
    clock: FakeClock | None = None,
    collectors: Iterable[MetricCollector] = (),
) -> Monitor:
    host = HostInfo(
        platform="linux",
        hostname="test",
        ncpu=4,
        clk_tck=100,
        page_size=4096,
        mem_total_bytes=1024,
    )
    matcher = matcher if matcher is not None else build_matcher(kind="keyword", value="train.py")
    tracker = Tracker(
        matcher=matcher,
        strategies=build_strategies(config.expand),
        source=source,
        self_pid=9,
    )
    return Monitor(
        source=source,
        tracker=tracker,
        aggregator=Aggregator(host=host),
        sink=sink if sink is not None else RecordingSink(),
        clock=clock if clock is not None else FakeClock(),
        config=config,
        host=host,
        collectors=collectors,
        matcher=matcher.describe(),
    )


@pytest.fixture
def config() -> WatchConfig:
    return WatchConfig(interval=1.0, expand=(ExpansionName.TREE,), memory=MemoryDetail.RESIDENT)


def test_records_are_written_in_order(proc_root: pathlib.Path, config: WatchConfig) -> None:
    write_proc(proc_root, 100, cmdline="python train.py", utime=10)
    config.max_samples = 2
    sink = RecordingSink()
    monitor = build_monitor(ScriptedSource(proc_root), config, sink)

    monitor.run()

    assert sink.header is not None
    assert sink.header["type"] == "header"
    assert as_mapping(sink.header["matcher"])["value"] == "train.py"
    assert [r["seq"] for r in sink.samples] == [0, 1]
    assert sink.summary is not None
    assert sink.summary["samples"] == 2
    assert as_records(sink.samples[0]["procs"])[0]["pid"] == 100


def test_sampling_uses_absolute_deadlines_so_drift_cannot_accumulate(
    proc_root: pathlib.Path, config: WatchConfig
) -> None:
    write_proc(proc_root, 100, cmdline="python train.py")
    config.max_samples = 4
    config.interval = 0.5
    clock = FakeClock()
    monitor = build_monitor(ScriptedSource(proc_root), config, clock=clock)

    monitor.run()

    assert clock.sleeps == [0.0, 0.5, 1.0, 1.5]


def test_the_run_ends_when_the_last_process_exits(proc_root: pathlib.Path, config: WatchConfig) -> None:
    import shutil

    write_proc(proc_root, 100, cmdline="python train.py")
    # First scan seeds the tracker; the workload disappears before sample 1.
    source = ScriptedSource(
        proc_root,
        script=[lambda: None, lambda: None, lambda: shutil.rmtree(proc_root / "100")],
    )
    sink = RecordingSink()
    monitor = build_monitor(source, config, sink)

    monitor.run()

    assert len(sink.samples) == 2
    assert sink.samples[-1]["n_procs"] == 0


def test_keep_going_survives_an_empty_sample(proc_root: pathlib.Path, config: WatchConfig) -> None:
    import shutil

    write_proc(proc_root, 100, cmdline="python train.py")
    config.when_empty = WhenEmpty.KEEP_WATCHING
    config.max_samples = 3
    source = ScriptedSource(
        proc_root,
        script=[lambda: None, lambda: None, lambda: shutil.rmtree(proc_root / "100")],
    )
    sink = RecordingSink()

    build_monitor(source, config, sink).run()

    assert len(sink.samples) == 3


def test_duration_stops_the_run(proc_root: pathlib.Path, config: WatchConfig) -> None:
    write_proc(proc_root, 100, cmdline="python train.py")
    config.duration = 2.0
    sink = RecordingSink()

    build_monitor(ScriptedSource(proc_root), config, sink).run()

    assert [r["t"] for r in sink.samples] == [0.0, 1.0, 2.0]


def test_a_missing_workload_is_reported_not_guessed(proc_root: pathlib.Path, config: WatchConfig) -> None:
    write_proc(proc_root, 100, cmdline="unrelated")
    monitor = build_monitor(ScriptedSource(proc_root), config)

    with pytest.raises(WorkloadNotFound):
        monitor.run()


def test_wait_polls_until_the_workload_appears(proc_root: pathlib.Path, config: WatchConfig) -> None:
    config.missing_workload = MissingWorkload.WAIT
    config.max_samples = 1
    source = ScriptedSource(
        proc_root,
        script=[
            lambda: None,
            lambda: write_proc(proc_root, 100, cmdline="python train.py"),
        ],
    )
    sink = RecordingSink()

    build_monitor(source, config, sink).run()

    assert sink.samples[0]["n_procs"] == 1


def test_wait_stops_cleanly_when_the_user_interrupts(proc_root: pathlib.Path, config: WatchConfig) -> None:
    """No timeout: it waits until the process appears, or until asked to stop."""
    config.missing_workload = MissingWorkload.WAIT
    monitor = build_monitor(ScriptedSource(proc_root), config)
    monitor.request_stop()

    with pytest.raises(WorkloadNotFound, match="stopped before"):
        monitor.run()


def test_a_collector_contributes_its_own_namespace(proc_root: pathlib.Path, config: WatchConfig) -> None:
    """The seam GPU support will use: extra keys, no schema surgery."""

    class FakeGpu:
        namespace = "gpu"
        closed = False

        def collect(self, snapshot: Snapshot) -> dict[str, object]:
            return {"utilization_percent": 42, "procs": snapshot.n_procs}

        def close(self) -> None:
            self.closed = True

    write_proc(proc_root, 100, cmdline="python train.py")
    config.max_samples = 1
    sink = RecordingSink()
    gpu = FakeGpu()
    monitor = build_monitor(ScriptedSource(proc_root), config, sink, collectors=[gpu])

    monitor.run()

    assert sink.samples[0]["gpu"] == {"utilization_percent": 42, "procs": 1}
    assert gpu.closed is True


def test_a_broken_collector_cannot_cost_us_the_log(proc_root: pathlib.Path, config: WatchConfig) -> None:
    class Broken:
        namespace = "broken"

        def collect(self, snapshot: Snapshot) -> dict[str, object]:
            raise RuntimeError(f"driver exploded on sample {snapshot.seq}")

        def close(self) -> None:
            raise RuntimeError("still exploding")

    write_proc(proc_root, 100, cmdline="python train.py")
    config.max_samples = 1
    sink = RecordingSink()
    monitor = build_monitor(ScriptedSource(proc_root), config, sink, collectors=[Broken()])

    monitor.run()

    assert len(sink.samples) == 1
    assert "broken" not in sink.samples[0]
    assert sink.summary is not None


def test_the_summary_is_written_even_when_a_sink_fails_mid_run(proc_root: pathlib.Path, config: WatchConfig) -> None:
    class Exploding(RecordingSink):
        @override
        def sample(self, record: Record) -> None:
            super().sample(record)
            raise OSError("disk full")

    write_proc(proc_root, 100, cmdline="python train.py")
    config.max_samples = 2
    sink = Exploding()
    monitor = build_monitor(ScriptedSource(proc_root), config, sink)

    with pytest.raises(OSError):
        monitor.run()

    assert sink.summary is not None  # written from the finally block
