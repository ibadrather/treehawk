"""The command line surface.

These check the contract a user sees - which flags exist, what the exit codes
mean, what a mistake says - and that ``watch`` and ``run`` drive the platform
they are handed. Every command that samples runs against a fake machine passed
in through :class:`Runtime`, so nothing here reads the real process table,
starts a real process or waits on a real clock.
"""

from __future__ import annotations

import pathlib
import re
import signal
from collections.abc import Iterator

import pytest
from conftest import FakeClock, FakeLauncher, fake_platform, write_cgroup, write_log, write_proc
from typer.testing import CliRunner

from treehawk.cli import app
from treehawk.cli.constants import CLI
from treehawk.cli.models import Runtime
from treehawk.core.config import WorkloadOutput
from treehawk.core.interfaces import Record
from treehawk.core.values import as_records
from treehawk.platforms.models import Platform
from treehawk.report import read_records

runner = CliRunner()

# Typer forces a terminal under CI (GITHUB_ACTIONS, FORCE_COLOR), and Rich then
# styles "--interval" as two spans, so help text is compared with styling gone.
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

SELF_PID = 9
"""treehawk's own PID, inside the fake machine."""


@pytest.fixture(autouse=True)
def restore_signal_handlers() -> Iterator[None]:
    """``watch`` and ``run`` install SIGINT and SIGTERM handlers; put pytest's back."""
    saved = [(signum, signal.getsignal(signum)) for signum in (signal.SIGINT, signal.SIGTERM)]
    yield
    for signum, handler in saved:
        if handler is not None:
            signal.signal(signum, handler)


