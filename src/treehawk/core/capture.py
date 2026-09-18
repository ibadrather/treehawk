"""Reading a launched workload's own output.

``run`` gives the workload a pty rather than treehawk's terminal, and this is
what sits on the other end of it. Two destinations, for two different reasons:
every line goes to a file beside the log, because that is the only copy that
survives the run, and every line is also handed to the sinks, because the
dashboard shows the last few of them.

The reading happens on a thread of its own, and that thread must not be allowed
to die. A pty has a finite buffer: a reader that stops reading is a workload
that blocks on its next ``print``, which would make treehawk the reason the
thing it is measuring ran slowly. Every failure below is therefore recorded and
stepped over rather than raised.
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import IO

from treehawk.core.constants import CORE
from treehawk.core.interfaces import Sink

_BREAK = re.compile(rb"\r\n|\r|\n")
"""What ends a line. ``\\r`` counts: a progress bar that only ever writes
carriage returns must not sit in the buffer until the run is over."""


def split_lines(buffer: bytes, *, final: bool = False) -> tuple[list[bytes], bytes]:
    """Split ``buffer`` into complete lines and the incomplete remainder.

    A trailing ``\\r`` is held back unless ``final``, because the ``\\n`` that
    would pair with it may be in the next read - splitting eagerly there turns
    one ``\\r\\n`` into a line break and a spurious empty line.
    """
    if final:
        lines = _BREAK.split(buffer)
        if lines and not lines[-1]:
            lines.pop()  # the buffer ended on a line break, not a bare line
        return lines, b""
    held = b""
    if buffer.endswith(b"\r"):
        buffer, held = buffer[:-1], b"\r"
    lines = _BREAK.split(buffer)
    return lines, lines.pop() + held


class OutputReader:
    """Drains a workload's output on a background thread.

    Owns the stream it was given and closes it when the workload's last writer
    lets go, which is the event that ends the thread.
    """

    def __init__(self, *, stream: IO[bytes], sink: Sink, mirror: str | None = None) -> None:
        self._stream = stream
        self._sink = sink
        self._mirror_path = mirror
        self._mirror: IO[str] | None = None
        self._thread = threading.Thread(target=self._drain, name="treehawk-output", daemon=True)
        self.errors: list[BaseException] = []
        """What a destination lost. Reported by the command line once the run is
        over, the same way :class:`~treehawk.sinks.base.CompositeSink` does."""

    def start(self) -> None:
        self._mirror = self._open_mirror()
        self._thread.start()

    def stop(self, *, timeout: float = CORE.output_drain_grace) -> None:
        """Wait for the remaining output to be drained.

        A workload that left a descendant holding the other end of the pty can
        keep the thread alive; it is a daemon, so waiting is bounded and giving
        up on it costs nothing but the last few lines.
        """
        self._thread.join(timeout)

    # -- the thread -------------------------------------------------------

    def _drain(self) -> None:
        buffer = b""
        try:
            with contextlib.suppress(OSError):
                # A pty master reports the last writer closing the slave as
                # EIO rather than as end of file, so both mean "finished".
                while True:
                    chunk = self._stream.read(CORE.output_read_size)
                    if not chunk:
                        break
                    lines, buffer = split_lines(buffer + chunk)
                    if len(buffer) >= CORE.output_line_limit:
                        lines.append(buffer)  # no newline in sight; do not grow further
                        buffer = b""
                    for line in lines:
                        self._emit(line)
            remainder, _ = split_lines(buffer, final=True)
            for line in remainder:
                self._emit(line)
        finally:
            self._close()

    def _emit(self, line: bytes) -> None:
        text = line.decode(CORE.output_encoding, "replace")
        if self._mirror is not None:
            self._guard(lambda: self._write_mirror(text))
        self._guard(lambda: self._sink.output(text))

    def _write_mirror(self, text: str) -> None:
        if self._mirror is not None:
            self._mirror.write(text + "\n")

    def _open_mirror(self) -> IO[str] | None:
        if self._mirror_path is None:
            return None
        try:
            return Path(self._mirror_path).open("w", encoding=CORE.output_encoding, newline="\n")
        except OSError as exc:
            self.errors.append(exc)
            return None

    def _close(self) -> None:
        for stream in (self._mirror, self._stream):
            if stream is not None:
                self._guard(stream.close)
        self._mirror = None

    def _guard(self, action: Callable[[], None]) -> None:
        """Run ``action``, keeping any failure rather than losing the thread."""
        try:
            action()
        except BaseException as exc:
            self.errors.append(exc)
