"""The command line.

This is the composition root: the only module that knows about every layer. It
reads arguments, picks implementations, and hands them to the monitor - which
sees nothing but interfaces.

It is also the only place that handles errors. Everything underneath raises a
:class:`TreehawkError` and lets it travel; :func:`_guard` turns it into a line
of text and an exit code here, once, rather than at forty catch sites.

Four commands, and the default behaviour of each is the one you want most of
the time: ``watch`` and ``run`` sample until the workload ends, ``report`` and
``pdf`` read a finished log back.
"""

from __future__ import annotations

import functools
import json
import signal
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Annotated, Final, ParamSpec

import typer
from rich.console import Console

from treehawk import __version__
from treehawk.cli.options import (
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
from treehawk.cli.wiring import build_session
from treehawk.core.config import (
    DEFAULT_EXPANSIONS,
    ExpansionName,
    Isolation,
    LogDetail,
    LogFormat,
    MemoryDetail,
    MissingWorkload,
    WatchConfig,
)
from treehawk.core.errors import ConfigError, TreehawkError
from treehawk.core.interfaces import ProcessLauncher, ProcessMatcher
from treehawk.core.matchers import build_matcher
from treehawk.core.models import LaunchedWorkload
from treehawk.core.monitor import Monitor
from treehawk.platforms.registry import Platform, get_platform
from treehawk.report import Report, build_report
from treehawk.sinks import CompositeSink, build_file_sink, build_screen_sink
from treehawk.ui.theme import PALETTE
from treehawk.ui.views import render_header_facts, render_summary

TERMINATE_GRACE: Final = 5.0
"""Seconds a workload is given to exit on SIGTERM before it is killed."""

STDOUT_PATH: Final = "-"

app = typer.Typer(
    name="treehawk",
    help="Log the CPU and RAM of a process and every process it spawns, including ones that detach.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

stderr_console = Console(stderr=True)
stdout_console = Console()

P = ParamSpec("P")


def _guard(command: Callable[P, None]) -> Callable[P, None]:
    """Turn any deliberate failure into one line of text and an exit code.

    Applied to every command, so nothing below the CLI has to know how a
    failure should be presented or what it is worth exiting with.
    """

    @functools.wraps(command)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            command(*args, **kwargs)
        except TreehawkError as exc:
            stderr_console.print(f"[bold]treehawk:[/bold] {exc}")
            raise typer.Exit(exc.exit_code) from None

    return wrapper


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    *,
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the version and exit.", is_eager=True),
    ] = False,
) -> None:
    if version:
        stdout_console.print(f"treehawk {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        stdout_console.print(ctx.get_help())
        raise typer.Exit()


@app.command()
@_guard
def watch(
    *,
    target: Annotated[
        str | None,
        typer.Argument(
            metavar="KEYWORD",
            help="Text to look for in the command line of a running process.",
        ),
    ] = None,
    pid: Annotated[int | None, typer.Option("--pid", "-p", help="Watch this process id.")] = None,
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

    [dim]treehawk watch train.py
    treehawk watch --pid 4213[/dim]
    """
    matcher = _select_matcher(target=target, pid=pid, exact=exact, regex=regex)
    fmt = LogFormat.CSV if csv else LogFormat.JSONL
    config = _build_config(
        interval=interval,
        duration=duration,
        expand=expand,
        detail=LogDetail.AGGREGATE if aggregate_only else LogDetail.PER_PROCESS,
        memory=MemoryDetail.RESIDENT if no_pss else MemoryDetail.PROPORTIONAL,
        missing_workload=MissingWorkload.WAIT if wait else MissingWorkload.FAIL,
    )
    platform = get_platform()
    path = output or _default_log_path(fmt)
    sinks = _build_sinks(
        path=path,
        fmt=fmt,
        detail=config.detail,
        screen=None if quiet else stderr_console,
    )
    session = build_session(
        platform=platform,
        config=config,
        sink=sinks,
        matcher=matcher,
        mode="watch",
        notes=tuple(platform.notes),
    )
    _install_signal_handlers(monitor=session.monitor)
    if not quiet:
        _announce_log_path(path)

    session.monitor.run()
    _report_sink_errors(sinks)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
@_guard
def run(
    ctx: typer.Context,
    *,
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
            "--no-isolate",
            rich_help_panel=ADVANCED,
            help="Do not create a cgroup for the workload; track it through /proc only.",
        ),
    ] = False,
) -> None:
    """Start a command and watch it, until it ends.

    The command goes after [bold]--[/bold], and runs inside its own cgroup, so
    every descendant is accounted for exactly - detached or not.

    [dim]treehawk run -- python train.py --epochs 10[/dim]
    """
    argv = [argument for argument in ctx.args if argument != "--"]
    if not argv:
        raise ConfigError("nothing to run; put the command after --")

    fmt = LogFormat.CSV if csv else LogFormat.JSONL
    config = _build_config(
        interval=interval,
        duration=duration,
        expand=expand,
        detail=LogDetail.AGGREGATE if aggregate_only else LogDetail.PER_PROCESS,
        memory=MemoryDetail.RESIDENT if no_pss else MemoryDetail.PROPORTIONAL,
        missing_workload=MissingWorkload.FAIL,
    )
    platform = get_platform()
    path = output or _default_log_path(fmt)
    sinks = _build_sinks(
        path=path,
        fmt=fmt,
        detail=config.detail,
        screen=None if quiet else stderr_console,
    )
    launcher = _select_launcher(platform, isolation=Isolation.NONE if no_isolate else Isolation.CGROUP)

    if not quiet:
        _announce_log_path(path)
    workload = launcher.launch(argv)
    notes = tuple(platform.notes) + tuple(getattr(launcher, "notes", ()))

    session = build_session(
        platform=platform,
        config=config,
        sink=sinks,
        matcher=build_matcher(kind="pid", value=workload.pid),
        mode="run",
        argv=argv,
        pinned_group=workload.group_path,
        notes=notes,
    )
    # The workload already exists, so seed now rather than searching for it.
    session.tracker.seed(platform.process_source.scan())
    _install_signal_handlers(monitor=session.monitor, workload=workload)

    try:
        session.monitor.run(read_exit_code=workload.poll)
    finally:
        code = _stop_workload(workload)
    _report_sink_errors(sinks)
    if code:
        raise typer.Exit(code)


@app.command()
@_guard
def report(
    path: Annotated[Path, typer.Argument(help="A log written by a previous run.")],
    *,
    as_json: Annotated[bool, typer.Option("--json", help="Print the summary as JSON.")] = False,
) -> None:
    """Summarise a finished run."""
    data: Report = build_report(str(path))
    if as_json:
        stdout_console.print_json(json.dumps(data, default=str))
        return
    header, summary = data["header"], data["summary"]
    stdout_console.print(render_header_facts(header=header, palette=PALETTE))
    stdout_console.print(render_summary(summary=summary, header=header, palette=PALETTE, title=path.name))


@app.command()
@_guard
def pdf(
    path: Annotated[Path, typer.Argument(help="A log written by a previous run.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Where to write the PDF. Default: alongside the log."),
    ] = None,
) -> None:
    """Render a finished run as a multi-page PDF report."""
    from treehawk.charts.report import write_pdf_report

    destination = output or path.with_suffix(".pdf")
    pages = write_pdf_report(log_path=str(path), destination=str(destination))
    stdout_console.print(f"wrote [bold]{destination}[/bold] ({pages} pages)")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _select_matcher(*, target: str | None, pid: int | None, exact: str | None, regex: str | None) -> ProcessMatcher:
    given = [value for value in (target, pid, exact, regex) if value is not None]
    if len(given) > 1:
        raise ConfigError("give just one of KEYWORD, --pid, --exact or --regex")
    if pid is not None:
        return build_matcher(kind="pid", value=pid)
    if exact is not None:
        return build_matcher(kind="exact", value=exact)
    if regex is not None:
        return build_matcher(kind="regex", value=regex)
    if target is not None:
        return build_matcher(kind="keyword", value=target)
    raise ConfigError("give a keyword, or one of --pid, --exact, --regex")


def _select_launcher(platform: Platform, *, isolation: Isolation) -> ProcessLauncher:
    if platform.launcher is None:
        raise ConfigError("this platform cannot start processes")
    if isolation is Isolation.CGROUP:
        return platform.launcher
    from treehawk.platforms.linux.launcher import DirectLauncher, FallbackLauncher

    return FallbackLauncher([DirectLauncher()])


def _build_config(
    *,
    interval: float,
    duration: float | None,
    expand: list[ExpansionName] | None,
    detail: LogDetail,
    memory: MemoryDetail,
    missing_workload: MissingWorkload,
) -> WatchConfig:
    config = WatchConfig(
        interval=interval,
        duration=duration,
        detail=detail,
        memory=memory,
        expand=tuple(expand) if expand else DEFAULT_EXPANSIONS,
        missing_workload=missing_workload,
    )
    config.validate()
    return config


def _default_log_path(fmt: LogFormat) -> str:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return f"treehawk-{stamp}.{fmt}"


def _build_sinks(*, path: str, fmt: LogFormat, detail: LogDetail, screen: Console | None) -> CompositeSink:
    """The log, plus the on-screen view - or no view at all when ``screen`` is
    ``None``, which is what ``--quiet`` means."""
    sinks = CompositeSink()
    sinks.add_sink(build_file_sink(fmt=fmt, path=path, detail=detail))
    if screen is not None:
        sinks.add_sink(build_screen_sink(console=screen))
    return sinks


def _announce_log_path(path: str) -> None:
    if path != STDOUT_PATH:
        stderr_console.print(f"[dim]logging to[/dim] {path}")


def _report_sink_errors(sinks: CompositeSink) -> None:
    """Say what a destination lost, once the data that survived is safe.

    A sink that fails mid-run is reported rather than raised: losing the screen
    is no reason to lose the log, and by the time we get here the log has
    already been closed out.
    """
    if not sinks.errors:
        return
    first = sinks.errors[0]
    stderr_console.print(
        f"[bold]treehawk:[/bold] {len(sinks.errors)} output error(s); first was {type(first).__name__}: {first}"
    )


def _install_signal_handlers(*, monitor: Monitor, workload: LaunchedWorkload | None = None) -> None:
    def handler(signum: int, _frame: FrameType | None) -> None:
        if workload is not None:
            workload.signal(signum)
        monitor.request_stop()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, handler)


def _stop_workload(workload: LaunchedWorkload) -> int | None:
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
