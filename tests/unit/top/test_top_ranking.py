"""Ranking, rates, pacing and the bucketed timeline."""

from __future__ import annotations

import pytest
from conftest import FakeProcessSource

from treehawk.core.config import Pacing, TopConfig
from treehawk.core.models import SystemSample
from treehawk.top.models import Resource
from treehawk.top.pacing import AutoInterval, FixedInterval, build_pacing
from treehawk.top.ranker import Ranker
from treehawk.top.rates import CpuRates, HostCpuRate
from treehawk.top.timeline import Timeline

MIB = 1024 * 1024


def machine() -> FakeProcessSource:
    source = FakeProcessSource()
    source.put(1, comm="busy", rss_bytes=10 * MIB, starttime=1)
    source.put(2, comm="big", rss_bytes=900 * MIB, starttime=2)
    source.put(3, comm="both", rss_bytes=500 * MIB, starttime=3)
    source.put(4, comm="idle", rss_bytes=1 * MIB, starttime=4)
    return source


def test_the_set_is_the_union_of_each_ranking() -> None:
    procs = machine().scan()
    cpu = {(1, 1): 90.0, (3, 3): 40.0, (4, 4): 1.0}
    selection = Ranker(n=2).rank(procs=procs, cpu=cpu)
    assert selection.chosen == {
        (1, 1): frozenset({Resource.CPU}),
        (3, 3): frozenset({Resource.CPU, Resource.MEMORY}),
        (2, 2): frozenset({Resource.MEMORY}),
    }
    assert list(selection.chosen) == [(1, 1), (3, 3), (2, 2)]  # busiest first


def test_membership_changes_are_reported() -> None:
    ranker = Ranker(n=1)
    procs = machine().scan()
    first = ranker.rank(procs=procs, cpu={(1, 1): 90.0})
    assert sorted(first.entered) == [(1, 1), (2, 2)]
    assert first.left == []

    second = ranker.rank(procs=procs, cpu={(4, 4): 50.0})
    assert second.entered == [(4, 4)]
    assert second.left == [(1, 1)]

    third = ranker.rank(procs=procs, cpu={(4, 4): 50.0})
    assert third.entered == []
    assert third.left == []


def test_zombies_and_idle_processes_are_not_ranked_by_cpu() -> None:
    source = machine()
    source.put(5, comm="dead", rss_bytes=0, starttime=5, state="Z")
    selection = Ranker(n=5).rank(procs=source.scan(), cpu={(5, 5): 99.0})
    assert (5, 5) not in selection.chosen
    assert all(Resource.CPU not in reasons for reasons in selection.chosen.values())


def test_cpu_rates_need_two_readings_and_notice_exits() -> None:
    source = machine()
    rates = CpuRates(clk_tck=100)
    first, exited = rates.update(procs=source.scan(), dt=0.0)
    assert first == {}
    assert exited == []

    source.bump(1, ticks=50)
    source.remove(4)
    second, exited = rates.update(procs=source.scan(), dt=0.5)
    assert second[1, 1] == pytest.approx(100.0)  # 50 ticks = 0.5 s of cpu in 0.5 s
    assert second[2, 2] == pytest.approx(0.0)
    assert exited == [(4, 4)]


def test_host_rate_is_the_busy_share_of_every_core() -> None:
    rate = HostCpuRate()
    assert rate.update(SystemSample(cpu_busy_ticks=0, cpu_total_ticks=0)) is None
    assert rate.update(SystemSample(cpu_busy_ticks=30, cpu_total_ticks=120)) == pytest.approx(25.0)
    assert rate.update(None) is None


def test_fixed_pacing_ignores_cost() -> None:
    assert FixedInterval(0.5).after(3.0) == pytest.approx(0.5)


def test_auto_pacing_follows_the_cost_of_a_sample_within_bounds() -> None:
    pacing = AutoInterval(start=0.5, minimum=0.1, maximum=5.0, duty=0.05, smoothing=1.0)
    assert pacing.after(0.01) == pytest.approx(0.2)  # 10 ms at 5% duty
    assert pacing.after(0.0) == pytest.approx(0.1)  # never faster than the minimum
    assert pacing.after(10.0) == pytest.approx(5.0)  # never slower than the maximum


def test_auto_pacing_moves_gradually() -> None:
    pacing = AutoInterval(start=1.0, minimum=0.1, maximum=5.0, duty=0.05, smoothing=0.5)
    assert pacing.after(0.01) == pytest.approx(0.6)


def test_build_pacing_follows_the_config() -> None:
    assert isinstance(build_pacing(TopConfig(pacing=Pacing.AUTO)), AutoInterval)
    assert isinstance(build_pacing(TopConfig()), FixedInterval)


def test_timeline_keeps_the_peak_when_it_coarsens() -> None:
    timeline: Timeline[str] = Timeline(limit=10)
    for second in range(100):
        timeline.add(key="a", t=float(second), value=100.0 if second == 37 else 1.0)
        if second % 2 == 0:
            timeline.add(key="b", t=float(second), value=2.0)
    assert len(timeline.times()) <= 10
    assert max(value or 0.0 for value in timeline.values("a")) == pytest.approx(100.0)
    assert len(timeline.values("a")) == len(timeline.values("b")) == len(timeline.times())
    assert timeline.width >= 10.0


def test_timeline_leaves_holes_where_a_series_has_nothing() -> None:
    timeline: Timeline[str] = Timeline(limit=100)
    timeline.add(key="a", t=0.0, value=1.0)
    timeline.mark(5.0)
    assert timeline.values("a") == [1.0, None]
