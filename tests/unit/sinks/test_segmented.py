"""The lifetime log: one file per segment, compressed, pruned, readable."""

from __future__ import annotations

import gzip
import json
import os
import pathlib

from treehawk.core.interfaces import Record
from treehawk.report import log_files, peek_header, read_records
from treehawk.sinks.segmented import SegmentedJsonlSink


def header(started_at: str, *, boot_id: str | None = "abcd1234-0000") -> Record:
    return {"type": "header", "mode": "top", "started_at": started_at, "boot_id": boot_id}


def write_segment(sink: SegmentedJsonlSink, started_at: str, *, samples: int = 3, padding: int = 0) -> None:
    sink.open(header(started_at))
    for seq in range(samples):
        sink.sample({"type": "sample", "seq": seq, "pad": "x" * padding})
    sink.close({"type": "summary", "samples": samples})
    sink.wait()


def test_each_segment_is_its_own_compressed_file(tmp_path: pathlib.Path) -> None:
    sink = SegmentedJsonlSink(str(tmp_path))
    write_segment(sink, "2026-09-27T10:00:00.000+00:00")
    write_segment(sink, "2026-09-27T11:00:00.000+00:00")
    names = sorted(path.name for path in (tmp_path / "abcd1234").iterdir())
    assert names == ["top-20260927-100000.jsonl.gz", "top-20260927-110000.jsonl.gz"]
    with gzip.open(tmp_path / "abcd1234" / names[0], "rt") as handle:
        kinds = [json.loads(line)["type"] for line in handle]
    assert kinds == ["header", "sample", "sample", "sample", "summary"]


def test_the_open_segment_is_readable_while_it_is_written(tmp_path: pathlib.Path) -> None:
    sink = SegmentedJsonlSink(str(tmp_path))
    sink.open(header("2026-09-27T10:00:00Z"))
    sink.sample({"type": "sample", "seq": 0})
    assert sink.path is not None
    assert [record["type"] for record in read_records(str(sink.path))] == ["header", "sample"]
    sink.close({"type": "summary"})
    sink.wait()


def test_a_machine_without_a_boot_id_still_gets_a_directory(tmp_path: pathlib.Path) -> None:
    sink = SegmentedJsonlSink(str(tmp_path))
    sink.open(header("2026-09-27T10:00:00Z", boot_id=None))
    sink.close({"type": "summary"})
    sink.wait()
    assert (tmp_path / "boot-unknown").is_dir()


def test_two_segments_in_the_same_second_do_not_collide(tmp_path: pathlib.Path) -> None:
    sink = SegmentedJsonlSink(str(tmp_path))
    write_segment(sink, "2026-09-27T10:00:00Z")
    write_segment(sink, "2026-09-27T10:00:00Z")
    assert len(list((tmp_path / "abcd1234").iterdir())) == 2


def test_the_oldest_segments_go_when_over_budget(tmp_path: pathlib.Path) -> None:
    sink = SegmentedJsonlSink(str(tmp_path), keep_bytes=1)
    write_segment(sink, "2026-09-27T10:00:00Z", padding=5000)
    first = next((tmp_path / "abcd1234").iterdir())
    os.utime(first, (1, 1))  # unmistakably the oldest
    write_segment(sink, "2026-09-27T11:00:00Z", padding=5000)
    remaining = [path.name for path in (tmp_path / "abcd1234").iterdir()]
    assert first.name not in remaining


def test_without_a_budget_nothing_is_deleted(tmp_path: pathlib.Path) -> None:
    sink = SegmentedJsonlSink(str(tmp_path))
    for hour in range(4):
        write_segment(sink, f"2026-09-27T1{hour}:00:00Z", padding=5000)
    assert len(list((tmp_path / "abcd1234").iterdir())) == 4


def test_a_segment_left_behind_by_a_crash_is_compressed_on_the_next_start(tmp_path: pathlib.Path) -> None:
    stray = tmp_path / "old" / "top-20260101-000000.jsonl"
    stray.parent.mkdir(parents=True)
    stray.write_text('{"type":"header","mode":"top"}\n{"type":"sample","seq":0}\n{"type":"sam')
    sink = SegmentedJsonlSink(str(tmp_path))
    write_segment(sink, "2026-09-27T10:00:00Z")
    assert not stray.exists()
    recovered = stray.with_name(stray.name + ".gz")
    assert [record["type"] for record in read_records(str(recovered))] == ["header", "sample"]


def test_a_directory_is_read_oldest_segment_first(tmp_path: pathlib.Path) -> None:
    sink = SegmentedJsonlSink(str(tmp_path))
    write_segment(sink, "2026-09-27T10:00:00Z", samples=1)
    write_segment(sink, "2026-09-27T11:00:00Z", samples=2)
    files = log_files(tmp_path)
    os.utime(files[0], (100, 100))
    os.utime(files[1], (200, 200))
    records = list(read_records(str(tmp_path)))
    assert [record["type"] for record in records] == [
        "header",
        "sample",
        "summary",
        "header",
        "sample",
        "sample",
        "summary",
    ]
    assert peek_header(str(tmp_path))["started_at"] == "2026-09-27T10:00:00Z"


def test_a_truncated_compressed_segment_keeps_what_came_before(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "top-cut.jsonl.gz"
    lines = ['{"type":"header"}'] + [
        json.dumps({"type": "sample", "seq": seq, "v": seq * 7919 % 10007}) for seq in range(5000)
    ]
    whole = gzip.compress("\n".join(lines).encode())
    path.write_bytes(whole[: len(whole) // 2])
    records = list(read_records(str(path)))
    assert records[0]["type"] == "header"
    assert 100 < len(records) < 5001
