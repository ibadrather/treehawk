"""Output destinations. Every sink honours the same three-call contract."""

from prowatch.sinks.base import BaseSink, CompositeSink
from prowatch.sinks.console import ConsoleSink
from prowatch.sinks.csv_sink import CsvSink
from prowatch.sinks.factory import build_file_sink, build_screen_sink
from prowatch.sinks.jsonl import JsonlSink
from prowatch.sinks.live import LiveSink

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
