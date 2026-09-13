"""Builds the sink graph from CLI options.

Isolated here so that neither the CLI nor the monitor needs to know which sink
classes exist; registering a new format is a one-line change.
"""

from __future__ import annotations

from .base import CompositeSink
from .console import ConsoleSink
from .csv_sink import CsvSink
from .jsonl import JsonlSink

FORMATS = {
    "jsonl": lambda path, per_process: JsonlSink(path),
    "csv": lambda path, per_process: CsvSink(path, per_process=per_process),
}


def build_sink(
    *,
    fmt: str = "jsonl",
    output: str | None = None,
    per_process: bool = True,
    quiet: bool = False,
    show_procs: int = 0,
    stream=None,
) -> CompositeSink:
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
        sink.add(ConsoleSink(stream, show_procs=show_procs))
    return sink
