"""The whole-machine loop, on a fake machine and a clock that never waits."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pytest
from conftest import TEST_HOST, FakeClock, FakeProcessSource, FakeSystemSource, TranscriptSink

from treehawk.core.config import MemoryDetail, TopConfig
from treehawk.core.values import as_int, as_mapping, as_sequence
from treehawk.top.constants import TOP
from treehawk.top.models import CreepRule, SpikeRule, Thresholds
from treehawk.top.monitor import TopMonitor
from treehawk.top.pacing import AutoInterval

MIB = 1024 * 1024

QUICK = Thresholds(
    cpu_spike=SpikeRule(factor=4.0, floor=25.0, time_constant=5.0, warmup=3.0, cooldown=60.0),
    memory_spike=SpikeRule(factor=4.0, floor=64 * MIB, time_constant=5.0, warmup=3.0, cooldown=60.0),
    host_cpu_spike=SpikeRule(factor=4.0, floor=20.0, time_constant=5.0, warmup=3.0, cooldown=60.0),
    host_memory_spike=SpikeRule(factor=4.0, floor=256 * MIB, time_constant=5.0, warmup=3.0, cooldown=60.0),
    creep=CreepRule(
        bucket_seconds=2.0, window=5, min_slope_per_hour=MIB, min_r2=0.8, min_growth=MIB, refire_growth=10.0
    ),
    host_creep=CreepRule(
        bucket_seconds=2.0, window=5, min_slope_per_hour=10**15, min_r2=0.8, min_growth=10**15, refire_growth=10.0
    ),
)
"""Rules that fire within seconds, so a test needs tens of samples, not thousands."""

Step = Callable[[FakeProcessSource], object]


def run(
    source: FakeProcessSource,
    *,
    samples: int,
    config: TopConfig | None = None,
    system: FakeSystemSource | None = None,
    thresholds: Thresholds = QUICK,
) -> tuple[TranscriptSink, TopMonitor]:
    sink = TranscriptSink()
    config = config or TopConfig(top_n=2, interval=1.0, max_samples=samples, segment_seconds=None)
    monitor = TopMonitor(
        source=source,
        sink=sink,
        clock=FakeClock(),
        config=config,
        host=TEST_HOST,
        system=system,
        thresholds=thresholds,
    )
    monitor.run()
    return sink, monitor


def every_sample(step: Step, *, times: int) -> list[Step]:
    return [step] * times


def base_machine(script: Iterable[Step] = ()) -> FakeProcessSource:
    source = FakeProcessSource(script)
    source.put(10, comm="db", rss_bytes=800 * MIB, starttime=10)
    source.put(20, comm="web", rss_bytes=200 * MIB, starttime=20)
    source.put(30, comm="cron", rss_bytes=5 * MIB, starttime=30)
    return source


def idle(source: FakeProcessSource) -> None:
    """A sample in which nothing changes."""
    del source


def rows_of(record: dict[str, object]) -> list[tuple[int, int, str]]:
    """``(pid, starttime, reasons)`` of every top row in a sample."""
    out: list[tuple[int, int, str]] = []
    for raw in as_sequence(record.get("top")):
        row = as_sequence(raw)
        out.append((as_int(row[0]) or 0, as_int(row[1]) or 0, str(row[-1])))
    return out


def test_a_run_is_header_samples_summary() -> None:
    sink, _ = run(base_machine(), samples=3)
    assert sink.records[0]["type"] == "header"
    assert sink.records[0]["mode"] == "top"
    assert sink.records[-1]["type"] == "summary"
    assert len(sink.of("sample")) == 3
    assert sink.opens == sink.closes == 1


def test_only_the_top_n_are_read_in_depth_and_described_once() -> None:
    source = base_machine(every_sample(lambda s: s.bump(20, ticks=50), times=5))
    sink, _ = run(source, samples=5)
    procs = sink.of("proc")
    assert sorted(as_int(record["pid"]) or 0 for record in procs) == [10, 20]
    assert 30 not in source.enriched
    first_sample = sink.records.index(sink.of("sample")[0])
    assert all(sink.records.index(record) < first_sample for record in procs)


def test_rows_say_why_a_process_is_there() -> None:
    source = base_machine(every_sample(lambda s: s.bump(30, ticks=80), times=3))
    sink, _ = run(source, samples=3)
    last = rows_of(sink.of("sample")[-1])
    reasons = {pid: why for pid, _, why in last}
    assert reasons[30] == "c"  # busy but small
    assert reasons[10] == "m"  # big
    assert "m" in reasons[20]


def test_enter_and_leave_events_follow_membership() -> None:
    def start_burst(source: FakeProcessSource) -> None:
        source.put(40, comm="burst", rss_bytes=900 * MIB, starttime=40)

    def end_burst(source: FakeProcessSource) -> None:
        source.remove(40)

    source = base_machine([idle, start_burst, idle, end_burst])
    sink, _ = run(source, samples=5)
    events = [(record["kind"], record["pid"]) for record in sink.of("event")]
    assert ("enter", 40) in events
    assert ("leave", 20) in events  # pushed out of the top 2 by memory
    leave_40 = [record for record in sink.of("event") if record["kind"] == "leave" and record["pid"] == 40]
    assert leave_40[0]["reason"] == "exit"
    assert leave_40[0]["name"] == "burst"


def test_a_cpu_jump_is_logged_as_a_spike_even_outside_the_top_n() -> None:
    quiet = [lambda s: s.bump(30, ticks=1)] * 10
    burst = [lambda s: s.bump(30, ticks=95)] * 3
    source = base_machine(quiet + burst)
    config = TopConfig(top_n=1, interval=1.0, max_samples=13, segment_seconds=None)
    sink, _ = run(source, samples=13, config=config)
    spikes = [record for record in sink.of("event") if record["kind"] == "spike"]
    assert len(spikes) == 1
    assert spikes[0]["pid"] == 30
    assert spikes[0]["resource"] == "cpu"
    # A process named by an event gets its proc record even if it never ranked.
    assert any(record["pid"] == 30 for record in sink.of("proc"))


def test_steady_memory_growth_is_logged_as_a_creep() -> None:
    source = base_machine(every_sample(lambda s: s.bump(30, rss=MIB), times=30))
    sink, _ = run(source, samples=30)
    creeps = [record for record in sink.of("event") if record["kind"] == "creep"]
    assert [record["pid"] for record in creeps] == [30]
    assert (as_int(creeps[0]["slope_per_hour"]) or 0) > 1000 * MIB


def test_the_host_line_is_recorded() -> None:
    sink, _ = run(base_machine(), samples=3, system=FakeSystemSource())
    samples = sink.of("sample")
    assert as_mapping(samples[0]["host"])["cpu_percent"] is None  # no rate yet
    host = as_mapping(samples[1]["host"])
    assert host["cpu_percent"] == pytest.approx(25.0)
    assert host["mem_used_bytes"] == 2 * 1024**3
    assert sink.records[0]["boot_id"] == FakeSystemSource().boot


def test_segments_roll_with_their_own_header_and_summary() -> None:
    config = TopConfig(top_n=2, interval=1.0, max_samples=7, segment_seconds=3.0)
    sink, _ = run(base_machine(), samples=7, config=config)
    assert sink.opens == sink.closes == 3
    headers = sink.of("header")
    assert [record["segment"] for record in headers] == [0, 1, 2]
    # Each segment describes its processes again, so it stands on its own.
    assert len(sink.of("proc")) == 2 * 3
    summaries = sink.of("summary")
    assert [record["samples"] for record in summaries] == [3, 3, 1]


def test_the_summary_counts_cpu_used_while_ranked() -> None:
    source = base_machine(every_sample(lambda s: s.bump(20, ticks=50), times=5))
    sink, monitor = run(source, samples=5)
    del monitor
    summary = sink.of("summary")[0]
    top_cpu = as_sequence(summary["top_by_cpu"])
    first = as_mapping(top_cpu[0])
    assert first["pid"] == 20
    assert first["cpu_seconds"] == pytest.approx(2.0)  # four intervals at half a core
    assert summary["events"] == {"enter": 2}


def test_no_pss_skips_the_fair_measure() -> None:
    config = TopConfig(top_n=2, interval=1.0, max_samples=1, segment_seconds=None, memory=MemoryDetail.RESIDENT)
    sink, _ = run(base_machine(), samples=1, config=config)
    pss_index = list(TOP.compact_row).index("pss_bytes")
    assert all(as_sequence(raw)[pss_index] is None for raw in as_sequence(sink.of("sample")[0]["top"]))


def test_stop_request_still_writes_a_summary() -> None:
    sink = TranscriptSink()
    source = FakeProcessSource()
    monitor = TopMonitor(
        source=source,
        sink=sink,
        clock=FakeClock(),
        config=TopConfig(interval=1.0, segment_seconds=None),
        host=TEST_HOST,
    )
    source.script = [lambda _source: monitor.request_stop()]
    monitor.run()
    assert len(sink.of("sample")) == 1
    assert sink.records[-1]["type"] == "summary"


def test_automatic_pacing_writes_the_interval_it_chose() -> None:
    sink = TranscriptSink()
    monitor = TopMonitor(
        source=base_machine(),
        sink=sink,
        clock=FakeClock(),
        config=TopConfig(interval=1.0, max_samples=4, segment_seconds=None),
        host=TEST_HOST,
        pacing=AutoInterval(start=1.0, minimum=0.1, maximum=5.0, duty=0.05, smoothing=1.0),
    )
    monitor.run()
    intervals = [record["interval"] for record in sink.of("sample")]
    assert intervals[0] == pytest.approx(1.0)
    assert intervals[-1] == pytest.approx(0.1)  # a fake sample costs nothing, so it goes as fast as allowed
