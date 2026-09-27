"""The whole-machine sampling loop.

Like :class:`~treehawk.core.monitor.Monitor` it talks only to interfaces, so it
knows nothing about Linux. Unlike it there is no workload to follow: every
process is looked at on every sample, at the cost of one cheap read each, and
only the top N are read in depth and written down.

A sample does four things, in this order:

1. read the process table and the machine's counters;
2. run every process through the spike and creep detectors;
3. rank, and read the winners in depth (command line, fair memory);
4. write the sample and whatever events it produced.

The log is cut into segments. Each is closed with its own summary and a fresh
one opened with its own header, so any one file stands on its own and the
sink can compress and prune closed ones.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Mapping

from treehawk.core.config import TopConfig
from treehawk.core.interfaces import Clock, ProcessGpuSource, ProcessSource, Sink, SystemSource
from treehawk.core.models import GpuUsage, HostInfo, Identity, ProcInfo, SystemSample
from treehawk.core.records import PSS
from treehawk.top.constants import TOP
from treehawk.top.detectors import CreepDetector, SpikeDetector
from treehawk.top.models import (
    HOST_PID,
    EventKind,
    HostReading,
    RankedProc,
    Resource,
    Thresholds,
    TopEvent,
    TopSummary,
)
from treehawk.top.pacing import FixedInterval, IntervalPolicy
from treehawk.top.ranker import Ranker
from treehawk.top.rates import CpuRates, HostCpuRate
from treehawk.top.records import event_record, header_record, proc_record, sample_record, summary_record
from treehawk.top.summary import TopAccumulator

HOST_NAME = "host"
"""The name events about the whole machine carry."""


class TopMonitor:
    """Samples every process on the machine until it is stopped."""

    def __init__(
        self,
        *,
        source: ProcessSource,
        sink: Sink,
        clock: Clock,
        config: TopConfig,
        host: HostInfo,
        system: SystemSource | None = None,
        gpu: ProcessGpuSource | None = None,
        pacing: IntervalPolicy | None = None,
        thresholds: Thresholds = TOP.thresholds,
        memory_kind: str = PSS.key,
        notes: Iterable[str] = (),
    ) -> None:
        self._source = source
        self._sink = sink
        self._clock = clock
        self._config = config
        self._host = host
        self._system = system
        self._gpu = gpu
        self._pacing = pacing if pacing is not None else FixedInterval(config.interval)
        self._memory_kind = memory_kind
        self._notes = list(notes)
        self._stop = False

        self._cpu = CpuRates(clk_tck=host.clk_tck)
        self._host_cpu = HostCpuRate()
        self._ranker = Ranker(n=config.top_n)
        self._cpu_spikes: SpikeDetector[Identity] = SpikeDetector(thresholds.cpu_spike)
        self._memory_spikes: SpikeDetector[Identity] = SpikeDetector(thresholds.memory_spike)
        self._creep: CreepDetector[Identity] = CreepDetector(thresholds.creep)
        self._host_cpu_spikes: SpikeDetector[str] = SpikeDetector(thresholds.host_cpu_spike)
        self._host_memory_spikes: SpikeDetector[str] = SpikeDetector(thresholds.host_memory_spike)
        self._host_creep: CreepDetector[str] = CreepDetector(thresholds.host_creep)

        self._boot_id = system.boot_id() if system is not None else None
        self._segment = 0
        self._segment_started = 0.0
        self._summary = TopAccumulator()
        self._described: dict[Identity, str] = {}
        """Processes this segment already has a ``proc`` record for, and their command line."""
        self._names: dict[Identity, str] = {}
        """Names of the current top N, for the ``leave`` of one that has exited."""

    def request_stop(self) -> None:
        """Ask the loop to finish after the current sample (signal-safe)."""
        self._stop = True

    def run(self) -> TopSummary:
        """Sample until stopped, or a configured limit is reached. Always writes a summary."""
        self._config.validate()
        origin = self._clock.monotonic()
        self._open_segment(elapsed=0.0)

        seq = 0
        previous_at: float | None = None
        deadline = origin
        try:
            while not self._stop:
                self._clock.sleep_until(deadline)
                sampled_at = self._clock.monotonic()
                interval = self._pacing.interval
                dt = 0.0 if previous_at is None else sampled_at - previous_at
                overrun = previous_at is not None and dt > interval * TOP.overrun_factor
                elapsed = sampled_at - origin
                if self._segment_due(elapsed):
                    self._roll_segment(elapsed)

                self._sample(seq=seq, elapsed=elapsed, dt=dt, interval=interval, overrun=overrun)
                previous_at = sampled_at
                seq += 1

                next_interval = self._pacing.after(self._clock.monotonic() - sampled_at)
                # A late sample moves the schedule rather than being followed by
                # a burst of catch-up samples: the gap is flagged, not hidden.
                deadline = max(deadline + next_interval, self._clock.monotonic())
                if self._finished(samples_taken=seq, elapsed=elapsed):
                    break
        finally:
            self._close_gpu()
            summary = self._summary.finish()
            self._sink.close(summary_record(summary))
        return summary

    # -- segments ---------------------------------------------------------

    def _segment_due(self, elapsed: float) -> bool:
        length = self._config.segment_seconds
        return length is not None and elapsed - self._segment_started >= length

    def _open_segment(self, *, elapsed: float) -> None:
        self._segment_started = elapsed
        self._summary = TopAccumulator()
        self._described = {}
        self._sink.open(
            header_record(
                host=self._host,
                config=self._config,
                interval=self._pacing.interval,
                started_at=self._clock.now_iso(),
                boot_id=self._boot_id,
                segment=self._segment,
                memory_kind=self._memory_kind,
                notes=self._notes,
            )
        )

    def _roll_segment(self, elapsed: float) -> None:
        self._sink.close(summary_record(self._summary.finish()))
        self._segment += 1
        self._open_segment(elapsed=elapsed)

    def _finished(self, *, samples_taken: int, elapsed: float) -> bool:
        config = self._config
        if config.max_samples is not None and samples_taken >= config.max_samples:
            return True
        return config.duration is not None and elapsed >= config.duration

    # -- one sample -------------------------------------------------------

    def _sample(self, *, seq: int, elapsed: float, dt: float, interval: float, overrun: bool) -> None:
        timestamp = self._clock.now_iso()
        procs = self._source.scan()
        system = self._system.read() if self._system is not None else None
        host = self._host_reading(system)
        rates, exited = self._cpu.update(procs=procs, dt=dt)
        self._forget(exited)

        events = self._detect(procs=procs, rates=rates, host=host, t=elapsed, ts=timestamp)
        gpu = self._read_gpu()
        selection = self._ranker.rank(procs=procs, cpu=rates, gpu=gpu)
        by_identity = {info.identity: info for info in procs.values()}
        ranked = [
            self._enrich(info=by_identity[identity], reasons=reasons, cpu=rates, gpu=gpu)
            for identity, reasons in selection.chosen.items()
        ]
        events += self._membership(
            entered=selection.entered,
            left=selection.left,
            exited=set(exited),
            procs=by_identity,
            t=elapsed,
            ts=timestamp,
        )
        self._names = {entry.identity: entry.sample.info.comm for entry in ranked}

        for entry in ranked:
            self._describe(entry.sample.info, cmdline=entry.sample.cmdline)
        self._sink.sample(
            sample_record(
                seq=seq,
                t=elapsed,
                ts=timestamp,
                dt=dt,
                interval=interval,
                overrun=overrun,
                host=host,
                n_procs=len(procs),
                ranked=ranked,
            )
        )
        for event in events:
            self._sink.sample(event_record(event))
            self._summary.event(event.kind)
        self._summary.add(
            t=elapsed - self._segment_started,
            dt=dt,
            overrun=overrun,
            host=host,
            n_procs=len(procs),
            ranked=ranked,
            cmdlines=self._described,
        )

    def _host_reading(self, system: SystemSample | None) -> HostReading | None:
        cpu = self._host_cpu.update(system)
        if system is None:
            return None
        return HostReading(
            cpu_percent=cpu,
            mem_used_bytes=system.mem_used_bytes,
            mem_available_bytes=system.mem_available_bytes,
            swap_used_bytes=system.swap_used_bytes,
        )

    def _enrich(
        self,
        *,
        info: ProcInfo,
        reasons: frozenset[Resource],
        cpu: Mapping[Identity, float],
        gpu: Mapping[int, GpuUsage] | None,
    ) -> RankedProc:
        sample = self._source.enrich(info=info, memory=self._config.memory)
        sample.cpu_percent = cpu.get(info.identity)
        sample.via = "top"
        return RankedProc(sample=sample, reasons=reasons, gpu=gpu.get(info.pid) if gpu else None)

    def _describe(self, info: ProcInfo, *, cmdline: str | None = None) -> None:
        """Write a ``proc`` record the first time this segment mentions ``info``."""
        if info.identity in self._described:
            return
        text = cmdline if cmdline is not None else (self._source.read_cmdline(info.pid) or info.comm)
        self._described[info.identity] = text
        self._sink.sample(proc_record(pid=info.pid, starttime=info.starttime, name=info.comm, cmdline=text))

    def _forget(self, exited: Iterable[Identity]) -> None:
        gone = list(exited)
        for identity in gone:
            self._source.forget(identity)
        self._cpu_spikes.forget(gone)
        self._memory_spikes.forget(gone)
        self._creep.forget(gone)

    # -- detection --------------------------------------------------------

    def _detect(
        self,
        *,
        procs: Mapping[int, ProcInfo],
        rates: Mapping[Identity, float],
        host: HostReading | None,
        t: float,
        ts: str,
    ) -> list[TopEvent]:
        events: list[TopEvent] = []
        for info in procs.values():
            if info.is_zombie:
                continue
            identity = info.identity
            cpu = rates.get(identity)
            if cpu is not None:
                spike = self._cpu_spikes.observe(key=identity, value=cpu, t=t)
                if spike is not None:
                    events.append(
                        self._process_event(info, EventKind.SPIKE, Resource.CPU, spike.value, spike.baseline, t, ts)
                    )
            rss = info.rss_bytes
            if rss is None:
                continue
            spike = self._memory_spikes.observe(key=identity, value=float(rss), t=t)
            if spike is not None:
                events.append(
                    self._process_event(info, EventKind.SPIKE, Resource.MEMORY, spike.value, spike.baseline, t, ts)
                )
            creep = self._creep.observe(key=identity, value=float(rss), t=t)
            if creep is not None:
                self._describe(info)
                events.append(
                    TopEvent(
                        kind=EventKind.CREEP,
                        t=round(t, 3),
                        ts=ts,
                        pid=info.pid,
                        starttime=info.starttime,
                        name=info.comm,
                        resource=Resource.MEMORY,
                        value=creep.last,
                        baseline=creep.first,
                        slope_per_hour=round(creep.slope_per_hour, 1),
                        r2=round(creep.r2, 3),
                    )
                )
        if host is not None:
            events += self._detect_host(host=host, t=t, ts=ts)
        return events

    def _detect_host(self, *, host: HostReading, t: float, ts: str) -> list[TopEvent]:
        events: list[TopEvent] = []
        if host.cpu_percent is not None:
            spike = self._host_cpu_spikes.observe(key=HOST_NAME, value=host.cpu_percent, t=t)
            if spike is not None:
                events.append(self._host_event(EventKind.SPIKE, Resource.CPU, spike.value, spike.baseline, t, ts))
        used = host.mem_used_bytes
        if used is not None:
            spike = self._host_memory_spikes.observe(key=HOST_NAME, value=float(used), t=t)
            if spike is not None:
                events.append(self._host_event(EventKind.SPIKE, Resource.MEMORY, spike.value, spike.baseline, t, ts))
            creep = self._host_creep.observe(key=HOST_NAME, value=float(used), t=t)
            if creep is not None:
                events.append(
                    TopEvent(
                        kind=EventKind.CREEP,
                        t=round(t, 3),
                        ts=ts,
                        pid=HOST_PID,
                        starttime=0,
                        name=HOST_NAME,
                        resource=Resource.MEMORY,
                        value=creep.last,
                        baseline=creep.first,
                        slope_per_hour=round(creep.slope_per_hour, 1),
                        r2=round(creep.r2, 3),
                    )
                )
        return events

    def _process_event(
        self,
        info: ProcInfo,
        kind: EventKind,
        resource: Resource,
        value: float,
        baseline: float,
        t: float,
        ts: str,
    ) -> TopEvent:
        self._describe(info)
        return TopEvent(
            kind=kind,
            t=round(t, 3),
            ts=ts,
            pid=info.pid,
            starttime=info.starttime,
            name=info.comm,
            resource=resource,
            value=round(value, 2),
            baseline=round(baseline, 2),
        )

    @staticmethod
    def _host_event(kind: EventKind, resource: Resource, value: float, baseline: float, t: float, ts: str) -> TopEvent:
        return TopEvent(
            kind=kind,
            t=round(t, 3),
            ts=ts,
            pid=HOST_PID,
            starttime=0,
            name=HOST_NAME,
            resource=resource,
            value=round(value, 2),
            baseline=round(baseline, 2),
        )

    def _membership(
        self,
        *,
        entered: Iterable[Identity],
        left: Iterable[Identity],
        exited: set[Identity],
        procs: Mapping[Identity, ProcInfo],
        t: float,
        ts: str,
    ) -> list[TopEvent]:
        events: list[TopEvent] = []
        for identity in entered:
            info = procs[identity]
            events.append(
                TopEvent(
                    kind=EventKind.ENTER, t=round(t, 3), ts=ts, pid=info.pid, starttime=info.starttime, name=info.comm
                )
            )
        for identity in left:
            pid, starttime = identity
            events.append(
                TopEvent(
                    kind=EventKind.LEAVE,
                    t=round(t, 3),
                    ts=ts,
                    pid=pid,
                    starttime=starttime,
                    name=self._names.get(identity, "?"),
                    reason="exit" if identity in exited else "rank",
                )
            )
        return events

    # -- gpu --------------------------------------------------------------

    def _read_gpu(self) -> Mapping[int, GpuUsage] | None:
        if self._gpu is None:
            return None
        try:
            return self._gpu.read()
        except Exception:  # a broken GPU reader must not cost us the sample
            return None

    def _close_gpu(self) -> None:
        if self._gpu is not None:
            with contextlib.suppress(Exception):
                self._gpu.close()
