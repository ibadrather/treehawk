"""Fake kernels, so the platform code can be tested deterministically.

On Linux everything that reads the filesystem takes its root as a constructor
argument, which is what makes the fake /proc and cgroup trees below possible
without mocking or root privileges.

macOS has no filesystem to fake, so the seam is the syscall boundary instead:
:class:`FakeProcessTable` implements the same ``ProcessTable`` protocol the
real ``LibProc`` does. Because nothing in ``platforms/darwin`` imports the C
library until it is constructed, these tests run on Linux too.

Above the kernel, the rest of the machine is faked the same way: a clock that
never waits, a sink that remembers, a launcher that starts nothing, and
:func:`fake_platform`, which bundles the fake /proc and cgroup trees into the
``Platform`` the command line is handed.
"""

from __future__ import annotations

import pathlib
import signal
from collections.abc import Iterable
from dataclasses import dataclass, field

import pytest

from treehawk.core.compat import override
from treehawk.core.errors import LaunchFailed
from treehawk.core.interfaces import ProcessLauncher, Record
from treehawk.core.models import HostInfo, LaunchedWorkload
from treehawk.platforms.darwin.models import TaskInfo, Timebase
from treehawk.platforms.linux.cgroup2 import CgroupV2Source
from treehawk.platforms.linux.source import LinuxProcessSource
from treehawk.platforms.models import Platform
from treehawk.sinks.base import BaseSink

APPLE_SILICON_TIMEBASE = Timebase(numer=125, denom=3)
"""The real fraction on an M-series Mac; Intel reports 1/1. Tests use it so a
mach-to-nanoseconds mistake cannot pass unnoticed."""

# Field layout of /proc/<pid>/stat after the comm field; only the fields
# treehawk reads carry meaningful values here.
_STAT_TEMPLATE = (
    "{pid} ({comm}) {state} {ppid} {pgid} {sid} 0 -1 4194304 100 0 0 0 "
    "{utime} {stime} 0 0 20 0 {threads} 0 {starttime} 16637952 {rss_pages} " + " ".join(["0"] * 30)
)


def write_proc(
    root: pathlib.Path,
    pid: int,
    *,
    comm: str = "worker",
    state: str = "S",
    ppid: int = 1,
    pgid: int | None = None,
    sid: int | None = None,
    utime: int = 0,
    stime: int = 0,
    threads: int = 1,
    starttime: int = 1000,
    rss_pages: int = 256,
    cmdline: str = "python worker.py",
    cgroup: str = "/user.slice/app.scope",
    pss_kb: int | None = None,
    swap_kb: int = 0,
) -> pathlib.Path:
    """Materialise one process inside a fake /proc."""
    directory = root / str(pid)
    directory.mkdir(exist_ok=True, parents=True)
    stat = _STAT_TEMPLATE.format(
        pid=pid,
        comm=comm,
        state=state,
        ppid=ppid,
        pgid=pgid if pgid is not None else pid,
        sid=sid if sid is not None else pid,
        utime=utime,
        stime=stime,
        threads=threads,
        starttime=starttime,
        rss_pages=rss_pages,
    )
    _write(directory, "stat", stat + "\n")
    _write(directory, "cmdline", cmdline.replace(" ", "\x00") + "\x00")
    _write(directory, "cgroup", f"0::{cgroup}\n")
    _write(
        directory,
        "status",
        f"Name:\t{comm}\nVmRSS:\t{rss_pages * 4} kB\nVmSwap:\t{swap_kb} kB\n",
    )
    if pss_kb is not None:
        _write(directory, "smaps_rollup", f"Rss:\t{rss_pages * 4} kB\nPss:\t{pss_kb} kB\n")
    return directory


def write_cgroup(
    root: pathlib.Path,
    path: str,
    *,
    pids: Iterable[int] = (),
    cpu_usec: int | None = None,
    memory: int | None = None,
    peak: int | None = None,
) -> pathlib.Path:
    """Materialise one cgroup directory inside a fake cgroup2 mount."""
    directory = root / path.lstrip("/")
    directory.mkdir(exist_ok=True, parents=True)
    _write(directory, "cgroup.procs", "".join(f"{pid}\n" for pid in pids))
    if cpu_usec is not None:
        _write(directory, "cpu.stat", f"usage_usec {cpu_usec}\nuser_usec {cpu_usec}\n")
    if memory is not None:
        _write(directory, "memory.current", f"{memory}\n")
    if peak is not None:
        _write(directory, "memory.peak", f"{peak}\n")
    return directory


@dataclass(frozen=True, slots=True)
class FakeProcess:
    """One process in a fake macOS process table."""

    pid: int
    ppid: int = 1
    pgid: int | None = None
    sid: int | None = None
    status: int = 3  # SSLEEP
    starttime_usec: int = 1_700_000_000_000_000
    comm: str = "worker"
    cpu_mach_units: int = 0
    rss_bytes: int = 4 * 1024 * 1024
    threads: int = 1
    coalition: int | None = 1779
    argv: str = "python worker.py"
    footprint: int | None = None
    """None stands for the EPERM another user's process returns."""


