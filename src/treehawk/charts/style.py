"""Matplotlib styling, derived from the shared palette.

Everything visual is set here so the page functions contain plot calls and
nothing else. The rules encoded below are the ones that make a chart readable
rather than decorative: recessive axes, a grid behind the data, thin marks, and
no chartjunk.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from matplotlib.axes import Axes
from matplotlib.figure import Figure

if TYPE_CHECKING:
    # matplotlib 3.11 (Python 3.11+) types rc keys as a Literal; 3.10, the last
    # release for Python 3.10, types them as plain str and has no RcKeyType.
    if sys.version_info >= (3, 11):
        from matplotlib.typing import RcKeyType
    else:
        from typing import TypeAlias

        RcKeyType: TypeAlias = str

from treehawk.charts.constants import CHARTS
from treehawk.core.humanize import format_bytes
from treehawk.ui.models import Palette
from treehawk.ui.theme import PRINT


def rc_params(palette: Palette = PRINT) -> dict[RcKeyType, object]:
    """Global styling: recessive furniture, readable text, no clutter."""
    return {
        "figure.facecolor": palette.surface,
        "axes.facecolor": palette.surface,
        "savefig.facecolor": palette.surface,
        "axes.edgecolor": palette.grid,
        "axes.labelcolor": palette.text_secondary,
        "axes.titlecolor": palette.text_primary,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlepad": 10,
        "axes.labelsize": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": palette.grid,
        "grid.linewidth": 0.8,
        "xtick.color": palette.text_muted,
        "ytick.color": palette.text_muted,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "legend.labelcolor": palette.text_secondary,
        "lines.linewidth": CHARTS.line_width,
        "lines.solid_capstyle": "round",
        "text.color": palette.text_primary,
        "font.size": 9,
        "figure.dpi": 110,
    }


def draw_heading(fig: Figure, *, title: str, subtitle: str, palette: Palette = PRINT) -> None:
    """Put a consistent heading on a page."""
    fig.suptitle(title, x=0.06, y=0.965, ha="left", fontsize=16, fontweight="bold", color=palette.text_primary)
    fig.text(0.06, 0.925, subtitle, ha="left", fontsize=9.5, color=palette.text_secondary)


def draw_footer(fig: Figure, *, left: str, right: str, palette: Palette = PRINT) -> None:
    fig.text(0.06, 0.035, left, ha="left", fontsize=7.5, color=palette.text_muted)
    fig.text(0.94, 0.035, right, ha="right", fontsize=7.5, color=palette.text_muted)


def format_bytes_axis(ax: Axes) -> None:
    """Label a byte axis in KiB/MiB/GiB rather than scientific notation."""
    ax.yaxis.set_major_formatter(lambda value, _pos: format_bytes(value))


def format_time_axis(ax: Axes, *, duration: float) -> None:
    """Label the time axis in the unit the run is actually read in."""
    if duration >= 7200:
        ax.xaxis.set_major_formatter(lambda value, _pos: f"{value / 3600:.1f}h")
        ax.set_xlabel("elapsed (hours)")
    elif duration >= 180:
        ax.xaxis.set_major_formatter(lambda value, _pos: f"{value / 60:.0f}m")
        ax.set_xlabel("elapsed (minutes)")
    else:
        ax.set_xlabel("elapsed (seconds)")


def annotate_peak(
    ax: Axes,
    *,
    x: float,
    y: float,
    text: str,
    color: str,
    palette: Palette = PRINT,
) -> None:
    """A single direct label at the peak - never a number on every point."""
    ax.plot(
        [x], [y], marker="o", markersize=5, color=color, zorder=5, markeredgecolor=palette.surface, markeredgewidth=1.5
    )
    ax.annotate(
        text,
        xy=(x, y),
        xytext=(6, 6),
        textcoords="offset points",
        fontsize=8,
        color=palette.text_primary,
        fontweight="bold",
    )


def draw_placeholder(ax: Axes, *, message: str, palette: Palette = PRINT) -> None:
    """Say why a chart is blank instead of showing empty axes."""
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=10, color=palette.text_muted, transform=ax.transAxes)
    ax.set_axis_off()
