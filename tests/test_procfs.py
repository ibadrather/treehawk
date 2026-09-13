"""Parsing /proc text, including the shapes that break naive parsers."""

from __future__ import annotations

import pytest

from prowatch.platforms.linux.procfs import (
    ProcStatParseError,
    parse_cgroup,
    parse_cmdline,
    parse_meminfo_total,
    parse_smaps_rollup,
    parse_stat,
    parse_status_memory,
)

REAL_STAT = (
    "18973 (cat) R 18971 18973 18971 0 -1 4194304 469 0 0 0 12 34 0 0 20 0 7 0 "
    "65717 16637952 1788 " + " ".join(["0"] * 30)
)


def test_parse_stat_reads_the_fields_prowatch_depends_on():
    parsed = parse_stat(REAL_STAT, page_size=4096)
    assert parsed["pid"] == 18973
    assert parsed["comm"] == "cat"
    assert parsed["state"] == "R"
    assert parsed["ppid"] == 18971
    assert parsed["sid"] == 18971
    assert parsed["cpu_ticks"] == 12 + 34
    assert parsed["threads"] == 7
    assert parsed["starttime"] == 65717
    assert parsed["rss_bytes"] == 1788 * 4096


def test_parse_stat_survives_a_command_name_containing_spaces_and_parens():
    # comm is attacker-controlled in effect: any program can rename itself.
    line = REAL_STAT.replace("(cat)", "(we ird) (name)")
    parsed = parse_stat(line)
    assert parsed["comm"] == "we ird) (name"
    assert parsed["ppid"] == 18971
    assert parsed["starttime"] == 65717


def test_parse_stat_rejects_a_truncated_line():
    # Reading /proc races with process exit; a partial read must not be trusted.
    with pytest.raises(ProcStatParseError):
        parse_stat("18973 (cat) R 18971")


def test_parse_cmdline_joins_nul_separated_arguments():
    assert parse_cmdline(b"python\x00train.py\x00--epochs\x0010\x00") == (
        "python train.py --epochs 10"
    )


def test_parse_cmdline_of_a_kernel_thread_is_empty():
    assert parse_cmdline(b"") == ""


def test_parse_status_and_smaps_convert_kb_to_bytes():
    assert parse_status_memory("VmRSS:\t2048 kB\nVmSwap:\t16 kB\n") == {
        "rss_bytes": 2048 * 1024,
        "swap_bytes": 16 * 1024,
    }
    assert parse_smaps_rollup("Rss:\t2048 kB\nPss:\t1024 kB\nSwap:\t0 kB\n") == {
        "pss_bytes": 1024 * 1024,
        "swap_bytes": 0,
    }


def test_parse_cgroup_takes_the_v2_line():
    text = "1:name=systemd:/legacy\n0::/user.slice/user-1000.slice/app.scope\n"
    assert parse_cgroup(text) == "/user.slice/user-1000.slice/app.scope"


def test_parse_cgroup_returns_none_without_a_v2_hierarchy():
    assert parse_cgroup("1:cpu:/legacy\n") is None


def test_parse_meminfo_total():
    assert parse_meminfo_total("MemTotal:       32471504 kB\nMemFree: 1 kB\n") == (
        32471504 * 1024
    )
