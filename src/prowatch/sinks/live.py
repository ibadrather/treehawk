"""The live dashboard, as a sink.

An adapter, nothing more: it owns the Rich ``Live`` region and forwards records
to a :class:`~prowatch.ui.dashboard.Dashboard`, which does the drawing. Keeping
the two apart means the dashboard can be rendered to a string in a test without
a terminal, and this class stays small enough to read.
"""

from __future__ import annotations

from rich.console import Console
from rich.live import Live

from ..core.interfaces import Record
from ..ui.dashboard import Dashboard
from ..ui.theme import PALETTE, Palette
from ..ui.views import render_summary
from .base import BaseSink


class LiveSink(BaseSink):
    """Draws the dashboard while the run is in progress, then the summary."""

    def __init__(
        self,
        console: Console | None = None,
        *,
        palette: Palette = PALETTE,
        max_rows: int = 12,
        refresh_per_second: float = 4.0,
    ) -> None:
        self._console = console or Console(stderr=True)
        self._palette = palette
        self._dashboard = Dashboard(palette, max_rows=max_rows)
        self._refresh = refresh_per_second
        self._live: Live | None = None
        self._header: Record = {}

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

    def sample(self, record: Record) -> None:
        self._dashboard.update(record)
        if self._live is not None:
            self._live.update(self._dashboard.render())

    def close(self, summary: Record) -> None:
        # The live region is transient, so it disappears and leaves the summary
        # as the only thing in the scrollback - which is what you want to keep.
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._console.print(render_summary(summary, self._header, self._palette))
