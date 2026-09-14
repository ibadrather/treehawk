"""The sampling loop, driven by a fake clock so the tests are instant."""

from __future__ import annotations

import pytest
from conftest import write_proc

from treehawk.core.aggregate import Aggregator
from treehawk.core.config import (
    MemoryDetail,
    MissingWorkload,
    WatchConfig,
    WhenEmpty,
)
from treehawk.core.matchers import build_matcher
from treehawk.core.errors import WorkloadNotFound
from treehawk.core.monitor import Monitor
from treehawk.core.strategies import build_strategies
from treehawk.core.tracker import Tracker
from treehawk.platforms.linux.source import LinuxProcessSource
from treehawk.sinks.base import BaseSink


class FakeClock:
    """Time only moves when the loop asks it to."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def now_iso(self):
        return f"2026-01-01T00:00:{self.t:06.3f}Z"

    def sleep_until(self, deadline):
        self.sleeps.append(deadline)
        self.t = max(self.t, deadline)


class RecordingSink(BaseSink):
    def __init__(self):
        self.header = None
        self.samples = []
        self.summary = None

    def open(self, header):
        self.header = header

    def sample(self, record):
        self.samples.append(record)

    def close(self, summary):
        self.summary = summary


class ScriptedSource:
    """Wraps the real /proc reader and lets a test edit the tree per sample."""

    def __init__(self, root, script=()):
        self._inner = LinuxProcessSource(str(root), page_size=4096)
        self._script = list(script)
        self.scans = 0

    def scan(self):
        if self._script:
            self._script.pop(0)()
        self.scans += 1
        return self._inner.scan()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def build_monitor(source, config, sink=None, matcher=None, collectors=()):
    from treehawk.core.models import HostInfo

    host = HostInfo(
        platform="linux", hostname="test", ncpu=4, clk_tck=100,
        page_size=4096, mem_total_bytes=1024,
    )
    matcher = matcher or build_matcher(kind="keyword", value="train.py")
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
        sink=sink or RecordingSink(),
        clock=FakeClock(),
        config=config,
        host=host,
        matcher=matcher.describe(),
    )


@pytest.fixture
def config():
    return WatchConfig(
        interval=1.0, expand=("tree",), memory=MemoryDetail.RESIDENT
    )


def test_records_are_written_in_order(proc_root, config):
    write_proc(proc_root, 100, cmdline="python train.py", utime=10)
    config.max_samples = 2
    sink = RecordingSink()
    monitor = build_monitor(ScriptedSource(proc_root), config, sink)

    monitor.run()

    assert sink.header["type"] == "header"
    assert sink.header["matcher"]["value"] == "train.py"
    assert [r["seq"] for r in sink.samples] == [0, 1]
    assert sink.summary["samples"] == 2
    assert sink.samples[0]["procs"][0]["pid"] == 100


def test_sampling_uses_absolute_deadlines_so_drift_cannot_accumulate(proc_root, config):
    write_proc(proc_root, 100, cmdline="python train.py")
    config.max_samples = 4
    config.interval = 0.5
    monitor = build_monitor(ScriptedSource(proc_root), config)

    monitor.run()

    assert monitor._clock.sleeps == [0.0, 0.5, 1.0, 1.5]


def test_the_run_ends_when_the_last_process_exits(proc_root, config):
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


def test_keep_going_survives_an_empty_sample(proc_root, config):
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


def test_duration_stops_the_run(proc_root, config):
    write_proc(proc_root, 100, cmdline="python train.py")
    config.duration = 2.0
    sink = RecordingSink()

    build_monitor(ScriptedSource(proc_root), config, sink).run()

    assert [r["t"] for r in sink.samples] == [0.0, 1.0, 2.0]


def test_a_missing_workload_is_reported_not_guessed(proc_root, config):
    write_proc(proc_root, 100, cmdline="unrelated")
    monitor = build_monitor(ScriptedSource(proc_root), config)

    with pytest.raises(WorkloadNotFound):
        monitor.run()


def test_wait_polls_until_the_workload_appears(proc_root, config):
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


def test_wait_stops_cleanly_when_the_user_interrupts(proc_root, config):
    """No timeout: it waits until the process appears, or until asked to stop."""
    config.missing_workload = MissingWorkload.WAIT
    monitor = build_monitor(ScriptedSource(proc_root), config)
    monitor.request_stop()

    with pytest.raises(WorkloadNotFound, match="stopped before"):
        monitor.run()


def test_a_collector_contributes_its_own_namespace(proc_root, config):
    """The seam GPU support will use: extra keys, no schema surgery."""

    class FakeGpu:
        namespace = "gpu"

        def collect(self, snapshot):
            return {"utilization_percent": 42, "procs": snapshot.n_procs}

        def close(self):
            self.closed = True

    write_proc(proc_root, 100, cmdline="python train.py")
    config.max_samples = 1
    sink = RecordingSink()
    gpu = FakeGpu()
    monitor = build_monitor(ScriptedSource(proc_root), config, sink)
    monitor._collectors = [gpu]

    monitor.run()

    assert sink.samples[0]["gpu"] == {"utilization_percent": 42, "procs": 1}
    assert gpu.closed is True


def test_a_broken_collector_cannot_cost_us_the_log(proc_root, config):
    class Broken:
        namespace = "broken"

        def collect(self, snapshot):
            raise RuntimeError("driver exploded")

        def close(self):
            raise RuntimeError("still exploding")

    write_proc(proc_root, 100, cmdline="python train.py")
    config.max_samples = 1
    sink = RecordingSink()
    monitor = build_monitor(ScriptedSource(proc_root), config, sink)
    monitor._collectors = [Broken()]

    monitor.run()

    assert len(sink.samples) == 1
    assert "broken" not in sink.samples[0]
    assert sink.summary is not None


def test_the_summary_is_written_even_when_a_sink_fails_mid_run(proc_root, config):
    class Exploding(RecordingSink):
        def sample(self, record):
            super().sample(record)
            raise OSError("disk full")

    write_proc(proc_root, 100, cmdline="python train.py")
    config.max_samples = 2
    sink = Exploding()
    monitor = build_monitor(ScriptedSource(proc_root), config, sink)

    with pytest.raises(OSError):
        monitor.run()

    assert sink.summary is not None  # written from the finally block
