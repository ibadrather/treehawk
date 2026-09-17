"""End-to-end runs against real processes.

These are the tests that actually prove the product claim: that a child which
double-forks, calls setsid and outlives its parent still shows up in the log.
Everything else is a unit test of one piece of that.
"""

from __future__ import annotations

import json
import os
import pathlib
import pty
import subprocess
import sys

import pytest

from treehawk.core.interfaces import Record
from treehawk.core.values import as_float, as_records

WORKLOAD = str(pathlib.Path(__file__).parent / "workload.py")
pytestmark = pytest.mark.integration


def treehawk(*args: str, timeout: float = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "treehawk", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def treehawk_on_a_terminal(*args: str, timeout: float = 90) -> str:
    """Run treehawk with a pty for its own stdio, and return what it drew.

    Capturing the workload only happens when there is a live region to protect,
    which means a real terminal - so the only honest way to exercise it is to
    give treehawk one.
    """
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-m", "treehawk", *args],
        stdin=slave,
        stdout=slave,
        stderr=slave,
    )
    os.close(slave)
    drawn = bytearray()
    with os.fdopen(master, "rb", 0) as stream:
        while True:
            try:
                chunk = stream.read(65536)
            except OSError:
                break  # the pty reports the last writer letting go as EIO
            if not chunk:
                break
            drawn += chunk
    assert process.wait(timeout) == 0
    return drawn.decode("utf-8", "replace")


def read_log(path: str) -> tuple[Record, list[Record], Record]:
    lines = pathlib.Path(path).read_text().splitlines()
    records: list[Record] = [json.loads(line) for line in lines if line.strip()]
    header = next(r for r in records if r["type"] == "header")
    samples = [r for r in records if r["type"] == "sample"]
    summary = next((r for r in records if r["type"] == "summary"), {})
    return header, samples, summary


def tracked_processes(samples: list[Record]) -> dict[object, object]:
    """pid -> how it was discovered, across the whole run."""
    return {p["pid"]: p["via"] for s in samples for p in as_records(s.get("procs"))}


def number(record: Record, key: str) -> float:
    """A numeric field the test needs the record to carry."""
    value = as_float(record.get(key))
    assert value is not None, f"{key} is missing from {record}"
    return value


def test_run_mode_captures_a_detached_grandchild(tmp_path: pathlib.Path) -> None:
    log = tmp_path / "run.jsonl"

    result = treehawk(
        "run",
        "-i",
        "0.25",
        "-q",
        "-o",
        str(log),
        "--",
        sys.executable,
        WORKLOAD,
        "--seconds",
        "2",
        "--mb",
        "48",
        "--detach",
        "--parent-seconds",
        "0.3",
    )

    assert result.returncode == 0, result.stderr
    _, samples, summary = read_log(str(log))
    tracked = tracked_processes(samples)

    assert len(tracked) >= 2, f"only found {tracked}"
    # The parent exits after ~0.3s; the detached child runs for ~2s. If the
    # child were lost, the run would have ended almost immediately.
    assert number(summary, "duration_s") > 1.5
    assert number(summary, "peak_rss_bytes") > 40 * 1024 * 1024
    assert number(summary, "cpu_seconds_used") > 1.5
    assert summary["exit_code"] == 0


def test_run_mode_uses_a_cgroup_boundary_when_systemd_is_available(tmp_path: pathlib.Path) -> None:
    log = tmp_path / "run.jsonl"
    treehawk(
        "run",
        "-i",
        "0.25",
        "-q",
        "-o",
        str(log),
        "--",
        sys.executable,
        WORKLOAD,
        "--seconds",
        "1",
        "--mb",
        "32",
        "--detach",
    )
    header, samples, _ = read_log(str(log))

    group_path = header["group_path"]
    if group_path is None:
        pytest.skip("no usable cgroup boundary on this machine")

    assert isinstance(group_path, str)
    assert "treehawk-" in group_path
    # Kernel-side accounting, not our summation.
    assert any(s["group_memory_bytes"] for s in samples)
    assert tracked_processes(samples)  # and the children were found through it


def test_watch_mode_adopts_a_child_that_detaches_after_we_attach(tmp_path: pathlib.Path) -> None:
    """The hard case for attach mode: the fork happens while we are watching."""
    log = tmp_path / "watch.jsonl"
    workload = subprocess.Popen(
        [
            sys.executable,
            WORKLOAD,
            "--seconds",
            "3",
            "--mb",
            "48",
            "--detach",
            "--spawn-delay",
            "1",
            "--parent-seconds",
            "0.2",
        ],
        stderr=subprocess.DEVNULL,
    )
    try:
        result = treehawk(
            "watch",
            "--pid",
            str(workload.pid),
            "-i",
            "0.3",
            "-q",
            "-o",
            str(log),
            "-d",
            "8",
        )
        assert result.returncode == 0, result.stderr
        _, samples, summary = read_log(str(log))
        tracked = tracked_processes(samples)

        assert len(tracked) >= 2, f"detached child was lost: {tracked}"
        assert "orphan" in tracked.values() or "session" in tracked.values()
        assert number(summary, "duration_s") > 2.0
        assert number(summary, "peak_rss_bytes") > 40 * 1024 * 1024
    finally:
        workload.wait(timeout=15)


