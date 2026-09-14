"""Presentation layer.

The only place in treehawk that knows about Rich or matplotlib. Nothing here is
imported by ``core``; the CLI composes these views onto the data that ``core``
produces, so the monitoring logic stays renderer-agnostic.
"""

from treehawk.ui.models import Palette
from treehawk.ui.theme import PALETTE

__all__ = ["PALETTE", "Palette"]