def plain(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def on(platform: Platform, *, clock: FakeClock | None = None) -> Runtime:
    """A runtime that is all fake: this platform, a clock that never waits, a fixed PID."""
    return Runtime(
        platform=lambda: platform,
        clock=clock if clock is not None else FakeClock(),
        self_pid=lambda: SELF_PID,
    )


def read_log(path: pathlib.Path) -> tuple[Record, list[Record], Record]:
    records = list(read_records(str(path)))
    header = next(record for record in records if record["type"] == "header")
    samples = [record for record in records if record["type"] == "sample"]
    summary = next(record for record in records if record["type"] == "summary")
    return header, samples, summary


def tracked_pids(samples: list[Record]) -> set[object]:
    return {proc["pid"] for sample in samples for proc in as_records(sample.get("procs"))}


def test_version_is_reported() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "treehawk" in result.stdout


def test_bare_invocation_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "watch" in result.stdout and "run" in result.stdout


@pytest.mark.parametrize("command", ["watch", "run"])
def test_the_everyday_options_are_the_short_list(command: str) -> None:
    """Anything rarely needed lives under Advanced, so --help stays readable."""
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    text = plain(result.stdout)
    head = text.split("Advanced")[0]
    for flag in ("--interval", "--output", "--csv", "--quiet"):
        assert flag in head
    assert "Advanced" in text


def test_watch_without_a_target_says_what_to_give() -> None:
    result = runner.invoke(app, ["watch"])
    assert result.exit_code == 1
    assert "keyword" in result.output


def test_watch_rejects_two_ways_of_naming_the_same_thing() -> None:
    result = runner.invoke(app, ["watch", "train.py", "--pid", "42"])
    assert result.exit_code == 1
    assert "just one" in result.output


def test_watch_reports_a_process_that_is_not_running(
    tmp_path: pathlib.Path, proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    write_proc(proc_root, 100, cmdline="python unrelated.py")

    result = runner.invoke(
        app,
        ["watch", "train.py", "-q", "-o", str(tmp_path / "x.jsonl")],
        obj=on(fake_platform(proc_root, cgroup_root)),
    )

    assert result.exit_code == 2
    assert "no process matched" in result.output


def test_watch_samples_the_machine_it_is_given(
    tmp_path: pathlib.Path, proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    """The real wiring, end to end, on a fake /proc and a clock that never waits."""
    write_proc(proc_root, 100, cmdline="python train.py", utime=10)
    log = tmp_path / "watch.jsonl"

    result = runner.invoke(
        app,
        ["watch", "train.py", "-q", "-o", str(log), "-d", "2"],
        obj=on(fake_platform(proc_root, cgroup_root)),
    )

    assert result.exit_code == 0, result.output
    header, samples, summary = read_log(log)
    assert header["mode"] == "watch"
    assert [sample["t"] for sample in samples] == [0.0, 1.0, 2.0]
    assert tracked_pids(samples) == {100}
    assert summary["samples"] == 3


def test_watch_never_seeds_from_treehawk_or_the_shell_that_started_it(
    tmp_path: pathlib.Path, proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    """Both carry the keyword - it was typed there - and neither is the workload."""
    write_proc(proc_root, 8, comm="zsh", cmdline="zsh -c treehawk watch train.py")
    write_proc(proc_root, SELF_PID, ppid=8, sid=8, comm="treehawk", cmdline="treehawk watch train.py")
    write_proc(proc_root, 100, cmdline="python train.py")
    log = tmp_path / "watch.jsonl"

    result = runner.invoke(
        app,
        ["watch", "train.py", "-q", "-o", str(log), "-d", "1"],
        obj=on(fake_platform(proc_root, cgroup_root)),
    )

    assert result.exit_code == 0, result.output
    _, samples, _ = read_log(log)
    assert tracked_pids(samples) == {100}


def test_run_without_a_command_says_so() -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 1
    assert "after --" in result.output


def test_run_starts_the_command_through_the_platform_launcher(
    tmp_path: pathlib.Path, proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    scope = "/user.slice/treehawk-1.scope"
    write_proc(proc_root, 100, cmdline="python job.py", cgroup=scope)
    write_cgroup(cgroup_root, scope, pids=[100], cpu_usec=2_000_000)
    launcher = FakeLauncher(pid=100, group_path=scope, exit_code=3, notes=("started by the fake launcher",))
    log = tmp_path / "run.jsonl"

    result = runner.invoke(
        app,
        ["run", "-q", "-o", str(log), "-d", "1", "--", "python", "job.py"],
        obj=on(fake_platform(proc_root, cgroup_root, launcher=launcher)),
    )

    assert [workload.argv for workload in launcher.workloads] == [["python", "job.py"]]
    assert result.exit_code == 3, result.output
    header, samples, summary = read_log(log)
    assert header["group_path"] == scope
    assert header["argv"] == ["python", "job.py"]
    notes = header["notes"]
    assert isinstance(notes, list)
    assert "started by the fake launcher" in notes
    # The workload has a boundary of its own, so CPU comes from the kernel's counter.
    assert samples[-1]["cpu_seconds_total"] == pytest.approx(2.0)
    assert summary["exit_code"] == 3


def test_no_isolate_uses_the_platform_s_direct_launcher(
    tmp_path: pathlib.Path, proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    write_proc(proc_root, 100, cmdline="python job.py")
    isolating = FakeLauncher(launcher_name="scope")
    direct = FakeLauncher(launcher_name="direct")

    result = runner.invoke(
        app,
        ["run", "-q", "-o", str(tmp_path / "run.jsonl"), "-d", "1", "--no-isolate", "--", "python", "job.py"],
        obj=on(fake_platform(proc_root, cgroup_root, launcher=isolating, direct_launcher=direct)),
    )

    assert result.exit_code == 0, result.output
    assert not isolating.workloads
    assert len(direct.workloads) == 1


def test_quiet_leaves_the_workload_the_terminal(
    tmp_path: pathlib.Path, proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    """Capturing is there to protect the live region; --quiet draws none."""
    write_proc(proc_root, 100, cmdline="python job.py")
    launcher = FakeLauncher()
    log = tmp_path / "run.jsonl"

    result = runner.invoke(
        app,
        ["run", "-q", "-o", str(log), "-d", "1", "--", "python", "job.py"],
        obj=on(fake_platform(proc_root, cgroup_root, launcher=launcher)),
    )

    assert result.exit_code == 0, result.output
    assert launcher.requested == [WorkloadOutput.INHERIT]
    assert not log.with_suffix(".out").exists()


def test_no_capture_leaves_the_workload_the_terminal(
    tmp_path: pathlib.Path, proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    write_proc(proc_root, 100, cmdline="python job.py")
    launcher = FakeLauncher()
    log = tmp_path / "run.jsonl"

    result = runner.invoke(
        app,
        ["run", "--no-capture", "-o", str(log), "-d", "1", "--", "python", "job.py"],
        obj=on(fake_platform(proc_root, cgroup_root, launcher=launcher)),
    )

    assert result.exit_code == 0, result.output
    assert launcher.requested == [WorkloadOutput.INHERIT]
    assert not log.with_suffix(".out").exists()


def test_a_workload_that_ignores_sigterm_is_killed_after_the_grace_period(
    tmp_path: pathlib.Path, proc_root: pathlib.Path, cgroup_root: pathlib.Path
) -> None:
    write_proc(proc_root, 100, cmdline="python job.py")
    launcher = FakeLauncher(exit_code=None, ignores_sigterm=True)
    clock = FakeClock()

    result = runner.invoke(
        app,
        ["run", "-q", "-o", str(tmp_path / "run.jsonl"), "-d", "1", "--", "python", "job.py"],
        obj=on(fake_platform(proc_root, cgroup_root, launcher=launcher), clock=clock),
    )

    (workload,) = launcher.workloads
    assert workload.signals == [signal.SIGTERM, signal.SIGKILL]
    assert clock.t >= 1.0 + CLI.terminate_grace
    assert result.exit_code == -signal.SIGKILL


def test_an_impossible_interval_is_refused() -> None:
    result = runner.invoke(app, ["watch", "x", "-i", "0"])
    assert result.exit_code != 0


def test_report_renders_a_log(log: str) -> None:
    result = runner.invoke(app, ["report", log])
    assert result.exit_code == 0
    assert "peak" in result.stdout
    assert "train.py" in result.stdout


def test_report_can_emit_json(log: str) -> None:
    result = runner.invoke(app, ["report", log, "--json"])
    assert result.exit_code == 0
    assert '"summary"' in result.stdout


def test_report_on_a_missing_file_fails_clearly(tmp_path: pathlib.Path) -> None:
    result = runner.invoke(app, ["report", str(tmp_path / "nope.jsonl")])
    assert result.exit_code == 1
    assert "cannot read" in result.output


def test_pdf_writes_next_to_the_log(tmp_path: pathlib.Path) -> None:
    log_path = write_log(tmp_path / "run.jsonl")
    result = runner.invoke(app, ["pdf", log_path])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "run.pdf").exists()


def test_pdf_on_an_empty_log_fails_clearly(tmp_path: pathlib.Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    result = runner.invoke(app, ["pdf", str(empty)])
    assert result.exit_code == 1