@dataclass
class FakeProcessTable:
    """A ``ProcessTable`` with no kernel behind it.

    Counts its own reads, so a test can assert that the command line really is
    cached rather than fetched once per sample.
    """

    processes: list[FakeProcess] = field(default_factory=list)
    timebase_fraction: Timebase = APPLE_SILICON_TIMEBASE
    argv_reads: int = 0

    def add(self, process: FakeProcess) -> None:
        self.processes.append(process)

    def remove(self, pid: int) -> None:
        """Let a process exit between two scans."""
        self.processes = [process for process in self.processes if process.pid != pid]

    def _find(self, pid: int) -> FakeProcess | None:
        return next((process for process in self.processes if process.pid == pid), None)

    # -- the ProcessTable protocol ----------------------------------------

    def timebase(self) -> Timebase:
        return self.timebase_fraction

    def list_pids(self) -> list[int]:
        return [process.pid for process in self.processes]

    def task_info(self, pid: int) -> TaskInfo | None:
        process = self._find(pid)
        if process is None:
            return None
        return TaskInfo(
            pid=process.pid,
            ppid=process.ppid,
            pgid=process.pgid if process.pgid is not None else process.pid,
            status=process.status,
            starttime_usec=process.starttime_usec,
            comm=process.comm,
            cpu_mach_units=process.cpu_mach_units,
            rss_bytes=process.rss_bytes,
            threads=process.threads,
        )

    def coalition_id(self, pid: int) -> int | None:
        process = self._find(pid)
        return None if process is None else process.coalition

    def argv(self, pid: int) -> str:
        self.argv_reads += 1
        process = self._find(pid)
        return "" if process is None else process.argv

    def footprint(self, pid: int) -> int | None:
        process = self._find(pid)
        return None if process is None else process.footprint

    def session_id(self, pid: int) -> int:
        process = self._find(pid)
        if process is None:
            return 0
        return process.sid if process.sid is not None else process.pid


@pytest.fixture
def table() -> FakeProcessTable:
    """An empty macOS process table, for the darwin backend tests."""
    return FakeProcessTable()


def _write(directory: pathlib.Path, name: str, text: str) -> None:
    (directory / name).write_text(text)


@pytest.fixture
def proc_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "proc"
    root.mkdir()
    # Noise that every real /proc has and every scan must ignore.
    (root / "meminfo").write_text("MemTotal:       32471504 kB\n")
    (root / "self").mkdir()
    return root


@pytest.fixture
def cgroup_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cgroup.controllers").write_text("cpu memory pids\n")
    return root


def write_log(path: pathlib.Path, *, samples: int = 6, per_process: bool = True, interval: float = 0.5) -> str:
    """A realistic JSONL log, for the readers, the views and the PDF report."""
    import json

    records: list[dict[str, object]] = [
        {
            "type": "header",
            "schema": 1,
            "treehawk_version": "0.1.0",
            "started_at": "2026-01-01T00:00:00Z",
            "mode": "run",
            "matcher": {"kind": "pid", "value": 100},
            "argv": ["python", "train.py"],
            "interval": interval,
            "expand": ["tree", "cgroup", "session", "orphan"],
            "per_process": per_process,
            "group_path": "/user.slice/treehawk-1.scope",
            "host": {
                "platform": "linux",
                "hostname": "test",
                "ncpu": 4,
                "clk_tck": 100,
                "page_size": 4096,
                "mem_total_bytes": 8 * 1024**3,
            },
            "notes": [],
        }
    ]
    for seq in range(samples):
        procs: list[dict[str, object]] = []
        if per_process:
            procs.append(
                {
                    "pid": 100,
                    "ppid": 1,
                    "starttime": 1000,
                    "name": "python",
                    "cmdline": "python train.py",
                    "state": "R",
                    "threads": 2,
                    "cpu_percent": None if seq == 0 else 90.0 + seq,
                    "cpu_seconds": seq * 0.5,
                    "rss_bytes": 50_000_000 + seq * 1_000_000,
                    "pss_bytes": 40_000_000,
                    "swap_bytes": 0,
                    "via": "match",
                }
            )
            if seq >= 2:
                procs.append(
                    {
                        "pid": 200,
                        "ppid": 1,
                        "starttime": 2000,
                        "name": "worker",
                        "cmdline": "python worker.py",
                        "state": "R",
                        "threads": 1,
                        "cpu_percent": 70.0,
                        "cpu_seconds": (seq - 1) * 0.4,
                        "rss_bytes": 20_000_000,
                        "pss_bytes": 15_000_000,
                        "swap_bytes": 0,
                        "via": "orphan",
                    }
                )
        records.append(
            {
                "type": "sample",
                "seq": seq,
                "t": round(seq * interval, 3),
                "ts": f"2026-01-01T00:00:{seq:02d}Z",
                "n_procs": len(procs) or 1,
                "cpu_percent": None if seq == 0 else 90.0 + seq + (70.0 if seq >= 2 else 0),
                "cpu_percent_norm": None if seq == 0 else 25.0,
                "cpu_seconds_total": seq * 0.9,
                "cpu_seconds_used": seq * 0.9,
                "rss_bytes": 50_000_000 + seq * 1_000_000,
                "pss_bytes": 40_000_000,
                "swap_bytes": 0,
                "group_memory_bytes": 45_000_000,
                "group_memory_peak_bytes": 60_000_000,
                "overrun": seq == 3,
                "procs": procs,
            }
        )
    records.append(
        {
            "type": "summary",
            "schema": 1,
            "samples": samples,
            "duration_s": round((samples - 1) * interval, 3),
            "peak_cpu_percent": 165.0,
            "mean_cpu_percent": 120.0,
            "peak_n_procs": 2,
            "total_procs_seen": 2 if per_process else 0,
            "peak_rss_bytes": 55_000_000,
            "peak_pss_bytes": 40_000_000,
            "peak_group_memory_bytes": 60_000_000,
            "cpu_seconds_total": 4.5,
            "cpu_seconds_used": 4.5,
            "overruns": 1,
            "exit_code": 0,
            "top_by_cpu": [],
            "top_by_memory": [],
        }
    )
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    return str(path)


