"""The command line.

This is the composition root: the only module that knows about every layer. It
reads arguments, picks implementations, and hands them to the monitor - which
sees nothing but interfaces. What it needs from the machine itself - the
platform, the clock, its own PID - it takes from a :class:`Runtime`, so a test
can run every command against a fake one.

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
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Annotated, ParamSpec

import typer
from rich.console import Console

from treehawk import __version__
from treehawk.cli.constants import CLI
from treehawk.cli.models import Runtime
from treehawk.cli.options import (
    AggregateOnly,
    Csv,
    Duration,
    Expand,
    Interval,
    NoCapture,
    NoPss,
    Output,
    Quiet,
)
from treehawk.cli.parsing import parse_interval, parse_moment, parse_size, parse_span
from treehawk.cli.service import install as install_service
from treehawk.cli.service import status as service_status
from treehawk.cli.service import uninstall as uninstall_service
from treehawk.cli.service import unit_text
from treehawk.cli.wiring import build_session, build_top_monitor
from treehawk.core.capture import OutputReader
from treehawk.core.config import (
    DEFAULT_EXPANSIONS,
    ExpansionName,
    Isolation,
    LogDetail,
    LogFormat,
    MemoryDetail,
    MissingWorkload,
    TopConfig,
    WatchConfig,
    WorkloadOutput,
)
from treehawk.core.errors import ConfigError, TreehawkError
from treehawk.core.interfaces import Clock, ProcessLauncher, ProcessMatcher, Sink
from treehawk.core.matchers import build_matcher
from treehawk.core.models import LaunchedWorkload
from treehawk.core.monitor import Monitor
from treehawk.platforms.models import Platform
from treehawk.report import Report, build_report, peek_header
from treehawk.sinks import CompositeSink, build_file_sink, build_screen_sink
from treehawk.sinks.constants import SINKS
from treehawk.sinks.segmented import SegmentedJsonlSink
from treehawk.sinks.top_screen import TopLiveSink, build_top_screen_sink
from treehawk.top.constants import TOP
from treehawk.top.history import history_record, load_history
from treehawk.top.monitor import TopMonitor
from treehawk.top.records import MODE as TOP_MODE
from treehawk.ui.theme import PALETTE
from treehawk.ui.top_views import render_top_report
from treehawk.ui.views import render_header_facts, render_summary

app = typer.Typer(
    name="treehawk",
    help="Log the CPU and RAM of a process and every process it spawns, including ones that detach.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

service_app = typer.Typer(
    help="Run [bold]treehawk top[/bold] as a systemd service, from boot to shutdown (Linux, as root).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(service_app, name="service")

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
    ctx: typer.Context,
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
    runtime = _runtime(ctx)
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
    platform = runtime.platform()
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
        clock=runtime.clock,
        self_pid=runtime.self_pid(),
        notes=tuple(platform.notes),
    )
    _install_signal_handlers(monitor=session.monitor)
    if not quiet:
        _announce_log_path(path)

    session.monitor.run()
    _report_errors(sinks.errors)


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
    no_capture: NoCapture = False,
    no_isolate: Annotated[
        bool,
        typer.Option(
            "--no-isolate",
            rich_help_panel=CLI.advanced_panel,
            help="Do not ask for an accounting boundary; track the workload through the process table only.",
        ),
    ] = False,
) -> None:
    """Start a command and watch it, until it ends.

    The command goes after [bold]--[/bold]. On Linux it runs inside its own
    cgroup, so every descendant is accounted for exactly - detached or not;
    elsewhere it gets its own session and membership is inferred.

    Whatever the workload prints is read over a pty of its own: the last lines
    appear in the dashboard, and the whole stream is kept beside the log as
    [bold].out[/bold]. Use [bold]--no-capture[/bold] to hand it this terminal
    instead.

    [dim]treehawk run -- python train.py --epochs 10[/dim]
    """
    runtime = _runtime(ctx)
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
    platform = runtime.platform()
    path = output or _default_log_path(fmt)
    screen = None if quiet else stderr_console
    sinks = _build_sinks(path=path, fmt=fmt, detail=config.detail, screen=screen)
    launcher = _select_launcher(platform, isolation=Isolation.NONE if no_isolate else Isolation.CGROUP)
    destination = _select_workload_output(screen=screen, no_capture=no_capture)
    workload_log = _workload_log_path(path) if destination is WorkloadOutput.CAPTURE else None

    if not quiet:
        _announce_log_path(path)
        if workload_log is not None:
            stderr_console.print(f"[dim]workload output to[/dim] {workload_log}")
    workload = launcher.launch(argv, output=destination)
    reader = _start_reader(workload=workload, sink=sinks, mirror=workload_log)

    session = build_session(
        platform=platform,
        config=config,
        sink=sinks,
        matcher=build_matcher(kind="pid", value=workload.pid),
        mode="run",
        clock=runtime.clock,
        self_pid=runtime.self_pid(),
        argv=argv,
        pinned_group=workload.group_path,
        notes=tuple(platform.notes) + tuple(workload.notes),
    )
    # The workload already exists, so seed now rather than searching for it.
    session.tracker.seed(platform.process_source.scan())
    _install_signal_handlers(monitor=session.monitor, workload=workload)

    try:
        session.monitor.run(read_exit_code=workload.poll)
    finally:
        code = _stop_workload(workload, clock=runtime.clock)
        # After the workload, so the last thing it printed is drained rather
        # than cut off by our own teardown.
        if reader is not None:
            reader.stop()
    _report_errors(sinks.errors + (reader.errors if reader is not None else []))
    if code:
        raise typer.Exit(code)


Since = Annotated[
    str | None,
    typer.Option("--since", help="For a top log: start here. A time ('2026-09-27 14:00') or a span ago ('2h')."),
]
Until = Annotated[
    str | None,
    typer.Option("--until", help="For a top log: stop here. A time, or a span ago."),
]


@app.command()
@_guard
def report(
    path: Annotated[Path, typer.Argument(help="A log written by a previous run, or a top log directory.")],
    *,
    as_json: Annotated[bool, typer.Option("--json", help="Print the summary as JSON.")] = False,
    since: Since = None,
    until: Until = None,
) -> None:
    """Summarise a finished run, or what a machine did under [bold]top[/bold]."""
    if _is_top_log(path):
        start, end = _window(since=since, until=until)
        history = load_history(str(path), since=start, until=end)
        if as_json:
            stdout_console.print_json(json.dumps(history_record(history), default=str))
            return
        stdout_console.print(render_top_report(history=history, palette=PALETTE, title=path.name))
        return
    _no_window(since=since, until=until)
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
    path: Annotated[Path, typer.Argument(help="A log written by a previous run, or a top log directory.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Where to write the PDF. Default: alongside the log."),
    ] = None,
    since: Since = None,
    until: Until = None,
) -> None:
    """Render a finished run, or a [bold]top[/bold] log, as a multi-page PDF report."""
    if _is_top_log(path):
        from treehawk.charts.report import write_top_pdf_report

        destination = output or (path.with_suffix(".pdf") if path.is_file() else path.parent / f"{path.name}.pdf")
        start, end = _window(since=since, until=until)
        pages = write_top_pdf_report(log_path=str(path), destination=str(destination), since=start, until=end)
    else:
        from treehawk.charts.report import write_pdf_report

        _no_window(since=since, until=until)
        destination = output or path.with_suffix(".pdf")
        pages = write_pdf_report(log_path=str(path), destination=str(destination))
    stdout_console.print(f"wrote [bold]{destination}[/bold] ({pages} pages)")


# --------------------------------------------------------------------------
# top and its service
# --------------------------------------------------------------------------

TopN = Annotated[
    int,
    typer.Option("--top", "-n", min=1, help="How many processes to follow, by cpu and by memory each."),
]
TopInterval = Annotated[
    str,
    typer.Option(
        "--interval",
        "-i",
        metavar="SECONDS|auto",
        help="Seconds between samples, or 'auto' to sample as often as the machine allows.",
    ),
]
Keep = Annotated[
    str,
    typer.Option(
        "--keep",
        metavar="SIZE",
        rich_help_panel=CLI.top_panel,
        help="Disk the log directory may use; the oldest files go first. 500M, 2G, ...",
    ),
]
Segment = Annotated[
    str,
    typer.Option(
        "--segment",
        metavar="SPAN",
        rich_help_panel=CLI.top_panel,
        help="Time one log file covers before the next is started and this one compressed. 30m, 1h, 1d, ...",
    ),
]


@app.command()
@_guard
def top(
    ctx: typer.Context,
    *,
    top_n: TopN = TOP.default_top_n,
    interval: TopInterval = str(TOP.default_interval),
    directory: Annotated[
        Path | None,
        typer.Option(
            "--dir",
            metavar="PATH",
            help=f"Where the logs go, one directory per boot. Default: ./{CLI.default_top_dir}",
        ),
    ] = None,
    keep: Keep = "1G",
    segment: Segment = "1h",
    quiet: Quiet = False,
    no_pss: NoPss = False,
    duration: Duration = None,
) -> None:
    """Follow the top processes of the whole machine until stopped.

    Every process is looked at on every sample; the top N by cpu and the top
    N by memory are logged in full. A jump far above a process' own baseline
    is logged as a [bold]spike[/bold], and memory that keeps rising for half
    an hour as a [bold]creep[/bold] - a leak suspect.

    [dim]treehawk top
    treehawk top --interval auto --top 20
    sudo treehawk service install[/dim]
    """
    runtime = _runtime(ctx)
    config = _top_config(interval=interval, top_n=top_n, segment=segment, no_pss=no_pss, duration=duration)
    platform = runtime.platform()
    log_dir = str(directory or CLI.default_top_dir)
    log = SegmentedJsonlSink(log_dir, keep_bytes=parse_size(keep))
    sinks = CompositeSink([log])
    screen = None if quiet else build_top_screen_sink(console=stderr_console)
    if screen is not None:
        sinks.add_sink(screen)
    monitor = build_top_monitor(
        platform=platform,
        config=config,
        sink=sinks,
        clock=runtime.clock,
        notes=tuple(platform.notes),
    )
    _install_top_signal_handlers(monitor)
    if not quiet:
        stderr_console.print(f"[dim]logging to[/dim] {log_dir}")
    try:
        monitor.run()
    finally:
        if isinstance(screen, TopLiveSink):
            screen.stop()
        log.wait()
    if not quiet:
        stderr_console.print(f"[dim]read it back with[/dim] treehawk report {log_dir}")
    _report_errors(sinks.errors + log.errors)


@service_app.command("install")
@_guard
def service_install(
    ctx: typer.Context,
    *,
    top_n: TopN = TOP.default_top_n,
    interval: TopInterval = str(TOP.default_interval),
    directory: Annotated[
        Path,
        typer.Option("--dir", metavar="PATH", help="Where the service logs."),
    ] = Path(CLI.service_state_dir),
    keep: Keep = "1G",
    segment: Segment = "1h",
    no_pss: NoPss = False,
) -> None:
    """Install and start the service; it starts again at every boot.

    [dim]sudo "$(command -v treehawk)" service install --interval auto[/dim]
    """
    runtime = _runtime(ctx)
    _top_config(interval=interval, top_n=top_n, segment=segment, no_pss=no_pss, duration=None)  # fail now, not at boot
    parse_size(keep)
    _require_root(runtime)
    command = [
        sys.executable,
        "-m",
        "treehawk",
        "top",
        "--dir",
        str(directory.absolute()),
        "--top",
        str(top_n),
        "--interval",
        interval,
        "--keep",
        keep,
        "--segment",
        segment,
    ]
    if no_pss:
        command.append("--no-pss")
    text = unit_text(command=command, log_dir=str(directory.absolute()))
    path = install_service(text=text, unit_dir=runtime.unit_dir, manager=runtime.systemctl)
    stdout_console.print(f"installed [bold]{path}[/bold]; logging to {directory.absolute()}")
    stdout_console.print(f"[dim]follow it with[/dim] journalctl -u {CLI.service_name} -f")
    stdout_console.print(f"[dim]read it back with[/dim] treehawk report {directory.absolute()}")


@service_app.command("uninstall")
@_guard
def service_uninstall(ctx: typer.Context) -> None:
    """Stop the service and remove it. The logs it wrote are kept."""
    runtime = _runtime(ctx)
    _require_root(runtime)
    path = uninstall_service(unit_dir=runtime.unit_dir, manager=runtime.systemctl)
    stdout_console.print(f"removed [bold]{path}[/bold]")


@service_app.command("status")
@_guard
def service_status_command(ctx: typer.Context) -> None:
    """Show whether the service is running."""
    code = service_status(manager=_runtime(ctx).systemctl)
    if code:
        raise typer.Exit(code)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _runtime(ctx: typer.Context) -> Runtime:
    """The machine this invocation runs on: the real one, unless a test passed its own."""
    return ctx.obj if isinstance(ctx.obj, Runtime) else Runtime()


def _is_top_log(path: Path) -> bool:
    return peek_header(str(path)).get("mode") == TOP_MODE


def _window(*, since: str | None, until: str | None) -> tuple[float | None, float | None]:
    """``--since`` and ``--until`` as Unix times."""
    now = time.time()
    return (
        parse_moment(since, now=now) if since is not None else None,
        parse_moment(until, now=now) if until is not None else None,
    )


def _no_window(*, since: str | None, until: str | None) -> None:
    if since is not None or until is not None:
        raise ConfigError("--since and --until apply to top logs only")


def _top_config(*, interval: str, top_n: int, segment: str, no_pss: bool, duration: float | None) -> TopConfig:
    pacing, seconds = parse_interval(interval)
    config = TopConfig(
        top_n=top_n,
        interval=seconds if seconds is not None else TOP.default_interval,
        pacing=pacing,
        memory=MemoryDetail.RESIDENT if no_pss else MemoryDetail.PROPORTIONAL,
        duration=duration,
        segment_seconds=parse_span(segment),
    )
    config.validate()
    return config


def _require_root(runtime: Runtime) -> None:
    if not runtime.is_root():
        raise ConfigError('installing a system service needs root: sudo "$(command -v treehawk)" service ...')


def _install_top_signal_handlers(monitor: TopMonitor) -> None:
    def handler(_signum: int, _frame: FrameType | None) -> None:
        monitor.request_stop()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, handler)


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
    launcher = platform.launcher if isolation is Isolation.CGROUP else platform.direct_launcher
    if launcher is None:
        raise ConfigError("this platform cannot start processes")
    return launcher


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
    return _stamped_name(f".{fmt}")


def _stamped_name(suffix: str) -> str:
    """``treehawk-<when><suffix>``, in the current directory."""
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return f"treehawk-{stamp}{suffix}"


def _select_workload_output(*, screen: Console | None, no_capture: bool) -> WorkloadOutput:
    """Whether to read the workload's output or leave it the terminal.

    Capturing exists to protect the live region, so it is worth doing exactly
    when there is one: ``--quiet`` and a redirected stderr both mean nothing is
    being repainted, and then the workload is better off owning the terminal it
    would have had anyway.
    """
    if no_capture or screen is None or not screen.is_terminal:
        return WorkloadOutput.INHERIT
    return WorkloadOutput.CAPTURE


def _workload_log_path(log_path: str) -> str:
    """Where everything the workload printed is kept: beside the log, as ``.out``."""
    if log_path == SINKS.stdout_path:
        return _stamped_name(SINKS.workload_log_suffix)
    return str(Path(log_path).with_suffix(SINKS.workload_log_suffix))


def _start_reader(*, workload: LaunchedWorkload, sink: Sink, mirror: str | None) -> OutputReader | None:
    """Begin draining the workload's output, if there is any to drain."""
    stream = workload.output_stream
    if stream is None:
        return None
    reader = OutputReader(stream=stream, sink=sink, mirror=mirror)
    reader.start()
    return reader


