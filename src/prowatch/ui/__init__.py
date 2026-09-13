"""Presentation layer.

The only place in prowatch that knows about Rich or matplotlib. Nothing here is
imported by ``core``; the CLI composes these views onto the data that ``core``
produces, so the monitoring logic stays renderer-agnostic.
"""

from .theme import PALETTE, Palette

__all__ = ["PALETTE", "Palette"]
