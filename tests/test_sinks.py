"""Output formats and the composite that fans records out to them."""

from __future__ import annotations

import csv
import json
import pathlib

import pytest

from treehawk.core.compat import override
from treehawk.core.config import LogDetail, LogFormat
from treehawk.core.errors import ConfigError
from treehawk.core.interfaces import Record
from treehawk.sinks import (
    CompositeSink,
    CsvSink,
    JsonlSink,
    build_file_sink,
    build_screen_sink,
)
from treehawk.sinks.base import BaseSink
from treehawk.sinks.console import ConsoleSink
from treehawk.sinks.factory import FILE_FORMATS

HEADER: Record = {"type": "header", "schema": 1, "interval": 1.0, "mode": "watch"}
SAMPLE: Record = {
    "type": "sample",
    "seq": 0,
    "t": 0.0,
    "ts": "2026-01-01T00:00:00Z",
    "n_procs": 1,
    "cpu_percent": 12.5,
    "rss_bytes": 2048,
    "procs": [
        {
            "pid": 100,
            "ppid": 1,
            "name": "worker",
            "cmdline": "python worker.py",
            "cpu_percent": 12.5,
            "rss_bytes": 2048,
            "via": "match",
        }
    ],
}
SUMMARY: Record = {"type": "summary", "samples": 1, "peak_rss_bytes": 2048}


def test_jsonl_writes_one_record_per_line(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "run.jsonl"
    sink = JsonlSink(str(path))

    sink.open(HEADER)
    sink.sample(SAMPLE)
    sink.close(SUMMARY)

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["type"] for r in records] == ["header", "sample", "summary"]
    assert records[1]["procs"][0]["pid"] == 100


def test_jsonl_flushes_so_a_killed_run_still_leaves_a_readable_log(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "run.jsonl"
    sink = JsonlSink(str(path))
    sink.open(HEADER)
    sink.sample(SAMPLE)

    # No close(): simulate SIGKILL. What was written must already be on disk.
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 2


def test_csv_splits_aggregate_and_per_process_rows(tmp_path: pathlib.Path) -> None:
    sink = CsvSink(str(tmp_path / "run.csv"), detail=LogDetail.PER_PROCESS)

    sink.open(HEADER)
    sink.sample(SAMPLE)
    sink.close(SUMMARY)

    rows = list(csv.DictReader((tmp_path / "run.csv").open()))
    assert rows[0]["cpu_percent"] == "12.5"
    procs = list(csv.DictReader((tmp_path / "run.procs.csv").open()))
    assert procs[0]["pid"] == "100"
    assert procs[0]["seq"] == "0"  # joinable back to the aggregate row
    # CSV has no place for run metadata, so it goes beside the data.
    assert json.loads((tmp_path / "run.header.json").read_text())["schema"] == 1
    assert json.loads((tmp_path / "run.summary.json").read_text())["samples"] == 1


def test_csv_without_per_process_writes_one_file(tmp_path: pathlib.Path) -> None:
    sink = CsvSink(str(tmp_path / "run.csv"), detail=LogDetail.AGGREGATE)
    sink.open(HEADER)
    sink.sample(SAMPLE)
    sink.close(SUMMARY)

    assert not (tmp_path / "run.procs.csv").exists()


def test_composite_delivers_to_every_sink_even_if_one_fails() -> None:
    class Broken(BaseSink):
        @override
        def sample(self, record: Record) -> None:
            raise OSError(f"disk full at sample {record.get('seq')}")

    class Good(BaseSink):
        def __init__(self) -> None:
            self.samples: list[Record] = []

        @override
        def sample(self, record: Record) -> None:
            self.samples.append(record)

    good = Good()
    composite = CompositeSink([Broken(), good])

    composite.sample(SAMPLE)

    assert good.samples == [SAMPLE]
    assert isinstance(composite.errors[0], OSError)


def test_the_file_sink_matches_the_requested_format(tmp_path: pathlib.Path) -> None:
    jsonl = build_file_sink(
        fmt=LogFormat.JSONL,
        path=str(tmp_path / "a.jsonl"),
        detail=LogDetail.PER_PROCESS,
    )
    csv_sink = build_file_sink(
        fmt=LogFormat.CSV,
        path=str(tmp_path / "a.csv"),
        detail=LogDetail.PER_PROCESS,
    )

    assert isinstance(jsonl, JsonlSink)
    assert isinstance(csv_sink, CsvSink)


def test_build_file_sink_rejects_an_unknown_format(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(FILE_FORMATS, LogFormat.CSV)
    with pytest.raises(ConfigError, match="unknown format"):
        build_file_sink(fmt=LogFormat.CSV, path=str(tmp_path / "a"), detail=LogDetail.PER_PROCESS)


def test_the_screen_sink_picks_itself_from_the_console(tmp_path: pathlib.Path) -> None:
    """A pipe gets plain lines; only a real terminal gets cursor control."""
    from rich.console import Console

    piped = build_screen_sink(console=Console(file=(tmp_path / "out").open("w")))
    assert isinstance(piped, ConsoleSink)
