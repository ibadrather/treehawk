"""Sink contract and composition.

The contract is ``open(header)`` -> ``sample(record)``* -> ``close(summary)``.
Sinks receive plain dictionaries, so they never depend on the domain model.
"""

from __future__ import annotations

from typing import Callable, Iterable

from treehawk.core.interfaces import Record, Sink


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

    This is where an observability tool earns its keep: one failing destination
    must not cost the others their data, and it must certainly not end the run
    that is producing it. Failures are therefore recorded in :attr:`errors`
    rather than raised, and the command line reports them once the run is over.
    """

    def __init__(self, sinks: Iterable[Sink] = ()) -> None:
        self._sinks: list[Sink] = list(sinks)
        self.errors: list[BaseException] = []

    def add_sink(self, sink: Sink) -> "CompositeSink":
        self._sinks.append(sink)
        return self

    def __len__(self) -> int:
        return len(self._sinks)

    def open(self, header: Record) -> None:
        self._deliver(lambda sink: sink.open(header))

    def sample(self, record: Record) -> None:
        self._deliver(lambda sink: sink.sample(record))

    def close(self, summary: Record) -> None:
        self._deliver(lambda sink: sink.close(summary))

    def _deliver(self, send: Callable[[Sink], None]) -> None:
        for sink in self._sinks:
            try:
                send(sink)
            except BaseException as exc:  # noqa: BLE001 - recorded, not swallowed
                self.errors.append(exc)
