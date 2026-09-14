"""Option definitions shared by ``watch`` and ``run``.

Declared once as ``Annotated`` aliases so the two commands cannot drift apart,
and so the help text lives next to the type rather than inside a command body.

The split into two panels is deliberate. The everyday surface is four options -
how often, where to write, which format, how loud - and everything that exists
only for an awkward situation is grouped under "Advanced" so it does not compete
for attention.
"""

from __future__ import annotations

from typing import Annotated, Final

import typer

from treehawk.core.config import ExpansionName

ADVANCED: Final = "Advanced"

Interval = Annotated[
    float,
    typer.Option(
        "--interval",
        "-i",
        min=0.01,
        help="Seconds between samples.",
        show_default=True,
    ),
]

Output = Annotated[
    str | None,
    typer.Option(
        "--output",
        "-o",
        metavar="PATH",
        help="Log file. '-' writes to stdout. Default: treehawk-<timestamp>.jsonl in the current directory.",
    ),
]

Csv = Annotated[
    bool,
    typer.Option("--csv", help="Write CSV instead of JSON Lines."),
]

Quiet = Annotated[
    bool,
    typer.Option("--quiet", "-q", help="No output on screen; just write the log."),
]

Duration = Annotated[
    float | None,
    typer.Option(
        "--duration",
        "-d",
        metavar="SECONDS",
        rich_help_panel=ADVANCED,
        help="Stop after this long. By default treehawk runs until the process ends.",
    ),
]

Expand = Annotated[
    list[ExpansionName] | None,
    typer.Option(
        "--expand",
        rich_help_panel=ADVANCED,
        help="Which rules may adopt processes into the workload. Repeatable; all four are used by default.",
    ),
]

NoPss = Annotated[
    bool,
    typer.Option(
        "--no-pss",
        rich_help_panel=ADVANCED,
        help="Skip the shared-memory correction. Cheaper per sample, but "
        "summed RSS over-counts pages shared between children.",
    ),
]

AggregateOnly = Annotated[
    bool,
    typer.Option(
        "--aggregate-only",
        rich_help_panel=ADVANCED,
        help="Log only the workload total, not a row per process. Much smaller logs for a run that lasts days.",
    ),
]
