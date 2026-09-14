"""Fake /proc and cgroup trees, so the Linux code can be tested deterministically.

Everything that reads the filesystem takes its root as a constructor argument,
which is what makes these fixtures possible without mocking or root privileges.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterable

import pytest

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
