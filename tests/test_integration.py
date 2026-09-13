"""End-to-end runs against real processes.

These are the tests that actually prove the product claim: that a child which
double-forks, calls setsid and outlives its parent still shows up in the log.
Everything else is a unit test of one piece of that.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

WORKLOAD = os.path.join(os.path.dirname(__file__), "workload.py")
pytestmark = pytest.mark.integration


def prowatch(*args, timeout=90):
    return subprocess.run(
        [sys.executable, "-m", "prowatch.cli", *args],
        capture_output=True, text=True, timeout=timeout,
    )


def read_log(path):
    records = [json.loads(line) for line in open(path) if line.strip()]
    header = next(r for r in records if r["type"] == "header")
    samples = [r for r in records if r["type"] == "sample"]
    summary = next((r for r in records if r["type"] == "summary"), None)
    return header, samples, summary


def tracked_processes(samples):
    """pid -> how it was discovered, across the whole run."""
    return {p["pid"]: p["via"] for s in samples for p in s.get("procs", ())}


def test_run_mode_captures_a_detached_grandchild(tmp_path):
    log = tmp_path / "run.jsonl"

    result = prowatch(
        "run", "-i", "0.25", "-q", "-o", str(log), "--",
        sys.executable, WORKLOAD, "--seconds", "2", "--mb", "48",
        "--detach", "--parent-seconds", "0.3",
    )

    assert result.returncode == 0, result.stderr
    header, samples, summary = read_log(str(log))
    tracked = tracked_processes(samples)

    assert len(tracked) >= 2, f"only found {tracked}"
    # The parent exits after ~0.3s; the detached child runs for ~2s. If the
    # child were lost, the run would have ended almost immediately.
    assert summary["duration_s"] > 1.5
    assert summary["peak_rss_bytes"] > 40 * 1024 * 1024
    assert summary["cpu_seconds_used"] > 1.5
    assert summary["exit_code"] == 0


def test_run_mode_uses_a_cgroup_boundary_when_systemd_is_available(tmp_path):
    log = tmp_path / "run.jsonl"
    prowatch(
        "run", "-i", "0.25", "-q", "-o", str(log), "--",
        sys.executable, WORKLOAD, "--seconds", "1", "--mb", "32", "--detach",
    )
    header, samples, summary = read_log(str(log))

    if header["group_path"] is None:
        pytest.skip("no usable cgroup boundary on this machine")

    assert "prowatch-" in header["group_path"]
    # Kernel-side accounting, not our summation.
    assert any(s["group_memory_bytes"] for s in samples)
    assert tracked_processes(samples)  # and the children were found through it


def test_watch_mode_adopts_a_child_that_detaches_after_we_attach(tmp_path):
    """The hard case for attach mode: the fork happens while we are watching."""
    log = tmp_path / "watch.jsonl"
    workload = subprocess.Popen(
        [sys.executable, WORKLOAD, "--seconds", "3", "--mb", "48", "--detach",
         "--spawn-delay", "1", "--parent-seconds", "0.2"],
        stderr=subprocess.DEVNULL,
    )
    try:
        result = prowatch(
            "watch", "--pid", str(workload.pid), "-i", "0.3", "-q",
            "-o", str(log), "-d", "8",
        )
        assert result.returncode == 0, result.stderr
        header, samples, summary = read_log(str(log))
        tracked = tracked_processes(samples)

        assert len(tracked) >= 2, f"detached child was lost: {tracked}"
        assert "orphan" in tracked.values() or "session" in tracked.values()
        assert summary["duration_s"] > 2.0
        assert summary["peak_rss_bytes"] > 40 * 1024 * 1024
    finally:
        workload.wait(timeout=15)


def test_watch_mode_does_not_adopt_unrelated_processes(tmp_path):
    """Guard against the opposite failure: quietly tracking the whole terminal."""
    log = tmp_path / "watch.jsonl"
    target = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(6)"])
    noise = subprocess.Popen(
        [sys.executable, "-c", "import time,subprocess,sys; time.sleep(0.5);"
         "subprocess.run([sys.executable,'-c','import time;time.sleep(1)'])"]
    )
    try:
        prowatch("watch", "--pid", str(target.pid), "-i", "0.3", "-q",
                 "-o", str(log), "-d", "3")
        _, samples, _ = read_log(str(log))

        assert set(tracked_processes(samples)) == {target.pid}
    finally:
        for process in (target, noise):
            process.kill()
            process.wait(timeout=10)


def test_watch_by_keyword_matches_the_command_line(tmp_path):
    log = tmp_path / "watch.jsonl"
    workload = subprocess.Popen(
        [sys.executable, WORKLOAD, "--seconds", "2", "--mb", "16",
         "--children", "0", "--parent-seconds", "2"],
        stderr=subprocess.DEVNULL,
    )
    try:
        result = prowatch("watch", "workload.py", "-i", "0.3", "-q",
                          "-o", str(log), "-d", "3")
        assert result.returncode == 0, result.stderr
        _, samples, _ = read_log(str(log))

        assert workload.pid in tracked_processes(samples)
    finally:
        workload.wait(timeout=15)


def test_a_missing_process_exits_with_a_clear_message(tmp_path):
    result = prowatch("watch", "definitely-not-running-xyzzy", "-q",
                      "-o", str(tmp_path / "x.jsonl"))

    assert result.returncode == 2
    assert "no process matched" in result.stderr


def test_report_round_trips_a_real_run(tmp_path):
    log = tmp_path / "run.jsonl"
    prowatch("run", "-i", "0.25", "-q", "-o", str(log), "--",
             sys.executable, WORKLOAD, "--seconds", "1", "--mb", "32",
             "--children", "1")

    result = prowatch("report", str(log))

    assert result.returncode == 0
    assert "top by cpu time" in result.stdout
    assert "peak rss" in result.stdout


def test_csv_output_is_written_and_joinable(tmp_path):
    base = tmp_path / "run.csv"
    prowatch("run", "-i", "0.25", "-q", "-f", "csv", "-o", str(base), "--",
             sys.executable, WORKLOAD, "--seconds", "1", "--mb", "16",
             "--children", "1")

    import csv as csv_module

    rows = list(csv_module.DictReader(base.open()))
    procs = list(csv_module.DictReader((tmp_path / "run.procs.csv").open()))
    assert rows and procs
    assert {p["seq"] for p in procs} <= {r["seq"] for r in rows}
