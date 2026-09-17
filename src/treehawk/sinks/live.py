"""The live dashboard, as a sink.

An adapter, nothing more: it owns the Rich ``Live`` region and forwards records
to a :class:`~treehawk.ui.dashboard.Dashboard`, which does the drawing. Keeping
the two apart means the dashboard can be rendered to a string in a test without
a terminal, and this class stays small enough to read.
"""

from __future__ import annotations

from rich.console import Console
from rich.live import Live

from treehawk.core.compat import override
from treehawk.core.interfaces import Record
from treehawk.sinks.base import BaseSink
from treehawk.ui.dashboard import Dashboard
from treehawk.ui.models import Palette
from treehawk.ui.theme import PALETTE
from treehawk.ui.views import render_summary


class LiveSink(BaseSink):
    """Draws the dashboard while the run is in progress, then the summary."""

    def __init__(
        self,
        *,
        console: Console,
        palette: Palette = PALETTE,
        max_rows: int = 12,
        refresh_per_second: float = 4.0,
    ) -> None:
        self._console = console
        self._palette = palette
        self._dashboard = Dashboard(palette=palette, max_rows=max_rows)
        self._refresh = refresh_per_second
        self._live: Live | None = None
        self._header: Record = {}

    @override
    def open(self, header: Record) -> None:
        self._header = header
        self._dashboard.start(header)
        self._live = Live(
            self._dashboard.render(),
            console=self._console,
            refresh_per_second=self._refresh,
            transient=True,
        )
        self._live.start()

    @override
    def sample(self, record: Record) -> None:
        self._dashboard.update(record)
        self._redraw()

    @override
    def output(self, line: str) -> None:
        """Show a line the workload printed, inside the dashboard.

        Called from the reader thread rather than the sampling loop, which is
        the whole point of routing it here: the workload's output and the live
        region reach the terminal through one Rich console, so they take turns
        instead of overwriting each other.
        """
        self._dashboard.append_output(line)
        self._redraw()

    def _redraw(self) -> None:
        if self._live is not None:
            self._live.update(self._dashboard.render())

    @override
    def close(self, summary: Record) -> None:
        # The live region is transient, so it disappears and leaves the summary
        # as the only thing in the scrollback - which is what you want to keep.
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._console.print(render_summary(summary=summary, header=self._header, palette=self._palette))
