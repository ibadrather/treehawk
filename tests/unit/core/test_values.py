"""The coercions that read records back out of parsed JSON."""

from __future__ import annotations

import pytest

from treehawk.core.values import as_mapping, as_record, as_records, is_record


@pytest.mark.parametrize("value", [None, 1, "x", [], (), [{"a": 1}]])
def test_non_dicts_are_not_records(value: object) -> None:
    assert not is_record(value)
    assert as_record(value) is None
    assert as_mapping(value) == {}


def test_a_record_is_returned_as_is_not_copied() -> None:
    record: dict[str, object] = {"a": 1}
    assert as_record(record) is record
    assert as_mapping(record) is record


def test_an_empty_record_is_still_a_record() -> None:
    record: dict[str, object] = {}
    assert as_record(record) is record


def test_as_records_keeps_only_records_in_order() -> None:
    first: dict[str, object] = {"pid": 1}
    second: dict[str, object] = {}
    records = as_records([first, 3, None, "x", second, [1]])
    assert len(records) == 2
    assert records[0] is first
    assert records[1] is second


@pytest.mark.parametrize("value", [None, {"a": 1}, "ab", 3])
def test_as_records_of_a_non_sequence_is_empty(value: object) -> None:
    assert as_records(value) == []


def test_as_records_accepts_tuples() -> None:
    record: dict[str, object] = {"a": 1}
    assert as_records((record,)) == [record]
