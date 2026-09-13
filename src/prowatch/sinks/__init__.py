"""Output destinations. Every sink honours the same three-call contract."""

from .base import BaseSink, CompositeSink
from .console import ConsoleSink
from .csv_sink import CsvSink
from .factory import build_sink
from .jsonl import JsonlSink

__all__ = [
    "BaseSink",
    "CompositeSink",
    "ConsoleSink",
    "CsvSink",
    "JsonlSink",
    "build_sink",
]
