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

from collections.abc import Iterable, Mapping

from treehawk.core.config import TopConfig
from treehawk.core.interfaces import Clock, ProcessSource, Sink, SystemSource
from treehawk.core.models import HostInfo, Identity, ProcInfo, SystemSample
from treehawk.core.records import PSS
from treehawk.top.constants import TOP
from treehawk.top.detectors import CreepDetector, SpikeDetector
from treehawk.top.models import (
    HOST,
    Creep,
    EventKind,
    HostReading,
    RankedProc,
    Resource,
    Spike,
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
        self._pacing = pacing if pacing is not None else FixedInterval(config.interval)
        self._memory_kind = memory_kind
        self._notes = list(notes)
        self._stop = False

        self._cpu = CpuRates(clk_tck=host.clk_tck)
        self._host_cpu = HostCpuRate()
        self._ranker = Ranker(n=config.top_n)
        self._cpu_spikes = SpikeDetector(thresholds.cpu_spike)
        self._memory_spikes = SpikeDetector(thresholds.memory_spike)
        self._creep = CreepDetector(thresholds.creep)
        self._host_cpu_spikes = SpikeDetector(thresholds.host_cpu_spike)
        self._host_memory_spikes = SpikeDetector(thresholds.host_memory_spike)
        self._host_creep = CreepDetector(thresholds.host_creep)

        self._boot_id = system.boot_id() if system is not None else None
        self._segment = 0
        self._segment_started = 0.0
        self._summary = TopAccumulator()
        self._described: set[Identity] = set()
        """Processes this segment already has a ``proc`` record for."""
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
        self._described = set()
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
        selection = self._ranker.rank(procs=procs, cpu=rates)
        by_identity = {info.identity: info for info in procs.values()}
        ranked = [
            self._enrich(info=by_identity[identity], reasons=reasons, cpu=rates)
            for identity, reasons in selection.chosen.items()
        ]
        for identity in selection.entered:
            info = by_identity[identity]
            events.append(
                _event(EventKind.ENTER, pid=info.pid, starttime=info.starttime, name=info.comm, t=elapsed, ts=timestamp)
            )
        gone = set(exited)
        for identity in selection.left:
            pid, starttime = identity
            events.append(
                _event(
                    EventKind.LEAVE,
                    pid=pid,
                    starttime=starttime,
                    name=self._names.get(identity, "?"),
                    t=elapsed,
                    ts=timestamp,
                    reason="exit" if identity in gone else "rank",
                )
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
        self._summary.add(t=elapsed - self._segment_started, dt=dt, overrun=overrun, host=host, n_procs=len(procs))

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

    def _enrich(self, *, info: ProcInfo, reasons: frozenset[Resource], cpu: Mapping[Identity, float]) -> RankedProc:
        sample = self._source.enrich(info=info, memory=self._config.memory)
        sample.cpu_percent = cpu.get(info.identity)
        sample.via = "top"
        return RankedProc(sample=sample, reasons=reasons)

    def _describe(self, info: ProcInfo, *, cmdline: str | None = None) -> None:
        """Write a ``proc`` record the first time this segment mentions ``info``."""
        if info.identity in self._described:
            return
        self._described.add(info.identity)
        text = cmdline if cmdline is not None else (self._source.read_cmdline(info.pid) or info.comm)
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
            found = _check(
                key=info.identity,
                name=info.comm,
                cpu=rates.get(info.identity),
                memory=info.rss_bytes,
                cpu_spikes=self._cpu_spikes,
                memory_spikes=self._memory_spikes,
                creep=self._creep,
                t=t,
                ts=ts,
            )
            if found:
                self._describe(info)
                events += found
        if host is not None:
            events += _check(
                key=HOST,
                name=HOST_NAME,
                cpu=host.cpu_percent,
                memory=host.mem_used_bytes,
                cpu_spikes=self._host_cpu_spikes,
                memory_spikes=self._host_memory_spikes,
                creep=self._host_creep,
                t=t,
                ts=ts,
            )
        return events


def _check(
    *,
    key: Identity,
    name: str,
    cpu: float | None,
    memory: int | None,
    cpu_spikes: SpikeDetector,
    memory_spikes: SpikeDetector,
    creep: CreepDetector,
    t: float,
    ts: str,
) -> list[TopEvent]:
    """Run one process, or the machine, through its detectors."""
    pid, starttime = key
    events: list[TopEvent] = []
    if cpu is not None:
        spike = cpu_spikes.observe(key=key, value=cpu, t=t)
        if spike is not None:
            events.append(
                _event(
                    EventKind.SPIKE,
                    pid=pid,
                    starttime=starttime,
                    name=name,
                    t=t,
                    ts=ts,
                    resource=Resource.CPU,
                    spike=spike,
                )
            )
    if memory is not None:
        spike = memory_spikes.observe(key=key, value=float(memory), t=t)
        if spike is not None:
            events.append(
                _event(
                    EventKind.SPIKE,
                    pid=pid,
                    starttime=starttime,
                    name=name,
                    t=t,
                    ts=ts,
                    resource=Resource.MEMORY,
                    spike=spike,
                )
            )
        rising = creep.observe(key=key, value=float(memory), t=t)
        if rising is not None:
            events.append(
                _event(
                    EventKind.CREEP,
                    pid=pid,
                    starttime=starttime,
                    name=name,
                    t=t,
                    ts=ts,
                    resource=Resource.MEMORY,
                    creep=rising,
                )
            )
    return events


def _event(
    kind: EventKind,
    *,
    pid: int,
    starttime: int,
    name: str,
    t: float,
    ts: str,
    resource: Resource | None = None,
    spike: Spike | None = None,
    creep: Creep | None = None,
    reason: str | None = None,
) -> TopEvent:
    """One event; a spike carries its reading and baseline, a creep the growth of its floor."""
    value = baseline = slope_per_hour = r2 = None
    if spike is not None:
        value, baseline = round(spike.value, 2), round(spike.baseline, 2)
    if creep is not None:
        value, baseline = creep.last, creep.first
        slope_per_hour, r2 = round(creep.slope_per_hour, 1), round(creep.r2, 3)
    return TopEvent(
        kind=kind,
        t=round(t, 3),
        ts=ts,
        pid=pid,
        starttime=starttime,
        name=name,
        resource=resource,
        value=value,
        baseline=baseline,
        slope_per_hour=slope_per_hour,
        r2=r2,
        reason=reason,
    )
