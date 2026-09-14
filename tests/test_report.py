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
