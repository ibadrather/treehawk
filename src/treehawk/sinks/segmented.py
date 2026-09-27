"""A log that runs for the lifetime of a machine without filling its disk.

Every ``open`` starts a new file and every ``close`` finishes one, so the
monitor decides where a segment ends and this sink only handles the files:

    <directory>/<boot>/top-<started>.jsonl      the segment being written
    <directory>/<boot>/top-<started>.jsonl.gz   a closed one, compressed

Closed segments are compressed on a background thread, so a sample is never
held up behind gzip, and the oldest are deleted while the directory is over
its size budget. A segment left uncompressed by a power cut is compressed the
next time the sink starts.

Unlike :class:`~treehawk.sinks.jsonl.JsonlSink` the file stays open, flushed
after every line: reopening it twice a second, forever, would cost more than
the sampling does. A crash loses at most the line being written, and the
readers skip a truncated final line.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import shutil
import threading
from pathlib import Path
from typing import IO

from treehawk.core.compat import override
from treehawk.core.interfaces import Record
from treehawk.report import log_files
from treehawk.sinks.base import BaseSink
from treehawk.sinks.constants import SINKS


class SegmentedJsonlSink(BaseSink):
    """Writes each open/close cycle to its own file, then compresses and prunes."""

    def __init__(self, directory: str, *, keep_bytes: int | None = None) -> None:
        self._root = Path(directory)
        self._keep_bytes = keep_bytes
        self._handle: IO[str] | None = None
        self._path: Path | None = None
        self._boot_dir: Path | None = None
        """Where segments are going now; never removed, even when pruned empty."""
        self._archiver: threading.Thread | None = None
        self._recovered = False
        self.errors: list[BaseException] = []
        """What the archiver lost. Nothing here is allowed to stop the sampling."""

    @override
    def open(self, header: Record) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        if not self._recovered:
            self._recovered = True
            self._archive(list(self._root.rglob(f"{SINKS.segment_prefix}*{SINKS.segment_suffix}")))
        self._path = self._next_path(header)
        self._boot_dir = self._path.parent
        self._boot_dir.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("w", encoding="utf-8", buffering=1)
        self._write(header)

    @override
    def sample(self, record: Record) -> None:
        self._write(record)

    @override
    def close(self, summary: Record) -> None:
        self._write(summary)
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        if self._path is not None:
            self._archive([self._path])
            self._path = None

    def wait(self) -> None:
        """Block until the archiver is done. Tests, and the end of a run, want this."""
        if self._archiver is not None:
            self._archiver.join()
            self._archiver = None

    def _write(self, record: Record) -> None:
        if self._handle is None:
            return
        self._handle.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")

    def _next_path(self, header: Record) -> Path:
        boot = str(header.get("boot_id") or "").replace("-", "")[: SINKS.boot_dir_length] or SINKS.unknown_boot
        digits = "".join(character for character in str(header.get("started_at") or "") if character.isdigit())
        stamp = f"{digits[:8]}-{digits[8:14]}" if len(digits) >= 14 else "00000000-000000"
        directory = self._root / boot
        candidate = directory / f"{SINKS.segment_prefix}{stamp}{SINKS.segment_suffix}"
        counter = 1
        while candidate.exists() or candidate.with_name(candidate.name + SINKS.archive_suffix).exists():
            candidate = directory / f"{SINKS.segment_prefix}{stamp}-{counter}{SINKS.segment_suffix}"
            counter += 1
        return candidate

    # -- archiving --------------------------------------------------------

    def _archive(self, paths: list[Path]) -> None:
        """Compress ``paths`` and prune, on a thread, one archiver at a time."""
        self.wait()
        thread = threading.Thread(target=self._archive_now, args=(paths,), name="treehawk-archive")
        self._archiver = thread
        thread.start()

    def _archive_now(self, paths: list[Path]) -> None:
        try:
            for path in paths:
                self._gzip(path)
            if self._keep_bytes is not None:
                self._prune(self._keep_bytes)
        except BaseException as exc:
            self.errors.append(exc)

    @staticmethod
    def _gzip(path: Path) -> None:
        target = path.with_name(path.name + SINKS.archive_suffix)
        with path.open("rb") as source, gzip.open(target, "wb") as destination:
            shutil.copyfileobj(source, destination)
        path.unlink()

    def _prune(self, budget: int) -> None:
        """Delete the oldest closed segments until the directory fits ``budget``.

        Only compressed files count as closed: anything else may be the segment
        being written right now, on the other thread. Names start with the
        segment's start time, so they sort oldest first. The newest closed
        segment is kept whatever the budget, so a budget set too small still
        leaves the last hour to look at.
        """
        segments = [path for path in log_files(self._root) if path.name.startswith(SINKS.segment_prefix)]
        sizes = {path: path.stat().st_size for path in segments}
        total = sum(sizes.values())
        closed = [path for path in segments if path.name.endswith(SINKS.archive_suffix)]
        for path in closed[:-1]:  # the newest closed segment always stays
            if total <= budget:
                break
            with contextlib.suppress(OSError):
                path.unlink()
                total -= sizes[path]
                if path.parent != self._boot_dir and not any(path.parent.iterdir()):
                    path.parent.rmdir()
