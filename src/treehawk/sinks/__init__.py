"""Output destinations. Every sink honours the same three-call contract."""

from treehawk.sinks.base import BaseSink, CompositeSink
from treehawk.sinks.console import ConsoleSink
from treehawk.sinks.csv_sink import CsvSink
from treehawk.sinks.factory import build_file_sink, build_screen_sink
from treehawk.sinks.jsonl import JsonlSink
from treehawk.sinks.live import LiveSink

__all__ = [
    "BaseSink",
    "CompositeSink",
    "ConsoleSink",
    "CsvSink",
    "JsonlSink",
    "LiveSink",
    "build_file_sink",
    "build_screen_sink",
]