@pytest.fixture
def log(tmp_path: pathlib.Path) -> str:
    return write_log(tmp_path / "run.jsonl")


# -- the rest of the machine ------------------------------------------------


class FakeClock:
    """Time only moves when the loop asks it to."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def now_iso(self) -> str:
        return f"2026-01-01T00:00:{self.t:06.3f}Z"

    def sleep_until(self, deadline: float) -> None:
        self.sleeps.append(deadline)
        self.t = max(self.t, deadline)


class RecordingSink(BaseSink):
    """Keeps every record it is sent, for the test to read back."""

    def __init__(self) -> None:
        self.header: Record | None = None
        self.samples: list[Record] = []
        self.summary: Record | None = None

    @override
    def open(self, header: Record) -> None:
        self.header = header

    @override
    def sample(self, record: Record) -> None:
        self.samples.append(record)

    @override
    def close(self, summary: Record) -> None:
        self.summary = summary


class FakeWorkload(LaunchedWorkload):
    """A launched workload with no process behind it.

    ``poll`` reports ``exit_code`` until a signal ends it. One that ignores
    SIGTERM keeps running until SIGKILL, like a workload that will not shut down.
    """

    def __init__(
        self,
        *,
        pid: int,
        argv: list[str],
        group_path: str | None = None,
        notes: tuple[str, ...] = (),
        exit_code: int | None = 0,
        ignores_sigterm: bool = False,
    ) -> None:
        super().__init__(pid=pid, argv=argv, group_path=group_path, notes=notes)
        self.exit_code = exit_code
        self.ignores_sigterm = ignores_sigterm
        self.signals: list[int] = []

    @override
    def poll(self) -> int | None:
        return self.exit_code

    @override
    def signal(self, signum: int = signal.SIGTERM) -> None:
        self.signals.append(signum)
        if self.exit_code is None and (signum == signal.SIGKILL or not self.ignores_sigterm):
            self.exit_code = -signum


@dataclass
class FakeLauncher:
    """A ``ProcessLauncher`` that starts nothing, and keeps what it "started"."""

    launcher_name: str = "fake"
    is_available: bool = True
    failure: str | None = None
    """When set, :meth:`launch` fails with this message."""
    pid: int = 100
    group_path: str | None = None
    notes: tuple[str, ...] = ()
    exit_code: int | None = 0
    ignores_sigterm: bool = False
    workloads: list[FakeWorkload] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.launcher_name

    def available(self) -> bool:
        return self.is_available

    def launch(self, argv: list[str]) -> LaunchedWorkload:
        if self.failure is not None:
            raise LaunchFailed(self.failure)
        workload = FakeWorkload(
            pid=self.pid,
            argv=list(argv),
            group_path=self.group_path,
            notes=self.notes,
            exit_code=self.exit_code,
            ignores_sigterm=self.ignores_sigterm,
        )
        self.workloads.append(workload)
        return workload


TEST_HOST = HostInfo(
    platform="linux",
    hostname="test",
    ncpu=4,
    clk_tck=100,
    page_size=4096,
    mem_total_bytes=8 * 1024**3,
)


@dataclass(frozen=True)
class FakeHost:
    """A ``HostInfoSource`` that describes a fixed machine rather than this one."""

    info: HostInfo

    def host_info(self) -> HostInfo:
        return self.info


def fake_platform(
    proc_root: pathlib.Path,
    cgroup_root: pathlib.Path,
    *,
    launcher: ProcessLauncher | None = None,
    direct_launcher: ProcessLauncher | None = None,
) -> Platform:
    """The whole platform over the fake /proc and cgroup trees.

    This is what the command line is handed in tests: every read goes to the
    fake trees, and nothing is launched unless a fake launcher is given.
    """
    return Platform(
        name="linux",
        process_source=LinuxProcessSource(str(proc_root), page_size=TEST_HOST.page_size),
        host_source=FakeHost(TEST_HOST),
        group_source=CgroupV2Source(str(cgroup_root)),
        launcher=launcher,
        direct_launcher=direct_launcher,
    )
