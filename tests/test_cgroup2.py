"""Reading kernel-side group accounting from a fake cgroup2 mount."""

from __future__ import annotations

from conftest import write_cgroup
from treehawk.platforms.linux.cgroup2 import CgroupV2Source


def test_pids_in_includes_nested_groups(cgroup_root):
    # systemd and container runtimes nest; a child moved into a sub-group is
    # still part of the workload.
    write_cgroup(cgroup_root, "/app.scope", pids=[100])
    write_cgroup(cgroup_root, "/app.scope/worker", pids=[101, 102])
    source = CgroupV2Source(str(cgroup_root))

    assert source.pids_in("/app.scope") == {100, 101, 102}
    assert source.pids_in("/app.scope/worker") == {101, 102}


def test_pids_in_returns_none_for_a_group_that_is_gone(cgroup_root):
    assert CgroupV2Source(str(cgroup_root)).pids_in("/vanished.scope") is None


def test_metrics_reads_cpu_and_memory(cgroup_root):
    write_cgroup(
        cgroup_root, "/app.scope", pids=[100],
        cpu_usec=2_500_000, memory=1024, peak=4096,
    )
    metrics = CgroupV2Source(str(cgroup_root)).metrics("/app.scope")

    assert metrics.cpu_usec == 2_500_000
    assert metrics.memory_bytes == 1024
    assert metrics.memory_peak_bytes == 4096


def test_metrics_treats_max_and_missing_files_as_unknown(cgroup_root):
    directory = write_cgroup(cgroup_root, "/app.scope", pids=[100])
    (directory and open(f"{directory}/memory.current", "w")).write("max\n")

    metrics = CgroupV2Source(str(cgroup_root)).metrics("/app.scope")
    assert metrics.memory_bytes is None  # 'max' is a limit, not a reading
    assert metrics.cpu_usec is None  # controller not delegated here
    assert metrics.path == "/app.scope"


def test_available_reflects_a_real_mount(cgroup_root, tmp_path):
    assert CgroupV2Source(str(cgroup_root)).available()
    assert not CgroupV2Source(str(tmp_path / "nope")).available()
