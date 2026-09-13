"""Fake /proc and cgroup trees, so the Linux code can be tested deterministically.

Everything that reads the filesystem takes its root as a constructor argument,
which is what makes these fixtures possible without mocking or root privileges.
"""

from __future__ import annotations

import os

import pytest

# Field layout of /proc/<pid>/stat after the comm field; only the fields
# prowatch reads carry meaningful values here.
_STAT_TEMPLATE = (
    "{pid} ({comm}) {state} {ppid} {pgid} {sid} 0 -1 4194304 100 0 0 0 "
    "{utime} {stime} 0 0 20 0 {threads} 0 {starttime} 16637952 {rss_pages} "
    + " ".join(["0"] * 30)
)


def write_proc(
    root,
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
):
    """Materialise one process inside a fake /proc."""
    directory = os.path.join(str(root), str(pid))
    os.makedirs(directory, exist_ok=True)
    stat = _STAT_TEMPLATE.format(
        pid=pid, comm=comm, state=state, ppid=ppid,
        pgid=pgid if pgid is not None else pid,
        sid=sid if sid is not None else pid,
        utime=utime, stime=stime, threads=threads,
        starttime=starttime, rss_pages=rss_pages,
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


def write_cgroup(root, path: str, *, pids=(), cpu_usec=None, memory=None, peak=None):
    """Materialise one cgroup directory inside a fake cgroup2 mount."""
    directory = os.path.join(str(root), path.lstrip("/"))
    os.makedirs(directory, exist_ok=True)
    _write(directory, "cgroup.procs", "".join(f"{pid}\n" for pid in pids))
    if cpu_usec is not None:
        _write(directory, "cpu.stat", f"usage_usec {cpu_usec}\nuser_usec {cpu_usec}\n")
    if memory is not None:
        _write(directory, "memory.current", f"{memory}\n")
    if peak is not None:
        _write(directory, "memory.peak", f"{peak}\n")
    return directory


def _write(directory: str, name: str, text: str) -> None:
    with open(os.path.join(directory, name), "w") as handle:
        handle.write(text)


@pytest.fixture
def proc_root(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    # Noise that every real /proc has and every scan must ignore.
    (root / "meminfo").write_text("MemTotal:       32471504 kB\n")
    (root / "self").mkdir()
    return root


@pytest.fixture
def cgroup_root(tmp_path):
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cgroup.controllers").write_text("cpu memory pids\n")
    return root
