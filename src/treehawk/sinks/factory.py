"""Builds the sinks a run writes to.

Isolated here so that neither the CLI nor the monitor needs to know which sink
classes exist; registering a new format is a one-line change.

There are two kinds of destination and they are chosen for different reasons,
so they are built by different functions: the log file is whatever the user
asked for, while the on-screen view picks itself from the terminal it has.
"""

from __future__ import annotations

from typing import Protocol

from rich.console import Console

from treehawk.core.config import LogDetail, LogFormat
from treehawk.core.errors import ConfigError
from treehawk.core.interfaces import Sink
from treehawk.sinks.console import ConsoleSink
from treehawk.sinks.csv_sink import CsvSink
from treehawk.sinks.jsonl import JsonlSink
from treehawk.sinks.live import LiveSink


class FileSinkFactory(Protocol):
    """Builds one file sink. Declared as a protocol so the registry is typed."""

    def __call__(self, *, path: str, detail: LogDetail) -> Sink: ...


def _build_jsonl(*, path: str, detail: LogDetail) -> Sink:
    del detail  # a JSON line holds whatever the sample record carries
    return JsonlSink(path)


def _build_csv(*, path: str, detail: LogDetail) -> Sink:
    return CsvSink(path, detail=detail)


FILE_FORMATS: dict[LogFormat, FileSinkFactory] = {
    LogFormat.JSONL: _build_jsonl,
    LogFormat.CSV: _build_csv,
}


def build_file_sink(*, fmt: LogFormat, path: str, detail: LogDetail) -> Sink:
    """The sink that writes the log. Unknown formats raise :class:`ConfigError`."""
    try:
        factory = FILE_FORMATS[fmt]
    except KeyError:
        known = ", ".join(sorted(str(name) for name in FILE_FORMATS))
        raise ConfigError(f"unknown format {fmt!r}; known: {known}") from None
    return factory(path=path, detail=detail)


def build_screen_sink(*, console: Console) -> Sink:
    """The on-screen view, which picks itself.

    A terminal gets the live dashboard; anything else - a pipe, a log file, CI -
    gets plain lines, because cursor control in a captured stream is noise.
    """
    if console.is_terminal:
        return LiveSink(console=console)
    return ConsoleSink(console.file)
