"""JSON Lines output: one record per line.

The default format because it is append-only (a killed run still leaves a valid
file up to the last line) and self-describing, so later additions such as GPU
fields do not break existing readers.

Each record is appended and the file closed again straight away: the run this
is recording may be killed at any moment, and a record still sitting in an open
buffer is data that was never collected.
"""

from __future__ import annotations

import json
import pathlib
import sys

from treehawk.core.compat import override
from treehawk.core.interfaces import Record
from treehawk.sinks.base import BaseSink
from treehawk.sinks.constants import SINKS


class JsonlSink(BaseSink):
    """Writes records as JSON Lines to a file, or to stdout when path is ``-``."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._writing = False

    @property
    def path(self) -> str:
        return self._path

    @override
    def open(self, header: Record) -> None:
        if self._path != SINKS.stdout_path:
            pathlib.Path(self._path).write_text("", encoding="utf-8")  # a fresh log, not an old one extended
        self._writing = True
        self._write(header)

    @override
    def sample(self, record: Record) -> None:
        self._write(record)

    @override
    def close(self, summary: Record) -> None:
        self._write(summary)
        self._writing = False

    def _write(self, record: Record) -> None:
        if not self._writing:
            return
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        if self._path == SINKS.stdout_path:
            sys.stdout.write(line)
            sys.stdout.flush()
            return
        with pathlib.Path(self._path).open("a", encoding="utf-8") as handle:
            handle.write(line)
