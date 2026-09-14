"""Reading a (fake) /proc tree end to end."""

from __future__ import annotations

import os

from conftest import write_proc
from treehawk.core.config import MemoryDetail
from treehawk.platforms.linux.source import LinuxProcessSource


def test_scan_returns_every_process_and_ignores_non_pid_entries(proc_root):
    write_proc(proc_root, 100, comm="parent")
    write_proc(proc_root, 101, comm="child", ppid=100)
    source = LinuxProcessSource(str(proc_root), page_size=4096)

    procs = source.scan()

    assert set(procs) == {100, 101}  # 'meminfo' and 'self' are skipped
    assert procs[101].ppid == 100


def test_scan_skips_a_process_that_exits_mid_read(proc_root):
    write_proc(proc_root, 100)
    broken = write_proc(proc_root, 101)
    with open(os.path.join(broken, "stat"), "w") as handle:
        handle.write("101 (worker) S")  # truncated, as a racing read would see

    assert set(LinuxProcessSource(str(proc_root)).scan()) == {100}


def test_enrich_adds_cmdline_pss_and_cgroup(proc_root):
    write_proc(
        proc_root, 100, cmdline="python train.py", pss_kb=512,
        swap_kb=8, cgroup="/user.slice/train.scope",
    )
    source = LinuxProcessSource(str(proc_root), page_size=4096)
    info = source.scan()[100]

    sample = source.enrich(info=info, memory=MemoryDetail.PROPORTIONAL)

    assert sample.cmdline == "python train.py"
    assert sample.pss_bytes == 512 * 1024
    assert sample.swap_bytes == 8 * 1024
    assert sample.cgroup == "/user.slice/train.scope"


def test_enrich_skips_pss_when_not_wanted(proc_root):
    write_proc(proc_root, 100, pss_kb=512)
    source = LinuxProcessSource(str(proc_root))
    sample = source.enrich(info=source.scan()[100], memory=MemoryDetail.RESIDENT)
    assert sample.pss_bytes is None


def test_enrich_tolerates_an_unreadable_process(proc_root):
    # Another user's process: stat is readable, smaps_rollup is not.
    write_proc(proc_root, 100, cmdline="secret", pss_kb=None)
    source = LinuxProcessSource(str(proc_root))
    sample = source.enrich(info=source.scan()[100], memory=MemoryDetail.PROPORTIONAL)
    assert sample.pss_bytes is None
    assert sample.info.rss_bytes is not None


def test_cmdline_is_cached_per_identity_and_dropped_on_exit(proc_root):
    write_proc(proc_root, 100, cmdline="first")
    source = LinuxProcessSource(str(proc_root))
    info = source.scan()[100]
    assert source.enrich(info=info, memory=MemoryDetail.RESIDENT).cmdline == "first"

    write_proc(proc_root, 100, cmdline="second")  # same identity: cache wins
    assert source.enrich(info=info, memory=MemoryDetail.RESIDENT).cmdline == "first"

    source.forget(info.identity)
    assert source.enrich(info=info, memory=MemoryDetail.RESIDENT).cmdline == "second"
