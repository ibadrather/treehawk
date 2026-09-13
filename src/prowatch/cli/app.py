"""The command line.

This is the composition root: the only module that knows about every layer. It
reads arguments, picks implementations, and hands them to the monitor - which
sees nothing but interfaces.

Three commands, and the default behaviour of each is the one you want most of
the time: ``watch`` and ``run`` sample until the workload ends, ``report`` and
``pdf`` read a finished log back.
"""

from __future__ import annotations

import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Annotated, Final, NoReturn

import typer
from rich.console import Console

from prowatch import __version__
from prowatch.cli.options import (
    ADVANCED,
    AggregateOnly,
    Csv,
    Duration,
    Expand,
    Interval,
    NoPss,
    Output,
    Quiet,
)
from prowatch.cli.wiring import assemble
from prowatch.core.config import DEFAULT_EXPANSIONS, ExpansionName, WatchConfig
from prowatch.core.interfaces import ProcessLauncher, ProcessMatcher, Sink
from prowatch.core.matchers import build_matcher
from prowatch.core.models import LaunchedWorkload
from prowatch.core.monitor import Monitor, WorkloadNotFound
from prowatch.platforms.registry import Platform, UnsupportedPlatform, get_platform
from prowatch.report import Report, ReportError, build_report
from prowatch.sinks import build_sink
from prowatch.ui.theme import PALETTE
from prowatch.ui.views import render_header_facts, render_summary

EXIT_ERROR: Final = 1
EXIT_NOT_FOUND: Final = 2
TERMINATE_GRACE: Final = 5.0

app = typer.Typer(
    name="prowatch",
    help="Log the CPU and RAM of a process and every process it spawns, "
         "including ones that detach.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

err = Console(stderr=True)
out = Console()


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the version and exit.", is_eager=True),
    ] = False,
) -> None:
    if version:
        out.print(f"prowatch {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        out.print(ctx.get_help())
        raise typer.Exit()


@app.command()
def watch(
    target: Annotated[
        str | None,
        typer.Argument(
            metavar="KEYWORD",
            help="Text to look for in the command line of a running process.",
        ),
    ] = None,
    pid: Annotated[
        int | None, typer.Option("--pid", "-p", help="Watch this process id.")
    ] = None,
    exact: Annotated[
        str | None,
        typer.Option("--exact", "-e", help="The full command line, matched whole."),
    ] = None,
    regex: Annotated[
        str | None,
        typer.Option("--regex", "-r", help="A regular expression over the command line."),
    ] = None,
    wait: Annotated[
        bool,
        typer.Option("--wait", help="Keep looking until the process appears."),
    ] = False,
    interval: Interval = 1.0,
    output: Output = None,
    csv: Csv = False,
    quiet: Quiet = False,
    duration: Duration = None,
    expand: Expand = None,
    no_pss: NoPss = False,
    aggregate_only: AggregateOnly = False,
) -> None:
    """Watch a process that is already running, until it ends.

    [dim]prowatch watch train.py
    prowatch watch --pid 4213[/dim]
    """
    matcher = _matcher(target=target, pid=pid, exact=exact, regex=regex)
    platform = _platform()
    config = _config(
        interval=interval, duration=duration, expand=expand, no_pss=no_pss,
        aggregate_only=aggregate_only, wait=wait,
    )
    sink, path = _sink(output=output, csv=csv, quiet=quiet, config=config)
    session = assemble(
        platform=platform, config=config, sink=sink, matcher=matcher,
        mode="watch", notes=tuple(platform.notes),
    )
    _install_signal_handlers(session.monitor)
    _announce(path, quiet=quiet)

    try:
        session.monitor.run()
    except WorkloadNotFound as exc:
        err.print(f"[bold]prowatch:[/bold] {exc}")
        raise typer.Exit(EXIT_NOT_FOUND) from None


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def run(
    ctx: typer.Context,
    interval: Interval = 1.0,
    output: Output = None,
    csv: Csv = False,
    quiet: Quiet = False,
    duration: Duration = None,
    expand: Expand = None,
    no_pss: NoPss = False,
    aggregate_only: AggregateOnly = False,
    no_isolate: Annotated[
        bool,
        typer.Option(
            "--no-isolate", rich_help_panel=ADVANCED,
            help="Do not create a cgroup for the workload; track it through "
                 "/proc only.",
        ),
    ] = False,
) -> None:
    """Start a command and watch it, until it ends.

    The command goes after [bold]--[/bold], and runs inside its own cgroup, so
    every descendant is accounted for exactly - detached or not.

    [dim]prowatch run -- python train.py --epochs 10[/dim]
    """
    argv = [arg for arg in ctx.args if arg != "--"]
    if not argv:
        err.print("[bold]prowatch:[/bold] nothing to run; put the command after --")
        raise typer.Exit(EXIT_ERROR)

    platform = _platform()
    config = _config(
        interval=interval, duration=duration, expand=expand, no_pss=no_pss,
        aggregate_only=aggregate_only, wait=False,
    )
    sink, path = _sink(output=output, csv=csv, quiet=quiet, config=config)
    launcher = _launcher(platform, isolate=not no_isolate)

    _announce(path, quiet=quiet)
    workload = launcher.launch(argv)
    notes = tuple(platform.notes) + tuple(getattr(launcher, "notes", ()))

    session = assemble(
        platform=platform, config=config, sink=sink,
        matcher=build_matcher("pid", workload.pid),
        mode="run", argv=argv, pinned_group=workload.group_path, notes=notes,
    )
    # The workload already exists, so seed now rather than searching for it.
    session.tracker.seed(platform.processes.scan())
    _install_signal_handlers(session.monitor, workload)

    try:
        session.monitor.run(exit_code=workload.poll)
    finally:
        code = _finalize(workload)
    if code:
        raise typer.Exit(code)


@app.command()
def report(
    path: Annotated[Path, typer.Argument(help="A log written by a previous run.")],
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the summary as JSON.")
    ] = False,
) -> None:
    """Summarise a finished run."""
    data = _load_report(path)
    if as_json:
        out.print_json(json.dumps(data, default=str))
        return
    header, summary = data["header"], data["summary"]
    out.print(render_header_facts(header, PALETTE))
    out.print(render_summary(summary, header, PALETTE, title=path.name))


