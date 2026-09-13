"""JSON Lines output: one record per line.

The default format because it is append-only (a killed run still leaves a valid
file up to the last line) and self-describing, so later additions such as GPU
fields do not break existing readers.
"""

from __future__ import annotations

import json
import sys
from typing import TextIO
from ..core.interfaces import Record
from .base import BaseSink


class JsonlSink(BaseSink):
    """Writes records as JSON Lines to a file, or to stdout when path is ``-``."""

    def __init__(self, path: str, *, flush: bool = True) -> None:
        self._path = path
        self._flush = flush
        self._handle: TextIO | None = None

    @property
    def path(self) -> str:
        return self._path

    def open(self, header: Record) -> None:
        if self._path == "-":
            self._handle = sys.stdout
        else:
            self._handle = open(self._path, "w", encoding="utf-8")
        self._write(header)

    def sample(self, record: Record) -> None:
        self._write(record)

    def close(self, summary: Record) -> None:
        self._write(summary)
        if self._handle is not None and self._path != "-":
            self._handle.close()
        self._handle = None

    def _write(self, record: Record) -> None:
        if self._handle is None:
            return
        json.dump(record, self._handle, separators=(",", ":"), default=str)
        self._handle.write("\n")
        if self._flush:
            self._handle.flush()
