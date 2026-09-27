"""Machine-wide counters from /proc/stat and /proc/meminfo."""

from __future__ import annotations

import pathlib

from treehawk.platforms.linux.procfs import parse_cpu_totals, parse_meminfo
from treehawk.platforms.linux.system import LinuxSystemSource

STAT = "cpu  100 5 50 800 20 3 2 10 0 0\ncpu0 50 2 25 400 10 1 1 5 0 0\nintr 12345\n"
MEMINFO = (
    "MemTotal:       16000000 kB\n"
    "MemFree:         1000000 kB\n"
    "MemAvailable:    6000000 kB\n"
    "SwapTotal:       2000000 kB\n"
    "SwapFree:        1500000 kB\n"
)


def test_cpu_totals_count_idle_and_iowait_as_not_busy() -> None:
    assert parse_cpu_totals(STAT) == (170, 990)


def test_cpu_totals_of_a_malformed_file() -> None:
    assert parse_cpu_totals("intr 1\n") is None
    assert parse_cpu_totals("cpu  a b c d\n") is None


def test_meminfo() -> None:
    assert parse_meminfo(MEMINFO) == {
        "mem_total_bytes": 16000000 * 1024,
        "mem_available_bytes": 6000000 * 1024,
        "swap_total_bytes": 2000000 * 1024,
        "swap_free_bytes": 1500000 * 1024,
    }


def test_the_source_reads_a_proc_tree(proc_root: pathlib.Path) -> None:
    (proc_root / "stat").write_text(STAT)
    (proc_root / "meminfo").write_text(MEMINFO)
    boot = proc_root / "sys" / "kernel" / "random"
    boot.mkdir(parents=True)
    (boot / "boot_id").write_text("5d1e8a52-6a5b-4c1f-9f0e-000000000001\n")
    source = LinuxSystemSource(str(proc_root))
    sample = source.read()
    assert sample is not None
    assert sample.cpu_busy_ticks == 170
    assert sample.mem_used_bytes == 10000000 * 1024
    assert sample.swap_used_bytes == 500000 * 1024
    assert source.boot_id() == "5d1e8a52-6a5b-4c1f-9f0e-000000000001"


def test_the_source_without_proc(tmp_path: pathlib.Path) -> None:
    source = LinuxSystemSource(str(tmp_path / "missing"))
    assert source.read() is None
    assert source.boot_id() is None