def _build_sinks(*, path: str, fmt: LogFormat, detail: LogDetail, screen: Console | None) -> CompositeSink:
    """The log, plus the on-screen view - or no view at all when ``screen`` is
    ``None``, which is what ``--quiet`` means."""
    sinks = CompositeSink()
    sinks.add_sink(build_file_sink(fmt=fmt, path=path, detail=detail))
    if screen is not None:
        sinks.add_sink(build_screen_sink(console=screen))
    return sinks


def _announce_log_path(path: str) -> None:
    if path != SINKS.stdout_path:
        stderr_console.print(f"[dim]logging to[/dim] {path}")


def _report_errors(errors: list[BaseException]) -> None:
    """Say what a destination lost, once the data that survived is safe.

    A sink that fails mid-run is reported rather than raised: losing the screen
    is no reason to lose the log, and by the time we get here the log has
    already been closed out. The reader that drains a workload's output is held
    to the same rule, for the same reason.
    """
    if not errors:
        return
    first = errors[0]
    stderr_console.print(
        f"[bold]treehawk:[/bold] {len(errors)} output error(s); first was {type(first).__name__}: {first}"
    )


def _install_signal_handlers(*, monitor: Monitor, workload: LaunchedWorkload | None = None) -> None:
    def handler(signum: int, _frame: FrameType | None) -> None:
        if workload is not None:
            workload.signal(signum)
        monitor.request_stop()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, handler)


def _stop_workload(workload: LaunchedWorkload, *, clock: Clock) -> int | None:
    """Stop the workload if monitoring ended first, and report its exit code."""
    code = workload.poll()
    if code is not None:
        return code
    workload.signal(signal.SIGTERM)
    deadline = clock.monotonic() + CLI.terminate_grace
    while clock.monotonic() < deadline:
        code = workload.poll()
        if code is not None:
            return code
        clock.sleep_until(clock.monotonic() + CLI.stop_poll_interval)
    workload.signal(signal.SIGKILL)
    return workload.poll()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
