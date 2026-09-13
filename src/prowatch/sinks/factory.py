"""Builds the sink graph from CLI options.

Isolated here so that neither the CLI nor the monitor needs to know which sink
classes exist; registering a new format is a one-line change.
"""

from __future__ import annotations

from typing import Callable

from rich.console import Console

from ..core.interfaces import Sink
from .base import CompositeSink
from .console import ConsoleSink
from .csv_sink import CsvSink
from .jsonl import JsonlSink
from .live import LiveSink

FileSinkFactory = Callable[[str, bool], Sink]

FORMATS: dict[str, FileSinkFactory] = {
    "jsonl": lambda path, per_process: JsonlSink(path),
    "csv": lambda path, per_process: CsvSink(path, per_process=per_process),
}


def build_sink(
    *,
    fmt: str = "jsonl",
    output: str | None = None,
    per_process: bool = True,
    quiet: bool = False,
    console: Console | None = None,
) -> CompositeSink:
    """Compose the file sink and the on-screen view for one run.

    The on-screen half picks itself: a terminal gets the live dashboard, and
    anything else - a pipe, a log file, CI - gets plain lines, because cursor
    control in a captured stream is noise.
    """
    sink = CompositeSink()
    if output:
        try:
            factory = FORMATS[fmt]
        except KeyError:
            raise ValueError(
                f"unknown format {fmt!r}; known: {', '.join(sorted(FORMATS))}"
            ) from None
        sink.add(factory(output, per_process))
    if not quiet:
        console = console or Console(stderr=True)
        sink.add(LiveSink(console) if console.is_terminal else ConsoleSink(console.file))
    return sink
