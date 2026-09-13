"""Sink contract and composition.

The contract is ``open(header)`` -> ``sample(record)``* -> ``close(summary)``.
Sinks receive plain dictionaries, so they never depend on the domain model.
"""

from __future__ import annotations

from typing import Iterable

from ..core.interfaces import Record, Sink


class BaseSink:
    """No-op base class: subclasses override only what they care about.

    Defaults are harmless rather than abstract, which keeps every subclass a
    valid stand-in for the others (Liskov) even when it ignores a record type.
    """

    def open(self, header: Record) -> None:
        return None

    def sample(self, record: Record) -> None:
        return None

    def close(self, summary: Record) -> None:
        return None


class CompositeSink(BaseSink):
    """Fans every record out to several sinks; itself a sink.

    One failing destination must not cost the others their data, so failures
    are collected and re-raised only after everyone has been given the record.
    """

    def __init__(self, sinks: Iterable[Sink] = ()) -> None:
        self._sinks: list[Sink] = list(sinks)
        self.errors: list[BaseException] = []

    def add(self, sink: Sink) -> "CompositeSink":
        self._sinks.append(sink)
        return self

    def __len__(self) -> int:
        return len(self._sinks)

    def open(self, header: Record) -> None:
        self._each("open", header)

    def sample(self, record: Record) -> None:
        self._each("sample", record)

    def close(self, summary: Record) -> None:
        self._each("close", summary)

    def _each(self, method: str, payload: Record) -> None:
        for sink in self._sinks:
            try:
                getattr(sink, method)(payload)
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                self.errors.append(exc)
