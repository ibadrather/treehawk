"""Turning counters into rates, and samples into a summary."""

from __future__ import annotations

from prowatch.core.aggregate import Aggregator, SummaryAccumulator
from prowatch.core.config import CpuSource
from prowatch.core.models import GroupMetrics, HostInfo, ProcInfo, ProcSample
from prowatch.core.tracker import RefreshResult

HOST = HostInfo(
    platform="linux", hostname="test", ncpu=4, clk_tck=100,
    page_size=4096, mem_total_bytes=8 * 1024**3,
)


def proc(pid=100, ticks=0, rss=1024, pss=None, starttime=1000):
    info = ProcInfo(
        pid=pid, ppid=1, pgid=pid, sid=pid, starttime=starttime, state="R",
        threads=1, comm="worker", cpu_ticks=ticks, rss_bytes=rss,
    )
    return ProcSample(info=info, cmdline="python worker.py", pss_bytes=pss)


def refresh(samples, exited_ticks=0, exited=()):
    return RefreshResult(
        alive=[s.info for s in samples],
        alive_cpu_ticks=sum(s.info.cpu_ticks for s in samples),
        exited_cpu_ticks=exited_ticks,
        exited=list(exited),
    )


def build(agg, samples, *, seq, dt, elapsed=None, **kwargs):
    return agg.build(
        seq=seq, elapsed=elapsed if elapsed is not None else seq * dt, dt=dt,
        timestamp="2026-01-01T00:00:00Z", refresh=kwargs.pop("refresh", refresh(samples)),
        samples=samples, **kwargs,
    )


def test_first_sample_reports_no_cpu_rate():
    """There is nothing to subtract from yet; a made-up number would be worse."""
    snap = build(Aggregator(host=HOST), [proc(ticks=500)], seq=0, dt=0.0)
    assert snap.cpu_percent is None
    assert snap.procs[0].cpu_percent is None


def test_cpu_percent_comes_from_the_counter_delta():
    agg = Aggregator(host=HOST)
    build(agg, [proc(ticks=0)], seq=0, dt=0.0)
    # 100 ticks at 100 Hz = 1.0s of cpu in 1.0s of wall time = one core busy.
    snap = build(agg, [proc(ticks=100)], seq=1, dt=1.0)

    assert snap.cpu_percent == 100.0
    assert snap.cpu_percent_norm == 25.0  # of a 4-core machine
    assert snap.procs[0].cpu_percent == 100.0


def test_aggregate_cpu_includes_a_child_that_exited_mid_interval():
    """The per-process rows cannot show this; the total must not lose it."""
    agg = Aggregator(host=HOST)
    build(agg, [proc(pid=100, ticks=0), proc(pid=200, ticks=0)], seq=0, dt=0.0)

    survivor = proc(pid=100, ticks=50)
    snap = build(
        agg, [survivor], seq=1, dt=1.0,
        refresh=refresh([survivor], exited_ticks=150, exited=[(200, 1000)]),
    )

    assert snap.cpu_seconds_total == 2.0  # 0.5s survivor + 1.5s departed
    assert snap.cpu_percent == 200.0


def test_cpu_total_never_goes_backwards():
    agg = Aggregator(host=HOST)
    build(agg, [proc(ticks=1000)], seq=0, dt=0.0)
    snap = build(agg, [], seq=1, dt=1.0, refresh=refresh([]))

    assert snap.cpu_seconds_total == 10.0
    assert snap.cpu_percent == 0.0


def test_cpu_seconds_used_excludes_work_done_before_we_attached():
    agg = Aggregator(host=HOST)
    build(agg, [proc(ticks=3000)], seq=0, dt=0.0)  # 30s already burned
    snap = build(agg, [proc(ticks=3100)], seq=1, dt=1.0)

    assert snap.cpu_seconds_total == 31.0
    assert snap.cpu_seconds_used == 1.0


def test_group_cpu_is_preferred_when_the_workload_owns_a_cgroup():
    """The kernel counter also covers processes we never managed to sample."""
    agg = Aggregator(host=HOST, cpu_source=CpuSource.GROUP)
    group = GroupMetrics(path="/app.scope", cpu_usec=5_000_000, memory_bytes=2048)
    snap = build(agg, [proc(ticks=100)], seq=0, dt=0.0, group=group)

    assert snap.cpu_seconds_total == 5.0  # not the 1.0s /proc could see


def test_memory_is_summed_per_source_and_stays_none_when_unavailable():
    snap = build(
        Aggregator(host=HOST),
        [proc(pid=100, rss=1000, pss=600), proc(pid=200, rss=1000, pss=400)],
        seq=0, dt=0.0,
    )
    assert snap.rss_bytes == 2000
    assert snap.pss_bytes == 1000  # shared pages counted once
    assert snap.swap_bytes is None


def test_summary_tracks_peaks_and_means():
    agg = Aggregator(host=HOST)
    acc = SummaryAccumulator(clk_tck=100)
    for seq, ticks in enumerate((0, 100, 150)):
        acc.add(build(agg, [proc(ticks=ticks, rss=1000 * (seq + 1))], seq=seq, dt=1.0))

    summary = acc.finish(exit_code=0)

    assert summary.samples == 3
    assert summary.peak_cpu_percent == 100.0
    assert summary.mean_cpu_percent == 75.0  # first sample contributes no rate
    assert summary.peak_rss_bytes == 3000
    assert summary.exit_code == 0
    assert summary.top_by_cpu[0]["cpu_seconds"] == 1.5
