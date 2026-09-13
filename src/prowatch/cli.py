"""Command line entry point - the composition root.

This is the one module that knows about every other one. It parses arguments,
picks concrete implementations, wires them together and hands the result to the
monitor. Everything downstream sees only the interfaces it was given, which is
what keeps the rest of the codebase free of platform and format decisions.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime

from . import __version__
from .core.aggregate import Aggregator
from .core.clock import SystemClock
from .core.config import WatchConfig
from .core.matchers import build_matcher
from .core.models import LaunchedWorkload
from .core.monitor import Monitor, WorkloadNotFound
from .core.strategies import DEFAULT_STRATEGIES, STRATEGY_KINDS, build_strategies
from .core.tracker import Tracker
from .gpu import build_collectors
from .platforms.registry import UnsupportedPlatform, get_platform
from .report import ReportError, build_report, format_report
from .sinks import build_sink

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_FOUND = 2
TERMINATE_GRACE = 5.0


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prowatch",
        description=(
            "Log CPU and RAM usage of a process and every process it spawns, "
            "including detached ones."
        ),
    )
    parser.add_argument("--version", action="version", version=f"prowatch {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    watch = sub.add_parser(
        "watch",
        help="attach to a process that is already running",
        description=(
            "Find a running process by keyword, exact command line, regex or pid, "
            "then log it and everything it spawns."
        ),
    )
    watch.add_argument(
        "target",
        nargs="?",
        help="keyword to look for in the command line (default interpretation)",
    )
    selector = watch.add_mutually_exclusive_group()
    selector.add_argument("-m", "--match", help="keyword to look for (same as target)")
    selector.add_argument(
        "-e", "--exact", help="full command line the process was started with"
    )
    selector.add_argument("-r", "--regex", help="regular expression over the command line")
    selector.add_argument("-p", "--pid", type=int, help="an explicit process id")
    watch.add_argument(
        "--case-sensitive", action="store_true", help="make keyword/regex case sensitive"
    )
    watch.add_argument(
        "--wait",
        nargs="?",
        type=float,
        const=60.0,
        metavar="SECONDS",
        help="wait up to SECONDS (default 60) for the process to appear",
    )
    watch.add_argument(
        "--rescan",
        action="store_true",
        help="re-run the matcher every sample, picking up later restarts too",
    )
    _add_common_options(watch)
    watch.set_defaults(func=cmd_watch)

    run = sub.add_parser(
        "run",
        help="start a command and log it from its first instant",
        description=(
            "Start a command inside its own cgroup (via systemd-run --user --scope) "
            "so that every descendant, detached or not, is accounted for exactly."
        ),
    )
    run.add_argument(
        "--no-isolate",
        action="store_true",
        help="do not create a cgroup; track through /proc only",
    )
    run.add_argument(
        "--leave-running",
        action="store_true",
        help="do not stop the workload when monitoring ends early",
    )
    _add_common_options(run)
    run.add_argument(
        "argv",
        nargs=argparse.REMAINDER,
        help="the command to run, after a literal --",
    )
    run.set_defaults(func=cmd_run)

    report = sub.add_parser("report", help="summarise a log written by a previous run")
    report.add_argument("path", help="path to a .jsonl log")
    report.add_argument("--top", type=int, default=5, help="processes per table (5)")
    report.add_argument("--json", action="store_true", help="print the summary as JSON")
    report.set_defaults(func=cmd_report)

    return parser


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-i", "--interval", type=float, default=1.0,
        help="seconds between samples (1.0)",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="log file; '-' for stdout (default: prowatch-<timestamp>.jsonl)",
    )
    parser.add_argument(
        "-f", "--format", default="jsonl", choices=("jsonl", "csv"),
        help="log format (jsonl)",
    )
    parser.add_argument("-d", "--duration", type=float, help="stop after SECONDS")
    parser.add_argument("-n", "--max-samples", type=int, help="stop after N samples")
    parser.add_argument(
        "--no-per-process", action="store_true",
        help="log only the aggregate, not one row per process",
    )
    parser.add_argument(
        "--no-pss", action="store_true",
        help="skip smaps_rollup; cheaper per sample, no shared-memory correction",
    )
    parser.add_argument(
        "--expand", default=",".join(DEFAULT_STRATEGIES),
        help=(
            "comma-separated membership rules "
            f"({', '.join(sorted(STRATEGY_KINDS))}); 'none' to disable"
        ),
    )
    parser.add_argument(
        "--keep-going", action="store_true",
        help="keep sampling after the last tracked process exits",
    )
    parser.add_argument(
        "--show-procs", type=int, default=0, metavar="N",
        help="also print the top N processes per sample on the console",
    )
    parser.add_argument("--top", type=int, default=5, help="processes in the summary (5)")
    parser.add_argument("-q", "--quiet", action="store_true", help="no console output")
    parser.add_argument(
        "--collector", action="append", default=[], metavar="NAME",
        help="extra metric collector to enable (none available yet)",
    )


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_watch(args: argparse.Namespace) -> int:
    matcher = _matcher_from(args)
    platform = get_platform()
    config = _config_from(args)
    sink, output = _sink_from(args)

    monitor, _tracker = _assemble(
        platform=platform,
        config=config,
        sink=sink,
        matcher=matcher,
        mode="watch",
        notes=platform.notes,
    )
    _install_signal_handlers(monitor)
    _announce(args, output)

    try:
        monitor.run()
    except WorkloadNotFound as exc:
        print(f"prowatch: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    argv = [a for a in args.argv if a != "--"] if args.argv else []
    if not argv:
        print("prowatch: nothing to run; pass the command after --", file=sys.stderr)
        return EXIT_ERROR

    platform = get_platform()
    config = _config_from(args)
    sink, output = _sink_from(args)

    launcher = platform.launcher
    if args.no_isolate:
        from .platforms.linux.launcher import DirectLauncher, FallbackLauncher

        launcher = FallbackLauncher([DirectLauncher()])
    if launcher is None:
        print("prowatch: this platform cannot launch processes", file=sys.stderr)
        return EXIT_ERROR

    _announce(args, output)
    workload = launcher.launch(argv)
    notes = list(platform.notes) + list(getattr(launcher, "notes", []))

    monitor, tracker = _assemble(
        platform=platform,
        config=config,
        sink=sink,
        matcher=build_matcher("pid", workload.pid),
        mode="run",
        argv=argv,
        pinned_group=workload.group_path,
        notes=notes,
    )
    # The workload already exists, so seed immediately rather than searching.
    tracker.seed(platform.processes.scan())
    _install_signal_handlers(monitor, workload)

    try:
        monitor.run(exit_code=lambda: _poll_exit_code(workload))
    finally:
        code = _finalize_workload(workload, leave_running=args.leave_running)
    return code if code is not None else EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    try:
        report = build_report(args.path, top_n=args.top)
    except ReportError as exc:
        print(f"prowatch: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_report(report))
    return EXIT_OK


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------

def _assemble(
    *,
    platform,
    config: WatchConfig,
    sink,
    matcher,
    mode: str,
    argv: list[str] | None = None,
    pinned_group: str | None = None,
    notes=(),
):
    host = platform.host_info()
    source = platform.processes
    self_pid = _self_pid()
    # prowatch's own wrapper chain (shell, uv, timeout, ...) is never part of
    # the workload: it carries the keyword because the user typed it there, and
    # adopting it would drag in the whole terminal. An explicit --pid may still
    # name one of them as the seed.
    exclude = {self_pid} | _ancestors(source, self_pid)
    tracker = Tracker(
        matcher=matcher,
        strategies=build_strategies(config.expand),
        source=source,
        groups=platform.groups,
        self_pid=self_pid,
        self_group=source.read_group_path(self_pid),
        self_sid=_self_sid(source, self_pid),
        pinned_group=pinned_group,
        rescan=config.rescan,
        exclude_pids=exclude,
        seed_excluded=matcher.describe().get("kind") == "pid",
    )
    monitor = Monitor(
        source=source,
        tracker=tracker,
        aggregator=Aggregator(host, prefer_group_cpu=pinned_group is not None),
        sink=sink,
        clock=SystemClock(),
        config=config,
        host=host,
        groups=platform.groups,
        collectors=build_collectors(config.collectors),
        mode=mode,
        matcher=matcher.describe(),
        argv=argv,
        notes=notes,
    )
    return monitor, tracker


def _matcher_from(args: argparse.Namespace):
    kwargs = {"case_sensitive": args.case_sensitive}
    if args.pid is not None:
        return build_matcher("pid", args.pid)
    if args.exact:
        return build_matcher("exact", args.exact)
    if args.regex:
        return build_matcher("regex", args.regex, **kwargs)
    keyword = args.match or args.target
    if not keyword:
        raise SystemExit(
            "prowatch: give a keyword, or one of --exact/--regex/--pid"
        )
    return build_matcher("keyword", keyword, **kwargs)


def _config_from(args: argparse.Namespace) -> WatchConfig:
    expand = () if args.expand.strip().lower() in ("", "none") else tuple(
        part.strip() for part in args.expand.split(",") if part.strip()
    )
    config = WatchConfig(
        interval=args.interval,
        duration=args.duration,
        max_samples=args.max_samples,
        per_process=not args.no_per_process,
        want_pss=not args.no_pss,
        expand=expand,
        rescan=getattr(args, "rescan", False),
        wait=getattr(args, "wait", None),
        stop_when_empty=not args.keep_going,
        top_n=args.top,
        collectors=tuple(args.collector),
    )
    config.validate()
    return config


def _sink_from(args: argparse.Namespace):
    output = args.output
    if output is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = f"prowatch-{stamp}.{'csv' if args.format == 'csv' else 'jsonl'}"
    sink = build_sink(
        fmt=args.format,
        output=output,
        per_process=not args.no_per_process,
        quiet=args.quiet,
        show_procs=args.show_procs,
    )
    return sink, output


def _announce(args: argparse.Namespace, output: str) -> None:
    if not args.quiet and output != "-":
        print(f"prowatch: logging to {output}", file=sys.stderr)


def _install_signal_handlers(monitor: Monitor, workload=None) -> None:
    def handler(signum, _frame):
        if workload is not None:
            workload.signal(signum)
        monitor.request_stop()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, handler)


def _poll_exit_code(workload: LaunchedWorkload) -> int | None:
    return workload.poll()


def _finalize_workload(
    workload: LaunchedWorkload, *, leave_running: bool
) -> int | None:
    code = _poll_exit_code(workload)
    if code is not None or leave_running:
        return code
    workload.signal(signal.SIGTERM)
    deadline = time.monotonic() + TERMINATE_GRACE
    while time.monotonic() < deadline:
        code = _poll_exit_code(workload)
        if code is not None:
            return code
        time.sleep(0.05)
    workload.signal(signal.SIGKILL)
    return _poll_exit_code(workload)


def _self_pid() -> int:
    return os.getpid()


def _self_sid(source, pid: int) -> int:
    read = getattr(source, "read_info", None)
    info = read(pid) if read else None
    return info.sid if info is not None else 0


def _ancestors(source, pid: int, *, limit: int = 64) -> set[int]:
    """Every process between prowatch and PID 1."""
    read = getattr(source, "read_info", None)
    if read is None:
        return set()
    found: set[int] = set()
    current = pid
    for _ in range(limit):
        info = read(current)
        if info is None or info.ppid <= 0 or info.ppid in found:
            break
        found.add(info.ppid)
        current = info.ppid
    return found


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except UnsupportedPlatform as exc:
        print(f"prowatch: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:
        print(f"prowatch: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