def test_watch_mode_does_not_adopt_unrelated_processes(tmp_path: pathlib.Path) -> None:
    """Guard against the opposite failure: quietly tracking the whole terminal."""
    log = tmp_path / "watch.jsonl"
    target = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(6)"])
    noise = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import time,subprocess,sys; time.sleep(0.5);"
                "subprocess.run([sys.executable,'-c','import time;time.sleep(1)'])"
            ),
        ]
    )
    try:
        treehawk("watch", "--pid", str(target.pid), "-i", "0.3", "-q", "-o", str(log), "-d", "3")
        _, samples, _ = read_log(str(log))

        assert set(tracked_processes(samples)) == {target.pid}
    finally:
        for process in (target, noise):
            process.kill()
            process.wait(timeout=10)


def test_watch_by_keyword_matches_the_command_line(tmp_path: pathlib.Path) -> None:
    log = tmp_path / "watch.jsonl"
    workload = subprocess.Popen(
        [sys.executable, WORKLOAD, "--seconds", "2", "--mb", "16", "--children", "0", "--parent-seconds", "2"],
        stderr=subprocess.DEVNULL,
    )
    try:
        result = treehawk("watch", "workload.py", "-i", "0.3", "-q", "-o", str(log), "-d", "3")
        assert result.returncode == 0, result.stderr
        _, samples, _ = read_log(str(log))

        assert workload.pid in tracked_processes(samples)
    finally:
        workload.wait(timeout=15)


def test_a_missing_process_exits_with_a_clear_message(tmp_path: pathlib.Path) -> None:
    result = treehawk("watch", "definitely-not-running-xyzzy", "-q", "-o", str(tmp_path / "x.jsonl"))

    assert result.returncode == 2
    assert "no process matched" in result.stderr


def test_report_round_trips_a_real_run(tmp_path: pathlib.Path) -> None:
    log = tmp_path / "run.jsonl"
    treehawk(
        "run",
        "-i",
        "0.25",
        "-q",
        "-o",
        str(log),
        "--",
        sys.executable,
        WORKLOAD,
        "--seconds",
        "1",
        "--mb",
        "32",
        "--children",
        "1",
    )

    result = treehawk("report", str(log))

    assert result.returncode == 0
    assert "top by cpu time" in result.stdout
    assert "peak rss" in result.stdout


def test_csv_output_is_written_and_joinable(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "run.csv"
    treehawk(
        "run",
        "-i",
        "0.25",
        "-q",
        "--csv",
        "-o",
        str(base),
        "--",
        sys.executable,
        WORKLOAD,
        "--seconds",
        "1",
        "--mb",
        "16",
        "--children",
        "1",
    )

    import csv as csv_module

    rows = list(csv_module.DictReader(base.open()))
    procs = list(csv_module.DictReader((tmp_path / "run.procs.csv").open()))
    assert rows and procs
    assert {p["seq"] for p in procs} <= {r["seq"] for r in rows}


def test_the_workload_s_output_is_kept_beside_the_log_and_shown_in_the_dashboard(
    tmp_path: pathlib.Path,
) -> None:
    """The dashboard and the workload no longer fight over the terminal.

    Both used to write to it directly: the workload moved the cursor out from
    under the region Rich was repainting, and each wrecked the other. Now the
    workload gets a pty of its own, its last lines are drawn inside the
    dashboard, and the whole stream is kept in a file beside the log.
    """
    log = tmp_path / "run.jsonl"

    drawn = treehawk_on_a_terminal(
        "run",
        "-i",
        "0.25",
        "-o",
        str(log),
        "--",
        sys.executable,
        WORKLOAD,
        "--seconds",
        "1",
        "--mb",
        "8",
        "--parent-seconds",
        "1",
    )

    printed = log.with_suffix(".out")
    assert printed.exists(), "the workload's output was not kept"
    assert "parent pid=" in printed.read_text(), printed.read_text()
    # Drawn inside the dashboard rather than over it.
    assert "output" in drawn
    assert "parent pid=" in drawn
    # And the run itself is unaffected.
    _, samples, summary = read_log(str(log))
    assert samples
    assert summary["exit_code"] == 0


def test_no_capture_leaves_the_workload_the_terminal(tmp_path: pathlib.Path) -> None:
    """The escape hatch for a workload that wants a terminal of its own."""
    log = tmp_path / "run.jsonl"

    drawn = treehawk_on_a_terminal(
        "run",
        "-i",
        "0.25",
        "--no-capture",
        "-o",
        str(log),
        "--",
        sys.executable,
        WORKLOAD,
        "--seconds",
        "1",
        "--mb",
        "8",
        "--parent-seconds",
        "1",
    )

    assert not log.with_suffix(".out").exists()
    assert "parent pid=" in drawn  # it reached the terminal directly
