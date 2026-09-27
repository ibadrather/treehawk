"""Spike and creep detection, fed hand-made series."""

from __future__ import annotations

import itertools
import math

import pytest

from treehawk.top.detectors import CreepDetector, SpikeDetector
from treehawk.top.models import CreepRule, SpikeRule

P = (1, 100)
"""The one process most tests follow."""

SPIKE = SpikeRule(factor=4.0, floor=25.0, time_constant=10.0, warmup=5.0, cooldown=20.0)
CREEP = CreepRule(
    bucket_seconds=10.0,
    window=6,
    min_slope_per_hour=1000.0,
    min_r2=0.8,
    min_growth=100.0,
    refire_growth=0.25,
)


def feed(detector: SpikeDetector, values: list[float], *, start: float = 0.0) -> list[float]:
    """Times at which the detector fired, one reading per second."""
    fired: list[float] = []
    for index, value in enumerate(values):
        t = start + index
        if detector.observe(key=P, value=value, t=t) is not None:
            fired.append(t)
    return fired


def test_a_jump_fires_once() -> None:
    detector: SpikeDetector = SpikeDetector(SPIKE)
    values = [5.0 + (index % 3) for index in range(30)] + [95.0] * 5 + [5.0] * 10
    assert feed(detector, values) == [30.0]


def test_the_spike_reports_value_and_baseline() -> None:
    detector: SpikeDetector = SpikeDetector(SPIKE)
    for t in range(20):
        detector.observe(key=P, value=10.0, t=float(t))
    spike = detector.observe(key=P, value=90.0, t=20.0)
    assert spike is not None
    assert spike.value == pytest.approx(90.0)
    assert math.isclose(spike.baseline, 10.0)


def test_nothing_fires_during_warmup() -> None:
    detector: SpikeDetector = SpikeDetector(SPIKE)
    assert feed(detector, [0.0, 0.0, 100.0]) == []


def test_a_small_rise_is_not_a_spike_however_steady_the_baseline() -> None:
    detector: SpikeDetector = SpikeDetector(SPIKE)
    # 0.1% to 20%: many standard deviations, but under the absolute floor.
    assert feed(detector, [0.1] * 30 + [20.0]) == []


def test_noise_does_not_fire() -> None:
    detector: SpikeDetector = SpikeDetector(SPIKE)
    noisy = [50.0 + 30.0 * math.sin(index) for index in range(200)]
    assert feed(detector, noisy) == []


def test_keys_are_independent_and_forgettable() -> None:
    detector = SpikeDetector(SPIKE)
    a, b = (1, 1), (2, 2)
    for t in range(10):
        detector.observe(key=a, value=1.0, t=float(t))
        detector.observe(key=b, value=1.0, t=float(t))
    detector.forget([a])
    # A forgotten key starts again, warmup and all; the other keeps its baseline.
    assert detector.observe(key=a, value=99.0, t=10.0) is None
    assert detector.observe(key=b, value=99.0, t=10.0) is not None


def creep_times(detector: CreepDetector, values: list[float]) -> list[float]:
    fired: list[float] = []
    for index, value in enumerate(values):
        t = float(index)
        if detector.observe(key=P, value=value, t=t) is not None:
            fired.append(t)
    return fired


def test_steady_growth_is_a_leak_suspect() -> None:
    detector: CreepDetector = CreepDetector(CREEP)
    growing = [1000.0 + 5.0 * index for index in range(80)]  # 5 B/s = 18 kB/h
    fired = creep_times(detector, growing)
    assert fired
    assert fired[0] == pytest.approx(60.0)  # six ten-second buckets have closed


def test_a_sawtooth_with_a_flat_floor_is_not() -> None:
    detector: CreepDetector = CreepDetector(CREEP)
    # A garbage collector: climbs, drops back to the same floor, again and again.
    sawtooth = [1000.0 + 50.0 * (index % 7) for index in range(200)]
    assert creep_times(detector, sawtooth) == []


def test_flat_memory_is_not() -> None:
    detector: CreepDetector = CreepDetector(CREEP)
    assert creep_times(detector, [1000.0] * 200) == []


def test_a_suspect_is_reported_again_only_after_growing_further() -> None:
    detector: CreepDetector = CreepDetector(CREEP)
    fired = creep_times(detector, [1000.0 + 5.0 * index for index in range(400)])
    # 25% more than the last report each time, not every bucket.
    assert 1 < len(fired) < 10
    first = detector_values_at(fired)
    assert all(later >= earlier * 1.25 for earlier, later in itertools.pairwise(first))


def detector_values_at(times: list[float]) -> list[float]:
    # The floor of the bucket closed at t is the value ten seconds earlier.
    return [1000.0 + 5.0 * (t - 10.0) for t in times]


def test_the_report_carries_the_fit() -> None:
    detector: CreepDetector = CreepDetector(CREEP)
    creep = None
    for index in range(80):
        creep = detector.observe(key=P, value=1000.0 + 5.0 * index, t=float(index)) or creep
    assert creep is not None
    assert math.isclose(creep.slope_per_hour, 5.0 * 3600.0, rel_tol=1e-6)
    assert creep.r2 > 0.99
    assert creep.last > creep.first
