"""Reading a log back."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Sequence

import pytest

from treehawk.core.errors import ReportError
from treehawk.core.interfaces import Record
from treehawk.core.values import as_mapping, as_records
from treehawk.report import build_report


def write_log(path: pathlib.Path, records: list[Record]) -> str:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return str(path)


HEADER: Record = {
    "type": "header",
    "schema": 1,
    "mode": "watch",
    "interval": 0.5,
    "matcher": {"kind": "keyword", "value": "train.py"},
    "started_at": "2026-01-01T00:00:00Z",
    "host": {"hostname": "test", "ncpu": 4, "mem_total_bytes": 8 * 1024**3},
}


def sample(seq: int, cpu: float | None, rss: int, procs: Sequence[Record] = ()) -> Record:
    return {
        "type": "sample",
        "seq": seq,
        "t": seq * 0.5,
        "n_procs": len(procs) or 1,
        "cpu_percent": cpu,
        "rss_bytes": rss,
        "pss_bytes": rss - 100,
        "cpu_seconds_used": seq * 0.5,
        "procs": list(procs),
    }


def test_report_prefers_the_recorded_summary(tmp_path: pathlib.Path) -> None:
    path = write_log(
        tmp_path / "run.jsonl",
        [
            HEADER,
            sample(0, None, 1000),
            sample(1, 50.0, 2000),
            {"type": "summary", "samples": 2, "peak_cpu_percent": 99.0, "peak_rss_bytes": 2000, "exit_code": 0},
        ],
    )

    summary = build_report(path)["summary"]

    assert summary["peak_cpu_percent"] == pytest.approx(99.0)
    assert summary["exit_code"] == 0


def test_report_recomputes_when_the_run_was_killed(tmp_path: pathlib.Path) -> None:
    """No summary record, and a half-written final line."""
    path = tmp_path / "run.jsonl"
    write_log(path, [HEADER, sample(0, None, 1000), sample(1, 50.0, 3000)])
    with path.open("a") as handle:
        handle.write('{"type": "sample", "seq": 2, "cpu_per')

    summary = build_report(str(path))["summary"]

    assert summary["samples"] == 2
    assert summary["peak_cpu_percent"] == pytest.approx(50.0)
    assert summary["peak_rss_bytes"] == 3000
    assert summary["mean_cpu_percent"] == pytest.approx(50.0)


def test_report_ranks_processes_when_the_summary_has_no_tables(tmp_path: pathlib.Path) -> None:
    procs: list[Record] = [
        {"pid": 100, "starttime": 1, "name": "a", "cmdline": "a", "via": "match", "cpu_seconds": 1.0, "rss_bytes": 500},
        {
            "pid": 200,
            "starttime": 2,
            "name": "b",
            "cmdline": "b",
            "via": "orphan",
            "cpu_seconds": 9.0,
            "rss_bytes": 100,
        },
    ]
    path = write_log(tmp_path / "run.jsonl", [HEADER, sample(0, 10.0, 600, procs)])

    summary = build_report(path)["summary"]

    assert [p["pid"] for p in as_records(summary["top_by_cpu"])] == [200, 100]
    assert [p["pid"] for p in as_records(summary["top_by_memory"])] == [100, 200]


def test_report_keeps_the_run_metadata(tmp_path: pathlib.Path) -> None:
    path = write_log(tmp_path / "run.jsonl", [HEADER, sample(0, 10.0, 1048576)])

    header = build_report(path)["header"]

    assert as_mapping(header["matcher"])["value"] == "train.py"
    assert as_mapping(header["host"])["ncpu"] == 4


def test_missing_and_empty_logs_fail_clearly(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ReportError, match="cannot read"):
        build_report(str(tmp_path / "nope.jsonl"))
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with pytest.raises(ReportError, match="no samples"):
        build_report(str(empty))


def stamped(seq: int, *, t: float, wall: float, asleep: float | None = None) -> Record:
    """A sample taken ``t`` seconds into the run, ``wall`` seconds after it began by the wall clock."""
    record = {**sample(seq, 50.0, 1000), "t": t, "ts": f"2026-01-01T00:{int(wall // 60):02d}:{wall % 60:06.3f}Z"}
    if asleep is not None:
        record["asleep_s"] = asleep
    return record


def test_the_duration_counts_the_time_the_machine_slept(tmp_path: pathlib.Path) -> None:
    path = write_log(
        tmp_path / "run.jsonl",
        [
            HEADER,
            stamped(0, t=0.0, wall=0.0, asleep=0.0),
            stamped(1, t=1.0, wall=1.0, asleep=0.0),
            stamped(2, t=602.0, wall=602.0, asleep=600.0),
            {"type": "summary", "samples": 3, "duration_s": 602.0, "asleep_s": 600.0},
        ],
    )

    summary = build_report(path)["summary"]

    assert summary["duration_s"] == pytest.approx(602.0)
    assert summary["asleep_s"] == pytest.approx(600.0)


def test_an_older_log_recovers_its_sleep_from_the_wall_clock(tmp_path: pathlib.Path) -> None:
    """Before ``asleep_s``, ``t`` stopped while the machine slept, and the summary did too."""
    path = write_log(
        tmp_path / "run.jsonl",
        [
            HEADER,
            stamped(0, t=0.0, wall=0.0),
            stamped(1, t=1.0, wall=1.2),  # a slow scan, not a sleep
            stamped(2, t=2.0, wall=602.0),
            stamped(3, t=3.0, wall=603.0),
            {"type": "summary", "samples": 4, "duration_s": 3.0},
        ],
    )

    summary = build_report(path)["summary"]

    assert summary["duration_s"] == pytest.approx(603.0)
    assert summary["asleep_s"] == pytest.approx(600.0)