@app.command()
def pdf(
    path: Annotated[Path, typer.Argument(help="A log written by a previous run.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Where to write the PDF. "
                                            "Default: alongside the log."),
    ] = None,
) -> None:
    """Render a finished run as a multi-page PDF report."""
    from prowatch.charts.report import write_pdf_report

    destination = output or path.with_suffix(".pdf")
    try:
        pages = write_pdf_report(str(path), str(destination))
    except ReportError as exc:
        err.print(f"[bold]prowatch:[/bold] {exc}")
        raise typer.Exit(EXIT_ERROR) from None
    out.print(f"wrote [bold]{destination}[/bold] ({pages} pages)")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _matcher(
    *, target: str | None, pid: int | None, exact: str | None, regex: str | None
) -> ProcessMatcher:
    given = [value for value in (target, pid, exact, regex) if value is not None]
    if len(given) > 1:
        _fail("give just one of KEYWORD, --pid, --exact or --regex")
    if pid is not None:
        return build_matcher("pid", pid)
    if exact is not None:
        return build_matcher("exact", exact)
    if regex is not None:
        return build_matcher("regex", regex)
    if target is not None:
        return build_matcher("keyword", target)
    _fail("give a keyword, or one of --pid, --exact, --regex")


def _platform() -> Platform:
    try:
        return get_platform()
    except UnsupportedPlatform as exc:
        _fail(str(exc))


def _launcher(platform: Platform, *, isolate: bool) -> ProcessLauncher:
    if platform.launcher is None:
        _fail("this platform cannot start processes")
    if isolate:
        return platform.launcher
    from prowatch.platforms.linux.launcher import DirectLauncher, FallbackLauncher

    return FallbackLauncher([DirectLauncher()])


def _config(
    *,
    interval: float,
    duration: float | None,
    expand: list[ExpansionName] | None,
    no_pss: bool,
    aggregate_only: bool,
    wait: bool,
) -> WatchConfig:
    config = WatchConfig(
        interval=interval,
        duration=duration,
        per_process=not aggregate_only,
        want_pss=not no_pss,
        expand=tuple(expand) if expand else DEFAULT_EXPANSIONS,
        wait=wait,
    )
    try:
        config.validate()
    except ValueError as exc:
        _fail(str(exc))
    return config


def _sink(
    *, output: str | None, csv: bool, quiet: bool, config: WatchConfig
) -> tuple[Sink, str]:
    path = output or (
        f"prowatch-{datetime.now().strftime('%Y%m%d-%H%M%S')}."
        f"{'csv' if csv else 'jsonl'}"
    )
    sink = build_sink(
        fmt="csv" if csv else "jsonl",
        output=path,
        per_process=config.per_process,
        quiet=quiet,
        console=err,
    )
    return sink, path


def _announce(path: str, *, quiet: bool) -> None:
    if not quiet and path != "-":
        err.print(f"[dim]logging to[/dim] {path}")


def _load_report(path: Path) -> Report:
    try:
        return build_report(str(path))
    except ReportError as exc:
        err.print(f"[bold]prowatch:[/bold] {exc}")
        raise typer.Exit(EXIT_ERROR) from None


def _install_signal_handlers(
    monitor: Monitor, workload: LaunchedWorkload | None = None
) -> None:
    def handler(signum: int, _frame: FrameType | None) -> None:
        if workload is not None:
            workload.signal(signum)
        monitor.request_stop()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, handler)


def _finalize(workload: LaunchedWorkload) -> int | None:
    """Stop the workload if monitoring ended first, and report its exit code."""
    code = workload.poll()
    if code is not None:
        return code
    workload.signal(signal.SIGTERM)
    deadline = time.monotonic() + TERMINATE_GRACE
    while time.monotonic() < deadline:
        code = workload.poll()
        if code is not None:
            return code
        time.sleep(0.05)
    workload.signal(signal.SIGKILL)
    return workload.poll()


def _fail(message: str) -> NoReturn:
    err.print(f"[bold]prowatch:[/bold] {message}")
    raise typer.Exit(EXIT_ERROR)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
