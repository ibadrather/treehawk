"""The sampling loop.

This is the high-level policy of the program, and it deliberately knows nothing
about Linux: it talks only to the interfaces in :mod:`prowatch.core.interfaces`,
which the composition root wires up to a concrete platform. Porting to another
OS means providing new implementations, not touching this file.
"""

from __future__ import annotations

from typing import Callable, Iterable

from prowatch.core.aggregate import Aggregator, RunSummary, SummaryAccumulator
from prowatch.core.config import MissingWorkload, WatchConfig, WhenEmpty
from prowatch.core.errors import WorkloadNotFound
from prowatch.core.interfaces import (
    Clock,
    GroupMetricSource,
    MetricCollector,
    ProcessSource,
    Record,
    Sink,
)
from prowatch.core.models import GroupMetrics, HostInfo, ProcInfo, ProcSample, Snapshot
from prowatch.core.records import header_record, sample_record, summary_record
from prowatch.core.tracker import RefreshResult, Tracker


__all__ = ["Monitor", "WorkloadNotFound"]


class Monitor:
    """Samples a workload on a fixed cadence until it ends or is stopped."""

    def __init__(
        self,
        *,
        source: ProcessSource,
        tracker: Tracker,
        aggregator: Aggregator,
        sink: Sink,
        clock: Clock,
        config: WatchConfig,
        host: HostInfo,
        groups: GroupMetricSource | None = None,
        collectors: Iterable[MetricCollector] = (),
        mode: str = "watch",
        matcher: Record | None = None,
        argv: list[str] | None = None,
        notes: Iterable[str] = (),
    ) -> None:
        self._source = source
        self._tracker = tracker
        self._aggregator = aggregator
        self._sink = sink
        self._clock = clock
        self._config = config
        self._host = host
        self._groups = groups
        self._collectors = list(collectors)
        self._mode = mode
        self._matcher = matcher
        self._argv = argv
        self._notes = list(notes)
        self._stop = False
        self._summaries = SummaryAccumulator(clk_tck=host.clk_tck, top_n=config.top_n)

    def request_stop(self) -> None:
        """Ask the loop to finish after the current sample (signal-safe)."""
        self._stop = True

    def run(
        self, *, read_exit_code: Callable[[], int | None] | None = None
    ) -> RunSummary:
        """Sample until a stop condition fires. Always writes a summary."""
        self._config.validate()
        if not self._tracker.seeded and not self._tracker.pinned_group:
            self._await_workload()

        self._sink.open(
            header_record(
                host=self._host,
                config=self._config,
                mode=self._mode,
                matcher=self._matcher,
                started_at=self._clock.now_iso(),
                group_path=self._tracker.pinned_group,
                argv=self._argv,
                notes=self._notes,
            )
        )

        seq = 0
        previous_at: float | None = None
        origin = self._clock.monotonic()
        try:
            while not self._stop:
                self._clock.sleep_until(origin + seq * self._config.interval)
                sampled_at = self._clock.monotonic()
                dt = 0.0 if previous_at is None else sampled_at - previous_at
                snapshot = self._sample(
                    seq=seq,
                    elapsed=sampled_at - origin,
                    dt=dt,
                    overrun=(
                        previous_at is not None
                        and dt > self._config.interval * 1.5
                    ),
                )
                previous_at = sampled_at
                seq += 1

                self._sink.sample(
                    sample_record(
                        snapshot=snapshot,
                        detail=self._config.detail,
                        clk_tck=self._host.clk_tck,
                    )
                )
                self._summaries.add(snapshot)

                if self._should_finish(
                    snapshot=snapshot,
                    samples_taken=seq,
                    elapsed=sampled_at - origin,
                ):
                    break
        finally:
            self._close_collectors()
            code = read_exit_code() if read_exit_code is not None else None
            summary = self._summaries.finish(exit_code=code)
            self._sink.close(summary_record(summary))
        return summary

    def _close_collectors(self) -> None:
        for collector in self._collectors:
            try:
                collector.close()
            except Exception:  # a broken collector must not cost us the log
                pass

    def _sample(
        self, *, seq: int, elapsed: float, dt: float, overrun: bool
    ) -> Snapshot:
        procs = self._source.scan()
        refresh = self._tracker.refresh(procs)
        for identity in refresh.exited:
            self._source.forget(identity)

        samples = [
            self._enrich(info=info, refresh=refresh) for info in refresh.alive
        ]
        snapshot = self._aggregator.build(
            seq=seq,
            elapsed=elapsed,
            dt=dt,
            timestamp=self._clock.now_iso(),
            refresh=refresh,
            samples=samples,
            group=self._group_metrics(),
            overrun=overrun,
        )
        for collector in self._collectors:
            try:
                extra = collector.collect(snapshot)
            except Exception:  # one broken collector must not stop the sample
                continue
            if extra:
                snapshot.extra[collector.namespace] = dict(extra)
        return snapshot

    def _enrich(self, *, info: ProcInfo, refresh: RefreshResult) -> ProcSample:
        sample = self._source.enrich(info=info, memory=self._config.memory)
        sample.via = refresh.via.get(info.pid, sample.via)
        return sample

    def _group_metrics(self) -> GroupMetrics | None:
        if self._groups is None:
            return None
        paths = self._tracker.group_paths
        if not paths:
            return None
        # The shallowest accepted boundary contains the others.
        path = self._tracker.pinned_group or min(paths, key=lambda p: (p.count("/"), p))
        return self._groups.metrics(path)

    def _should_finish(
        self, *, snapshot: Snapshot, samples_taken: int, elapsed: float
    ) -> bool:
        config = self._config
        if config.max_samples is not None and samples_taken >= config.max_samples:
            return True
        if config.duration is not None and elapsed >= config.duration:
            return True
        if config.when_empty is WhenEmpty.STOP and snapshot.n_procs == 0:
            return True
        return False

    def _await_workload(self) -> None:
        """Seed the tracker, optionally polling until the workload shows up.

        With :data:`MissingWorkload.WAIT` there is no timeout: prowatch is meant
        to be left running, so it keeps looking until the process appears or the
        user stops it.
        """
        poll = min(0.2, self._config.interval)
        while True:
            if self._tracker.seed(self._source.scan()):
                return
            if self._config.missing_workload is MissingWorkload.FAIL:
                raise WorkloadNotFound("no process matched")
            if self._stop:
                raise WorkloadNotFound("stopped before a process matched")
            self._clock.sleep_until(self._clock.monotonic() + poll)
