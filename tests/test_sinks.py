"""Output formats and the composite that fans records out to them."""

from __future__ import annotations

import csv
import json

import pytest

from prowatch.sinks import CompositeSink, CsvSink, JsonlSink, build_sink
from prowatch.sinks.base import BaseSink

HEADER = {"type": "header", "schema": 1, "interval": 1.0, "mode": "watch"}
SAMPLE = {
    "type": "sample", "seq": 0, "t": 0.0, "ts": "2026-01-01T00:00:00Z",
    "n_procs": 1, "cpu_percent": 12.5, "rss_bytes": 2048,
    "procs": [
        {"pid": 100, "ppid": 1, "name": "worker", "cmdline": "python worker.py",
         "cpu_percent": 12.5, "rss_bytes": 2048, "via": "match"}
    ],
}
SUMMARY = {"type": "summary", "samples": 1, "peak_rss_bytes": 2048}


def test_jsonl_writes_one_record_per_line(tmp_path):
    path = tmp_path / "run.jsonl"
    sink = JsonlSink(str(path))

    sink.open(HEADER)
    sink.sample(SAMPLE)
    sink.close(SUMMARY)

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["type"] for r in records] == ["header", "sample", "summary"]
    assert records[1]["procs"][0]["pid"] == 100


def test_jsonl_flushes_so_a_killed_run_still_leaves_a_readable_log(tmp_path):
    path = tmp_path / "run.jsonl"
    sink = JsonlSink(str(path))
    sink.open(HEADER)
    sink.sample(SAMPLE)

    # No close(): simulate SIGKILL. What was written must already be on disk.
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 2


def test_csv_splits_aggregate_and_per_process_rows(tmp_path):
    sink = CsvSink(str(tmp_path / "run.csv"), per_process=True)

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


def test_csv_without_per_process_writes_one_file(tmp_path):
    sink = CsvSink(str(tmp_path / "run.csv"), per_process=False)
    sink.open(HEADER)
    sink.sample(SAMPLE)
    sink.close(SUMMARY)

    assert not (tmp_path / "run.procs.csv").exists()


def test_composite_delivers_to_every_sink_even_if_one_fails():
    class Broken(BaseSink):
        def sample(self, record):
            raise OSError("disk full")

    class Good(BaseSink):
        def __init__(self):
            self.samples = []

        def sample(self, record):
            self.samples.append(record)

    good = Good()
    composite = CompositeSink([Broken(), good])

    composite.sample(SAMPLE)

    assert good.samples == [SAMPLE]
    assert isinstance(composite.errors[0], OSError)


def test_build_sink_honours_quiet_and_output(tmp_path):
    assert len(build_sink(output=str(tmp_path / "a.jsonl"), quiet=True)) == 1
    assert len(build_sink(output=str(tmp_path / "a.jsonl"), quiet=False)) == 2
    assert len(build_sink(output=None, quiet=True)) == 0


def test_build_sink_rejects_an_unknown_format(tmp_path):
    with pytest.raises(ValueError, match="unknown format"):
        build_sink(fmt="parquet", output=str(tmp_path / "a"))
