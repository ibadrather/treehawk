"""Sink contract and composition.

The contract is ``open(header)`` -> ``sample(record)``* -> ``close(summary)``.
Sinks receive plain dictionaries, so they never depend on the domain model.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from treehawk.core.compat import override
from treehawk.core.interfaces import Record, Sink


class BaseSink:
    """No-op base class: subclasses override only what they care about.

    Defaults are harmless rather than abstract, which keeps every subclass a
    valid stand-in for the others (Liskov) even when it ignores a record type.
    """

    def open(self, header: Record) -> None:
        """Receive the run's header record. Ignored unless overridden."""

    def sample(self, record: Record) -> None:
        """Receive one sample record. Ignored unless overridden."""

    def close(self, summary: Record) -> None:
        """Receive the run's summary record. Ignored unless overridden."""


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

    def add_sink(self, sink: Sink) -> CompositeSink:
        self._sinks.append(sink)
        return self

    def __len__(self) -> int:
        return len(self._sinks)

    @override
    def open(self, header: Record) -> None:
        self._deliver(lambda sink: sink.open(header))

    @override
    def sample(self, record: Record) -> None:
        self._deliver(lambda sink: sink.sample(record))

    @override
    def close(self, summary: Record) -> None:
        self._deliver(lambda sink: sink.close(summary))

    def _deliver(self, send: Callable[[Sink], None]) -> None:
        for sink in self._sinks:
            self._deliver_one(sink=sink, send=send)

    def _deliver_one(self, *, sink: Sink, send: Callable[[Sink], None]) -> None:
        try:
            send(sink)
        except BaseException as exc:
            self.errors.append(exc)
