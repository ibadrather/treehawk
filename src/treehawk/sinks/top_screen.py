"""Showing ``top`` on screen while it runs.

A terminal gets a live table; anything else gets one line per spike or leak
suspect, which is what someone tailing a service's output wants to see.
"""

from __future__ import annotations

from typing import IO

from rich.console import Console
from rich.live import Live

from treehawk.core.compat import override
from treehawk.core.interfaces import Record, Sink
from treehawk.sinks.base import BaseSink
from treehawk.top.models import EventKind
from treehawk.ui.models import Palette
from treehawk.ui.theme import PALETTE
from treehawk.ui.top_views import TopBoard, describe_event


class TopLiveSink(BaseSink):
    """Draws the live ``top`` table. Survives segment rolls: it only stops on :meth:`stop`."""

    def __init__(self, *, console: Console, palette: Palette = PALETTE, refresh_per_second: float = 4.0) -> None:
        self._console = console
        self._board = TopBoard(palette=palette)
        self._refresh = refresh_per_second
        self._live: Live | None = None

    @override
    def open(self, header: Record) -> None:
        self._board.start(header)
        if self._live is None:
            self._live = Live(
                self._board.render(),
                console=self._console,
                refresh_per_second=self._refresh,
                transient=True,
            )
            self._live.start()

    @override
    def sample(self, record: Record) -> None:
        self._board.update(record)
        if self._live is not None and record.get("type") == "sample":
            self._live.update(self._board.render())

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None


class TopEventSink(BaseSink):
    """Prints spikes and leak suspects as plain lines."""

    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream

    @override
    def sample(self, record: Record) -> None:
        if record.get("type") == "event" and record.get("kind") in {str(EventKind.SPIKE), str(EventKind.CREEP)}:
            self._stream.write(f"{record.get('ts', '')} {describe_event(record)}\n")
            self._stream.flush()


def build_top_screen_sink(*, console: Console) -> Sink:
    if console.is_terminal:
        return TopLiveSink(console=console)
    return TopEventSink(console.file)
