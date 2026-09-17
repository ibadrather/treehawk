"""Reading a workload's own output: where the lines are cut, and where they go.

The reader runs on a thread, so every test here drives it through a real pipe
and waits for it to finish rather than reaching inside it.
"""

from __future__ import annotations

import io
import os
import pathlib

from treehawk.core.capture import OutputReader, split_lines
from treehawk.core.compat import override
from treehawk.core.constants import CORE
from treehawk.sinks.base import BaseSink


class RecordingOutput(BaseSink):
    """Keeps every line the workload printed, in order."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    @override
    def output(self, line: str) -> None:
        self.lines.append(line)


class BrokenOutput(BaseSink):
    """A sink that fails on every line, the way a closed terminal would."""

    @override
    def output(self, line: str) -> None:
        message = f"cannot show {line!r}"
        raise RuntimeError(message)


def drain(payload: bytes, *, sink: BaseSink | None = None, mirror: str | None = None) -> RecordingOutput:
    """Push ``payload`` through a real pipe and wait for the reader to finish."""
    recorder = RecordingOutput()
    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, "wb", 0) as writer:
        reader = OutputReader(stream=os.fdopen(read_fd, "rb", 0), sink=sink or recorder, mirror=mirror)
        reader.start()
        writer.write(payload)
    reader.stop()
    return recorder


# -- where a line ends ------------------------------------------------------


def test_newlines_end_lines_and_a_partial_line_is_held_back() -> None:
    lines, rest = split_lines(b"first\nsecond\nthi")

    assert lines == [b"first", b"second"]
    assert rest == b"thi"


def test_a_carriage_return_ends_a_line_too() -> None:
    """A progress bar that only ever writes ``\\r`` must still be shown.

    The last one is held back rather than emitted: until the next read arrives
    it cannot be known whether it is a line of its own or half of a ``\\r\\n``.
    """
    lines, rest = split_lines(b"33%\r66%\r")

    assert lines == [b"33%"]
    assert rest == b"66%\r"


def test_a_crlf_split_across_two_reads_stays_one_line_break() -> None:
    """The ``\\r`` is held until its ``\\n`` can be seen, so no empty line appears."""
    first, rest = split_lines(b"done\r")
    second, remainder = split_lines(rest + b"\nnext\n")

    assert first == []
    assert second == [b"done", b"next"]
    assert remainder == b""


def test_the_last_line_is_emitted_even_without_a_trailing_newline() -> None:
    lines, rest = split_lines(b"no newline here", final=True)

    assert lines == [b"no newline here"]
    assert rest == b""


def test_a_trailing_break_does_not_produce_an_empty_final_line() -> None:
    lines, _ = split_lines(b"one\ntwo\n", final=True)

    assert lines == [b"one", b"two"]


# -- the reader -------------------------------------------------------------


def test_every_line_reaches_the_sink() -> None:
    recorder = drain(b"loading\nepoch 0\nepoch 1\n")

    assert recorder.lines == ["loading", "epoch 0", "epoch 1"]


def test_the_whole_stream_is_kept_in_the_mirror_file(tmp_path: pathlib.Path) -> None:
    mirror = tmp_path / "run.out"

    drain(b"one\ntwo\n", mirror=str(mirror))

    assert mirror.read_text() == "one\ntwo\n"


def test_a_workload_that_never_prints_a_newline_does_not_grow_the_buffer() -> None:
    """Output past the limit is emitted in pieces rather than buffered on.

    What matters is that the buffer stays bounded no matter how long the
    workload goes without a newline, and that nothing is dropped to achieve it.
    """
    payload = b"x" * (CORE.output_read_size * 2 + 10)

    recorder = drain(payload)

    assert len(recorder.lines) > 1, "the whole payload was held in the buffer"
    assert all(len(line) <= CORE.output_read_size + CORE.output_line_limit for line in recorder.lines)
    assert "".join(recorder.lines) == payload.decode()


def test_a_line_left_unterminated_at_the_end_is_still_emitted() -> None:
    recorder = drain(b"finished\r")

    assert recorder.lines == ["finished"]


def test_undecodable_bytes_are_replaced_rather_than_losing_the_line() -> None:
    recorder = drain(b"caf\xff\n")

    assert len(recorder.lines) == 1
    assert recorder.lines[0].startswith("caf")


def test_a_failing_sink_is_recorded_and_the_rest_is_still_drained(tmp_path: pathlib.Path) -> None:
    """The thread must survive: a reader that stops reading blocks the workload."""
    mirror = tmp_path / "run.out"
    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, "wb", 0) as writer:
        reader = OutputReader(stream=os.fdopen(read_fd, "rb", 0), sink=BrokenOutput(), mirror=str(mirror))
        reader.start()
        writer.write(b"one\ntwo\n")
    reader.stop()

    assert len(reader.errors) == 2
    assert mirror.read_text() == "one\ntwo\n"  # the durable copy was unaffected


def test_a_mirror_that_cannot_be_opened_is_reported_but_costs_no_output(tmp_path: pathlib.Path) -> None:
    recorder = RecordingOutput()
    unwritable = tmp_path / "missing" / "run.out"
    reader = OutputReader(stream=io.BytesIO(b"still here\n"), sink=recorder, mirror=str(unwritable))

    reader.start()
    reader.stop()

    assert [type(error) for error in reader.errors] == [FileNotFoundError]
    assert recorder.lines == ["still here"]


def test_the_stream_is_closed_once_it_has_been_drained() -> None:
    stream = io.BytesIO(b"done\n")
    reader = OutputReader(stream=stream, sink=RecordingOutput())

    reader.start()
    reader.stop()

    assert stream.closed
